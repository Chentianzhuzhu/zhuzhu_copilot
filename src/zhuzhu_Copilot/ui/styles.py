"""全局基础样式：极简四色系（石墨黑 + 淡灰 + 白 + 深蓝）

主面板、各对话框、WebView 面板共用同一套 PALETTE 与 GLOBAL_QSS，
修改色板即可全局换肤。风格：简约、高效、深色基调。

美化版：纯黑 #000 柔化为石墨色阶（直出纯黑太生硬，且选中/边框在纯黑上不可见），
保留黑/灰/白/深蓝的极简语言，补全 hover / pressed / focus 全交互态。
"""

from PyQt6.QtCore import QByteArray, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPalette, QPixmap
from PyQt6.QtSvg import QSvgRenderer

from zhuzhu_Copilot.ui.tokens import (
    FONT_BASE,
    FONT_BODY,
    FONT_CAPTION,
    FONT_LARGE,
    FONT_SMALL,
    RADIUS_MD,
    RADIUS_SM,
    RADIUS_XS,
    SPACING_MD,
    SPACING_SM,
)

# 极简四色系：石墨黑 + 淡灰 + 白 + 深蓝
DARK_PALETTE = {
    "bg_top": "#101216",            # 石墨黑（柔化纯黑，保留深色质感）
    "bg_bottom": "#0B0D11",         # 近黑
    "card": "#181B21",              # 卡片（深灰偏蓝，层次高于背景）
    "primary": "#2F52D8",           # 深蓝（主色，提亮后深色底下更清晰）
    "primary_hover": "#4464EE",
    "primary_pressed": "#2643B0",   # 按压态
    "primary_light": "#22336B",     # 选中态（深蓝底，白字可读）
    "text": "#F3F5F9",              # 白
    "text_secondary": "#9BA3B0",    # 淡灰
    "border": "#272C36",
    "hover": "#1F232C",
    "success": "#34D399",
    "warning": "#FBBF24",
    "danger": "#F87171",
}

# 浅色主题色板（与 AI 面板浅色主题同风格，颜色沿用四色系）
LIGHT_PALETTE = {
    "bg_top": "#F7F8FB",
    "bg_bottom": "#EDF0F5",
    "card": "#FFFFFF",
    "primary": "#1E40AF",
    "primary_hover": "#2B51D1",
    "primary_pressed": "#1A368F",   # 按压态
    "primary_light": "#E4EBFC",     # 选中态（浅蓝底，深字可读）
    "text": "#1B2433",
    "text_secondary": "#5F6B7E",
    "border": "#E3E8F0",
    "hover": "#EDF1F8",
    "success": "#16A34A",
    "warning": "#D97706",
    "danger": "#DC2626",
}

# 当前激活色板（set_palette() 就地更新内容，引用方读取即时生效）
PALETTE = dict(DARK_PALETTE)

# 全局滚动条滑块不透明度：淡灰极简，滑块压到 30% 半透明（悬停提亮到 55% 保持可辨）
_SCROLLBAR_OPACITY = 0.30
_SCROLLBAR_HOVER_OPACITY = 0.55


def _scrollbar_handle() -> str:
    """滚动条滑块色：边框色压到 30% 不透明度（全局统一淡灰）。"""
    c = QColor(PALETTE["border"])
    c.setAlphaF(_SCROLLBAR_OPACITY)
    return f"rgba({c.red()},{c.green()},{c.blue()},{c.alpha()})"


def _scrollbar_handle_hover() -> str:
    """滚动条滑块悬停色：提亮到 55%，保持可辨又不喧宾夺主。"""
    c = QColor(PALETTE["text_secondary"])
    c.setAlphaF(_SCROLLBAR_HOVER_OPACITY)
    return f"rgba({c.red()},{c.green()},{c.blue()},{c.alpha()})"


