"""STEP 로딩, 형상 지표 계산, 파트 자동 분류, 홀 검출.

gmsh OCC 커널의 조회 API만 사용하므로 별도 CAD 라이브러리가 필요 없다.

버전 이력
---------
v1.2 (수정본)
  - solid이 없는 surface 전용 STEP도 처리할 수 있도록 load_step이 surface
    태그까지 함께 돌려주도록 변경 (load_shapes).
  - washer 생성으로 면이 쪼개질 때 두께 정보가 유실되던 문제 수정.
    setup_holes/apply_washers가 meta 딕셔너리를 받아 자식 면에 부모의
    두께를 물려준다.
v1.1
  - "signal only works in main thread of the main interpreter" 오류 수정.
    gmsh.initialize()가 SIGINT 핸들러를 등록하는데 파이썬은 메인 스레드가
    아니면 이를 금지한다. 메시 작업은 GUI 워커 스레드에서 돌기 때문에
    interruptible=False로 초기화하도록 바꿨다.
    해당 인자가 없는 구버전 gmsh를 위해 TypeError 폴백을 둔다.
v1.0
  - 최초 작성.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence, Tuple

import gmsh

from .config import MeshConfig, PartType

Vec = Tuple[float, float, float]
Logger = Callable[[str], None]


# ------------------------------------------------------------------ 벡터 유틸
def sub(a: Sequence[float], b: Sequence[float]) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def dot(a: Sequence[float], b: Sequence[float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def scale(a: Sequence[float], s: float) -> Vec:
    return (a[0] * s, a[1] * s, a[2] * s)


def norm(a: Sequence[float]) -> float:
    return math.sqrt(dot(a, a))


def unit(a: Sequence[float]) -> Vec:
    n = norm(a)
    return (a[0] / n, a[1] / n, a[2] / n) if n > 1e-12 else (0.0, 0.0, 0.0)


def cross(a: Sequence[float], b: Sequence[float]) -> Vec:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def perp_basis(n: Sequence[float]) -> Tuple[Vec, Vec]:
    """법선 n에 수직인 정규직교 두 벡터."""
    ref = (0.0, 0.0, 1.0) if abs(n[2]) < 0.9 else (1.0, 0.0, 0.0)
    u = unit(cross(n, ref))
    v = unit(cross(n, u))
    return u, v


# ------------------------------------------------------------------ 세션 관리
def start(verbose: bool = False, term: bool = False) -> None:
    if not gmsh.isInitialized():
        # 워커 스레드에서 돌기 때문에 gmsh의 SIGINT 핸들러 등록을 꺼야 한다
        try:
            gmsh.initialize(interruptible=False)
        except TypeError:
            gmsh.initialize()   # interruptible 인자가 없는 구버전
    gmsh.option.setNumber("General.Terminal", 1 if term else 0)
    gmsh.option.setNumber("General.Verbosity", 5 if verbose else 1)
    gmsh.clear()


def stop() -> None:
    if gmsh.isInitialized():
        gmsh.finalize()


def load_step(path: str, cfg: MeshConfig, log: Logger) -> List[int]:
    """STEP을 읽고 solid 태그 리스트를 반환."""
    gmsh.option.setNumber("Geometry.OCCImportLabels", 1)
    gmsh.option.setNumber("Geometry.Tolerance", 1e-6)
    gmsh.model.occ.importShapes(path)
    gmsh.model.occ.synchronize()

    if abs(cfg.scale - 1.0) > 1e-9:
        gmsh.model.occ.dilate(gmsh.model.getEntities(), 0, 0, 0,
                              cfg.scale, cfg.scale, cfg.scale)
        gmsh.model.occ.synchronize()
        log(f"  단위 스케일 {cfg.scale}배 적용")

    solids = [t for _, t in gmsh.model.getEntities(3)]
    if solids:
        log(f"  solid {len(solids)}개 로드")
    else:
        surfs = [t for _, t in gmsh.model.getEntities(2)]
        log(f"  solid 없음 — surface {len(surfs)}개를 mid-surface로 사용")
    return solids


def load_shapes(path: str, cfg: MeshConfig, log: Logger) -> Tuple[List[int], List[int]]:
    """(solid 태그, surface 태그)를 함께 반환.

    설계팀이나 ANSA에서 뽑은 mid-surface STEP처럼 solid이 아예 없는 파일도
    있으므로, 그런 경우 surface를 바로 shell 메시 대상으로 쓴다.
    """
    solids = load_step(path, cfg, log)
    surfaces = [t for _, t in gmsh.model.getEntities(2)]
    return solids, surfaces


# ------------------------------------------------------------------ 면/솔리드 정보
def face_normal(tag: int) -> Vec:
    """면 파라미터 중앙에서의 법선(외향)."""
    b = gmsh.model.getParametrizationBounds(2, tag)
    u = 0.5 * (b[0][0] + b[1][0])
    v = 0.5 * (b[0][1] + b[1][1])
    n = gmsh.model.getNormal(tag, [u, v])
    return unit((n[0], n[1], n[2]))


def face_area(tag: int) -> float:
    return gmsh.model.occ.getMass(2, tag)


def face_center(tag: int) -> Vec:
    c = gmsh.model.occ.getCenterOfMass(2, tag)
    return (c[0], c[1], c[2])


def is_planar(tag: int) -> bool:
    return gmsh.model.getType(2, tag) == "Plane"


def bbox_diag(dim: int, tag: int) -> float:
    b = gmsh.model.getBoundingBox(dim, tag)
    return norm((b[3] - b[0], b[4] - b[1], b[5] - b[2]))


@dataclass
class SolidInfo:
    tag: int
    volume: float
    area: float
    diag: float
    eq_thickness: float   # 2V/A — 얇은 판재에서 판 두께에 수렴
    faces: List[int]


def solid_info(tag: int) -> SolidInfo:
    vol = abs(gmsh.model.occ.getMass(3, tag))
    faces = [t for _, t in gmsh.model.getBoundary([(3, tag)],
                                                  combined=False, oriented=False)]
    faces = sorted(set(abs(t) for t in faces))
    area = sum(face_area(f) for f in faces)
    eq_t = 2.0 * vol / area if area > 1e-12 else 0.0
    return SolidInfo(tag, vol, area, bbox_diag(3, tag), eq_t, faces)


# ------------------------------------------------------------------ 압출 검출
@dataclass
class Extrusion:
    cap: int          # 소스가 될 단면(캡) 면 태그
    other_cap: int
    axis: Vec         # 단위 압출 방향 (cap -> other_cap)
    length: float
    cap_area: float
    cap_diag: float


def detect_extrusion(info: SolidInfo, cfg: MeshConfig) -> Optional[Extrusion]:
    """평행하고 면적이 같은 평면 캡 한 쌍 + V ≈ A×L 조건으로 압출 형상을 판별."""
    planar = [f for f in info.faces if is_planar(f)]
    best: Optional[Extrusion] = None
    for i, a in enumerate(planar):
        na, ca, aa = face_normal(a), face_center(a), face_area(a)
        if aa < 1e-9:
            continue
        for b in planar[i + 1:]:
            nb, cb, ab = face_normal(b), face_center(b), face_area(b)
            if dot(na, nb) > -0.999:          # 서로 반대 방향이어야 캡
                continue
            if abs(aa - ab) / max(aa, ab) > 0.02:
                continue
            d = sub(cb, ca)
            length = abs(dot(d, na))
            if length < 1e-6:
                continue
            # 측면 어긋남이 크면 캡이 아님
            lateral = norm(sub(d, scale(na, dot(d, na))))
            if lateral > 0.02 * max(length, math.sqrt(aa)):
                continue
            if abs(info.volume - aa * length) / info.volume > cfg.extrusion_tol:
                continue
            cand = Extrusion(a, b, unit(scale(na, -1.0)), length, aa, bbox_diag(2, a))
            if best is None or cand.length > best.length:
                best = cand
    return best


# ------------------------------------------------------------------ 자동 분류
def classify(info: SolidInfo, cfg: MeshConfig, log: Logger) -> Tuple[PartType, str]:
    ext = detect_extrusion(info, cfg)
    if ext is not None and ext.length > ext.cap_diag:
        # 단면 대비 충분히 길다 -> 진짜 압출 프로파일
        return PartType.EXTRUSION, (
            f"압출 단면 검출 (길이 {ext.length:.1f}, 단면 대각 {ext.cap_diag:.1f})"
        )
    if info.eq_thickness <= cfg.thin_thickness_max and info.eq_thickness < 0.05 * info.diag:
        return PartType.PRESS, f"등가두께 {info.eq_thickness:.2f} — 박판으로 판정"
    return PartType.INJECTION, (
        f"등가두께 {info.eq_thickness:.2f}, 압출 단면 없음 — 복잡 형상으로 판정"
    )


# ------------------------------------------------------------------ 홀 검출
@dataclass
class Hole:
    curve: int
    face: int
    center: Vec
    radius: float
    normal: Vec       # 홀이 놓인 면의 법선

    @property
    def diameter(self) -> float:
        return 2.0 * self.radius


def _is_full_circle(curve: int) -> Optional[float]:
    """닫힌 완전원이면 반지름을 반환, 아니면 None."""
    if gmsh.model.getType(1, curve) != "Circle":
        return None
    length = gmsh.model.occ.getMass(1, curve)
    r = length / (2.0 * math.pi)
    if r <= 1e-9:
        return None
    b = gmsh.model.getBoundingBox(1, curve)
    extent = max(b[3] - b[0], b[4] - b[1], b[5] - b[2])
    # 완전원이면 최대 bbox 폭 = 지름
    if abs(extent - 2.0 * r) > 0.05 * r:
        return None
    return r


def find_holes(face_tags: Sequence[int], cfg: MeshConfig, log: Logger) -> List[Hole]:
    """평면 위의 완전 원형 엣지를 홀 림으로 수집."""
    holes: List[Hole] = []
    seen: set[int] = set()
    for f in face_tags:
        if not is_planar(f):
            continue
        n = face_normal(f)
        f_area = face_area(f)
        curves = gmsh.model.getBoundary([(2, f)], combined=False, oriented=False)
        for _, c in curves:
            c = abs(c)
            if c in seen:
                continue
            r = _is_full_circle(c)
            if r is None:
                continue
            if not (cfg.min_hole_dia <= 2 * r <= cfg.max_hole_dia):
                continue
            if math.pi * r * r > 0.6 * f_area:   # 면 대부분을 차지하면 외곽선
                continue
            cm = gmsh.model.occ.getCenterOfMass(1, c)
            seen.add(c)
            holes.append(Hole(c, f, (cm[0], cm[1], cm[2]), r, n))
    if holes:
        dias = sorted({round(h.diameter, 2) for h in holes})
        log(f"  홀 {len(holes)}개 검출 — 지름 {dias}")
    return holes


# ------------------------------------------------------------------ 홀 메시 제어
def apply_hole_transfinite(holes: Sequence[Hole], cfg: MeshConfig, log: Logger) -> int:
    """홀 원주에 정확히 hole_nodes개 절점이 놓이도록 transfinite 지정."""
    ok = 0
    for h in holes:
        try:
            # 닫힌 곡선은 seam 절점이 중복 계수되므로 +1
            gmsh.model.mesh.setTransfiniteCurve(h.curve, cfg.hole_nodes + 1)
            ok += 1
        except Exception:
            pass
    if ok:
        log(f"  홀 원주 노드 {cfg.hole_nodes}개 강제 적용: {ok}/{len(holes)}")
    return ok


def hole_size_field(holes: Sequence[Hole], cfg: MeshConfig) -> Optional[int]:
    """홀 주변 washer 폭 안쪽을 원주 요소크기로 유지하는 Distance/Threshold field.

    정렬 washer가 실패했거나 tetra처럼 정렬 링을 못 만드는 경우의 대체 수단.
    """
    if not holes:
        return None
    fields = []
    for h in holes:
        target = 2.0 * math.pi * h.radius / max(cfg.hole_nodes, 3)
        d = gmsh.model.mesh.field.add("Distance")
        gmsh.model.mesh.field.setNumbers(d, "CurvesList", [h.curve])
        gmsh.model.mesh.field.setNumber(d, "Sampling", 200)
        t = gmsh.model.mesh.field.add("Threshold")
        gmsh.model.mesh.field.setNumber(t, "InField", d)
        gmsh.model.mesh.field.setNumber(t, "SizeMin", target)
        gmsh.model.mesh.field.setNumber(t, "SizeMax", cfg.element_size)
        gmsh.model.mesh.field.setNumber(t, "DistMin", cfg.washer_width)
        gmsh.model.mesh.field.setNumber(t, "DistMax", cfg.washer_width * 2.0)
        fields.append(t)
    mn = gmsh.model.mesh.field.add("Min")
    gmsh.model.mesh.field.setNumbers(mn, "FieldsList", fields)
    return mn


def build_structured_washer(hole: Hole, cfg: MeshConfig) -> bool:
    """홀 주위에 4분할 정렬 washer 링을 생성한다.

    반환값이 False면 형상 분할에 실패한 것이므로 size field로 대체해야 한다.
    절차: (1) 반지름 r+w 디스크를 면에 imprint  (2) 4방향 반경선으로 링을 4등분
          (3) 각 sector를 transfinite + recombine.
    """
    r, w, n = hole.radius, cfg.washer_width, hole.normal
    c = hole.center
    seg = max(2, round(cfg.hole_nodes / 4))          # 사분면당 원주 분할 수
    nr = max(1, round(w / min(cfg.element_size, 2.0 * math.pi * r / cfg.hole_nodes)))

    disk = gmsh.model.occ.addDisk(c[0], c[1], c[2], r + w, r + w, zAxis=list(n))
    out, _ = gmsh.model.occ.fragment([(2, hole.face)], [(2, disk)])
    gmsh.model.occ.synchronize()

    # imprint 후 링 면 = 중심에서 r ~ r+w 범위에 있는 작은 면
    ring = []
    for dim, tag in out:
        if dim != 2:
            continue
        a = face_area(tag)
        if a <= 1e-9:
            continue
        ctr = face_center(tag)
        if norm(sub(ctr, c)) < 0.25 * r and abs(a - math.pi * ((r + w) ** 2 - r * r)) < 0.25 * a:
            ring.append(tag)
    if len(ring) != 1:
        return False

    u, v = perp_basis(n)
    dirs = [u, v, scale(u, -1.0), scale(v, -1.0)]
    cutters = []
    for d in dirs:
        p0 = [c[i] + d[i] * r for i in range(3)]
        p1 = [c[i] + d[i] * (r + w) for i in range(3)]
        a = gmsh.model.occ.addPoint(*p0)
        b = gmsh.model.occ.addPoint(*p1)
        cutters.append((1, gmsh.model.occ.addLine(a, b)))
    out2, _ = gmsh.model.occ.fragment([(2, ring[0])], cutters)
    gmsh.model.occ.synchronize()

    sectors = [t for d, t in out2 if d == 2]
    if len(sectors) != 4:
        return False

    for s in sectors:
        edges = [abs(t) for _, t in gmsh.model.getBoundary([(2, s)],
                                                          combined=False, oriented=False)]
        for e in edges:
            length = gmsh.model.occ.getMass(1, e)
            if gmsh.model.getType(1, e) == "Line" and abs(length - w) < 0.05 * w:
                gmsh.model.mesh.setTransfiniteCurve(e, nr + 1)   # 반경 방향
            else:
                gmsh.model.mesh.setTransfiniteCurve(e, seg + 1)  # 원주 방향
        gmsh.model.mesh.setTransfiniteSurface(s)
        gmsh.model.mesh.setRecombine(2, s)
    return True


def apply_washers(holes: Sequence[Hole], cfg: MeshConfig, log: Logger,
                  meta: Optional[dict] = None) -> List[Hole]:
    """정렬 washer를 시도하고, 실패한 홀 목록을 돌려준다.

    meta(면 태그 -> 두께)를 넘기면 washer 때문에 쪼개진 자식 면들이
    부모 면의 두께를 물려받는다. 이게 없으면 *SHELL SECTION을 쓸 때
    두께 그룹이 통째로 날아간다.
    """
    if not cfg.structured_washer or cfg.washer_width <= 0:
        return list(holes)
    failed: List[Hole] = []
    made = 0
    for h in holes:
        before = {t for _, t in gmsh.model.getEntities(2)}
        try:
            ok = build_structured_washer(h, cfg)
        except Exception:
            ok = False
        if ok:
            made += 1
        else:
            failed.append(h)
        if meta is not None and h.face in meta:
            parent = meta[h.face]
            after = {t for _, t in gmsh.model.getEntities(2)}
            for t in after - before:
                meta[t] = parent
            if h.face not in after:
                meta.pop(h.face, None)
    if made:
        log(f"  정렬 washer 생성 {made}개 (폭 {cfg.washer_width})")
    if failed:
        log(f"  washer 분할 실패 {len(failed)}개 — 크기 필드로 대체")
    return failed


def setup_holes(face_tags: Sequence[int], cfg: MeshConfig, log: Logger,
                structured: bool = True,
                meta: Optional[dict] = None) -> Optional[int]:
    """홀 검출 -> washer 생성 -> 원주 노드 고정 -> 남은 홀에 크기 필드.

    반환값은 Min field 태그(없으면 None). washer가 만들어진 홀은 내부 원이
    4개 호로 쪼개지므로 재검출에서 자연히 빠지고, 실패한 홀만 크기 필드를 받는다.
    """
    holes = find_holes(face_tags, cfg, log)
    if not holes:
        return None
    if structured and cfg.structured_washer and cfg.washer_width > 0:
        apply_washers(holes, cfg, log, meta=meta)
        current = [t for _, t in gmsh.model.getEntities(2)]
        holes = find_holes(current, cfg, lambda _m: None)
    if not holes:
        return None
    apply_hole_transfinite(holes, cfg, log)
    return hole_size_field(holes, cfg)
