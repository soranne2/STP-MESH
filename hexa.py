"""압출 파트: 단면을 quad로 깔고 압출 방향으로 sweep해 hexa를 만든다.

핵심 아이디어는 형상을 다시 쓰는 것이다. 진짜 압출물이면 캡(단면) 면과
압출 벡터만으로 원형상을 정확히 복원할 수 있으므로, 캡을 복사해 quad로 메시하고
gmsh의 extrude(recombine=True)로 층을 쌓으면 100% hexa가 나온다.

'두께방향 layer'는 단면 벽 두께를 hex_through_layers로 나눈 값을
단면 요소 크기로 잡아 구현한다 (예: 벽 2.5mm, 2층 -> 단면 크기 1.25mm).

버전 이력
---------
v1.5 (수정본)
  - 캡이 여러 장으로 쪼개진 형상 지원 (Extrusion.caps가 리스트).
  - 압출 판정 실패 시 원인을 축별 수치로 로그에 남기고, hex_force_extrusion이
    켜져 있으면 가장 긴 축으로 강제 진행한다.
  - 진행 단계 콜백(stage) 지원.
v1.3 (수정본)
  - 원통 등 주기적 면에서 full-quad 재조합이 실패하던 문제 대응.
    generate_mesh()를 통해 호출해 blossom으로 자동 재시도한다.
v1.0
  - 최초 작성.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional

import gmsh

from . import geometry as g
from .config import MeshConfig

Logger = Callable[[str], None]
Stage = Optional[Callable[[float, str], None]]


def wall_thickness(caps: List[int]) -> float:
    """얇은 벽 단면의 등가 벽두께 ~= 2 x 단면적 / 단면 둘레."""
    area = sum(g.face_area(c) for c in caps)
    peri = 0.0
    for c in caps:
        for _, e in gmsh.model.getBoundary([(2, c)], combined=False, oriented=False):
            peri += gmsh.model.occ.getMass(1, abs(e))
    return 2.0 * area / peri if peri > 1e-12 else 0.0


def _resolve_extrusion(info: g.SolidInfo, cfg: MeshConfig, log: Logger):
    ext = g.detect_extrusion(info, cfg)
    if ext is not None:
        return ext

    log("  압출 판정 실패 — 축별 진단:")
    log("    " + g.describe_extrusion_failure(info, cfg))
    if not cfg.hex_force_extrusion:
        raise RuntimeError(
            "압출 단면을 찾지 못했습니다. 위 진단의 면적차/체적오차를 보고 "
            "'캡 면적 차이 허용'이나 '압출 판정 허용'을 그 값보다 크게 잡거나, "
            "'압출 강제 진행'을 켜세요"
        )
    ext = g.detect_extrusion_forced(info, cfg)
    if ext is None:
        raise RuntimeError("강제 모드에서도 평행한 끝면 쌍을 찾지 못했습니다")
    log(f"  [강제] 최장축을 압출 방향으로 사용 — 체적오차 {ext.vol_err * 100:.1f}%")
    log("    단면이 일정하지 않으면 결과 형상이 원본과 달라질 수 있습니다")
    return ext


def mesh(info: g.SolidInfo, cfg: MeshConfig, log: Logger,
         stage: Stage = None) -> Dict[str, object]:
    def step(frac: float, text: str) -> None:
        if stage is not None:
            stage(frac, text)

    step(0.05, "압출 단면 검출")
    ext = _resolve_extrusion(info, cfg, log)

    t_wall = cfg.hex_wall_thickness or wall_thickness(ext.caps)
    section_size = t_wall / max(cfg.hex_through_layers, 1) if t_wall > 0 else cfg.element_size
    section_size = min(max(section_size, cfg.element_size * 0.02), cfg.element_size)
    n_axial = max(1, round(ext.length / cfg.axial_size()))

    log(f"  압출 길이 {ext.length:.2f}, 캡 {len(ext.caps)}장, 벽두께 {t_wall:.2f}")
    log(f"  단면 크기 {section_size:.3f} (두께방향 {cfg.hex_through_layers}층), "
        f"길이방향 {n_axial}층")

    # 캡만 남기고 원형상 제거
    step(0.20, "단면 추출")
    copied = gmsh.model.occ.copy([(2, c) for c in ext.caps])
    gmsh.model.occ.synchronize()
    keep = {t for _, t in copied}
    try:
        gmsh.model.occ.remove([(3, info.tag)], recursive=False)
    except Exception:
        pass
    gmsh.model.occ.synchronize()
    for d, t in list(gmsh.model.getEntities(2)):
        if t not in keep:
            try:
                gmsh.model.occ.remove([(d, t)], recursive=False)
            except Exception:
                pass
    gmsh.model.occ.synchronize()
    cap_tags: List[int] = sorted(keep)

    # 홀 처리는 단면(2D) 상태에서 해야 washer 링이 그대로 sweep된다
    step(0.30, "홀 / washer 처리")
    field = g.setup_holes(cap_tags, cfg, log, structured=True)
    cap_tags = [t for _, t in gmsh.model.getEntities(2)]

    step(0.40, "압출")
    vec = g.scale(ext.axis, ext.length)
    out = gmsh.model.occ.extrude(
        [(2, t) for t in cap_tags], vec[0], vec[1], vec[2],
        numElements=[n_axial], recombine=True,
    )
    gmsh.model.occ.synchronize()
    vols = [t for d, t in out if d == 3]
    if not vols:
        raise RuntimeError("압출(extrude) 결과에 volume이 없습니다")

    gmsh.option.setNumber("Mesh.MeshSizeMin", section_size * 0.5)
    gmsh.option.setNumber("Mesh.MeshSizeMax", section_size)
    gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 0)
    gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 0)
    gmsh.option.setNumber("Mesh.Algorithm", 8)
    gmsh.option.setNumber("Mesh.RecombineAll", 1)
    gmsh.option.setNumber("Mesh.RecombinationAlgorithm", 3 if cfg.hex_full_quad else 1)
    gmsh.option.setNumber("Mesh.SubdivisionAlgorithm", 0)
    if field is not None:
        gmsh.model.mesh.field.setAsBackgroundMesh(field)
    for t in cap_tags:
        try:
            gmsh.model.mesh.setRecombine(2, t)
        except Exception:
            pass

    step(0.50, "메시 생성")
    g.generate_mesh(3, log)
    if cfg.second_order:
        gmsh.model.mesh.setOrder(2)
        gmsh.option.setNumber("Mesh.SecondOrderIncomplete", 1)  # C3D20R

    pg = gmsh.model.addPhysicalGroup(3, vols)
    gmsh.model.setPhysicalName(3, pg, "SOLID_HEX")
    return {"axial_layers": float(n_axial), "wall_thickness": t_wall}