def _build_global_qss() -> str:
    """按当前 PALETTE 生成全局 QSS（主窗口基础样式，浅色/深色随主题重建）"""
    return f"""
QMainWindow {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {PALETTE['bg_top']}, stop:1 {PALETTE['bg_bottom']});
}}

QMainWindow, QWidget {{
    color: {PALETTE['text']};
    font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
    font-size: {FONT_BASE}px;
}}

QLabel {{
    color: {PALETTE['text']};
    background: transparent;
}}

QLabel#title {{
    font-size: {FONT_LARGE}px;
    font-weight: 700;
    color: {PALETTE['primary_hover']};
}}

QLabel#subtitle {{
    font-size: {FONT_BODY}px;
    color: {PALETTE['text_secondary']};
}}

QPushButton {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {PALETTE['primary_hover']}, stop:1 {PALETTE['primary']});
    color: #FFFFFF;
    border: none;
    border-radius: {RADIUS_SM}px;
    padding: 10px 24px;
    font-weight: 600;
}}

QPushButton:hover {{
    background: {PALETTE['primary_hover']};
}}

QPushButton:pressed {{
    background: {PALETTE['primary_pressed']};
}}

QPushButton:disabled {{
    background: {PALETTE['border']};
    color: {PALETTE['text_secondary']};
}}

QPushButton#secondary {{
    background: {PALETTE['card']};
    color: {PALETTE['text']};
    border: 1px solid {PALETTE['border']};
}}

QPushButton#secondary:hover {{
    background: {PALETTE['hover']};
    border-color: {PALETTE['primary_hover']};
}}

QPushButton#secondary:pressed {{
    background: {PALETTE['primary_light']};
}}

QLineEdit, QComboBox {{
    background-color: {PALETTE['card']};
    color: {PALETTE['text']};
    border: 1px solid {PALETTE['border']};
    border-radius: {RADIUS_SM}px;
    padding: 8px 12px;
    min-height: 20px;
    selection-background-color: {PALETTE['primary']};
    selection-color: #FFFFFF;
}}

QLineEdit:hover, QComboBox:hover {{
    border: 1px solid {PALETTE['text_secondary']};
}}

QLineEdit:focus, QComboBox:focus {{
    border: 1px solid {PALETTE['primary_hover']};
}}

QComboBox::drop-down {{
    border: none;
    width: 30px;
}}

QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {PALETTE['text_secondary']};
    margin-right: 8px;
}}

QComboBox QAbstractItemView {{
    border: 1px solid {PALETTE['border']};
    border-radius: {RADIUS_SM}px;
    padding: 4px;
    outline: none;
    background-color: {PALETTE['card']};
    color: {PALETTE['text']};
    selection-background-color: {PALETTE['primary']};
    selection-color: #FFFFFF;
}}

QComboBox QAbstractItemView::item {{
    padding: 6px 10px;
    border-radius: {RADIUS_XS}px;
}}

QProgressBar {{
    border: none;
    border-radius: {RADIUS_XS}px;
    background-color: {PALETTE['bg_bottom']};
    text-align: center;
    height: 16px;
    color: {PALETTE['text']};
    font-size: {FONT_CAPTION}px;
}}

QProgressBar::chunk {{
    border-radius: {RADIUS_XS}px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {PALETTE['primary']}, stop:1 {PALETTE['primary_hover']});
}}

QListWidget {{
    background-color: {PALETTE['card']};
    border: 1px solid {PALETTE['border']};
    border-radius: {RADIUS_MD}px;
    padding: 6px;
    outline: none;
    color: {PALETTE['text']};
}}

QListWidget::item {{
    border: none;
    padding: 10px 12px;
    border-radius: {RADIUS_SM}px;
}}

QListWidget::item:selected {{
    background-color: {PALETTE['primary_light']};
    color: {PALETTE['text']};
}}

QListWidget::item:hover {{
    background-color: {PALETTE['hover']};
}}

QScrollBar:vertical {{
    background: transparent;
    width: 12px;
    margin: 2px 2px 2px 0;
}}

QScrollBar::handle:vertical {{
    background: {_scrollbar_handle()};
    border-radius: 4px;
    min-height: 40px;
    margin: 0 2px;
}}

QScrollBar::handle:vertical:hover {{
    background: {_scrollbar_handle_hover()};
}}

QScrollBar::sub-page:vertical, QScrollBar::add-page:vertical {{
    background: transparent;
}}

QScrollBar::sub-line:vertical, QScrollBar::add-line:vertical {{
    background: transparent;
    height: 0;
    width: 0;
}}

QScrollBar:horizontal {{
    background: transparent;
    height: 12px;
    margin: 0 2px 2px 2px;
}}

QScrollBar::handle:horizontal {{
    background: {_scrollbar_handle()};
    border-radius: 4px;
    min-width: 40px;
    margin: 2px 0;
}}

QScrollBar::handle:horizontal:hover {{
    background: {_scrollbar_handle_hover()};
}}

QScrollBar::sub-page:horizontal, QScrollBar::add-page:horizontal {{
    background: transparent;
}}

QScrollBar::sub-line:horizontal, QScrollBar::add-line:horizontal {{
    background: transparent;
    height: 0;
    width: 0;
}}

QTextEdit {{
    background-color: {PALETTE['card']};
    color: {PALETTE['text']};
    border: 1px solid {PALETTE['border']};
    border-radius: {RADIUS_MD}px;
    padding: 12px;
    selection-background-color: {PALETTE['primary']};
    selection-color: #FFFFFF;
}}

QTextEdit:focus {{
    border: 1px solid {PALETTE['primary_hover']};
}}

QToolTip {{
    background-color: {PALETTE['card']};
    color: {PALETTE['text']};
    border: 1px solid {PALETTE['border']};
    border-radius: {RADIUS_XS}px;
    padding: 5px 9px;
    font-size: {FONT_SMALL}px;
}}

QMenu {{
    background-color: {PALETTE['card']};
    color: {PALETTE['text']};
    border: 1px solid {PALETTE['border']};
    border-radius: {RADIUS_MD}px;
    padding: 5px;
}}

QMenu::item {{
    padding: 7px 24px 7px 16px;
    border-radius: {RADIUS_XS}px;
}}

QMenu::item:selected {{
    background-color: {PALETTE['primary_light']};
}}

QMenu::separator {{
    height: 1px;
    background: {PALETTE['border']};
    margin: 5px 10px;
}}

QCheckBox, QRadioButton {{
    color: {PALETTE['text']};
    spacing: {SPACING_SM}px;
}}

QSplitter::handle {{
    background: {PALETTE['border']};
}}
"""


