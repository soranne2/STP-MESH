"""실행 준비 창.

run.bat이 콘솔에 [1/3] 같은 글자를 찍던 자리를 대신한다. 표준 라이브러리인
tkinter만 쓰기 때문에 gmsh/PySide6가 아직 깔리지 않은 상태에서도 뜬다.

하는 일
  1. .venv 확인 및 생성
  2. gmsh / PySide6 확인 및 설치 (pip 출력을 창에 흘려보낸다)
  3. 준비가 끝나면 창을 닫고 app.py 실행

버전 이력
---------
v1.5
  - 최초 작성. 설치 진행 상황을 콘솔이 아니라 독립된 창으로 보여준다.
"""
from __future__ import annotations

import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk

APP_DIR = Path(__file__).resolve().parent
VENV_DIR = APP_DIR / ".venv"
REQUIREMENTS = APP_DIR / "requirements.txt"

BG = "#0D0D0F"
SURFACE = "#17171A"
TEXT = "#EDEDF0"
MUTED = "#85858F"
BLUE = "#3182F6"
RED = "#F04452"

STEPS = 3


def venv_python() -> Path:
    if sys.platform.startswith("win"):
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def _no_window() -> dict:
    """윈도우에서 검은 콘솔 창이 깜빡이지 않게 한다."""
    if sys.platform.startswith("win"):
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        return {"startupinfo": si, "creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


class SetupWindow:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("STEP Mesher 준비")
        self.root.configure(bg=BG)
        self.root.resizable(False, False)
        self._center(540, 340)

        self.msgs: "queue.Queue[tuple[str, object]]" = queue.Queue()
        self.failed = False
        self.launch_cmd: list[str] | None = None

        tk.Label(self.root, text="STEP Mesher", bg=BG, fg=TEXT,
                 font=("Malgun Gothic", 17, "bold")).pack(anchor="w", padx=26, pady=(24, 0))
        self.stage = tk.Label(self.root, text="시작하는 중", bg=BG, fg=MUTED,
                              font=("Malgun Gothic", 10))
        self.stage.pack(anchor="w", padx=26, pady=(4, 14))

        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("Toss.Horizontal.TProgressbar", troughcolor=SURFACE,
                        background=BLUE, bordercolor=SURFACE,
                        lightcolor=BLUE, darkcolor=BLUE, thickness=6)
        self.bar = ttk.Progressbar(self.root, style="Toss.Horizontal.TProgressbar",
                                   maximum=STEPS, length=488)
        self.bar.pack(padx=26)

        self.log = tk.Text(self.root, height=9, bg="#0A0A0C", fg="#C9C9D1",
                           insertbackground=TEXT, relief="flat", wrap="none",
                           font=("Consolas", 9))
        self.log.pack(padx=26, pady=(16, 10), fill="both", expand=True)
        self.log.configure(state="disabled")

        self.close_btn = tk.Button(self.root, text="닫기", command=self.root.destroy,
                                   bg=SURFACE, fg=TEXT, relief="flat",
                                   activebackground="#26262C", activeforeground=TEXT,
                                   padx=18, pady=6, font=("Malgun Gothic", 9))

    # ---------------------------------------------------------- UI 도우미
    def _center(self, w: int, h: int) -> None:
        x = (self.root.winfo_screenwidth() - w) // 2
        y = (self.root.winfo_screenheight() - h) // 3
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def put(self, kind: str, payload: object) -> None:
        self.msgs.put((kind, payload))

    def _append(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text.rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _drain(self) -> None:
        while True:
            try:
                kind, payload = self.msgs.get_nowait()
            except queue.Empty:
                break
            if kind == "stage":
                step, text = payload  # type: ignore[misc]
                self.bar["value"] = step
                self.stage.configure(text=text)
            elif kind == "log":
                self._append(str(payload))
            elif kind == "fail":
                self.failed = True
                self.stage.configure(text=str(payload), fg=RED)
                self.close_btn.pack(pady=(0, 16))
            elif kind == "launch":
                self.launch_cmd = list(payload)  # type: ignore[arg-type]
                self.root.after(200, self.root.destroy)
                return
        self.root.after(80, self._drain)

    # ---------------------------------------------------------- 준비 작업
    def work(self) -> None:
        try:
            self._ensure_venv()
            self._ensure_packages()
            self.put("stage", (STEPS, "STEP Mesher 실행"))
            self.put("launch", [str(venv_python()), str(APP_DIR / "app.py")])
        except Exception as exc:
            self.put("log", str(exc))
            self.put("fail", "준비 실패 — 위 내용을 확인하세요")

    def _ensure_venv(self) -> None:
        if venv_python().exists():
            self.put("stage", (1, "[1/3] 가상환경 확인 완료"))
            return
        self.put("stage", (0, "[1/3] 가상환경을 만드는 중"))
        self.put("log", f"python -m venv {VENV_DIR}")
        self._run([sys.executable, "-m", "venv", str(VENV_DIR)])
        if not venv_python().exists():
            raise RuntimeError("가상환경 생성에 실패했습니다")
        self.put("stage", (1, "[1/3] 가상환경 생성 완료"))

    def _ensure_packages(self) -> None:
        vpy = str(venv_python())
        probe = subprocess.run([vpy, "-c", "import gmsh, PySide6"],
                               capture_output=True, **_no_window())
        if probe.returncode == 0:
            self.put("stage", (2, "[2/3] 필요한 패키지가 이미 설치되어 있습니다"))
            return
        self.put("stage", (1, "[2/3] gmsh / PySide6 설치 중 (몇 분 걸립니다)"))
        self._run([vpy, "-m", "pip", "install", "--upgrade", "pip"])
        self._run([vpy, "-m", "pip", "install", "-r", str(REQUIREMENTS)])
        check = subprocess.run([vpy, "-c", "import gmsh, PySide6"],
                               capture_output=True, **_no_window())
        if check.returncode != 0:
            raise RuntimeError(
                "패키지 설치가 끝나지 않았습니다. 사내망 프록시를 쓰신다면 "
                "run.bat 위쪽의 HTTP_PROXY / HTTPS_PROXY 줄을 채워주세요."
            )
        self.put("stage", (2, "[2/3] 패키지 설치 완료"))

    def _run(self, cmd: list[str]) -> None:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace", **_no_window())
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if line:
                self.put("log", line)
        if proc.wait() != 0:
            raise RuntimeError(f"명령 실패: {' '.join(cmd[:3])} ...")

    # ---------------------------------------------------------- 진입
    def run(self) -> int:
        threading.Thread(target=self.work, daemon=True).start()
        self.root.after(80, self._drain)
        self.root.mainloop()
        if self.launch_cmd:
            subprocess.Popen(self.launch_cmd, cwd=str(APP_DIR))
            return 0
        return 1 if self.failed else 0


if __name__ == "__main__":
    raise SystemExit(SetupWindow().run())
