"""메시 파라미터 정의 및 프리셋 입출력."""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field, fields
from enum import Enum
from pathlib import Path
from typing import List


class PartType(str, Enum):
    AUTO = "auto"            # 형상 지표로 자동 판정
    PRESS = "press"          # 얇은 프레스 -> mid-surface shell
    INJECTION = "injection"  # 복잡한 사출 -> tetra solid
    EXTRUSION = "extrusion"  # 압출 -> 두께방향 layer hexa

    @property
    def label(self) -> str:
        return {
            "auto": "자동 판정",
            "press": "프레스 (shell)",
            "injection": "사출 (tetra)",
            "extrusion": "압출 (hexa)",
        }[self.value]


@dataclass
class MeshConfig:
    # ---------------- 공통 ----------------
    element_size: float = 5.0      # 기본 요소 크기
    hole_nodes: int = 8            # 홀 원주 노드 수
    washer_width: float = 3.0      # 홀 주변 washer 폭
    min_hole_dia: float = 1.0      # 이 지름 미만 홀은 무시
    max_hole_dia: float = 80.0     # 이 지름 초과 원형 엣지는 홀로 안 봄
    structured_washer: bool = True  # 4분할 정렬 washer 시도 (실패 시 size field로 fallback)
    second_order: bool = False     # 2차 요소 (S8R / C3D10 / C3D20)
    optimize: bool = True          # netgen 최적화
    scale: float = 1.0             # STEP 단위 보정 (m 입력이면 1000)

    # ---------------- 자동 분류 ----------------
    thin_thickness_max: float = 6.0   # 등가두께가 이 값 이하면 얇은 판재로 봄
    extrusion_tol: float = 0.03       # V ≈ A_cap × L 판정 허용오차

    # ---------------- shell (프레스) ----------------
    shell_max_thickness: float = 6.0   # 이 두께 이하 면쌍만 mid-surface 후보
    shell_area_tol: float = 0.35       # 면쌍 면적 차이 허용 비율
    shell_quad_dominant: bool = True   # quad-dominant (S4R 위주)
    shell_imprint: bool = True         # mid-surface 패치끼리 imprint하여 절점 공유
    shell_fallback_offset: bool = True  # 면쌍 실패 시 최대면 오프셋으로 대체

    # ---------------- tetra (사출) ----------------
    tet_min_size_factor: float = 0.25  # 최소 크기 = element_size × factor
    tet_curvature_nodes: int = 12      # 곡률 1바퀴당 요소 수
    tet_algorithm: int = 10            # 1=Delaunay, 10=HXT

    # ---------------- hexa (압출) ----------------
    hex_through_layers: int = 2     # 벽 두께방향 요소 층 수
    hex_axial_size: float = 0.0     # 압출 방향 요소 크기 (0이면 element_size 사용)
    hex_wall_thickness: float = 0.0  # 0이면 단면에서 자동 산출
    hex_full_quad: bool = True      # blossom full-quad로 100% hexa 유도

    # ---------------- 출력 ----------------
    export_formats: List[str] = field(default_factory=lambda: ["inp"])
    write_sections: bool = True     # shell 두께를 *SHELL SECTION으로 함께 출력
    quality_metric: str = "minSICN"  # 품질 리포트 지표
    quality_warn: float = 0.3       # 이 값 미만이면 경고 카운트

    # ---------------- 파생값 ----------------
    def axial_size(self) -> float:
        return self.hex_axial_size if self.hex_axial_size > 0 else self.element_size

    def min_size(self) -> float:
        return max(self.element_size * self.tet_min_size_factor, 1e-3)

    # ---------------- 프리셋 ----------------
    def save(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8"
        )

    @classmethod
    def load(cls, path: str | Path) -> "MeshConfig":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})
