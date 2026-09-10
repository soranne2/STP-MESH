"""메시 출력. Abaqus .inp는 후처리로 요소 타입과 SECTION까지 맞춰준다.

gmsh의 Abaqus writer는 shell 요소를 평면응력(CPS3/CPS4)으로 써버리기 때문에
그대로 Abaqus에 넣으면 S3/S4R로 안 잡힌다. 여기서 키워드를 치환하고
두께별 *SHELL SECTION을 붙여 바로 해석에 들어갈 수 있는 상태로 만든다.

버전 이력
---------
v1.2 (수정본)
  - 출력 형식을 inp(Abaqus)와 k(LS-DYNA) 두 가지로 정리.
    gmsh의 LS-DYNA writer는 .key 확장자만 받으므로 저장 후 .k로 바꾼다.
v1.0
  - 최초 작성.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Dict, List

import gmsh

from .config import MeshConfig, PartType

Logger = Callable[[str], None]

# gmsh가 쓰는 타입 -> Abaqus 실사용 타입
SHELL_MAP = {
    "CPS3": "S3", "CPS4": "S4R", "CPS6": "STRI65", "CPS8": "S8R",
    "STRI3": "S3", "T3D2": "B31",
}
SOLID_MAP = {
    "C3D8": "C3D8R", "C3D20": "C3D20R",
}


def write(base: Path, cfg: MeshConfig, part_type: PartType,
          thickness_groups: Dict[str, int], log: Logger) -> List[str]:
    gmsh.option.setNumber("Mesh.SaveAll", 0)
    gmsh.option.setNumber("Mesh.SaveGroupsOfNodes", 1)
    gmsh.option.setNumber("Mesh.SaveGroupsOfElements", 1)

    written: List[str] = []
    for fmt in cfg.export_formats:
        try:
            if fmt == "k":
                out = _write_dyna(base, log)
            else:
                out = base.with_suffix("." + fmt)
                gmsh.write(str(out))
        except Exception as exc:
            log(f"  [경고] {fmt} 저장 실패: {exc}")
            continue
        if fmt == "inp":
            _fix_inp(out, cfg, part_type, thickness_groups, log)
        written.append(str(out))
        log(f"  저장: {out.name}")
    return written


def _write_dyna(base: Path, log: Logger) -> Path:
    """LS-DYNA keyword 파일 저장.

    gmsh는 .key 확장자만 LS-DYNA writer로 인식하므로 그렇게 쓴 뒤 .k로 바꾼다.
    """
    tmp = base.with_suffix(".key")
    gmsh.write(str(tmp))
    out = base.with_suffix(".k")
    if out.exists():
        out.unlink()
    tmp.rename(out)
    return out


def _fix_inp(path: Path, cfg: MeshConfig, part_type: PartType,
             thickness_groups: Dict[str, int], log: Logger) -> None:
    text = path.read_text(encoding="utf-8", errors="ignore")
    mapping = SHELL_MAP if part_type is PartType.PRESS else SOLID_MAP
    swapped = 0
    for src, dst in mapping.items():
        needle = f"type={src}"
        if needle in text:
            swapped += text.count(needle)
            text = text.replace(needle, f"type={dst}")
        needle_u = f"TYPE={src}"
        if needle_u in text:
            swapped += text.count(needle_u)
            text = text.replace(needle_u, f"TYPE={dst}")

    lines = [text.rstrip("\n")]
    if part_type is PartType.PRESS and cfg.write_sections and thickness_groups:
        lines.append("**")
        lines.append("** 두께별 shell section — MATERIAL 이름만 맞춰서 쓰면 된다")
        for t_str in sorted(thickness_groups):
            t = float(t_str)
            elset = f"SHELL_T{t:.2f}".replace(".", "p")
            lines.append(f"*SHELL SECTION, ELSET={elset}, MATERIAL=MAT_STEEL")
            lines.append(f"{t:.4f}, 5")
    elif part_type in (PartType.INJECTION, PartType.EXTRUSION) and cfg.write_sections:
        elset = "SOLID_TET" if part_type is PartType.INJECTION else "SOLID_HEX"
        lines.append("**")
        lines.append(f"*SOLID SECTION, ELSET={elset}, MATERIAL=MAT_1")
        lines.append(",")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if swapped:
        log(f"  요소 타입 {swapped}건 Abaqus 표준으로 치환")
