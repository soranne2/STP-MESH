"""얇은 프레스 파트: mid-surface 추출 + quad-dominant shell 메시.

한계를 먼저 밝혀둔다. 상용 전처리기(ANSA MIDDLE, HyperMesh Midsurface)의
mid-surface는 면쌍 추출 + 벤딩부 연장 + 자동 봉합까지 한 번에 처리한다.
여기서는 그중 '평행 평면 면쌍의 t/2 오프셋'만 구현했다.

- 평평한/계단진 프레스물: 잘 맞는다.
- 벤딩 R 구간: 원통면 쌍은 mid 곡면을 만들지 않으므로 패치 사이에 틈이 남는다.
  imprint로 인접 패치를 연결하되, 남은 free edge 수를 리포트한다.
- 그래도 안 되면 설계팀에서 mid-surface STEP을 받거나 ANSA에서 뽑은 뒤
  이 프로그램의 메시/홀/washer 부분만 쓰는 편이 빠르다.

버전 이력
---------
v1.5 (수정본)
  - 진행 단계 콜백(stage) 지원.
v1.3 (수정본)
  - surface 전용 STEP에서도 두께를 실측한다. 면쌍 탐색을 solid이 아니라
    면 목록에 대해 수행하도록 바꿔, 겉면만 있는 모델도 마주보는 면 간
    거리로 판 두께를 잰다. shell_default_thickness는 면쌍을 하나도 못
    찾았을 때의 최후 수단으로만 쓰인다.
  - Mesh.RecombinationAlgorithm을 full-quad(2)에서 blossom(1)로 변경.
    원통면 등 주기적 면에서 "Full-quad recombination not ready yet for
    periodic surfaces" 예외가 나던 원인이다. 생성은 generate_mesh()를 통해
    호출해 남은 재조합 실패도 자동 복구한다.
v1.2 (수정본)
  - solid이 없는 surface 전용 STEP을 그대로 shell 메시하는 mesh_surfaces() 추가.
  - 2D 메시 생성 부분을 _mesh_2d()로 분리해 solid 경로와 공유.
  - washer로 면이 쪼개져도 두께 그룹이 유지되도록 setup_holes에 meta 전달.
v1.0
  - 최초 작성.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import gmsh

from . import geometry as g
from .config import MeshConfig

Logger = Callable[[str], None]
Stage = Optional[Callable[[float, str], None]]


@dataclass
class FacePair:
    a: int
    b: int
    thickness: float


def find_face_pairs(faces: Sequence[int], cfg: MeshConfig) -> List[FacePair]:
    """마주보는 평면 쌍(법선이 반대, 거리 = 판 두께)을 찾는다.

    solid의 경계면이든 STEP에 그냥 들어있는 면이든 동일하게 동작한다.
    """
    planar = [f for f in faces if g.is_planar(f)]
    cache = {f: (g.face_normal(f), g.face_center(f), g.face_area(f)) for f in planar}
    pairs: List[FacePair] = []
    used: set[int] = set()

    # 큰 면부터 짝을 지어야 오인식이 적다
    order = sorted(planar, key=lambda f: -cache[f][2])
    for a in order:
        if a in used:
            continue
        na, ca, aa = cache[a]
        best = None
        for b in order:
            if b == a or b in used:
                continue
            nb, cb, ab = cache[b]
            if abs(g.dot(na, nb)) < 0.995:      # 평행(같은 방향/반대 방향 모두 허용)
                continue
            if abs(aa - ab) / max(aa, ab) > cfg.shell_area_tol:
                continue
            d = g.sub(cb, ca)
            t = abs(g.dot(d, na))
            if t < 1e-6 or t > cfg.shell_max_thickness:
                continue
            lateral = g.norm(g.sub(d, g.scale(na, g.dot(d, na))))
            if lateral > 0.3 * math.sqrt(max(aa, ab)):
                continue
            if best is None or t < best[1]:
                best = (b, t)
        if best is not None:
            pairs.append(FacePair(a, best[0], best[1]))
            used.add(a)
            used.add(best[0])
    return pairs


def _keep_only(keep: Sequence[int]) -> None:
    """지정한 surface만 남기고 나머지 형상을 모델에서 제거한다."""
    keep_set = set(keep)
    for _, t in list(gmsh.model.getEntities(3)):
        try:
            gmsh.model.occ.remove([(3, t)], recursive=False)
        except Exception:
            pass
    gmsh.model.occ.synchronize()
    for _, t in list(gmsh.model.getEntities(2)):
        if t in keep_set:
            continue
        try:
            gmsh.model.occ.remove([(2, t)], recursive=False)
        except Exception:
            pass
    gmsh.model.occ.synchronize()


def offset_pairs(pairs: Sequence[FacePair], log: Logger) -> Tuple[List[int], Dict[int, float]]:
    """각 면쌍의 한쪽 면을 t/2만큼 안쪽으로 옮겨 mid-surface 패치를 만든다."""
    mid_tags: List[int] = []
    thickness: Dict[int, float] = {}
    for p in pairs:
        n = g.face_normal(p.a)
        cb = g.face_center(p.b)
        ca = g.face_center(p.a)
        # 짝이 되는 면 쪽(재료 안쪽)으로 절반만큼 이동
        direction = 1.0 if g.dot(g.sub(cb, ca), n) > 0 else -1.0
        copied = gmsh.model.occ.copy([(2, p.a)])
        gmsh.model.occ.translate(copied, *g.scale(n, direction * p.thickness / 2.0))
        for _, t in copied:
            mid_tags.append(t)
            thickness[t] = p.thickness
    gmsh.model.occ.synchronize()
    return mid_tags, thickness


def imprint_patches(tags: List[int], thickness: Dict[int, float],
                    log: Logger) -> Tuple[List[int], Dict[int, float]]:
    """인접 패치끼리 imprint해서 경계에서 절점을 공유하게 만든다."""
    if len(tags) < 2:
        return tags, thickness
    try:
        out, mapping = gmsh.model.occ.fragment(
            [(2, tags[0])], [(2, t) for t in tags[1:]]
        )
        gmsh.model.occ.synchronize()
        new_thick: Dict[int, float] = {}
        for src, children in zip(tags, mapping):
            for d, t in children:
                if d == 2:
                    new_thick[t] = thickness.get(src, 0.0)
        if new_thick:
            log(f"  패치 imprint 완료 — 면 {len(new_thick)}개")
            return list(new_thick.keys()), new_thick
    except Exception as exc:
        log(f"  imprint 실패, 개별 패치 유지: {exc}")
    return tags, thickness


def report_free_edges(log: Logger) -> int:
    """한 면에만 붙은 엣지 수 = 패치 사이 미봉합 구간의 지표."""
    count: Dict[int, int] = {}
    for _, s in gmsh.model.getEntities(2):
        for _, c in gmsh.model.getBoundary([(2, s)], combined=False, oriented=False):
            c = abs(c)
            count[c] = count.get(c, 0) + 1
    free = sum(1 for v in count.values() if v == 1)
    if free:
        log(f"  free edge {free}개 — 벤딩부 등 미봉합 구간일 수 있음 (확인 필요)")
    return free


def _mesh_2d(tags: List[int], thickness: Dict[int, float],
             cfg: MeshConfig, log: Logger, stage: Stage = None) -> Dict[str, object]:
    """홀/washer 처리 후 2D 메시를 만들고 두께별 물리 그룹을 붙인다."""
    if stage:
        stage(0.30, "홀 / washer 처리")
    field = g.setup_holes(tags, cfg, log, structured=True, meta=thickness)
    free = report_free_edges(log)

    gmsh.option.setNumber("Mesh.MeshSizeMin", cfg.element_size * 0.2)
    gmsh.option.setNumber("Mesh.MeshSizeMax", cfg.element_size)
    gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 0)
    gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 0)
    gmsh.option.setNumber("Mesh.MeshSizeFromPoints", 0)
    if field is not None:
        gmsh.model.mesh.field.setAsBackgroundMesh(field)

    if cfg.shell_quad_dominant:
        gmsh.option.setNumber("Mesh.Algorithm", 8)          # Frontal-Delaunay for quads
        gmsh.option.setNumber("Mesh.RecombineAll", 1)
        # 1 = blossom. 2/3(full-quad)은 원통 등 주기적 면에서 예외를 던진다.
        gmsh.option.setNumber("Mesh.RecombinationAlgorithm", 1)
    else:
        gmsh.option.setNumber("Mesh.Algorithm", 6)

    if stage:
        stage(0.50, "메시 생성")
    g.generate_mesh(2, log)
    if cfg.optimize:
        gmsh.model.mesh.optimize("Laplace2D")
    if cfg.second_order:
        gmsh.model.mesh.setOrder(2)
        gmsh.option.setNumber("Mesh.SecondOrderIncomplete", 1)

    # 두께별로 물리 그룹을 만들어 두면 *SHELL SECTION 작성이 쉬워진다
    existing = {t for _, t in gmsh.model.getEntities(2)}
    groups: Dict[float, List[int]] = {}
    for tag, t in thickness.items():
        if tag in existing:
            groups.setdefault(round(t, 3), []).append(tag)
    for t, group_tags in sorted(groups.items()):
        pg = gmsh.model.addPhysicalGroup(2, group_tags)
        gmsh.model.setPhysicalName(2, pg, f"SHELL_T{t:.2f}".replace(".", "p"))

    return {"free_edges": float(free),
            "thickness_groups": {f"{k:.3f}": len(v) for k, v in groups.items()}}


def mesh(info: g.SolidInfo, cfg: MeshConfig, log: Logger,
         stage: Stage = None) -> Dict[str, object]:
    """solid에서 mid-surface를 뽑아 shell 메시."""
    if stage:
        stage(0.05, "면쌍 탐색")
    pairs = find_face_pairs(info.faces, cfg)
    if pairs:
        ts = sorted({round(p.thickness, 2) for p in pairs})
        log(f"  면쌍 {len(pairs)}쌍, 두께 {ts}")
    else:
        if not cfg.shell_fallback_offset:
            raise RuntimeError("mid-surface 면쌍을 찾지 못했습니다")
        biggest = max(info.faces, key=g.face_area)
        pairs = [FacePair(biggest, biggest, info.eq_thickness)]
        log(f"  면쌍 실패 — 최대면 단독 오프셋으로 대체 (t={info.eq_thickness:.2f})")

    if stage:
        stage(0.20, "mid-surface 생성")
    mid_tags, thickness = offset_pairs(pairs, log)
    _keep_only(mid_tags)
    if cfg.shell_imprint:
        mid_tags, thickness = imprint_patches(mid_tags, thickness, log)
    return _mesh_2d(mid_tags, thickness, cfg, log, stage)


def mesh_surfaces(surface_tags: List[int], cfg: MeshConfig,
                  log: Logger, stage: Stage = None) -> Dict[str, object]:
    """solid 없이 면만 있는 STEP을 shell 메시.

    먼저 마주보는 면쌍을 찾아 실제 판 두께를 재고 mid-surface를 만든다.
    겉면만 있는 모델이라도 상하면이 짝지어지면 두께가 그대로 나온다.
    면쌍을 하나도 못 찾으면 들어온 면을 그대로 쓰고
    shell_default_thickness를 두께로 가정한다.
    """
    if stage:
        stage(0.05, "면쌍 탐색")
    pairs = find_face_pairs(surface_tags, cfg)
    if pairs:
        ts = sorted({round(p.thickness, 2) for p in pairs})
        log(f"  면쌍 {len(pairs)}쌍에서 두께 실측 — {ts}")
        mid_tags, thickness = offset_pairs(pairs, log)
        _keep_only(mid_tags)
    else:
        t0 = cfg.shell_default_thickness
        log(f"  면쌍을 찾지 못함 — 입력 면을 그대로 사용, 두께 {t0} 가정")
        log("   (실제 두께를 넣으려면 '기본 판 두께' 값을 바꾸거나 "
            "'면쌍 최대 두께'를 키워보세요)")
        mid_tags = list(surface_tags)
        thickness = {t: t0 for t in mid_tags}

    if cfg.shell_imprint:
        mid_tags, thickness = imprint_patches(mid_tags, thickness, log)
    return _mesh_2d(mid_tags, thickness, cfg, log, stage)
