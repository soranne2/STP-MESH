"""GUI 없이 배치로 돌릴 때 쓰는 진입점.

    python cli.py part1.stp part2.stp -o out --type auto --size 5 --hole-nodes 8 --washer 3
    python cli.py *.stp --preset mesh_preset.json --dry-run
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from mesher.config import MeshConfig, PartType
from mesher.pipeline import run_batch


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="STEP -> Abaqus 메시 생성기")
    p.add_argument("files", nargs="+", help="STEP 파일")
    p.add_argument("-o", "--out", default="mesh_out", help="출력 폴더")
    p.add_argument("--type", default="auto",
                   choices=[t.value for t in PartType], help="파트 유형")
    p.add_argument("--preset", help="프리셋 JSON")
    p.add_argument("--size", type=float, help="요소 크기")
    p.add_argument("--hole-nodes", type=int, help="홀 원주 노드 수")
    p.add_argument("--washer", type=float, help="washer 폭")
    p.add_argument("--layers", type=int, help="hexa 두께방향 층 수")
    p.add_argument("--format", nargs="+", default=None, help="출력 형식 (inp msh nas ...)")
    p.add_argument("--dry-run", action="store_true",
                   help="분류와 설정만 출력하고 메시는 만들지 않음")
    a = p.parse_args(argv)

    cfg = MeshConfig.load(a.preset) if a.preset else MeshConfig()
    if a.size is not None:
        cfg.element_size = a.size
    if a.hole_nodes is not None:
        cfg.hole_nodes = a.hole_nodes
    if a.washer is not None:
        cfg.washer_width = a.washer
    if a.layers is not None:
        cfg.hex_through_layers = a.layers
    if a.format:
        cfg.export_formats = a.format

    ptype = PartType(a.type)
    items = [(str(Path(f)), ptype) for f in a.files]

    if a.dry_run:
        print(f"[dry-run] 파일 {len(items)}개, 유형 {ptype.label}")
        print(f"  요소 크기 {cfg.element_size}, 홀 노드 {cfg.hole_nodes}, "
              f"washer {cfg.washer_width}, 두께층 {cfg.hex_through_layers}")
        print(f"  출력 {cfg.export_formats} -> {a.out}")
        for f, _ in items:
            print(f"  - {f}")
        return 0

    results = run_batch(items, cfg, a.out, log=print)
    print("\n── 결과 ──")
    for r in results:
        print(" ", r.summary())
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
