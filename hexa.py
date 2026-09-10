"""압출 파트: 단면을 quad로 깔고 압출 방향으로 sweep해 hexa를 만든다.

핵심 아이디어는 형상을 다시 쓰는 것이다. 진짜 압출물이면 캡(단면) 면 하나와
압출 벡터만으로 원형상을 정확히 복원할 수 있으므로, 캡을 복사해 quad로 메시하고
gmsh의 extrude(recombine=True)로 층을 쌓으면 100% hexa가 나온다.

'두께방향 layer'는 단면 벽 두께를 hex_through_layers로 나눈 값을
단면 요소 크기로 잡아 구현한다 (예: 벽 2.5mm, 2층 -> 단면 크기 1.25mm).
"""
from __future__ import annotations

import math
from typing import Callable, Dict, List

import gmsh

from . import geometry as g
from .config import MeshConfig

Logger = Callable[[str], None]


def wall_thickness(cap: int) -> float:
    """얇은 벽 단면의 등가 벽두께 ≈ 2 × 단면적 / 단면 둘레."""
    area = g.face_area(cap)
    peri = 0.0
    for _, c in gmsh.model.getBoundary([(2, cap)], combined=False, oriented=False):
        peri += gmsh.model.occ.getMass(1, abs(c))
    return 2.0 * area / peri if peri > 1e-12 else 0.0


def mesh(info: g.SolidInfo, cfg: MeshConfig, log: Logger) -> Dict[str, float]:
    ext = g.detect_extrusion(info, cfg)
    if ext is None:
        raise RuntimeError(
            "압출 단면(평행한 동일 면적 캡 한 쌍)을 찾지 못했습니다. "
            "사출(tetra)로 바꾸거나 extrusion_tol을 키워보세요."
        )

    t_wall = cfg.hex_wall_thickness or wall_thickness(ext.cap)
    section_size = t_wall / max(cfg.hex_through_layers, 1) if t_wall > 0 else cfg.element_size
    section_size = min(max(section_size, cfg.element_size * 0.02), cfg.element_size)
    n_axial = max(1, round(ext.length / cfg.axial_size()))

    log(f"  압출 길이 {ext.length:.2f}, 벽두께 {t_wall:.2f}")
    log(f"  단면 크기 {section_size:.3f} (두께방향 {cfg.hex_through_layers}층), "
        f"길이방향 {n_axial}층")

    # 캡만 남기고 원형상 제거
    copied = gmsh.model.occ.copy([(2, ext.cap)])
    gmsh.model.occ.synchronize()
    keep = {t for _, t in copied}
    gmsh.model.occ.remove([(3, info.tag)], recursive=False)
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
    field = g.setup_holes(cap_tags, cfg, log, structured=True)
    cap_tags = [t for _, t in gmsh.model.getEntities(2)]

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
    gmsh.option.setNumber("Mesh.RecombinationAlgorithm", 3 if cfg.hex_full_quad else 2)
    gmsh.option.setNumber("Mesh.SubdivisionAlgorithm", 0)
    if field is not None:
        gmsh.model.mesh.field.setAsBackgroundMesh(field)
    for t in cap_tags:
        try:
            gmsh.model.mesh.setRecombine(2, t)
        except Exception:
            pass

    gmsh.model.mesh.generate(3)
    if cfg.second_order:
        gmsh.model.mesh.setOrder(2)
        gmsh.option.setNumber("Mesh.SecondOrderIncomplete", 1)  # C3D20R

    pg = gmsh.model.addPhysicalGroup(3, vols)
    gmsh.model.setPhysicalName(3, pg, "SOLID_HEX")
    return {"axial_layers": float(n_axial), "wall_thickness": t_wall}
