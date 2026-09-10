"""메인 윈도우. 파일 목록 + 파라미터 + 로그, 메시 생성은 워커 스레드에서 돈다.

버전 이력
---------
v1.5 (수정본)
  - 오른쪽 파라미터 패널을 스크롤에서 탭(공통 / 프레스 / 사출 / 압출 / 옵션)으로 변경.
  - 프리셋 기본 위치를 프로그램 폴더 아래 presets/ 로 고정. 종료할 때
    presets/last.json에 자동 저장하고 다음 실행에서 자동으로 불러온다.
    저장/불러오기 대화상자도 이 폴더에서 시작한다.
  - 진행 바가 파일 단위가 아니라 단계 단위로 올라간다. 상태줄에 현재 단계와
    경과 시간을 함께 보여준다.
v1.4 (수정본)
  - [전체 적용]에서 AttributeError: 'str' object has no attribute 'label' 수정.
    PartType이 str Enum이라 QComboBox.currentData()가 평범한 str을 돌려준다.
    콤보에서 읽은 값은 전부 PartType.coerce()로 정규화한다. 같은 원인으로
    압출을 골라도 tetra가 돌던 문제도 함께 해결된다.
  - 진행 표시 개선: 경과 시간 표시, 파트별 소요 시간 로그.
v1.3 (수정본)
  - 공통 항목에 '면 봉합으로 solid 복원'과 '봉합 허용오차' 추가.
v1.2 (수정본)
  - '일괄 유형 변경'을 [전체 적용] 버튼 방식으로 변경.
  - 출력 형식을 inp(Abaqus)와 k(LS-DYNA) 두 가지로 정리.
  - surface 전용 STEP에서 쓸 기본 판 두께 입력란 추가.
v1.0
  - 최초 작성.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from dataclasses import fields as dc_fields
from pathlib import Path
from typing import Dict, List, Tuple

from PySide6.QtCore import QObject, QThread, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
    QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QSizePolicy, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem,
    QTabWidget, QVBoxLayout, QWidget,
)

from mesher.config import MeshConfig, PartType
from mesher.pipeline import Result, run_batch

from .theme import C, QSS

STEP_EXT = (".stp", ".step", ".STP", ".STEP")

# 프로그램 폴더 기준 프리셋 위치. 실행 위치와 무관하게 항상 같은 곳을 쓴다.
APP_DIR = Path(__file__).resolve().parents[1]
PRESET_DIR = APP_DIR / "presets"
LAST_PRESET = PRESET_DIR / "last.json"

# (필드명, 라벨, 타입, 최소, 최대, 스텝, 툴팁)
COMMON_SPEC = [
    ("element_size", "요소 크기", float, 0.05, 500.0, 0.5, "기본 mesh size"),
    ("hole_nodes", "홀 원주 노드", int, 4, 64, 4, "홀 한 바퀴에 놓일 절점 수"),
    ("washer_width", "washer 폭", float, 0.0, 100.0, 0.5, "홀 주변 정렬 링의 반경 폭"),
    ("min_hole_dia", "홀 최소 지름", float, 0.0, 500.0, 0.5, "이보다 작은 원은 무시"),
    ("max_hole_dia", "홀 최대 지름", float, 1.0, 5000.0, 5.0, "이보다 큰 원은 홀로 안 봄"),
    ("scale", "단위 배율", float, 0.001, 1000.0, 1.0, "STEP이 m 단위면 1000"),
    ("heal_tolerance", "봉합 허용오차", float, 1e-06, 10.0, 0.0001,
     "면을 꿰맬 때 허용할 틈. 형상의 실제 틈보다 커야 solid이 만들어진다"),
]
SHELL_SPEC = [
    ("shell_max_thickness", "면쌍 최대 두께", float, 0.1, 100.0, 0.5,
     "이 두께 이하의 평행면 쌍만 mid-surface 후보"),
    ("shell_area_tol", "면적 차이 허용", float, 0.0, 1.0, 0.05,
     "면쌍 판정 시 면적 차이 허용 비율"),
    ("thin_thickness_max", "박판 판정 두께", float, 0.1, 100.0, 0.5,
     "자동 판정에서 이 등가두께 이하면 프레스로 봄"),
    ("shell_default_thickness", "기본 판 두께", float, 0.01, 100.0, 0.1,
     "면쌍에서 두께를 못 잴 때만 쓰는 값"),
]
TET_SPEC = [
    ("tet_min_size_factor", "최소 크기 비율", float, 0.02, 1.0, 0.05,
     "최소 요소 크기 = 요소 크기 x 이 값"),
    ("tet_curvature_nodes", "곡률 분할 수", int, 0, 60, 2,
     "원 한 바퀴에 들어갈 요소 수 (0이면 곡률 제어 끔)"),
]
HEX_SPEC = [
    ("hex_through_layers", "두께방향 층 수", int, 1, 20, 1,
     "벽 두께를 몇 겹으로 나눌지"),
    ("hex_axial_size", "길이방향 크기", float, 0.0, 500.0, 1.0,
     "0이면 요소 크기와 동일"),
    ("hex_wall_thickness", "벽 두께 지정", float, 0.0, 100.0, 0.5,
     "0이면 단면에서 자동 산출"),
    ("extrusion_tol", "압출 판정 허용", float, 0.001, 0.9, 0.01,
     "V = 단면적 x 길이 오차 허용. 로그의 체적오차보다 크게 잡으면 통과한다"),
    ("hex_cap_area_tol", "캡 면적 차이 허용", float, 0.001, 0.9, 0.01,
     "양 끝 단면의 면적 차이 허용 비율"),
]
CHECK_SPEC = [
    ("auto_make_solid", "면 봉합으로 solid 복원"),
    ("structured_washer", "정렬 washer 사용"),
    ("shell_quad_dominant", "shell quad 우선"),
    ("shell_imprint", "패치 imprint(절점 공유)"),
    ("hex_full_quad", "hexa 100% 유도"),
    ("hex_force_extrusion", "압출 강제 진행"),
    ("second_order", "2차 요소"),
    ("optimize", "메시 최적화"),
    ("write_sections", "SECTION 자동 작성"),
]
FORMATS = {"inp": "inp (Abaqus)", "k": "k (LS-DYNA)"}


def card(title: str) -> Tuple[QFrame, QVBoxLayout]:
    f = QFrame()
    f.setObjectName("Card")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(16, 14, 16, 16)
    lay.setSpacing(10)
    if title:
        t = QLabel(title)
        t.setObjectName("SectionTitle")
        lay.addWidget(t)
    return f, lay


class Worker(QObject):
    logged = Signal(str)
    progressed = Signal(int, int)
    staged = Signal(float, str)
    finished = Signal(list)

    def __init__(self, items: List[Tuple[str, PartType]], cfg: MeshConfig, outdir: str):
        super().__init__()
        self.items, self.cfg, self.outdir = items, cfg, outdir
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        try:
            results = run_batch(
                self.items, self.cfg, self.outdir,
                log=self.logged.emit,
                progress=lambda i, n: self.progressed.emit(i, n),
                should_stop=lambda: self._stop,
                stage=lambda frac, text: self.staged.emit(frac, text),
            )
        except Exception as exc:  # 워커에서 죽어도 UI는 살려둔다
            self.logged.emit(f"[치명적 오류] {exc}")
            results = []
        self.finished.emit(results)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("STEP Mesher")
        self.resize(1240, 840)
        self.setAcceptDrops(True)
        self.cfg = MeshConfig()
        self.widgets: Dict[str, QWidget] = {}
        self.format_boxes: Dict[str, QCheckBox] = {}
        self.thread: QThread | None = None
        self.worker: Worker | None = None
        self.results: List[Result] = []
        self.started_at: float = 0.0
        self.stage_text: str = ""
        self.tick = QTimer(self)
        self.tick.setInterval(500)
        self.tick.timeout.connect(self._update_status)
        self._build()
        self._load_last_preset()

    # ------------------------------------------------------------- 레이아웃
    def _build(self) -> None:
        root = QWidget()
        root.setObjectName("Root")
        outer = QVBoxLayout(root)
        outer.setContentsMargins(22, 18, 22, 18)
        outer.setSpacing(14)

        head = QVBoxLayout()
        head.setSpacing(2)
        title = QLabel("STEP Mesher")
        title.setObjectName("Title")
        sub = QLabel("STEP 파일을 읽어 파트 유형에 맞는 메시를 만들고 내보냅니다")
        sub.setObjectName("Subtitle")
        head.addWidget(title)
        head.addWidget(sub)
        outer.addLayout(head)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(self._left_panel())
        split.addWidget(self._right_panel())
        split.setStretchFactor(0, 5)
        split.setStretchFactor(1, 4)
        outer.addWidget(split, 1)

        outer.addWidget(self._footer())
        self.setCentralWidget(root)
        self.setStyleSheet(QSS)

    def _left_panel(self) -> QWidget:
        wrap = QWidget()
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 7, 0)
        lay.setSpacing(12)

        box, inner = card("파트")
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["파일", "유형", "상태"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.Fixed)
        hh.setSectionResizeMode(2, QHeaderView.Fixed)
        self.table.setColumnWidth(1, 130)
        self.table.setColumnWidth(2, 90)
        self.table.setMinimumHeight(220)
        inner.addWidget(self.table, 1)

        hint = QLabel("STEP 파일을 창에 끌어다 놓아도 됩니다")
        hint.setObjectName("Hint")
        inner.addWidget(hint)

        btns = QHBoxLayout()
        add = QPushButton("파일 추가")
        add.clicked.connect(self.add_files)
        rem = QPushButton("선택 삭제")
        rem.clicked.connect(self.remove_selected)
        clr = QPushButton("비우기")
        clr.setObjectName("Danger")
        clr.clicked.connect(lambda: self.table.setRowCount(0))
        self.bulk = QComboBox()
        for pt in PartType:
            self.bulk.addItem(pt.label, pt)
        self.bulk.setToolTip("적용할 파트 유형을 고른 뒤 [전체 적용]을 누르세요")
        apply_all = QPushButton("전체 적용")
        apply_all.setToolTip("목록의 모든 파트를 왼쪽에서 고른 유형으로 바꿉니다")
        apply_all.clicked.connect(self._bulk_type)
        for w in (add, rem, clr):
            btns.addWidget(w)
        btns.addStretch(1)
        btns.addWidget(self.bulk)
        btns.addWidget(apply_all)
        inner.addLayout(btns)
        lay.addWidget(box, 1)

        out_box, out_lay = card("출력")
        row = QHBoxLayout()
        self.outdir = QLineEdit(str(APP_DIR / "mesh_out"))
        pick = QPushButton("폴더")
        pick.clicked.connect(self.pick_outdir)
        openb = QPushButton("열기")
        openb.clicked.connect(self.open_outdir)
        row.addWidget(self.outdir, 1)
        row.addWidget(pick)
        row.addWidget(openb)
        out_lay.addLayout(row)

        fmt = QHBoxLayout()
        fmt.addWidget(QLabel("형식"))
        for key, label in FORMATS.items():
            cb = QCheckBox(label)
            cb.setChecked(key == "inp")
            self.format_boxes[key] = cb
            fmt.addWidget(cb)
        fmt.addStretch(1)
        out_lay.addLayout(fmt)
        lay.addWidget(out_box)
        return wrap

    def _right_panel(self) -> QWidget:
        wrap = QWidget()
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(7, 0, 0, 0)
        lay.setSpacing(12)

        tabs = QTabWidget()
        tabs.addTab(self._spec_page(COMMON_SPEC), "공통")
        tabs.addTab(self._spec_page(SHELL_SPEC), "프레스")
        tabs.addTab(self._spec_page(TET_SPEC), "사출")
        tabs.addTab(self._spec_page(HEX_SPEC), "압출")
        tabs.addTab(self._options_page(), "옵션")
        lay.addWidget(tabs, 1)

        preset, p_lay = card("프리셋")
        row = QHBoxLayout()
        save = QPushButton("저장")
        save.clicked.connect(self.save_preset)
        load = QPushButton("불러오기")
        load.clicked.connect(self.load_preset)
        reset = QPushButton("기본값")
        reset.clicked.connect(self.reset_preset)
        for w in (save, load, reset):
            row.addWidget(w)
        row.addStretch(1)
        p_lay.addLayout(row)
        path_hint = QLabel(f"저장 위치: {PRESET_DIR}\n종료할 때 현재 설정이 "
                           f"last.json으로 자동 저장됩니다")
        path_hint.setObjectName("Hint")
        path_hint.setWordWrap(True)
        p_lay.addWidget(path_hint)
        lay.addWidget(preset)
        return wrap

    def _spec_page(self, spec) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(10)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(9)
        for r, (name, label, typ, lo, hi, step, tip) in enumerate(spec):
            lab = QLabel(label)
            lab.setToolTip(tip)
            if typ is int:
                w: QWidget = QSpinBox()
                w.setRange(int(lo), int(hi))
                w.setSingleStep(int(step))
            else:
                w = QDoubleSpinBox()
                w.setRange(lo, hi)
                w.setSingleStep(step)
                w.setDecimals(6 if step < 0.001 else 3)
            w.setToolTip(tip)
            w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self.widgets[name] = w
            grid.addWidget(lab, r, 0)
            grid.addWidget(w, r, 1)
        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(1, 2)
        lay.addLayout(grid)
        lay.addStretch(1)
        return page

    def _options_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(9)
        for name, label in CHECK_SPEC:
            cb = QCheckBox(label)
            self.widgets[name] = cb
            lay.addWidget(cb)
        lay.addStretch(1)
        return page

    def _footer(self) -> QWidget:
        box, lay = card("")
        lay.setContentsMargins(16, 12, 16, 14)
        self.log = QPlainTextEdit()
        self.log.setObjectName("Log")
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(150)
        lay.addWidget(self.log)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        lay.addWidget(self.bar)

        row = QHBoxLayout()
        self.status = QLabel("대기 중")
        self.status.setObjectName("Hint")
        row.addWidget(self.status, 1)
        self.view_btn = QPushButton("gmsh로 보기")
        self.view_btn.clicked.connect(self.open_viewer)
        self.view_btn.setEnabled(False)
        self.stop_btn = QPushButton("중단")
        self.stop_btn.setObjectName("Danger")
        self.stop_btn.clicked.connect(self.stop_run)
        self.stop_btn.setEnabled(False)
        self.run_btn = QPushButton("메시 생성")
        self.run_btn.setObjectName("Primary")
        self.run_btn.clicked.connect(self.start_run)
        for w in (self.view_btn, self.stop_btn, self.run_btn):
            row.addWidget(w)
        lay.addLayout(row)
        return box

    # ------------------------------------------------------------- 파일 목록
    def dragEnterEvent(self, e) -> None:
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:
        paths = [u.toLocalFile() for u in e.mimeData().urls()]
        self.add_paths([p for p in paths if p.endswith(STEP_EXT)])

    def add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "STEP 파일 선택", "", "STEP (*.stp *.step);;모든 파일 (*)"
        )
        self.add_paths(paths)

    def add_paths(self, paths: List[str]) -> None:
        existing = {self.table.item(r, 0).data(Qt.UserRole)
                    for r in range(self.table.rowCount())}
        for p in paths:
            if p in existing:
                continue
            r = self.table.rowCount()
            self.table.insertRow(r)
            item = QTableWidgetItem(Path(p).name)
            item.setData(Qt.UserRole, p)
            item.setToolTip(p)
            self.table.setItem(r, 0, item)
            combo = QComboBox()
            for pt in PartType:
                combo.addItem(pt.label, pt)
            self.table.setCellWidget(r, 1, combo)
            self.table.setItem(r, 2, QTableWidgetItem("대기"))
        if paths:
            self.append_log(f"{len(paths)}개 파일 추가")

    def remove_selected(self) -> None:
        for r in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(r)

    def _bulk_type(self) -> None:
        """[전체 적용] 버튼: 목록의 모든 행을 선택한 유형으로 바꾼다."""
        if self.table.rowCount() == 0:
            return
        # PartType은 str Enum이라 Qt를 거치면 평범한 str로 돌아온다
        target = PartType.coerce(self.bulk.currentData())
        idx = self.bulk.currentIndex()
        for r in range(self.table.rowCount()):
            combo = self.table.cellWidget(r, 1)
            if isinstance(combo, QComboBox):
                combo.setCurrentIndex(idx)
        self.append_log(f"전체 {self.table.rowCount()}개 파트를 "
                        f"'{target.label}'으로 변경")

    def pick_outdir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "출력 폴더", self.outdir.text())
        if d:
            self.outdir.setText(d)

    def open_outdir(self) -> None:
        d = Path(self.outdir.text())
        if not d.exists():
            self.append_log("출력 폴더가 아직 없습니다")
            return
        if sys.platform.startswith("win"):
            os.startfile(d)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(d)])
        else:
            subprocess.Popen(["xdg-open", str(d)])

    # ------------------------------------------------------------- 설정 동기화
    def _push_config(self) -> None:
        for f in dc_fields(self.cfg):
            w = self.widgets.get(f.name)
            if isinstance(w, (QSpinBox, QDoubleSpinBox)):
                w.setValue(getattr(self.cfg, f.name))
            elif isinstance(w, QCheckBox):
                w.setChecked(bool(getattr(self.cfg, f.name)))
        for name, cb in self.format_boxes.items():
            cb.setChecked(name in self.cfg.export_formats)

    def _pull_config(self) -> MeshConfig:
        cfg = MeshConfig()
        for f in dc_fields(cfg):
            w = self.widgets.get(f.name)
            if isinstance(w, QSpinBox):
                setattr(cfg, f.name, int(w.value()))
            elif isinstance(w, QDoubleSpinBox):
                setattr(cfg, f.name, float(w.value()))
            elif isinstance(w, QCheckBox):
                setattr(cfg, f.name, w.isChecked())
        cfg.export_formats = [n for n, cb in self.format_boxes.items()
                              if cb.isChecked()] or ["inp"]
        return cfg

    def _load_last_preset(self) -> None:
        """지난번 설정을 자동으로 복원한다."""
        if LAST_PRESET.exists():
            try:
                self.cfg = MeshConfig.load(LAST_PRESET)
                self._push_config()
                self.append_log(f"지난 설정 복원: {LAST_PRESET.name}")
                return
            except Exception as exc:
                self.append_log(f"지난 설정을 읽지 못했습니다: {exc}")
        self._push_config()

    def _save_last_preset(self) -> None:
        try:
            PRESET_DIR.mkdir(parents=True, exist_ok=True)
            self._pull_config().save(LAST_PRESET)
        except Exception:
            pass

    def closeEvent(self, event) -> None:
        self._save_last_preset()
        super().closeEvent(event)

    def save_preset(self) -> None:
        PRESET_DIR.mkdir(parents=True, exist_ok=True)
        path, _ = QFileDialog.getSaveFileName(
            self, "프리셋 저장", str(PRESET_DIR / "mesh_preset.json"), "JSON (*.json)"
        )
        if path:
            self._pull_config().save(path)
            self.append_log(f"프리셋 저장: {path}")

    def load_preset(self) -> None:
        PRESET_DIR.mkdir(parents=True, exist_ok=True)
        path, _ = QFileDialog.getOpenFileName(
            self, "프리셋 불러오기", str(PRESET_DIR), "JSON (*.json)"
        )
        if path:
            self.cfg = MeshConfig.load(path)
            self._push_config()
            self.append_log(f"프리셋 적용: {path}")

    def reset_preset(self) -> None:
        self.cfg = MeshConfig()
        self._push_config()
        self.append_log("기본값으로 되돌림")

    # ------------------------------------------------------------- 실행
    def _items(self) -> List[Tuple[str, PartType]]:
        items = []
        for r in range(self.table.rowCount()):
            path = self.table.item(r, 0).data(Qt.UserRole)
            combo = self.table.cellWidget(r, 1)
            ptype = PartType.coerce(combo.currentData()) if combo else PartType.AUTO
            items.append((path, ptype))
        return items

    def start_run(self) -> None:
        items = self._items()
        if not items:
            QMessageBox.information(self, "파일 없음", "STEP 파일을 먼저 추가하세요.")
            return
        cfg = self._pull_config()
        outdir = self.outdir.text().strip() or str(APP_DIR / "mesh_out")
        self._save_last_preset()

        for r in range(self.table.rowCount()):
            self.table.setItem(r, 2, QTableWidgetItem("진행"))
        self.log.clear()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.view_btn.setEnabled(False)
        self.started_at = time.perf_counter()
        self.stage_text = "준비"
        self.tick.start()
        self._update_status()

        self.thread = QThread(self)
        self.worker = Worker(items, cfg, outdir)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.logged.connect(self.append_log)
        self.worker.progressed.connect(self.on_progress)
        self.worker.staged.connect(self.on_stage)
        self.worker.finished.connect(self.on_finished)
        self.worker.finished.connect(self.thread.quit)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.start()

    def stop_run(self) -> None:
        if self.worker:
            self.worker.stop()
            self.stage_text = "중단 요청 — 현재 파트까지 마무리"
            self._update_status()

    # ------------------------------------------------------------- 진행 표시
    def _fmt_elapsed(self) -> str:
        s = int(time.perf_counter() - self.started_at)
        return f"{s // 60}분 {s % 60}초" if s >= 60 else f"{s}초"

    def _update_status(self) -> None:
        self.status.setText(f"{self.stage_text} · {self.bar.value()}% · "
                            f"경과 {self._fmt_elapsed()}")

    def on_stage(self, frac: float, text: str) -> None:
        self.bar.setValue(int(round(100 * min(max(frac, 0.0), 1.0))))
        self.stage_text = text
        self._update_status()

    def on_progress(self, done: int, total: int) -> None:
        self.stage_text = f"{done}/{total} 파일 완료"
        self._update_status()

    def on_finished(self, results: List[Result]) -> None:
        self.tick.stop()
        self.results = results
        by_source: Dict[str, List[Result]] = {}
        for r in results:
            by_source.setdefault(r.source, []).append(r)
        for row in range(self.table.rowCount()):
            path = self.table.item(row, 0).data(Qt.UserRole)
            rs = by_source.get(path, [])
            if not rs:
                text = "건너뜀"
            elif all(r.ok for r in rs):
                text = "완료"
            elif any(r.ok for r in rs):
                text = "일부 실패"
            else:
                text = "실패"
            item = QTableWidgetItem(text)
            item.setForeground(Qt.white if text == "완료" else Qt.gray)
            self.table.setItem(row, 2, item)

        ok = sum(1 for r in results if r.ok)
        self.append_log("")
        self.append_log(f"-- 결과 {ok}/{len(results)} 성공 --")
        for r in results:
            self.append_log("  " + r.summary())
        self.bar.setValue(100)
        self.status.setText(
            f"완료 — 성공 {ok} / 전체 {len(results)} · 총 {self._fmt_elapsed()}"
        )
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.view_btn.setEnabled(any(r.files for r in results))

    def open_viewer(self) -> None:
        target = next((f for r in self.results for f in r.files), None)
        if not target:
            return
        exe = shutil.which("gmsh")
        if exe:
            subprocess.Popen([exe, target])
        else:
            QMessageBox.information(
                self, "gmsh 실행 파일 없음",
                "PATH에 gmsh 실행 파일이 없습니다.\n"
                f"파일을 직접 열어 확인하세요:\n{target}",
            )

    # ------------------------------------------------------------- 로그
    def append_log(self, text: str) -> None:
        self.log.appendPlainText(text)
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())
