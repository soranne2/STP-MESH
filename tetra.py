"""복잡한 사출 파트: tetra 기반 3D solid 메시.

사출물은 리브/보스/필렛이 많아 정렬 메시가 사실상 불가능하므로
곡률 기반 크기 제어 + 홀 주변 국부 세밀화를 조합한다.
tetra에는 정렬 washer 링을 만들 수 없어 홀 처리는
'원주 노드 8개 고정 + washer 폭 안쪽 크기 유지'로 구현한다.
"""
from __future__ import annotations

from typing import Callable, Dict

import gmsh

from . import geometry as g
from .config import MeshConfig

Logger = Callable[[str], None]


def mesh(info: g.SolidInfo, cfg: MeshConfig, log: Logger) -> Dict[str, float]:
    field = g.setup_holes(info.faces, cfg, log, structured=False)

    gmsh.option.setNumber("Mesh.MeshSizeMin", cfg.min_size())
    gmsh.option.setNumber("Mesh.MeshSizeMax", cfg.element_size)
    gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", cfg.tet_curvature_nodes)
    gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 1)
    gmsh.option.setNumber("Mesh.Algorithm", 6)                  # 2D: Frontal-Delaunay
    gmsh.option.setNumber("Mesh.Algorithm3D", cfg.tet_algorithm)
    gmsh.option.setNumber("Mesh.RecombineAll", 0)
    if field is not None:
        gmsh.model.mesh.field.setAsBackgroundMesh(field)
        # 배경 필드를 쓸 때 곡률 제어가 덮어쓰지 않도록 Min으로 합친다
        gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", cfg.tet_curvature_nodes)

    log(f"  tetra 생성 (size {cfg.element_size}, min {cfg.min_size():.2f})")
    gmsh.model.mesh.generate(3)

    if cfg.optimize:
        gmsh.model.mesh.optimize("Netgen")
        gmsh.model.mesh.optimize("HighOrderFast" if cfg.second_order else "Laplace2D")
    if cfg.second_order:
        gmsh.model.mesh.setOrder(2)
        gmsh.option.setNumber("Mesh.SecondOrderIncomplete", 0)  # C3D10

    pg = gmsh.model.addPhysicalGroup(3, [info.tag])
    gmsh.model.setPhysicalName(3, pg, "SOLID_TET")
    return {}
