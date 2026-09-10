"""메인 윈도우. 파일 목록 + 파라미터 + 로그, 메시 생성은 워커 스레드에서 돈다.

버전 이력
---------
v1.3 (수정본)
  - 공통 항목에 '면 봉합으로 solid 복원'과 '봉합 허용오차' 추가.
    겉면만 있는 STEP을 solid로 되살려 압출/사출 메시를 적용하기 위한 옵션.
v1.2 (수정본)
  - "일괄 유형 변경"이 목록을 고르는 순간 바로 적용되던 것을 고침.
    이제 유형을 고른 뒤 [전체 적용] 버튼을 눌러야 반영된다.
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
from dataclasses import fields as dc_fields
from pathlib import Path
from typing import Dict, List, Tuple

from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
    QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QScrollArea, QSizePolicy, QSpinBox, QSplitter, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from mesher.config import MeshConfig, PartType
from mesher.pipeline import Result, run_batch

from .theme import C, QSS

STEP_EXT = (".stp", ".step", ".STP", ".STEP")

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
     "solid 없이 면만 있는 STEP에서 SECTION에 쓸 두께"),
]
TET_SPEC = [
    ("tet_min_size_factor", "최소 크기 비율", float, 0.02, 1.0, 0.05,
     "최소 요소 크기 = 요소 크기 × 이 값"),
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
    ("extrusion_tol", "압출 판정 허용", float, 0.001, 0.5, 0.01,
     "V ≈ 단면적 × 길이 오차 허용"),
]
CHECK_SPEC = [
    ("auto_make_solid", "면 봉합으로 solid 복원"),
    ("structured_washer", "정렬 washer 사용"),
    ("shell_quad_dominant", "shell quad 우선"),
    ("shell_imprint", "패치 imprint(절점 공유)"),
    ("hex_full_quad", "hexa 100% 유도"),
    ("second_order", "2차 요소"),
    ("optimize", "메시 최적화"),
    ("write_sections", "SECTION 자동 작성"),
]
# 체크박스에 쓸 이름: 내부 형식 키 -> 표시 문구
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
            )
        except Exception as exc:  # 워커에서 죽어도 UI는 살려둔다
            self.logged.emit(f"[치명적 오류] {exc}")
            results = []
        self.finished.emit(results)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("STEP Mesher")
        self.resize(1240, 820)
        self.setAcceptDrops(True)
        self.cfg = MeshConfig()
        self.widgets: Dict[str, QWidget] = {}
        self.format_boxes: Dict[str, QCheckBox] = {}
        self.thread: QThread | None = None
        self.worker: Worker | None = None
        self.results: List[Result] = []
        self._build()
        self._push_config()

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
        sub = QLabel("STEP 파일을 읽어 파트 유형에 맞는 메시를 만들고 Abaqus로 내보냅니다")
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
        self.table.setMinimumHeight(200)
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
        self.outdir = QLineEdit(str(Path.home() / "mesh_out"))
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
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(7, 0, 0, 0)
        lay.setSpacing(12)

        lay.addWidget(self._spec_card("공통", COMMON_SPEC))
        lay.addWidget(self._spec_card("프레스 · shell", SHELL_SPEC))
        lay.addWidget(self._spec_card("사출 · tetra", TET_SPEC))
        lay.addWidget(self._spec_card("압출 · hexa", HEX_SPEC))

        opt, opt_lay = card("옵션")
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(8)
        for i, (name, label) in enumerate(CHECK_SPEC):
            cb = QCheckBox(label)
            self.widgets[name] = cb
            grid.addWidget(cb, i // 2, i % 2)
        opt_lay.addLayout(grid)
        lay.addWidget(opt)

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
        lay.addWidget(preset)

        lay.addStretch(1)
        scroll.setWidget(inner)
        return scroll

    def _spec_card(self, title: str, spec) -> QFrame:
        box, lay = card(title)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
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
        return box

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
        target = self.bulk.currentData()
        if target is None or self.table.rowCount() == 0:
            return
        for r in range(self.table.rowCount()):
            combo = self.table.cellWidget(r, 1)
            if isinstance(combo, QComboBox):
                combo.setCurrentIndex(combo.findData(target))
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
        cfg.export_formats = [n for n, cb in self.format_boxes.items() if cb.isChecked()] or ["inp"]
        return cfg

    def save_preset(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "프리셋 저장", "mesh_preset.json",
                                              "JSON (*.json)")
        if path:
            self._pull_config().save(path)
            self.append_log(f"프리셋 저장: {path}")

    def load_preset(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "프리셋 불러오기", "", "JSON (*.json)")
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
            items.append((path, combo.currentData() if combo else PartType.AUTO))
        return items

    def start_run(self) -> None:
        items = self._items()
        if not items:
            QMessageBox.information(self, "파일 없음", "STEP 파일을 먼저 추가하세요.")
            return
        cfg = self._pull_config()
        outdir = self.outdir.text().strip() or str(Path.home() / "mesh_out")

        for r in range(self.table.rowCount()):
            self.table.setItem(r, 2, QTableWidgetItem("진행"))
        self.log.clear()
        self.bar.setValue(0)
        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.view_btn.setEnabled(False)
        self.status.setText(f"{len(items)}개 파트 처리 중")

        self.thread = QThread(self)
        self.worker = Worker(items, cfg, outdir)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.logged.connect(self.append_log)
        self.worker.progressed.connect(self.on_progress)
        self.worker.finished.connect(self.on_finished)
        self.worker.finished.connect(self.thread.quit)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.start()

    def stop_run(self) -> None:
        if self.worker:
            self.worker.stop()
            self.status.setText("중단 요청 — 현재 파트까지 마무리합니다")

    def on_progress(self, done: int, total: int) -> None:
        self.bar.setValue(int(100 * done / max(total, 1)))

    def on_finished(self, results: List[Result]) -> None:
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
        self.append_log(f"── 결과 {ok}/{len(results)} 성공 ──")
        for r in results:
            self.append_log("  " + r.summary())
        self.bar.setValue(100)
        self.status.setText(f"완료 — 성공 {ok} / 전체 {len(results)}")
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
