"""얇은 프레스 파트: mid-surface 추출 + quad-dominant shell 메시.

한계를 먼저 밝혀둔다. 상용 전처리기(ANSA MIDDLE, HyperMesh Midsurface)의
mid-surface는 면쌍 추출 + 벤딩부 연장 + 자동 봉합까지 한 번에 처리한다.
여기서는 그중 '평행 평면 면쌍의 t/2 오프셋'만 구현했다.

- 평평한/계단진 프레스물: 잘 맞는다.
- 벤딩 R 구간: 원통면 쌍은 mid 곡면을 만들지 않으므로 패치 사이에 틈이 남는다.
  imprint로 인접 패치를 연결하되, 남은 free edge 수를 리포트한다.
- 그래도 안 되면 설계팀에서 mid-surface STEP을 받거나 ANSA에서 뽑은 뒤
  이 프로그램의 메시/홀/washer 부분만 쓰는 편이 빠르다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Dict, List, Tuple

import gmsh

from . import geometry as g
from .config import MeshConfig

Logger = Callable[[str], None]


@dataclass
class FacePair:
    a: int
    b: int
    thickness: float


def find_face_pairs(info: g.SolidInfo, cfg: MeshConfig) -> List[FacePair]:
    """마주보는 평면 쌍(외향 법선이 반대, 거리 = 판 두께)을 찾는다."""
    planar = [f for f in info.faces if g.is_planar(f)]
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
            if g.dot(na, nb) > -0.995:
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


def build_midsurface(info: g.SolidInfo, cfg: MeshConfig,
                     log: Logger) -> Tuple[List[int], Dict[int, float]]:
    """면쌍마다 t/2 오프셋 패치를 만들고 (면 태그 목록, 면->두께) 를 반환."""
    pairs = find_face_pairs(info, cfg)
    if not pairs:
        if not cfg.shell_fallback_offset:
            raise RuntimeError("mid-surface 면쌍을 찾지 못했습니다")
        biggest = max(info.faces, key=g.face_area)
        pairs = [FacePair(biggest, biggest, info.eq_thickness)]
        log(f"  면쌍 실패 — 최대면 단독 오프셋으로 대체 (t={info.eq_thickness:.2f})")
    else:
        ts = sorted({round(p.thickness, 2) for p in pairs})
        log(f"  면쌍 {len(pairs)}쌍, 두께 {ts}")

    mid_tags: List[int] = []
    thickness: Dict[int, float] = {}
    for p in pairs:
        n = g.face_normal(p.a)
        copied = gmsh.model.occ.copy([(2, p.a)])
        # 외향 법선의 반대(재료 안쪽)로 t/2 이동
        gmsh.model.occ.translate(copied, *g.scale(n, -p.thickness / 2.0))
        for _, t in copied:
            mid_tags.append(t)
            thickness[t] = p.thickness
    gmsh.model.occ.synchronize()

    # 원본 solid는 제거해서 메시 대상에서 뺀다
    gmsh.model.occ.remove([(3, info.tag)], recursive=False)
    gmsh.model.occ.synchronize()
    for dim in (3, 2, 1, 0):
        for d, t in gmsh.model.getEntities(dim):
            if d == 2 and t in thickness:
                continue
            if d == 2:
                gmsh.model.occ.remove([(d, t)], recursive=False)
    gmsh.model.occ.synchronize()

    if cfg.shell_imprint and len(mid_tags) > 1:
        try:
            out, mapping = gmsh.model.occ.fragment(
                [(2, mid_tags[0])], [(2, t) for t in mid_tags[1:]]
            )
            gmsh.model.occ.synchronize()
            new_thick: Dict[int, float] = {}
            for src, children in zip(mid_tags, mapping):
                for d, t in children:
                    if d == 2:
                        new_thick[t] = thickness[src]
            if new_thick:
                thickness = new_thick
                mid_tags = list(new_thick.keys())
            log(f"  패치 imprint 완료 — 면 {len(mid_tags)}개")
        except Exception as exc:
            log(f"  imprint 실패, 개별 패치 유지: {exc}")

    return mid_tags, thickness


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


def mesh(info: g.SolidInfo, cfg: MeshConfig, log: Logger) -> Dict[str, float]:
    mid_tags, thickness = build_midsurface(info, cfg, log)

    field = g.setup_holes(mid_tags, cfg, log, structured=True)
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
        gmsh.option.setNumber("Mesh.RecombinationAlgorithm", 2)
    else:
        gmsh.option.setNumber("Mesh.Algorithm", 6)

    gmsh.model.mesh.generate(2)
    if cfg.optimize:
        gmsh.model.mesh.optimize("Laplace2D")
    if cfg.second_order:
        gmsh.model.mesh.setOrder(2)
        gmsh.option.setNumber("Mesh.SecondOrderIncomplete", 1)

    # 두께별로 물리 그룹을 만들어 두면 *SHELL SECTION 작성이 쉬워진다
    groups: Dict[float, List[int]] = {}
    for tag, t in thickness.items():
        groups.setdefault(round(t, 3), []).append(tag)
    for t, tags in sorted(groups.items()):
        pg = gmsh.model.addPhysicalGroup(2, tags)
        gmsh.model.setPhysicalName(2, pg, f"SHELL_T{t:.2f}".replace(".", "p"))

    return {"free_edges": float(free),
            "thickness_groups": {f"{k:.3f}": len(v) for k, v in groups.items()}}
