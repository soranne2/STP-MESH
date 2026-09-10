"""블랙 + 토스블루 톤 스타일시트.

버전 이력
---------
v1.1 (수정본)
  - QScrollArea 안쪽 컨테이너 위젯과 QSplitter에 배경이 적용되지 않아
    파라미터 패널(공통 / 프레스 / 사출 / 압출) 뒤로 흰 바탕이 비치던 문제 수정.
    QScrollArea의 자식 위젯, QSplitter, splitter handle까지 투명 처리하고
    QMainWindow 자체 배경색을 지정했다.
v1.0
  - 최초 작성.
"""

C = {
    "bg":        "#0D0D0F",
    "surface":   "#17171A",
    "raised":    "#1F1F24",
    "border":    "#2A2A31",
    "text":      "#EDEDF0",
    "muted":     "#85858F",
    "blue":      "#3182F6",
    "blue_hi":   "#4B93F8",
    "blue_lo":   "#1B64DA",
    "blue_soft": "#1A2A45",
    "green":     "#22C55E",
    "red":       "#F04452",
    "amber":     "#F5A524",
}

FONT_STACK = "'Pretendard', 'Pretendard Variable', 'Malgun Gothic', 'Apple SD Gothic Neo', sans-serif"

QSS = f"""
* {{
    font-family: {FONT_STACK};
    font-size: 13px;
    color: {C['text']};
}}
QWidget#Root {{ background: {C['bg']}; }}

QLabel#Title {{ font-size: 20px; font-weight: 700; letter-spacing: -0.4px; }}
QLabel#Subtitle {{ color: {C['muted']}; font-size: 12px; }}
QLabel#SectionTitle {{
    font-size: 12px; font-weight: 600; color: {C['muted']};
    padding: 2px 0 6px 0;
}}
QLabel#Hint {{ color: {C['muted']}; font-size: 11px; }}

QFrame#Card {{
    background: {C['surface']};
    border: 1px solid {C['border']};
    border-radius: 14px;
}}

QPushButton {{
    background: {C['raised']};
    border: 1px solid {C['border']};
    border-radius: 10px;
    padding: 8px 14px;
    font-weight: 500;
}}
QPushButton:hover {{ background: #26262C; }}
QPushButton:pressed {{ background: #101014; }}
QPushButton:disabled {{ color: #4C4C55; background: #131317; }}

QPushButton#Primary {{
    background: {C['blue']};
    border: none;
    color: #FFFFFF;
    font-weight: 600;
    padding: 11px 18px;
}}
QPushButton#Primary:hover {{ background: {C['blue_hi']}; }}
QPushButton#Primary:pressed {{ background: {C['blue_lo']}; }}
QPushButton#Primary:disabled {{ background: #1D2A3C; color: #5A6577; }}

QPushButton#Danger {{ color: {C['red']}; }}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {C['raised']};
    border: 1px solid {C['border']};
    border-radius: 9px;
    padding: 7px 10px;
    selection-background-color: {C['blue']};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border: 1px solid {C['blue']};
}}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 16px; border: none; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {C['raised']};
    border: 1px solid {C['border']};
    selection-background-color: {C['blue_soft']};
    outline: none;
}}

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{
    width: 17px; height: 17px;
    border: 1px solid {C['border']};
    border-radius: 5px;
    background: {C['raised']};
}}
QCheckBox::indicator:checked {{
    background: {C['blue']};
    border: 1px solid {C['blue']};
    image: none;
}}

QTableWidget {{
    background: {C['surface']};
    border: none;
    gridline-color: transparent;
    selection-background-color: {C['blue_soft']};
    outline: none;
}}
QTableWidget::item {{ padding: 6px 8px; border-bottom: 1px solid {C['border']}; }}
QHeaderView::section {{
    background: {C['surface']};
    color: {C['muted']};
    border: none;
    border-bottom: 1px solid {C['border']};
    padding: 8px;
    font-size: 12px;
}}

QPlainTextEdit#Log {{
    background: #0A0A0C;
    border: 1px solid {C['border']};
    border-radius: 12px;
    padding: 10px;
    font-family: 'JetBrains Mono', 'D2Coding', Consolas, monospace;
    font-size: 12px;
    color: #C9C9D1;
}}

QProgressBar {{
    background: {C['raised']};
    border: none;
    border-radius: 4px;
    height: 6px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{ background: {C['blue']}; border-radius: 4px; }}

QMainWindow {{ background: {C['bg']}; }}
QScrollArea,
QScrollArea > QWidget,
QScrollArea > QWidget > QWidget,
QSplitter,
QSplitter > QWidget {{ background: transparent; border: none; }}
QSplitter::handle {{ background: transparent; }}
QSplitter::handle:horizontal {{ width: 14px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 4px; }}
QScrollBar::handle:vertical {{ background: #33333B; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #43434D; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QToolTip {{
    background: {C['raised']};
    border: 1px solid {C['border']};
    border-radius: 8px;
    padding: 6px 8px;
    color: {C['text']};
}}
"""
