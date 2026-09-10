"""파일 하나를 받아 분류 -> 메시 -> 품질확인 -> 저장까지 수행.

버전 이력
---------
v1.2 (수정본)
  - solid이 없는 surface 전용 STEP을 실패 처리하던 것을 고쳐, 들어온 면을
    그대로 mid-surface로 보고 shell 메시하도록 분기 추가.
v1.0
  - 최초 작성.
"""
from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

import gmsh

from . import exporter, geometry as g, hexa, shell, tetra
from .config import MeshConfig, PartType

Logger = Callable[[str], None]

ELEMENT_NAMES = {
    1: "line2", 2: "tri3", 3: "quad4", 4: "tet4", 5: "hex8", 6: "prism6",
    7: "pyr5", 8: "line3", 9: "tri6", 10: "quad9", 11: "tet10", 12: "hex27",
    16: "quad8", 17: "hex20", 18: "prism15",
}


@dataclass
class Result:
    source: str
    part: str
    part_type: PartType
    reason: str = ""
    files: List[str] = field(default_factory=list)
    nodes: int = 0
    elements: Dict[str, int] = field(default_factory=dict)
    quality_min: float = 0.0
    quality_avg: float = 0.0
    quality_bad: int = 0
    extra: Dict[str, object] = field(default_factory=dict)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error

    def summary(self) -> str:
        if self.error:
            return f"{self.part}: 실패 — {self.error}"
        el = ", ".join(f"{k} {v:,}" for k, v in sorted(self.elements.items()))
        return (f"{self.part} [{self.part_type.label}] "
                f"node {self.nodes:,} / {el} / "
                f"품질 min {self.quality_min:.3f} avg {self.quality_avg:.3f}"
                + (f" / 저품질 {self.quality_bad}" if self.quality_bad else ""))


def _count_elements() -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for dim in (2, 3):
        etypes, etags, _ = gmsh.model.mesh.getElements(dim)
        for et, tags in zip(etypes, etags):
            name = ELEMENT_NAMES.get(int(et), f"type{int(et)}")
            counts[name] = counts.get(name, 0) + len(tags)
    return counts


def _quality(cfg: MeshConfig) -> tuple[float, float, int]:
    dim = 3 if gmsh.model.getEntities(3) else 2
    etypes, etags, _ = gmsh.model.mesh.getElements(dim)
    tags: List[int] = []
    for arr in etags:
        tags.extend(int(t) for t in arr)
    if not tags:
        return 0.0, 0.0, 0
    try:
        q = gmsh.model.mesh.getElementQualities(tags, cfg.quality_metric)
    except Exception:
        return 0.0, 0.0, 0
    q = list(q)
    bad = sum(1 for v in q if v < cfg.quality_warn)
    return min(q), sum(q) / len(q), bad


def _isolate(solid_tag: int) -> None:
    """대상 solid 외의 volume을 제거해 단독 처리한다."""
    for _, t in list(gmsh.model.getEntities(3)):
        if t != solid_tag:
            try:
                gmsh.model.occ.remove([(3, t)], recursive=True)
            except Exception:
                pass
    gmsh.model.occ.synchronize()


def run_file(step_path: str, requested: PartType, cfg: MeshConfig,
             outdir: str, log: Logger) -> List[Result]:
    src = Path(step_path)
    out_root = Path(outdir)
    out_root.mkdir(parents=True, exist_ok=True)
    results: List[Result] = []

    # 1차 로드: solid / surface 개수 파악
    g.start()
    try:
        solids, surfaces = g.load_shapes(str(src), cfg, log)
    except Exception as exc:
        g.stop()
        return [Result(str(src), src.stem, requested, error=f"STEP 로드 실패: {exc}")]
    n_solids, n_surfaces = len(solids), len(surfaces)
    g.stop()

    if n_solids == 0:
        if n_surfaces == 0:
            return [Result(str(src), src.stem, requested,
                           error="STEP 안에 solid도 surface도 없습니다")]
        return [_run_surface_only(src, cfg, out_root, log)]

    for idx, solid in enumerate(solids, start=1):
        name = src.stem if n_solids == 1 else f"{src.stem}_p{idx:02d}"
        res = Result(str(src), name, requested)
        log(f"[{name}] 처리 시작")
        try:
            g.start()
            g.load_step(str(src), cfg, lambda _m: None)
            _isolate(solid)
            info = g.solid_info(solid)

            part_type = requested
            if part_type is PartType.AUTO:
                part_type, reason = g.classify(info, cfg, log)
                res.reason = reason
                log(f"  자동 판정: {part_type.label} — {reason}")
            res.part_type = part_type

            if part_type is PartType.PRESS:
                extra = shell.mesh(info, cfg, log)
            elif part_type is PartType.EXTRUSION:
                extra = hexa.mesh(info, cfg, log)
            else:
                extra = tetra.mesh(info, cfg, log)
            res.extra = extra

            node_tags, _, _ = gmsh.model.mesh.getNodes()
            res.nodes = len(node_tags)
            res.elements = _count_elements()
            res.quality_min, res.quality_avg, res.quality_bad = _quality(cfg)

            groups = extra.get("thickness_groups") or {}
            res.files = exporter.write(out_root / name, cfg, part_type,
                                       groups if isinstance(groups, dict) else {}, log)
            log(f"  완료 — {res.summary()}")
        except Exception as exc:
            res.error = str(exc)
            log(f"  [오류] {exc}")
            log(traceback.format_exc(limit=3))
        finally:
            g.stop()
        results.append(res)

    return results


def _run_surface_only(src: Path, cfg: MeshConfig, out_root: Path,
                      log: Logger) -> Result:
    """solid 없이 면만 들어있는 STEP (mid-surface STEP) 처리."""
    name = src.stem
    res = Result(str(src), name, PartType.PRESS,
                 reason="solid 없음 — 입력 surface를 mid-surface로 사용")
    log(f"[{name}] surface 전용 STEP — shell 메시로 처리")
    try:
        g.start()
        _, surfaces = g.load_shapes(str(src), cfg, lambda _m: None)
        extra = shell.mesh_surfaces(surfaces, cfg, log)
        res.extra = extra

        node_tags, _, _ = gmsh.model.mesh.getNodes()
        res.nodes = len(node_tags)
        res.elements = _count_elements()
        res.quality_min, res.quality_avg, res.quality_bad = _quality(cfg)

        groups = extra.get("thickness_groups") or {}
        res.files = exporter.write(out_root / name, cfg, PartType.PRESS,
                                   groups if isinstance(groups, dict) else {}, log)
        log(f"  완료 — {res.summary()}")
    except Exception as exc:
        res.error = str(exc)
        log(f"  [오류] {exc}")
        log(traceback.format_exc(limit=3))
    finally:
        g.stop()
    return res


def run_batch(items: List[tuple[str, PartType]], cfg: MeshConfig, outdir: str,
              log: Logger, progress: Optional[Callable[[int, int], None]] = None,
              should_stop: Optional[Callable[[], bool]] = None) -> List[Result]:
    all_res: List[Result] = []
    total = len(items)
    for i, (path, ptype) in enumerate(items):
        if should_stop is not None and should_stop():
            log("사용자 중단")
            break
        all_res.extend(run_file(path, ptype, cfg, outdir, log))
        if progress is not None:
            progress(i + 1, total)
    return all_res
