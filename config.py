"""메시 파라미터 정의 및 프리셋 입출력.

버전 이력
---------
v1.5 (수정본)
  - 압출 캡 검출 관련 옵션 추가: hex_cap_area_tol(캡 면적 차이 허용),
    hex_force_extrusion(판정 실패 시 최장축으로 강제 진행).
    캡이 여러 장으로 쪼개져 있거나 끝단에 가공 형상이 있는 모델 대응.
v1.4 (수정본)
  - PartType.coerce() 추가. PartType이 str Enum이라 Qt 위젯을 거치면
    평범한 str로 돌아오고, 그러면 'is PartType.EXTRUSION' 비교가 전부
    False가 되어 무조건 tetra로 빠지는 문제가 있었다.
v1.3 (수정본)
  - auto_make_solid 추가. 겉면 surface만 들어있는 STEP을 sew/heal해서
    solid로 복원한다. 이게 되어야 압출 hexa나 tetra를 적용할 수 있다.
  - shell_default_thickness는 이제 "면쌍에서 두께를 못 재는 경우"에만 쓰인다.
v1.2 (수정본)
  - 출력 형식을 inp(Abaqus)와 k(LS-DYNA) 두 가지로 정리.
  - surface 전용 STEP 지원을 위해 shell_default_thickness 추가.
    solid이 없는 STEP은 두께 정보를 알 수 없으므로 이 값으로 SECTION을 쓴다.
v1.0
  - 최초 작성.
"""
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

    @classmethod
    def coerce(cls, value) -> "PartType":
        """문자열이든 Enum이든 PartType으로 정규화.

        PartType은 str을 상속하므로 Qt를 거치면 평범한 str로 돌아온다.
        경계에서 반드시 이 함수를 통과시켜야 한다.
        """
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value))
        except ValueError:
            return cls.AUTO

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
    auto_make_solid: bool = True   # 면만 있는 STEP을 sew해서 solid로 복원
    heal_tolerance: float = 1e-4   # sew/heal 허용오차 (모델 단위)

    # ---------------- 자동 분류 ----------------
    thin_thickness_max: float = 6.0   # 등가두께가 이 값 이하면 얇은 판재로 봄
    extrusion_tol: float = 0.03       # V ≈ A_cap × L 판정 허용오차

    # ---------------- shell (프레스) ----------------
    shell_max_thickness: float = 6.0   # 이 두께 이하 면쌍만 mid-surface 후보
    shell_area_tol: float = 0.35       # 면쌍 면적 차이 허용 비율
    shell_quad_dominant: bool = True   # quad-dominant (S4R 위주)
    shell_imprint: bool = True         # mid-surface 패치끼리 imprint하여 절점 공유
    shell_fallback_offset: bool = True  # 면쌍 실패 시 최대면 오프셋으로 대체
    shell_default_thickness: float = 1.0  # 면쌍에서 두께를 못 잴 때만 쓰는 값

    # ---------------- tetra (사출) ----------------
    tet_min_size_factor: float = 0.25  # 최소 크기 = element_size × factor
    tet_curvature_nodes: int = 12      # 곡률 1바퀴당 요소 수
    tet_algorithm: int = 10            # 1=Delaunay, 10=HXT

    # ---------------- hexa (압출) ----------------
    hex_through_layers: int = 2     # 벽 두께방향 요소 층 수
    hex_axial_size: float = 0.0     # 압출 방향 요소 크기 (0이면 element_size 사용)
    hex_wall_thickness: float = 0.0  # 0이면 단면에서 자동 산출
    hex_full_quad: bool = True      # blossom full-quad로 100% hexa 유도
    hex_cap_area_tol: float = 0.05  # 양 끝 캡 면적 차이 허용 비율
    hex_force_extrusion: bool = False  # 판정 실패 시 최장축 기준으로 강제 진행

    # ---------------- 출력 ----------------
    export_formats: List[str] = field(default_factory=lambda: ["inp"])  # inp / k
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