GLOBAL_QSS = _build_global_qss()


def set_palette(theme: str) -> None:
    """按主题（dark/light）就地切换 PALETTE 内容并重建 GLOBAL_QSS。
    引用 PALETTE 的字符串在取值时读取最新颜色，无需改动各处 f-string。"""
    global GLOBAL_QSS
    src = LIGHT_PALETTE if str(theme).lower() == "light" else DARK_PALETTE
    if PALETTE == src:
        return
    PALETTE.clear()
    PALETTE.update(src)
    GLOBAL_QSS = _build_global_qss()


def apply_palette(app):
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(PALETTE["bg_top"]))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(PALETTE["text"]))
    palette.setColor(QPalette.ColorRole.Base, QColor(PALETTE["card"]))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(PALETTE["bg_bottom"]))
    palette.setColor(QPalette.ColorRole.Text, QColor(PALETTE["text"]))
    palette.setColor(QPalette.ColorRole.Button, QColor(PALETTE["primary"]))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#FFFFFF"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(PALETTE["primary"]))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
    app.setPalette(palette)


def svg_icon(svg: str, size: int = 24) -> QIcon:
    """SVG 矢量图 → QIcon（淡灰简约图标，替代 emoji）"""
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    renderer.render(p)
    p.end()
    return QIcon(pix)
