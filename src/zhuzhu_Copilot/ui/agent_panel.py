"""zhuzhu Copilot 工具面板（深色"星际控制台"风格，无 emoji，矢量图标）

消息事件流（1:1 复刻 ui_style_demo/index.html 的结构，实现见 ui/agent_chat_bubbles.py）：
- AI 回合没有填充气泡：上下虚线分区 + 左侧竖虚线 + 骑线耗时徽章；回合内依次是
  思考气泡（超 5 行折叠）→ 工具调用行 → 命令块 → 正文；回合结束后过程区整体收起，
  只留最后一段正文与「查看执行过程」开关（收起态不创建过程控件，长会话才不卡）
- 用户消息靠右（深蓝非对称圆角气泡），AI 回合铺满内容宽度
- 工具图标：每个工具一个专属线条矢量图标（ui/tool_icons.py，族底图 + 动作角标）
- 流式输出：33ms 固定节拍（≈30fps）连续落字，dirty 防抖合并增量
- 上下文：engine 复用保留跨任务对话历史（截图仅保留最近 2 张防膨胀），可一键清空
- 反馈：发送中/停止中按钮状态 + "思考中"点号动画 + tokens 实时统计
- 每步确认：AskBeforeEdit 弹窗确认（确认后危险命令可执行）；YOLO 无确认、不设任何限制（可操作任意目录/系统目录、执行任意命令）
- MCP / skills / agents：从 ~/.zhuzhu_Copilot/agent/*.json 加载
"""

from zhuzhu_Copilot import app_identity
import base64
import ctypes
import datetime
import html as _html
import json
import math
import os
import re
import shutil
import sys
import threading
import time
import uuid
import webbrowser
from functools import lru_cache
from pathlib import Path

from PyQt6.QtCore import (
    QAbstractAnimation,
    QAbstractNativeEventFilter,
    QBuffer,
    QByteArray,
    QEasingCurve,
    QEvent,
    QFileSystemWatcher,
    QIODevice,
    QMimeData,
    QObject,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    QRegularExpression,
    QSize,
    Qt,
    QThread,
    QTimer,
    QUrl,
    QVariantAnimation,
    pyqtProperty,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QAbstractTextDocumentLayout,
    QColor,
    QCursor,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QFont,
    QIcon,
    QImage,
    QImageReader,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
    QRegion,
    QShortcut,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextOption,
)
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressDialog,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTabWidget,
    QTextBrowser,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QWidgetItem,
)

from zhuzhu_Copilot.core import (
    agent_agents,
    agent_engine,
    agent_llm,
    agent_panels,
    agent_plugins,
    agent_sandbox,
    agent_screen,
    agent_skills,
    agent_subagent,
    agent_tools,
    agent_tts,
    agent_ui_ux,
    agent_workflow,
)
from zhuzhu_Copilot.core.agent_mcp import McpManager
from zhuzhu_Copilot.ui import agent_chat_bubbles as chat_bubbles
from zhuzhu_Copilot.ui import tool_icons
from zhuzhu_Copilot.ui.tokens import (
    FONT_BASE,
    FONT_BODY,
    FONT_CAPTION,
    FONT_HERO,
    FONT_SMALL,
    FONT_TITLE,
    RADIUS_LG,
    RADIUS_MD,
    RADIUS_SM,
    SPACING_LG,
    SPACING_MD,
    SPACING_SM,
    SPACING_XS,
)
from zhuzhu_Copilot.ui.widgets import add_brand_footer
from zhuzhu_Copilot.utils.helpers import is_admin, set_native_window_icon

# ---------- 主题系统 ----------
# 极简四色系：纯黑/淡灰 + 白 + 深蓝。深色（默认）与浅色两套色板，支持手动选择或按时间自动。
_THEMES = {
    "dark": {
        "BG": "#101216", "BG_BOTTOM": "#0B0D11", "PANEL": "#181B21",
        "CARD": "#1F232C", "BORDER": "#272C36", "BORDER_SOFT": "#333A46",
        "TEXT": "#F3F5F9", "TEXT_DIM": "#9BA3B0", "ACCENT": "#2F52D8",
        "ACCENT_HOVER": "#4464EE", "LINK_COLOR": "#5B82F6", "USER_BG": "#2F52D8",
        "AI_BG": "#1F232C", "OK": "#34D399", "WARN": "#FBBF24", "ERR": "#F87171",
        "HOVER": "#272C36", "CODE_ACCENT": "#7EA0F8", "CODE_BG": "#14161B",
    },
    "light": {
        "BG": "#F7F8FB", "BG_BOTTOM": "#EDF0F5", "PANEL": "#FFFFFF",
        "CARD": "#FFFFFF", "BORDER": "#E3E8F0", "BORDER_SOFT": "#D5DCE8",
        "TEXT": "#1B2433", "TEXT_DIM": "#5F6B7E", "ACCENT": "#1E40AF",
        "ACCENT_HOVER": "#2B51D1", "LINK_COLOR": "#2B51D1", "USER_BG": "#1E40AF",
        "AI_BG": "#FFFFFF", "OK": "#16A34A", "WARN": "#D97706", "ERR": "#DC2626",
        "HOVER": "#EDF1F8", "CODE_ACCENT": "#2B4BD8", "CODE_BG": "#F0F3F9",
    },
}


def _theme_setting() -> str:
    """读取主题设置：dark / light / auto（默认 light，安装未设置时即浅色）"""
    try:
        v = str(app_identity.qsettings().value("agent_theme", "light"))
    except Exception:
        v = "light"
    return v if v in ("dark", "light", "auto") else "light"


def _resolve_theme() -> str:
    """解析实际主题：auto 按本地系统时间 8-20 浅色、20-次日8 深色"""
    m = _theme_setting()
    if m != "auto":
        return m
    hour = datetime.datetime.now().hour
    return "light" if 8 <= hour < 20 else "dark"


# 主题版本号：每次 _apply_colors 应用新色板时自增。渲染缓存（_seg_cache/_rd_cache）
# 的签名/键内含该版本，保证任何重渲染路径都不会命中旧主题色的缓存（聊天气泡/代码块
# 随主题实时换色），即使个别路径漏了显式清缓存也不会残留旧色。
_THEME_VERSION = 0

# 事件流里工具/技能/插件矢量图用的淡灰（随主题的 TEXT_DIM 走：深色 #9BA3B0、浅色 #5F6B7E）。
# 取名一个专用常量而不是直接用 TEXT_DIM：这三类图标是同一语义层（淡灰矢量图），
# 将来要单独调色只改这里；同时避免与「正文次要文字色」耦合带来的误改。
ICON_GRAY = _THEMES["dark"]["TEXT_DIM"]

# 最近一次已应用的色板：_apply_colors 幂等判断用。apply_package_theme 会先 apply_theme
# 再 apply_theme_custom 连续触发 _apply_colors，色板未变时跳过，避免 _THEME_VERSION 无谓
# 自增导致渲染缓存整体失效、QSS 字符串重复生成（主题切换/面板重建的卡顿来源之一）。
_LAST_PALETTE: dict = {}

# AI 确认/提问弹窗不再自动超时：用户要求「无限等待」，
# 弹窗保持打开直到作答/取消，任务相应挂起等待（见 _confirm_tool / _ask_user_tool）。

# 歌词视图高度（px）：约为 5 行歌词的可视区域
LYRICS_VIEW_H = 150

# 歌词高精度跟随步进（毫秒）：独立于进度条 500ms 心跳，保证左→右填充平滑连续
LYRICS_STEP_MS = 60


def _mix_hex(top: str, bottom: str, alpha: float) -> str:
    """颜色叠加：把 top 以 alpha 不透明度压在 bottom 上，返回 #RRGGBB。

    事件流气泡里有几处半透明底（思考子弹的 tag 胶囊、命令块内底），Qt 富文本/QSS
    都支持 rgba，但叠在不透明底上取实色更稳定（不依赖合成时机），故统一在此换算。
    """
    a = max(0.0, min(1.0, float(alpha)))
    t, b = QColor(top), QColor(bottom)
    return QColor(
        int(round(t.red() * a + b.red() * (1 - a))),
        int(round(t.green() * a + b.green() * (1 - a))),
        int(round(t.blue() * a + b.blue() * (1 - a))),
    ).name()


def _apply_colors(t: dict) -> None:
    """从色板字典更新模块级颜色常量与派生样式常量（apply_theme / 自定义包主题共用）"""
    global BG, BG_BOTTOM, PANEL, CARD, BORDER, BORDER_SOFT, TEXT, TEXT_DIM
    global ACCENT, ACCENT_HOVER, LINK_COLOR, USER_BG, AI_BG, OK, WARN, ERR, HOVER
    global CODE_ACCENT
    global CODE_BG
    global ICON_GRAY
    global _BTN_GHOST, _BTN_COMPACT, _BTN_GHOST_ACCENT, _BTN_PRIMARY, _BTN_DIM
    global _QCOMBO, _BTN_ICON, _BTN_DANGER
    global _THEME_VERSION, _LAST_PALETTE
    # 幂等：色板与上次一致则跳过，避免 _THEME_VERSION 无谓自增 + QSS 重复生成
    if _LAST_PALETTE and _LAST_PALETTE == t:
        return
    _LAST_PALETTE = dict(t)
    _THEME_VERSION += 1   # 主题变更 → 渲染缓存整体失效
    BG = t["BG"]; BG_BOTTOM = t["BG_BOTTOM"]; PANEL = t["PANEL"]; CARD = t["CARD"]
    BORDER = t["BORDER"]; BORDER_SOFT = t["BORDER_SOFT"]; TEXT = t["TEXT"]
    TEXT_DIM = t["TEXT_DIM"]; ACCENT = t["ACCENT"]; ACCENT_HOVER = t["ACCENT_HOVER"]
    ICON_GRAY = t["TEXT_DIM"]   # 工具/技能/插件矢量图的淡灰，随主题切换
    LINK_COLOR = t["LINK_COLOR"]; USER_BG = t["USER_BG"]; AI_BG = t["AI_BG"]
    OK = t["OK"]; WARN = t["WARN"]; ERR = t["ERR"]; HOVER = t["HOVER"]
    CODE_ACCENT = t["CODE_ACCENT"]
    CODE_BG = t.get("CODE_BG") or PANEL   # 代码块背景：浅色下区别于白色气泡，深色下略高于面板
    # 重建模块级派生样式常量（以更新后的颜色重新生成字符串；几何/字距规格取自 Design Tokens）
    _BTN_GHOST = (f"QPushButton {{ background: transparent; color: {TEXT_DIM};"
                  f"border: 1px solid {BORDER}; border-radius: {RADIUS_SM}px;"
                  f"padding: {SPACING_SM}px {SPACING_MD}px;"
                  f"font-size: {FONT_SMALL}px; font-weight: 600; }}"
                  f"QPushButton:hover {{ border-color: {ACCENT_HOVER}; color: {TEXT};"
                  f"background: {HOVER}; }}")
    _BTN_COMPACT = (f"QPushButton {{ background: transparent; color: {TEXT_DIM};"
                    f"border: 1px solid {BORDER}; border-radius: {RADIUS_SM}px;"
                    f"padding: 1px {SPACING_SM}px; font-size: {FONT_SMALL}px; }}"
                    f"QPushButton:hover {{ border-color: {ACCENT_HOVER}; color: {TEXT};"
                    f"background: {HOVER}; }}")
    _BTN_GHOST_ACCENT = (f"QPushButton {{ background: transparent; color: {ACCENT_HOVER};"
                         f"border: 1px solid {BORDER_SOFT}; border-radius: {RADIUS_SM}px;"
                         f"padding: {SPACING_SM}px {SPACING_MD}px;"
                         f"font-size: {FONT_SMALL}px; font-weight: 600; }}"
                         f"QPushButton:hover {{ border-color: {ACCENT_HOVER}; background: {HOVER}; }}")
    _BTN_PRIMARY = (f"QPushButton {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
                    f"stop:0 {ACCENT_HOVER}, stop:1 {ACCENT}); color: #FFFFFF; border: none;"
                    f"border-radius: 17px; padding: 0 {SPACING_LG}px;"
                    f"font-size: {FONT_BODY}px; font-weight: 700; }}"
                    f"QPushButton:hover {{ background: {ACCENT_HOVER}; }}"
                    f"QPushButton:pressed {{ background: {QColor(ACCENT).darker(118).name()}; }}"
                    f"QPushButton:disabled {{ background: {CARD}; color: {TEXT_DIM}; }}")
    _BTN_DIM = (f"QPushButton {{ background: #94A3B8; color: #FFFFFF; border: none;"
                f"border-radius: 17px; padding: 0 {SPACING_LG}px;"
                f"font-size: {FONT_BODY}px; font-weight: 700; }}"
                f"QPushButton:hover {{ background: #64748B; }}"
                f"QPushButton:disabled {{ background: {CARD}; color: {TEXT_DIM}; }}")
    _QCOMBO = (f"QComboBox {{ background: {PANEL}; color: {TEXT}; border: 1px solid {BORDER};"
               f"border-radius: {RADIUS_SM}px; padding: {SPACING_SM}px {SPACING_MD}px;"
               f"font-size: {FONT_SMALL}px; }}"
               f"QComboBox::drop-down {{ border: none; width: 22px; }}"
               f"QComboBox QAbstractItemView {{ background: {PANEL}; color: {TEXT};"
               f"border: 1px solid {BORDER}; selection-background-color: {HOVER};"
               f"selection-color: {TEXT}; }}")
    _BTN_ICON = (f"QPushButton {{ background: transparent; border: 1px solid {BORDER};"
                 f"border-radius: {RADIUS_SM}px; }}"
                 f"QPushButton:hover {{ background: {HOVER}; border-color: {BORDER_SOFT}; }}")
    _BTN_DANGER = (f"QPushButton {{ background: {ERR}; color: #FFFFFF; border: none;"
                   f"border-radius: {RADIUS_SM}px; padding: 0; }}"
                   f"QPushButton:hover {{ background: #EF4444; }}"
                   f"QPushButton:disabled {{ background: {CARD}; color: {TEXT_DIM}; }}")
    _apply_global_dialog_qss()


# 液态玻璃「下拉/菜单/提示」弹出层渐变：低磨砂、高液态——白底更透（磨砂弱），
# 顶部白色高光点睛（液态感），供 全局对话框 QSS / AI 设置弹窗 / 自带样式下拉 三处复用，
# 避免梯度串散落硬编码，方便统一调风格。
_GLASS_POPUP_BG = ("qlineargradient(x1:0,y1:0,x2:0,y2:1,"
                   "stop:0 rgba(255,255,255,196), stop:0.15 rgba(255,255,255,110),"
                   "stop:0.5 rgba(240,243,249,76), stop:1 rgba(250,251,253,104))")
_GLASS_POPUP_BD = "rgba(255,255,255,225)"
_GLASS_POPUP_HOVER = "rgba(255,255,255,110)"
_GLASS_POPUP_SEL = "rgba(255,255,255,150)"
# QMenu 专用浅透明白液态玻璃（叠在 Acrylic 真毛玻璃上透出磨砂，避免 milky 盖成白板）：
# 顶部受光→明亮液态→底部微光；item 高亮用不透明色，避免透明窗口里叠加泛白
_GLASS_MENU_BG = ("qlineargradient(x1:0,y1:0,x2:0,y2:1,"
                  "stop:0 rgba(255,255,255,115), stop:0.18 rgba(255,255,255,78),"
                  "stop:0.6 rgba(255,255,255,50), stop:1 rgba(255,255,255,86))")
_GLASS_MENU_SEL = "#FFFFFF"
_GLASS_MENU_ITEM_HOVER = "#FFFFFF"


def _glass_combo_qss(text_color: str = "", arrow_color: str = "",
                     radius: str = "8px", with_arrow: bool = True) -> str:
    """液态玻璃下拉框 QSS：透明玻璃底 + 白色受光边 + 黑字 +（可选）CSS 三角箭头。
    给设置页/编辑弹窗等「自带样式的 QComboBox」补液态玻璃外观，
    避免实体小方框、无下拉箭头（自带样式会屏蔽应用级箭头的 QSS）。
    with_arrow=False 时不输出箭头规则，供 _ArrowComboBox 自绘箭头时避免出现双箭头。"""
    tc = text_color or TEXT
    ac = arrow_color or TEXT_DIM
    qss = (
        f"QComboBox {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
        f"stop:0 rgba(255,255,255,52), stop:0.45 rgba(255,255,255,16), "
        f"stop:1 rgba(255,255,255,36)); color: {tc};"
        f"border: 1px solid rgba(255,255,255,110); border-top: 1px solid rgba(255,255,255,225);"
        f"border-radius: {radius}; }}"
        f"QComboBox::drop-down {{ border: none; width: 24px; background: transparent; }}"
    )
    if with_arrow:
        qss += (f"QComboBox::down-arrow {{ image: none; border-left: 4px solid transparent;"
                f" border-right: 4px solid transparent; border-top: 5px solid {ac};"
                f" margin-right: 6px; }}")
    return qss


# 输入行圆形图标按钮直径（上传 / 优化共用；圆角取直径一半即正圆，见 _round_icon_btn_qss）
_ROUND_BTN_D = 42


def _round_icon_btn_qss(diameter: int) -> str:
    """输入行圆形图标按钮的统一皮肤（上传 / 优化共用，两者观感必须完全一致）。

    实心面板底 + 1px 描边正圆（圆角取直径一半），hover 时描边转强调色。
    透明底 + 无描边时圆角看不出来，按钮就成了一个裸图标（用户反馈「没有圆形的 UI 附着样式」）。
    """
    return (f"QPushButton {{ background: {PANEL}; border: 1px solid {BORDER};"
            f"border-radius: {diameter // 2}px; }}"
            f"QPushButton:hover {{ border: 1px solid {ACCENT}; }}")


def _glass_combo_view_qss(text_color: str = "", radius: str = f"{RADIUS_MD}px") -> str:
    """液态玻璃下拉「弹出视图」QSS：低磨砂高液态的透明白渐变 + 白色受光描边 + 圆角。
    用于自带样式的 QComboBox，覆盖默认的不透明 PANEL 白块底；圆角与 _PopupGlassFixer
    的圆角蒙版一致（radius 默认 12px），保证无方块边角。
    弹出的矩形容器仅由蒙版剪圆角，玻璃本身由这里的半透明渐变呈现（不依赖 Acrylic）。"""
    tc = text_color or TEXT
    return (
        f"QComboBox QAbstractItemView {{ background: {_GLASS_POPUP_BG}; color: {tc};"
        f" border: 1px solid {_GLASS_POPUP_BD}; border-radius: {radius};"
        f" padding: 4px; outline: none;"
        f" selection-background-color: {_GLASS_POPUP_SEL}; selection-color: {tc}; }}"
        f"QComboBox QAbstractItemView::item {{ padding: {SPACING_SM}px {SPACING_MD}px;"
        f" border-radius: {RADIUS_SM}px; }}"
        f"QComboBox QAbstractItemView::item:hover {{ background: {_GLASS_POPUP_HOVER}; }}"
        f"QComboBox QAbstractItemView QScrollBar:vertical {{ background: transparent; width: 8px; }}"
        f"QComboBox QAbstractItemView QScrollBar::handle:vertical"
        f" {{ background: rgba(255,255,255,170); border-radius: 4px; min-height: 24px; }}"
    )


def _global_dialog_qss() -> str:
    """原生对话框全局样式表（QMessageBox/QInputDialog/QFileDialog/QColorDialog/QMenu/
    QToolTip 等）：用当前主题常量实时换色，实现默认 UI/UX 双模式自适应。
    仅作用于未自带样式的对话框类控件，面板/设置页自身样式（widget 级 QSS）优先级更高，
    不会被覆盖。
    自定义 UI/UX（液态玻璃）激活时，下拉/菜单/提示改用与主面板相同的液态玻璃渐变
    + 白色高光描边（只换背景风格，字体/字号与主风格参数均不变）。"""
    _glass = agent_ui_ux.is_custom_package_active()
    if _glass:
        # 下拉/菜单/Tooltip 弹出层：液态玻璃——顶部高光→中段高透亮（磨砂弱、液态强）
        _popup_bg = _GLASS_POPUP_BG
        _popup_bd = _GLASS_POPUP_BD
        _popup_hover = _GLASS_POPUP_HOVER
        _popup_sel = _GLASS_POPUP_SEL
        _tip_bg = "#F7F9FC"             # Tooltip：液态玻璃实心浅底（杜绝纯黑）
        _tip_bd = "rgba(255,255,255,225)"
        # 对话框底 / 输入框 / 按钮：不透明浅色液态磨砂渐变（磨砂感而非暗色感，黑字可读）。
        # 注意 QMessageBox/QInputDialog 等原生对话框窗口不是半透明窗口，用半透明渐变会
        # 与窗口默认深色底叠加成"纯黑" → 必须用不透明浅色。
        _dlg_bg = ("qlineargradient(x1:0,y1:0,x2:0,y2:1,"
                   "stop:0 #F8FAFD, stop:0.5 #F0F3F9, stop:1 #F6F8FC)")
        _in_bg = ("qlineargradient(x1:0,y1:0,x2:0,y2:1,"
                  "stop:0 rgba(255,255,255,44), stop:0.5 rgba(255,255,255,16), "
                  "stop:1 rgba(255,255,255,32))")
        _in_hover = ("qlineargradient(x1:0,y1:0,x2:0,y2:1,"
                     "stop:0 rgba(255,255,255,70), stop:0.5 rgba(255,255,255,26), "
                     "stop:1 rgba(255,255,255,48))")
        # 勾选框/单选的液态玻璃指示器：内嵌对勾/圆点（取活跃包 resources/ 下的 SVG）
        try:
            _res = (agent_ui_ux._UI_UX_DIR / agent_ui_ux.get_active_package()
                    / "resources")
            _check_url = str(_res / "check.svg").replace("\\", "/")
            _radio_url = str(_res / "radio.svg").replace("\\", "/")
        except Exception:
            _check_url = _radio_url = ""
    else:
        _popup_bg = PANEL
        _popup_bd = BORDER
        _popup_hover = HOVER
        _popup_sel = HOVER
        _dlg_bg = PANEL
        _in_bg = PANEL
        _in_hover = HOVER
        _check_url = _radio_url = ""
        _tip_bg = PANEL                # Tooltip：跟随主题面板/强调色（深色下为深底浅字）
        _tip_bd = ACCENT
    # QMenu 底：液态玻璃模式用稳定不透明液态渐变（杜绝 忽暗/hover 泛白），默认模式用面板色
    if _glass:
        _menu_bg, _menu_sel, _menu_hover = _GLASS_MENU_BG, _GLASS_MENU_SEL, _GLASS_MENU_ITEM_HOVER
    else:
        _menu_bg, _menu_sel, _menu_hover = PANEL, HOVER, HOVER
    # 液态玻璃专属控件样式：勾选框/单选指示器 + 下拉小箭头（仅自定义包激活时附加）
    _ctrl_qss = ""
    if _glass:
        _ctrl_qss = (
            f"QCheckBox, QRadioButton {{ color: {TEXT}; spacing: 8px; }}"
            f"QCheckBox::indicator, QRadioButton::indicator {{ width: 18px; height: 18px;"
            f" border: 1px solid {_popup_bd}; border-radius: 6px;"
            f" background: rgba(255,255,255,28); }}"
            f"QRadioButton::indicator {{ border-radius: 9px; }}"
            f"QCheckBox::indicator:checked, QRadioButton::indicator:checked {{"
            f" background: rgba(255,255,255,150);"
            f" border: 1px solid rgba(255,255,255,170); }}"
            f"QCheckBox::indicator:checked {{ image: url('{_check_url}'); }}"
            f"QRadioButton::indicator:checked {{ image: url('{_radio_url}'); }}"
            f"QComboBox::drop-down {{ border: none; width: 24px;"
            f" background: transparent; }}"
            f"QComboBox::down-arrow {{ image: none; border-left: 4px solid transparent;"
            f" border-right: 4px solid transparent; border-top: 5px solid {TEXT_DIM};"
            f" margin-right: 6px; }}"
        )
    _base = (
        f"QMessageBox, QInputDialog, QFileDialog, QColorDialog, QProgressDialog {{"
        f" background: {_dlg_bg}; }}"
        f"QMessageBox QLabel, QInputDialog QLabel, QColorDialog QLabel,"
        f"QProgressDialog QLabel {{ color: {TEXT}; }}"
        f"QMessageBox QPushButton, QInputDialog QPushButton, QFileDialog QPushButton,"
        f"QColorDialog QPushButton, QDialogButtonBox QPushButton {{"
        f" background: {_in_bg}; color: {TEXT}; border: 1px solid {_popup_bd};"
        f" border-top: 1px solid rgba(255,255,255,240);"
        f" border-radius: 8px; padding: 6px 18px; min-width: 60px; }}"
        f"QMessageBox QPushButton:hover, QInputDialog QPushButton:hover,"
        f"QFileDialog QPushButton:hover, QColorDialog QPushButton:hover,"
        f"QDialogButtonBox QPushButton:hover {{ border-color: {_popup_bd};"
        f" color: {TEXT}; background: {_in_hover}; }}"
        f"QMessageBox QPushButton:default, QInputDialog QPushButton:default,"
        f"QDialogButtonBox QPushButton:default {{ background: {ACCENT}; color: #FFFFFF;"
        f" border: none; }}"
        f"QInputDialog QLineEdit, QFileDialog QLineEdit, QFileDialog QComboBox,"
        f"QColorDialog QLineEdit {{ background: {_in_bg}; color: {TEXT};"
        f" border: 1px solid {_popup_bd}; border-top: 1px solid rgba(255,255,255,240);"
        f" border-radius: 6px; padding: 5px 10px; }}"
        f"QFileDialog QListView, QFileDialog QTreeView, QFileDialog QListWidget,"
        f"QColorDialog QListView {{ background: {_in_bg}; color: {TEXT};"
        f" border: 1px solid {_popup_bd}; }}"
        f"QFileDialog QHeaderView::section {{ background: {_in_bg}; color: {TEXT_DIM};"
        f" border: none; border-bottom: 1px solid {_popup_bd}; padding: 4px 8px; }}"
        f"QFileDialog QSplitter::handle {{ background: {_popup_bd}; }}"
        f"QComboBox QAbstractItemView {{ background: {_popup_bg}; color: {TEXT};"
        f" border: 1px solid {_popup_bd}; border-radius: 12px; padding: 4px;"
        f" outline: none; selection-background-color: {_popup_sel};"
        f" selection-color: {TEXT}; }}"
        f"QComboBox QAbstractItemView::item {{ padding: {SPACING_SM}px {SPACING_MD}px;"
        f" border-radius: {RADIUS_SM}px; }}"
        f"QComboBox QAbstractItemView::item:hover {{ background: {_popup_hover}; }}"
        f"QComboBox QAbstractItemView QScrollBar:vertical {{ background: transparent; width: 8px; }}"
        f"QComboBox QAbstractItemView QScrollBar::handle:vertical {{ background: rgba(255,255,255,170);"
        f" border: 1px solid rgba(120,130,145,60); border-radius: 4px; min-height: 24px; }}"
        f"QComboBox QAbstractItemView QScrollBar::handle:vertical:hover {{ background: rgba(255,255,255,220); }}"
        f"QComboBox QAbstractItemView QScrollBar::add-line:vertical,"
        f"QComboBox QAbstractItemView QScrollBar::sub-line:vertical {{ height: 0; }}"
        f"QMenu {{ background: {_menu_bg}; color: {TEXT}; border: 1px solid {_GLASS_POPUP_BD};"
        f" border-radius: 12px; padding: 4px; }}"
        f"QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}"
        f"QMenu::item:selected {{ background: {_menu_sel}; color: {TEXT};"
        f" border: 1px solid rgba(255,255,255,230); }}"
        f"QMenu::item:hover {{ background: {_menu_hover}; }}"
        f"QMenu::separator {{ height: 1px; background: {_popup_hover};"
        f" margin: 6px 10px; }}"
        f"QToolTip {{ background: {_tip_bg}; color: {TEXT}; border: 1px solid {_tip_bd};"
        f" border-radius: 8px; padding: 4px 8px; }}"
        f"QScrollBar:vertical {{ background: transparent; width: 10px; }}"
        f"QScrollBar::handle:vertical {{ background: {_popup_bd}; border-radius: 5px;"
        f" min-height: 30px; }}"
        f"QScrollBar::handle:vertical:hover {{ background: {_popup_sel}; }}"
        f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}"
        f"QScrollBar:horizontal {{ background: transparent; height: 10px; }}"
        f"QScrollBar::handle:horizontal {{ background: {_popup_bd}; border-radius: 5px;"
        f" min-width: 30px; }}"
        f"QScrollBar::handle:horizontal:hover {{ background: {_popup_sel}; }}"
    ) + _ctrl_qss
    return _base


# 应用级全局 QSS 内容缓存：_retheme / 主题切换期间会多次调用 _apply_global_dialog_qss，
# 而 app.setStyleSheet 会触发所有控件全量重算样式（面板含大量 HTML 聊天气泡/子窗口时极
# 昂贵，反复调用会长时间阻塞主线程，表现为"切换 UIUX 后主面板无响应"）。
# "applied"=上一次真正 setStyleSheet 的内容；"pending"=重建期挂起待应用标记。
_APP_QSS_CACHE = {"applied": None, "pending": False}
_APP_QSS_DEFER = False      # 面板就地重建期间置 True：暂不触发应用级全量重算


def _apply_global_dialog_qss() -> None:
    """把对话框全局样式应用到 QApplication（无应用时跳过）。
    _apply_colors 每次主题更新后调用 → 原生对话框随深浅主题即时换色（双模式自适应）；
    同时安装应用级自动液态玻璃化（自定义包激活时所有弹窗自动叠加 Acrylic）。
    带内容缓存去重：QSS 内容未变化不重复 setStyleSheet（避免主线程长阻塞）。"""
    try:
        app = QApplication.instance()
        if app is None:
            return
        if _APP_QSS_DEFER:
            # _retheme 重建期：旧控件树即将销毁/新树重建中，对其全量重算样式纯属浪费
            # （且会触发整树 relayout，是无响应根因）→ 仅当内容确实变化时标记待应用；
            # 重建前已在空控件树上提前落地最终内容时，重建期的过渡色板变化会被去重。
            qss = _global_dialog_qss()
            if qss != _APP_QSS_CACHE["applied"]:
                _APP_QSS_CACHE["pending"] = True
            return
        qss = _global_dialog_qss()
        if qss == _APP_QSS_CACHE["applied"] and not _APP_QSS_CACHE["pending"]:
            return
        _APP_QSS_CACHE["pending"] = False
        _APP_QSS_CACHE["applied"] = qss
        app.setStyleSheet(qss)
        try:
            agent_ui_ux.ensure_auto_glass()
        except Exception:
            pass
    except Exception:
        pass


# 最近一次实际应用的主题名（apply_theme / apply_theme_custom 更新）。
# AgentPanel._refresh_meta 轮询比对 _resolve_theme()：auto 模式下跨 8:00/20:00 时间点后
# 实际主题变化而面板主体未重建（标题栏/对话框边框随 showEvent 即时换色，主体样式只随
# _retheme 更新）→ 检测到不一致时自动就地重建，无需重启即可整体换色。
_APPLIED_THEME = ""


def apply_theme() -> str:
    """按当前设置（含时间自动）更新模块级颜色常量与派生样式常量，返回实际主题名。
    由主题切换/启动时调用；后接 AgentPanel 重建以即时生效。"""
    global _APPLIED_THEME
    cur = _resolve_theme()
    _apply_colors(_THEMES[cur])
    _APPLIED_THEME = cur
    # 同步应用级色板（styles.PALETTE）：两套色板必须同源，否则会出现
    # 「对话框按深色默认值出深底 + 本模块按设置出浅色控件」这类混搭。
    try:
        from zhuzhu_Copilot.ui import styles as _styles
        _styles.set_palette(cur)
    except Exception:
        pass
    return cur


def apply_theme_custom(overrides: dict = None) -> str:
    """按当前设置更新主题，并用 overrides 覆盖色板中的颜色键（供 UI/UX 包自定义主题）。
    overrides 只接受色板中存在的键，其余保持全局主题值。返回实际主题名。"""
    global _APPLIED_THEME
    cur = _resolve_theme()
    t = dict(_THEMES[cur])
    if overrides:
        for k, v in overrides.items():
            if k in t:
                t[k] = v
    _apply_colors(t)
    _APPLIED_THEME = cur
    return cur


apply_theme()   # 模块加载即按设置/时间确定初始主题


def _scrollbar_css(width: int = 8, radius: int = 4, both: bool = True) -> str:
    """主题自适应滚动条样式：轨道用 BG、滑块用 BORDER（深色=纯黑、浅色=浅灰）。
    随主题重建，避免浅色模式残留纯黑轨道/滑块。both=False 时仅生成垂直滚动条。
    液态玻璃激活时改用透明轨道 + 亮白磨砂滑块（消除暗色主题残留）。"""
    if agent_ui_ux.is_custom_package_active():
        g = (f"QScrollBar:vertical {{ background: transparent; width: {width + 2}px; }}"
             f"QScrollBar::handle:vertical {{ background: rgba(255,255,255,112);"
             f"border: 1px solid rgba(255,255,255,95); border-radius: {radius + 1}px; min-height: 30px; }}"
             f"QScrollBar::handle:vertical:hover {{ background: rgba(255,255,255,185); }}"
             f"QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}"
             f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical"
             f" {{ background: transparent; width: 0; height: 0; }}")
        if not both:
            return g
        return g + (
            f"QScrollBar:horizontal {{ background: transparent; height: {width + 2}px; }}"
            f"QScrollBar::handle:horizontal {{ background: rgba(255,255,255,112);"
            f"border: 1px solid rgba(255,255,255,95); border-radius: {radius + 1}px; min-width: 30px; }}"
            f"QScrollBar::handle:horizontal:hover {{ background: rgba(255,255,255,185); }}"
            f"QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}"
            f"QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal"
            f" {{ background: transparent; width: 0; height: 0; }}")
    v = (f"QScrollBar:vertical {{ background: transparent; width: {width + 4}px; }}"
         f"QScrollBar::handle:vertical {{ background: {BORDER};"
         f"border-radius: {radius}px; min-height: 30px; margin: 0 2px; }}"
         f"QScrollBar::handle:vertical:hover {{ background: {BORDER_SOFT}; }}"
         f"QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical"
         f"{{ background: transparent; }}"
         f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical"
         f"{{ background: transparent; width: 0px; height: 0px; }}")
    if not both:
        return v
    return v + (
        f"QScrollBar:horizontal {{ background: transparent; height: {width + 4}px; }}"
        f"QScrollBar::handle:horizontal {{ background: {BORDER};"
        f"border-radius: {radius}px; min-width: 30px; margin: 2px 0; }}"
        f"QScrollBar::handle:horizontal:hover {{ background: {BORDER_SOFT}; }}"
        f"QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal"
        f"{{ background: transparent; }}"
        f"QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal"
        f"{{ background: transparent; width: 0px; height: 0px; }}")


def _app_icon_path() -> str:
    if getattr(sys, "frozen", False):
        return os.path.join(getattr(sys, "_MEIPASS", "."), "assets", "icon.ico")
    return str(Path(__file__).resolve().parents[3] / "assets" / "icon.ico")


def _dark_titlebar(widget) -> None:
    """把 Windows 系统标题栏设为深色（暗色标题栏 + 纯黑标题栏/边框），与面板纯黑风格统一。
    浅色主题下跳过：保留系统浅色标题栏，避免黑底标题栏破坏浅色整体风格。"""
    if _resolve_theme() != "dark":
        return
    try:
        hwnd = int(widget.winId())
        dwm = ctypes.windll.dwmapi
        on = ctypes.c_int(1)
        # DWMWA_USE_IMMERSIVE_DARK_MODE=20（Win10 1903+ / Win11）
        dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(on), ctypes.sizeof(on))
        # 标题栏/边框取主题石墨黑（BGR 序：#101216 → 0x161210），与面板底色统一
        black = ctypes.c_int(0x161210)
        # DWMWA_BORDER_COLOR=34 / DWMWA_CAPTION_COLOR=35（Win11 22H2+，旧系统失败自动忽略）
        for attr in (34, 35):
            try:
                dwm.DwmSetWindowAttribute(hwnd, attr,
                                          ctypes.byref(black), ctypes.sizeof(black))
            except Exception:
                pass
        _redraw_titlebar(hwnd)
    except Exception:
        pass


def _light_titlebar(widget) -> None:
    """浅色主题：恢复系统浅色标题栏（关闭沉浸式深色模式，重置标题栏/边框为系统默认）"""
    try:
        hwnd = int(widget.winId())
        dwm = ctypes.windll.dwmapi
        off = ctypes.c_int(0)
        dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(off), ctypes.sizeof(off))
        default = ctypes.c_uint(0xFFFFFFFE)   # DWMWA_COLOR_DEFAULT（系统默认色）
        for attr in (34, 35):
            try:
                dwm.DwmSetWindowAttribute(hwnd, attr,
                                          ctypes.byref(default), ctypes.sizeof(default))
            except Exception:
                pass
        _redraw_titlebar(hwnd)
    except Exception:
        pass


def _redraw_titlebar(hwnd) -> None:
    """用 SWP_FRAMECHANGED 强制立即重绘标题栏/边框，否则 DWM 属性更改不会即时生效"""
    try:
        # SWP_NOSIZE|SWP_NOMOVE|SWP_NOZORDER|SWP_FRAMECHANGED
        ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0,
                                          0x0001 | 0x0002 | 0x0004 | 0x0020)
    except Exception:
        pass


# 统一给所有 QDialog 子类深色标题栏（AgentPanel 等自实现 showEvent 经 super() 同样生效）
_orig_dialog_show = QDialog.showEvent


def _dialog_show(self, e):
    _orig_dialog_show(self, e)
    _dark_titlebar(self)


QDialog.showEvent = _dialog_show


def _frameless_titlebar(dialog, title, closable=True):
    """无边框对话框的自定义标题栏：拖动（startSystemMove）+ 可选关闭按钮。"""
    bar = QWidget()
    bar.setFixedHeight(48)
    bar.setStyleSheet("background: transparent;")
    lay = QHBoxLayout(bar)
    lay.setContentsMargins(16, 0, 12, 0)
    lay.setSpacing(10)
    lbl = QLabel(title)
    lbl.setStyleSheet(f"color: {TEXT}; font-size: 15px; font-weight: 800;")
    lay.addWidget(lbl)
    lay.addStretch(1)
    if closable:
        b = QPushButton("✕")
        b.setFixedSize(32, 32)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setAutoDefault(False)
        b.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {TEXT_DIM};"
            "border: none; border-radius: 8px; font-size: 14px; }}"
            f"QPushButton:hover {{ background: rgba(255,255,255,40); color: {TEXT}; }}")
        b.clicked.connect(dialog.reject)
        lay.addWidget(b)

    def _press(e):
        if e.button() == Qt.MouseButton.LeftButton:
            try:
                wh = dialog.windowHandle()
                if wh is not None:
                    wh.startSystemMove()
            except Exception:
                pass
            e.accept()
    bar.mousePressEvent = _press
    return bar


def _std_icon(sp) -> QIcon:
    """系统矢量图标（无 emoji）"""
    return QApplication.style().standardIcon(sp)


# 齿轮（设置）：Lucide 开源简约线条矢量图（stroke 用 {color} 占位，由 _svg_icon 着色）
_GEAR_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
             'stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
             '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08'
             'a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74'
             'l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25'
             'a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25'
             'a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74'
             'v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08'
             'a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/>'
             '<circle cx="12" cy="12" r="3"/></svg>')


@lru_cache(maxsize=512)
def _svg_icon(svg: str, size: int = 18, color: str = TEXT_DIM) -> QIcon:
    """渲染内联 SVG 线条矢量图标（开源矢量路径，统一着色，线条风格一致）。
    结果按 (svg, size, color) 缓存：主题切换/面板重建时重复图标直接命中，避免反复渲染。"""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    try:
        r = QSvgRenderer(svg.replace("{color}", color).encode("utf-8"))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r.render(p)
        p.end()
    except Exception:
        pass
    return QIcon(pm)


@lru_cache(maxsize=512)
def _line_icon(kind: str, size: int = 18, color: str = TEXT_DIM) -> QIcon:
    """淡灰色线条简约矢量图标（QPainter 手绘，统一线条风格，不依赖系统图标/emoji）。
    结果按 (kind, size, color) 缓存：主题切换/面板重建时重复图标直接命中。"""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), 1.8)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    s = float(size)
    if kind == "send":          # 上箭头（发送）
        p.drawLine(QPointF(s * 0.5, s * 0.20), QPointF(s * 0.5, s * 0.80))
        p.drawLine(QPointF(s * 0.26, s * 0.46), QPointF(s * 0.5, s * 0.20))
        p.drawLine(QPointF(s * 0.74, s * 0.46), QPointF(s * 0.5, s * 0.20))
    elif kind == "stop":        # 实心方块（停止）
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color))
        r = s * 0.28
        p.drawRoundedRect(QRectF(s * 0.5 - r, s * 0.5 - r, r * 2, r * 2),
                          s * 0.08, s * 0.08)
    elif kind == "play":        # 播放（右三角）
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color))
        pts = (QPointF(s * 0.34, s * 0.22), QPointF(s * 0.34, s * 0.78),
               QPointF(s * 0.78, s * 0.50))
        p.drawPolygon(*pts)
    elif kind == "pause":       # 暂停（两竖条）
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color))
        p.drawRoundedRect(QRectF(s * 0.30, s * 0.22, s * 0.14, s * 0.56), 1.5, 1.5)
        p.drawRoundedRect(QRectF(s * 0.56, s * 0.22, s * 0.14, s * 0.56), 1.5, 1.5)
    elif kind == "prev":        # 上一首（左三角 + 竖条）
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color))
        p.drawRoundedRect(QRectF(s * 0.26, s * 0.28, s * 0.10, s * 0.44), 1.2, 1.2)
        pts = (QPointF(s * 0.38, s * 0.26), QPointF(s * 0.38, s * 0.74),
               QPointF(s * 0.72, s * 0.50))
        p.drawPolygon(*pts)
    elif kind == "next":        # 下一首（右三角 + 竖条）
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color))
        p.drawRoundedRect(QRectF(s * 0.64, s * 0.28, s * 0.10, s * 0.44), 1.2, 1.2)
        pts = (QPointF(s * 0.62, s * 0.26), QPointF(s * 0.62, s * 0.74),
               QPointF(s * 0.28, s * 0.50))
        p.drawPolygon(*pts)
    elif kind == "shuffle":     # 随机（两交叉箭头）
        p.drawLine(QPointF(s * 0.22, s * 0.32), QPointF(s * 0.50, s * 0.32))
        p.drawLine(QPointF(s * 0.22, s * 0.68), QPointF(s * 0.50, s * 0.68))
        p.drawLine(QPointF(s * 0.50, s * 0.32), QPointF(s * 0.78, s * 0.68))
        p.drawLine(QPointF(s * 0.78, s * 0.28), QPointF(s * 0.78, s * 0.40))
        p.drawLine(QPointF(s * 0.78, s * 0.68), QPointF(s * 0.70, s * 0.68))
        p.drawLine(QPointF(s * 0.62, s * 0.40), QPointF(s * 0.30, s * 0.40))
        p.drawLine(QPointF(s * 0.50, s * 0.68), QPointF(s * 0.30, s * 0.68))
    elif kind == "repeat":      # 循环（圆形箭头）
        p.drawArc(QRectF(s * 0.16, s * 0.26, s * 0.58, s * 0.48),
                  -60 * 16, 300 * 16)
        p.drawLine(QPointF(s * 0.68, s * 0.22), QPointF(s * 0.74, s * 0.36))
        p.drawLine(QPointF(s * 0.68, s * 0.22), QPointF(s * 0.58, s * 0.26))
    elif kind == "retry":       # 重试（圆形箭头 + 箭头尖）
        p.drawArc(QRectF(s * 0.20, s * 0.20, s * 0.60, s * 0.60),
                  40 * 16, 285 * 16)
        p.drawLine(QPointF(s * 0.68, s * 0.18), QPointF(s * 0.72, s * 0.36))
        p.drawLine(QPointF(s * 0.68, s * 0.18), QPointF(s * 0.54, s * 0.24))
    elif kind == "vol":         # 音量（喇叭 + 声波）
        p.drawPolygon(*((QPointF(s * 0.22, s * 0.40), QPointF(s * 0.36, s * 0.40),
                         QPointF(s * 0.50, s * 0.30), QPointF(s * 0.50, s * 0.70),
                         QPointF(s * 0.36, s * 0.60), QPointF(s * 0.22, s * 0.60))))
        p.drawArc(QRectF(s * 0.52, s * 0.32, s * 0.22, s * 0.36), -55 * 16, 110 * 16)
        p.drawArc(QRectF(s * 0.52, s * 0.22, s * 0.30, s * 0.56), -50 * 16, 100 * 16)
    elif kind == "music":       # 音乐（双音符）
        p.setPen(QPen(QColor(color), 1.6, cap=Qt.PenCapStyle.RoundCap,
                      join=Qt.PenJoinStyle.RoundJoin))
        p.drawLine(QPointF(s * 0.30, s * 0.26), QPointF(s * 0.72, s * 0.18))
        p.drawLine(QPointF(s * 0.72, s * 0.18), QPointF(s * 0.72, s * 0.66))
        p.drawEllipse(QPointF(s * 0.30, s * 0.68), s * 0.11, s * 0.10)
        p.drawEllipse(QPointF(s * 0.72, s * 0.68), s * 0.11, s * 0.10)
        p.drawLine(QPointF(s * 0.30, s * 0.26), QPointF(s * 0.30, s * 0.66))
    elif kind == "full":        # 全屏（四角扩出箭头）
        for x1, y1, x2, y2 in ((0.28, 0.28, 0.42, 0.28),   # 左上→右
                               (0.28, 0.28, 0.28, 0.42),
                               (0.72, 0.28, 0.58, 0.28),
                               (0.72, 0.28, 0.72, 0.42),
                               (0.28, 0.72, 0.42, 0.72),
                               (0.28, 0.72, 0.28, 0.58),
                               (0.72, 0.72, 0.58, 0.72),
                               (0.72, 0.72, 0.72, 0.58)):
            p.drawLine(QPointF(s * x1, s * y1), QPointF(s * x2, s * y2))
    elif kind == "trash":       # 垃圾桶（清空）
        p.drawLine(QPointF(s * 0.22, s * 0.28), QPointF(s * 0.78, s * 0.28))
        p.drawLine(QPointF(s * 0.36, s * 0.28), QPointF(s * 0.36, s * 0.19))
        p.drawLine(QPointF(s * 0.64, s * 0.28), QPointF(s * 0.64, s * 0.19))
        p.drawLine(QPointF(s * 0.40, s * 0.19), QPointF(s * 0.60, s * 0.19))
        p.drawLine(QPointF(s * 0.31, s * 0.34), QPointF(s * 0.36, s * 0.80))
        p.drawLine(QPointF(s * 0.69, s * 0.34), QPointF(s * 0.64, s * 0.80))
        p.drawLine(QPointF(s * 0.36, s * 0.80), QPointF(s * 0.64, s * 0.80))
        p.drawLine(QPointF(s * 0.45, s * 0.40), QPointF(s * 0.46, s * 0.70))
        p.drawLine(QPointF(s * 0.58, s * 0.40), QPointF(s * 0.57, s * 0.70))
    elif kind == "new":         # 新建对话（圆角框 + 加号）
        p.drawRoundedRect(QRectF(s * 0.18, s * 0.18, s * 0.64, s * 0.64),
                          s * 0.14, s * 0.14)
        p.drawLine(QPointF(s * 0.5, s * 0.32), QPointF(s * 0.5, s * 0.68))
        p.drawLine(QPointF(s * 0.32, s * 0.5), QPointF(s * 0.68, s * 0.5))
    elif kind == "plus":        # 加号（上传附件，精确居中）
        p.drawLine(QPointF(s * 0.5, s * 0.26), QPointF(s * 0.5, s * 0.74))
        p.drawLine(QPointF(s * 0.26, s * 0.5), QPointF(s * 0.74, s * 0.5))
    elif kind == "ok":          # 对勾（保存/确定）
        p.setPen(QPen(QColor(color), 2.2, cap=Qt.PenCapStyle.RoundCap,
                      join=Qt.PenJoinStyle.RoundJoin))
        p.drawLine(QPointF(s * 0.24, s * 0.52), QPointF(s * 0.44, s * 0.72))
        p.drawLine(QPointF(s * 0.44, s * 0.72), QPointF(s * 0.78, s * 0.30))
    elif kind == "no":          # 叉（拒绝）
        p.drawLine(QPointF(s * 0.28, s * 0.28), QPointF(s * 0.72, s * 0.72))
        p.drawLine(QPointF(s * 0.72, s * 0.28), QPointF(s * 0.28, s * 0.72))
    elif kind == "drive":       # 硬盘（通用与记忆）
        p.drawRoundedRect(QRectF(s * 0.16, s * 0.28, s * 0.68, s * 0.44),
                          s * 0.06, s * 0.06)
        p.setBrush(QColor(color))
        p.drawEllipse(QPointF(s * 0.32, s * 0.50), s * 0.05, s * 0.05)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(QPointF(s * 0.44, s * 0.50), QPointF(s * 0.78, s * 0.50))
    elif kind == "list":        # 列表（自定义规则）
        p.drawLine(QPointF(s * 0.22, s * 0.30), QPointF(s * 0.78, s * 0.30))
        p.drawLine(QPointF(s * 0.22, s * 0.50), QPointF(s * 0.78, s * 0.50))
        p.drawLine(QPointF(s * 0.22, s * 0.70), QPointF(s * 0.78, s * 0.70))
    elif kind == "doc":         # 文档（系统提示词）
        p.drawRoundedRect(QRectF(s * 0.22, s * 0.16, s * 0.56, s * 0.68),
                          s * 0.06, s * 0.06)
        p.drawLine(QPointF(s * 0.34, s * 0.36), QPointF(s * 0.66, s * 0.36))
        p.drawLine(QPointF(s * 0.34, s * 0.50), QPointF(s * 0.66, s * 0.50))
        p.drawLine(QPointF(s * 0.34, s * 0.64), QPointF(s * 0.56, s * 0.64))
    elif kind == "bash":        # 终端（bash 白名单）
        p.drawRoundedRect(QRectF(s * 0.16, s * 0.24, s * 0.68, s * 0.52),
                          s * 0.07, s * 0.07)
        p.drawLine(QPointF(s * 0.28, s * 0.40), QPointF(s * 0.40, s * 0.50))
        p.drawLine(QPointF(s * 0.28, s * 0.60), QPointF(s * 0.40, s * 0.50))
        p.drawLine(QPointF(s * 0.48, s * 0.56), QPointF(s * 0.72, s * 0.56))
    elif kind == "net":         # 网络（模型接入）
        p.drawEllipse(QPointF(s * 0.5, s * 0.28), s * 0.10, s * 0.10)
        p.drawArc(QRectF(s * 0.22, s * 0.28, s * 0.56, s * 0.52), 0, 180 * 16)
        p.drawArc(QRectF(s * 0.32, s * 0.28, s * 0.36, s * 0.34), 0, 180 * 16)
    elif kind == "folder":      # 文件夹（技能/浏览）
        p.drawRoundedRect(QRectF(s * 0.16, s * 0.32, s * 0.68, s * 0.46),
                          s * 0.05, s * 0.05)
        p.drawLine(QPointF(s * 0.16, s * 0.42), QPointF(s * 0.42, s * 0.42))
        p.drawLine(QPointF(s * 0.46, s * 0.42), QPointF(s * 0.52, s * 0.32))
        p.drawLine(QPointF(s * 0.84, s * 0.36), QPointF(s * 0.84, s * 0.32))
        p.drawLine(QPointF(s * 0.84, s * 0.32), QPointF(s * 0.76, s * 0.32))
    elif kind == "apps":        # 应用集合（四宫格，zhuzhu Copilot 浮层入口）
        for _dx, _dy in ((0.20, 0.20), (0.56, 0.20), (0.20, 0.56), (0.56, 0.56)):
            p.drawRoundedRect(QRectF(s * _dx, s * _dy, s * 0.24, s * 0.24),
                              s * 0.06, s * 0.06)
    elif kind == "user":        # 用户/Agent（圆头 + 肩部弧线）
        p.drawEllipse(QPointF(s * 0.5, s * 0.30), s * 0.14, s * 0.14)
        p.drawArc(QRectF(s * 0.26, s * 0.58, s * 0.48, s * 0.32), 0, 180 * 16)
    elif kind == "server":      # 服务器（MCP）
        p.drawRoundedRect(QRectF(s * 0.18, s * 0.20, s * 0.64, s * 0.24),
                          s * 0.05, s * 0.05)
        p.drawRoundedRect(QRectF(s * 0.18, s * 0.56, s * 0.64, s * 0.24),
                          s * 0.05, s * 0.05)
        p.setBrush(QColor(color))
        p.drawEllipse(QPointF(s * 0.30, s * 0.32), s * 0.04, s * 0.04)
        p.drawEllipse(QPointF(s * 0.30, s * 0.68), s * 0.04, s * 0.04)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(QPointF(s * 0.44, s * 0.32), QPointF(s * 0.72, s * 0.32))
        p.drawLine(QPointF(s * 0.44, s * 0.68), QPointF(s * 0.72, s * 0.68))
    elif kind == "puzzle":      # 拼图块（插件）
        p.drawRoundedRect(QRectF(s * 0.20, s * 0.20, s * 0.30, s * 0.30),
                          s * 0.08, s * 0.08)
        p.drawArc(QRectF(s * 0.34, s * 0.34, s * 0.24, s * 0.24), 0, 360 * 16)
        p.drawRoundedRect(QRectF(s * 0.50, s * 0.50, s * 0.30, s * 0.30),
                          s * 0.08, s * 0.08)
        p.drawLine(QPointF(s * 0.20, s * 0.50), QPointF(s * 0.20, s * 0.80))
        p.drawLine(QPointF(s * 0.20, s * 0.80), QPointF(s * 0.50, s * 0.80))
        p.drawLine(QPointF(s * 0.50, s * 0.20), QPointF(s * 0.80, s * 0.20))
        p.drawLine(QPointF(s * 0.80, s * 0.20), QPointF(s * 0.80, s * 0.50))
    elif kind == "mic":       # 麦克风（语音合成音色）
        p.drawRoundedRect(QRectF(s * 0.38, s * 0.14, s * 0.24, s * 0.44), s * 0.06, s * 0.06)
        p.drawLine(QPointF(s * 0.38, s * 0.52), QPointF(s * 0.62, s * 0.52))
        p.drawLine(QPointF(s * 0.38, s * 0.72), QPointF(s * 0.62, s * 0.72))
        p.drawLine(QPointF(s * 0.50, s * 0.52), QPointF(s * 0.50, s * 0.72))
        p.drawArc(QRectF(s * 0.34, s * 0.54, s * 0.32, s * 0.30), 0, 180 * 16)
    elif kind == "video":     # 视频（胶片框 + 播放三角）
        p.drawRoundedRect(QRectF(s * 0.14, s * 0.22, s * 0.72, s * 0.56),
                          s * 0.06, s * 0.06)
        p.drawLine(QPointF(s * 0.42, s * 0.36), QPointF(s * 0.62, s * 0.50))
        p.drawLine(QPointF(s * 0.62, s * 0.50), QPointF(s * 0.42, s * 0.64))
        p.drawLine(QPointF(s * 0.42, s * 0.64), QPointF(s * 0.42, s * 0.36))
    elif kind == "audio":     # 音频（扬声器锥体 + 声波）
        p.drawLine(QPointF(s * 0.24, s * 0.40), QPointF(s * 0.40, s * 0.40))
        p.drawLine(QPointF(s * 0.40, s * 0.40), QPointF(s * 0.52, s * 0.30))
        p.drawLine(QPointF(s * 0.52, s * 0.30), QPointF(s * 0.52, s * 0.70))
        p.drawLine(QPointF(s * 0.52, s * 0.70), QPointF(s * 0.40, s * 0.60))
        p.drawLine(QPointF(s * 0.40, s * 0.60), QPointF(s * 0.24, s * 0.60))
        p.drawLine(QPointF(s * 0.24, s * 0.60), QPointF(s * 0.24, s * 0.40))
        p.drawArc(QRectF(s * 0.56, s * 0.34, s * 0.22, s * 0.32), 0, 180 * 16)
        p.drawArc(QRectF(s * 0.62, s * 0.28, s * 0.22, s * 0.44), 0, 180 * 16)
    elif kind == "chart":       # 统计（坐标轴 + 三根柱，与 token/上下文统计入口一致）
        p.drawLine(QPointF(s * 0.18, s * 0.14), QPointF(s * 0.18, s * 0.84))
        p.drawLine(QPointF(s * 0.18, s * 0.84), QPointF(s * 0.88, s * 0.84))
        p.drawLine(QPointF(s * 0.36, s * 0.84), QPointF(s * 0.36, s * 0.62))
        p.drawLine(QPointF(s * 0.54, s * 0.84), QPointF(s * 0.54, s * 0.44))
        p.drawLine(QPointF(s * 0.72, s * 0.84), QPointF(s * 0.72, s * 0.26))
    elif kind == "magic":     # 魔法棒（提示词优化）：星号 + 弧形
        p.setPen(QPen(QColor(color), 1.5, cap=Qt.PenCapStyle.RoundCap,
                      join=Qt.PenJoinStyle.RoundJoin))
        # 星号主体：竖线 + 交叉斜线
        p.drawLine(QPointF(s * 0.50, s * 0.14), QPointF(s * 0.50, s * 0.38))
        p.drawLine(QPointF(s * 0.38, s * 0.26), QPointF(s * 0.62, s * 0.26))
        p.drawLine(QPointF(s * 0.40, s * 0.16), QPointF(s * 0.60, s * 0.36))
        p.drawLine(QPointF(s * 0.60, s * 0.16), QPointF(s * 0.40, s * 0.36))
        # 尾部把手
        p.drawLine(QPointF(s * 0.50, s * 0.38), QPointF(s * 0.50, s * 0.52))
        p.drawLine(QPointF(s * 0.50, s * 0.52), QPointF(s * 0.70, s * 0.70))
        # 星点：小圆点
        p.setBrush(QColor(color))
        p.drawEllipse(QPointF(s * 0.72, s * 0.28), s * 0.04, s * 0.04)
        p.drawEllipse(QPointF(s * 0.28, s * 0.72), s * 0.04, s * 0.04)
        p.drawEllipse(QPointF(s * 0.78, s * 0.56), s * 0.04, s * 0.04)
    elif kind == "clock":     # 时钟（回合耗时徽章）
        p.drawEllipse(QPointF(s * 0.5, s * 0.5), s * 0.36, s * 0.36)
        p.drawLine(QPointF(s * 0.5, s * 0.30), QPointF(s * 0.5, s * 0.52))
        p.drawLine(QPointF(s * 0.5, s * 0.52), QPointF(s * 0.66, s * 0.62))
    elif kind == "think":     # 灯泡（思考过程）：泡体 + 底部两道横线
        p.drawArc(QRectF(s * 0.26, s * 0.10, s * 0.48, s * 0.48), 0, 360 * 16)
        p.drawLine(QPointF(s * 0.36, s * 0.72), QPointF(s * 0.64, s * 0.72))
        p.drawLine(QPointF(s * 0.42, s * 0.86), QPointF(s * 0.58, s * 0.86))
    elif kind == "tool":      # 扳手（工具调用）
        p.drawLine(QPointF(s * 0.22, s * 0.78), QPointF(s * 0.58, s * 0.42))
        p.drawArc(QRectF(s * 0.50, s * 0.10, s * 0.40, s * 0.40), -30 * 16, 250 * 16)
        p.drawLine(QPointF(s * 0.22, s * 0.78), QPointF(s * 0.34, s * 0.66))
    elif kind == "term":      # 终端提示符（命令块）：右尖括号 + 下划线
        p.drawLine(QPointF(s * 0.22, s * 0.30), QPointF(s * 0.44, s * 0.50))
        p.drawLine(QPointF(s * 0.44, s * 0.50), QPointF(s * 0.22, s * 0.70))
        p.drawLine(QPointF(s * 0.54, s * 0.72), QPointF(s * 0.80, s * 0.72))
    elif kind == "chev":      # 下尖角（展开/收起指示）
        p.drawLine(QPointF(s * 0.26, s * 0.38), QPointF(s * 0.50, s * 0.62))
        p.drawLine(QPointF(s * 0.50, s * 0.62), QPointF(s * 0.74, s * 0.38))
    p.end()
    return QIcon(pm)


# _line_icon 已实现的 kind 集合：图标入口据此区分「已知线条图标」与「未登记 kind」，
# 未登记的一律走 tool_icons 兜底组合，避免图标壳出现空白（新增线条 kind 时同步补这里）。
_LINE_ICON_KINDS = frozenset({
    "send", "stop", "play", "pause", "prev", "next", "shuffle", "repeat",
    "retry", "vol", "music", "full", "trash", "new", "plus", "ok", "no",
    "drive", "list", "doc", "bash", "net", "folder", "apps", "user", "server",
    "puzzle", "mic", "video", "audio", "chart", "magic", "clock", "think",
    "tool", "term", "chev",
})


@lru_cache(maxsize=512)
def _file_icon(ext: str, size: int = 18, color: str = TEXT_DIM) -> QIcon:
    """文件类型矢量图标：文档轮廓 + 类型文字角标（py/java/js/go/cpp/c 等）。
    淡灰线条简约风格，与其它矢量图标统一，无 emoji。结果按 (ext, size, color) 缓存。"""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = float(size)
    pen = QPen(QColor(color), max(1.2, s * 0.09))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    # 文档轮廓（右上角折角）
    x0, y0, w, h = s * 0.16, s * 0.10, s * 0.68, s * 0.80
    path = QPainterPath()
    path.moveTo(x0 + w * 0.30, y0)
    path.lineTo(x0 + w, y0)
    path.lineTo(x0 + w, y0 + h)
    path.lineTo(x0, y0 + h)
    path.lineTo(x0, y0 + h * 0.30)
    path.lineTo(x0 + w * 0.30, y0)
    path.closeSubpath()
    p.drawPath(path)
    # 类型文字
    label = (ext or "file").upper()
    if len(label) > 4:
        label = label[:4]
    f = QFont()
    f.setBold(True)
    f.setPixelSize(int(s * 0.30))
    p.setFont(f)
    p.setPen(QPen(QColor(color), 1.0))
    p.drawText(QRectF(x0, y0, w, h), Qt.AlignmentFlag.AlignCenter, label)
    p.end()
    return QIcon(pm)


# 图片 / 视频 / 音频扩展名集合（工作树用现成缩略图）
_IMG_EXTS = frozenset({"png", "jpg", "jpeg", "gif", "webp", "bmp", "ico",
                       "svg", "avif", "jfif"})
_VIDEO_EXTS = frozenset({"mp4", "mkv", "mov", "avi", "flv", "webm", "wmv",
                         "m4v", "ts", "mpeg", "mpg"})
_AUDIO_EXTS = frozenset({"mp3", "wav", "flac", "ogg", "m4a", "aac", "wma",
                         "opus", "aiff"})
# 源码/文本类扩展名（用现成代码文档图标 + 类型角标）
_CODE_EXTS = frozenset({"py", "js", "ts", "jsx", "tsx", "java", "c", "cpp",
                        "h", "hpp", "go", "rs", "rb", "php", "swift", "kt",
                        "cs", "html", "css", "scss", "json", "xml", "yaml",
                        "yml", "toml", "md", "sql", "sh", "bat", "ps1"})


@lru_cache(maxsize=256)
def _file_thumb(path, ext: str, size: int = 18, color: str = TEXT_DIM) -> QIcon:
    """工作树文件图标：图片用真实文件内容缩略图；视频/音频用类型专属现成矢量图标；
    源码/其它类型用文档轮廓 + 类型角标。图片加载失败回退为类型图标。
    结果按 (path, size, color) 缓存：主题切换/工作树刷新时避免重复解码磁盘图片。"""
    if ext in _IMG_EXTS:
        pm = QPixmap(str(path))
        if not pm.isNull():
            return QIcon(pm.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation))
    if ext in _VIDEO_EXTS:
        return _line_icon("video", size, color)
    if ext in _AUDIO_EXTS:
        return _line_icon("audio", size, color)
    return _file_icon(ext, size, color)


def _esc(s: str) -> str:
    return _html.escape(str(s), quote=False)


def _collapse_blank(s: str) -> str:
    """去除文本首尾空行并把连续空行折叠为单个空行（保留段落分隔）。
    子 Agent 流式增量/命令输出常以多余换行结束——直接入 HTML 会 <br/> 成片，
    造成气泡底部大块空白与段落间距过大。"""
    s = str(s or "")
    lines = s.splitlines()
    out, blank = [], False
    for ln in lines:
        b = not ln.strip()
        if b and blank:
            continue
        blank = b
        out.append(ln)
    while out and not out[0].strip():
        out.pop(0)
    while out and not out[-1].strip():
        out.pop()
    return "\n".join(out)


# ---------- 欢迎页时段欢迎语（按本地小时整点区间，覆盖全天 24 小时） ----------
_WELCOME_GREETINGS = (
    (0, 6, "凌晨了，快睡吧！！！幸苦了一天喵喵喵！！！"),
    (7, 11, "早上好，新的一天开始了，喵~"),
    (12, 14, "中午了，有点困了，喵~"),
    (15, 19, "下午好，继续努力！！！"),
    (20, 23, "瞌睡了，但bug还没修完！！！"),
)


def _welcome_greeting(now=None) -> str:
    """按本地时间返回欢迎页欢迎语（默认取当前时间，可注入 datetime 便于测试）"""
    hour = (now or datetime.datetime.now()).hour
    for start, end, text in _WELCOME_GREETINGS:
        if start <= hour <= end:
            return text
    return _WELCOME_GREETINGS[0][2]   # 兜底：正常覆盖全天，极端情况回凌晨


def _warn_box(parent, title: str, text: str):
    """白色字体的警告弹窗：深色主题下 QMessageBox 默认文字可能不可读"""
    mb = QMessageBox(parent)
    mb.setIcon(QMessageBox.Icon.Warning)
    mb.setWindowTitle(title)
    mb.setText(text)
    mb.setStyleSheet(
        f"QMessageBox {{ background: {PANEL}; }}"
        f"QLabel {{ color: {TEXT}; font-size: 13px; }}"
        f"QPushButton {{ color: {TEXT}; background: {PANEL};"
        f"border: 1px solid {BORDER}; border-radius: 6px;"
        f"padding: 6px 16px; min-width: 64px; }}"
        f"QPushButton:hover {{ border-color: {ACCENT_HOVER}; }}")
    mb.exec()


# ---------- 轻量 Markdown → HTML 渲染 ----------
# 数学公式（轻量 LaTeX → Unicode/HTML，零额外依赖）
_GREEK = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε",
    "zeta": "ζ", "eta": "η", "theta": "θ", "iota": "ι", "kappa": "κ",
    "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ", "pi": "π", "rho": "ρ",
    "sigma": "σ", "tau": "τ", "upsilon": "υ", "phi": "φ", "chi": "χ",
    "psi": "ψ", "omega": "ω",
    "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ",
    "Pi": "Π", "Sigma": "Σ", "Upsilon": "Υ", "Phi": "Φ", "Psi": "Ψ",
    "Omega": "Ω",
}
_MATH_SYMBOLS = {
    "times": "×", "div": "÷", "pm": "±", "cdot": "·", "le": "≤", "leq": "≤",
    "ge": "≥", "geq": "≥", "neq": "≠", "approx": "≈", "equiv": "≡",
    "rightarrow": "→", "leftarrow": "←", "leftrightarrow": "↔", "infty": "∞",
    "sum": "∑", "int": "∫", "prod": "∏", "partial": "∂", "forall": "∀",
    "exists": "∃", "in": "∈", "notin": "∉", "subset": "⊂", "subseteq": "⊆",
    "cup": "∪", "cap": "∩", "emptyset": "∅", "nabla": "∇", "dots": "…",
    "cdots": "⋯", "ldots": "…", "to": "→", "ast": "∗", "propto": "∝",
    "angle": "∠", "perp": "⊥", "parallel": "∥", "therefore": "∴",
    "because": "∵", "mod": " mod ", "circ": "∘", "deg": "°",
}


def _formula_to_html(s: str) -> str:
    """轻量 LaTeX 公式 → HTML：希腊字母/符号/分数/根号/上下标"""
    s = s.strip()
    # 去掉排版命令（\left \right \displaystyle \quad 等）
    s = re.sub(r"\\(?:left|right|displaystyle|textstyle|quad|qquad|,|;|!|:)\b", "", s)
    # 分数 → (分子)/(分母)（参数可为含花括号的表达式，非贪婪逐层处理）
    while True:
        m = re.search(r"\\frac\{(.+?)\}\{(.+?)\}", s)
        if not m:
            break
        s = s[:m.start()] + f"({m.group(1)})/({m.group(2)})" + s[m.end():]
    # 根号：\sqrt[n]{x} → n√(x)；\sqrt{x} → √(x)
    s = re.sub(r"\\sqrt\[([^{}]*)\]\{(.+?)\}",
               lambda m: f"{m.group(1)}√({m.group(2)})", s)
    s = re.sub(r"\\sqrt\{(.+?)\}", r"√(\1)", s)
    # 上下标：^{...} _{...} ^x _x
    s = re.sub(r"\^\{([^{}]*)\}", r"<sup>\1</sup>", s)
    s = re.sub(r"_\{([^{}]*)\}", r"<sub>\1</sub>", s)
    s = re.sub(r"\^([a-zA-Z0-9])", r"<sup>\1</sup>", s)
    s = re.sub(r"_([a-zA-Z0-9])", r"<sub>\1</sub>", s)
    # 命名符号：\pi → π、\times → ×、\text{...} → 原文
    s = re.sub(r"\\text\{([^{}]*)\}", r"\1", s)

    def _sym(m):
        name = m.group(1)
        return _GREEK.get(name) or _MATH_SYMBOLS.get(name) or m.group(0)
    s = re.sub(r"\\([a-zA-Z]+)", _sym, s)
    return s


def _linkify(s: str) -> str:
    """把（已转义的）文本中的裸 URL / Windows 路径转为蓝色可点击链接。
    文件路径统一用 file:/// 前缀，便于 _on_bubble_link 识别后 os.startfile 打开。"""
    pattern = re.compile(
        r"(?<![\"'\w])("
        r"https?://[^\s<>\"']+|"          # 裸 URL
        r"[A-Za-z]:[\\/][^\s<>\"']*|"     # 盘符绝对路径 C:\... / C:/...
        r"\\\\[^\s<>\"']*"                # UNC 路径 \\server\share
        r")")
    # URL 尾部非法字符（全角标点、中文文本等），ASCII URL 字符白名单
    _tail = re.compile(r"[^A-Za-z0-9/_\-?=&.%#:@+~]+$")

    def _to(m):
        token = m.group(1)
        if token.startswith(("http://", "https://")):
            token = _tail.sub("", token)
            href = token
        elif token.startswith("file://"):
            href = token
        else:
            token = token.rstrip(".,;:!)]}，。；：！？】\"'")
            href = "file:///" + token.replace("\\", "/")
        return (f'<a href="{href}" style="color:{LINK_COLOR};'
                f'text-decoration:underline;">{token}</a>')
    return pattern.sub(_to, s)


def _inline_md(s: str) -> str:
    """行内样式：数学公式 $...$、`code`、**bold**、[text](url)、裸 URL/文件路径"""
    # 先提取行内公式为占位符，避免被转义/加粗等逻辑破坏
    formulas = {}

    def _cap(m):
        idx = f"\x00F{len(formulas)}\x00"
        formulas[idx] = _formula_to_html(m.group(1))
        return idx
    s = re.sub(r"\$([^$\n]+)\$", _cap, s)
    s = _esc(s)
    for idx, html in formulas.items():
        s = s.replace(idx, html)
    s = re.sub(r"`([^`]+)`",
               f"<code style='background:{CODE_BG};color:{CODE_ACCENT};padding:1px 5px;"
               "border-radius:4px;font-family:Consolas;'>\\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", s)
    # [text](url) 先占位，避免其 href/文字被 _linkify 二次加工
    links = {}

    def _cap_link(m):
        idx = f"\x00L{len(links)}\x00"
        links[idx] = (f'<a href="{m.group(2)}" style="color:{LINK_COLOR};'
                      f'text-decoration:underline;">{m.group(1)}</a>')
        return idx
    s = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", _cap_link, s)
    s = _linkify(s)
    for idx, html in links.items():
        s = s.replace(idx, html)
    return s


def _md_table_to_html(lines: list) -> str:
    """Markdown 表格行列表 → HTML 表格；非表格（无分隔行）返回空串"""
    rows = []
    for line in lines:
        body = line.strip()
        if body.startswith("|"):
            body = body[1:]
        if body.endswith("|"):
            body = body[:-1]
        rows.append([c.strip() for c in body.split("|")])
    if len(rows) < 2:
        return ""
    sep = rows[1]
    if not all(re.match(r"^:?-+:?$", c) for c in sep):
        return ""
    ncol = max(len(rows[0]), len(sep), max(len(r) for r in rows[2:])) if rows[2:] else \
        max(len(rows[0]), len(sep))
    th = (f"border:1px solid {BORDER_SOFT};padding:4px 8px;background:{CARD};"
          f"color:{TEXT};font-weight:600;")
    td = f"border:1px solid {BORDER_SOFT};padding:4px 8px;color:{TEXT_DIM};"
    out = ["<table style='border-collapse:collapse;margin:6px 0;font-size:13px;'>"]
    out.append("<tr>")
    for c in range(ncol):
        out.append(f"<th style='{th}'>{_inline_md(rows[0][c] if c < len(rows[0]) else '')}</th>")
    out.append("</tr>")
    for r in rows[2:]:
        out.append("<tr>")
        for c in range(ncol):
            out.append(f"<td style='{td}'>{_inline_md(r[c] if c < len(r) else '')}</td>")
        out.append("</tr>")
    out.append("</table>")
    return "".join(out)


def _md_to_html(raw: str) -> str:
    """块级 Markdown → HTML：代码块、标题、列表、段落、表格"""
    lines = raw.split("\n")
    out = []
    in_code = False
    in_list = False
    code_buf = []
    i, n = 0, len(lines)
    while i < n:
        s = lines[i].strip()
        if s.startswith("```"):
            if in_code:
                out.append(f"<pre style='background:{CODE_BG};color:{TEXT};padding:8px;"
                           "border-radius:6px;font-family:Consolas;font-size:12px;"
                           f"border:1px solid {BORDER_SOFT};'>" + _esc("\n".join(code_buf)) + "</pre>")
                code_buf = []
                in_code = False
            else:
                in_code = True
            i += 1
            continue
        if in_code:
            code_buf.append(lines[i])
            i += 1
            continue
        if not s:
            if in_list:
                out.append("</ul>")
                in_list = False
            i += 1
            continue
        # Markdown 表格：| 单元格 | ... + 分隔行 + 数据行
        if s.startswith("|") and s.count("|") >= 3:
            tbl = [s]
            i += 1
            while i < n and lines[i].strip().startswith("|"):
                tbl.append(lines[i].strip())
                i += 1
            table = _md_table_to_html(tbl)
            if table:
                if in_list:
                    out.append("</ul>")
                    in_list = False
                out.append(table)
            else:   # 不是表格，按普通段落逐行渲染
                if in_list:
                    out.append("</ul>")
                    in_list = False
                for ln in tbl:
                    out.append("<p style='margin:4px 0;'>" + _inline_md(ln) + "</p>")
            continue
        # 块级数学公式 $$...$$（单行）→ 居中展示
        m = re.match(r"^\$\$(.+)\$\$\s*$", s)
        if m:
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append(f"<div style='text-align:center;margin:8px 0;"
                       f"font-family:Georgia,'Times New Roman',serif;font-size:16px;"
                       f"color:{TEXT};'>{_formula_to_html(m.group(1))}</div>")
            i += 1
            continue
        # 水平分隔线：--- / *** / ___
        m = re.match(r"^(-{3,}|\*{3,}|_{3,})\s*$", s)
        if m:
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append(f"<hr style='border:none;border-top:1px solid {BORDER_SOFT};margin:8px 0;'>")
            i += 1
            continue
        # 引用块：> ...（连续多行合并渲染）
        if s.startswith(">"):
            if in_list:
                out.append("</ul>")
                in_list = False
            quote = [s[1:].strip()]
            i += 1
            while i < n and lines[i].strip().startswith(">"):
                quote.append(lines[i].strip()[1:].strip())
                i += 1
            out.append(f"<blockquote style='margin:6px 0;padding:2px 12px;"
                       f"border-left:3px solid {ACCENT};color:{TEXT_DIM};'>"
                       f"{_inline_md(' '.join(quote))}</blockquote>")
            continue
        m = re.match(r"^(#{1,6})\s+(.*)", s)
        if m:
            if in_list:
                out.append("</ul>")
                in_list = False
            lvl = len(m.group(1))
            out.append(f"<h{lvl} style='margin:8px 0 4px;color:{TEXT};"
                       f"font-size:{max(13, 20 - lvl)}px;'>{_inline_md(m.group(2))}</h{lvl}>")
            i += 1
            continue
        if re.match(r"^[-*+]\s+", s) or re.match(r"^\d+[.)]\s+", s):
            if not in_list:
                out.append("<ul style='margin:4px 0;padding-left:18px;'>")
                in_list = True
            item = re.sub(r"^[-*+]\s+|^\d+[.)]\s+", "", s)
            out.append("<li>" + _inline_md(item) + "</li>")
            i += 1
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        out.append("<p style='margin:4px 0;'>" + _inline_md(s) + "</p>")
        i += 1
    if in_code:
        out.append(f"<pre style='background:{CODE_BG};color:{TEXT};padding:8px;"
                   "border-radius:6px;font-family:Consolas;font-size:12px;"
                   f"border:1px solid {BORDER_SOFT};'>" + _esc("\n".join(code_buf)) + "</pre>")
    if in_list:
        out.append("</ul>")
    return "".join(out)


def _render_text(raw: str) -> str:
    """AI 正文渲染：Markdown 解析；代码块未闭合（``` 为奇数）时自动补全闭合，
    避免整段退化为纯文本导致标题/加粗/列表等全部无法解析。"""
    if raw.count("```") % 2 == 1:
        raw = raw + "\n```"
    return _md_to_html(raw)


def _split_md_blocks(raw: str) -> list:
    """把 markdown 文本切成渲染块：代码块整体为一块（可跨空行），
    其余以空行分界。流式输出时只有尾部块会增长，前面的块内容恒定 →
    可按块内容做增量渲染缓存（key 即块字符串）。"""
    blocks, buf, in_code = [], [], False
    for line in raw.split("\n"):
        if in_code:
            buf.append(line)
            if line.strip().startswith("```"):
                blocks.append("\n".join(buf))
                buf, in_code = [], False
            continue
        if line.strip().startswith("```"):
            if buf:
                blocks.append("\n".join(buf))
                buf = []
            buf.append(line)
            in_code = True
            continue
        if not line.strip():
            if buf:
                blocks.append("\n".join(buf))
                buf = []
            continue
        buf.append(line)
    if buf:
        blocks.append("\n".join(buf))
    return blocks


def _render_text_incr(raw: str, cache: dict) -> str:
    """增量渲染：按块缓存已渲染 HTML，只重算增长的尾部块。
    长文本流式时避免每次对整段全量 markdown 重渲染（单次 O(n)、累计 O(n²) 卡顿）。
    尾部未闭合代码块（``` 为奇数）仅补闭合渲染该块，完整块照常缓存，无需整段重渲染。
    缓存键含 _THEME_VERSION：主题切换后整批缓存自动 miss，代码块/表格等随新色板重渲染，
    即使 _rd_cache 未被显式清空也不会残留旧主题色。"""
    blocks = _split_md_blocks(raw)
    unclosed_code = raw.count("```") % 2 == 1
    last = len(blocks) - 1
    parts = []
    for idx, b in enumerate(blocks):
        key = (b, _THEME_VERSION)
        if idx == last and unclosed_code and b.lstrip().startswith("```"):
            key = (b + "\n```", _THEME_VERSION)   # 未闭合代码块补闭合后渲染（输出与完整闭合一致）
        h = cache.get(key)
        if h is None:
            h = _render_text(key[0])
            cache[key] = h
            if len(cache) > 80:   # 块缓存容量上限（长文本单段缓存 HTML 占内存，按插入序保留最近 40 项）
                for _k in list(cache)[: len(cache) - 40]:
                    del cache[_k]
        parts.append(h)
    return "".join(parts)


def _seg_full_len(seg: dict):
    """流式段的「全文长度」（已到达的字符数）；非流式段返回 None。

    只有 text / think 两种段参与「落字节奏」，其余段（工具/命令/结果/截图…）一次性成型。
    """
    t = seg.get("type")
    if t == "text":
        return len(seg.get("raw", ""))
    if t == "think":
        return len(seg.get("html", ""))
    return None


def _shown_len(seg: dict) -> int:
    """段当前**应显示**的字符数：未走落字节奏（历史回放/后台缓冲）的段 = 全文。

    渲染与签名都只看这个前缀：模型一次吐 30~200 字，直接整段塞进气泡的观感是「一跳一大段」，
    按节奏逐帧推进前缀才是连续的落字（配合浮现动效即「一个字一个字丝滑浮现」）。
    """
    full = _seg_full_len(seg)
    if full is None:
        return 0
    n = seg.get("_shown")
    return full if n is None else max(0, min(int(n), full))


def _strip_seg_render_cache(seg):
    """剥离段 dict 中的内部键，**返回副本**（绝不改动入参）。

    用在哪：会话落盘前必须剥掉渲染用内部键（`_rd_cache` 的键是元组、json 无法序列化），
    加载后按需重建；`_seg_cache` 存在面板层不随段保存，故统一剥离所有下划线前缀内部键兜底。

    为什么必须返回副本：实时段里还挂着**流式落字进度** `_shown`。落盘（每 4 秒的任务中自动
    备份、切会话、发送前提交、退出兜底）走的是与内存态**同一批 dict 对象**，若就地剥离，
    落字进度会被清掉，下一次 `_reveal_note` 把它重新初始化为 0 → 已经显示出来的内容从头
    重播一遍；长思考进入折叠态后可见的就是前几行，用户看到的就是「思考气泡重复播放前五行」。
    """
    if not isinstance(seg, dict):
        return seg
    return {k: v for k, v in seg.items() if not k.startswith("_")}


def _segs_same(a: list, b: list) -> bool:
    """两组 AI 段是否为同一轮回复（内容指纹：type + 文本字段）。

    用于 _write_ui_json 判断「rows 末尾的 AI 行是否就是当前未归档段 ds」：
    strip 渲染缓存后 seg 对象被替换（引用失效），不能 is 比较，必须按内容判等。
    """
    a = a or []
    b = b or []
    if len(a) != len(b):
        return False

    def _fp(seg):
        if not isinstance(seg, dict):
            return None
        return (seg.get("type"),
                seg.get("raw") or seg.get("html") or seg.get("url") or seg.get("title"))

    for x, y in zip(a, b):
        if _fp(x) != _fp(y):
            return False
    return True


# 单段渲染字符上限：单段 HTML 最终进入一个独立区块控件（demo .cmd / .think-bubble 等），
# 超长的命令输出 / 子 Agent 输出按此截断展示，保证「单块体积有界」——长对话里一个数
# 百 KB 的块会让该控件的富文本布局变慢，进而拖累滚动与缩放。
_RESULT_TRUNCATE = 6000

# 流式输出节拍（毫秒）：4ms ≈ 240fps。段级增量渲染 + 高度测量缓存 + 气泡性能优化后，
# 单帧渲染成本亚毫秒~11ms，高帧节拍不再排满主线程。dirty 防抖把期间 token 增量合并为一次渲染，
# 实际帧率受单帧渲染时间约束（约60~90fps），但帧间隔更短、落字更连贯。正文超大时放宽到 60ms 兜底。
# 滚动跟随同频，跟手且不做无谓的超高频重绘。
_STREAM_TICK_MS = 4
_SCROLL_TICK_MS = 4
_STREAM_BIG_CHARS = 200_000
# 长任务（回合内区块多）的内容节拍上限：每 tick 成本 ∝ 区块数，4ms 节拍在几十个区块的
# 回合里等于把主线程排满 —— 「气泡样式一多就卡」的乘数就在这里。放宽到 16ms（60fps，
# 与屏幕刷新对齐）可省约 4 倍主线程；落字连贯由浮现层（EMERGE_TICK_MS≈125fps 合成，
# 与内容节拍解耦）保证，观感不会变生硬。
_STREAM_TICK_LONG_MS = 16
_STREAM_TICK_BLOCKS = 12      # 区块数达到该值即视为长任务
# 流式落字节奏（reveal pacing，见 AgentPanel._reveal_tick）：模型是**成批**吐字的
# （一次 10~200 字），把最新文本直接塞进气泡的观感就是「一跳一大段」—— 这是「文字输出
# 太快」的真实成因（平均速度由模型决定，界面能做的是把「批」摊成连续落字）。
# 规则：以基础速度落字；积压超过 _REVEAL_MAX_LAG_S 就按积压量加速，所以滞后有上界，
# 不会长期落后于模型，收尾时最多补一小段。
_REVEAL_CPS = 55             # 基础落字速度（字/秒）。**必须低于模型的到达速度**（中文流式常见
                             # 30~60 字/秒）才有丝滑感：只要落字比到达慢，队列永远不空、每拍
                             # 都有字可落（连续）；反过来（如 90）会「攒够一批→一口气落完→
                             # 空等下一批」，每个断点都是一次可见的急停 —— 用户感知的
                             # 「速度太快」多半来自这种一顿一顿，而非平均速度
_REVEAL_MAX_LAG_S = 0.90     # 允许的最大滞后（批越大越按此摊平，观感越连续）
_REVEAL_CAP_CPS = 280        # 追赶速度上限。单拍步长 = CAP × dt（dt 上限 0.12s）≈ 34 字，
                             # 再高就会出现「一帧跳一大段」的跳变（600 时单帧可跳 72 字）

# 子 Agent 块展开态的体积上限：一个子块由多步工具输出拼成，实测 7 个子块可占
# 629KB 富文本的 94%（单块富文本布局 200~500ms → 任何交互都卡）。
# 展开态只渲染最近 N 步、单步与总结各自截断，保证单块体积有界。
_SUB_OPEN_MAX_STEPS = 24      # 展开时最多渲染的步骤数（取最近 N 步）
_SUB_STEP_TRUNCATE = 1200     # 展开时单步输出截断（字符）
_SUB_RAW_TRUNCATE = 2400      # 展开时子 Agent 总结文本截断（字符）


# ---------- AI 输入 / 提问构造 helper（模块级，前台渲染与后台缓冲共用） ----------

def _cmd_text_from_args(args_json: str) -> str:
    """从工具 args（JSON 串）提取要陈列的命令全文；非 run_command / 无命令返回空串"""
    try:
        args = json.loads(args_json or "{}")
    except Exception:
        return ""
    if not isinstance(args, dict):
        return ""
    return str(args.get("command") or args.get("cmd") or "").strip()


def _remember_cmd(st: dict, args_json: str):
    """记录引擎即将执行的 run_command 全文到会话状态（仅当前一次）：
    执行结果(_on_result)到达时把命令拼接到输出前面一并渲染（与 ask_user 的
    「提问 + 用户回答」连续展示同构），命令不单独成卡。非 run_command/空命令不记录。"""
    cmd = _cmd_text_from_args(args_json)
    if cmd:
        st["last_cmd"] = cmd


def _cmd_html(cmd) -> str:
    """命令全文 → 命令块内层 HTML：转义 + 换行转 <br/>，超长按 _RESULT_TRUNCATE 截断。

    demo 的 .cmd .in 直接展示单行命令；这里的命令可能含换行与 shell 特殊字符，
    必须转义后再入富文本，否则会被当作标签解析（命令内容本身就是最需要如实展示的）。"""
    raw = _esc(str(cmd or "")).replace("\n", "<br/>")
    if len(raw) > _RESULT_TRUNCATE:
        raw = raw[:_RESULT_TRUNCATE] + "…"
    return raw


def _hhmmss(ts: float) -> str:
    """时间戳 → `HH:MM:SS`（回合工具行的「开始 → 结束」区间文案，与 demo .sys-meta 同构）"""
    try:
        return datetime.datetime.fromtimestamp(float(ts)).strftime("%H:%M:%S")
    except Exception:
        return ""


# ---------- 打字指示器 / 状态文案（当前动作 → 状态描述） ----------
# 转圈行随 AI 当前动作实时切换文案：工具调用走 _op_status_text(工具名)，
# 正文回复走 _OP_STATUS_REPLY。未使用 emoji；渲染为淡灰小字（TEXT_DIM）。
#
# 查找顺序（三层，便于扩展）：精确映射 _OP_STATUS → 族兜底（前缀/关键词）
# → 「正在调用工具 <名>」。新增工具即便没登记精确文案，也会落到所属族的合理
# 描述（如新增 browser_xxx 自动是「正在操作浏览器」），因此无需为每个新工具改表。
_OP_STATUS = {
    # ---- 文件读写 ----
    "write_file": "正在写入文件",
    "edit_file": "正在编辑文件",
    "search_replace": "正在编辑文件",
    "insert_lines": "正在向文件插入内容",
    "delete_file": "正在删除文件",
    "undo_file": "正在回滚文件",
    "read_file": "正在读取文件",
    "list_directory": "正在浏览目录",
    "new_project": "正在创建项目",
    "extract_text": "正在提取文档内容",
    # ---- 文件搜索（按名 / 按内容 / 代码 / 大文件） ----
    "search_files": "正在搜索文件",
    "search_large": "正在搜索大文件",
    "grep": "正在检索内容",
    "search_code": "正在检索代码",
    "find_app": "正在查找应用",
    "explore_project": "正在扫描项目",
    # ---- 命令（执行 / 轮询进度） ----
    "run_command": "正在执行命令",
    "check_command": "正在轮询命令进度",
    # ---- 网页 ----
    "web_search": "正在联网搜索",
    "web_fetch": "正在抓取网页",
    "fast_download": "正在下载文件",
    # ---- 浏览器操作 ----
    "browser_open": "正在打开浏览器",
    "browser_navigate": "正在打开网页",
    "browser_snapshot": "正在截取页面",
    "browser_click": "正在点击页面",
    "browser_type": "正在输入文本",
    "browser_scroll": "正在滚动页面",
    "browser_eval": "正在执行页面脚本",
    "browser_html": "正在读取页面源码",
    "browser_tabs": "正在读取标签页",
    "browser_switch_tab": "正在切换标签页",
    "browser_close": "正在关闭浏览器",
    # ---- 文档 / 媒体产出 ----
    "create_docx": "正在生成 Word 文档",
    "create_pptx": "正在生成 PPT",
    "create_xlsx": "正在生成 Excel 表格",
    "beautify_docx": "正在美化 Word 文档",
    "beautify_pptx": "正在美化 PPT",
    "beautify_xlsx": "正在美化 Excel 表格",
    "read_docx": "正在读取 Word 文档",
    "read_pptx": "正在读取 PPT",
    "read_xlsx": "正在读取 Excel 表格",
    "read_pdf": "正在读取 PDF",
    "edit_docx": "正在编辑 Word 文档",
    "edit_pptx": "正在编辑 PPT",
    "edit_xlsx": "正在编辑 Excel 表格",
    "generate_image": "正在生成图片",
    # ---- 时间 / 系统 ----
    "get_time": "正在读取时间",
    "system_info": "正在读取系统信息",
    "env_var": "正在读取环境变量",
    "git_info": "正在查询仓库状态",
    "optimize_memory": "正在优化内存",
    "clipboard": "正在读写剪贴板",
    # ---- 任务清单 / 记忆 ----
    "update_todo": "正在更新任务清单",
    "list_todo": "正在读取任务清单",
    "save_memory": "正在保存记忆",
    "load_memory": "正在读取记忆",
    # ---- 应用 / 交互 ----
    "migrate_app": "正在迁移应用",
    "uninstall_app": "正在卸载应用",
    "ask_user": "正在向你提问",
    # ---- 扩展能力 ----
    "create_skill": "正在创建技能",
    "create_plugin": "正在创建插件",
    "create_workflow": "正在创建工作流",
    "register_sub_agent": "正在注册子 Agent",
    "dispatch_sub_agents": "正在派发子 Agent",
    "manage_uiux": "正在处理界面定制",
    "set_session_name": "正在命名对话",
    "look_context": "正在读取共享上下文",
    "chat_with": "正在与 Agent 通信",
    "preview_open": "正在你的浏览器打开预览",
    "preview_refresh": "正在刷新浏览器预览",
}

# 正文回复（非工具动作）的状态文案：AI 输出普通正文期间转圈行显示此项
_OP_STATUS_REPLY = "正在回复正文"

# 族兜底①：工具名前缀 → 状态文案（先匹配，比关键词更具体）
_OP_FAMILY_PREFIX = (
    ("browser", "正在操作浏览器"),
    ("tts_", "正在处理语音"),
    ("register_", "正在注册能力"),
    ("list_", "正在读取清单"),
    ("inspect_", "正在检查配置"),
    ("set_", "正在更新配置"),
    ("create_", "正在创建"),
    ("delete_", "正在删除"),
)
# 族兜底②：工具名含关键词 → 状态文案（前缀未命中时兜底）
_OP_FAMILY_KEYWORD = (
    ("workflow", "正在处理工作流"),
    ("agent", "正在处理 Agent 协作"),
    ("mcp", "正在处理 MCP 服务"),
    ("context", "正在处理共享上下文"),
)


def _op_status_text(name: str) -> str:
    """工具名 → 打字指示器状态文案。

    三层查找：精确映射 → 族兜底（前缀→关键词）→ 「正在调用工具 <名>」。
    新增工具未登记精确文案时自动落到所属族的描述，避免生硬回退。
    """
    n = str(name or "")
    hit = _OP_STATUS.get(n)
    if hit:
        return hit
    low = n.casefold()
    for prefix, text in _OP_FAMILY_PREFIX:
        if low.startswith(prefix):
            return text
    for kw, text in _OP_FAMILY_KEYWORD:
        if kw in low:
            return text
    return f"正在调用工具 {n}"





def _seg_sig(seg: dict) -> tuple:
    """段内容签名（用于渲染缓存命中判断）：只取能影响渲染结果的字段，
    流式追加只改尾部 -> O(1) 签名，避免每 tick 对全段做昂贵重渲染"""
    t = seg["type"]
    if t == "text":
        raw = seg["raw"]
        # 含 streaming 标志：流式结束（streaming 置 False）后签名变化 → 缓存 miss →
        # 重新走完整 markdown 渲染补全格式（否则缓存一直返回流式轻量渲染，md 永不解析）。
        # 长度/tail 都只取「已显示前缀」：落字节奏推进前缀 → 签名随之变化触发重渲染；
        # 尚未显示的尾字不进签名（进了也只是白白重算）。
        n = _shown_len(seg)
        return (t, bool(seg.get("streaming")), n, raw[max(0, n - 64):n])
    if t == "think":
        h = seg.get("html", "")
        n = _shown_len(seg)
        return (t, bool(seg.get("collapsed")), n, h[max(0, n - 64):n])
    if t == "result":
        # cmd（拼接在输出前的命令全文）参与签名：不同命令不可命中同缓存
        return (t, bool(seg.get("collapsed")), seg["html"], seg.get("cmd") or "")
    if t == "progress":
        return (t, seg.get("pct"), seg.get("text"))
    if t == "image":
        return (t, seg.get("url"))
    if t == "sub":
        # 段签名须覆盖 steps（工具/输出步骤）：否则子 Agent 持续追加步骤时签名不变，
        # 缓存一直返回旧 HTML → 步骤永远不渲染、气泡留大片空白。
        # collapsed 参与签名：折叠/展开切换后必须重渲染（与 think/result 一致），
        # 缺省视为折叠（老数据没有该字段，默认折叠才不会撑大历史气泡）。
        # paused 参与签名：点击「暂停/恢复」后必须重渲染（否则状态标记永远不出现，
        # 用户看到的是「点了没反应」）。
        raw = seg.get("raw", "")
        steps = seg.get("steps") or []
        return (t, bool(seg.get("collapsed", True)), bool(seg.get("paused")),
                seg.get("title"), len(raw), raw[-64:],
                len(steps), tuple((s.get("kind"), str(s.get("text") or "")[-96:])
                                  for s in steps[-8:]))
    if t == "op":
        # cmd（AI 输入的命令全文）在工具执行确认后才填充，纳入签名确保填充后重渲染；
        # out（该工具/命令的输出，就地续写在本段）同样纳入 —— 输出到达即在本行下方展开；
        # name/meta/ico 亦纳入：技能行从「调用技能」翻转为「已调用技能」需触发重渲染；
        # tip 同样纳入：图标气泡提示（工具用途/插件来源）异步判定完成后要能刷到界面上。
        return (t, seg["html"], seg.get("cmd") or "", seg.get("out") or "",
                seg.get("name") or "", seg.get("meta") or "", seg.get("ico") or "",
                seg.get("tip") or "")
    if t == "mark":
        return (t, seg["html"])
    if t == "ask":
        # AI 提问段：q/options 弹窗前确定，answer 作答后回填 → 均纳入签名。
        # options 列表需归一为 tuple，签名元组必须整体可哈希（否则 _seg_cache.get 抛
        # TypeError: unhashable type: 'list'）
        opts = seg.get("options") or []
        return (t, seg.get("q", ""), tuple(opts) if isinstance(opts, list) else opts,
                seg.get("multi"), seg.get("answer", ""), bool(seg.get("answered")))
    return (t,)


class _TypingDots(QWidget):
    """任务执行中 AI 气泡下方的打字指示器动画（iMessage 风格：三点依次弹起，
    相位错开 1/3 循环，随消息流滚动，无 emoji）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(46, 16)
        self._phase = 0.0      # 循环相位 0→1（每点激活时刻错开 1/3）
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(35)

    def _tick(self):
        self._phase += 0.045
        if self._phase >= 1.0:
            self._phase -= 1.0
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(ACCENT))
        for i in range(3):
            # 相位错开 1/3：三点从左到右依次"弹起放大再回落"，形成打字节奏
            t = (self._phase - i / 3.0) % 1.0
            amp = max(0.0, math.sin(t * math.pi))   # t=0.5 时最大
            r = 2.2 + 2.8 * amp                     # 半径随节奏放大
            p.setOpacity((90 + 165 * amp) / 255)    # 同步淡入淡出
            p.drawEllipse(QPointF(5 + i * 13, 8), r, r)
        p.setOpacity(1.0)
        p.end()


# 写入类操作：确认弹窗隐藏具体内容，仅提示目标
_HIDE_CONTENT_TOOLS = {"write_file", "edit_file", "save_memory", "insert_lines"}


class _MultiLineInputDialog(QDialog):
    """多行文本输入弹窗（自定义插件/工作流等场景）。

    替代 QInputDialog.getMultiLineText：其打开时会把预填内容整体选中，用户一按回车，
    整段预填内容即被替换为换行符，表现为"输入框无法换行/内容被清空"。
    本弹窗不自动全选、光标置于文末：回车=插入换行，Ctrl+回车=确定，Esc=取消。
    """

    def __init__(self, title: str, label: str, initial: str = "",
                 wrap: bool = True, parent=None, min_size=(560, 340)):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(*min_size)
        self.setModal(True)
        _glass = agent_ui_ux.is_custom_package_active()
        if _glass:
            _dlg_bg = ("qlineargradient(x1:0,y1:0,x2:0,y2:1,"
                       "stop:0 rgba(248,250,253,190), stop:0.5 rgba(240,243,249,172), "
                       "stop:1 rgba(246,248,252,182))")
            _in_bg = ("qlineargradient(x1:0,y1:0,x2:0,y2:1,"
                      "stop:0 rgba(255,255,255,44), stop:0.5 rgba(255,255,255,16), "
                      "stop:1 rgba(255,255,255,32))")
            _bd = "rgba(255,255,255,96)"
            _hover = ("qlineargradient(x1:0,y1:0,x2:0,y2:1,"
                      "stop:0 rgba(255,255,255,70), stop:0.5 rgba(255,255,255,26), "
                      "stop:1 rgba(255,255,255,48))")
        else:
            _dlg_bg = PANEL
            _in_bg = PANEL
            _bd = BORDER
            _hover = PANEL
        self.setStyleSheet(f"QDialog {{ background: {_dlg_bg}; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)
        if label:
            lbl = QLabel(label)
            lbl.setStyleSheet(f"color: {TEXT_DIM}; font-size: 12px;")
            lbl.setWordWrap(True)
            lay.addWidget(lbl)
        self.edit = QPlainTextEdit()
        self.edit.setLineWrapMode(
            QPlainTextEdit.LineWrapMode.NoWrap if not wrap
            else QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.edit.setPlainText(initial)
        # 关键：打开后不自动全选，光标移到文末；回车=在末尾追加换行而非清空内容
        cur = self.edit.textCursor()
        cur.clearSelection()
        cur.movePosition(QTextCursor.MoveOperation.End)
        self.edit.setTextCursor(cur)
        self.edit.setStyleSheet(
            f"QPlainTextEdit {{ background: {_in_bg}; color: {TEXT};"
            f"border: 1px solid {_bd}; border-radius: 8px; padding: 8px;"
            f"font-size: 13px; font-family: Consolas, 'Microsoft YaHei'; }}"
            f"QPlainTextEdit:focus {{ border: 1px solid {ACCENT};"
            f" background: {_hover}; }}")
        lay.addWidget(self.edit, 1)
        hint = QLabel("回车 = 换行｜Ctrl + 回车 = 确定｜Esc = 取消")
        hint.setStyleSheet(f"color: {TEXT_DIM}; font-size: {FONT_CAPTION}px;")
        lay.addWidget(hint)
        btns = QHBoxLayout()
        # 「重新查看新手指南」入口：文字链接样式，靠左
        guide = QPushButton("重新查看新手指南")
        guide.setStyleSheet(f"background: transparent; color: {self._DIM}; border: none;"
                            "font-size: 12px; text-decoration: underline; padding: 6px 8px;")
        guide.setAutoDefault(False)
        guide.setCursor(Qt.CursorShape.PointingHandCursor)
        guide.setToolTip("再次打开首次安装时的分步新手指南（含设置介绍与初始偏好）")
        guide.clicked.connect(self._open_guide)
        btns.addWidget(guide)
        btns.addStretch(1)
        cancel = QPushButton("取消")
        cancel.setStyleSheet(f"background: {_in_bg}; color: {TEXT};"
                             f"border: 1px solid {_bd}; border-radius: 8px;"
                             "padding: 7px 18px;")
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        ok = QPushButton("确定")
        ok.setStyleSheet(f"background: {ACCENT}; color: #FFFFFF; border: none;"
                         "border-radius: 8px; padding: 7px 24px; font-weight: 700;")
        ok.setAutoDefault(False)
        ok.clicked.connect(self.accept)
        btns.addWidget(cancel)
        btns.addWidget(ok)
        lay.addLayout(btns)
        self.edit.setFocus()

    def keyPressEvent(self, e):
        """回车 → 由 QPlainTextEdit 处理为换行，阻止传播到 QDialog 默认按钮关闭弹窗；
        Ctrl+回车 → 确定；Esc → 取消"""
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self.accept()
            # 否则吃掉事件，不让 QDialog 默认按钮被触发
            e.accept()
            return
        super().keyPressEvent(e)

    def get_value(self) -> str:
        return self.edit.toPlainText()

    @staticmethod
    def get(parent, title: str, label: str, initial: str = "",
             wrap: bool = True, min_size=(560, 340)) -> tuple:
        """弹出多行输入框，返回 (text, ok)。ok=False 时 text 为空串。"""
        dlg = _MultiLineInputDialog(title, label, initial, wrap, parent, min_size)
        agent_ui_ux.glassify_dialog(dlg)
        ok = dlg.exec() == QDialog.DialogCode.Accepted
        return (dlg.get_value(), True) if ok else ("", False)


class _ConfirmDialog(QDialog):
    """工具执行确认：显示操作与风险等级（不再内嵌屏幕截图，更轻更快）

    快捷键：回车 = 允许执行；空格再回车 = 允许执行并加入 bash 命令白名单
    （本次起该命令免确认）。命令过长时截断显示。
    """

    def __init__(self, name: str, args: dict, risk: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("AI 请求执行操作")
        self.setMinimumSize(560, 430)
        self.result_ok = False
        self.bulk_allow = False    # "本次任务内全部允许"：整段任务免（非危险）确认
        self.stop_requested = False  # "拒绝并停止任务"：终止在途任务
        self._space_pending = 0.0   # 空格按下时间戳：空格+回车=允许并加白名单

        _glass = agent_ui_ux.is_custom_package_active()
        if _glass:
            _dlg_bg = "transparent"   # 液态玻璃：透明底，由 glassify_dialog 叠加 Acrylic+光泽
            _in_bg = ("qlineargradient(x1:0,y1:0,x2:0,y2:1,"
                      "stop:0 rgba(255,255,255,44), stop:0.5 rgba(255,255,255,16), "
                      "stop:1 rgba(255,255,255,32))")
            _bd = "rgba(255,255,255,96)"
        else:
            _dlg_bg = PANEL
            _in_bg = BG
            _bd = BORDER
        self.setStyleSheet(
            f"QDialog {{ background: {_dlg_bg}; }}"
            f"QLabel {{ color: {TEXT}; font-size: 13px; }}"
            f"QPushButton {{ border: none; border-radius: 10px; "
            f"padding: 9px 22px; font-weight: 700; font-size: 13px; }}")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(12)

        risk_color = {"safe": OK, "risky": WARN, "dangerous": ERR}
        risk_txt = {"safe": "安全（白名单）", "risky": "需谨慎", "dangerous": "危险（确认后将执行）"}
        head = QLabel(f"AI 想执行：<b>{_esc(name)}</b>　风险：<span style='color:{risk_color.get(risk, TEXT)}'>"
                      f"{risk_txt.get(risk, risk)}</span>")
        head.setStyleSheet("font-size: 14px;")
        lay.addWidget(head)

        arg_txt = QPlainTextEdit()
        arg_txt.setReadOnly(True)
        arg_txt.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        arg_txt.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        # 只读 + 可滚动：命令/参数过长时滚动查看，避免内容把弹窗控件挤压变形
        arg_txt.setStyleSheet(
            f"background: {_in_bg}; color: {TEXT_DIM}; border: 1px solid {_bd};"
            "border-radius: 8px; padding: 10px; font-size: 12px; font-family: Consolas;"
            "selection-background-color: rgba(255,255,255,120);"
            "QScrollBar:vertical { background: transparent; width: 8px; margin: 2px; }"
            "QScrollBar::handle:vertical { background: rgba(255,255,255,90);"
            "border-radius: 4px; min-height: 24px; }"
            "QScrollBar::add-line, QScrollBar::sub-line { height: 0px; }")
        lay.addWidget(arg_txt, 1)

        if name in _HIDE_CONTENT_TOOLS:
            # 写入类操作：隐藏写入内容正文，仅显示目标路径与操作类型
            if name == "save_memory":
                shown = {"目标": "本地记忆文件 memory.md",
                         "写入量": f"{len(str(args.get('content', '')))} 字符"}
            else:
                shown = {"目标路径": args.get("path", ""),
                         "操作": {"write_file": "覆盖写入", "edit_file": "精确替换"}.get(name, name)}
            arg_txt.setPlainText("（写入内容已隐藏，仅确认是否允许此操作）\n\n"
                                 + json.dumps(shown, ensure_ascii=False, indent=2))
        else:
            text = json.dumps(args, ensure_ascii=False, indent=2)
            # 命令/内容过长时截断显示，避免弹窗被超长文本撑爆
            if len(text) > 600:
                text = text[:600] + "\n…（内容过长，仅显示前 600 字符）"
            arg_txt.setPlainText(text)

        # 快捷键提示
        hint = QLabel("回车 = 允许｜空格 + 回车 = 允许并加入白名单（该命令免确认）｜Esc = 拒绝｜"
                      "弹窗将一直等待你的选择，不会自动超时")
        hint.setStyleSheet(f"color: {TEXT_DIM}; font-size: {FONT_CAPTION}px;")
        lay.addWidget(hint)

        btns = QHBoxLayout()
        deny = QPushButton(_std_icon(QStyle.StandardPixmap.SP_DialogNoButton), "拒绝")
        deny.setStyleSheet(f"background: {ERR}; color: white;")
        deny.setAutoDefault(False)
        deny.clicked.connect(self._deny)
        allow = QPushButton(_std_icon(QStyle.StandardPixmap.SP_DialogYesButton), "允许执行")
        allow.setStyleSheet(f"background: {OK}; color: #06281B;")
        allow.setAutoDefault(False)
        allow.clicked.connect(self._allow)
        wl = QPushButton("允许并加入白名单")
        wl.setStyleSheet(f"background: {ACCENT}; color: white;")
        wl.setAutoDefault(False)
        wl.clicked.connect(self._allow_whitelist)
        btns.addWidget(deny)
        btns.addWidget(allow)
        btns.addWidget(wl)
        lay.addLayout(btns)
        # 次级操作：本次任务内批量授权 / 拒绝并停止整段任务（缓解多步自动化点击疲劳）
        sub = QHBoxLayout()
        sub.addStretch(1)
        bulk = QPushButton("本次任务内全部允许")
        bulk.setStyleSheet(f"background: transparent; color: {ACCENT}; border: none;"
                           f"font-size: {FONT_SMALL}px; font-weight: 700;")
        bulk.setAutoDefault(False)
        bulk.clicked.connect(self._allow_bulk)
        stop = QPushButton("拒绝并停止任务")
        stop.setStyleSheet(f"background: transparent; color: {ERR}; border: none;"
                           f"font-size: {FONT_SMALL}px; font-weight: 700;")
        stop.setAutoDefault(False)
        stop.clicked.connect(self._deny_stop)
        sub.addWidget(bulk)
        sub.addWidget(stop)
        lay.addLayout(sub)
        add_brand_footer(self)
        # 与 AI 主面板统一：透明底 + Acrylic 毛玻璃 + 光泽/波纹（液态玻璃）。
        # 继续降低磨砂质感、提升液态感：磨砂底更透(frost_alpha=40)、Acrylic 更透(tint)。
        if _glass:
            try:
                agent_ui_ux.glassify_dialog(self, tint=0x30FFFFFF, frost_alpha=40)
            except Exception:
                pass

    def _timeout_close(self):
        """弹窗超时：置拒绝并关闭，释放主线程与引擎等待，任务按拒绝继续推进"""
        # 用户已手动应答后宿主在 exec 返回时已读取结果，此后再触发无副作用
        self.result_ok = False
        self.reject()

    # ---- 键盘快捷键：回车=允许；空格+回车=允许并加入白名单 ----
    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Space:
            self._space_pending = time.time()
            e.accept()
            return
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if getattr(self, "_space_pending", 0) and (time.time() - self._space_pending) < 2.0:
                self._allow_whitelist()
            else:
                self._allow()
            e.accept()
            return
        super().keyPressEvent(e)

    def _allow(self, *_):
        self.result_ok = True
        self.accept()

    def _deny(self, *_):
        self.result_ok = False
        self.accept()

    def _allow_whitelist(self, *_):
        """允许执行，并把 run_command 的命令加入 bash 白名单（本次起免确认）"""
        self._add_cmd_whitelist()
        self._allow()

    def _allow_bulk(self, *_):
        """本次任务内全部允许：允许本条，并标记由宿主在任务内后续免确认（危险仍硬拒）"""
        self.bulk_allow = True
        self._allow()

    def _deny_stop(self, *_):
        """拒绝本条并请求停止整段在途任务"""
        self.stop_requested = True
        self._deny()

    @staticmethod
    def _add_cmd_whitelist():
        """把本对话框对应命令加入 custom_safe_commands（幂等去重）后保存 settings。"""
        if not _ConfirmDialog._current_cmd:
            return
        try:
            s = agent_skills.load_settings()
            cur = [str(c).strip() for c in (s.get("custom_safe_commands") or [])
                   if str(c).strip()]
            cmd = str(_ConfirmDialog._current_cmd).strip()
            if cmd and cmd.lower() not in {c.lower() for c in cur}:
                cur.append(cmd)
                s["custom_safe_commands"] = cur
                agent_skills.save_settings(s)
        except Exception:
            pass


# 最近一次确认的 run_command 命令（供"加入白名单"写入 settings）
_ConfirmDialog._current_cmd = ""


# 常用 MCP 服务器模板（选择后预填命令）
_MCP_TEMPLATES = [
    ("自定义", None),
    ("Excel 表格操作", {"command": "npx",
                        "args": ["-y", "@executeautomation/excel-mcp-server"]}),
    ("文件系统操作", {"command": "npx",
                      "args": ["-y", "@modelcontextprotocol/server-filesystem"]}),
    ("WPS 文本操作（需自建服务器）", {"command": "", "args": []}),
]


def _split_args(s: str) -> list:
    """按空格拆分命令行参数；引号包裹的空格路径视为单个参数（引号剥离、反斜杠保留）"""
    out, cur, quote = [], "", None
    for ch in s:
        if quote:
            if ch == quote:
                quote = None
            else:
                cur += ch
        elif ch in ('"', "'"):
            quote = ch
        elif ch.isspace():
            if cur:
                out.append(cur)
                cur = ""
        else:
            cur += ch
    if cur:
        out.append(cur)
    return out


class _AgentSettingsDialog(QDialog):
    """AI 设置：左侧导航 + 右侧分组设置（通用记忆 / 规则 / 提示词 / bash / 模型 / 技能 / MCP / 插件）"""

    plugin_done = pyqtSignal(str, str)   # 插件创建完成（ok, message），后台线程回主线程
    workflow_done = pyqtSignal(str, str)  # 工作流 AI 生成完成（ok, message），后台线程回主线程
    workflow_progress = pyqtSignal(int, str)  # 工作流生成进度（百分比, 阶段提示），后台线程回主线程
    plugin_progress = pyqtSignal(int, str)    # 插件生成进度（百分比, 阶段提示），后台线程回主线程
    uiux_done = pyqtSignal(str, str)      # UI/UX AI 生成完成（ok, message）
    uiux_progress = pyqtSignal(int, str)  # UI/UX 生成进度（百分比, 阶段提示）

    # 极简配色：纯黑 / 淡黑 / 白 / 深蓝
    _BG = "#000000"
    _PANEL = "#141414"
    _PANEL2 = "#1E1E1E"
    _TEXT = "#F5F5F5"
    _DIM = "#8A8A8A"
    _ACCENT = "#1E40AF"
    _ACCENT_HOVER = "#2563EB"
    _DANGER = "#DC2626"
    _DANGER_HOVER = "#EF4444"
    _BORDER = "#000000"

    def __init__(self, parent=None):
        super().__init__(parent)
        # 主题自适应：用当前主题全局色覆盖类级深色常量，使设置对话框在浅色模式下随之变浅
        self._BG = BG
        self._PANEL = PANEL
        self._PANEL2 = CARD
        self._TEXT = TEXT
        self._DIM = TEXT_DIM
        self._ACCENT = ACCENT
        self._ACCENT_HOVER = ACCENT_HOVER
        self._DANGER = ERR
        self._DANGER_HOVER = ERR
        self._BORDER = BORDER
        # 生成进度条（工作流/插件）状态：对话框 + 渐进动画定时器 + 当前百分比
        self._pbar = None
        self._pbar_timer = None
        self._pbar_val = 0
        self.setWindowTitle("AI 设置")
        # objectName 用于限定透明背景规则只作用于设置对话框自身，不级联到其子弹窗
        # （QMessageBox/QInputDialog 等也是 QDialog 子类，若规则用裸 QDialog 选择器，
        #  会继承 background:transparent → 弹窗透明显示为纯黑）
        self.setObjectName("agentSettingsDlg")
        _glass = agent_ui_ux.is_custom_package_active()
        if _glass:
            self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                                | Qt.WindowType.Dialog)
        self.setMinimumSize(991, 687)
        self.resize(991, 687)
        _dlg_bg = "transparent" if _glass else self._BG
        if _glass:
            # 液态玻璃：下拉/菜单弹出层同主面板液态渐变（顶部高光→中段高透亮）
            _popup_bg = _GLASS_POPUP_BG
            _popup_bd = _GLASS_POPUP_BD
            _popup_sel = _GLASS_POPUP_SEL
            _popup_hover = _GLASS_POPUP_HOVER
            _in_bg = ("qlineargradient(x1:0,y1:0,x2:0,y2:1,"
                      "stop:0 rgba(255,255,255,44), stop:0.5 rgba(255,255,255,16), "
                      "stop:1 rgba(255,255,255,32))")
            _in_focus = ("qlineargradient(x1:0,y1:0,x2:0,y2:1,"
                         "stop:0 rgba(255,255,255,70), stop:0.5 rgba(255,255,255,26), "
                         "stop:1 rgba(255,255,255,48))")
        else:
            _popup_bg = self._PANEL
            _popup_bd = self._BORDER
            _popup_sel = self._PANEL2
            _popup_hover = self._PANEL2
            _in_bg = self._PANEL
            _in_focus = self._PANEL2
        self.setStyleSheet(
            # QDialog#agentSettingsDlg：透明背景仅作用于设置对话框自身，
            # 避免级联到子 QMessageBox/QInputDialog 导致其背景透明变纯黑
            f"QDialog#agentSettingsDlg {{ background: {_dlg_bg}; }}"
            f"QLabel {{ color: {self._TEXT}; font-size: 13px; }}"
            f"QLineEdit, QPlainTextEdit, QComboBox {{ background: {_in_bg};"
            f"color: {self._TEXT}; border: 1px solid {self._BORDER};"
            "border-radius: 12px; padding: 6px 10px; }}"
            f"QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{"
            f"border: 1px solid {self._ACCENT}; background: {_in_focus}; }}"
            f"QComboBox QAbstractItemView {{ background: {_popup_bg};"
            f"color: {self._TEXT}; border: 1px solid {_popup_bd};"
            f"border-radius: 12px; padding: 4px; outline: none;"
            f"selection-background-color: {_popup_sel}; }}"
            f"QComboBox QAbstractItemView::item {{ padding: {SPACING_SM}px {SPACING_MD}px;"
        f" border-radius: {RADIUS_SM}px; }}"
            f"QComboBox QAbstractItemView::item:hover {{ background: {_popup_hover}; }}"
            f"QScrollBar:vertical {{ background: transparent; width: 8px; }}"
            f"QScrollBar::handle:vertical {{ background: {self._BORDER};"
            "border-radius: 4px; min-height: 30px; }}")
        s = agent_skills.load_settings()
        self._mcp_servers = agent_skills.load_mcp_servers()

        if _glass:
            outer = QVBoxLayout(self)
            outer.setContentsMargins(0, 0, 0, 0)
            outer.setSpacing(0)
            outer.addWidget(_frameless_titlebar(self, "AI 设置"))
            root = QHBoxLayout()
            root.setContentsMargins(0, 0, 0, 0)
            root.setSpacing(0)
            outer.addLayout(root, 1)
        else:
            root = QHBoxLayout(self)
            root.setContentsMargins(0, 0, 0, 0)
            root.setSpacing(0)

        # ---------- 左侧导航栏（矢量图标 + 文字） ----------
        self.nav = QListWidget()
        self.nav.setFixedWidth(176)
        self.nav.setStyleSheet(
            f"QListWidget {{ background: {self._PANEL}; border: none;"
            "padding-top: 10px; outline: none; }}"
            f"QListWidget::item {{ color: {self._DIM}; padding: 12px 14px;"
            "font-size: 13px; font-weight: 600; border: none;"
            f"border-left: 3px solid transparent; }}"
            f"QListWidget::item:hover {{ background: {self._PANEL2};"
            f"color: {self._TEXT}; }}"
            f"QListWidget::item:selected {{ background: {self._PANEL2};"
            f"color: {self._ACCENT_HOVER};"
            f"border-left: 3px solid {self._ACCENT_HOVER}; }}")
        for name, kind in (
            ("通用与记忆", "drive"),
            ("对话流", "folder"),
            ("自定义规则", "list"),
            ("系统提示词", "doc"),
            ("bash 白名单", "bash"),
            ("模型接入", "net"),
            ("技能", "folder"),
            ("MCP 服务器", "server"),
            ("插件", "puzzle"),
            ("语音合成", "mic"),
            ("音乐", "music"),
            ("Agent 管理", "user"),
            ("工作流", "folder"),
            ("UI/UX 自定义", "folder"),
        ):
            self.nav.addItem(QListWidgetItem(_line_icon(kind, 16), name))
        self.nav.setCurrentRow(0)
        self.nav.currentRowChanged.connect(self._switch_page)
        root.addWidget(self.nav)

        # ---------- 右侧设置项（堆叠切换；懒加载：仅先构建第一页，其余点击导航时才构建，
        # 避免打开面板时同步构建全部 11 页造成卡顿；保存时 _ensure_all_pages 补齐） ----------
        right = QVBoxLayout()
        right.setContentsMargins(24, 20, 24, 16)
        right.setSpacing(12)
        self.stack = QStackedWidget()
        self._page_builders = [
            lambda: self._build_general_page(s),
            self._build_sessions_page,
            lambda: self._build_rules_page(s),
            lambda: self._build_prompt_page(s),
            lambda: self._build_bash_page(s),
            lambda: self._build_model_page(s),
            self._build_skill_page,
            self._build_mcp_page,
            self._build_plugin_page,
            self._build_tts_page,
            self._build_music_page,
            self._build_agent_page,
            self._build_workflow_page,
            self._build_uiux_page,
        ]
        self.stack.addWidget(self._page_builders[0]())
        # 页面栈显式透明背景：QStackedWidget 在样式表环境下会被风格系统置为
        # autoFillBackground 并按 app 级 palette 填充——主窗口移除后 app palette
        # 不再随主题更新（残留深色），浅色主题下内容区会渲染成大块纯黑（对话流/
        # 语音合成等留白多的页面最明显）。透明后透出设置对话框自身背景（浅色），
        # 液态玻璃模式下也能正确透出毛玻璃效果。
        self.stack.setStyleSheet("QStackedWidget { background: transparent; }")
        # 右侧内容区包滚动容器：窗口高度缩小后页面内容超出时滚动展示，避免被挤压
        self._page_scroll = QScrollArea()
        self._page_scroll.setWidgetResizable(True)
        self._page_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._page_scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }")
        self._page_scroll.setWidget(self.stack)
        right.addWidget(self._page_scroll, 1)

        btns = QHBoxLayout()
        btns.addStretch(1)
        save = QPushButton(_std_icon(QStyle.StandardPixmap.SP_DialogYesButton), "保存")
        save.setStyleSheet(f"background: {self._ACCENT}; color: #FFFFFF;"
                           "border: none; border-radius: 8px; padding: 8px 28px;"
                           "font-size: 13px; font-weight: 700;")
        save.setAutoDefault(False)
        save.clicked.connect(self._save)
        cancel = QPushButton("取消")
        cancel.setStyleSheet(f"background: {self._PANEL}; color: {self._TEXT};"
                             f"border: 1px solid {self._BORDER}; border-radius: 8px;"
                             "padding: 8px 24px; font-size: 13px; font-weight: 600;")
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        btns.addWidget(save)
        btns.addWidget(cancel)
        right.addLayout(btns)
        root.addLayout(right)

        self.plugin_done.connect(self._on_plugin_done)
        self.workflow_done.connect(self._on_workflow_done)
        self.workflow_progress.connect(self._on_workflow_progress)
        self.plugin_progress.connect(self._on_plugin_progress)
        self.uiux_done.connect(self._on_uiux_done)
        self.uiux_progress.connect(self._on_uiux_progress)

    # ---------- 各分组页面 ----------
    def _page(self, title: str) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        t = QLabel(title)
        t.setStyleSheet(f"color: {self._TEXT}; font-size: 16px; font-weight: 700;")
        lay.addWidget(t)
        return w

    def _page_body(self, w: QWidget) -> QVBoxLayout:
        return w.layout()

    def _build_general_page(self, s) -> QWidget:
        w = self._page("通用与记忆")
        lay = self._page_body(w)
        # 主题：深色 / 浅色 / 按时间自动（8-20 浅色、20-次日8 深色），保存后立即重建面板生效
        theme_row = QHBoxLayout()
        theme_row.setSpacing(10)
        theme_lbl = QLabel("界面主题")
        theme_lbl.setStyleSheet(f"color: {self._TEXT}; font-size: 13px;")
        theme_lbl.setFixedWidth(70)
        theme_row.addWidget(theme_lbl)
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("深色", "dark")
        self.theme_combo.addItem("浅色", "light")
        self.theme_combo.addItem("按时间自动（8-20 浅色）", "auto")
        saved_theme = _theme_setting()
        ti = self.theme_combo.findData(saved_theme)
        self.theme_combo.setCurrentIndex(ti if ti >= 0 else 2)
        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        theme_row.addWidget(self.theme_combo, 1)
        # 液态玻璃等自定义 UI/UX 主题自带固定配色，与深浅主题无关 → 禁用主题切换
        if agent_ui_ux.is_custom_package_active():
            self.theme_combo.setEnabled(False)
            hint = QLabel("当前启用 UI/UX 自定义主题（自带固定配色），深浅主题切换已禁用")
            hint.setStyleSheet(f"color: {self._DIM}; font-size: 12px;")
            hint.setWordWrap(True)
            theme_row.addWidget(hint)
        lay.addLayout(theme_row)
        self.memory_check = QCheckBox("开启长期记忆（save_memory / load_memory）")
        self.memory_check.setChecked(bool(s.get("memory_enabled", True)))
        self.memory_check.setStyleSheet(f"color: {self._TEXT}; font-size: 13px; spacing: 8px;")
        lay.addWidget(self.memory_check)
        # 任务清单窗口：关闭后 AI 面板不再显示 todos 独立窗口（连续清空提示中可一键跳转此处）
        self.todos_check = QCheckBox("显示任务清单窗口（todos）")
        self.todos_check.setChecked(self._todos_enabled())
        self.todos_check.setStyleSheet(f"color: {self._TEXT}; font-size: 13px; spacing: 8px;")
        self.todos_check.toggled.connect(self._on_todos_toggled)
        lay.addWidget(self.todos_check)
        # Git 分支面板：关闭后 AI 面板不再显示 git 只读面板
        self.git_check = QCheckBox("显示 Git 分支面板（git）")
        self.git_check.setChecked(self._git_enabled())
        self.git_check.setStyleSheet(f"color: {self._TEXT}; font-size: 13px; spacing: 8px;")
        self.git_check.toggled.connect(self._on_git_toggled)
        lay.addWidget(self.git_check)
        # 工作树面板：关闭后 AI 面板不再显示 worktree 文件树（主窗口 worktree 已移除，仅此一处）
        self.wt_check = QCheckBox("显示工作树文件面板（worktree）")
        self.wt_check.setChecked(self._wt_enabled())
        self.wt_check.setStyleSheet(f"color: {self._TEXT}; font-size: 13px; spacing: 8px;")
        self.wt_check.toggled.connect(self._on_wt_toggled)
        lay.addWidget(self.wt_check)
        # 代码预览面板：双击工作树文件在右侧预览，关闭后不显示
        self.code_check = QCheckBox("显示代码预览面板（双击文件预览）")
        self.code_check.setChecked(self._code_enabled())
        self.code_check.setStyleSheet(f"color: {self._TEXT}; font-size: 13px; spacing: 8px;")
        self.code_check.toggled.connect(self._on_code_toggled)
        lay.addWidget(self.code_check)
        # 面板位置：子面板可自由拖动并记忆位置；一键恢复默认停靠布局
        reset_row = QHBoxLayout()
        reset_row.setSpacing(10)
        reset_lbl = QLabel("面板位置")
        reset_lbl.setStyleSheet(f"color: {self._DIM}; font-size: 12px;")
        reset_lbl.setFixedWidth(70)
        reset_row.addWidget(reset_lbl)
        reset_btn = QPushButton("重置全部面板位置（恢复默认停靠）")
        reset_btn.setStyleSheet(
            f"background: {self._PANEL}; color: {self._TEXT}; border: 1px solid {self._BORDER};"
            "border-radius: 8px; padding: 6px 14px; font-weight: 600;")
        reset_btn.setAutoDefault(False)
        reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        reset_btn.setToolTip("将 todos / Git / 工作树 / 代码预览等子面板的拖动位置清除，"
                             "回到主面板侧边的默认停靠布局")
        reset_btn.clicked.connect(self._on_reset_panels)
        reset_row.addWidget(reset_btn, 1)
        lay.addLayout(reset_row)
        # 面板偏好：四个子面板（工作树/Git/任务清单/代码预览）与主面板的关系
        pref_lbl = QLabel("面板偏好")
        pref_lbl.setStyleSheet(
            f"color: {self._DIM}; font-size: 12px; font-weight: 700; margin-top: 8px;")
        lay.addWidget(pref_lbl)
        self.panel_mode_combo = QComboBox()
        self.panel_mode_combo.addItem("贴附主面板（独立窗口靠边悬浮）", "attach")
        self.panel_mode_combo.addItem("融入主面板（与主面板同一窗口）", "dock")
        saved_mode = _panel_mode_setting()
        _mi = self.panel_mode_combo.findData(saved_mode)
        self.panel_mode_combo.setCurrentIndex(_mi if _mi >= 0 else 0)
        self.panel_mode_combo.setStyleSheet(_QCOMBO)
        self.panel_mode_combo.setToolTip("贴附 = 子面板为独立窗口，依序停靠主面板左右侧；"
                                        "融入 = 子面板嵌入主面板左右栏内，随主面板同显同隐（同一窗口）")
        self.panel_mode_combo.currentIndexChanged.connect(self._on_panel_mode_changed)
        lay.addWidget(self.panel_mode_combo)
        pref_sub = QLabel("贴附：子面板悬浮在主面板外侧，可各自拖动位置与调整大小；"
                          "融入：四块面板嵌入主面板左右栏，作为同一窗口的一部分。设置保存后立即生效。")
        pref_sub.setStyleSheet(f"color: {self._DIM}; font-size: 12px;")
        pref_sub.setWordWrap(True)
        lay.addWidget(pref_sub)
        # 能力开关：分别控制 MCP / 技能 / 插件，关闭后 AI 不再加载对应能力（settings cap_*）
        cap_lbl = QLabel("能力开关")
        cap_lbl.setStyleSheet(f"color: {self._DIM}; font-size: 12px; font-weight: 700; margin-top: 8px;")
        lay.addWidget(cap_lbl)
        self.cap_mcp_check = QCheckBox("启用 MCP 服务器（外部工具）")
        self.cap_mcp_check.setChecked(agent_skills.cap_enabled("mcp"))
        self.cap_mcp_check.setToolTip("关闭后 AI 不再暴露任何 MCP 服务器工具")
        self.cap_mcp_check.setStyleSheet(f"color: {self._TEXT}; font-size: 13px; spacing: 8px;")
        lay.addWidget(self.cap_mcp_check)
        self.cap_skill_check = QCheckBox("启用技能（自动匹配 / 指令注入）")
        self.cap_skill_check.setChecked(agent_skills.cap_enabled("skill"))
        self.cap_skill_check.setToolTip("关闭后 AI 不再加载任何技能规范流程")
        self.cap_skill_check.setStyleSheet(f"color: {self._TEXT}; font-size: 13px; spacing: 8px;")
        lay.addWidget(self.cap_skill_check)
        self.cap_plugin_check = QCheckBox("启用插件（技能 + MCP）")
        self.cap_plugin_check.setChecked(agent_skills.cap_enabled("plugin"))
        self.cap_plugin_check.setToolTip("关闭后插件登记的技能与 MCP 服务器不再生效")
        self.cap_plugin_check.setStyleSheet(f"color: {self._TEXT}; font-size: 13px; spacing: 8px;")
        lay.addWidget(self.cap_plugin_check)
        # 工具管控：禁用指定工具 / 禁用全部工具调用（系统层面：schema 与执行层双重生效，
        # 既有设置期也即时生效，提示词干预无法绕过）
        tool_lbl = QLabel("工具管控")
        tool_lbl.setStyleSheet(
            f"color: {self._DIM}; font-size: 12px; font-weight: 700; margin-top: 8px;")
        lay.addWidget(tool_lbl)
        self.disable_all_check = QCheckBox("禁用全部工具调用（AI 仅可对话回复）")
        self.disable_all_check.setChecked(agent_sandbox.tools_disabled_all())
        self.disable_all_check.setToolTip("开启后 AI 无法调用任何工具（含 grep/read_file/web_search 等），"
                                          "只能以文本对话作答")
        self.disable_all_check.setStyleSheet(f"color: {self._TEXT}; font-size: 13px; spacing: 8px;")
        lay.addWidget(self.disable_all_check)
        dis_sub = QLabel("禁用指定工具：每行一个工具名（如 grep / read_file / web_search / run_command）。"
                         "被禁用工具将无法被 AI 调用，即使提示词诱导也会被系统直接拒绝。")
        dis_sub.setStyleSheet(f"color: {self._DIM}; font-size: 12px;")
        dis_sub.setWordWrap(True)
        lay.addWidget(dis_sub)
        self.disable_tools_edit = QPlainTextEdit()
        self.disable_tools_edit.setPlaceholderText("每行一个工具名，如：\ngrep\nread_file\nweb_search")
        _banned = sorted(agent_sandbox.disabled_tools())
        if _banned:
            self.disable_tools_edit.setPlainText("\n".join(_banned))
        self.disable_tools_edit.setMaximumHeight(96)
        self.disable_tools_edit.setStyleSheet(
            f"QPlainTextEdit {{ background: {self._PANEL}; color: {self._TEXT};"
            f"border: 1px solid {self._BORDER}; border-radius: 8px;"
            "padding: 6px 10px; font-size: 13px; }}"
            f"QPlainTextEdit:focus {{ border: 1px solid {self._ACCENT_HOVER}; }}")
        lay.addWidget(self.disable_tools_edit)
        # 趣味互动：AI 随机截屏分析屏幕并弹出俏皮锐评气泡（涉及周期性全屏截图，默认开启）
        fun_lbl = QLabel("趣味互动")
        fun_lbl.setStyleSheet(f"color: {self._DIM}; font-size: 12px; font-weight: 700; margin-top: 8px;")
        lay.addWidget(fun_lbl)
        self.fun_check = QCheckBox("AI 趣味锐评（随机截屏分析并弹可爱气泡）")
        self.fun_check.setChecked(
            str(app_identity.qsettings().value("agent_fun", "1"))
            .strip().lower() in ("1", "true", "yes", "on"))
        self.fun_check.setToolTip("开启后 AI 每隔几分钟随机截取全屏分析，在屏幕底部弹出俏皮锐评气泡（5 秒后消失，不计入上下文与记忆）")
        self.fun_check.setStyleSheet(f"color: {self._TEXT}; font-size: 13px; spacing: 8px;")
        lay.addWidget(self.fun_check)
        fun_interval_row = QHBoxLayout()
        fun_interval_row.setSpacing(10)
        fun_interval_lbl = QLabel("锐评间隔")
        fun_interval_lbl.setStyleSheet(f"color: {self._TEXT}; font-size: 13px;")
        fun_interval_lbl.setFixedWidth(70)
        fun_interval_row.addWidget(fun_interval_lbl)
        self.fun_interval_combo = QComboBox()
        self.fun_interval_combo.addItem("1~3 分钟", "frequent")
        self.fun_interval_combo.addItem("3~6 分钟", "normal")
        self.fun_interval_combo.addItem("5~10 分钟", "relaxed")
        saved_fun = str(app_identity.qsettings().value("agent_fun_interval", "normal"))
        fi = self.fun_interval_combo.findData(saved_fun)
        self.fun_interval_combo.setCurrentIndex(fi if fi >= 0 else 1)
        fun_interval_row.addWidget(self.fun_interval_combo, 1)
        lay.addLayout(fun_interval_row)
        # 执行模式：AskBeforeEdit / Edit / YOLO（原面板顶栏下拉，迁入设置页）
        mode_row = QHBoxLayout()
        mode_row.setSpacing(10)
        mode_lbl = QLabel("执行模式")
        mode_lbl.setStyleSheet(f"color: {self._TEXT}; font-size: 13px;")
        mode_lbl.setFixedWidth(70)
        mode_row.addWidget(mode_lbl)
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("AskBeforeEdit（每步操作确认）", "ask")
        self.mode_combo.addItem("Edit（仅非白名单 bash 命令确认）", "edit")
        self.mode_combo.addItem("YOLO（无确认直行，不设任何限制）", "yolo")
        saved_mode = app_identity.qsettings().value("agent_mode", "ask")
        mi = self.mode_combo.findData(saved_mode)
        self.mode_combo.setCurrentIndex(mi if mi >= 0 else 0)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        mode_row.addWidget(self.mode_combo, 1)
        lay.addLayout(mode_row)
        # 工作目录已迁移到「对话流」页按会话设置（删除通用设置里的全局入口，
        # 避免两处入口语义冲突；会话未设置时仍回退历史全局默认值 agent_workdir）
        tip = QLabel("记忆：AI 可将重要信息写入 memory.md 并在后续任务中读取。")
        tip.setStyleSheet(f"color: {self._DIM}; font-size: 12px;")
        lay.addWidget(tip)
        lay.addStretch(1)
        return w

    def _build_sessions_page(self, s=None) -> QWidget:
        """对话流页面：为每个对话分配独立工作目录（新建对话自动沿用上一对话目录）"""
        w = self._page("对话流")
        lay = self._page_body(w)
        tip = QLabel("为每个对话分配独立的工作目录；新建对话会自动沿用上一个对话的工作目录。"
                     "留空表示该对话沿用全局工作目录。")
        tip.setStyleSheet(f"color: {self._DIM}; font-size: 12px;")
        tip.setWordWrap(True)
        lay.addWidget(tip)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        body = QWidget()
        body.setStyleSheet("background: transparent;")
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(0, 0, 4, 0)
        body_lay.setSpacing(8)

        self._session_wd_rows = []
        self._wd_scroll = scroll   # 供 focus_workdir_row 滚动定位
        try:
            panel = self.parent()
            lst = panel._load_session_list() if panel is not None else []
        except Exception:
            lst = []
        lst = sorted(lst, key=lambda x: x.get("updated", 0), reverse=True)
        if not lst:
            empty = QLabel("暂无对话记录")
            empty.setStyleSheet(f"color: {self._DIM}; font-size: 12px;")
            body_lay.addWidget(empty)
        for item in lst:
            sid = item.get("id", "")
            name = item.get("name", "新对话") or "新对话"
            wd = str(item.get("workdir") or "").strip()
            # 每行外层包 QFrame：既做卡片底（视觉），也作为定位/闪烁高亮的载体
            frame = QFrame()
            frame.setObjectName("sessionWdRow")
            frame.setStyleSheet(self._wd_row_qss(False))
            row = QHBoxLayout(frame)
            row.setContentsMargins(10, 6, 8, 6)
            row.setSpacing(8)
            name_lbl = QLabel(name)
            name_lbl.setStyleSheet(f"color: {self._TEXT}; font-size: 13px;")
            name_lbl.setFixedWidth(120)
            name_lbl.setToolTip(sid)
            row.addWidget(name_lbl)
            wd_edit = QLineEdit()
            wd_edit.setPlaceholderText("留空沿用全局工作目录")
            wd_edit.setText(wd)
            row.addWidget(wd_edit, 1)
            browse = QPushButton(_line_icon("folder", 18), "浏览…")
            browse.setFixedWidth(96)
            browse.setFixedHeight(30)
            browse.setStyleSheet(f"background: {self._PANEL}; color: {self._TEXT};"
                                 f"border: 1px solid {self._BORDER}; border-radius: 8px;"
                                 "padding: 0 10px; font-weight: 600;")
            browse.setAutoDefault(False)
            browse.setCursor(Qt.CursorShape.PointingHandCursor)
            browse.setToolTip("选择该对话的工作目录")
            browse.clicked.connect(
                lambda _=False, e=wd_edit: self._browse_session_workdir(e))
            row.addWidget(browse)
            body_lay.addWidget(frame)
            self._session_wd_rows.append((sid, wd_edit, frame))
        body_lay.addStretch(1)
        scroll.setWidget(body)
        lay.addWidget(scroll, 1)
        return w

    def _wd_row_qss(self, highlight: bool) -> str:
        """会话工作目录行卡片样式：常态 1px 边框；闪烁高亮用深蓝 2px 边框"""
        if highlight:
            return (f"QFrame#sessionWdRow {{ background: {self._PANEL};"
                    f"border: 2px solid {self._ACCENT_HOVER}; border-radius: 8px; }}")
        return (f"QFrame#sessionWdRow {{ background: {self._PANEL};"
                f"border: 1px solid {self._BORDER}; border-radius: 8px; }}")

    def _flash_workdir_row(self, frame):
        """边框闪烁提醒（深蓝高亮 ⇄ 常态交替 8 次），提醒用户当前对话的设置行位置"""
        if getattr(self, "_wd_flash_timer", None) is None:
            self._wd_flash_timer = QTimer(self)
            self._wd_flash_timer.setInterval(220)
            self._wd_flash_timer.timeout.connect(self._wd_flash_tick)
        self._wd_flash_left = 8
        self._wd_flash_frame = frame
        self._wd_flash_timer.start()

    def _wd_flash_tick(self):
        self._wd_flash_left -= 1
        frame = self._wd_flash_frame
        if frame is None or not frame.isVisible():
            self._wd_flash_timer.stop()
            self._wd_flash_frame = None
            return
        frame.setStyleSheet(self._wd_row_qss(highlight=self._wd_flash_left % 2))
        if self._wd_flash_left <= 0:
            self._wd_flash_timer.stop()
            self._wd_flash_frame = None

    def focus_workdir_row(self, sid: str):
        """定位到「对话流」页（第 2 项）并滚动到指定会话行、闪烁其边框，
        用于工作目录为空时从聊天页点击「去设置」跳转过来提醒位置。"""
        try:
            if self.nav.count() > 1:
                self.nav.setCurrentRow(1)   # 懒构建对话流页后切换
            scroll = getattr(self, "_wd_scroll", None)
            for _sid, _wd_edit, frame in getattr(self, "_session_wd_rows", []):
                if _sid == sid:
                    if scroll is not None:
                        scroll.ensureWidgetVisible(frame)
                    self._flash_workdir_row(frame)
                    return
        except Exception:
            pass

    def _browse_session_workdir(self, edit):
        """为某对话选择工作目录（写入对应输入框，保存时持久化）"""
        start = edit.text().strip() or str(Path.home())
        d = QFileDialog.getExistingDirectory(self, "选择该对话的工作目录", start)
        if d:
            edit.setText(d)

    def _on_theme_changed(self, *_):
        """主题下拉切换：液态玻璃等自定义 UI/UX 主题自带固定配色，禁止切换深浅主题；
        其余主题立即写入 QSettings；关闭设置对话框后重建 AI 面板（即时生效，
        避免在模态对话框内关闭父面板导致信号中断）。"""
        if agent_ui_ux.is_custom_package_active():
            # 自定义主题禁用深浅切换：回退到当前保存的主题，且不触发重建
            try:
                cur = _theme_setting()
                ti = self.theme_combo.findData(cur)
                self.theme_combo.blockSignals(True)
                self.theme_combo.setCurrentIndex(ti if ti >= 0 else 2)
                self.theme_combo.blockSignals(False)
            except Exception:
                pass
            return
        idx = self.theme_combo.currentIndex()
        mode = self.theme_combo.itemData(idx)
        if not mode:
            mode = "auto"
        app_identity.qsettings().setValue("agent_theme", mode)
        setattr(self, "_theme_changed", True)

    # ---------- 子面板开关 ----------
    def _todos_enabled(self) -> bool:
        return str(app_identity.qsettings()
                   .value("agent_show_todos", "1")).strip().lower() in ("1", "true", "yes")

    def _on_todos_toggled(self, checked: bool):
        """任务清单窗口开关：点击即生效（无需点保存），立即写入并同步显示/隐藏"""
        app_identity.qsettings().setValue(
            "agent_show_todos", "1" if checked else "0")
        panel = self.parent()
        if panel is not None and hasattr(panel, "_sync_todos_win"):
            panel._sync_todos_win()
        if panel is not None and hasattr(panel, "_apply_reserve"):
            panel._apply_reserve()

    def _git_enabled(self) -> bool:
        return str(app_identity.qsettings()
                   .value("agent_show_git", "1")).strip().lower() in ("1", "true", "yes")

    def _wt_enabled(self) -> bool:
        return str(app_identity.qsettings()
                   .value("agent_show_worktree", "1")).strip().lower() in ("1", "true", "yes")

    def _code_enabled(self) -> bool:
        return str(app_identity.qsettings()
                   .value("agent_show_code", "1")).strip().lower() in ("1", "true", "yes")

    def _on_git_toggled(self, checked: bool):
        """Git 分支面板开关：点击即生效，立即写入并同步显示/隐藏"""
        app_identity.qsettings().setValue(
            "agent_show_git", "1" if checked else "0")
        panel = self.parent()
        if panel is not None and hasattr(panel, "_sync_git_win"):
            panel._sync_git_win()
        if panel is not None and hasattr(panel, "_apply_reserve"):
            panel._apply_reserve()

    def _on_wt_toggled(self, checked: bool):
        """工作树面板开关：点击即生效，立即写入并同步显示/隐藏"""
        app_identity.qsettings().setValue(
            "agent_show_worktree", "1" if checked else "0")
        panel = self.parent()
        if panel is not None and hasattr(panel, "_sync_wt_win"):
            panel._sync_wt_win()
        if panel is not None and hasattr(panel, "_apply_reserve"):
            panel._apply_reserve()

    def _on_code_toggled(self, checked: bool):
        """代码预览面板开关：点击即生效，立即写入并同步显示/隐藏"""
        app_identity.qsettings().setValue(
            "agent_show_code", "1" if checked else "0")
        panel = self.parent()
        if panel is not None and hasattr(panel, "_sync_code_win"):
            panel._sync_code_win()
        if panel is not None and hasattr(panel, "_apply_reserve"):
            panel._apply_reserve()

    def _build_rules_page(self, s) -> QWidget:
        w = self._page("自定义规则")
        lay = self._page_body(w)
        sub = QLabel("每行一条，追加到系统提示词末尾，约束 AI 行为")
        sub.setStyleSheet(f"color: {self._DIM}; font-size: 12px;")
        lay.addWidget(sub)
        self.rules_edit = QPlainTextEdit()
        self.rules_edit.setPlaceholderText("如：\n操作注册表前必须先 ask_user 确认\n不要移动正在运行的应用")
        self.rules_edit.setPlainText("\n".join(str(r) for r in (s.get("custom_rules") or [])))
        lay.addWidget(self.rules_edit, 1)
        return w

    def _build_prompt_page(self, s) -> QWidget:
        w = self._page("系统提示词")
        lay = self._page_body(w)
        sub = QLabel("追加到默认人设之后，不覆盖内置角色设定")
        sub.setStyleSheet(f"color: {self._DIM}; font-size: 12px;")
        lay.addWidget(sub)
        self.prompt_edit = QPlainTextEdit()
        self.prompt_edit.setPlaceholderText("补充的提示词…")
        self.prompt_edit.setPlainText(str(s.get("custom_system_prompt") or ""))
        lay.addWidget(self.prompt_edit, 1)
        return w

    def _build_bash_page(self, s) -> QWidget:
        w = self._page("bash 命令白名单")
        lay = self._page_body(w)
        sub = QLabel("每行一条，命中前缀即免确认（如填 git push 可匹配 git push origin main；"
                     "单命令词如 python 需整条完全一致，防止 -c 注入被放行）")
        sub.setStyleSheet(f"color: {self._DIM}; font-size: 12px;")
        sub.setWordWrap(True)
        lay.addWidget(sub)
        self.safe_edit = QPlainTextEdit()
        self.safe_edit.setPlaceholderText("如：\nnpm\npip\npython\ngit")
        self.safe_edit.setPlainText(
            "\n".join(str(c) for c in (s.get("custom_safe_commands") or [])))
        lay.addWidget(self.safe_edit, 1)
        return w

    def _build_model_page(self, s) -> QWidget:
        w = self._page("模型接入")
        lay = self._page_body(w)
        sub = QLabel("多服务商模型：每个服务商一张卡片，点击卡片编辑其地址/Key/模型；"
                    "全部服务商模型统一参与路由与切换，模型名含纯文本关键字（如 deepseek）"
                    "自动禁用图片/截图能力")
        sub.setStyleSheet(f"color: {self._DIM}; font-size: 12px;")
        sub.setWordWrap(True)
        lay.addWidget(sub)
        m = s.get("model") or {}
        if not isinstance(m, dict):
            m = {}
        cfg = agent_llm.load_model_config()
        self._providers = list(cfg.get("providers") or [])
        # 服务商卡片列表
        self.provider_list = QListWidget()
        self.provider_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)   # 禁止横向滚动条（含最大化时）
        self.provider_list.setStyleSheet(
            f"QListWidget {{ background: {self._PANEL}; color: {self._TEXT};"
            f"border: 1px solid {self._BORDER}; border-radius: 8px; padding: 6px; }}"
            f"QListWidget::item {{ border-radius: 8px; margin: 2px; }}"
            f"QListWidget::item:selected {{ background: {self._PANEL2}; }}")
        self.provider_list.itemClicked.connect(self._on_provider_select)
        self.provider_list.itemDoubleClicked.connect(self._on_provider_edit)
        self.provider_list.itemSelectionChanged.connect(self._update_provider_ui)
        lay.addWidget(self.provider_list, 1)
        self._reload_provider_list()
        # 列表尺寸变化（窗口缩放）时重算卡片高度，避免长内容卡片被挤压裁剪
        try:
            from PyQt6.QtCore import QEvent as _QEv
            from PyQt6.QtCore import QObject as _QObj

            class _CardRemeasurer(_QObj):
                def __init__(self, owner):
                    super().__init__()
                    self.owner = owner

                def eventFilter(self, obj, ev):
                    if ev.type() in (_QEv.Type.Resize, _QEv.Type.Show):
                        self.owner._remeasure_provider_cards()
                    return False

            self._provider_resizer = _CardRemeasurer(self)
            self.provider_list.installEventFilter(self._provider_resizer)
        except Exception:
            pass
        # 服务商操作按钮
        prow = QHBoxLayout()
        prow.setSpacing(8)
        add_p = QPushButton(_std_icon(QStyle.StandardPixmap.SP_FileDialogNewFolder), "添加服务商")
        add_p.setStyleSheet(f"background: {self._ACCENT}; color: #FFFFFF; border: none;"
                            "border-radius: 8px; padding: 7px 16px; font-weight: 700;")
        add_p.setAutoDefault(False)
        add_p.clicked.connect(self._on_provider_add)
        self.del_provider_btn = QPushButton(_line_icon("trash", 16), "删除")
        self.del_provider_btn.setAutoDefault(False)
        self.del_provider_btn.clicked.connect(self._on_provider_delete)
        prow.addWidget(add_p)
        prow.addWidget(self.del_provider_btn)
        prow.addStretch(1)
        lay.addLayout(prow)
        # 选中提示语（状态栏）
        self.provider_hint = QLabel("点击选中服务商卡片（删除按钮随之变红可用）；双击卡片编辑")
        self.provider_hint.setStyleSheet(f"color: {self._DIM}; font-size: 12px;")
        self.provider_hint.setWordWrap(True)
        lay.addWidget(self.provider_hint)
        self._update_provider_ui()
        # 思考强度（工作力度）：8 档滑块（关闭/低/中/高/超高/最高/极致/超级）+ 自动按难度开关；
        # 档位满刻度可调，上游 /models 声明力度级别时自动映射并提示（见 _sync_effort_declared）
        self._effort = cfg.get("effort", "medium")
        self._auto_effort = bool(cfg.get("auto_effort", True))
        self._declared_efforts = None
        eff_row = QHBoxLayout()
        eff_row.setSpacing(10)
        eff_lbl = QLabel("思考强度")
        eff_lbl.setStyleSheet(f"color: {self._TEXT}; font-size: 13px;")
        eff_lbl.setFixedWidth(70)
        eff_row.addWidget(eff_lbl)
        self.effort_slider = QSlider(Qt.Orientation.Horizontal)
        self.effort_slider.setRange(0, len(agent_llm.EFFORTS) - 1)
        self.effort_slider.setFixedWidth(180)
        self.effort_slider.setPageStep(1)
        _eff_lv = [agent_llm.effort_label(x) for x in agent_llm.EFFORTS]
        self.effort_slider.setToolTip("思考强度（工作力度）：决定思考档位与所用模型，"
                                      + " / ".join(_eff_lv))
        self.effort_slider.setStyleSheet(
            f"QSlider::groove:horizontal {{ height: 4px; background: {self._BORDER};"
            "border-radius: 2px; }}"
            f"QSlider::sub-page:horizontal {{ background: {self._ACCENT}; border-radius: 2px; }}"
            f"QSlider::handle:horizontal {{ width: 14px; height: 14px; margin: -5px 0;"
            f"background: {self._ACCENT}; border: 2px solid {self._BG}; border-radius: 7px; }}"
            f"QSlider::handle:horizontal:hover {{ background: {self._ACCENT_HOVER}; }}")
        self.effort_slider.valueChanged.connect(self._on_effort_slider)
        eff_row.addWidget(self.effort_slider)
        self.effort_label = QLabel(agent_llm.effort_label(self._effort))
        self.effort_label.setStyleSheet(
            f"color: {self._ACCENT}; font-size: 14px; font-weight: 800;")
        self.effort_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.effort_label.setFixedWidth(56)
        eff_row.addWidget(self.effort_label)
        eff_row.addStretch(1)
        lay.addLayout(eff_row)
        # 挡位刻度 + 上游声明提示（同一行文案，fetch 后由 _sync_effort_declared 刷新）
        self.effort_hint = QLabel(" · ".join(_eff_lv))
        self.effort_hint.setStyleSheet(f"color: {self._DIM}; font-size: 11px;")
        self.effort_hint.setWordWrap(True)
        lay.addWidget(self.effort_hint)
        self.auto_effort_check = QCheckBox("自动按难度（按任务难度自动选择工作力度）")
        self.auto_effort_check.setStyleSheet(f"color: {self._TEXT}; font-size: 13px; spacing: 8px;")
        self.auto_effort_check.setToolTip("按任务难度自动选择工作力度（智能调用）；关闭后仅手动拖动")
        self.auto_effort_check.toggled.connect(self._on_effort_auto)
        # 先初始化滑块值（屏蔽信号），再设自动开关：避免 setChecked 触发回调时
        # 读到未初始化的滑块（值为 0=low）把 _effort 覆盖成 low —— 这是「工作力度
        # 无法保存」的真根因（初始化阶段把配置里的力度洗成 low）。
        self.effort_slider.blockSignals(True)
        self.effort_slider.setValue(agent_llm.EFFORTS.index(self._effort)
                                    if self._effort in agent_llm.EFFORTS else 0)
        self.effort_slider.blockSignals(False)
        self.auto_effort_check.blockSignals(True)
        self.auto_effort_check.setChecked(self._auto_effort)
        self.auto_effort_check.blockSignals(False)
        lay.addWidget(self.auto_effort_check)
        # 自动按难度开启时以提示色标注（手动力度会被自动估算覆盖），不置灰不锁定
        self._sync_effort_ui()
        # 已保存服务商声明过力度级别时，打开设置页即自动映射滑块挡位
        self._sync_effort_declared()
        # 思考模式：跟随自动 / 始终开启 / 始终关闭 + 每次发送强制思考
        self._think_mode = cfg.get("think_mode", "auto")
        self._force_think = bool(cfg.get("force_think", False))
        think_row = QHBoxLayout()
        think_row.setSpacing(10)
        think_lbl = QLabel("思考模式")
        think_lbl.setStyleSheet(f"color: {self._TEXT}; font-size: 13px;")
        think_lbl.setFixedWidth(70)
        think_row.addWidget(think_lbl)
        self.think_combo = QComboBox()
        self.think_combo.addItem("跟随自动（按工作力度）", "auto")
        self.think_combo.addItem("始终开启（强制思考）", "on")
        self.think_combo.addItem("始终关闭（不思考）", "off")
        _ti = self.think_combo.findData(self._think_mode)
        self.think_combo.setCurrentIndex(_ti if _ti >= 0 else 0)
        self.think_combo.setStyleSheet(_QCOMBO)
        self.think_combo.setToolTip("跟随自动 = 按工作力度自动开关思考；"
                                    "始终开启/关闭 = 每次请求带对应思考参数覆盖默认行为")
        self.think_combo.currentIndexChanged.connect(self._on_think_mode_changed)
        think_row.addWidget(self.think_combo, 1)
        think_row.addStretch(1)
        lay.addLayout(think_row)
        self.force_think_check = QCheckBox("每次发送消息时强制思考（不允许模型选择不思考）")
        self.force_think_check.setStyleSheet(f"color: {self._TEXT}; font-size: 13px; spacing: 8px;")
        self.force_think_check.setToolTip("勾选后每次请求都强制启用思考参数，模型无法选择不思考；"
                                          "同时自动把上方思考模式切到「始终开启」")
        self.force_think_check.blockSignals(True)
        self.force_think_check.setChecked(self._force_think)
        self.force_think_check.blockSignals(False)
        self.force_think_check.toggled.connect(self._on_force_think_toggled)
        lay.addWidget(self.force_think_check)
        # ── 上下文窗口（1M 开关）──────────────────────────────────────────────
        # 用户可强制按 1M 上限计算：窗口、预留输出、预警/压缩/硬裁阈值、进度条统统一致。
        # 窗口取值优先级见 agent_llm.resolve_context：
        #   1M 开关 > 上游服务商声明 > 服务商配置手填 > 内置已知表 > 模型名推断
        self._context_1m = bool(cfg.get("context_1m", False))
        self.context_1m_check = QCheckBox(
            "开启 1M 上下文（模型支持1M上下文窗口，输入+输出=1M上下文窗口）")
        self.context_1m_check.setStyleSheet(
            f"color: {self._TEXT}; font-size: 13px; spacing: 8px;")
        self.context_1m_check.setToolTip(
            "模型支持1M上下文窗口，输入+输出=1M上下文窗口；"
            "勾选后，所有上下文用量与压缩阈值一律按 1M token 上限计算"
            f"（{_fmt_tokens(agent_llm.ONE_M_CONTEXT)}）；"
            "服务商实际是否支持由上游决定，若上游拒绝请关闭本开关。")
        self.context_1m_check.blockSignals(True)
        self.context_1m_check.setChecked(self._context_1m)
        self.context_1m_check.blockSignals(False)
        self.context_1m_check.toggled.connect(self._on_context_1m_toggled)
        lay.addWidget(self.context_1m_check)
        self.context_hint = QLabel("")
        self.context_hint.setStyleSheet(f"color: {self._DIM}; font-size: 12px;")
        self.context_hint.setWordWrap(True)
        lay.addWidget(self.context_hint)
        self._sync_context_hint()
        tip = QLabel("思考模式按上方开关手动控制。开启「始终思考」后，思考强度取当前「思考强度」"
                     "滑块档位并自动映射：DeepSeek V4 仅 high/max、GLM-5.2 全档、"
                     "OpenAI o 系列 reasoning_effort、Agnes reasoning_effort 等")
        tip.setStyleSheet(f"color: {self._DIM}; font-size: 12px;")
        tip.setWordWrap(True)
        lay.addWidget(tip)
        lay.addStretch(1)
        return w

    def _build_tts_page(self) -> QWidget:
        w = self._page("语音合成")
        lay = self._page_body(w)
        cfg = agent_tts.load_config()
        tip = QLabel(f"当前音色：{agent_tts.VOICE_DISPLAY_NAME}（AI 回复时自动流式合成语音，"
                     "边生成边播放，无需手动选择音色）")
        tip.setStyleSheet(f"color: {self._TEXT}; font-size: 13px;")
        tip.setWordWrap(True)
        lay.addWidget(tip)
        self.auto_read_check = QCheckBox("AI 回复自动朗读（开关默认开启，关闭后仅显式要求朗读时播放）")
        self.auto_read_check.setChecked(bool(cfg.get("auto_read", True)))
        self.auto_read_check.setStyleSheet(f"color: {self._TEXT}; font-size: 13px; spacing: 8px;")
        lay.addWidget(self.auto_read_check)
        # 语速调节：对齐参考音频节奏（0.5x~1.5x，默认 1.0 = 与参考一致）
        spd_row = QHBoxLayout()
        spd_row.setSpacing(10)
        spd_lbl = QLabel("语速")
        spd_lbl.setStyleSheet(f"color: {self._TEXT}; font-size: 13px;")
        spd_lbl.setFixedWidth(70)
        spd_row.addWidget(spd_lbl)
        self.speed_slider = QSlider(Qt.Orientation.Horizontal)
        self.speed_slider.setRange(50, 150)
        self.speed_slider.setValue(int(float(cfg.get("speech_rate") or 1.0) * 100))
        self.speed_slider.setPageStep(5)
        self.speed_slider.setToolTip("拖动调节语速（100% = 与参考音频一致）")
        self.speed_slider.setStyleSheet(
            f"QSlider::groove:horizontal {{ height: 4px; background: {self._BORDER};"
            "border-radius: 2px; }"
            f"QSlider::sub-page:horizontal {{ background: {self._ACCENT}; border-radius: 2px; }}"
            f"QSlider::handle:horizontal {{ width: 14px; height: 14px; margin: -5px 0;"
            f"background: {self._ACCENT}; border-radius: 7px; }}"
            f"QSlider::handle:horizontal:hover {{ background: {self._ACCENT_HOVER}; }}")
        self.speed_slider.valueChanged.connect(self._on_speed_changed)
        spd_row.addWidget(self.speed_slider, 1)
        self.speed_label = QLabel(f"{self.speed_slider.value()}%")
        self.speed_label.setStyleSheet(f"color: {self._TEXT}; font-size: 13px;")
        self.speed_label.setFixedWidth(48)
        spd_row.addWidget(self.speed_label)
        lay.addLayout(spd_row)
        lay.addStretch(1)
        return w

    def _on_reset_panels(self, *_):
        """一键重置子面板位置：清除持久化位置与手动移动标记，恢复默认停靠布局"""
        p = self.parent()
        if p is None:
            return
        try:
            _reset_panel_poses(p)
        except Exception:
            return
        try:
            QMessageBox.information(self, "面板位置", "已重置全部子面板位置，恢复默认停靠布局。")
        except Exception:
            pass

    def _on_speed_changed(self, v: int):
        """语速滑块变更：立即持久化到 tts.json，下次合成生效"""
        self.speed_label.setText(f"{v}%")
        agent_tts.save_config(speech_rate=round(v / 100.0, 2))

    # ---------- 音乐播放 ----------
    def _build_music_page(self) -> QWidget:
        w = self._page("音乐")
        lay = self._page_body(w)
        from zhuzhu_Copilot.core import music_player as agent_music
        self._music = player = agent_music.get_player()

        tip = QLabel("上传 MP3 / WAV 等音频到本地库，支持播放进度记忆、音量调节、"
                     "顺序 / 随机 / 单曲循环切换以及打开 AI 面板自动播放。")
        tip.setStyleSheet(f"color: {self._DIM}; font-size: 12px;")
        tip.setWordWrap(True)
        lay.addWidget(tip)

        # 当前曲目 + 状态
        self.music_now = QLabel("未在播放")
        self.music_now.setStyleSheet(f"color: {self._ACCENT}; font-size: 13px; font-weight: 700;")
        self.music_now.setWordWrap(True)
        lay.addWidget(self.music_now)

        # 操作行：上传 / 删除
        ops = QHBoxLayout()
        ops.setSpacing(8)
        up = QPushButton(_line_icon("plus", 16, self._TEXT), "上传歌曲")
        up.setStyleSheet(f"background: {self._ACCENT}; color: #FFFFFF; border: none;"
                         "border-radius: 8px; padding: 7px 16px; font-weight: 700;")
        up.setAutoDefault(False)
        up.clicked.connect(self._music_upload)
        ops.addWidget(up)
        self.music_del = QPushButton(_line_icon("trash", 16, self._TEXT), "删除选中")
        self.music_del.setStyleSheet(
            f"background: {self._PANEL}; color: {self._TEXT}; border: 1px solid {self._BORDER};"
            "border-radius: 8px; padding: 7px 14px; font-weight: 600;")
        self.music_del.setAutoDefault(False)
        self.music_del.clicked.connect(self._music_delete)
        ops.addWidget(self.music_del)
        ops.addStretch(1)
        lay.addLayout(ops)

        # 歌词区：跟随播放自动滚动，当前行按节奏从左往右平滑填充
        from zhuzhu_Copilot.core.lyrics_engine import get_lyrics_engine
        from zhuzhu_Copilot.ui.lyrics_view import LyricsView
        self._lyrics = get_lyrics_engine().bind(player)
        lyr_head = QHBoxLayout()
        lyr_head.setSpacing(8)
        lyr_t = QLabel("歌词")
        lyr_t.setStyleSheet(f"color: {self._TEXT}; font-size: 13px; font-weight: 700;")
        lyr_head.addWidget(lyr_t)
        self.lyrics_import = QPushButton(_line_icon("plus", 14, self._TEXT), "导入歌词")
        self.lyrics_import.setStyleSheet(
            f"background: transparent; color: {self._DIM}; border: 1px solid {self._BORDER};"
            "border-radius: 6px; padding: 3px 10px; font-size: 12px;")
        self.lyrics_import.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lyrics_import.setAutoDefault(False)
        self.lyrics_import.setToolTip("为当前歌曲选择 .lrc 歌词文件，复制入库并绑定（重启后仍有效）")
        self.lyrics_import.clicked.connect(self._lyrics_import)
        lyr_head.addWidget(self.lyrics_import)
        # 桌面歌词开关：始终置顶小窗，当前句纯白左→右填充 + 下一句淡灰预唱；
        # 开启状态持久化，重启后按上次选择自动显示
        from zhuzhu_Copilot.ui.desktop_lyrics import get_desktop_lyrics
        self._desktop_lyrics = get_desktop_lyrics().bind(player)
        remember_on = str(app_identity.qsettings().value(
            "desktop_lyrics/enabled", "0")) == "1"
        if remember_on and not self._desktop_lyrics.isVisible():
            self._desktop_lyrics.show()
            self._desktop_lyrics.raise_()
            self._desktop_lyrics.sync_now()
        self.lyrics_desktop = QPushButton(_line_icon("music", 14, self._TEXT), " 桌面歌词")
        self.lyrics_desktop.setCheckable(True)
        self.lyrics_desktop.setChecked(self._desktop_lyrics.isVisible())
        self.lyrics_desktop.setStyleSheet(
            "QPushButton { background: transparent; color: %s; border: 1px solid %s;"
            "border-radius: 6px; padding: 3px 10px; font-size: 12px; }"
            "QPushButton:hover { border-color: %s; color: %s; background: %s; }"
            "QPushButton:checked { background: %s; color: #FFFFFF; border-color: %s; }"
            % (self._DIM, self._BORDER, self._ACCENT_HOVER, self._TEXT, HOVER,
               self._ACCENT, self._ACCENT))
        self.lyrics_desktop.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lyrics_desktop.setAutoDefault(False)
        self.lyrics_desktop.setToolTip("开启/关闭桌面歌词（状态记忆）。始终置顶最多 2 行："
                               "当前句纯白左→右填充在上、下一句淡灰预唱在下；"
                               "鼠标悬停显示背景版，滚轮调整字号并自动记忆")
        self.lyrics_desktop.toggled.connect(self._toggle_desktop_lyrics)
        lyr_head.addWidget(self.lyrics_desktop)
        lyr_head.addStretch(1)
        lay.addLayout(lyr_head)
        self.lyrics_view = LyricsView()
        self.lyrics_view.set_colors(self._TEXT, self._DIM, self._ACCENT,
                                    HOVER, self._PANEL)
        self.lyrics_view.setFixedHeight(LYRICS_VIEW_H)
        lay.addWidget(self.lyrics_view)
        self._lyrics.lyrics_changed.connect(self._lyrics_update)
        # 高精度歌词跟随定时器：与进度条 500ms 心跳解耦，保证填充/滚动平滑连续
        self._lyrics_timer = QTimer(self)
        self._lyrics_timer.setInterval(LYRICS_STEP_MS)
        self._lyrics_timer.timeout.connect(self._step_lyrics)

        # 歌单
        self.music_list = QListWidget()
        self.music_list.setStyleSheet(
            f"QListWidget {{ background: {self._PANEL}; color: {self._TEXT};"
            f"border: 1px solid {self._BORDER}; border-radius: 8px; padding: 4px; }}"
            f"QListWidget::item {{ padding: 6px 8px; border-radius: 5px; }}"
            f"QListWidget::item:hover {{ background: {self._ACCENT}; }}"
            f"QListWidget::item:selected {{ background: {self._ACCENT}; color: #FFFFFF; }}")
        self.music_list.itemDoubleClicked.connect(
            lambda it: self._music.play_name(it.data(Qt.ItemDataRole.UserRole)))
        lay.addWidget(self.music_list, 1)

        # 播放控制行：上一首 / 播放暂停 / 下一首 / 模式
        ctl = QHBoxLayout()
        ctl.setSpacing(8)
        self.music_prev = QPushButton(_line_icon("prev", 18, self._TEXT), "")
        self.music_toggle = QPushButton(_line_icon("play", 18, "#06281B"), "")
        self.music_next = QPushButton(_line_icon("next", 18, self._TEXT), "")
        self.music_mode = QPushButton(_line_icon("repeat", 16, self._TEXT), " 顺序")
        for b in (self.music_prev, self.music_next, self.music_mode):
            b.setStyleSheet(
                f"background: {self._PANEL}; color: {self._TEXT}; border: 1px solid {self._BORDER};"
                "border-radius: 8px; padding: 8px 14px; font-weight: 700;")
        self.music_toggle.setStyleSheet(
            f"background: {OK}; color: #06281B; border: none;"
            "border-radius: 8px; padding: 8px 20px; font-weight: 700;")
        for b in (self.music_prev, self.music_toggle, self.music_next, self.music_mode):
            b.setAutoDefault(False)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
        self.music_prev.clicked.connect(player.play_prev)
        self.music_toggle.clicked.connect(self._music_toggle_btn)
        self.music_next.clicked.connect(player.play_next)
        self.music_mode.clicked.connect(self._music_cycle_mode)
        ctl.addWidget(self.music_prev)
        ctl.addWidget(self.music_toggle)
        ctl.addWidget(self.music_next)
        ctl.addWidget(self.music_mode)
        ctl.addStretch(1)
        lay.addLayout(ctl)

        # 进度条 + 时间
        prog = QHBoxLayout()
        prog.setSpacing(8)
        self.music_pos = QLabel("00:00")
        self.music_pos.setStyleSheet(f"color: {self._DIM}; font-size: 12px;")
        self.music_pos.setFixedWidth(42)
        prog.addWidget(self.music_pos)
        self.music_slider = QSlider(Qt.Orientation.Horizontal)
        self.music_slider.setRange(0, 0)
        self.music_slider.setStyleSheet(
            f"QSlider::groove:horizontal {{ height: 4px; background: {self._BORDER};"
            "border-radius: 2px; }"
            f"QSlider::sub-page:horizontal {{ background: {self._ACCENT}; border-radius: 2px; }}"
            f"QSlider::handle:horizontal {{ width: 13px; height: 13px; margin: -5px 0;"
            f"background: {self._ACCENT}; border-radius: 7px; }}")
        self.music_slider.sliderMoved.connect(self._music_seek)
        prog.addWidget(self.music_slider, 1)
        self.music_total = QLabel("00:00")
        self.music_total.setStyleSheet(f"color: {self._DIM}; font-size: 12px;")
        self.music_total.setFixedWidth(42)
        prog.addWidget(self.music_total)
        lay.addLayout(prog)

        # 音量
        vol = QHBoxLayout()
        vol.setSpacing(8)
        vl = QLabel("音量")
        vl.setStyleSheet(f"color: {self._TEXT}; font-size: 13px;")
        vl.setFixedWidth(42)
        vol.addWidget(vl)
        self.music_vol = QSlider(Qt.Orientation.Horizontal)
        self.music_vol.setRange(0, 100)
        self.music_vol.setValue(int(player.volume() * 100))
        self.music_vol.setStyleSheet(self.music_slider.styleSheet())
        self.music_vol.sliderMoved.connect(lambda v: player.set_volume(v / 100.0))
        self.music_vol.valueChanged.connect(
            lambda v: self.music_vol_lbl.setText(f"{v}%"))
        vol.addWidget(self.music_vol, 1)
        self.music_vol_lbl = QLabel(f"{int(player.volume() * 100)}%")
        self.music_vol_lbl.setStyleSheet(f"color: {self._TEXT}; font-size: 12px;")
        self.music_vol_lbl.setFixedWidth(40)
        vol.addWidget(self.music_vol_lbl)
        lay.addLayout(vol)

        # 自动播放
        self.music_auto = QCheckBox("打开 AI 面板后自动播放歌曲（自动续播上次位置）")
        self.music_auto.setChecked(player.autoplay())
        self.music_auto.setStyleSheet(f"color: {self._TEXT}; font-size: 13px; spacing: 8px;")
        self.music_auto.toggled.connect(player.set_autoplay)
        lay.addWidget(self.music_auto)

        lay.addStretch(1)
        # 信号绑定：曲目/状态/进度更新
        player.song_changed.connect(self._music_refresh)
        player.state_changed.connect(self._music_refresh)
        player.library_changed.connect(self._music_refresh)
        if hasattr(player, "tick"):
            player.tick.connect(self._music_tick)
        self._music_refresh()
        return w

    # ---- 音乐页交互 ----
    def _music_refresh(self, *_):
        mp = self._music
        try:
            mp.refresh_library()
        except Exception:
            pass
        if not hasattr(self, "music_list"):
            return
        lib = mp.library()
        cur = mp.current()
        self.music_list.blockSignals(True)
        self.music_list.clear()
        for name in lib:
            it = QListWidgetItem(name)
            if name == cur:
                it.setText(f"▶ {name}")
            it.setData(Qt.ItemDataRole.UserRole, name)
            self.music_list.addItem(it)
        if cur in lib:
            self.music_list.setCurrentRow(lib.index(cur))
        self.music_list.blockSignals(False)
        self.music_del.setEnabled(bool(lib))
        self.music_prev.setEnabled(bool(lib))
        self.music_next.setEnabled(bool(lib))
        if cur:
            self.music_now.setText(f"当前：{cur}")
        else:
            self.music_now.setText("未在播放")
        playing = mp.is_playing()
        self.music_toggle.setIcon(
            _line_icon("pause" if playing and not mp.is_paused() else "play",
                       18, "#06281B" if playing else "#06281B"))
        self.music_toggle.setToolTip("暂停" if playing and not mp.is_paused() else "播放")
        self.music_mode.setText(f" {mp.mode_label()}")
        self.music_mode.setIcon(
            _line_icon("shuffle" if mp.mode() == "random"
                       else "repeat", 16, self._TEXT))
        # blockSignals 保护：刷新勾选状态不触发 toggled，避免意外覆盖用户设置的 autoplay
        self.music_auto.blockSignals(True)
        self.music_auto.setChecked(mp.autoplay())
        self.music_auto.blockSignals(False)
        # 传真实播放位置：无参 _music_tick() 会让 cur=0，拖动音量条触发刷新时
        # 进度条被 setValue(0) 强行跳回开头
        self._music_tick(mp.current_position(), mp.duration())
        self._lyrics_update()

    def _lyrics_update(self, *_):
        """刷新歌词视图数据（当前歌曲行列表 / 导入按钮可用态）"""
        if not hasattr(self, "lyrics_view"):
            return
        lines = self._lyrics.lines()
        self.lyrics_view.set_lines(lines)
        self.lyrics_import.setToolTip(
            "为当前歌曲选择 .lrc 歌词文件" if self._music.current()
            else "请先选择要导入歌词的歌曲")
        self.lyrics_import.setEnabled(bool(self._music.current()))
        self._ensure_lyrics_timer()

    def _toggle_desktop_lyrics(self, on: bool):
        """桌面歌词开关：显示/隐藏；跟随由桌面歌词自治驱动；开关状态持久化"""
        win = getattr(self, "_desktop_lyrics", None)
        if win is None:
            return
        if on:
            win.show()
            win.raise_()
            win.sync_now()
        else:
            win.hide()
        app_identity.qsettings().setValue(
            "desktop_lyrics/enabled", "1" if on else "0")

    def _lyrics_want(self) -> bool:
        """是否需要内嵌歌词跟随：当前歌曲有歌词"""
        if not hasattr(self, "_lyrics_timer"):
            return False
        return hasattr(self, "lyrics_view") and self._lyrics.have_lyrics()

    def _ensure_lyrics_timer(self):
        """按播放状态与内嵌歌词展示需求启停高精度跟随定时器"""
        mp = self._music
        want = bool(mp.is_playing() and not mp.is_paused()) and self._lyrics_want()
        if want and not self._lyrics_timer.isActive():
            self._lyrics_timer.start()
        elif not want and self._lyrics_timer.isActive():
            self._lyrics_timer.stop()

    def _step_lyrics(self):
        """高精度歌词跟随（60ms 步进）：连续填充进度 + 行切换滚动，避免 500ms 跳变卡顿"""
        mp = self._music
        if not (mp.is_playing() and not mp.is_paused()):
            self._lyrics_timer.stop()
            return
        pos = mp.current_position_f()
        if hasattr(self, "lyrics_view") and self._lyrics.have_lyrics():
            idx, fill = self._lyrics.locate(pos)
            self.lyrics_view.set_current(idx, fill)

    def _music_tick(self, cur: int = 0, total: int = 0):
        if not hasattr(self, "music_slider"):
            return
        if not cur is None:
            mp = self._music
            if total <= 0:
                total = mp.duration()
            if total:
                self.music_slider.setRange(0, total)
            if not self.music_slider.isSliderDown():
                self.music_slider.setValue(int(cur))
            self.music_pos.setText(_fmt_sec(int(cur)))
            self.music_total.setText(_fmt_sec(int(total)))
            # 歌词节奏跟随移交高精度定时器 _step_lyrics（60ms 连续驱动，避免 500ms 心跳跳变）
            self._ensure_lyrics_timer()

    def _lyrics_import(self, *_):
        """为当前歌曲导入 .lrc 歌词文件（复制入库并绑定持久化）"""
        song = self._music.current()
        if not song:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "选择歌词文件", "", "歌词文件 (*.lrc);;所有文件 (*.*)")
        if not path:
            return
        if not self._lyrics.import_lyric(song, path):
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "导入歌词", "无法导入：文件不是有效的 LRC 歌词或读取失败。")
            return
        self._lyrics_update()

    def _music_toggle_btn(self, *_):
        mp = self._music
        if mp.is_playing() and not mp.is_paused():
            mp.toggle()
        else:
            mp.toggle()   # 统一 toggle 处理开始/暂停/续播
        self._music_refresh()

    def _music_cycle_mode(self, *_):
        mp = self._music
        from zhuzhu_Copilot.core.music_player import (
            MODE_LOOP_ONE,
            MODE_RANDOM,
            MODE_SEQUENCE,
        )
        order = (MODE_SEQUENCE, MODE_RANDOM, MODE_LOOP_ONE)
        nxt = order[(order.index(mp.mode()) + 1) % len(order)]
        mp.set_mode(nxt)
        self._music_refresh()

    def _music_seek(self, v: int):
        self._music.seek(v)

    def _music_upload(self, *_):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择音频文件", "",
            "音频文件 (*.mp3 *.wav *.ogg *.flac *.m4a);;所有文件 (*.*)")
        if not files:
            return
        ok_names = self._music.import_files(files)
        if ok_names:
            self._music_refresh()
            # 定位到第一个新歌
            lib = self._music.library()
            for i, n in enumerate(lib):
                if n in ok_names:
                    self.music_list.setCurrentRow(i)
                    break

    def _music_delete(self, *_):
        it = self.music_list.currentItem()
        if it is None:
            return
        name = it.data(Qt.ItemDataRole.UserRole)
        if not name:
            return
        self._lyrics.forget(name)
        self._music.delete_song(name)
        self._music_refresh()

    def _build_skill_page(self) -> QWidget:
        w = self._page("技能")
        lay = self._page_body(w)
        sub = QLabel(
            "技能按工作流隔离：切换到/激活某工作流时，只加载该工作流启用的技能。"
            "选择下方工作流作用域后，可逐项启用/禁用内置与用户私有技能（即时生效）；"
            "导入/删除同样在技能目录即时生效。")
        sub.setStyleSheet(f"color: {self._DIM}; font-size: 12px;")
        sub.setWordWrap(True)
        lay.addWidget(sub)
        # 工作流作用域选择
        scope = QHBoxLayout()
        scope.setSpacing(8)
        scope.addWidget(QLabel("工作流作用域:"))
        self.skill_wf_combo = QComboBox()
        self.skill_wf_combo.setMinimumWidth(240)
        if agent_ui_ux.is_custom_package_active():
            # 液态玻璃：透明玻璃底 + 白色受光边 + 黑字 + 下拉箭头（自带样式会屏蔽应用级箭头）
            # 弹出视图用半透明玻璃渐变（非不透明 PANEL），保证液态玻璃观感
            self.skill_wf_combo.setStyleSheet(
                _glass_combo_qss(self._TEXT, self._DIM, radius="6px")
                + _glass_combo_view_qss(self._TEXT))
        else:
            self.skill_wf_combo.setStyleSheet(
                f"QComboBox {{ background: {self._PANEL}; color: {self._TEXT};"
                f"border: 1px solid {self._BORDER}; border-radius: 6px; padding: 6px 10px; }}"
                f"QComboBox QAbstractItemView {{ background: {self._PANEL}; color: {self._TEXT};"
                f"border: 1px solid {self._BORDER}; selection-background-color: {self._PANEL2}; }}")
        self.skill_wf_combo.currentIndexChanged.connect(self._on_skill_wf_changed)
        scope.addWidget(self.skill_wf_combo)
        scope.addStretch(1)
        lay.addLayout(scope)
        # 技能启停列表（勾选=启用；取消=禁用）
        self.skill_list = QListWidget()
        self.skill_list.setStyleSheet(
            f"QListWidget {{ background: {self._PANEL}; color: {self._TEXT};"
            f"border: 1px solid {self._BORDER}; border-radius: 8px; padding: 6px; }}"
            f"QListWidget::item {{ padding: 7px 10px; border-radius: 6px; }}"
            f"QListWidget::item:selected {{ background: {self._PANEL2};"
            f"color: {self._ACCENT_HOVER}; }}")
        self.skill_list.itemChanged.connect(self._on_skill_item_changed)
        lay.addWidget(self.skill_list, 1)
        self.skill_feedback = QLabel("")
        self.skill_feedback.setStyleSheet(f"color: {self._DIM}; font-size: 12px;")
        self.skill_feedback.setWordWrap(True)
        lay.addWidget(self.skill_feedback)
        row = QHBoxLayout()
        row.setSpacing(8)
        imp = QPushButton(_std_icon(QStyle.StandardPixmap.SP_FileDialogNewFolder),
                          "导入市场标准技能（SKILL.md 或 zip 包）…")
        imp.setStyleSheet(f"background: {self._PANEL}; color: {self._TEXT};"
                          f"border: 1px solid {self._BORDER}; border-radius: 8px;"
                          "padding: 8px 16px; font-weight: 600;")
        imp.setAutoDefault(False)
        imp.setToolTip("选择市场标准的 SKILL.md 文件或含 SKILL.md 的 zip 包，"
                       "导入到技能目录并即时生效（/技能名 或对话描述即可调用）")
        imp.clicked.connect(self._import_skill)
        row.addWidget(imp)
        del_skill = QPushButton(_std_icon(QStyle.StandardPixmap.SP_TrashIcon), "删除技能…")
        del_skill.setStyleSheet(f"background: {self._PANEL}; color: {self._DIM};"
                                f"border: 1px solid {self._BORDER}; border-radius: 8px;"
                                "padding: 8px 16px; font-weight: 600;")
        del_skill.setAutoDefault(False)
        del_skill.setToolTip("删除用户导入/创建的技能（连同 SKILL.md 与附属文件）；内置技能不可删除")
        del_skill.clicked.connect(self._delete_skill)
        row.addWidget(del_skill)
        wf_skill = QPushButton("分配工作流…")
        wf_skill.setStyleSheet(f"background: {self._PANEL}; color: {self._TEXT};"
                               f"border: 1px solid {self._BORDER}; border-radius: 8px;"
                               "padding: 8px 16px; font-weight: 600;")
        wf_skill.setAutoDefault(False)
        wf_skill.setToolTip("指定技能可用的一个或多个工作流（空=全局所有工作流）；"
                            "绑定后其他工作流不再加载该技能")
        wf_skill.clicked.connect(self._on_skill_workflows)
        row.addWidget(wf_skill)
        row.addStretch(1)
        lay.addLayout(row)
        self._reload_skill_wf_combo()
        return w

    def _reload_skill_wf_combo(self):
        """刷新工作流作用域下拉（默认选中当前激活工作流）并加载对应技能清单"""
        if not hasattr(self, "skill_wf_combo") or self.skill_wf_combo is None:
            return
        active = agent_workflow.active_workflow()
        self.skill_wf_combo.blockSignals(True)
        self.skill_wf_combo.clear()
        for wf in agent_workflow.list_workflows():
            label = wf["name"]
            if wf["is_default"]:
                label += "（默认）"
            elif wf["active"]:
                label += "（激活中）"
            elif not wf.get("enabled", True):
                label += "（已禁用）"
            self.skill_wf_combo.addItem(label, wf["name"])
        idx = self.skill_wf_combo.findData(active)
        if idx < 0:
            idx = 0
        self.skill_wf_combo.setCurrentIndex(idx)
        self.skill_wf_combo.blockSignals(False)
        self._reload_skill_list(active)

    def _current_skill_workflow(self) -> str:
        if not hasattr(self, "skill_wf_combo") or self.skill_wf_combo is None:
            return agent_workflow.active_workflow()
        return self.skill_wf_combo.currentData() or ""

    def _on_skill_wf_changed(self, *_):
        self._reload_skill_list(self._current_skill_workflow())

    def _reload_skill_list(self, workflow: str):
        """按工作流加载技能启停清单：[内置]/[用户]/[工作流专属] 标注 + 勾选启用状态"""
        if not hasattr(self, "skill_list") or self.skill_list is None:
            return
        self.skill_list.blockSignals(True)
        self.skill_list.clear()
        for s in agent_skills.list_skills_for_workflow(workflow):
            if s["workflow_skill"]:
                tag = "工作流专属"
            elif s["builtin"]:
                tag = "内置"
            else:
                tag = "用户"
            bind = f"   [工作流: {', '.join(s['workflows'])}]" if s.get("workflows") else ""
            text = f"{s['name']}  [{tag}]{bind}  {s['description']}"
            item = QListWidgetItem(text)
            item.setToolTip(s["description"])
            if s["workflow_skill"]:
                # 工作流专属技能恒启用（该工作流自带），不允许在此停用
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
                item.setForeground(QColor(self._DIM))
            else:
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if s["enabled"]
                                   else Qt.CheckState.Unchecked)
                item.setData(Qt.ItemDataRole.UserRole, s["name"])
            self.skill_list.addItem(item)
        self.skill_list.blockSignals(False)

    def _on_skill_item_changed(self, item):
        """勾选/取消勾选技能 → 写入该工作流的 skill_states（即时生效）"""
        wf = self._current_skill_workflow()
        if not wf:
            return
        name = item.data(Qt.ItemDataRole.UserRole)
        if not name:
            return
        enabled = item.checkState() == Qt.CheckState.Checked
        ok, msg = agent_workflow.set_skill_state(wf, name, enabled)
        if hasattr(self, "skill_feedback"):
            color = self._ACCENT if ok else self._DANGER_HOVER
            self.skill_feedback.setStyleSheet(f"color: {color}; font-size: 12px;")
            self.skill_feedback.setText(msg if ok else f"操作失败: {msg}")

    def _on_skill_workflows(self, *_):
        """指定技能可用的工作流（多选/逗号分隔；空=全局）"""
        row = self.skill_list.currentRow()
        if row < 0:
            QMessageBox.information(self, "提示", "请先选择一个技能")
            return
        item = self.skill_list.item(row)
        name = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not name:
            QMessageBox.information(self, "提示", "工作流专属技能由所属工作流自带，无需分配")
            return
        current = agent_skills.get_workflow_binding("skill", name)
        ws, ok = self._ask_workflow_names(f"分配技能「{name}」的工作流", ", ".join(current))
        if not ok:
            return
        ok2, msg = agent_skills.set_workflow_binding("skill", name, ws)
        if ok2:
            QMessageBox.information(self, "技能", msg)
        else:
            QMessageBox.warning(self, "操作失败", msg)
        self._reload_skill_list(self._current_skill_workflow())

    def _build_mcp_page(self) -> QWidget:
        w = self._page("MCP 服务器")
        lay = self._page_body(w)
        sub = QLabel("配置外部工具服务器（stdio 本地命令 / sse 远程 URL），保存后自动重连，AI 即可调用其工具")
        sub.setStyleSheet(f"color: {self._DIM}; font-size: 12px;")
        sub.setWordWrap(True)
        lay.addWidget(sub)
        self.mcp_list = QListWidget()
        self.mcp_list.setStyleSheet(
            f"QListWidget {{ background: {self._PANEL}; color: {self._TEXT};"
            f"border: 1px solid {self._BORDER}; border-radius: 8px; padding: 6px; }}"
            f"QListWidget::item {{ padding: 8px 10px; border-radius: 6px; }}"
            f"QListWidget::item:selected {{ background: {self._PANEL2};"
            f"color: {self._ACCENT_HOVER}; }}")
        lay.addWidget(self.mcp_list, 1)
        row = QHBoxLayout()
        row.setSpacing(8)
        add_b = QPushButton(_std_icon(QStyle.StandardPixmap.SP_FileDialogNewFolder), "添加")
        add_b.setStyleSheet(f"background: {self._ACCENT}; color: #FFFFFF;"
                            "border: none; border-radius: 8px; padding: 7px 16px; font-weight: 700;")
        add_b.setAutoDefault(False)
        add_b.clicked.connect(self._on_mcp_add)
        edit_b = QPushButton("编辑")
        edit_b.setStyleSheet(f"background: {self._PANEL}; color: {self._TEXT};"
                             f"border: 1px solid {self._BORDER}; border-radius: 8px;"
                             "padding: 7px 16px; font-weight: 600;")
        edit_b.setAutoDefault(False)
        edit_b.clicked.connect(self._on_mcp_edit)
        del_b = QPushButton(_line_icon("trash", 16), "删除")
        del_b.setStyleSheet(f"background: {self._PANEL}; color: {self._DIM};"
                            f"border: 1px solid {self._BORDER}; border-radius: 8px;"
                            "padding: 7px 16px; font-weight: 600;")
        del_b.setAutoDefault(False)
        del_b.clicked.connect(self._on_mcp_delete)
        row.addWidget(add_b)
        row.addWidget(edit_b)
        row.addWidget(del_b)
        row.addStretch(1)
        lay.addLayout(row)
        self._reload_mcp_list()
        return w

    def _build_agent_page(self) -> QWidget:
        """Agent 管理：主 Agent（每个对话流 agent.py 人格）+ 自定义子 Agent（sub_<名>）"""
        w = self._page("Agent 管理")
        lay = self._page_body(w)
        sub = QLabel(
            "管理每个对话流（工作流）的主 Agent 与自定义子 Agent：主 Agent 人格由该对话流"
            "agent.py 定义（可编辑）；子 Agent 以 sub_<名> 注册进指定对话流，对话中可直接调用。")
        sub.setStyleSheet(f"color: {self._DIM}; font-size: 12px;")
        sub.setWordWrap(True)
        lay.addWidget(sub)

        # 对话流选择
        wf_row = QHBoxLayout()
        wf_lbl = QLabel("对话流")
        wf_lbl.setStyleSheet(f"color: {self._TEXT}; font-size: 13px;")
        wf_row.addWidget(wf_lbl)
        self.agent_wf = QComboBox()
        self.agent_wf.currentIndexChanged.connect(self._reload_agent_view)
        wf_row.addWidget(self.agent_wf, 1)
        lay.addLayout(wf_row)

        # ---- 主 Agent ----
        mh = QLabel("主 Agent")
        mh.setStyleSheet(f"color: {self._ACCENT}; font-size: 13px; font-weight: 700;")
        lay.addWidget(mh)
        self.agent_main_info = QLabel("")
        self.agent_main_info.setStyleSheet(f"color: {self._DIM}; font-size: 12px;")
        self.agent_main_info.setWordWrap(True)
        lay.addWidget(self.agent_main_info)
        mrow = QHBoxLayout()
        mrow.setSpacing(8)
        m_edit = QPushButton("编辑主 Agent（agent.py）")
        m_edit.setStyleSheet(f"background: {self._ACCENT}; color: #FFFFFF; border: none;"
                             "border-radius: 8px; padding: 7px 14px; font-weight: 700;")
        m_edit.setAutoDefault(False)
        m_edit.clicked.connect(self._on_agent_edit_main)
        mrow.addWidget(m_edit)
        mrow.addStretch(1)
        lay.addLayout(mrow)

        # ---- 子 Agent ----
        sh = QLabel("子 Agent（本对话流注册）")
        sh.setStyleSheet(f"color: {self._ACCENT}; font-size: 13px; font-weight: 700;")
        lay.addWidget(sh)
        self.agent_sub_list = QListWidget()
        self.agent_sub_list.setStyleSheet(
            f"QListWidget {{ background: {self._PANEL}; color: {self._TEXT};"
            f"border: 1px solid {self._BORDER}; border-radius: 8px; padding: 6px; }}"
            f"QListWidget::item {{ padding: 8px 10px; border-radius: 6px; }}"
            f"QListWidget::item:selected {{ background: {self._PANEL2};"
            f"color: {self._ACCENT_HOVER}; }}")
        lay.addWidget(self.agent_sub_list, 1)
        srow = QHBoxLayout()
        srow.setSpacing(8)
        s_add = QPushButton("注册子 Agent")
        s_add.setStyleSheet(f"background: {self._ACCENT}; color: #FFFFFF; border: none;"
                            "border-radius: 8px; padding: 7px 14px; font-weight: 700;")
        s_add.setAutoDefault(False)
        s_add.clicked.connect(self._on_agent_add_sub)
        s_del = QPushButton("删除选中")
        s_del.setStyleSheet(f"background: {self._PANEL}; color: {self._TEXT};"
                            f"border: 1px solid {self._BORDER}; border-radius: 8px;"
                            "padding: 7px 14px; font-weight: 600;")
        s_del.setAutoDefault(False)
        s_del.clicked.connect(self._on_agent_del_sub)
        srow.addWidget(s_add)
        srow.addWidget(s_del)
        srow.addStretch(1)
        lay.addLayout(srow)
        self._reload_agent_view()
        return w

    # ---------- Agent 管理交互 ----------
    def _agent_current_wf(self) -> str:
        """Agent 页当前选中的对话流名"""
        return str(self.agent_wf.currentData() or "")

    def _reload_agent_view(self, *_):
        """填充对话流下拉（保留当前选择）+ 刷新主 Agent 信息与子 Agent 列表"""
        if not hasattr(self, "agent_wf"):
            return
        cur = self.agent_wf.currentData()
        self.agent_wf.blockSignals(True)
        self.agent_wf.clear()
        for wf in agent_workflow.list_workflows():
            self.agent_wf.addItem(wf["name"], wf["name"])
        if cur is not None:
            idx = self.agent_wf.findData(cur)
            if idx >= 0:
                self.agent_wf.setCurrentIndex(idx)
        self.agent_wf.blockSignals(False)
        self._refresh_agent_main()
        self._refresh_agent_subs()

    def _refresh_agent_main(self):
        name = self._agent_current_wf()
        if not name:
            self.agent_main_info.setText("请选择对话流")
            return
        wf = next((x for x in agent_workflow.list_workflows() if x["name"] == name), None)
        files = ", ".join(wf["core_files"]) if wf and wf["core_files"] else "—"
        try:
            custom = (agent_workflow.workflow_dir(name) / "agent.py").is_file()
        except Exception:
            custom = False
        self.agent_main_info.setText(
            f"主 Agent 人格：{'自定义（agent.py）' if custom else '内置默认'}\n核心文件：{files}")

    def _refresh_agent_subs(self):
        if not hasattr(self, "agent_sub_list"):
            return
        name = self._agent_current_wf()
        self.agent_sub_list.clear()
        for it in agent_subagent.registered_subagents(name):
            desc = (it["description"] or "").strip()
            goal = (it["goal"] or "").strip()
            item = QListWidgetItem(it["name"])
            item.setText(f"{it['name']}  {desc[:30]}\n    目标: {goal[:60]}")
            item.setToolTip(goal)
            item.setData(Qt.ItemDataRole.UserRole, it["name"])
            self.agent_sub_list.addItem(item)

    def _on_agent_edit_main(self, *_):
        """编辑选中对话流的主 Agent 人格（agent.py）"""
        name = self._agent_current_wf()
        if not name:
            QMessageBox.information(self, "提示", "请先选择对话流")
            return
        rok, content = agent_workflow.read_core_file(name, "agent.py")
        if not rok:
            # 内置对话流未添加 agent.py：引导到工作流页添加
            QMessageBox.information(
                self, "提示",
                f"对话流「{name}」尚无 agent.py 自定义文件。\n"
                "请到「工作流」页选中该对话流，用「添加」加入 agent.py 后即可编辑主 Agent 人格。")
            return
        text, ok2 = _MultiLineInputDialog.get(
            self, f"编辑主 Agent · {name}", "修改主 Agent 人格（agent.py）：",
            content, wrap=False, min_size=(680, 480))
        if not ok2:
            return
        wok, msg = agent_workflow.write_core_file(name, "agent.py", text)
        if wok:
            self._wf_changed = True
            QMessageBox.information(self, "已保存", msg)
        else:
            QMessageBox.warning(self, "保存失败", msg)
        self._refresh_agent_main()

    def _on_agent_add_sub(self, *_):
        """注册子 Agent 到选中对话流（subagents.json）"""
        name = self._agent_current_wf()
        if not name:
            QMessageBox.information(self, "提示", "请先选择对话流")
            return
        sname, ok1 = QInputDialog.getText(self, "注册子 Agent", "子 Agent 名称（如 sub_reviewer）：")
        if not ok1 or not sname.strip():
            return
        desc, ok2 = QInputDialog.getText(self, "注册子 Agent", "简短描述（列表展示用）：")
        if not ok2:
            return
        goal, ok3 = _MultiLineInputDialog.get(
            self, "注册子 Agent", "任务指令（goal，必填）：", "", wrap=True, min_size=(520, 200))
        if not ok3 or not goal.strip():
            return
        allowed, ok4 = QInputDialog.getText(
            self, "注册子 Agent",
            "允许工具（逗号分隔，如 read_file,write_file；留空=全部白名单）：")
        if not ok4:
            return
        ok, msg = agent_subagent.register_subagent(
            sname.strip(), desc.strip(), goal.strip(), allowed.strip(), workflow=name)
        if ok:
            QMessageBox.information(self, "注册成功", msg)
        else:
            QMessageBox.warning(self, "注册失败", msg)
        self._refresh_agent_subs()

    def _on_agent_del_sub(self, *_):
        """从选中对话流删除子 Agent"""
        name = self._agent_current_wf()
        it = self.agent_sub_list.currentItem()
        if not name or it is None:
            QMessageBox.information(self, "提示", "请选择要删除的子 Agent")
            return
        sname = str(it.data(Qt.ItemDataRole.UserRole) or "")
        ret = QMessageBox.question(
            self, "确认删除", f"确定从对话流「{name}」删除子 Agent「{sname}」？")
        if ret != QMessageBox.StandardButton.Yes:
            return
        ok, msg = agent_subagent.unregister_subagent(sname, workflow=name)
        if ok:
            QMessageBox.information(self, "已删除", msg)
        else:
            QMessageBox.warning(self, "删除失败", msg)
        self._refresh_agent_subs()

    def _build_workflow_page(self) -> QWidget:
        """工作流管理（Cordis）：列表 + 自然语言 AI 生成 + 添加/编辑/删除核心文件 + 切换/禁用/删除"""
        w = self._page("工作流")
        lay = self._page_body(w)
        sub = QLabel(
            "管理自定义 Agent 工作流（Cordis）：默认使用内置工作流，可用自然语言让 AI 生成核心文件，"
            "从下方「内置工作流」一键创建预设，或进入内置工作流添加/编辑核心文件；切换即热插拔生效。"
            "默认工作流 _default 内置只读，可添加/删除用户覆盖文件；用户工作流可整体启用/禁用或删除。")
        sub.setStyleSheet(f"color: {self._DIM}; font-size: 12px;")
        sub.setWordWrap(True)
        lay.addWidget(sub)
        self.wf_list = QListWidget()
        self.wf_list.setStyleSheet(
            f"QListWidget {{ background: {self._PANEL}; color: {self._TEXT};"
            f"border: 1px solid {self._BORDER}; border-radius: 8px; padding: 6px; }}"
            f"QListWidget::item {{ padding: 8px 10px; border-radius: 6px; }}"
            f"QListWidget::item:selected {{ background: {self._PANEL2};"
            f"color: {self._ACCENT_HOVER}; }}")
        lay.addWidget(self.wf_list, 1)

        def _btn(text, accent=False, tip=""):
            b = QPushButton(text)
            if accent:
                b.setStyleSheet(f"background: {self._ACCENT}; color: #FFFFFF; border: none;"
                                "border-radius: 8px; padding: 7px 14px; font-weight: 700;")
            else:
                b.setStyleSheet(f"background: {self._PANEL}; color: {self._TEXT};"
                                f"border: 1px solid {self._BORDER}; border-radius: 8px;"
                                "padding: 7px 14px; font-weight: 600;")
            b.setAutoDefault(False)
            if tip:
                b.setToolTip(tip)
            return b

        row = QHBoxLayout()
        row.setSpacing(8)
        nl_b = _btn("创建", accent=True,
                    tip="创建（自然语言）：输入自然语言描述，AI 直接生成工作流核心文件")
        nl_b.clicked.connect(self._on_workflow_create)
        add_b = _btn("添加", tip="添加核心文件：为选中工作流添加单个核心文件（含内置工作流）")
        add_b.clicked.connect(self._on_workflow_add_file)
        edit_b = _btn("编辑", tip="编辑文件：编辑选中工作流的某个核心文件")
        edit_b.clicked.connect(self._on_workflow_edit_file)
        del_file_b = _btn("删文件", tip="删除文件：删除选中工作流的某个核心文件（回退内置）")
        del_file_b.clicked.connect(self._on_workflow_delete_file)
        switch_b = _btn("切换", tip="切换/激活：把选中工作流切换为激活（立即生效）")
        switch_b.clicked.connect(self._on_workflow_switch)
        toggle_b = _btn("开关", tip="启用/禁用：启用或禁用选中用户工作流（默认工作流不可禁用）")
        toggle_b.clicked.connect(self._on_workflow_toggle)
        del_b = _btn("删除", tip="删除：删除选中用户工作流（默认工作流不可删除）")
        del_b.clicked.connect(self._on_workflow_delete)
        for b in (nl_b, add_b, edit_b, del_file_b, switch_b, toggle_b, del_b):
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)

        # 内置工作流预设：随包提供，一键创建（含核心文件与多个注册式子 Agent）
        preset_row = QHBoxLayout()
        preset_row.setSpacing(8)
        preset_lbl = QLabel("内置工作流")
        preset_lbl.setStyleSheet(f"color: {self._DIM}; font-size: 12px;")
        preset_row.addWidget(preset_lbl)
        self.wf_preset_combo = QComboBox()
        self.wf_preset_combo.setStyleSheet(_QCOMBO)   # 主题自适应下拉（浅色下避免黑底黑字）
        self.wf_preset_combo.setMinimumWidth(260)
        preset_row.addWidget(self.wf_preset_combo, 1)
        preset_b = _btn("创建内置", accent=True,
                        tip="从内置预设创建工作流（如三省六部制度：中书省主 Agent + 门下省/尚书省"
                            "与吏户礼兵刑工六部，共 8 个注册式子 Agent，编队共用共同上下文空间）")
        preset_b.clicked.connect(self._on_workflow_create_builtin)
        preset_row.addWidget(preset_b)
        lay.addLayout(preset_row)
        self._reload_builtin_presets()
        self._reload_workflow_list()
        return w

    def _reload_builtin_presets(self):
        """刷新内置工作流预设下拉（标注是否已创建）"""
        if getattr(self, "wf_preset_combo", None) is None:
            return
        self.wf_preset_combo.clear()
        for p in agent_workflow.list_builtin_workflows():
            tag = "（已创建）" if p.get("created") else ""
            self.wf_preset_combo.addItem(f"{p.get('display_name') or p.get('id')}{tag}",
                                         p.get("id"))

    def _on_workflow_create_builtin(self, *_):
        """一键创建选中的内置工作流（复制预设核心文件并登记注册式子 Agent）"""
        pid = str(self.wf_preset_combo.currentData() or "")
        if not pid:
            QMessageBox.information(self, "提示", "暂无可创建的内置工作流预设")
            return
        preset = next((p for p in agent_workflow.list_builtin_workflows()
                       if p.get("id") == pid), {})
        wf_name = preset.get("workflow_name") or pid
        agents = preset.get("agents") or []
        detail = (f"创建内置工作流「{preset.get('display_name') or pid}」？\n\n"
                  f"工作流名：{wf_name}\n"
                  + (f"注册式子 Agent：{len(agents)} 个\n" if agents else "")
                  + "\n创建后可在列表中选择该工作流并点「切换」激活生效。")
        if QMessageBox.question(self, "创建内置工作流", detail) \
                != QMessageBox.StandardButton.Yes:
            return
        ok, msg = agent_workflow.create_builtin_workflow(pid)
        if ok:
            self._wf_changed = True
            QMessageBox.information(self, "创建内置工作流", msg)
        else:
            QMessageBox.warning(self, "创建内置工作流失败", msg)
        self._reload_builtin_presets()
        self._reload_workflow_list()

    def _reload_workflow_list(self):
        if not hasattr(self, "wf_list") or self.wf_list is None:
            return   # 工作流页尚未懒加载构建时跳过
        self.wf_list.clear()
        for wf in agent_workflow.list_workflows():
            if wf["is_default"]:
                tag = "默认"
            elif wf["active"]:
                tag = "激活"
            elif not wf.get("enabled", True):
                tag = "禁用"
            else:
                tag = "未激活"
            desc = (wf.get("description") or "").strip()
            if len(desc) > 40:
                desc = desc[:40] + "…"
            files = ", ".join(wf["core_files"]) if wf["core_files"] else "—"
            self.wf_list.addItem(f"{wf['name']}  [{tag}]  {desc}\n    核心文件: {files}")

    def _current_workflow(self) -> dict:
        row = self.wf_list.currentRow()
        wfs = agent_workflow.list_workflows()
        if 0 <= row < len(wfs):
            return wfs[row]
        return None

    # ---------- 生成进度条（工作流/插件共用） ----------
    def _pbar_open(self, title: str, label: str):
        """打开确定进度条（0-100）并启动渐进动画，避免 LLM 阻塞阶段看起来卡死"""
        dlg = QProgressDialog(label, "取消", 0, 100, self)
        dlg.setWindowTitle(title)
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setMinimumWidth(380)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        dlg.setValue(0)
        dlg.show()
        self._pbar = dlg
        self._pbar_val = 0
        if self._pbar_timer is None:
            self._pbar_timer = QTimer(self)
            self._pbar_timer.setInterval(250)
            self._pbar_timer.timeout.connect(self._pbar_tick)
        self._pbar_timer.start()

    def _pbar_update(self, pct: int, text: str):
        """阶段进度更新：百分比取更大值（真实阶段），文案同步"""
        dlg = getattr(self, "_pbar", None)
        if dlg is None:
            return
        if pct > self._pbar_val:
            self._pbar_val = pct
            dlg.setValue(pct)
        if text:
            dlg.setLabelText(text)

    def _pbar_tick(self):
        """渐进动画：阶段之间（LLM 阻塞）无更新时让进度条缓慢爬升到 95%，避免看起来卡死"""
        dlg = getattr(self, "_pbar", None)
        if dlg is None:
            return
        if self._pbar_val < 95:
            self._pbar_val += 1
            dlg.setValue(self._pbar_val)

    def _pbar_close(self):
        timer = getattr(self, "_pbar_timer", None)
        if timer is not None:
            timer.stop()
        dlg = getattr(self, "_pbar", None)
        if dlg is not None:
            dlg.setValue(100)
            dlg.close()
            self._pbar = None

    def _on_workflow_create(self, *_):
        """自然语言描述 → 后台线程 AI 生成工作流核心文件（真实 API），生成期间显示百分比进度"""
        text, ok = _MultiLineInputDialog.get(
            self, "创建工作流", "用自然语言描述你想要的 AI Agent 工作流（做什么、需要哪些能力）：",
            "帮我创建一个会写周报的 Agent：自动总结当天工作，生成周报 Markdown 文件")
        if not ok or not text.strip():
            return
        self._pbar_open("创建工作流", "正在分析需求并设计工作流…")
        threading.Thread(target=self._workflow_worker, args=(text.strip(),), daemon=True).start()

    def _on_workflow_cancel(self):
        """用户取消：关闭进度框（后台 daemon 线程会自行结束/被兜底收尾），避免"卡死"感"""
        self._pbar_close()

    def _on_workflow_progress(self, pct: int, text: str):
        """生成期间更新进度百分比与文案"""
        self._pbar_update(pct, text)

    def _workflow_worker(self, desc: str):
        """后台生成工作流。任何异常都必须回传 done，否则模态进度框永不关闭造成"卡死"；
        LLM 调用内部自带超时，保证线程不会无限阻塞。"""
        try:
            ok, msg = agent_workflow.create_workflow_from_nl(
                desc, on_status=lambda p, s: self.workflow_progress.emit(p, s))
            self.workflow_done.emit("1" if ok else "0", msg)
        except Exception as e:
            self.workflow_done.emit("0", f"创建工作流异常: {e}")

    def _on_workflow_done(self, ok: str, msg: str):
        self._pbar_close()
        if ok == "1":
            self._wf_changed = True
            QMessageBox.information(self, "创建工作流", msg)
        else:
            QMessageBox.warning(self, "创建工作流失败", msg)
        self._reload_workflow_list()

    def _on_workflow_add_file(self, *_):
        wf = self._current_workflow()
        if not wf:
            QMessageBox.information(self, "提示", "请先选择要添加核心文件的工作流")
            return
        file, ok = QInputDialog.getItem(
            self, "添加核心文件", f"选择要添加到 {wf['name']} 的核心文件：",
            ["tools.py", "agent.py", "llm.py", "mcp.json", "skills/", "plugins/"], 0, False)
        if not ok:
            return
        ok2, msg = agent_workflow.add_core_file(wf["name"], file.rstrip("/"))
        if ok2:
            self._wf_changed = True
            QMessageBox.information(self, "添加核心文件", msg)
        else:
            QMessageBox.warning(self, "添加失败", msg)
        self._reload_workflow_list()

    def _on_workflow_edit_file(self, *_):
        wf = self._current_workflow()
        if not wf:
            QMessageBox.information(self, "提示", "请先选择工作流")
            return
        files = [f for f in wf["core_files"] if f in ("agent.py", "llm.py", "tools.py", "mcp.json")]
        if not files:
            QMessageBox.information(self, "提示", "该工作流没有可编辑的文件型核心文件，请先添加")
            return
        file, ok = QInputDialog.getItem(self, "编辑核心文件", "选择要编辑的文件：", files, 0, False)
        if not ok:
            return
        rok, content = agent_workflow.read_core_file(wf["name"], file)
        if not rok:
            QMessageBox.warning(self, "读取失败", content)
            return
        text, ok2 = _MultiLineInputDialog.get(
            self, f"编辑 {file}", "修改文件内容：", content, wrap=False, min_size=(680, 480))
        if not ok2:
            return
        wok, msg = agent_workflow.write_core_file(wf["name"], file, text)
        if wok:
            self._wf_changed = True
            QMessageBox.information(self, "已保存", msg)
        else:
            QMessageBox.warning(self, "保存失败", msg)
        self._reload_workflow_list()

    def _on_workflow_delete_file(self, *_):
        wf = self._current_workflow()
        if not wf:
            QMessageBox.information(self, "提示", "请先选择工作流")
            return
        files = [f for f in wf["core_files"] if f in ("agent.py", "llm.py", "tools.py", "mcp.json")]
        if not files:
            QMessageBox.information(self, "提示", "该工作流没有可删除的文件型核心文件")
            return
        file, ok = QInputDialog.getItem(self, "删除核心文件",
                                        "选择要删除的文件（删除后回退内置默认）：", files, 0, False)
        if not ok:
            return
        ret = QMessageBox.question(
            self, "确认删除", f"确定删除 {wf['name']}/{file}？该模块将回退到内置默认实现。")
        if ret != QMessageBox.StandardButton.Yes:
            return
        dok, msg = agent_workflow.delete_core_file(wf["name"], file)
        if dok:
            self._wf_changed = True
            QMessageBox.information(self, "已删除", msg)
        else:
            QMessageBox.warning(self, "删除失败", msg)
        self._reload_workflow_list()

    def _on_workflow_switch(self, *_):
        wf = self._current_workflow()
        if not wf:
            QMessageBox.information(self, "提示", "请先选择要切换的工作流")
            return
        ok, msg = agent_workflow.set_active(wf["name"])
        if ok:
            self._wf_changed = True
            QMessageBox.information(self, "已切换", msg + "（保存后立即生效）")
        else:
            QMessageBox.warning(self, "切换失败", msg)
        self._reload_workflow_list()

    def _on_workflow_toggle(self, *_):
        wf = self._current_workflow()
        if not wf:
            QMessageBox.information(self, "提示", "请先选择工作流")
            return
        ok, msg = agent_workflow.set_enabled(wf["name"], not wf.get("enabled", True))
        if ok:
            self._wf_changed = True
            QMessageBox.information(self, "已更新", msg + "（保存后立即生效）")
        else:
            QMessageBox.warning(self, "操作失败", msg)
        self._reload_workflow_list()

    def _on_workflow_delete(self, *_):
        wf = self._current_workflow()
        if not wf:
            QMessageBox.information(self, "提示", "请先选择要删除的工作流")
            return
        if wf["is_default"]:
            QMessageBox.warning(self, "不可删除", "默认工作流不可删除")
            return
        ret = QMessageBox.question(
            self, "确认删除", f"确定删除工作流「{wf['name']}」？文件将一并删除，不可恢复。")
        if ret != QMessageBox.StandardButton.Yes:
            return
        ok, msg = agent_workflow.delete_workflow(wf["name"])
        if ok:
            self._wf_changed = True
            QMessageBox.information(self, "已删除", msg)
        else:
            QMessageBox.warning(self, "删除失败", msg)
        self._reload_workflow_list()

    # ---------- UI/UX 自定义 ----------
    def _build_uiux_page(self) -> QWidget:
        """UI/UX 自定义（Cordis 热插拔）：列表 + AI 生成 + 新建 + 切换 + 编辑 + 删除"""
        w = self._page("UI/UX 自定义")
        lay = self._page_body(w)
        sub = QLabel(
            "自定义 AI 面板界面（热插拔即生效）：可用自然语言让 AI 生成，或手动新建/编辑/切换。"
            "内置默认包不可删除，加载失败自动回退默认。")
        sub.setStyleSheet(f"color: {self._DIM}; font-size: 12px;")
        sub.setWordWrap(True)
        lay.addWidget(sub)
        self.uiux_list = QListWidget()
        self.uiux_list.setStyleSheet(
            f"QListWidget {{ background: {self._PANEL}; color: {self._TEXT};"
            f"border: 1px solid {self._BORDER}; border-radius: 8px; padding: 6px; }}"
            f"QListWidget::item {{ padding: 8px 10px; border-radius: 6px; }}"
            f"QListWidget::item:selected {{ background: {self._PANEL2};"
            f"color: {self._ACCENT_HOVER}; }}")
        lay.addWidget(self.uiux_list, 1)

        def _btn(text, accent=False, tip=""):
            b = QPushButton(text)
            if accent:
                b.setStyleSheet(f"background: {self._ACCENT}; color: #FFFFFF; border: none;"
                                "border-radius: 8px; padding: 7px 14px; font-weight: 700;")
            else:
                b.setStyleSheet(f"background: {self._PANEL}; color: {self._TEXT};"
                                f"border: 1px solid {self._BORDER}; border-radius: 8px;"
                                "padding: 7px 14px; font-weight: 600;")
            b.setAutoDefault(False)
            if tip:
                b.setToolTip(tip)
            return b

        # 按钮用网格排布（每行 6 个），避免文字被挤压
        grid = QGridLayout()
        grid.setSpacing(8)
        _btns = [
            ("AI 生成", True, "用自然语言让 AI 生成完整 UI/UX 包", self._on_uiux_create),
            ("新建", False, "从默认模板创建空白 UI/UX 包", self._on_uiux_new),
            ("切换", False, "把选中包设为当前界面（保存后热插拔生效）", self._on_uiux_switch),
            ("编辑代码", False, "编辑选中包的 build_ui.py（面板构建代码）", self._on_uiux_edit_ui),
            ("欢迎页", False, "编辑选中包的 build_welcome.py（可选）", self._on_uiux_edit_welcome),
            ("主题", False, "编辑自定义主题色板（深色/浅色，JSON）", self._on_uiux_edit_theme),
            ("样式", False, "编辑 panel.qss（QSS 深度定制全部组件外观）", self._on_uiux_edit_qss),
            ("依赖", False, "勾选导出时随包打包的插件与 MCP server", self._on_uiux_edit_deps),
            ("导入", False, "导入 zip UI/UX 资源包（自动校验安全并安装依赖）", self._on_uiux_import),
            ("导出", False, "导出 zip 资源包（含声明的插件/MCP，可共享）", self._on_uiux_export),
            ("删除", False, "删除选中 UI/UX 包（内置包不可删除）", self._on_uiux_delete),
        ]
        for i, (txt, accent, tip, handler) in enumerate(_btns):
            b = _btn(txt, accent=accent, tip=tip)
            b.clicked.connect(handler)
            grid.addWidget(b, i // 6, i % 6)
        for c in range(6):
            grid.setColumnStretch(c, 1)
        lay.addLayout(grid)

        self._uiux_items = []
        self._reload_uiux_list()
        return w

    def _reload_uiux_list(self):
        """刷新 UI/UX 包列表"""
        pkgs = agent_ui_ux.list_packages()
        active = agent_ui_ux.get_active_package()
        self._uiux_items = pkgs
        self.uiux_list.clear()
        for p in pkgs:
            name = p.get("name", "")
            mark = " ● 当前" if name == active else ""
            builtin = "（内置）" if p.get("is_builtin") else ""
            desc = (p.get("description") or "").strip()
            label = f"{name}{mark}{builtin}  —  {desc}" if desc else f"{name}{mark}{builtin}"
            self.uiux_list.addItem(label)
        # 选中当前活跃包
        for i, p in enumerate(pkgs):
            if p.get("name") == active:
                self.uiux_list.setCurrentRow(i)
                break

    def _current_uiux(self) -> dict:
        row = self.uiux_list.currentRow()
        if 0 <= row < len(self._uiux_items):
            return self._uiux_items[row]
        return {}

    def _on_uiux_switch(self, *_):
        p = self._current_uiux()
        if not p:
            QMessageBox.information(self, "提示", "请先选择要切换的 UI/UX 包")
            return
        ok, msg = agent_ui_ux.set_active_package(p["name"])
        if ok:
            self._ui_ux_changed = True
            QMessageBox.information(self, "已切换", msg + "（保存后立即生效）")
        else:
            QMessageBox.warning(self, "切换失败", msg)
        self._reload_uiux_list()

    def _on_uiux_new(self, *_):
        name, ok = QInputDialog.getText(self, "新建 UI/UX 包", "包名（英文，字母数字下划线连字符）：")
        if not ok or not name.strip():
            return
        name = name.strip()
        desc, ok2 = QInputDialog.getText(self, "新建 UI/UX 包", "描述（一句话说明风格）：")
        if not ok2:
            return
        ok3, msg = agent_ui_ux.create_package(name, desc)
        if ok3:
            QMessageBox.information(self, "已创建", msg + "，可点击「编辑代码」开始定制")
        else:
            QMessageBox.warning(self, "创建失败", msg)
        self._reload_uiux_list()

    def _on_uiux_edit_ui(self, *_):
        p = self._current_uiux()
        if not p:
            QMessageBox.information(self, "提示", "请先选择要编辑的 UI/UX 包")
            return
        code = agent_ui_ux.get_build_ui_code(p["name"])
        text, ok = _MultiLineInputDialog.get(
            self, f"编辑 {p['name']} / build_ui.py", "修改面板构建代码：", code,
            wrap=False, min_size=(720, 520))
        if not ok:
            return
        ok2, msg = agent_ui_ux.set_build_ui_code(p["name"], text)
        if ok2:
            if agent_ui_ux.get_active_package() == p["name"]:
                self._ui_ux_changed = True
            QMessageBox.information(self, "已保存", msg + "（保存后生效，出错自动回退默认）")
        else:
            QMessageBox.warning(self, "保存失败", msg)
        self._reload_uiux_list()

    def _on_uiux_edit_welcome(self, *_):
        p = self._current_uiux()
        if not p:
            QMessageBox.information(self, "提示", "请先选择要编辑的 UI/UX 包")
            return
        code = agent_ui_ux.get_welcome_code(p["name"])
        text, ok = _MultiLineInputDialog.get(
            self, f"编辑 {p['name']} / build_welcome.py", "修改欢迎页代码（可选，留空使用默认）：", code,
            wrap=False, min_size=(680, 480))
        if not ok:
            return
        ok2, msg = agent_ui_ux.set_welcome_code(p["name"], text)
        if ok2:
            if agent_ui_ux.get_active_package() == p["name"]:
                self._ui_ux_changed = True
            QMessageBox.information(self, "已保存", msg + "（保存后生效，出错自动回退默认）")
        else:
            QMessageBox.warning(self, "保存失败", msg)
        self._reload_uiux_list()

    def _on_uiux_edit_theme(self, *_):
        p = self._current_uiux()
        if not p:
            QMessageBox.information(self, "提示", "请先选择要编辑主题的 UI/UX 包")
            return
        if p.get("is_builtin"):
            QMessageBox.warning(self, "不可编辑", "内置默认包的配色由全局主题决定，不能单独编辑")
            return
        import json as _json
        cur = agent_ui_ux.get_package_theme(p["name"])
        if not cur:
            cur = {"dark": {}, "light": {}}
        text, ok = _MultiLineInputDialog.get(
            self, f"编辑 {p['name']} / 主题色板",
            "编辑自定义主题色板（JSON，键限色板常量名如 BG/TEXT/ACCENT，留空两套则删除主题）：\n"
            '{"dark": {"BG": "#...", "ACCENT": "#..."}, "light": {...}}',
            _json.dumps(cur, ensure_ascii=False, indent=2), wrap=False, min_size=(680, 460))
        if not ok:
            return
        try:
            theme = _json.loads(text) if text.strip() else None
        except Exception:
            QMessageBox.warning(self, "格式错误", "主题色板不是合法 JSON，请检查格式")
            return
        ok2, msg = agent_ui_ux.update_package_theme(p["name"], theme if theme is not None else {})
        if ok2:
            if agent_ui_ux.get_active_package() == p["name"]:
                self._ui_ux_changed = True
            QMessageBox.information(self, "已保存", msg + "（保存后生效，出错自动回退默认）")
        else:
            QMessageBox.warning(self, "保存失败", msg)
        self._reload_uiux_list()

    def _on_uiux_edit_qss(self, *_):
        p = self._current_uiux()
        if not p:
            QMessageBox.information(self, "提示", "请先选择要编辑样式的 UI/UX 包")
            return
        if p.get("is_builtin"):
            QMessageBox.warning(self, "不可编辑", "内置默认包的样式由全局样式表决定，不能单独编辑")
            return
        cur = agent_ui_ux.get_panel_qss(p["name"])
        text, ok = _MultiLineInputDialog.get(
            self, f"编辑 {p['name']} / panel.qss",
            "编辑包级样式表（QSS，深度定制全部组件外观：按钮形状/滑动条/输入框/对话框/图标等）：",
            cur, wrap=False, min_size=(720, 520))
        if not ok:
            return
        ok2, msg = agent_ui_ux.set_panel_qss(p["name"], text)
        if ok2:
            if agent_ui_ux.get_active_package() == p["name"]:
                self._ui_ux_changed = True
            QMessageBox.information(self, "已保存", msg + "（保存后生效）")
        else:
            QMessageBox.warning(self, "保存失败", msg)
        self._reload_uiux_list()

    def _on_uiux_edit_deps(self, *_):
        """编辑 UI/UX 包依赖（插件/MCP server）：勾选后导出 zip 时随包打包，对方导入即装即用"""
        p = self._current_uiux()
        if not p:
            QMessageBox.information(self, "提示", "请先选择要编辑依赖的 UI/UX 包")
            return
        if p.get("is_builtin"):
            QMessageBox.warning(self, "不可编辑", "内置默认包不能编辑依赖")
            return
        from zhuzhu_Copilot.core import agent_plugins, agent_skills
        cur_plugins = {str(x) for x in (p.get("plugins") or [])}
        cur_mcp = {str(x) for x in (p.get("mcp") or [])}

        def _plugin_items():
            try:
                return [str(x.get("name") or "") for x in agent_plugins.list_plugins()
                        if x.get("name")]
            except Exception:
                return []

        def _mcp_items():
            try:
                return [str(s.get("name") or "") for s in agent_skills.load_mcp_servers()
                        if s.get("name")]
            except Exception:
                return []

        dlg = QDialog(self)
        dlg.setWindowTitle(f"编辑 {p['name']} 依赖（导出时打包）")
        dlg.setMinimumSize(620, 440)
        v = QVBoxLayout(dlg)
        info = QLabel("勾选导出 zip 时随包打包的插件与 MCP server。\n"
                      "对方导入该包时会自动安装这些依赖，实现即装即用。")
        info.setStyleSheet(f"color: {self._DIM}; font-size: 12px;")
        info.setWordWrap(True)
        v.addWidget(info)

        body = QHBoxLayout()
        for title, items, checked, attr in (
                ("插件", _plugin_items(), cur_plugins, "_pl"),
                ("MCP server", _mcp_items(), cur_mcp, "_mc")):
            box = QVBoxLayout()
            t = QLabel(title)
            t.setStyleSheet(f"color: {self._TEXT}; font-weight: 700; font-size: 13px;")
            box.addWidget(t)
            lst = QListWidget()
            lst.setStyleSheet(
                f"QListWidget {{ background: {self._PANEL}; color: {self._TEXT};"
                f"border: 1px solid {self._BORDER}; border-radius: 8px; }}")
            for nm in items:
                it = QListWidgetItem(nm)
                it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                it.setCheckState(Qt.CheckState.Checked if nm in checked else Qt.CheckState.Unchecked)
                lst.addItem(it)
            setattr(dlg, attr, lst)
            box.addWidget(lst, 1)
            body.addLayout(box, 1)
        v.addLayout(body, 1)

        btns = QHBoxLayout()
        btns.addStretch(1)
        ok_b = QPushButton("保存")
        ok_b.setStyleSheet(f"background: {self._ACCENT}; color: #FFFFFF; border: none;"
                           "border-radius: 8px; padding: 7px 18px; font-weight: 700;")
        ok_b.setAutoDefault(False)
        ok_b.clicked.connect(dlg.accept)
        cancel_b = QPushButton("取消")
        cancel_b.setStyleSheet(f"background: {self._PANEL}; color: {self._TEXT};"
                               f"border: 1px solid {self._BORDER}; border-radius: 8px;"
                               "padding: 7px 18px; font-weight: 600;")
        cancel_b.setAutoDefault(False)
        cancel_b.clicked.connect(dlg.reject)
        btns.addWidget(cancel_b)
        btns.addWidget(ok_b)
        v.addLayout(btns)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        plugins = [dlg._pl.item(i).text() for i in range(dlg._pl.count())
                   if dlg._pl.item(i).checkState() == Qt.CheckState.Checked]
        mcp = [dlg._mc.item(i).text() for i in range(dlg._mc.count())
               if dlg._mc.item(i).checkState() == Qt.CheckState.Checked]
        ok2, msg = agent_ui_ux.update_package_deps(p["name"], plugins=plugins, mcp=mcp)
        if ok2:
            QMessageBox.information(self, "已保存", msg + "；点击「导出」即可生成含依赖的资源包")
        else:
            QMessageBox.warning(self, "保存失败", msg)
        self._reload_uiux_list()

    def _on_uiux_import(self, *_):
        path, _ = QFileDialog.getOpenFileName(
            self, "导入 UI/UX 资源包", "", "UI/UX 资源包 (*.zip);;所有文件 (*.*)",
            options=QFileDialog.Option.DontUseNativeDialog)   # Qt 渲染 → 随深浅主题换色
        if not path:
            return
        # 同名包已存在时确认覆盖
        import zipfile as _zf
        name = ""
        try:
            with _zf.ZipFile(path) as z:
                meta = json.loads(z.read("ui_ux.json").decode("utf-8", errors="replace"))
                name = str(meta.get("name") or "").strip()
        except Exception:
            pass
        if name and agent_ui_ux.get_package(name):
            ret = QMessageBox.question(
                self, "覆盖确认", f"已存在 UI/UX 包「{name}」，是否覆盖导入？")
            if ret != QMessageBox.StandardButton.Yes:
                return
        ok, msg = agent_ui_ux.import_package_zip(path, overwrite=True)
        if ok:
            QMessageBox.information(self, "导入成功", msg + "，可点击「切换」启用")
        else:
            QMessageBox.warning(self, "导入失败", msg)
        self._reload_uiux_list()

    def _on_uiux_export(self, *_):
        p = self._current_uiux()
        if not p:
            QMessageBox.information(self, "提示", "请先选择要导出的 UI/UX 包")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 UI/UX 资源包", f"ui_ux_{p['name']}.zip", "UI/UX 资源包 (*.zip)",
            options=QFileDialog.Option.DontUseNativeDialog)   # Qt 渲染 → 随深浅主题换色
        if not path:
            return
        if not path.lower().endswith(".zip"):
            path += ".zip"
        ok, msg = agent_ui_ux.export_package_zip(p["name"], path)
        if ok:
            QMessageBox.information(self, "导出成功", f"已导出到：\n{msg}\n可分享给其他开发者导入使用")
        else:
            QMessageBox.warning(self, "导出失败", msg)
        self._reload_uiux_list()

    def _on_uiux_delete(self, *_):
        p = self._current_uiux()
        if not p:
            QMessageBox.information(self, "提示", "请先选择要删除的 UI/UX 包")
            return
        if p.get("is_builtin"):
            QMessageBox.warning(self, "不可删除", "内置 UI/UX 包不可删除")
            return
        ret = QMessageBox.question(
            self, "确认删除", f"确定删除 UI/UX 包「{p['name']}」？文件将一并删除，不可恢复。")
        if ret != QMessageBox.StandardButton.Yes:
            return
        ok, msg = agent_ui_ux.delete_package(p["name"])
        if ok:
            if agent_ui_ux.get_active_package() == p["name"]:
                self._ui_ux_changed = True
            QMessageBox.information(self, "已删除", msg)
        else:
            QMessageBox.warning(self, "删除失败", msg)
        self._reload_uiux_list()

    def _on_uiux_create(self, *_):
        """自然语言描述 → 后台线程 AI 生成 UI/UX 包（真实 API），生成期间显示百分比进度"""
        text, ok = _MultiLineInputDialog.get(
            self, "AI 生成 UI/UX", "用自然语言描述你想要的 AI 面板界面（风格、布局、配色等）：",
            "帮我做一个深蓝色科技风面板：标题更大气，输入框更大更圆润，按钮用渐变蓝色")
        if not ok or not text.strip():
            return
        self._pbar_open("AI 生成 UI/UX", "正在分析需求并设计界面…")
        threading.Thread(target=self._uiux_worker, args=(text.strip(),), daemon=True).start()

    def _uiux_worker(self, desc: str):
        """后台生成 UI/UX。任何异常都必须回传 done，否则模态进度框永不关闭造成"卡死"。"""
        try:
            ok, msg = agent_ui_ux.create_package_from_nl(
                desc, on_status=lambda p, s: self.uiux_progress.emit(p, s))
            self.uiux_done.emit("1" if ok else "0", msg)
        except Exception as e:
            self.uiux_done.emit("0", f"生成 UI/UX 异常: {e}")

    def _on_uiux_progress(self, pct: int, text: str):
        self._pbar_update(pct, text)

    def _on_uiux_done(self, ok: str, msg: str):
        self._pbar_close()
        if ok == "1":
            QMessageBox.information(self, "AI 生成 UI/UX", msg)
        else:
            QMessageBox.warning(self, "生成失败", msg)
        self._reload_uiux_list()

    def _build_plugin_page(self) -> QWidget:
        w = self._page("插件")
        lay = self._page_body(w)
        sub = QLabel("统一管理插件：用自然语言描述即可创建可运行插件（MCP 工具 + 标准技能 SKILL.md），"
                     "或导入插件包 / 标准技能。停用插件即时移除其 MCP 工具。")
        sub.setStyleSheet(f"color: {self._DIM}; font-size: 12px;")
        sub.setWordWrap(True)
        lay.addWidget(sub)
        self.plugin_list = QListWidget()
        self.plugin_list.setStyleSheet(
            f"QListWidget {{ background: {self._PANEL}; color: {self._TEXT};"
            f"border: 1px solid {self._BORDER}; border-radius: 8px; padding: 6px; }}"
            f"QListWidget::item {{ padding: 8px 10px; border-radius: 6px; }}"
            f"QListWidget::item:selected {{ background: {self._PANEL2};"
            f"color: {self._ACCENT_HOVER}; }}")
        lay.addWidget(self.plugin_list, 1)
        row = QHBoxLayout()
        row.setSpacing(8)
        create_b = QPushButton(_line_icon("plus", 16), "创建插件（自然语言）")
        create_b.setStyleSheet(f"background: {self._ACCENT}; color: #FFFFFF;"
                               "border: none; border-radius: 8px; padding: 7px 14px; font-weight: 700;")
        create_b.setAutoDefault(False)
        create_b.setToolTip("输入自然语言描述，AI 自动生成可运行的插件（MCP server + SKILL.md + 脚本/资源/示例）")
        create_b.clicked.connect(self._on_plugin_create)
        imp_zip = QPushButton(_line_icon("folder", 16), "导入插件包")
        imp_zip.setStyleSheet(f"background: {self._PANEL}; color: {self._TEXT};"
                              f"border: 1px solid {self._BORDER}; border-radius: 8px;"
                              "padding: 7px 14px; font-weight: 600;")
        imp_zip.setAutoDefault(False)
        imp_zip.setToolTip("导入 zip 插件包（含 plugin.json 的完整插件）")
        imp_zip.clicked.connect(self._on_plugin_import_zip)
        imp_skill = QPushButton("导入标准技能")
        imp_skill.setStyleSheet(f"background: {self._PANEL}; color: {self._TEXT};"
                                f"border: 1px solid {self._BORDER}; border-radius: 8px;"
                                "padding: 7px 14px; font-weight: 600;")
        imp_skill.setAutoDefault(False)
        imp_skill.setToolTip("导入市场标准 SKILL.md（或含 SKILL.md 的 zip），包装为 skill 型插件")
        imp_skill.clicked.connect(self._on_plugin_import_skill)
        call_b = QPushButton("调用")
        call_b.setStyleSheet(f"background: {self._PANEL}; color: {self._TEXT};"
                             f"border: 1px solid {self._BORDER}; border-radius: 8px;"
                             "padding: 7px 14px; font-weight: 600;")
        call_b.setAutoDefault(False)
        call_b.setToolTip("关闭设置并回到对话页，输入框预填「/插件名 」，回车即调用该插件；"
                          "调用时会把插件说明与调用规范直接交给模型")
        call_b.clicked.connect(self._on_plugin_call)
        toggle_b = QPushButton("启用/停用")
        toggle_b.setStyleSheet(f"background: {self._PANEL}; color: {self._TEXT};"
                               f"border: 1px solid {self._BORDER}; border-radius: 8px;"
                               "padding: 7px 14px; font-weight: 600;")
        toggle_b.setAutoDefault(False)
        toggle_b.clicked.connect(self._on_plugin_toggle)
        wf_b = QPushButton("分配工作流…")
        wf_b.setStyleSheet(f"background: {self._PANEL}; color: {self._TEXT};"
                           f"border: 1px solid {self._BORDER}; border-radius: 8px;"
                           "padding: 7px 14px; font-weight: 600;")
        wf_b.setAutoDefault(False)
        wf_b.setToolTip("指定插件可用的一个或多个工作流（空=全局所有工作流）；"
                        "插件技能与 MCP 服务器随之绑定生效")
        wf_b.clicked.connect(self._on_plugin_workflows)
        del_b = QPushButton(_line_icon("trash", 16), "删除")
        del_b.setStyleSheet(f"background: {self._PANEL}; color: {self._DIM};"
                            f"border: 1px solid {self._BORDER}; border-radius: 8px;"
                            "padding: 7px 14px; font-weight: 600;")
        del_b.setAutoDefault(False)
        del_b.clicked.connect(self._on_plugin_delete)
        row.addWidget(create_b)
        row.addWidget(imp_zip)
        row.addWidget(imp_skill)
        row.addWidget(toggle_b)
        row.addWidget(call_b)
        row.addWidget(wf_b)
        row.addWidget(del_b)
        row.addStretch(1)
        lay.addLayout(row)
        self._reload_plugin_list()
        return w

    def _reload_plugin_list(self):
        if not hasattr(self, "plugin_list") or self.plugin_list is None:
            return   # 插件页尚未懒加载构建时跳过
        self.plugin_list.clear()
        for p in agent_plugins.list_plugins():
            kind = {"mcp": "工具", "skill": "技能", "combined": "工具+技能"}.get(p.get("kind"), p.get("kind", ""))
            state = "启用" if p.get("enabled") else "停用"
            bound = agent_plugins.plugin_workflows(p.get("name", ""))
            wf = f"   [工作流: {', '.join(bound)}]" if bound else ""
            self.plugin_list.addItem(f"{p.get('name', '?')}   [{kind}]   {state}{wf}   {p.get('description', '')}")

    def _current_plugin(self) -> dict:
        row = self.plugin_list.currentRow()
        plugins = agent_plugins.list_plugins()
        if 0 <= row < len(plugins):
            return plugins[row]
        return None

    def _on_plugin_create(self, *_):
        """自然语言描述 → 后台线程 AI 生成插件（真实 API），生成期间显示百分比进度"""
        text, ok = _MultiLineInputDialog.get(
            self, "创建插件", "用自然语言描述你想要的插件（做什么、提供哪些能力）：",
            "帮我做一个每日天气查询插件：读取本地城市，查询当天天气并生成出行建议")
        if not ok or not text.strip():
            return
        kind, k_ok = QInputDialog.getItem(
            self, "创建插件", "插件类型：", ["combined（工具+技能）", "mcp（仅工具）", "skill（仅技能）"],
            0, False)
        if not k_ok:
            return
        k = {"combined（工具+技能）": "combined", "mcp（仅工具）": "mcp",
             "skill（仅技能）": "skill"}.get(kind, "combined")
        self._pbar_open("创建插件", "正在分析需求并生成插件设计…")
        threading.Thread(target=self._plugin_worker, args=(text.strip(), k), daemon=True).start()

    def _plugin_worker(self, desc: str, kind: str):
        """后台线程：AI 生成插件，经 plugin_done 信号回主线程。任何异常都回传 done，避免进度框卡死"""
        try:
            ok, msg = agent_plugins.create_plugin_from_nl(
                desc, kind, on_status=lambda p, s: self.plugin_progress.emit(p, s))
            self.plugin_done.emit("1" if ok else "0", msg)
        except Exception as e:
            self.plugin_done.emit("0", f"创建插件异常: {e}")

    def _on_plugin_progress(self, pct: int, text: str):
        """生成期间更新进度百分比与文案"""
        self._pbar_update(pct, text)

    def _on_plugin_done(self, ok: str, msg: str):
        self._pbar_close()
        if ok == "1":
            QMessageBox.information(self, "创建插件", msg)
        else:
            QMessageBox.warning(self, "创建插件失败", msg)
        self._reload_plugin_list()
        self._mcp_servers = agent_skills.load_mcp_servers()
        self._reload_mcp_list()

    def _on_plugin_import_zip(self, *_):
        """导入插件 zip 包（含 plugin.json）：导入后自动登记技能/MCP，刷新并重连"""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择插件包", "", "插件压缩包 (*.zip);;所有文件 (*.*)")
        if not path:
            return
        ok, msg = agent_plugins.import_plugin_zip(path)
        if ok:
            QMessageBox.information(self, "导入插件", msg)
        else:
            QMessageBox.warning(self, "导入失败", msg)
        self._reload_plugin_list()
        self._mcp_servers = agent_skills.load_mcp_servers()
        self._reload_mcp_list()
        if ok:
            threading.Thread(target=self._init_mcp, daemon=True).start()

    def _on_plugin_import_skill(self, *_):
        """导入标准技能为 skill 型插件"""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择标准技能文件", "",
            "技能文件 (*.md);;压缩包 (*.zip);;所有文件 (*.*)")
        if not path:
            return
        ok, msg = agent_plugins.import_plugin_skill(path)
        if ok:
            QMessageBox.information(self, "导入技能插件", msg)
        else:
            QMessageBox.warning(self, "导入失败", msg)
        self._reload_plugin_list()

    def _on_plugin_toggle(self, *_):
        """启用/停用插件：停用移除 MCP 登记，启用恢复；刷新列表与 MCP 列表"""
        p = self._current_plugin()
        if p is None:
            QMessageBox.information(self, "提示", "请先选择一个插件")
            return
        name = p.get("name", "")
        ok, msg = agent_plugins.set_plugin_enabled(name, not bool(p.get("enabled")))
        if ok:
            QMessageBox.information(self, "插件", msg)
        else:
            QMessageBox.warning(self, "操作失败", msg)
        self._reload_plugin_list()
        self._mcp_servers = agent_skills.load_mcp_servers()
        self._reload_mcp_list()

    def _on_plugin_call(self, *_):
        """插件列表「调用」：关闭设置并回到对话页，输入框预填 `/插件名 `（回车即调用）。

        插件的能力对用户是黑盒，这里只负责把「调用意图」交给面板；真正把插件说明与调用
        规范送进模型上下文的是引擎（见 agent_engine._sync_skill_msg），因此不需要用户在
        输入框里再写任何规范内容。
        """
        p = self._current_plugin()
        if p is None:
            QMessageBox.information(self, "提示", "请先在列表中选择一个插件")
            return
        name = str(p.get("name") or "")
        if not p.get("enabled", True):
            QMessageBox.warning(self, "调用插件", f"插件「{name}」已停用，请先启用再调用。")
            return
        fn = getattr(self.parent(), "request_plugin_call", None)
        if not callable(fn):
            QMessageBox.information(self, "调用插件", f"请在输入框输入 /{name} 调用该插件。")
            return
        fn(name)
        self.accept()      # 收起设置页：回到对话页即可直接回车调用

    def _on_plugin_delete(self, *_):
        """删除插件：移除插件目录并解绑 MCP/技能"""
        p = self._current_plugin()
        if p is None:
            QMessageBox.information(self, "提示", "请先选择一个插件")
            return
        name = p.get("name", "")
        reply = QMessageBox.question(
            self, "确认删除", f"确定删除插件「{name}」吗？\n将同时移除其目录与登记的 MCP 工具/技能。")
        if reply != QMessageBox.StandardButton.Yes:
            return
        ok, msg = agent_plugins.delete_plugin(name)
        if ok:
            QMessageBox.information(self, "删除插件", msg)
        else:
            QMessageBox.warning(self, "删除失败", msg)
        self._reload_plugin_list()
        self._mcp_servers = agent_skills.load_mcp_servers()
        self._reload_mcp_list()

    def _on_plugin_workflows(self, *_):
        """指定插件可用的工作流（多选/逗号分隔；空=全局）"""
        p = self._current_plugin()
        if p is None:
            QMessageBox.information(self, "提示", "请先选择一个插件")
            return
        name = p.get("name", "")
        current = agent_plugins.plugin_workflows(name)
        ws, ok = self._ask_workflow_names(
            f"分配插件「{name}」的工作流", ", ".join(current))
        if not ok:
            return
        ok2, msg = agent_plugins.set_plugin_workflows(name, ws)
        if ok2:
            QMessageBox.information(self, "插件", msg)
        else:
            QMessageBox.warning(self, "操作失败", msg)
        self._reload_plugin_list()
        if hasattr(self, "skill_wf_combo") and self.skill_wf_combo is not None:
            self._reload_skill_list(self._current_skill_workflow())

    def _ask_workflow_names(self, title: str, initial: str = ""):
        """通用：弹窗输入工作流名（逗号分隔，留空=全局所有工作流），返回 (工作流列表, ok)"""
        all_wf = agent_workflow.list_workflows()
        names = ", ".join(w["name"] for w in all_wf) or "（暂无）"
        hint = "可用工作流: " + names
        text, ok = QInputDialog.getText(
            self, title, f"工作流名用逗号分隔（可多选）；留空=全局所有工作流可用。\n{hint}",
            QLineEdit.EchoMode.Normal, initial)
        if not ok:
            return [], False
        ws = [w.strip() for w in re.split(r"[,，\s]+", text) if w.strip()]
        seen, out = set(), []
        for w in ws:
            if w not in seen:
                seen.add(w)
                out.append(w)
        return out, True

    def _ensure_all_pages(self):
        """按需补齐全部设置页（保存/需要读取各页控件时调用）"""
        while self.stack.count() < len(self._page_builders):
            self.stack.addWidget(self._page_builders[self.stack.count()]())

    def _switch_page(self, idx: int):
        # 懒加载：只构建到目标页再切换（打开面板只构建第一页，避免卡顿）
        while self.stack.count() <= idx:
            self.stack.addWidget(self._page_builders[self.stack.count()]())
        self.stack.setCurrentIndex(idx)
        # 切页后内容滚动回顶（页面包在 QScrollArea 中，避免长页面残留上一页滚动位置）
        try:
            sc = getattr(self, "_page_scroll", None)
            if sc is not None:
                sc.verticalScrollBar().setValue(0)
        except Exception:
            pass

    # ---------- 服务商卡片操作 ----------
    def _make_provider_card(self, p: dict) -> QWidget:
        card = QWidget()
        card.setStyleSheet(f"background: {self._PANEL}; border-radius: 8px;")
        # 声明 HeightForWidth：让 wordWrap 内容按真实宽度折算高度，卡片不被挤压裁剪
        try:
            sp = card.sizePolicy()
            sp.setHeightForWidth(True)
            card.setSizePolicy(sp)
        except Exception:
            pass
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 8, 12, 8)
        v.setSpacing(2)
        name_text = str(p.get("name", ""))
        if agent_llm.is_default_provider(p):
            name_text += "（内置 · 锁定）"
        if p.get("kind") == "deepseek_web":
            try:
                from zhuzhu_Copilot.core import agent_web_llm
                name_text += (" · 已登录" if agent_web_llm.has_valid_credentials()
                              else " · 未登录")
            except Exception:
                pass
        name = QLabel(name_text)
        name.setWordWrap(True)   # 长名称换行，避免把行高挤出导致文字被裁切
        name.setStyleSheet(f"color: {self._TEXT}; font-size: 14px; font-weight: 700;")
        v.addWidget(name)
        url = QLabel(str(p.get("base_url", "")))
        url.setWordWrap(True)   # 长接口地址换行完整显示，不被裁剪
        url.setStyleSheet(f"color: {self._DIM}; font-size: 11px;")
        url.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(url)
        mm = set(p.get("multimodal_models") or [])
        models = QLabel("模型：" + ", ".join(
            f"{x}（视觉）" if x in mm else str(x) for x in (p.get("models") or [])))
        models.setWordWrap(True)   # 长模型列表换行完整显示，不被裁剪
        models.setStyleSheet(f"color: {self._DIM}; font-size: 12px;")
        v.addWidget(models)
        # 保存子标签引用，用于选中时切换纯蓝底+白字
        card._name_lbl = name
        card._url_lbl = url
        card._models_lbl = models
        return card

    def _card_height(self, card, w) -> int:
        """可靠计算卡片高度：优先 heightForWidth；异常/返回 -1 时按各标签逐行估算。
        估算时把 名称/接口/模型 三行都计入（含换行），再统一加余量，
        保证换行后的最后一行不会被裁切/挤压。"""
        import math
        try:
            h = card.heightForWidth(w)
            if isinstance(h, (int, float)) and h > 56:
                return int(h) + 10
        except Exception:
            pass
        inner = max(w - 24, 100)

        def _lh(lbl, default):
            if lbl is None:
                return 0
            try:
                v = lbl.heightForWidth(inner)
                return int(math.ceil(v)) if isinstance(v, (int, float)) and v > 0 else default
            except Exception:
                return default

        # 纵向 = 上内边距(8) + 名称行 + 间距(2) + 接口行 + 间距(2) + 模型行 + 下内边距(8)
        h = (8
             + _lh(getattr(card, "_name_lbl", None), 24)
             + 2
             + _lh(getattr(card, "_url_lbl", None), 18)
             + 2
             + _lh(getattr(card, "_models_lbl", None), 18)
             + 8)
        return max(66, h + 8)

    def _reload_provider_list(self):
        self.provider_list.clear()
        for p in self._providers:
            p = dict(p)
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, p)
            card = self._make_provider_card(p)
            self.provider_list.addItem(item)
            self.provider_list.setItemWidget(item, card)
            # 卡片高度随内容自适应（换行后不裁剪完整保存信息）；宽度留出列表内边距/卡片边距
            w = max(self.provider_list.viewport().width() - 24, 200)
            item.setSizeHint(QSize(0, self._card_height(card, w)))
        # 列表尚未完成布局时宽度可能为 0 → 首帧后按真实宽度重新测量
        QTimer.singleShot(0, self._remeasure_provider_cards)
        QTimer.singleShot(250, self._remeasure_provider_cards)

    def _remeasure_provider_cards(self):
        """按列表当前真实宽度重算每张卡片高度（含窗口缩放/标签切换后），避免长内容卡片被挤压裁剪"""
        try:
            if not hasattr(self, "provider_list"):
                return
            w = max(self.provider_list.viewport().width() - 24, 200)
            if w <= 200 and self.provider_list.viewport().width() <= 0:
                QTimer.singleShot(200, self._remeasure_provider_cards)
                return
            for i in range(self.provider_list.count()):
                item = self.provider_list.item(i)
                cw = self.provider_list.itemWidget(item)
                if cw is None:
                    continue
                h = self._card_height(cw, w)
                cw.setFixedWidth(w)
                try:
                    cw.setMinimumHeight(h)   # 底限高度 = 内容所需，防止行内压缩裁剪文本
                except Exception:
                    pass
                cw.updateGeometry()
                item.setSizeHint(QSize(0, h))
            self.provider_list.doItemsLayout()
            self.provider_list.viewport().update()
        except Exception:
            pass

    def _current_provider(self) -> dict:
        item = self.provider_list.currentItem()
        if item is not None:
            return item.data(Qt.ItemDataRole.UserRole)
        return None

    def _on_provider_select(self, item):
        """单击卡片：选中（itemSelectionChanged 已触发 UI 刷新），不打开编辑"""
        self._update_provider_ui()

    def _update_provider_ui(self):
        """选中反馈：删除按钮选中时变红可用、未选中灰色；状态栏提示语；卡片高亮描边。
        同时刷新上下文上限说明（服务商增删改会改变窗口来源，用户需即时看到）。"""
        try:
            self._sync_context_hint()
        except Exception:
            pass
        if not hasattr(self, "del_provider_btn"):   # 初始化中按钮尚未创建时跳过
            return
        sel = self._current_provider()
        if sel is not None:
            locked = agent_llm.is_default_provider(sel)
            self.del_provider_btn.setEnabled(not locked)
            if locked:
                self.del_provider_btn.setStyleSheet(
                    f"background: {self._PANEL}; color: {self._DIM};"
                    f"border: 1px solid {self._BORDER}; border-radius: 8px;"
                    "padding: 7px 16px; font-weight: 600;")
                self.del_provider_btn.setToolTip("内置默认服务商（agnes）不可删除")
                self.provider_hint.setText(
                    f"「{sel.get('name')}」为内置默认服务商，整行锁定：不可删除、不可编辑")
                self.provider_hint.setStyleSheet(f"color: {self._DIM}; font-size: 12px;")
            else:
                self.del_provider_btn.setStyleSheet(
                    f"background: {self._DANGER}; color: #FFFFFF; border: none;"
                    "border-radius: 8px; padding: 7px 16px; font-weight: 700;")
                self.del_provider_btn.setToolTip(f"删除服务商「{sel.get('name')}」")
                models = ", ".join(sel.get("models") or [])
                self.provider_hint.setText(
                    f"已选中「{sel.get('name')}」｜接口 {sel.get('base_url')}｜模型：{models}")
                self.provider_hint.setStyleSheet(
                    f"color: {self._ACCENT_HOVER}; font-size: 12px;")
        else:
            self.del_provider_btn.setEnabled(True)
            self.del_provider_btn.setStyleSheet(
                f"background: {self._PANEL}; color: {self._DIM};"
                f"border: 1px solid {self._BORDER}; border-radius: 8px;"
                "padding: 7px 16px; font-weight: 600;")
            self.del_provider_btn.setToolTip("请先选中一个服务商")
            self.provider_hint.setText("点击选中服务商卡片（删除按钮随之变红可用）；双击卡片编辑")
            self.provider_hint.setStyleSheet(f"color: {self._DIM}; font-size: 12px;")
        # 卡片高亮：选中卡片纯蓝底 + 白字（无边框）
        for i in range(self.provider_list.count()):
            it = self.provider_list.item(i)
            w = self.provider_list.itemWidget(it)
            p = it.data(Qt.ItemDataRole.UserRole)
            selected = p is not None and sel is not None and p.get("name") == sel.get("name")
            if w is None:
                continue
            if selected:
                w.setStyleSheet(f"background: {self._ACCENT}; border-radius: 8px;")
                w._name_lbl.setStyleSheet("color: #FFFFFF; font-size: 14px; font-weight: 700;")
                w._url_lbl.setStyleSheet("color: #FFFFFF; font-size: 11px;")
                w._models_lbl.setStyleSheet("color: #FFFFFF; font-size: 12px;")
            else:
                w.setStyleSheet(f"background: {self._PANEL}; border-radius: 8px;")
                w._name_lbl.setStyleSheet(f"color: {self._TEXT}; font-size: 14px; font-weight: 700;")
                w._url_lbl.setStyleSheet(f"color: {self._DIM}; font-size: 11px;")
                w._models_lbl.setStyleSheet(f"color: {self._DIM}; font-size: 12px;")

    def _on_provider_add(self, *_):
        dlg = _ProviderDialog(parent=self)
        agent_ui_ux.glassify_dialog(dlg)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            p = dlg.provider_data()
            if any(x.get("name") == p["name"] for x in self._providers):
                QMessageBox.warning(self, "提示", f"已存在同名服务商「{p['name']}」")
                return
            self._providers.append(p)
            self._reload_provider_list()
            self.provider_list.setCurrentRow(self.provider_list.count() - 1)
            self._update_provider_ui()

    def _on_provider_edit(self, item):
        idx = self.provider_list.row(item)
        if not (0 <= idx < len(self._providers)):
            return
        p = self._providers[idx]
        if agent_llm.is_default_provider(p):
            QMessageBox.information(self, "提示", "内置默认服务商（agnes）不可编辑")
            return
        dlg = _ProviderDialog(provider=p, parent=self)
        agent_ui_ux.glassify_dialog(dlg)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        newp = dlg.provider_data()
        self._providers[idx] = newp
        self._reload_provider_list()
        self._update_provider_ui()

    def _on_provider_delete(self, *_):
        item = self.provider_list.currentItem()
        if item is None:
            QMessageBox.information(self, "提示", "请先选中一个服务商")
            return
        idx = self.provider_list.row(item)
        if not (0 <= idx < len(self._providers)):
            return
        p = self._providers[idx]
        if agent_llm.is_default_provider(p):
            QMessageBox.information(self, "提示", "内置默认服务商（agnes）不可删除")
            return
        reply = QMessageBox.question(
            self, "删除服务商", f"确定删除服务商「{p.get('name')}」？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._providers.pop(idx)
        self._reload_provider_list()
        self._update_provider_ui()

    # ---------- MCP 列表操作 ----------
    def _reload_mcp_list(self):
        if not hasattr(self, "mcp_list") or self.mcp_list is None:
            return   # MCP 页尚未懒加载构建时跳过（构建时/点击导航时再刷新）
        self.mcp_list.clear()
        for srv in self._mcp_servers:
            typ = "stdio" if srv.get("type", "stdio") == "stdio" else "sse"
            detail = srv.get("command", "") or srv.get("url", "")
            bound = agent_skills.get_workflow_binding("mcp", srv.get("name", ""))
            wf = f"   [工作流: {', '.join(bound)}]" if bound else ""
            self.mcp_list.addItem(f"{srv.get('name', '?')}   [{typ}]   {detail}{wf}")

    def _current_mcp(self) -> dict:
        row = self.mcp_list.currentRow()
        if 0 <= row < len(self._mcp_servers):
            return self._mcp_servers[row]
        return None

    def _on_mcp_add(self, *_):
        dlg = _McpServerDialog(parent=self)
        agent_ui_ux.glassify_dialog(dlg)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.server_data()
            self._mcp_servers.append(data)
            agent_skills.set_workflow_binding("mcp", data["name"], dlg.workflows())
            self._reload_mcp_list()

    def _on_mcp_edit(self, *_):
        if self._current_mcp() is None:
            QMessageBox.information(self, "提示", "请先选择一个服务器")
            return
        old = self._current_mcp()
        dlg = _McpServerDialog(server=old, parent=self)
        agent_ui_ux.glassify_dialog(dlg)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.server_data()
            self._mcp_servers[self.mcp_list.currentRow()] = data
            if old.get("name") and old.get("name") != data["name"]:
                agent_skills.remove_workflow_binding("mcp", old["name"])
            agent_skills.set_workflow_binding("mcp", data["name"], dlg.workflows())
            self._reload_mcp_list()

    def _on_mcp_delete(self, *_):
        row = self.mcp_list.currentRow()
        if 0 <= row < len(self._mcp_servers):
            name = self._mcp_servers[row].get("name", "")
            self._mcp_servers.pop(row)
            if name:
                agent_skills.remove_workflow_binding("mcp", name)
            self._reload_mcp_list()

    def _on_mode_changed(self, idx):
        """切换执行模式：切到 YOLO 需二次确认，防止误开。

        仅负责确认与持久化；引擎 direct 同步由面板保存回调
        （_apply_agent_settings）统一处理——本对话框无 _settings/_sess 面板属性。
        """
        val = self.mode_combo.itemData(idx)
        if val == "yolo":
            ret = QMessageBox.question(
                self, "开启直接工作模式",
                "YOLO 模式：AI 将直接执行任务，不再逐步询问/确认/约束，"
                "可操作任意目录（含系统目录）、执行任意命令，不再做危险操作拦截。\n"
                "请注意：此模式可能造成系统文件误删、关键配置被改等不可逆后果，请谨慎使用。"
                "确定开启？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if ret != QMessageBox.StandardButton.Yes:
                self.mode_combo.blockSignals(True)
                self.mode_combo.setCurrentIndex(0)   # 取消则回到 AskBeforeEdit
                self.mode_combo.blockSignals(False)
                return
        # 模式即时持久化（保存按钮 _save 也会写一次，同值幂等）；
        # 面板在保存后从 QSettings 重读并同步各引擎 direct 标志
        app_identity.qsettings().setValue("agent_mode", val or "ask")

    def _on_effort_slider(self, *_):
        """用户拖动思考强度滑块：自动按难度开启时自动关闭（让手动值立即生效），并刷新中文标签"""
        if self.auto_effort_check.isChecked():
            self.auto_effort_check.setChecked(False)
        self._effort = agent_llm.EFFORTS[self.effort_slider.value()]
        self.effort_label.setText(agent_llm.effort_label(self._effort))
        self._sync_effort_ui()

    def _on_effort_auto(self, *_):
        """自动按难度开关变更：刷新力度标签（不改手动值，保持已保存的手动力度）"""
        self.effort_label.setText(agent_llm.effort_label(self._effort))
        self._sync_effort_ui()

    def _on_panel_mode_changed(self, *_):
        """面板偏好切换：保存时由 _save 写入 QSettings，父面板 _apply_agent_settings 即时应用。
        此处仅保证选择「融入」时给出预期提示，无需额外处理。"""
        if self.panel_mode_combo.currentData() == "dock":
            self._panel_pref_hint = "融入主面板将在保存后生效（四个子面板与主面板同一窗口）"

    def _on_think_mode_changed(self, *_):
        """思考模式下拉变更：选「始终关闭」时若勾了强制思考，自动取消勾选保持自洽"""
        if self.think_combo.currentData() == "off" and self.force_think_check.isChecked():
            self.force_think_check.blockSignals(True)
            self.force_think_check.setChecked(False)
            self.force_think_check.blockSignals(False)

    def _on_force_think_toggled(self, checked: bool):
        """强制思考勾选：开启时自动把思考模式切到「始终开启」（强制思考优先于关闭）"""
        if checked:
            _oi = self.think_combo.findData("on")
            self.think_combo.blockSignals(True)
            self.think_combo.setCurrentIndex(_oi if _oi >= 0 else 0)
            self.think_combo.blockSignals(False)

    def _on_context_1m_toggled(self, _checked: bool = False):
        """1M 上下文开关：仅刷新说明文案（保存时写入配置，引擎按新上限重建）"""
        self._sync_context_hint()

    def _context_hint_text(self) -> str:
        """窗口说明文案：把「当前生效上限 + 来源 + 预算 + 压缩阈值」摆出来供用户核对。

        来源取自 agent_llm.resolve_context（与实际引擎同一条决策链，不存在第二套判断）。"""
        providers = list(getattr(self, "_providers", []) or [])
        first = providers[0] if providers else {}
        long_1m = bool(self.context_1m_check.isChecked())
        models = first.get("models") or []
        model = str(models[0]) if models else ""
        ctx = agent_llm.resolve_context(first.get("base_url"), model,
                                        provider=first, long_1m=long_1m)
        src_txt = {"1m": "1M 开关", "upstream": "上游服务商声明",
                   "configured": "服务商配置手填",
                   "known": "内置已知服务商表",
                   "inferred": "模型名推断"}.get(ctx.get("source"), "未知")
        reserve = int(ctx.get("max_output") or 0) or agent_engine._DEFAULT_MAX_OUTPUT
        budget = max(4096, int(ctx.get("window") or 0) - reserve)
        rate = int((agent_engine._CTX_RATIO_COMPRESS_LONG if long_1m
                    else agent_engine._CTX_RATIO_COMPRESS) * 100)
        return (f"当前上限：{_fmt_tokens(ctx.get('window'))} tokens（来源：{src_txt}）"
                f"；窗口由输入+输出共用：预留输出 {_fmt_tokens(reserve)}，"
                f"可用输入预算 {_fmt_tokens(budget)}"
                f"；达到预算 {rate}% 时自动用模型摘要压缩上下文。")

    def _sync_context_hint(self):
        """刷新窗口说明（幂等；控件未建好时静默跳过——重建/懒加载期间会被反复调用）"""
        try:
            txt = self._context_hint_text()
        except Exception:
            txt = ""
        try:
            self.context_hint.setText(txt)
        except Exception:
            pass

    def _sync_effort_ui(self):
        """手动力度始终可拖动（保存始终生效）；自动按难度开启时仅以提示色标注会被自动估算覆盖"""
        auto = self.auto_effort_check.isChecked()
        self.effort_slider.setEnabled(True)
        self.effort_label.setEnabled(True)
        self.effort_label.setStyleSheet(
            f"color: {self._DIM if auto else self._ACCENT}; font-size: 14px; font-weight: 800;")

    def _effort_models(self) -> list:
        """当前模型的力度声明查找来源：单服务商编辑框（models_edit）优先，
        多服务商卡片（_providers）兜底，保证主设置页也能读取 """
        try:
            if hasattr(self, "models_edit"):
                return [x.strip() for x in
                        self.models_edit.text().replace("，", ",").split(",") if x.strip()]
            out = []
            for p in getattr(self, "_providers", []) or []:
                for m in (p.get("models") or []):
                    ms = str(m).strip()
                    if ms and ms not in out:
                        out.append(ms)
            return out
        except Exception:
            return []

    def _sync_effort_declared(self):
        """上游 /models 声明的「可调思考力度级别」→ 自动映射到滑块挡位并提示。

        有声明：展示声明的档位（中文名）；当前力度不在声明内时就近映射到声明内的挡位，
        未声明的挡位在发送时由 build_effort_params 按邻近档折算，不会发非法参数。
        无声明：提示按模型类型内置映射（DeepSeek/GLM/OpenAI 等既有量表）。
        声明来源：本对话框临时拉取（_declarations）或已保存服务商卡片的 model_limits。"""
        decls = dict(getattr(self, "_declarations", None) or {})
        for p in getattr(self, "_providers", []) or []:
            for m, v in (p.get("model_limits") or {}).items():
                if isinstance(v, dict):
                    base = decls.setdefault(m, {})
                    base.update(v)
        declared = None
        for m in self._effort_models():
            d = decls.get(m) or {}
            if isinstance(d.get("effort_levels"), list):
                declared = [x for x in d["effort_levels"] if x in agent_llm.EFFORTS]
                break
        self._declared_efforts = declared
        hint = getattr(self, "effort_hint", None)
        if hint is None:
            return
        if declared:
            names = " / ".join(agent_llm.effort_label(x) for x in declared)
            hint.setText(f"上游声明本模型可调思考力度：{names}"
                         "（未列出的挡位发送时按邻近档自动折算）")
            if self._effort not in declared:
                nearest = min(declared, key=lambda x: abs(
                    agent_llm.effort_index(x) - agent_llm.effort_index(self._effort)))
                self._effort = nearest
                self.effort_slider.blockSignals(True)
                self.effort_slider.setValue(agent_llm.effort_index(nearest))
                self.effort_slider.blockSignals(False)
                self.effort_label.setText(agent_llm.effort_label(nearest))
        else:
            hint.setText(" · ".join(agent_llm.effort_label(x) for x in agent_llm.EFFORTS)
                         + "（未声明时按模型类型自动映射思考强度）")

    def _save(self, *_):
        # 懒加载页面后保存：先补齐全部页面，确保读取到所有页控件的当前状态
        self._ensure_all_pages()
        # 多服务商：基于卡片列表持久化，全部服务商统一参与路由
        providers = [dict(p) for p in getattr(self, "_providers", [])]
        if not providers:
            providers = [{"name": "默认服务商",
                          "base_url": agent_llm.DEFAULT_BASE_URL,
                          "api_key": agent_llm.DEFAULT_API_KEY,
                          "models": [agent_llm.DEFAULT_MODEL],
                          "protocol": "chat"}]
        first_models = providers[0].get("models") or [agent_llm.DEFAULT_MODEL]
        model = {
            "providers": providers,
            "model": first_models[0],   # 兼容旧字段
            "models": providers[0].get("models") or [],
            "protocol": providers[0]["protocol"] or "chat",
            "effort": self._effort,
            "auto_effort": self.auto_effort_check.isChecked(),
            "think_mode": self.think_combo.currentData() or "auto",
            "force_think": self.force_think_check.isChecked(),
            # 1M 上下文开关：开启后窗口与所有阈值一律按 1M 上限计算（见 agent_llm.resolve_context）
            "context_1m": self.context_1m_check.isChecked(),
        }
        # 请求超时配置（timeout/idle_timeout）本页暂未提供 UI，仅为后续扩展保留：
        # 透传旧配置防止保存时覆盖丢失（读取走 load_model_config 的 settings 键）
        try:
            _old_m = agent_llm.load_settings().get("model") or {}
        except Exception:
            _old_m = {}
        for _k in ("timeout", "idle_timeout"):
            _v = _old_m.get(_k)
            if isinstance(_v, (int, float)) and _v > 0:
                model[_k] = float(_v)
        data = {
            "custom_rules": [ln.strip() for ln in self.rules_edit.toPlainText().splitlines()
                             if ln.strip()],
            "custom_system_prompt": self.prompt_edit.toPlainText().strip(),
            "custom_safe_commands": [ln.strip() for ln in self.safe_edit.toPlainText().splitlines()
                                     if ln.strip()],
            "memory_enabled": self.memory_check.isChecked(),
            "cap_mcp": "1" if self.cap_mcp_check.isChecked() else "0",
            "cap_skill": "1" if self.cap_skill_check.isChecked() else "0",
            "cap_plugin": "1" if self.cap_plugin_check.isChecked() else "0",
            "disable_all_tools": self.disable_all_check.isChecked(),
            "disabled_tools": [ln.strip().lower() for ln in
                               self.disable_tools_edit.toPlainText().splitlines()
                               if ln.strip()],
            "model": model,
        }
        # 执行模式：写入 QSettings 并即时生效（保存成功与否的判定之外）
        q = app_identity.qsettings()
        q.setValue("agent_mode", self.mode_combo.currentData() or "ask")
        q.setValue("panel_pref_mode", self.panel_mode_combo.currentData() or "attach")
        q.setValue("agent_show_todos", "1" if self.todos_check.isChecked() else "0")
        # 趣味互动开关与间隔（QSettings 持久化，保存后让父面板即时生效）
        q.setValue("agent_fun", "1" if self.fun_check.isChecked() else "0")
        q.setValue("agent_fun_interval", self.fun_interval_combo.currentData() or "normal")
        # 工作目录不在通用设置页设置（入口已迁移到「对话流」页按会话配置）；
        # 保存后由父面板 _apply_agent_settings → _restore_workdir 按当前会话目录刷新面板。
        # 对话流：把每个对话的独立工作目录写回会话列表（新建对话沿用/切换应用均读此处）
        try:
            p = self.parent()
            for row in getattr(self, "_session_wd_rows", []):
                if len(row) < 2:
                    continue
                sid, wd_edit = row[0], row[1]
                if p is not None and hasattr(p, "_set_session_workdir"):
                    p._set_session_workdir(sid, wd_edit.text().strip())
        except Exception:
            pass
        if agent_skills.save_settings(data):
            # 保存 MCP 服务器配置；成功后触发后台重连
            mcp_ok = agent_skills.save_mcp_servers(self._mcp_servers)
            p = self.parent()
            if mcp_ok and p is not None and hasattr(p, "_reconnect_mcp"):
                p._reconnect_mcp()
            if not mcp_ok:
                QMessageBox.warning(self, "提示", "MCP 配置保存失败（无写入权限），其余设置已保存")
            # 音色与自动朗读：独立写入 tts.json，避免被 settings.json 覆写
            agent_tts.save_config(auto_read=self.auto_read_check.isChecked(),
                                  speech_rate=round(self.speed_slider.value() / 100.0, 2)
                                  if hasattr(self, "speed_slider") else 1.0,
                                  preferred_name=agent_tts.VOICE_DISPLAY_NAME)
            # 趣味互动设置即时生效：父面板按新开关/间隔重排或停止
            try:
                fp = self.parent()
                if fp is not None and hasattr(fp, "_on_fun_settings_changed"):
                    fp._on_fun_settings_changed()
            except Exception:
                pass
            self.accept()
        else:
            QMessageBox.warning(self, "错误", "保存设置失败（无写入权限）")

    def _import_skill(self, *_):
        """导入市场标准技能文件：SKILL.md 单文件或含 SKILL.md 的 zip 包"""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择市场标准技能文件", "",
            "技能文件 (*.md);;压缩包 (*.zip);;所有文件 (*.*)")
        if not path:
            return
        ok, msg = agent_skills.import_skill_file(path)
        if ok:
            QMessageBox.information(self, "导入技能", msg)
            self._reload_skill_list(self._current_skill_workflow())
        else:
            QMessageBox.warning(self, "导入失败", msg)

    def _delete_skill(self, *_):
        """删除用户技能：下拉选择（排除内置 JSON/md 技能），确认后删除并即时生效"""
        builtin = ({s.get("name") for s in agent_skills.DEFAULT_SKILLS}
                   | set(agent_skills._BUILTIN_MD_SKILLS))
        deletable = [s["name"] for s in agent_skills.load_skills()
                     if s.get("name") and s["name"] not in builtin]
        if not deletable:
            QMessageBox.information(self, "删除技能", "没有可删除的技能（内置技能不可删除）")
            return
        name, ok = QInputDialog.getItem(self, "删除技能", "选择要删除的技能：",
                                        deletable, 0, False)
        if not ok or not name:
            return
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定删除技能「{name}」吗？\n将同时删除其 SKILL.md 与附属文件（resources 等）。")
        if reply != QMessageBox.StandardButton.Yes:
            return
        ok2, msg = agent_skills.delete_skill(name)
        if ok2:
            QMessageBox.information(self, "删除技能", msg)
            self._reload_skill_list(self._current_skill_workflow())
        else:
            QMessageBox.warning(self, "删除失败", msg)

    def _open_guide(self, *_):
        """重新打开新手指南（复用父面板的打开逻辑；无父面板时独立打开）"""
        p = self.parent()
        if p is not None and hasattr(p, "_open_onboarding"):
            p._open_onboarding()
            return
        try:
            from zhuzhu_Copilot.ui.onboarding import OnboardingWizard
            OnboardingWizard(parent=self).exec()
        except Exception:
            pass


class _McpServerDialog(QDialog):
    """单个 MCP 服务器配置：stdio（命令+参数）或 SSE（URL）"""
    def __init__(self, server: dict = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑 MCP 服务器" if server else "添加 MCP 服务器")
        self.setMinimumWidth(480)
        _mcp_qss = (
            f"QDialog {{ background: {'transparent' if agent_ui_ux.is_custom_package_active() else BG}; }}"
            f"QLabel {{ color: {TEXT}; font-size: 13px; }}"
            f"QLineEdit, QComboBox {{ background: {PANEL}; color: {TEXT};"
            f"border: 1px solid {BORDER}; border-radius: 6px; padding: 6px 10px; }}"
            f"QLineEdit:focus, QComboBox:focus {{ border: 1px solid {ACCENT}; }}"
            f"QComboBox QAbstractItemView {{ background: {PANEL}; color: {TEXT};"
            f"border: 1px solid {BORDER}; selection-background-color: {CARD}; }}")
        if agent_ui_ux.is_custom_package_active():
            # 液态玻璃下拉：透明玻璃底 + 白受光边 + 箭头 + 玻璃渐变弹出视图（覆盖不透明白块）
            _mcp_qss += _glass_combo_qss(TEXT, TEXT_DIM, radius="6px")
            _mcp_qss += _glass_combo_view_qss(TEXT)
        self.setStyleSheet(_mcp_qss)
        self._server = server or {}
        form = QFormLayout(self)
        form.setContentsMargins(18, 16, 18, 16)
        form.setSpacing(12)

        self.name_edit = QLineEdit(server.get("name", "") if server else "")
        self.name_edit.setPlaceholderText("服务器名称，如 Excel、WPS")
        form.addRow("名称", self.name_edit)

        self.type_combo = QComboBox()
        self.type_combo.addItem("stdio（本地命令）", "stdio")
        self.type_combo.addItem("sse（远程 URL）", "sse")
        if server:
            idx = self.type_combo.findData(server.get("type", "stdio"))
            self.type_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.type_combo.currentIndexChanged.connect(self._sync_type)
        form.addRow("类型", self.type_combo)

        self.template_combo = QComboBox()
        for tpl, _ in _MCP_TEMPLATES:
            self.template_combo.addItem(tpl)
        self.template_combo.currentIndexChanged.connect(self._apply_template)
        form.addRow("模板", self.template_combo)

        self.command_edit = QLineEdit(server.get("command", "") if server else "")
        self.command_edit.setPlaceholderText("如 npx / uvx / python")
        form.addRow("命令", self.command_edit)

        self.args_edit = QLineEdit(" ".join(server.get("args", [])) if server else "")
        self.args_edit.setPlaceholderText("参数，空格分隔，如 -y @executeautomation/excel-mcp-server")
        form.addRow("参数", self.args_edit)

        self.url_edit = QLineEdit(server.get("url", "") if server else "")
        self.url_edit.setPlaceholderText("https://…/sse")
        form.addRow("URL", self.url_edit)

        self.workflow_edit = QLineEdit()
        if server:
            bound = agent_skills.get_workflow_binding("mcp", server.get("name", ""))
            self.workflow_edit.setText(", ".join(bound))
        self.workflow_edit.setPlaceholderText("工作流名，逗号分隔；留空=全局所有工作流可用")
        form.addRow("工作流", self.workflow_edit)

        btns = QHBoxLayout()
        ok = QPushButton(_std_icon(QStyle.StandardPixmap.SP_DialogYesButton), "确定")
        ok.setStyleSheet(f"background: {ACCENT}; color: #FFFFFF; border: none;"
                          "border-radius: 8px; padding: 8px 24px; font-weight: 700;")
        ok.setAutoDefault(False)
        ok.clicked.connect(self._accept_check)
        cancel = QPushButton("取消")
        cancel.setStyleSheet(f"background: {PANEL}; color: {TEXT};"
                             f"border: 1px solid {BORDER}; border-radius: 8px;"
                             "padding: 8px 22px; font-weight: 600;")
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        btns.addWidget(ok)
        btns.addWidget(cancel)
        form.addRow(btns)
        add_brand_footer(self)

        self._sync_type()
        if server and server.get("type") == "sse":
            self.template_combo.setCurrentIndex(0)

    def _sync_type(self, *_):
        sse = self.type_combo.currentData() == "sse"
        self.command_edit.setEnabled(not sse)
        self.args_edit.setEnabled(not sse)
        self.url_edit.setEnabled(sse)

    def _apply_template(self, idx):
        tpl = _MCP_TEMPLATES[idx][1]
        if not tpl:
            return
        if tpl.get("command"):
            self.command_edit.setText(tpl["command"])
            self.args_edit.setText(" ".join(tpl["args"]))
            self.type_combo.setCurrentIndex(0)
            self.url_edit.clear()

    def _accept_check(self, *_):
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "提示", "请输入服务器名称")
            return
        if self.type_combo.currentData() == "sse":
            if not self.url_edit.text().strip():
                QMessageBox.warning(self, "提示", "SSE 类型需要填写 URL")
                return
        else:
            if not self.command_edit.text().strip():
                QMessageBox.warning(self, "提示", "stdio 类型需要填写命令")
                return
        self.accept()

    def server_data(self) -> dict:
        d = {"name": self.name_edit.text().strip(),
             "type": self.type_combo.currentData()}
        if d["type"] == "sse":
            d["url"] = self.url_edit.text().strip()
        else:
            d["command"] = self.command_edit.text().strip()
            # 引号包裹的空格路径视为单个参数（剥离引号、保留反斜杠）
            args = [a for a in _split_args(self.args_edit.text()) if a.strip()]
            if args:
                d["args"] = args
        return d

    def workflows(self) -> list:
        """解析工作流绑定输入（逗号/空格分隔，去空；空 = 全局）"""
        ws = [w.strip() for w in re.split(r"[,，\s]+", self.workflow_edit.text()) if w.strip()]
        seen, out = set(), []
        for w in ws:
            if w not in seen:
                seen.add(w)
                out.append(w)
        return out


class _ConnTestThread(QThread):
    """后台连通性测试线程：真实 API 最小请求验证 base_url + api_key + 模型，结果经信号回主线程"""

    done = pyqtSignal(bool, str)

    def __init__(self, base_url: str, api_key: str, model: str, protocol: str, parent=None):
        super().__init__(parent)
        self._args = (base_url, api_key, model, protocol)

    def run(self):
        try:
            ok, msg = agent_llm.test_provider_connection(*self._args)
            self.done.emit(ok, msg)
        except Exception as e:   # noqa: BLE001
            self.done.emit(False, f"测试异常：{e}")


class _ModelsFetchThread(QThread):
    """后台从服务商上游拉取模型列表线程：真实 GET {base_url}/models，
    结果（含自动检测的多模态模型与**服务商声明的上下文能力**）经信号回主线程"""

    # ok, models_csv 或错误, 来源, 多模态模型_csv, 服务商声明 {模型: {context_window, max_output_tokens}}
    done = pyqtSignal(bool, str, str, str, object)

    def __init__(self, base_url: str, api_key: str, parent=None):
        super().__init__(parent)
        self._args = (base_url, api_key)

    def run(self):
        try:
            ok, result, source, vision, declared = agent_llm.fetch_provider_models(*self._args)
            self.done.emit(ok, ", ".join(result) if ok else result, source,
                           ", ".join(vision) if ok and vision else "",
                           dict(declared or {}) if ok else {})
        except Exception as e:   # noqa: BLE001
            self.done.emit(False, f"获取异常：{e}", "", "", {})


class _ProviderDialog(QDialog):
    """单个 AI 服务商配置：预设 / 名称 / 接口地址 / API Key / 模型列表 / 接口协议。
    添加/编辑保存前必须通过真实连通性测试（不 mock），确保接入即可用。"""

    ai_done = pyqtSignal(str)   # 默认 AI 排障分析结果（后台线程经信号回主线程）

    _BG = "#0F172A"
    _PANEL = "#1E293B"
    _TEXT = "#F1F5F9"
    _BORDER = "#334155"
    _ACCENT = "#1E40AF"
    _DIM = "#8A8A8A"
    _ERR = "#EF4444"
    _OK = "#22C55E"

    def __init__(self, provider: dict = None, parent=None):
        super().__init__(parent)
        # 主题自适应：用当前主题全局色覆盖类级深色常量，使对话框在浅色模式下随之变浅
        self._BG = BG
        self._PANEL = PANEL
        self._TEXT = TEXT
        self._BORDER = BORDER
        self._ACCENT = ACCENT
        self._DIM = TEXT_DIM
        self._ERR = ERR
        self._OK = OK
        self.setWindowTitle("编辑服务商" if provider else "添加服务商")
        self.setMinimumWidth(520)
        _pvd_qss = (
            f"QDialog {{ background: {'transparent' if agent_ui_ux.is_custom_package_active() else self._BG}; }}"
            f"QLabel {{ color: {self._TEXT}; font-size: 13px; }}"
            f"QLineEdit, QComboBox {{ background: {self._PANEL}; color: {self._TEXT};"
            f"border: 1px solid {self._BORDER}; border-radius: 6px; padding: 6px 10px; }}"
            f"QLineEdit:focus, QComboBox:focus {{ border: 1px solid {self._ACCENT}; }}"
            f"QComboBox QAbstractItemView {{ background: {self._PANEL}; color: {self._TEXT};"
            f"border: 1px solid {self._BORDER}; selection-background-color: {self._PANEL}; }}")
        if agent_ui_ux.is_custom_package_active():
            # 液态玻璃下拉：透明玻璃底 + 白受光边 + 箭头 + 玻璃渐变弹出视图（覆盖不透明白块）
            _pvd_qss += _glass_combo_qss(self._TEXT, self._DIM, radius="6px")
            _pvd_qss += _glass_combo_view_qss(self._TEXT)
        self.setStyleSheet(_pvd_qss)
        self._provider = provider or {}
        self._test_ok = False
        self._test_thread = None
        self._models_thread = None
        self._preset_kind = ""
        # 服务商声明的上下文能力（{模型: {context_window, max_output_tokens}}）：
        # 编辑既有服务商时先沿用已保存的声明，点「从上游获取」后被上游声明覆盖/补齐
        self._declarations = dict(self._provider.get("model_limits") or {})
        form = QFormLayout(self)
        form.setContentsMargins(18, 16, 18, 16)
        form.setSpacing(12)

        # 预设服务商：一键填入主流 coding/Agent 服务商（仍需填 Key 并测试）
        self.preset_combo = QComboBox()
        self.preset_combo.addItem("（自定义 / 手动填写）", None)
        for p in agent_llm.PRESET_PROVIDERS:
            self.preset_combo.addItem(p["name"], p)
        self.preset_combo.currentIndexChanged.connect(self._apply_preset)
        form.addRow("预设服务商", self.preset_combo)
        self.preset_hint = QLabel("")
        # 描述行：显式分行 + 实测字体高度（不依赖布局宽度换算），杜绝裁切/挤压
        self.preset_hint.setWordWrap(False)
        self.preset_hint.setTextFormat(Qt.TextFormat.PlainText)
        self.preset_hint.setStyleSheet(f"color: {self._DIM}; font-size: 12px;")
        form.addRow("", self.preset_hint)

        self.web_login_btn = QPushButton("登录网页版 DeepSeek")
        self.web_login_btn.setToolTip("启动独立浏览器到 chat.deepseek.com，登录一次后凭证持久化，"
                                     "此后免费调用不再弹登录")
        self.web_login_btn.setStyleSheet(
            f"background: {self._PANEL}; color: {self._TEXT}; border: 1px solid {self._ACCENT};"
            "border-radius: 8px; padding: 6px 14px; font-weight: 600;")
        self.web_login_btn.setAutoDefault(False)
        self.web_login_btn.clicked.connect(self._on_web_login)
        self.web_status = QLabel("")
        self.web_status.setStyleSheet(f"color: {self._DIM}; font-size: 12px;")
        wrow = QHBoxLayout()
        wrow.addWidget(self.web_login_btn)
        wrow.addWidget(self.web_status, 1)
        form.addRow("网页版登录", wrow)

        self.name_edit = QLineEdit(self._provider.get("name", ""))
        self.name_edit.setPlaceholderText("服务商名称，如 火山方舟、智谱、DeepSeek")
        form.addRow("名称", self.name_edit)

        self.base_edit = QLineEdit(self._provider.get("base_url", ""))
        self.base_edit.setPlaceholderText("https://api.example.com/v1")
        form.addRow("接口地址", self.base_edit)

        self.key_edit = QLineEdit(self._provider.get("api_key", ""))
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("sk-xxxxxxxx（必填，用于连通性测试）")
        form.addRow("API Key", self.key_edit)

        self.models_edit = QLineEdit(", ".join(self._provider.get("models", [])))
        self.models_edit.setPlaceholderText("模型名逗号分隔，如 glm-4.7, glm-4.7-flash")
        # 模型列表变化 → 声明的上下文只读行同步（用户手改列表时也要反映匹配结果）
        self.models_edit.textChanged.connect(self._sync_declared_label)
        mrow = QHBoxLayout()
        mrow.setSpacing(8)
        mrow.addWidget(self.models_edit, 1)
        self.fetch_btn = QPushButton("从上游获取")
        self.fetch_btn.setToolTip(
            "请求当前接口地址的 /models 端点，从服务商上游拉取可用模型列表并自动填入；"
            "多数服务商无需 API Key 即可枚举，填了 Key 会带上；失败时保留手动列表")
        self.fetch_btn.setStyleSheet(
            f"background: {self._PANEL}; color: {self._TEXT}; border: 1px solid {self._BORDER};"
            "border-radius: 8px; padding: 6px 14px; font-weight: 600;")
        self.fetch_btn.setAutoDefault(False)
        self.fetch_btn.clicked.connect(self._fetch_models)
        mrow.addWidget(self.fetch_btn)
        form.addRow("模型列表", mrow)

        # 上游声明的上下文能力（只读展示）：把"服务商自己声明的窗口/最大输出"直接摆给用户看，
        # 窗口决策优先级为 1M 开关 > 上游声明 > 手填 > 内置已知表 > 模型名推断
        self.declared_lbl = QLabel("")
        self.declared_lbl.setWordWrap(True)
        self.declared_lbl.setTextFormat(Qt.TextFormat.PlainText)
        self.declared_lbl.setStyleSheet(f"color: {self._DIM}; font-size: 12px;")
        form.addRow("声明的上下文", self.declared_lbl)
        self._sync_declared_label()

        self.multimodal_edit = QLineEdit(", ".join(self._provider.get("multimodal_models", [])))
        self.multimodal_edit.setPlaceholderText(
            "多模态（视觉）模型逗号分隔，留空按模型名自动识别；自动选择模式下视觉任务优先路由到这些模型")
        form.addRow("多模态模型", self.multimodal_edit)

        self.protocol_combo = QComboBox()
        self.protocol_combo.addItem("Chat Completions（/v1/chat/completions）", "chat")
        self.protocol_combo.addItem("Responses API（/v1/responses）", "responses")
        pidx = self.protocol_combo.findData(self._provider.get("protocol", "chat"))
        self.protocol_combo.setCurrentIndex(pidx if pidx >= 0 else 0)
        form.addRow("接口协议", self.protocol_combo)

        # 连通性测试：真实请求通过后保存；配置变化需重新测试
        trow = QHBoxLayout()
        self.test_btn = QPushButton("测试连接")
        self.test_btn.setStyleSheet(
            f"background: {self._PANEL}; color: {self._TEXT}; border: 1px solid {self._ACCENT};"
            "border-radius: 8px; padding: 8px 18px; font-weight: 600;")
        self.test_btn.clicked.connect(lambda: self._run_test(from_ok=False))
        self.ai_hint = QLabel("")
        self.ai_hint.setWordWrap(True)
        self.ai_hint.setTextFormat(Qt.TextFormat.MarkdownText)
        self.ai_hint.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse |
            Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.ai_hint.setOpenExternalLinks(True)
        self.ai_hint.setStyleSheet(f"color: {self._ACCENT}; font-size: 12px;")
        trow.addWidget(self.test_btn)
        trow.addWidget(self.ai_hint, 1)
        form.addRow("", trow)
        # 测试结果：独立整行（全宽换行显示不被挤压），成功后可点击查看完整保存信息
        self.test_result = QLabel("添加/编辑服务商前必须先通过连通性测试")
        self.test_result.setWordWrap(True)
        self.test_result.setTextFormat(Qt.TextFormat.RichText)
        self.test_result.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse |
            Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.test_result.setOpenExternalLinks(False)
        self.test_result.setStyleSheet(f"color: {self._DIM}; font-size: 12px;")
        self.test_result.linkActivated.connect(self._show_full_info)
        form.addRow("", self.test_result)
        self.ai_done.connect(self._on_ai_done)
        self.ai_done.connect(self._on_ai_done_web_login_forward)

        row = QHBoxLayout()
        ok = QPushButton("确定")
        ok.setStyleSheet(f"background: {self._ACCENT}; color: #FFFFFF; border: none;"
                         "border-radius: 8px; padding: 8px 24px; font-weight: 700;")
        ok.clicked.connect(self._confirm)
        cancel = QPushButton("取消")
        cancel.setStyleSheet(f"background: {self._PANEL}; color: {self._TEXT};"
                             f"border: 1px solid {self._BORDER}; border-radius: 8px;"
                             "padding: 8px 20px; font-weight: 600;")
        cancel.clicked.connect(self.reject)
        row.addStretch(1)
        row.addWidget(cancel)
        row.addWidget(ok)
        form.addRow("", row)

        # 任何配置变化 → 测试结果失效，需重新测试
        for w in (self.name_edit, self.base_edit, self.key_edit,
                  self.models_edit, self.multimodal_edit):
            w.textChanged.connect(self._invalidate)
        self.protocol_combo.currentIndexChanged.connect(self._invalidate)
        # 所有控件就绪后刷新网页版登录状态（依赖 key_edit/test_btn 已创建）
        self._refresh_web_status()

    def _invalidate(self, *_):
        self._test_ok = False
        self.test_result.setStyleSheet(f"color: {self._DIM}; font-size: 12px;")
        self.test_result.setText("配置已变化，需重新测试连接")
        self.ai_hint.setText("")
        self._refresh_web_status()

    def _apply_preset(self, idx):
        p = self.preset_combo.itemData(idx)
        if not p:
            self.preset_hint.setText("")
            self._preset_kind = ""
            self._refresh_web_status()
            self._fit_preset_hint()
            return
        self.name_edit.setText(p.get("name", ""))
        self.base_edit.setText(p.get("base_url", ""))
        self.key_edit.setText("")   # Key 必须用户填写
        self.models_edit.setText(", ".join(p.get("models") or []))
        self.multimodal_edit.setText(", ".join(p.get("multimodal_models") or []))
        pidx = self.protocol_combo.findData(p.get("protocol", "chat"))
        if pidx >= 0:
            self.protocol_combo.setCurrentIndex(pidx)
        self.preset_hint.setText(p.get("desc", ""))
        self._fit_preset_hint()
        self._preset_kind = p.get("kind") or ""
        self._refresh_web_status()

    def _current_params(self) -> dict:
        models = [x.strip() for x in self.models_edit.text().replace("，", ",").split(",")
                  if x.strip()]
        return {
            "name": self.name_edit.text().strip() or "服务商",
            "base_url": self.base_edit.text().strip(),
            "api_key": self.key_edit.text().strip(),
            "models": models,
            "protocol": self.protocol_combo.currentData() or "chat",
            "kind": getattr(self, "_preset_kind", ""),
        }

    def _fit_preset_hint(self):
        """按真实字体行高设定 preset_hint 最小高度（显式行数 × 行高），
        任何字体/DPI 下都完整显示，不会被布局压缩裁切。"""
        t = self.preset_hint.text()
        if not t:
            self.preset_hint.setMinimumHeight(0)
            return
        rows = t.count("\n") + 1
        fm = self.preset_hint.fontMetrics()
        self.preset_hint.setMinimumHeight(fm.height() * rows + fm.leading())

    def _is_web(self) -> bool:
        """是否 DeepSeek 网页版服务商（免 API Key，测试=验证登录）。"""
        return getattr(self, "_preset_kind", "") == "deepseek_web"

    def _refresh_web_status(self):
        """kind=deepseek_web 时显示登录状态行并豁免 API Key，否则隐藏并恢复必填。"""
        vis = self._is_web()
        self.web_login_btn.setVisible(vis)
        self.web_status.setVisible(vis)
        self.key_edit.setEnabled(not vis)          # 网页版免 API Key
        if vis:
            self.key_edit.setPlaceholderText("网页版免费接入，无需 API Key")
            self.key_edit.clear()
            self.test_btn.setText("验证登录")
        else:
            self.key_edit.setPlaceholderText("sk-xxxxxxxx（必填，用于连通性测试）")
            self.test_btn.setText("测试连接")
        if not vis:
            return
        try:
            from zhuzhu_Copilot.core import agent_web_llm
            st = ("已登录" if agent_web_llm.has_valid_credentials() else "未登录")
            self.web_status.setText(st)
            self.web_status.setStyleSheet(
                f"color: {'#22C55E' if st == '已登录' else self._ERR}; font-size: 12px;")
        except Exception:
            self.web_status.setText("")

    def _on_web_login(self, *_):
        self.web_login_btn.setEnabled(False)
        self.web_status.setText("正在打开浏览器，请在窗口中完成登录…")
        threading.Thread(target=self._web_login_worker, daemon=True).start()

    def _web_login_worker(self):
        try:
            from zhuzhu_Copilot.core import agent_web_llm
            ok, msg = agent_web_llm.login_now()
            self.ai_done.emit("web_login|" + ("1" if ok else "0") + "|" + msg)
        except Exception as e:
            self.ai_done.emit("web_login|0|" + str(e))

    def _on_ai_done_web_login(self, payload):
        _, ok, msg = payload.split("|", 2)
        self.web_login_btn.setEnabled(True)
        self._refresh_web_status()
        if ok == "1":
            self._test_ok = True
            self.test_result.setStyleSheet(f"color: {self._OK}; font-size: 12px;")
            self.test_result.setText("✓ 网页版登录成功，可直接点「确定」保存（免 API Key）")
            QMessageBox.information(self, "网页版登录", msg)
        else:
            QMessageBox.warning(self, "网页版登录失败", msg)

    def _on_ai_done_web_login_forward(self, payload):
        if str(payload).startswith("web_login|"):
            self._on_ai_done_web_login(str(payload))

    def _run_web_check(self, from_ok: bool):
        """网页版「验证登录」：登录态有效即可保存（免 API Key，不做连通性测试）。"""
        try:
            from zhuzhu_Copilot.core import agent_web_llm
            ok_creds = agent_web_llm.has_valid_credentials()
        except Exception:
            ok_creds = False
        if ok_creds:
            self._test_ok = True
            self.test_result.setStyleSheet(f"color: {self._OK}; font-size: 12px;")
            self.test_result.setText("✓ 网页版登录态有效，可保存使用（免 API Key）")
            if from_ok:
                self.accept()
        else:
            self._test_ok = False
            self.test_result.setStyleSheet(f"color: {self._ERR}; font-size: 12px;")
            self.test_result.setText("未检测到网页版登录态，已引导登录；登录完成后点「确定」保存")
            self._on_web_login()

    def _fetch_models(self, *_):
        """从服务商上游拉取模型列表并填入模型输入框（真实 GET /models，不 mock）。
        免 API Key：多数服务商 /models 支持匿名枚举；失败保留当前手填列表并提示。"""
        if self._models_thread is not None:
            try:
                if self._models_thread.isRunning():
                    return
            except RuntimeError:
                pass
            self._models_thread = None
        base = self.base_edit.text().strip()
        if not base:
            self.test_result.setStyleSheet(f"color: {self._ERR}; font-size: 12px;")
            self.test_result.setText("请先填写接口地址")
            return
        key = self.key_edit.text().strip()   # 可空：匿名枚举
        self.fetch_btn.setEnabled(False)
        self.fetch_btn.setText("获取中…")
        self.test_result.setStyleSheet(f"color: {self._ACCENT}; font-size: 12px;")
        self.test_result.setText(f"正在从上游 {base}/models 拉取模型列表…")
        self._models_thread = _ModelsFetchThread(base, key, self)
        self._models_thread.done.connect(self._on_models_fetched)
        self._models_thread.finished.connect(self._models_thread.deleteLater)
        self._models_thread.start()

    def _sync_declared_label(self):
        """刷新「声明的上下文」只读行：优先展示与当前模型列表匹配的声明，最多列 6 条"""
        try:
            models = [x.strip() for x in
                      self.models_edit.text().replace("，", ",").split(",") if x.strip()]
        except Exception:
            models = []
        rows = []
        for m in models:
            d = (self._declarations or {}).get(m)
            if not d:
                continue
            win = int(d.get("context_window") or 0)
            out = int(d.get("max_output_tokens") or 0)
            if not win and not out:
                continue
            seg = _fmt_tokens(win) if win else "未声明"
            if out:
                seg += f" / 输出上限 {_fmt_tokens(out)}"
            rows.append(f"{m}：{seg}")
        more = len(rows) - 6
        text = "；".join(rows[:6])
        if more > 0:
            text += f"，等 {len(rows)} 个模型"
        if not text:
            text = ("上游未声明（点「从上游获取」可读取服务商声明的窗口/最大输出；"
                    "未声明时按内置已知表或模型名推断）")
        self.declared_lbl.setText("（tokens）" + text if rows else text)

    def _on_models_fetched(self, ok: bool, msg: str, source: str = "", vision_csv: str = "",
                           declared: dict = None):
        self.fetch_btn.setEnabled(True)
        self.fetch_btn.setText("从上游获取")
        self._models_thread = None
        if ok:
            self.models_edit.setText(msg)   # 上游真实列表填入；textChanged 触发 _invalidate 重测
            # 服务商声明的上下文能力：与已保存声明合并（上游优先覆盖同模型条目）
            if isinstance(declared, dict) and declared:
                for k, v in declared.items():
                    if isinstance(v, dict) and (v.get("context_window")
                                                or v.get("max_output_tokens")
                                                or v.get("effort_levels")):
                        self._declarations[k] = dict(v)
            self._sync_declared_label()
            # 上游声明的力度级别已并入 _declarations，保存时随 model_limits 写入服务商，
            # 主设置页打开时由 _sync_effort_declared 自动映射滑块挡位
            n = len([x for x in msg.split(",") if x.strip()])
            note = "" if source == "upstream" else "（上游需鉴权/不支持枚举，已使用内置已知模型列表）"
            ctx_note = (f"，读取到 {len(declared)} 个模型的上下文声明"
                        if isinstance(declared, dict) and declared else "")
            # 自动检测多模态模型：合并进多模态模型列表（保留用户已填写的，去重）
            if vision_csv:
                existing = [x.strip() for x in
                            self.multimodal_edit.text().replace("，", ",").split(",") if x.strip()]
                merged = list(existing)
                for m in vision_csv.split(","):
                    m = m.strip()
                    if m and m not in merged:
                        merged.append(m)
                self.multimodal_edit.setText(", ".join(merged))
                if len(merged) > len(existing):
                    self.test_result.setStyleSheet(f"color: {self._OK}; font-size: 12px;")
                    self.test_result.setText(
                        f"✓ 已获取 {n} 个模型{note}，并自动识别 {len(merged) - len(existing)} 个多模态模型"
                        f"（可编辑）")
                    return
            self.test_result.setStyleSheet(f"color: {self._OK}; font-size: 12px;")
            self.test_result.setText(f"✓ 已获取 {n} 个模型{note}，请检查后点击「测试连接」")
        else:
            self.test_result.setStyleSheet(f"color: {self._ERR}; font-size: 12px;")
            self.test_result.setText(f"获取模型失败（保留当前列表）：{msg}")

    def _confirm(self, *_):
        """确定：已通过测试直接保存；否则自动先测，通过后再保存"""
        if self._test_ok:
            self.accept()
            return
        self._run_test(from_ok=True)

    def _run_test(self, from_ok: bool):
        # 网页版：测试=验证登录态（免 API Key），直接走登录校验分支
        if self._is_web():
            self._run_web_check(from_ok)
            return
        # 检查是否有正在运行的测试线程（对象可能已被 deleteLater 销毁 → 捕获 RuntimeError）
        if self._test_thread is not None:
            try:
                if self._test_thread.isRunning():
                    return
            except RuntimeError:
                pass
            self._test_thread = None
        p = self._current_params()
        if not p["base_url"] or not p["api_key"] or not p["models"]:
            self.test_result.setStyleSheet(f"color: {self._ERR}; font-size: 12px;")
            self.test_result.setText("请先填写接口地址、API Key 与至少一个模型名")
            return
        self.test_btn.setEnabled(False)
        self.test_btn.setText("测试中…")
        self.test_result.setStyleSheet(f"color: {self._ACCENT}; font-size: 12px;")
        self.test_result.setText(f"正在连接 {p['base_url']} 测试模型「{p['models'][0]}」…")
        self._test_thread = _ConnTestThread(p["base_url"], p["api_key"], p["models"][0],
                                            p["protocol"], self)
        self._test_thread.done.connect(
            lambda ok, msg, fo=from_ok: self._on_test_done(ok, msg, fo))
        self._test_thread.finished.connect(self._test_thread.deleteLater)
        self._test_thread.start()

    def _on_test_done(self, ok: bool, msg: str, from_ok: bool):
        self.test_btn.setEnabled(True)
        self.test_btn.setText("测试连接")
        self._test_thread = None   # 测试完成：清除引用，允许再次测试
        if ok:
            self._test_ok = True
            self.test_result.setStyleSheet(f"color: {self._OK}; font-size: 12px;")
            self.test_result.setText(
                "✓ " + _esc(msg)
                + "　<a href='show' style='color:#2563EB'>查看完整保存信息</a>")
            self.ai_hint.setText("")
            if from_ok:
                self.accept()
        else:
            self._test_ok = False
            self.test_result.setStyleSheet(f"color: {self._ERR}; font-size: 12px;")
            self.test_result.setText(_esc(msg))
            self.ai_hint.setStyleSheet(f"color: {self._ACCENT}; font-size: 12px;")
            self.ai_hint.setText("正在用默认 AI 分析失败原因，生成排障建议…")
            # 默认 AI 排障分析（后台线程，不阻塞界面）
            p = self._current_params()
            ctx = (f"接口地址：{p.get('base_url')}\n模型：{', '.join(p.get('models') or [])}\n"
                   f"协议：{p.get('protocol')}\n失败信息：{msg}")
            threading.Thread(target=self._ai_analyze, args=(ctx,), daemon=True).start()
            if from_ok:
                QMessageBox.warning(self, "连通性测试失败", msg)

    def _ai_analyze(self, ctx: str):
        """后台线程：调用默认 AI 分析连通性失败原因（结果经 ai_done 信号回主线程）"""
        suggestion = agent_llm.analyze_connection_error(ctx)
        self.ai_done.emit(suggestion)

    def _on_ai_done(self, suggestion: str):
        """AI 排障建议返回：以 markdown 格式渲染显示在测试按钮右侧"""
        s = (suggestion or "").strip()
        if s:
            self.ai_hint.setStyleSheet(f"color: {self._ACCENT}; font-size: 12px;")
            self.ai_hint.setText(s)

    def _show_full_info(self, *_):
        """点击「查看完整保存信息」：弹出完整连接配置（白字深底），可一键复制"""
        p = self._current_params()
        if not p.get("base_url"):
            return
        key = p.get("api_key") or ""
        masked = (key[:6] + "…" + key[-4:]) if len(key) > 10 else ("…" if key else "(未填写)")
        ep = "responses" if p.get("protocol") == "responses" else "chat/completions"
        endpoint = f"{p.get('base_url').rstrip('/')}/{ep}"
        lines = [
            f"服务商：{p.get('name') or '未命名'}",
            f"接口地址：{p.get('base_url')}",
            f"请求端点：{endpoint}",
            f"API Key：{masked}",
            f"模型列表：{', '.join(p.get('models') or []) or '（空）'}",
            f"接口协议：{'Responses API' if p.get('protocol') == 'responses' else 'Chat Completions'}",
        ]
        mm = self.multimodal_edit.text().strip()
        if mm:
            lines.append(f"多模态模型：{mm}")
        box = QMessageBox(self)
        box.setWindowTitle("完整保存信息")
        box.setStyleSheet(
            f"QMessageBox {{ background: {self._PANEL}; }}"
            f"QMessageBox QLabel {{ color: {self._TEXT}; font-size: 13px; }}"
            f"QMessageBox QPushButton {{ color: {self._TEXT}; background: {self._PANEL};"
            f"border: 1px solid {self._BORDER}; border-radius: 8px;"
            f"padding: 6px 14px; font-size: 13px; }}")
        box.setText("\n".join(lines))
        copy = box.addButton("复制完整信息", QMessageBox.ButtonRole.ActionRole)
        box.addButton("关闭", QMessageBox.ButtonRole.AcceptRole)
        box.exec()
        if box.clickedButton() is copy:
            QApplication.clipboard().setText("\n".join(lines))

    def closeEvent(self, e):
        t = self._test_thread
        if t is not None:
            try:
                # 线程 finished 已 deleteLater，可能已被销毁 → 捕获 RuntimeError 避免崩溃
                if t.isRunning():
                    t.wait(2000)   # 等待后台测试线程收尾，避免 QThread 运行中销毁
            except RuntimeError:
                pass
        super().closeEvent(e)

    def provider_data(self) -> dict:
        p = self._current_params()
        p["multimodal_models"] = [
            x.strip() for x in self.multimodal_edit.text().replace("，", ",").split(",")
            if x.strip()]
        # 服务商声明的上下文能力：只保留当前列表内的模型（列表换掉后旧声明不再有意义）
        models = set(p.get("models") or [])
        limits = {m: d for m, d in (self._declarations or {}).items() if m in models}
        if limits:
            p["model_limits"] = limits
        return p


class _AskUserDialog(QDialog):
    """AI 提问弹窗（TRAE 风格）：单选/多选选项或自由回答"""

    def __init__(self, question: str, options: list, multi_select: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle("AI 询问")
        self.setMinimumWidth(460)
        self._answer = ""
        _glass = agent_ui_ux.is_custom_package_active()
        if _glass:
            _dlg_bg = "transparent"   # 液态玻璃：透明底，由 glassify_dialog 叠加 Acrylic+光泽
            _line_bg = ("qlineargradient(x1:0,y1:0,x2:0,y2:1,"
                        "stop:0 rgba(255,255,255,44), stop:0.5 rgba(255,255,255,16), "
                        "stop:1 rgba(255,255,255,32))")
            _line_bd = "rgba(255,255,255,120)"
        else:
            _dlg_bg = PANEL
            _line_bg = BG
            _line_bd = BORDER
        self.setStyleSheet(
            f"QDialog {{ background: {_dlg_bg}; }}"
            f"QLabel {{ color: {TEXT}; font-size: 13px; }}"
            f"QRadioButton, QCheckBox {{ color: {TEXT}; font-size: 13px; spacing: 10px; }}"
            # 选择圆圈/复选框：未选中白色边框，选中填充强调色
            "QRadioButton::indicator, QCheckBox::indicator { width: 14px; height: 14px;"
            f" border: 1.5px solid #FFFFFF; background: transparent; }}"
            "QRadioButton::indicator { border-radius: 8px; }"
            "QCheckBox::indicator { border-radius: 3px; }"
            f"QRadioButton::indicator:checked, QCheckBox::indicator:checked {{"
            f" background: {ACCENT}; border-color: {ACCENT}; }}"
            f"QLineEdit {{ background: {_line_bg}; color: {TEXT}; border: 1px solid {_line_bd};"
            "border-radius: 8px; padding: 8px 12px; font-size: 13px; }}"
            f"QPushButton {{ border: none; border-radius: 8px; padding: 8px 22px;"
            "font-weight: 700; font-size: 13px; }}")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(12)

        q_lbl = QLabel(question)
        q_lbl.setWordWrap(True)
        q_lbl.setStyleSheet(f"font-size: 15px; font-weight: 700; color: {TEXT};")
        # 问题文本过长时滚动查看：包进限高的滚动区域，避免把选项/按钮挤出屏幕外无法点击
        q_wrap = QScrollArea()
        q_wrap.setWidgetResizable(True)
        q_wrap.setMaximumHeight(200)
        q_wrap.setFrameShape(QFrame.Shape.NoFrame)
        q_wrap.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        q_wrap.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            + _scrollbar_css(both=False))
        q_wrap.setWidget(q_lbl)
        lay.addWidget(q_wrap)

        self._choice_btns = []
        # 自定义输入框：有选项时默认隐藏（由"其他…"勾选控制显示），无选项时直接作自由回答
        self._free_input = QLineEdit()
        self._free_input.setPlaceholderText("输入自定义内容…")
        self._free_input.hide()
        lay.addWidget(self._free_input)
        if options:
            for opt in options:
                b = QCheckBox(str(opt)) if multi_select else QRadioButton(str(opt))
                b.setAutoExclusive(not multi_select)
                self._choice_btns.append(b)
                lay.addWidget(b)
            # "其他…"选项：勾选后显示输入框，可输入自定义内容
            other = QCheckBox("其他…") if multi_select else QRadioButton("其他…")
            other.setAutoExclusive(not multi_select)
            other.toggled.connect(lambda on: self._free_input.setVisible(on))
            self._choice_btns.append(other)
            lay.addWidget(other)
            self._free_input.setPlaceholderText("输入自定义内容…")
        else:
            self._free_input.setPlaceholderText("输入你的回答…")
            self._free_input.show()

        # 无限等待提示：弹窗保持打开直到作答/取消，不会自动超时
        timeout_hint = QLabel("弹窗将一直等待你的回答，不会自动超时")
        timeout_hint.setStyleSheet(f"color: {TEXT_DIM}; font-size: {FONT_CAPTION}px;")
        lay.addWidget(timeout_hint)

        btns = QHBoxLayout()
        ok = QPushButton(_std_icon(QStyle.StandardPixmap.SP_DialogYesButton), "确定")
        ok.setStyleSheet(f"background: {OK}; color: #06281B;")
        ok.clicked.connect(self._accept_clicked)
        cancel = QPushButton("取消")
        cancel.setStyleSheet(f"background: {PANEL}; color: {TEXT};"
                             f"border: 1px solid {BORDER};")
        cancel.clicked.connect(self.reject)
        for b in (ok, cancel):
            b.setAutoDefault(False)
        btns.addStretch(1)
        btns.addWidget(ok)
        btns.addWidget(cancel)
        lay.addLayout(btns)
        add_brand_footer(self)
        # 与 AI 主面板统一：透明底 + Acrylic 毛玻璃 + 光泽/波纹（液态玻璃）。
        # 降低磨砂质感、提升液态感：磨砂底更透(frost_alpha=52)、Acrylic 更透(tint)。
        if _glass:
            try:
                agent_ui_ux.glassify_dialog(self, tint=0x30FFFFFF, frost_alpha=40)
            except Exception:
                pass

    def _timeout_close(self):
        """弹窗超时：视为未作答并关闭，避免长期未回复占用模态导致程序卡死无响应"""
        # 已作答（accept）时 _answer 非空，不覆盖；未答则标记未作答
        if not self._answer:
            self._answer = "（用户未作答）"
            self.reject()

    def _accept_clicked(self, *_):
        sel = [b.text() for b in self._choice_btns if b.isChecked()]
        custom = self._free_input.text().strip() if self._free_input.isVisible() else ""
        # 自定义输入内容替换"其他…"选项；未填写的"其他…"直接忽略
        if custom:
            sel = [custom if t.startswith("其他…") else t for t in sel]
        else:
            sel = [t for t in sel if not t.startswith("其他…")]
        if sel:
            self._answer = " / ".join(sel)
        elif custom:
            self._answer = custom
        else:
            self._answer = "（用户未作答）"
        self.accept()

    def answer(self) -> str:
        return self._answer or "（用户取消回答）"


class _ComboCheckDelegate(QStyledItemDelegate):
    """下拉列表「当前选中项」右侧对勾标志：在弹出列表里对应当前生效项（如当前模型/
    当前会话）的那一行右侧绘制一个 ✓，即使鼠标悬停在其它行也不消失，持续标注当前项。
    - 复用于任何 QComboBox（模型/会话等），非硬编码：✓ 用路径折线绘制（不依赖字体），
      颜色默认取 ACCENT（明暗主题皆可读），可经 check_color 覆盖以适配未来主题。
    - 适配液态玻璃默认/选中高亮色的兜底逻辑见 attach_combo_checkmark。"""

    def __init__(self, combo, check_color=None, parent=None):
        super().__init__(parent or combo)
        self._combo = combo
        self._check_color = check_color

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        try:
            combo = self._combo
            if combo is None or index.row() != combo.currentIndex():
                return
            r = option.rect
            if r.width() <= 0 or r.height() <= 0:
                return
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            color = self._check_color if self._check_color is not None else ACCENT
            pen = QPen(QColor(color))
            pen.setWidthF(2.0)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            s = 4.5
            cy = r.center().y()
            rx = r.right() - 13
            path = QPainterPath()
            path.moveTo(rx - s, cy)
            path.lineTo(rx - s * 0.35, cy + s * 0.85)
            path.lineTo(rx + s, cy - s * 0.85)
            painter.drawPath(path)
            painter.restore()
        except Exception:
            pass


def attach_combo_checkmark(combo, check_color=None):
    """给下拉框弹出列表安装「当前选中项右侧对勾」委托（幂等，可重复调用）。

    复用 delegate 实例并设置到 combo.view() 上——QComboBox 的弹出列表视图在首次弹出前
    才创建，故此处调用 view() 会预先持有该视图；列表在主题切换/模型重建时重建后会
    重新调用本函数（以 _check_delegate_ok 避免重复安装）。失败静默返回 False。
    """
    try:
        if combo is None:
            return False
        if getattr(combo, "_check_delegate_ok", False):
            return True
        view = combo.view()   # 首次访问即创建弹出列表视图
        if view is None:
            return False
        delg = _ComboCheckDelegate(combo, check_color=check_color)
        view.setItemDelegate(delg)
        # 液态玻璃选中行用白底，ACENT 蓝在白/浅底上对比足够；若主题把文本画成极浅色，
        # 此兜底会动态换色，避免对勾在浅色底上不可见。
        combo._check_delegate = delg
        combo._check_delegate_ok = True
        return True
    except Exception:
        return False


class _ArrowComboBox(QComboBox):
    """带旋转动画下拉箭头的 QComboBox：展开时箭头旋转 180° 指向向上，收起时转回向下。

    深色主题下样式表常把原生下拉箭头覆盖消失，此组件在右侧下拉区自绘箭头，
    并配合 hover/展开状态变色，作为模型选择菜单的微交互点缀。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._arrow_angle = 0.0        # 箭头旋转角度：0=收起，180=展开
        self._arrow_open = False
        self._arrow_hover = False
        self._arrow_anim = QPropertyAnimation(self, b"arrowAngle", self)
        self._arrow_anim.setDuration(160)
        self._arrow_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    # ---------- 动画属性 ----------
    def get_arrowAngle(self) -> float:
        return self._arrow_angle

    def set_arrowAngle(self, a: float):
        self._arrow_angle = a
        self.update()

    arrowAngle = pyqtProperty(float, get_arrowAngle, set_arrowAngle)

    # ---------- 展开/收起触发旋转动画 ----------
    def showPopup(self):
        super().showPopup()
        self._run_arrow(True)

    def hidePopup(self):
        super().hidePopup()
        self._run_arrow(False)

    def _run_arrow(self, opening: bool):
        self._arrow_open = opening
        self._arrow_anim.stop()
        self._arrow_anim.setStartValue(self._arrow_angle)
        self._arrow_anim.setEndValue(180.0 if opening else 0.0)
        self._arrow_anim.start()

    def enterEvent(self, e):
        self._arrow_hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._arrow_hover = False
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, e):
        super().paintEvent(e)
        # 在右侧下拉区自绘旋转箭头（∨ 形折线，展开后旋转 180° 变为 ∧）
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        arrow_w = 22   # 与样式表 ::drop-down 宽度一致，预留箭头区域
        cx = self.rect().right() - arrow_w / 2
        cy = self.rect().center().y()
        p.save()
        p.translate(cx, cy)
        p.rotate(self._arrow_angle)
        color = ACCENT if (self._arrow_open or self._arrow_hover) else TEXT_DIM
        pen = QPen(QColor(color))
        pen.setWidthF(1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        s = 4.0
        path = QPainterPath()
        path.moveTo(-s, -s * 0.5)
        path.lineTo(0.0, s * 0.5)
        path.lineTo(s, -s * 0.5)
        p.drawPath(path)
        p.restore()
        p.end()


class _TurnWrap(QWidget):
    """AI 回合的包裹层：纵向排 [回合本体][重试行]（重试按钮位于回合正下方外侧）。

    **为什么必须自己钉高度**：回合体内含 word-wrap 富文本标签，整条链都带 heightForWidth。
    Qt 的 `QBoxLayout::heightForWidth` 给这层算出的高度会**随正文增长而虚高**
    （实测由 253px 一路涨到 828px，而回合本体始终 238px），且外层消息流会把它当实际
    高度用 —— 于是回合上下留出越来越大的一片空白（用户反馈的「AI 输出区上下大片空白、
    打字指示器一直往下跑」就是它在膨胀）。

    只覆盖 `sizeHint/heightForWidth` 拦不住它（Qt 内部走的是布局级 HFW 计算），因此这里
    再把自身高度**钉死**为「回合实际高度 + 间距 + 重试行高度」：回合高度本来就是
    `ChatTurn.relayout_heights` 按固定宽度算准的，包裹层不需要再猜。
    """

    def __init__(self, parent: QWidget = None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._turn: Optional[QWidget] = None

    def set_turn(self, turn: QWidget):
        """绑定被包裹的回合（高度据此计算；回合先建、后绑定）"""
        self._turn = turn
        self.sync_height()

    def _real_h(self) -> int:
        """包裹层应有的高度 = 回合内容高 + 间距 + 重试行高。

        **基准必须是「回合自己的内容高度」（heightForWidth），不能用本层给它的高度**：
        本层是 setFixedHeight，回合的实际高度正是本层分配出来的，用它做基准会让任何一次
        瞬时的偏小值变成**不动点** —— 偏小 → 回合被压扁 → 块与块互相盖住（用户反馈的
        「展开执行过程后文字严重折叠遮挡」），而且自己再也回不来。改用内容高度后，
        包裹层只会等于内容所需高度，既压不扁也不会残留空白。
        """
        turn = self._turn
        if turn is None:
            return 0
        h = 0
        w = int(turn.width())
        if w > 0:
            try:
                h = int(turn.heightForWidth(w))     # 按固定宽度算准的内容高（带缓存）
            except Exception:
                h = 0
        if h <= 0:
            # 尚未布局（宽度未知）：退回钉定值/实际值/建议值，宁可偏大也不能偏小
            h = max(int(turn.minimumHeight()), int(turn.height()),
                    int(turn.sizeHint().height()))
        v = self.layout()
        if v is not None and v.count() > 1:
            btm = v.itemAt(1)
            if btm is not None and not btm.isEmpty():
                h += v.spacing() + int(btm.sizeHint().height())
        return h

    def sync_height(self):
        """把自身高度同步为回合的真实高度（回合伸缩 / 重试行增删后都要跟上）"""
        h = self._real_h()
        if h > 0 and int(self.maximumHeight()) != h:
            self.setFixedHeight(h)

    def sizeHint(self) -> QSize:
        turn = self._turn
        if turn is None:
            return super().sizeHint()
        return QSize(max(0, int(turn.width())), self._real_h())

    def heightForWidth(self, _width: int) -> int:
        return self._real_h() or super().heightForWidth(_width)

    def event(self, e):
        # 回合伸缩 / 重试行显隐都会给本层发 LayoutRequest：跟着重钉高度
        if e.type() in (QEvent.Type.LayoutRequest, QEvent.Type.Show):
            self.sync_height()
        return super().event(e)


class FlowLayout(QLayout):
    """自动换行布局：子项宽度超出可用宽度时自动折行（附件缩略图条用）"""

    def __init__(self, parent=None, margin: int = 0, spacing: int = 8):
        super().__init__(parent)
        self._items = []
        self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing)

    def addWidget(self, w: QWidget):
        # 必须先 addChildWidget 建立父子关系，否则控件不会随布局显示/定位
        self.addChildWidget(w)
        self.addItem(QWidgetItem(w))

    def addItem(self, item):
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, w: int) -> int:
        return self._do_layout(QRect(0, 0, w, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for it in self._items:
            size = size.expandedTo(it.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        m = self.contentsMargins()
        x, y = rect.x() + m.left(), rect.y() + m.top()
        line_h = 0
        for it in self._items:
            w = it.sizeHint().width()
            if x + w > rect.right() - m.right():   # 放不下 → 折行
                x = rect.x() + m.left()
                y += line_h + self.spacing()
                line_h = 0
            if not test_only:
                it.setGeometry(QRect(QPoint(x, y), it.sizeHint()))
            x += w + self.spacing()
            line_h = max(line_h, it.sizeHint().height())
        return y + line_h + m.bottom() - rect.y()


class _DropTextEdit(QPlainTextEdit):
    """多行输入框：自动换行、高度自适应（42~140px）、Enter 发送（Shift+Enter 换行）、
    文件拖放（重写 drag/drop，不依赖事件冒泡）。"""
    submit = pyqtSignal()          # 用户按 Enter（发送）
    fileDropped = pyqtSignal(list)  # 拖入的文件路径列表

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.textChanged.connect(lambda: QTimer.singleShot(0, self._auto_height))
        # 初始校准：QPlainTextEdit 默认尺寸偏高，事件循环启动后立刻按内容压回单行高
        QTimer.singleShot(0, self._auto_height)

    def resizeEvent(self, e):
        # 窗口宽度变化 → 换行变化 → 高度需重新校准（去抖）
        super().resizeEvent(e)
        QTimer.singleShot(0, self._auto_height)

    def _auto_height(self):
        """高度随内容自适应：单行 42px，多行增高，最高 140px（超出内部滚动）"""
        doc = self.document()
        # 文档 textWidth 未设置时按无限宽布局（不换行、恒为单行）→ 对齐视口宽度
        vw = self.viewport().width()
        if vw > 0 and doc.textWidth() != vw:
            doc.setTextWidth(vw)
        # 注意：QPlainTextDocumentLayout.documentSize().height() 返回的是行数（非像素），
        # 需乘以行高换算像素高度，否则单行文本高度恒为 42 不增高
        lines = doc.documentLayout().documentSize().height()
        h = int(lines * self.fontMetrics().lineSpacing()) + 16   # 行高 × 行数 + 内边距
        self.setFixedHeight(min(max(h, 42), 140))

    def keyPressEvent(self, e):
        # Enter 发送；Shift+Enter 换行
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and \
                not (e.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            self.submit.emit()
            return
        super().keyPressEvent(e)

    def _has_files(self, e) -> bool:
        return e.mimeData().hasUrls()

    def dragEnterEvent(self, e):
        if self._has_files(e):
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        if self._has_files(e):
            e.acceptProposedAction()
        else:
            super().dragMoveEvent(e)

    def dropEvent(self, e):
        if self._has_files(e):
            paths = [u.toLocalFile() for u in e.mimeData().urls() if u.toLocalFile()]
            if paths:
                self.fileDropped.emit(paths)
            e.acceptProposedAction()
        else:
            super().dropEvent(e)


# ---------- 管理员权限下的原生拖放（Windows UIPI 绕行） ----------
# UIPI 会拦截普通 Explorer 拖入管理员（High IL）窗口的 OLE 拖放，Qt 常规 DnD 无解。
# 方案：RevokeDragDrop 移除 Qt 的 OLE 注册 → Explorer 回退发送 WM_DROPFILES 消息 →
# 原生事件过滤器解析文件路径与落点，合成 Qt 拖放事件投递给鼠标下方的控件。
_WM_DROPFILES = 0x0233
_WM_COPYDATA = 0x004A
_WM_COPYGLOBALDATA = 0x004D
_MSGFLT_ADD = 1


class _MSG(ctypes.Structure):
    """Win32 MSG 结构（64 位布局）"""
    _fields_ = [("hwnd", ctypes.c_void_p),
                ("message", ctypes.c_uint),
                ("wParam", ctypes.c_void_p),
                ("lParam", ctypes.c_void_p),
                ("time", ctypes.c_uint),
                ("pt_x", ctypes.c_long), ("pt_y", ctypes.c_long)]


def _drag_query_files(hdrop) -> list:
    """从 HDROP 句柄解析拖入的文件路径列表"""
    n = ctypes.windll.shell32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0)
    paths = []
    for i in range(n):
        ln = ctypes.windll.shell32.DragQueryFileW(hdrop, i, None, 0)
        buf = ctypes.create_unicode_buffer(ln + 1)
        ctypes.windll.shell32.DragQueryFileW(hdrop, i, buf, ln + 1)
        paths.append(buf.value)
    return paths


def _all_process_hwnds() -> list:
    """当前进程全部窗口句柄（顶层 + 所有后代），用于逐窗口设置 UIPI 放行与撤销 OLE 注册"""
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    pid = ctypes.windll.kernel32.GetCurrentProcessId()
    found = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def child_cb(hwnd, _lp):
        found.append(hwnd)
        return True

    def top_cb(hwnd, _lp):
        p = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid:
            found.append(hwnd)
            user32.EnumChildWindows(hwnd, WNDENUMPROC(child_cb), 0)
        return True

    user32.EnumWindows(WNDENUMPROC(top_cb), 0)
    return found


class _AdminDropFilter(QAbstractNativeEventFilter):
    """把 WM_DROPFILES 转成 Qt 拖放事件，投递给鼠标下方的可接收控件"""

    def __init__(self, panel):
        super().__init__()
        self._panel = panel

    def nativeEventFilter(self, eventType, message):
        try:
            # PyQt6 中 message 为 sip.voidptr，需先转整数地址再按 MSG 结构解析
            msg = ctypes.cast(int(message), ctypes.POINTER(_MSG)).contents
            if msg.message != _WM_DROPFILES:
                return False, 0
            print("[dnd] 收到 WM_DROPFILES", flush=True)
            hdrop = ctypes.c_void_p(msg.wParam)
            paths = _drag_query_files(hdrop)
            print("[dnd] 文件列表:", paths, flush=True)
            pt = _POINT()
            ctypes.windll.shell32.DragQueryPoint(hdrop, ctypes.byref(pt))
            ctypes.windll.shell32.DragFinish(hdrop)
            print(f"[dnd] DragQueryPoint=({pt.x},{pt.y}) 光标屏幕={QCursor.pos()}", flush=True)
            if paths:
                # 延迟到主循环投递，避免在原生消息处理中重入 Qt 事件循环
                QTimer.singleShot(0, lambda: self._on_received(paths, pt.x, pt.y))
            return True, 0
        except Exception as e:
            print("[dnd] nativeEventFilter 异常:", repr(e), flush=True)
            return False, 0

    def _on_received(self, paths: list, x: int, y: int):
        """收到系统 WM_DROPFILES：显示诊断状态并投递（证明 OS 已把拖放送达应用）"""
        self._deliver(x, y, paths)

    def _deliver(self, x: int, y: int, paths: list):
        """主循环内投递：用鼠标屏幕坐标定位控件（不依赖 DragQueryPoint 坐标语义），
        找不到可接收控件时回退面板自身；异常打印便于定位"""
        panel = self._panel
        try:
            if panel is None or not paths:
                return
            screen = QCursor.pos()
            w = QApplication.instance().widgetAt(screen)
            if w is None:   # 兜底：按 DragQueryPoint 坐标在面板内查找
                w = panel.childAt(QPoint(x, y))
            while w is not None and not w.acceptDrops():
                w = w.parentWidget()
            if w is None:
                w = panel
            print(f"[dnd] 投递目标: {type(w).__name__} acceptDrops={w.acceptDrops()}", flush=True)
            md = QMimeData()
            md.setUrls([QUrl.fromLocalFile(p) for p in paths])
            local = w.mapFromGlobal(screen)
            app = QApplication.instance()
            for evt in (QDragEnterEvent(local, Qt.DropAction.CopyAction, md,
                                        Qt.MouseButton.LeftButton,
                                        Qt.KeyboardModifier.NoModifier),
                        QDragMoveEvent(local, Qt.DropAction.CopyAction, md,
                                       Qt.MouseButton.LeftButton,
                                       Qt.KeyboardModifier.NoModifier),
                        QDropEvent(QPointF(local), Qt.DropAction.CopyAction, md,
                                   Qt.MouseButton.LeftButton,
                                   Qt.KeyboardModifier.NoModifier)):
                app.sendEvent(w, evt)
            print("[dnd] 已投递 Qt 拖放事件", flush=True)
        except Exception as e:
            print("[dnd] 投递异常:", repr(e), flush=True)


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class TodosPanel(QWidget):
    """左侧 TODOS 可视化面板：AI 使用 update_todo / list_todo 工具时显示任务进度。
    内嵌于 AgentPanel 布局（非独立窗口 → 天然无最小化/最大化/关闭按钮）。
    纯黑+淡灰+白+深蓝四色极简风格，无 emoji，状态用几何标记区分。
    鼠标悬停时右上角显示小 × 可手动清空任务清单。"""

    clear_requested = pyqtSignal()   # 用户手动清空任务清单
    height_changed = pyqtSignal(int) # 面板高度变化（排队面板联动）

    DOCK_MAX_ROWS = 5   # 融入主面板时最多按 5 行计高（余量留给同栏 Git 面板）
    MARGINS = (12, 12, 12, 10)        # 常规边距（上/右/下/左）
    MARGINS_DOCK = (12, 0, 12, 0)     # 融入栏内：上下贴边（与相邻面板之间不留底色缝隙）

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("todosPanel")
        # QWidget 默认不绘制 stylesheet 背景 → 加 WA_StyledBackground 才能画出纯黑底
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"QWidget#todosPanel {{ background: {BG}; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(*self.MARGINS)
        lay.setSpacing(8)

        header = QHBoxLayout()
        header.setSpacing(8)
        title = QLabel("任务清单")
        title.setStyleSheet(f"color: {TEXT}; font-size: 13px; font-weight: 800;")
        header.addWidget(title)
        self._count = QLabel("")
        self._count.setStyleSheet(f"color: {TEXT_DIM}; font-size: 11px;")
        header.addWidget(self._count)
        header.addStretch(1)
        # 右上角清空按钮：常显、尺寸足够大便于点击（鼠标激活区域不再过小）。
        # 默认主题：透明底 + 灰 ×；液态玻璃版由 _GlassTodos 覆写为透明底 + 浅色 ×
        self._close_btn = QPushButton(_line_icon("no", 13, TEXT_DIM), "")
        self._close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_btn.setAutoDefault(False)
        self._close_btn.setFixedSize(30, 26)
        self._close_btn.setIconSize(QSize(13, 13))
        self._close_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {TEXT_DIM};"
            "border: none; border-radius: 6px; }}"
            f"QPushButton:hover {{ background: rgba(255,255,255,42); color: {TEXT}; }}")
        self._close_btn.setToolTip("清空任务清单")
        self._close_btn.clicked.connect(self.clear_requested.emit)
        header.addWidget(self._close_btn)
        lay.addLayout(header)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            + _scrollbar_css(6, 3, both=False))
        self._list = QWidget()
        self._list.setStyleSheet("background: transparent;")
        self._list_lay = QVBoxLayout(self._list)
        self._list_lay.setContentsMargins(2, 0, 2, 0)
        self._list_lay.setSpacing(6)
        self._list_lay.addStretch(1)
        self._scroll.setWidget(self._list)
        lay.addWidget(self._scroll, 1)
        self._last_h = -1      # 上次应用的列表内容高度（变化时才通知宿主，避免重复 relayout）
        self.update_todos([])

    def update_todos(self, todos: list):
        """全量刷新任务列表；面板默认常显，无任务时显示提示语占位"""
        while self._list_lay.count() > 1:
            item = self._list_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        todos = [t for t in todos if isinstance(t, dict) and str(t.get("title") or "").strip()]
        if todos:
            for t in todos:
                self._list_lay.insertWidget(self._list_lay.count() - 1, self._row(t))
        else:
            empty = QLabel("复杂任务进度将会在这显示")
            empty.setStyleSheet(f"color: {TEXT_DIM}; font-size: 12px;")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            # 固定占位高度：QLabel 的 sizeHint 只按 12px 字号给一行高度，直接放入
            # 会被压缩到 12px 导致文字被裁切；给足最小高度避免底部文字被挤压
            empty.setMinimumHeight(36)
            self._list_lay.insertWidget(0, empty)
        done = sum(1 for t in todos if t.get("status") == "completed")
        self._count.setText(f"{done}/{len(todos)}" if todos else "")
        self.setVisible(True)   # 面板默认常显
        self._resize_to_content()
        # 布局未就绪（窗口未显示/未定位）时首次测量用的是不可靠的瞬时宽度，
        # 延迟到事件循环推进、布局完成后以真实几何复核一次，避免把错误面板高度
        # 钉死（底部文字被挤压或底部留大片空白）
        QTimer.singleShot(0, self._resize_to_content)

    def _content_height(self, limit: int = 0) -> int:
        """按任务行实际换行高度估算列表自然高度（含行间距）
        宽度取窗口固定宽度而非瞬时 self.width()：构造期/布局未激活时面板宽度
        是未确定的中间值（实测约 172，真实为 280），用它算出的行宽会整体失真，
        导致高度被钉死在错误数值（底部文字被挤压 / 底部留白）。
        limit>0 时只计前 limit 行（融入主面板按行数封顶用，余量留给同栏 Git 面板）；
        清单超出面板高度时由滚动区滚动查看（窗口尺寸固定，不随清单长短变化）。"""
        w = self.width()
        par = self.parent()
        pw = getattr(par, "WIDTH", 0) or 0
        if pw and (w <= 0 or abs(w - pw) > 4):
            w = pw   # 面板最终宽度 = 窗口固定宽度（TodoWindow 布局 margin 为 0）
        total, counted = 0, 0
        # 行宽 = 面板宽 - 面板内边距(24) - 列表边距(4) - 行边距(8)
        #           - 状态标记宽(14) - 行内间距(8)
        row_w = max(80, w - 24 - 4 - 8 - 14 - 8 - 4)
        for i in range(self._list_lay.count() - 1):   # 末尾保留 stretch
            w = self._list_lay.itemAt(i).widget()
            if w is None:
                continue
            if limit and counted >= limit:
                break                                 # 封顶：只按前 limit 行计高
            counted += 1
            lbl = w.findChild(QLabel, "todoTitle")
            if lbl is not None:
                h = lbl.heightForWidth(row_w)
                if not (isinstance(h, (int, float)) and h > 0):
                    h = lbl.sizeHint().height()
            else:
                # 占位提示等非任务行：按控件实际约束高度（含 minimumHeight），
                # 避免高度被低估导致文字挤压/裁切
                h = w.sizeHint().height()
                mh = w.minimumHeight()
                if mh > 0:
                    h = max(h, mh)
            total += max(20, h + 4)                    # 行高 + 行上下内边距
        total += max(0, counted - 1) * self._list_lay.spacing()
        return total

    def required_height(self) -> int:
        """面板按内容所需的高度 = 上边距(12) + 标题行(26) + 间距(8) + 列表 + 下边距(10)"""
        return self.panel_height_for(self._content_height())

    def dock_max_height(self) -> int:
        """融入主面板时可占用的最大高度 = 前 DOCK_MAX_ROWS 行所需高度。
        融入栏里三块面板共享栏高，清单再长也不该挤占同栏 Git 面板（用户反馈
        「git 面板变小了」）→ 按行数封顶，超出部分在面板内滚动。"""
        return self.panel_height_for(self._content_height(limit=self.DOCK_MAX_ROWS))

    def panel_height_for(self, content_h: int) -> int:
        """列表内容高度 → 面板窗口高度 = 上下边距 + 标题行 + 间距 + 内容。
        各分项都从实际布局取（融入时上下边距为 0 → 公式自动少 22px，省下的高度归 Git 面板）。"""
        lay = self.layout()
        m = lay.contentsMargins() if lay is not None else None
        top = m.top() if m is not None else self.MARGINS[1]
        bottom = m.bottom() if m is not None else self.MARGINS[3]
        spacing = lay.spacing() if lay is not None else 8
        header = int(self._close_btn.minimumHeight() or self._close_btn.height() or 26)
        return max(56, int(top) + int(bottom) + header + int(spacing) + max(0, int(content_h)))

    def set_dock_tight(self, tight: bool):
        """融入主面板时上下边距归零（贴边）：面板内容占满窗口上下，
        与相邻面板之间不再露出底色缝隙；贴附模式恢复常规边距。"""
        lay = self.layout()
        if lay is None:
            return
        lay.setContentsMargins(*(self.MARGINS_DOCK if tight else self.MARGINS))
        self._resize_to_content()   # 高度公式含边距 → 重算并通知宿主重新钉高

    def _resize_to_content(self):
        """内容变化：更新列表内容高度，并把「内容所需高度」通知宿主窗口。
        面板自身高度由窗口尺寸决定（固定默认尺寸），清单超出视口时在面板内滚动 ——
        不再按内容 setFixedHeight，避免面板/窗口随清单长短伸缩或在底部留空白带。"""
        content = self._content_height()
        # widgetResizable 会把内部列表压缩到视口：minimumHeight 设为内容高度后，
        # 内容超出视口即出现滚动条（不被裁剪、不留空白）。
        self._list.setMinimumHeight(content)
        h = self.required_height()
        if h != self._last_h:            # 内容所需高度变化才通知宿主，避免重复 relayout
            self._last_h = h
            self.height_changed.emit(h)

    def _row(self, t: dict) -> QWidget:
        title = str(t.get("title") or "")
        st = str(t.get("status") or "pending")
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        rl = QHBoxLayout(row)
        rl.setContentsMargins(4, 2, 4, 2)
        rl.setSpacing(8)
        mark = QLabel("●")
        mark.setFixedWidth(14)
        if st == "completed":
            mark.setText("✓")
            mark.setStyleSheet(f"color: {TEXT_DIM}; font-size: 12px;")
        elif st == "in_progress":
            mark.setStyleSheet(f"color: {ACCENT_HOVER}; font-size: 12px;")
        else:
            mark.setStyleSheet(f"color: {TEXT_DIM}; font-size: 12px;")
        rl.addWidget(mark, 0, Qt.AlignmentFlag.AlignTop)
        lbl = QLabel(_esc(title))
        lbl.setObjectName("todoTitle")
        lbl.setWordWrap(True)
        color = TEXT_DIM if st == "completed" else TEXT
        lbl.setStyleSheet(f"color: {color}; font-size: 12px;")
        rl.addWidget(lbl, 1)
        return row


def _is_binary(path: str) -> bool:
    """粗判二进制：文件开头含 NUL 字节即视为二进制"""
    try:
        with open(path, "rb") as f:
            return b"\x00" in f.read(2048)
    except OSError:
        return True


class _CodeHighlighter(QSyntaxHighlighter):
    """轻量通用语法高亮：注释 / 字符串 / 数字 / 函数调用 / 关键字。
    按扩展名选择关键字集合，深蓝+橙+淡绿+淡黄，极简四色系。"""

    _KEYWORDS = {
        "py": {"def", "import", "from", "class", "if", "elif", "else", "for", "while",
               "return", "try", "except", "finally", "with", "as", "lambda", "yield",
               "global", "nonlocal", "pass", "break", "continue", "and", "or", "not",
               "in", "is", "None", "True", "False", "raise", "assert", "del"},
        "js": {"function", "const", "let", "var", "if", "else", "for", "while", "return",
               "class", "extends", "new", "this", "async", "await", "import", "export",
               "from", "try", "catch", "finally", "switch", "case", "break", "default",
               "typeof", "instanceof", "null", "undefined", "true", "false"},
        "ts": {"type", "interface", "enum", "namespace", "implements", "public",
               "private", "protected", "readonly", "declare", "abstract", "as",
               "function", "const", "let", "var", "if", "else", "for", "while",
               "return", "class", "extends", "new", "this", "async", "await",
               "import", "export", "from", "try", "catch", "finally", "switch",
               "case", "break", "default", "null", "undefined", "true", "false"},
        "java": {"public", "private", "protected", "class", "interface", "extends",
                 "implements", "static", "final", "void", "int", "boolean", "String",
                 "if", "else", "for", "while", "do", "switch", "case", "break",
                 "return", "new", "try", "catch", "finally", "throw", "throws",
                 "this", "super", "import", "package", "null", "true", "false"},
        "c": {"int", "char", "float", "double", "void", "struct", "union", "enum",
              "typedef", "static", "const", "extern", "return", "if", "else", "for",
              "while", "do", "switch", "case", "break", "continue", "sizeof",
              "include", "define", "NULL", "true", "false"},
        "cpp": {"int", "char", "float", "double", "void", "bool", "struct", "class",
                "public", "private", "protected", "namespace", "using", "template",
                "typename", "static", "const", "constexpr", "auto", "return", "if",
                "else", "for", "while", "do", "switch", "case", "break", "continue",
                "new", "delete", "this", "nullptr", "true", "false", "include",
                "define"},
        "go": {"func", "package", "import", "var", "const", "type", "struct", "interface",
               "map", "chan", "go", "defer", "select", "if", "else", "for", "range",
               "switch", "case", "break", "continue", "return", "nil", "true", "false"},
        "rs": {"fn", "let", "mut", "const", "static", "struct", "enum", "trait",
               "impl", "mod", "use", "pub", "crate", "return", "if", "else", "for",
               "while", "loop", "match", "break", "continue", "unsafe", "fn"},
        "json": {"true", "false", "null"},
        "html": {"html", "head", "body", "div", "span", "p", "a", "img", "table",
                 "tr", "td", "th", "ul", "ol", "li", "form", "input", "button",
                 "script", "style", "link", "meta", "title"},
        "css": {"color", "background", "margin", "padding", "border", "display",
                "position", "width", "height", "font", "float", "flex", "grid",
                "overflow", "opacity", "transform"},
    }
    _DEFAULT_KW = {"if", "else", "for", "while", "return", "class", "function",
                   "import", "from", "new", "try", "catch", "finally", "switch",
                   "case", "break", "continue", "true", "false", "null", "None"}

    def __init__(self, document, ext: str = ""):
        super().__init__(document)
        self._kw = self._KEYWORDS.get(ext, self._DEFAULT_KW)
        self._rules = [
            (QRegularExpression(r"//[^\n]*"), "#6A9955"),          # 行注释
            (QRegularExpression(r"#[^\n]*"), "#6A9955"),
            (QRegularExpression(r"/\*[\s\S]*?\*/"), "#6A9955"),    # 块注释
            (QRegularExpression(r'"[^"\\]*(\\.[^"\\]*)*"'), "#CE9178"),  # 双引号字符串
            (QRegularExpression(r"'[^'\\]*(\\.[^'\\]*)*'"), "#CE9178"),  # 单引号字符串
            (QRegularExpression(r"\b\d+(\.\d+)?\b"), "#B5CEA8"),   # 数字
            (QRegularExpression(r"\b[A-Za-z_]\w*(?=\s*\()"), "#DCDCAA"),  # 函数调用
        ]
        self._kw_fmt = QTextCharFormat()
        self._kw_fmt.setForeground(QColor("#569CD6"))
        # 预编译关键字正则（每个文档只编译一次）。若在 highlightBlock 里按行重建，
        # 大文件会触发「行数×关键字数」次正则编译，拖垮 UI 线程导致预览卡死。
        self._kw_rx = [QRegularExpression(r"\b" + re.escape(w) + r"\b") for w in self._kw]

    def highlightBlock(self, text: str):
        for pattern, color in self._rules:
            it = pattern.globalMatch(text)
            while it.hasNext():
                m = it.next()
                fmt = QTextCharFormat()
                fmt.setForeground(QColor(color))
                self.setFormat(m.capturedStart(), m.capturedLength(), fmt)
        for rx in self._kw_rx:
            it = rx.globalMatch(text)
            while it.hasNext():
                m = it.next()
                self.setFormat(m.capturedStart(), m.capturedLength(), self._kw_fmt)


# ---------- 多格式预览解析：Web / Markdown / 图片 / Office(docx / pptx / xlsx) ----------
# Office 文件均为 OpenXML 压缩包，用标准库 zipfile+xml 提取纯文本/表格，无需任何三方依赖。
_IMAGE_EXTS = frozenset({"png", "jpg", "jpeg", "gif", "bmp", "webp", "ico", "svg"})
_OFFICE_EXTS = frozenset({"docx", "docm", "pptx", "pptm", "xlsx", "xlsm", "pdf"})
# 媒体文件：视频（QtMultimedia 解码）与音频（预览面板内播放）
_VIDEO_EXTS = frozenset({"mp4", "mkv", "avi", "mov", "webm", "m4v", "wmv", "mpg", "mpeg"})
_AUDIO_EXTS = frozenset({"mp3", "wav", "ogg", "flac", "m4a", "aac", "wma"})
_MEDIA_EXTS = _VIDEO_EXTS | _AUDIO_EXTS
# 预留扩展点：后续需要更精细渲染（公式/图表/批注）时可在此注册第三方解析器覆盖默认提取
_OFFICE_READERS = {}


def _zip_read_bytes(path: str, name: str):
    """从 OpenXML 压缩包中读取指定成员字节；不存在返回 None"""
    import zipfile
    try:
        with zipfile.ZipFile(path) as z:
            return z.read(name)
    except Exception:
        return None


def _xml_elems(root, tag):
    """按本地名（去掉命名空间前缀）递归查找元素"""
    local = tag.rsplit('}', 1)[-1]
    for el in root.iter():
        if el.tag.rsplit('}', 1)[-1] == local:
            yield el


def _xml_text(el, local="t"):
    """取元素下第一个指定本地名子元素的文本"""
    for sub in _xml_elems(el, local):
        return sub.text or ""
    return ""


def _shared_strings(path: str) -> list:
    """读取 xlsx 共享字符串表 -> [str,...]"""
    import xml.etree.ElementTree as ET
    data = _zip_read_bytes(path, "xl/sharedStrings.xml")
    if not data:
        return []
    out = []
    try:
        root = ET.fromstring(data)
        for si in _xml_elems(root, "si"):
            out.append("".join((t.text or "") for t in _xml_elems(si, "t")))
    except Exception:
        pass
    return out


def _col_of(ref: str) -> int:
    """'A1'/'AB12' -> 0-based 列号"""
    col = 0
    for ch in ref:
        if ch.isalpha():
            col = col * 26 + (ord(ch.upper()) - 64)
        else:
            break
    return max(col - 1, 0)


def _preview_xlsx(path: str, max_rows: int = 200, max_cols: int = 40) -> str:
    """xlsx -> HTML 表格（首张工作表；含共享字符串/内联字符串，行列表格上限保护）"""
    import xml.etree.ElementTree as ET
    import zipfile
    shared = _shared_strings(path)
    try:
        with zipfile.ZipFile(path) as z:
            sheet_files = sorted([n for n in z.namelist()
                                  if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")],
                                 key=lambda n: int(re.sub(r"\D", "", n.split("/")[-1]) or 0))
    except Exception:
        return _esc("无法读取 xlsx 结构")
    if not sheet_files:
        return _esc("未找到工作表")
    rows_map, max_c = {}, 0
    try:
        root = ET.fromstring(_zip_read_bytes(path, sheet_files[0]))
        for row in _xml_elems(root, "row"):
            r_idx = int(row.get("r") or len(rows_map) + 1)
            cells = {}
            for c in _xml_elems(row, "c"):
                ref = c.get("r", "")
                t = c.get("t", "")
                col = _col_of(ref)
                if col >= max_cols:
                    continue
                cell_txt = ""
                if t == "s":
                    v = _xml_text(c, "v")
                    try:
                        cell_txt = shared[int(v)] if shared else "".join(
                            (tt.text or "") for tt in _xml_elems(c, "t"))
                    except Exception:
                        cell_txt = ""
                elif t == "inlineStr":
                    cell_txt = "".join((tt.text or "") for tt in _xml_elems(c, "t"))
                elif t == "b":
                    cell_txt = {"1": "TRUE", "0": "FALSE"}.get(_xml_text(c, "v"), "")
                else:
                    cell_txt = _xml_text(c, "v")
                cells[col] = cell_txt
                max_c = max(max_c, col)
            if r_idx > max_rows:
                if len(rows_map) >= max_rows:
                    break
            rows_map[r_idx] = cells
    except Exception:
        return _esc("xlsx 解析失败")
    if not rows_map:
        return _esc("工作表为空")
    rows_list = sorted(rows_map.items())[:max_rows]
    thead = "<tr>" + "".join(f"<th style='font-weight:600;'>{_esc(_col_letter(i))}</th>"
                             for i in range(max_c + 1)) + "</tr>"
    body = []
    for r_idx, cells in rows_list:
        tds = []
        for i in range(max_c + 1):
            v = cells.get(i)
            tds.append(f"<td>{_esc(v) if v is not None else ''}</td>")
        body.append("<tr>" + "".join(tds) + "</tr>")
    return ("<table border='0' cellspacing='0' cellpadding='0'>"
            + thead + "".join(body) + "</table>")


def _col_letter(idx: int) -> str:
    """0-based 列号 -> 'A'/'Z'/'AA'"""
    s = ""
    idx += 1
    while idx:
        idx, rem = divmod(idx - 1, 26)
        s = chr(ord('A') + rem) + s
    return s


def _preview_pptx(path: str, max_slides: int = 60) -> str:
    """pptx -> HTML 逐页文本。"""
    import zipfile
    try:
        with zipfile.ZipFile(path) as z:
            slides = sorted([n for n in z.namelist()
                             if re.match(r"ppt/slides/slide\d+\.xml$", n)],
                            key=lambda n: int(re.sub(r"\D", "", n.split("/")[-1]) or 0))
    except Exception:
        return _esc("无法读取 pptx 结构")
    if not slides:
        return _esc("未找到幻灯片")
    parts = []
    for idx, name in enumerate(slides[:max_slides], 1):
        root = None
        try:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(_zip_read_bytes(path, name))
        except Exception:
            continue
        lines = []
        for t in _xml_elems(root, "t"):
            txt = (t.text or "").strip()
            if txt:
                lines.append(_esc(txt))
        if lines:
            parts.append(f"<p style='margin:0 0 2px;'><b>第 {idx} 页</b></p>"
                         + "".join(f"<p style='margin:0 0 2px 10px;'>{x}</p>" for x in lines))
    if not parts:
        return _esc("幻灯片内无文本内容")
    return "".join(parts)


def _preview_docx(path: str, max_chars: int = 60000) -> str:
    """docx -> HTML 段落（支持多级段落与基本表格）。"""
    import xml.etree.ElementTree as ET
    data = _zip_read_bytes(path, "word/document.xml")
    if not data:
        return _esc("无法读取 docx 文档")
    try:
        root = ET.fromstring(data)
    except Exception:
        return _esc("docx 解析失败")
    body = next(_xml_elems(root, "body"), root)
    out, total = [], 0
    for node in body:
        local = node.tag.rsplit('}', 1)[-1]
        if local == "sectPr":
            continue
        if local == "p":
            text = "".join((t.text or "") for t in _xml_elems(node, "t"))
            if total + len(text) > max_chars:
                out.append("<p style='color:" + TEXT_DIM + ";'>…（内容过长，已截断预览）</p>")
                break
            total += len(text)
            style = ""
            for pPr in _xml_elems(node, "pPr"):
                for pStyle in _xml_elems(pPr, "pStyle"):
                    if (pStyle.get("val") or "").lower().startswith(("heading", "title")):
                        style = "font-weight:600;font-size:1.1em;margin:6px 0 2px;"
                    break
            if text.strip():
                out.append(f"<p style='margin:2px 0;{style}'>{_esc(text)}</p>")
            else:
                out.append("<p style='margin:0;'>&nbsp;</p>")
        elif local == "tbl":
            out.append(_docx_table_html(node))
    return "".join(out) or _esc("文档正文为空")


def _docx_table_html(tbl) -> str:
    """粗略渲染 docx 表格为 HTML"""
    rows = []
    for tr in _xml_elems(tbl, "tr"):
        cells = []
        for tc in _xml_elems(tr, "tc"):
            text = "".join((t.text or "") for t in _xml_elems(tc, "t"))
            cells.append(f"<td style='border:1px solid {BORDER_SOFT};padding:2px 6px;'>{_esc(text)}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return (f"<table style='border-collapse:collapse;border:1px solid {BORDER_SOFT};"
            f"margin:4px 0;'>" + "".join(rows) + "</table>")


def _office_preview_html(path: str, ext: str) -> str:
    """按扩展名调取 Office 预览 HTML（降级路径）。

    优先 office.compat（QTextDocument 友好的保样式渲染：颜色/字体/底纹/边框/表格），
    失败或未覆盖时再退回内置极简文本渲染；未覆盖类型返回 None。
    """
    reader = _OFFICE_READERS.get(ext)
    if reader is not None:
        try:
            return reader(path)
        except Exception:
            pass
    try:
        from zhuzhu_Copilot.office import render_office_compat_html
        rich = render_office_compat_html(path, ext, _office_preview_theme())
        if rich:
            return rich
    except Exception:
        pass
    if ext in ("xlsx", "xlsm"):
        return _preview_xlsx(path)
    if ext in ("pptx", "pptm"):
        return _preview_pptx(path)
    if ext in ("docx", "docm"):
        return _preview_docx(path)
    return None


def _preview_wrap_html(inner: str, title: str = "") -> str:
    """把 Office/Markdown 渲染片段包成随主题配色的完整 HTML 页"""
    code_bg = _panel_theme_col("CODE_BG", "#17181A")
    text = _panel_theme_col("TEXT", "#F5F5F5")
    card = _panel_theme_col("CARD", "#1E1E1E")
    inner = (f"<p style='color:{TEXT_DIM};font-size:11px;margin:2px 0 8px;'>{_esc(title)}</p>"
             + inner)
    return ("<!DOCTYPE html><html><head><meta charset='utf-8'><style>"
            f"body{{background:{card};color:{text};font-size:13px;font-family:'Microsoft YaHei',"
            "Consolas,monospace;margin:8px;} "
            f"td{{color:{text};}} code{{background:{code_bg};border-radius:4px;padding:1px 5px;}} "
            f"pre{{background:{code_bg};border-radius:6px;padding:8px;}} "
            f"table{{border-collapse:collapse;}} th,td{{border:1px solid {BORDER_SOFT};"
            f"padding:3px 8px;}}</style></head><body>{inner}</body></html>")
def _office_preview_url(html: str):
    """把预览 HTML 写入临时文件并返回路径（失败返回 None，调用方回退 setHtml）。

    用本地文件 URL 而非 setHtml：QtWebEngineView.setHtml 对内容有约 2MB 限制，
    含内联图片（data URI）的 PPT/Word 极易超过 —— 超限会白屏，这正是"预览不出
    完整样式"的一种成因。临时文件固定在预览专用目录，每次覆盖写，避免堆积。
    """
    try:
        import tempfile
        d = os.path.join(tempfile.gettempdir(), "zhuzhu_copilot_preview")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, "office_preview.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        return path
    except Exception:
        return None


def office_preview_diag(msg: str):
    """记录 Office 预览诊断（追加到预览专用目录的 diag.log）。

    保真渲染失败在过去是「静默回退纯文本」——界面看不出原因、无法定位，
    用户只能反馈"没有样式"。此处统一留痕：环境问题（WebEngine 不可用/渲染失败）
    可直接读日志定位，不必再靠猜。
    """
    try:
        import tempfile
        import time as _t
        d = os.path.join(tempfile.gettempdir(), "zhuzhu_copilot_preview")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "diag.log"), "a", encoding="utf-8") as f:
            f.write("%s  %s\n" % (_t.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def _office_preview_theme() -> dict:
    """把当前面板主题色映射为 office.preview 的渲染主题（深/浅主题自动适配）。

    单一数据源：本模块的颜色是模块级常量（BG/PANEL/CARD/TEXT/TEXT_DIM/BORDER/
    ACCENT，由 _apply_colors 随主题切换更新），故直接读 globals()——**不要**引用
    PALETTE（本模块从未定义该名）。

    约束：本函数在预览热路径上，**任何情况都不得抛异常**。一旦抛错会同时否决
    保真渲染与降级渲染（曾因误用未定义的 PALETTE，导致两条路径全废、预览只剩
    纯文本）。故此处逐级回退：模块常量 → ui.styles.PALETTE → office 内置默认。
    """
    g = globals()

    def _pick(names, default):
        for n in names:
            v = g.get(n)
            if isinstance(v, str) and v.startswith("#"):
                return v
        return default

    theme = {"bg": _pick(("BG_BOTTOM", "BG"), ""),
             "card": _pick(("CARD", "PANEL"), ""),
             "text": _pick(("TEXT",), ""),
             "dim": _pick(("TEXT_DIM",), ""),
             "border": _pick(("BORDER",), ""),
             "accent": _pick(("ACCENT",), "")}
    if not all(theme.values()):
        # 模块常量尚未初始化（未 apply_theme / 自定义包异常）→ 用基础色板补齐
        try:
            from zhuzhu_Copilot.ui.styles import PALETTE as _P
            theme["bg"] = theme["bg"] or _P.get("bg_bottom") or _P.get("bg_top") or "#000000"
            theme["card"] = theme["card"] or _P.get("card") or "#1E1E1E"
            theme["text"] = theme["text"] or _P.get("text") or "#F5F5F5"
            theme["dim"] = theme["dim"] or _P.get("text_secondary") or "#9A9A9A"
            theme["border"] = theme["border"] or _P.get("border") or "#2A2A2A"
            theme["accent"] = theme["accent"] or _P.get("primary") or "#1F3A5F"
        except Exception:
            pass
    # 最后一层兜底：office.preview 内置深色（保证键值完整、绝不为空）
    from zhuzhu_Copilot.office.preview import THEME_DARK as _D
    for k, v in _D.items():
        theme[k] = theme.get(k) or v
    return theme


def _panel_theme_col(key: str, default: str) -> str:
    """读当前主题色常量（跟随主题切换）；缺失回退默认值"""
    for g in ("CODE_BG", "TEXT", "CARD", "BORDER_SOFT"):
        if key == g and g in globals():
            return globals()[g]
    return globals().get(key, default)


class _ZoomableImageView(QLabel):
    """可拖拽 / 缩放的图片预览控件。

    - 鼠标左键拖拽：平移图像
    - 鼠标滚轮：以光标为中心放大 / 缩小（0.4x ~ 8x）
    - 双击左键：复位为适配视口大小
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._src = None          # 原始 QPixmap（未缩放）
        self._scale = 1.0         # 当前缩放系数
        self._ox = 0.0            # 平移偏移（相对居中位置，像素）
        self._oy = 0.0
        self._dragging = False
        self._last_pos = None
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

    # ---------- 对外接口 ----------
    def set_pixmap_src(self, pix: QPixmap):
        """设置原始图片并复位（首次显示适配视口）"""
        self._src = pix
        self._scale = self._fit_scale()
        self._ox = 0.0
        self._oy = 0.0
        self.update()

    def show_fit(self):
        """复位为适配视口（双击/程序调用）"""
        if self._src is None or self._src.isNull():
            return
        self._scale = self._fit_scale()
        self._ox = 0.0
        self._oy = 0.0
        self.update()

    def _fit_scale(self) -> float:
        """适配视口的缩放系数（完整可见，不超过 1.0 防止小图被放大）"""
        if self._src is None or self._src.isNull():
            return 1.0
        w = max(1, self.width())
        h = max(1, self.height())
        return max(0.01, min(1.0, w / self._src.width(), h / self._src.height()))

    # ---------- 交互 ----------
    def wheelEvent(self, e):
        if self._src is None or self._src.isNull():
            return
        old = self._scale
        factor = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
        new = min(8.0, max(self._fit_scale() * 0.5, old * factor))
        if abs(new - old) < 1e-9:
            return
        # 以光标位置为锚点：保证光标下的图像内容不动
        pos = e.position()
        px = pos.x() - self.width() / 2.0
        py = pos.y() - self.height() / 2.0
        k = new / old
        self._ox = px - (px - self._ox) * k
        self._oy = py - (py - self._oy) * k
        self._scale = new
        self.update()
        e.accept()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self._src is not None:
            self._dragging = True
            self._last_pos = e.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._dragging and self._last_pos is not None:
            pos = e.position()
            self._ox += pos.x() - self._last_pos.x()
            self._oy += pos.y() - self._last_pos.y()
            self._last_pos = pos
            self.update()
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self._last_pos = None
            self.unsetCursor()
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self._src is not None:
            self.show_fit()
            e.accept()
            return
        super().mouseDoubleClickEvent(e)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        # 首次加载（尚无偏移）时随视口尺寸变化重新适配；用户已缩放/平移则不打扰
        if self._src is not None and not self._src.isNull() and self._ox == 0.0 and self._oy == 0.0:
            self._scale = self._fit_scale()
            self.update()

    # ---------- 绘制 ----------
    def paintEvent(self, e):
        if self._src is None or self._src.isNull():
            super().paintEvent(e)
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        w = max(1, int(self._src.width() * self._scale))
        h = max(1, int(self._src.height() * self._scale))
        x = int(round((self.width() - w) / 2.0 + self._ox))
        y = int(round((self.height() - h) / 2.0 + self._oy))
        p.drawPixmap(x, y, w, h, self._src)
        p.end()


def _fmt_sec(sec: int) -> str:
    """秒 → m:ss / h:mm:ss 时间标签（音乐进度条用）"""
    sec = max(0, int(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _build_diff_lines(old_text: str, new_text: str) -> tuple:
    """生成 AI 修改文件的行级差异视图。

    返回 (lines, first_change)；lines 为 [(sign, text)]，sign ∈ "+"/"-"/" "，
    text 已带行首符号；first_change 为首个变更行下标（无变更返回 None）。

    旧实现对 `(old_text or "").splitlines() or [""]` 用占位空行兜底，导致
    SequenceMatcher 把那行空串判为删除段 → 新建文件场景出现伪 -1 红行、首
    个变更行错指。空文本就当空行列表（差分器在两侧都空时无 opcodes、单
    侧空时输出纯 insert/delete），不强行塞占位。
    """
    import difflib
    old_lines = (old_text or "").splitlines()
    new_lines = (new_text or "").splitlines()
    sm = difflib.SequenceMatcher(None, old_lines, new_lines)
    lines, first = [], None
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i1, i2):
                lines.append((" ", old_lines[k]))
        elif tag == "delete":
            for k in range(i1, i2):
                lines.append(("-", f"- {old_lines[k]}"))
                if first is None:
                    first = len(lines) - 1
        elif tag == "insert":
            for k in range(j1, j2):
                lines.append(("+", f"+ {new_lines[k]}"))
                if first is None:
                    first = len(lines) - 1
        elif tag == "replace":
            for k in range(i1, i2):
                lines.append(("-", f"- {old_lines[k]}"))
                if first is None:
                    first = len(lines) - 1
            for k in range(j1, j2):
                lines.append(("+", f"+ {new_lines[k]}"))
                if first is None:
                    first = len(lines) - 1
    return lines, first


# ---------- token / 上下文统计浮层（顶栏统计按钮点击后丝滑弹出） ----------

def _fade(color: str, alpha: float) -> QColor:
    """给主题色叠加透明度：进度条轨道/次要分段等派生色统一走这里，深浅主题共用"""
    c = QColor(color)
    c.setAlphaF(max(0.0, min(1.0, float(alpha))))
    return c


def _fmt_tokens(n) -> str:
    """token 数千分位（1234567 → 1,234,567）"""
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return "0"


def _fmt_tokens_compact(n) -> str:
    """token 数紧凑显示（1,015,808 → 1.0M；122,880 → 122.9k；512 → 512）。
    用于卡片内一行中的次要信息，避免长数字被省略号截断。"""
    try:
        v = float(int(n))
    except (TypeError, ValueError):
        return "0"
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    if v >= 1000:
        return f"{v / 1000:.1f}k"
    return str(int(v))


def _clamp01(x) -> float:
    """占比钳制到 [0, 1]（容忍上游返回 > 窗口的异常值，刻度/分段不越界）"""
    try:
        return max(0.0, min(1.0, float(x)))
    except (TypeError, ValueError):
        return 0.0


def _fmt_pct(ratio, digits: int = 1) -> str:
    """占比 → 百分数字符串（0.0773 → 7.7%）；非零但不足最小分度时显示 <0.1%，
    避免"明明用了却显示 0%"的观感错误"""
    try:
        r = float(ratio)
    except (TypeError, ValueError):
        return "0%"
    if r > 0 and r * 100 < 0.1:
        return "<0.1%"
    return f"{r * 100:.{digits}f}%"


def _usage_palette() -> dict:
    """统计浮层配色：主体纯黑/淡灰/白 + 深蓝；语义色仅用于"接近阈值"的刻线与百分比
    文字（极小面积）。在刷新/绘制时读取 → 主题切换后无需重建控件即可生效。

    返回值一律为 QColor（含透明度），可直接交给 QPainter 绘制——绘制接口不接受
    颜色字符串，混用会在 paintEvent 里抛异常（Qt 虚函数内未捕获即整进程 abort）。"""
    return {
        "track": QColor(HOVER),            # 轨道
        "hit": QColor(ACCENT),             # 缓存命中段（深蓝实色）
        "miss": _fade(TEXT_DIM, 0.45),     # 未命中段（淡灰半透明）
        "out": QColor(ACCENT_HOVER),       # 输出段（亮一档蓝，与命中/未命中段区分）
        "estimate": _fade(ACCENT, 0.55),   # 本地估算占用：同色降透明，与上游真实值区分
        "rate": QColor(ACCENT_HOVER),      # 缓存命中率条
        "tick": _fade(TEXT_DIM, 0.75),     # 压缩阈值刻线
        "warn": QColor(WARN),              # 越过压缩阈值
        "err": QColor(ERR),                # 越过硬上限
    }


class _StatBar(QWidget):
    """分段进度条（自绘）：圆角轨道 + 多段填充 + 阈值刻线标记。

    - segments: [(占比 0~1, 颜色)]，按序自左向右累积（占比以整条为 1，超出自动截断）
    - ticks:    [(阈值占比 0~1, 颜色, 是否实线)]，画 1px 竖线标出关键阈值
    只负责绘制：数据由 set_data 注入，便于复用于其他统计条（命中率/占用率等）。"""

    def __init__(self, height: int = 12, radius: int = None, parent=None):
        super().__init__(parent)
        self._segments: list = []
        self._ticks: list = []
        self._radius = RADIUS_SM if radius is None else int(radius)
        self._height = int(height)
        self.setFixedHeight(self._height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def set_data(self, segments=None, ticks=None):
        self._segments = list(segments or [])
        self._ticks = list(ticks or [])
        self.update()

    def paintEvent(self, _e):
        """自绘：异常一律吞掉并正常结束 painter —— Qt 虚函数内未捕获异常会直接
        abort 整个进程（表现为"崩溃框/无 traceback"），统计条属可视化增强，
        绝不能因绘制问题拖垮应用。"""
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            r = QRectF(self.rect())
            radius = min(self._radius, r.height() / 2)
            path = QPainterPath()
            path.addRoundedRect(r, radius, radius)
            p.fillPath(path, _usage_palette()["track"])
            # 分段填充：裁剪在圆角轨道内（末段不再额外收圆角，由裁剪保证边缘形状）
            p.save()
            p.setClipPath(path)
            x = 0.0
            for ratio, color in self._segments:
                w = r.width() * _clamp01(ratio)
                if w <= 0:
                    continue
                p.fillRect(QRectF(x, 0.0, w, r.height()), color)
                x += w
            p.restore()
            # 阈值刻线：1px 竖线（虚线=压缩阈值，实线=硬上限等强约束）
            for ratio, color, solid in self._ticks:
                xx = r.width() * _clamp01(ratio)
                pen = QPen(color)
                pen.setWidth(1)
                pen.setStyle(Qt.PenStyle.SolidLine if solid else Qt.PenStyle.DashLine)
                p.setPen(pen)
                p.drawLine(QPointF(xx, r.top()), QPointF(xx, r.bottom()))
        except Exception:
            pass
        finally:
            try:
                p.end()
            except Exception:
                pass


class _PopoverDismissFilter(QObject):
    """浮层展示期间的全局失焦守卫：点击浮层与触发按钮之外、或按 Esc → 收起浮层。

    只在浮层可见时接管（其余事件一律放行），避免影响面板与其他对话框的交互；
    Esc 仅在浮层所在窗口为活动窗口时消费，防止抢掉模态弹窗的 Esc。"""

    def __init__(self, popover, opener, parent=None, on_dismiss=None):
        super().__init__(parent)
        self._pop = popover
        self._opener = opener
        # 收起回调：调用方需要感知"浮层被外部收起"时传入自己的关闭方法，
        # 否则宿主状态位不会同步（下次点按钮会先走一遍无效的关闭分支）。
        self._on_dismiss = on_dismiss

    def _dismiss(self):
        """执行收起：优先走调用方的关闭方法（保证其状态位/守卫一并更新）"""
        if self._on_dismiss is not None:
            self._on_dismiss()
        else:
            self._pop.close_animated()

    def eventFilter(self, obj, ev):
        try:
            if not self._pop.isVisible():
                return False
            et = ev.type()
            if et == QEvent.Type.KeyPress and ev.key() == Qt.Key.Key_Escape:
                if self._pop.window().isActiveWindow():
                    self._dismiss()
                    return True
                return False
            if et == QEvent.Type.MouseButtonPress:
                w = obj if isinstance(obj, QWidget) else None
                if w is None:
                    return False
                # 浮层内弹出的模态对话框（文件浏览 / 确认框）不算"点击外部"：
                # 其层级与浮层无父子关系，不特判会一点即收起浮层
                try:
                    if QApplication.activeModalWidget() is not None:
                        return False
                except Exception:
                    pass
                if w is self._pop or self._pop.isAncestorOf(w):
                    return False
                if self._opener is not None and (w is self._opener
                                                 or self._opener.isAncestorOf(w)):
                    return False   # 触发按钮自身：由 clicked 切换，避免"关了又开"
                self._dismiss()
        except Exception:
            pass
        return False


class _TokenStatsPopover(QFrame):
    """token / 上下文统计浮层卡片（自触发按钮下方弹出，内容全部来自引擎快照）。

    布局：数据来源标记 + 上下文占用条（条内按 缓存命中 / 未命中 / 输出 分段着色，条上画压缩阈值与
    硬上限制刻度线）+ 缓存命中率条 + 明细（本次上游输入/输出、累计消耗、消息条数、模型）。
    控件不直接访问引擎：数据由 set_stats() 注入，便于单测与后续扩展统计项。"""

    PREFERRED_WIDTH = 340
    SLIDE = 8          # 弹出位移（px）：自下方 8px 上移到目标位置
    IN_MS = 160        # 弹出时长（丝滑但不等候）
    OUT_MS = 120       # 收起时长（略快于弹出，手感收得住）

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("tokenStatsPop")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._eff = None
        self._anims: list = []
        self._sig = None            # 上次渲染的数据签名（幂等：值未变不重排）
        self._closing = False
        self._vals_raw = {}         # 明细值原文：省略宽度随卡片尺寸变化，需保留原文重算
        self._keys = []             # 明细键名标签：键列宽按其需宽锁定，避免被右侧值挤压
        self._key_w = 0
        self._build()
        self.apply_theme()
        # 先按首选宽落位：避免首帧在默认宽（640）下计算省略宽度而少省/多省
        self.resize(int(self.PREFERRED_WIDTH), max(1, self.sizeHint().height()))

    # ---------- 构建 ----------
    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SPACING_MD, SPACING_MD, SPACING_MD, SPACING_MD)
        lay.setSpacing(SPACING_SM)

        head = QHBoxLayout()
        head.setSpacing(SPACING_SM)
        self._title = QLabel("上下文统计")
        self._title.setObjectName("tkTitle")
        head.addWidget(self._title)
        head.addStretch(1)
        self._src_chip = QLabel("")
        self._src_chip.setObjectName("tkChip")
        self._src_chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        head.addWidget(self._src_chip, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addLayout(head)

        # 数值区：百分比为大字主体（左），「已用 / 上限」为次级一行（左）。
        # 之所以拆两行而不是并排一行：并排时"1,048,576"这类 9 位上限与百分比会互相抢宽，
        # 被挤成省略号——正是"设了 1M 却看不到 1M 总量"的观感来源；拆行后两者都完整可见，
        # 且任何字体/卡片宽度下都不会横向溢出（每行需宽都远小于卡片内容宽）。
        self._ratio = QLabel("0%")
        self._ratio.setObjectName("tkRatio")
        self._usage = QLabel("0 / 0")
        self._usage.setObjectName("tkUsage")
        nums = QVBoxLayout()
        nums.setContentsMargins(0, 0, 0, 0)
        nums.setSpacing(1)
        nums.addWidget(self._ratio)
        nums.addWidget(self._usage)
        lay.addLayout(nums)

        self._bar = _StatBar(height=12)
        lay.addWidget(self._bar)

        # 阈值图例：每条一行（换行而非横向溢出）。两条刻度加起来的需宽会超过卡片宽度，
        # 单行渲染时后半条会被硬裁掉（表现为"文字被挤压/遮住"）。
        self._ticks_lbl = QLabel("")
        self._ticks_lbl.setObjectName("tkDim")
        self._ticks_lbl.setTextFormat(Qt.TextFormat.RichText)
        self._ticks_lbl.setWordWrap(True)
        lay.addWidget(self._ticks_lbl)

        self._cache_lbl = QLabel("缓存命中率 —")
        self._cache_lbl.setObjectName("tkDim")
        self._cache_lbl.setWordWrap(True)    # 长明细自动折行，不做横向硬裁
        lay.addWidget(self._cache_lbl)
        self._cache_bar = _StatBar(height=6, radius=RADIUS_SM)
        lay.addWidget(self._cache_bar)

        self._grid = QGridLayout()
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(SPACING_SM)
        self._grid.setVerticalSpacing(SPACING_XS)
        self._vals = {}
        self._keys = []
        for row, (key, text) in enumerate((("last_in", "本次上游输入"),
                                          ("last_out", "本次输出"),
                                          ("cum", "累计消耗"),
                                          ("msgs", "上下文消息"),
                                          ("window_src", "上限来源"),
                                          ("compaction", "上下文压缩"),
                                          ("workflow", "工作流"),
                                          ("model", "模型"))):
            k = QLabel(text)
            k.setObjectName("tkKey")
            v = QLabel("—")
            v.setObjectName("tkVal")
            v.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            # 值列横向 Ignored：值自身的需宽不参与撑宽卡片（长文本按可用宽省略即可），
            # 否则一条超长值（如"累计消耗"的明细）会把键名挤到只剩省略号。
            v.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            self._grid.addWidget(k, row, 0)
            self._grid.addWidget(v, row, 1)
            self._vals[key] = v
            self._keys.append(k)
        self._grid.setColumnStretch(0, 0)
        self._grid.setColumnStretch(1, 1)
        self._sync_key_column()
        lay.addLayout(self._grid)

        self._hint = QLabel("")
        self._hint.setObjectName("tkHint")
        self._hint.setWordWrap(True)
        lay.addWidget(self._hint)
        lay.addStretch(1)

    # ---------- 自适应排版（值省略 / 列宽 / 高度随实际尺寸重算） ----------
    def _sync_key_column(self) -> int:
        """锁定键列宽 = 各键名自身字体下的最大需宽，并返回该宽度。

        键列是"标签"，不应被右侧长值挤压；用列最小宽把它钉住，右侧值列再按剩余
        像素做省略。字体随主题/系统缩放变化时重算（apply_theme / resize 时调用）。"""
        try:
            w = max((k.sizeHint().width() for k in self._keys), default=0)
        except Exception:
            w = 0
        self._key_w = int(w)
        if w > 0:
            self._grid.setColumnMinimumWidth(0, int(w))
        return int(w)

    def _val_px(self) -> int:
        """值列可用像素宽 = 卡片内容宽 − 键列 − 列间距（≤0 表示尚未布局）"""
        try:
            inner = int(self.width()) - 2 * SPACING_MD
        except Exception:
            inner = int(self.PREFERRED_WIDTH) - 2 * SPACING_MD
        return max(0, inner - int(self._key_w) - int(self._grid.horizontalSpacing()))

    def _set_val(self, key: str, text: str, full: str = None):
        """写入明细值：记住原文（省略宽度随卡片尺寸变化需重算）+ 悬停看全文。

        之前用固定的 190/260px 做省略，卡片实际值列只有约 210px，于是文本先被
        "省略"成 260px 再被控件硬裁 → 看起来压在键名上（挤压/遮挡）。改为按实际
        可用宽省略，任何卡片宽度/字体下都不会溢出。"""
        lbl = self._vals.get(key)
        if lbl is None:
            return
        self._vals_raw[key] = str(text if text is not None else "")
        self._vals_tip(key, full)
        self._elide_val(key)

    def _vals_tip(self, key: str, full: str = None):
        lbl = self._vals.get(key)
        if lbl is None:
            return
        raw = self._vals_raw.get(key, "")
        tip = raw if full is None else str(full or "")
        lbl.setToolTip(tip if tip and tip != "—" else "")

    def _elide_val(self, key: str):
        """按当前可用宽重算某一个值的省略文本"""
        lbl = self._vals.get(key)
        raw = self._vals_raw.get(key, "")
        if lbl is None or not raw:
            return
        w = self._val_px()
        if w <= 0:
            return
        try:
            lbl.setText(lbl.fontMetrics().elidedText(
                raw, Qt.TextElideMode.ElideMiddle, w))
        except Exception:
            lbl.setText(raw)

    def _relayout_texts(self):
        """尺寸/字体变化后重算键列宽与全部值省略（幂等，可反复调用）"""
        self._sync_key_column()
        for key in list(self._vals_raw.keys()):
            self._elide_val(key)

    def resizeEvent(self, event):
        """卡片尺寸变化 → 重算省略文本（值是按"可用像素"省略的，宽高都会改它）"""
        super().resizeEvent(event)
        self._relayout_texts()

    def apply_theme(self):
        """按当前主题色板重建卡片样式（主题切换/首次显示时调用；绘制色在 paint 时读取）"""
        self.setStyleSheet(
            f"#tokenStatsPop {{ background: {PANEL}; border: 1px solid {BORDER_SOFT};"
            f" border-radius: {RADIUS_MD}px; }}"
            f"#tokenStatsPop QLabel#tkTitle {{ color: {TEXT}; font-size: {FONT_BODY}px;"
            " font-weight: 700; }"
            f"#tokenStatsPop QLabel#tkChip {{ color: {TEXT_DIM}; background: {HOVER};"
            f" border: 1px solid {BORDER_SOFT}; border-radius: {RADIUS_SM}px;"
            f" padding: 1px {SPACING_SM}px; font-size: {FONT_CAPTION}px; }}"
            f"#tokenStatsPop QLabel#tkRatio {{ color: {TEXT}; font-size: {FONT_TITLE}px;"
            " font-weight: 700; }"
            f"#tokenStatsPop QLabel#tkUsage {{ color: {TEXT_DIM};"
            f" font-size: {FONT_SMALL}px; }}"
            f"#tokenStatsPop QLabel#tkDim {{ color: {TEXT_DIM};"
            f" font-size: {FONT_SMALL}px; }}"
            f"#tokenStatsPop QLabel#tkKey {{ color: {TEXT_DIM};"
            f" font-size: {FONT_SMALL}px; }}"
            f"#tokenStatsPop QLabel#tkVal {{ color: {TEXT}; font-size: {FONT_SMALL}px;"
            " font-weight: 600; }"
            f"#tokenStatsPop QLabel#tkHint {{ color: {TEXT_DIM};"
            f" font-size: {FONT_CAPTION}px; }}")
        # 字阶随主题/映射变化：键列宽与值省略宽度需按新字体重算（否则会挤/溢出）
        self._relayout_texts()
        self.update()

    # ---------- 数据注入 ----------
    @staticmethod
    def _segments(stats: dict, pal: dict, base: int) -> list:
        """占用条分段：统计口径＝输入+输出，占比基数＝可用输入预算（与三档阈值同口径，见 set_stats 的 base）。
        上游真实数据按 缓存命中 / 未命中 / 输出 拆分；本地估算无拆分信息 → 单段降透明色（与上游真实值一眼可辨）。"""
        used = float(stats.get("used") or 0)
        base = max(1, int(base or 0))
        if stats.get("source") != "upstream":
            return [(_clamp01(used / base), pal["estimate"])]
        last = stats.get("last") or {}
        hit = min(float(last.get("cache_hit") or 0), used)
        out = min(float(last.get("completion") or 0), max(0.0, used - hit))
        miss = max(0.0, used - hit - out)
        segs = []
        if hit > 0:
            segs.append((_clamp01(hit / base), pal["hit"]))
        if miss > 0:
            segs.append((_clamp01(miss / base), pal["miss"]))
        if out > 0:
            segs.append((_clamp01(out / base), pal["out"]))
        if not segs:
            segs.append((_clamp01(used / base), pal["hit"]))
        return segs

    @staticmethod
    def _tick_marks(r_comp: float, r_ceil: float, pal: dict) -> list:
        """阈值刻线：虚线=压缩触发点，实线=发送前硬上限（占比 0 视为无效不画）"""
        ticks = []
        if r_comp > 0:
            ticks.append((r_comp, pal["tick"], False))
        if r_ceil > 0:
            ticks.append((r_ceil, pal["tick"], True))
        return ticks

    def set_stats(self, stats: dict = None):
        """渲染一份统计快照（无引擎时只有界面侧字段：数值区显示空态，工作流/模型照常显示）；
        相同数据直接跳过重排（400ms 轮询下避免空转）。"""
        pal = _usage_palette()
        s = dict(stats or {})
        thr = s.get("thresholds") or {}
        win = max(1, int(s.get("window") or 0))
        # 是否拿到引擎数据：只看窗口（无引擎/引擎不支持统计时窗口缺省）
        has_engine = int(s.get("window") or 0) > 0
        used = int(s.get("used") or 0)
        cum = s.get("cumulative") or {}
        last = s.get("last") or {}
        msgs = s.get("messages")
        workflow = str(s.get("workflow") or "")
        model = str(s.get("model") or "")
        rate = s.get("cache_rate")
        budget = int(s.get("budget") or 0)
        # 占用条基数 = 可用输入预算（与预警/压缩/硬上限三个阈值同一口径）。
        # 若用裸窗口做基数，压缩刻线会落在「0.75×预算/窗口」这种非整数位置（1M 模式下
        # 79.4%），用户看到的百分比与「到 80% 才压缩」的设定对不上，误判为提前压缩。
        # 预算缺省（无引擎数据）时回退窗口，保证空态仍可渲染。
        base = budget if budget > 0 else win
        ratio = _clamp01(used / base)
        warn_now = bool(s.get("warn"))
        comp = s.get("compaction") or {}
        # 阈值占比（硬上限可能 > 窗口时截断到 100%，刻度线始终画在条内）
        r_comp = _clamp01(int(thr.get("compress") or 0) / base)
        r_ceil = _clamp01(int(thr.get("ceiling") or 0) / base)
        # 分层阈值配色（主流 agent 应用的三档）：接近但未越压缩线=强调色，
        # 越过压缩阈值=预警色，越过硬上限=危险色；面积始终只有百分比文字与刻线。
        if has_engine and r_ceil > 0 and ratio >= r_ceil:
            status_color = pal["err"]
        elif has_engine and r_comp > 0 and ratio >= r_comp:
            status_color = pal["warn"]
        elif has_engine and warn_now:
            status_color = QColor(ACCENT_HOVER)
        else:
            status_color = None
        src_txt = {"upstream": "上游真实用量", "estimate": "本地估算"}.get(
            s.get("source") or "", "无数据") if has_engine else "无数据"
        sig = (used, win, src_txt, ratio, rate, tuple(sorted(cum.items())),
               tuple(sorted(last.items())), msgs, workflow, model, has_engine,
               budget, warn_now, int(comp.get("count") or 0),
               int(comp.get("last_saved") or 0), str(s.get("window_source") or ""))
        if sig == self._sig:
            return
        self._sig = sig

        # 工作流 / 模型：无论有无引擎都显示（原顶栏「状态」按钮的信息并入此处）
        self._set_val("workflow", workflow or "—")
        self._set_val("model", model or "—")
        # 上限来源（1M 开关 / 上游声明 / 手填 / 内置已知表 / 模型名推断）+ 可用输入预算。
        # 用短标签：值列仅约 236px，长名（"内置已知服务商表 · 可用输入预算 122.9k"）会被
        # 省略成"…"，反而看不清来源；全称放在 tooltip。
        src_short = {"1m": "1M 开关", "upstream": "上游声明",
                     "configured": "服务商手填",
                     "known": "内置已知表",
                     "inferred": "模型名推断"}
        src_desc = {"1m": "1M 开关（已开启 1M 上下文）",
                    "upstream": "上游服务商在 /models 中的声明",
                    "configured": "服务商配置中手填",
                    "known": "内置已知服务商表（离线兜底）",
                    "inferred": "按模型名推断（最后兜底）"}
        src_label = src_short.get(str(s.get("window_source") or ""), "")
        src_full = src_desc.get(str(s.get("window_source") or ""), "")
        if src_label and budget > 0:
            src_label += f" · 预算 {_fmt_tokens_compact(budget)}"
        if src_full and budget > 0:
            src_full += (f"；窗口 {_fmt_tokens(win)}，预留输出 "
                         f"{_fmt_tokens(int(s.get('max_output') or 0))}，"
                         f"可用输入预算 {_fmt_tokens(budget)}")
        self._set_val("window_src", src_label or "—", full=src_full or src_label or "—")
        # 上下文压缩：次数 + 最近释放量（无则"尚未压缩"）
        n_comp = int(comp.get("count") or 0)
        self._set_val("compaction",
                      f"{n_comp} 次 · 最近释放 {_fmt_tokens(comp.get('last_saved'))}"
                      if n_comp > 0 else "尚未压缩")

        if not has_engine:
            self._usage.setText("—")
            self._ratio.setText("—")
            self._ratio.setStyleSheet(f"color: {TEXT_DIM};")
            self._bar.set_data([], [])
            self._src_chip.setText("无数据")
            self._cache_lbl.setText("缓存命中率 —")
            self._cache_bar.set_data([], [])
            for key in ("last_in", "last_out", "cum", "msgs",
                        "window_src", "compaction"):
                self._set_val(key, "—")
            self._hint.setText("本会话尚未产生请求：发送消息后展示服务商返回的真实用量。")
            return

        self._usage.setText(f"{_fmt_tokens(used)} / {_fmt_tokens(base)}")
        self._usage.setToolTip(
            f"已用（输入+输出）{_fmt_tokens(used)} / 可用输入预算 {_fmt_tokens(base)} tokens"
            + (f"（窗口 {_fmt_tokens(win)}）" if base != win else ""))
        self._ratio.setText(_fmt_pct(ratio))
        # 显式取 .name()：不依赖 QColor 的字符串化表现，保证仍是 px 可解析的十六进制
        self._ratio.setStyleSheet(
            f"color: {(status_color or QColor(TEXT_DIM)).name()};")
        self._bar.set_data(self._segments(s, pal, base),
                           self._tick_marks(r_comp, r_ceil, pal))
        self._src_chip.setText(src_txt)
        # 缓存明细：标签行只放紧凑值（精确到千分位会超出卡片宽），精确数字进 tooltip
        if s.get("source") == "upstream":
            seg_desc = ("命中 " + _fmt_tokens_compact(last.get("cache_hit") or 0)
                        + " · 未命中 " + _fmt_tokens_compact(last.get("cache_miss") or 0))
            seg_tip = ("本次提示词缓存：命中 "
                       + _fmt_tokens(last.get("cache_hit") or 0)
                       + " tokens · 未命中 "
                       + _fmt_tokens(last.get("cache_miss") or 0) + " tokens")
        else:
            seg_desc = "上游未返回缓存明细"
            seg_tip = ""
        self._cache_lbl.setText("缓存命中率 " + (_fmt_pct(rate) if rate is not None else "—")
                                + f"（{seg_desc}）")
        self._cache_lbl.setToolTip(seg_tip)
        self._cache_bar.set_data(
            [((rate or 0.0), pal["rate"])] if rate is not None else [], [])
        tick_bits = []
        if r_comp > 0:
            tick_bits.append(f"<span style='color:{pal['tick'].name()};'>●</span> "
                             f"压缩阈值 {_fmt_pct(r_comp, 0)}（{_fmt_tokens(thr.get('compress'))}）")
        if r_ceil > 0:
            tick_bits.append(f"<span style='color:{(status_color or pal['tick']).name()};'>●</span> "
                             f"硬上限 {_fmt_tokens(thr.get('ceiling'))}")
        # 每条刻度独占一行：两条图例合计需宽超过卡片内容宽，单行会被硬裁掉后半条
        self._ticks_lbl.setText("<br>".join(tick_bits))
        self._set_val("last_in", _fmt_tokens(last.get("prompt") or 0))
        self._set_val("last_out", _fmt_tokens(last.get("completion") or 0))
        # 累计消耗：行内用紧凑单位（1.5M / 234.6k）保证一行放得下，精确值放 tooltip
        self._set_val("cum",
                      f"{_fmt_tokens_compact(cum.get('total'))}（入 "
                      f"{_fmt_tokens_compact(cum.get('prompt'))} · 出 "
                      f"{_fmt_tokens_compact(cum.get('completion'))}）",
                      full=f"{_fmt_tokens(cum.get('total'))}（输入 "
                           f"{_fmt_tokens(cum.get('prompt'))} · 输出 "
                           f"{_fmt_tokens(cum.get('completion'))}）")
        self._set_val("msgs", "—" if msgs is None else f"{int(msgs)} 条")
        self._hint.setText(
            "数据来自上游真实用量（输入+输出），供判断上下文占用。"
            if s.get("source") == "upstream" else
            "上游暂未返回用量（或上下文刚被压缩），暂以本地估算值展示。")

    def _elide(self, text: str, width: int = 190) -> str:
        """文本过长时中间省略（不换行、不撑破卡片宽度）；width 为可用像素宽度。

        ⚠️ 明细值不再走这里：固定像素宽与实际值列宽（约 236px）不符，会先被"省略"
        到 260px 再被控件硬裁，出现压在键名上的观感。明细统一用 _set_val +
        按实际可用宽重算的 _elide_val。"""
        try:
            return self.fontMetrics().elidedText(
                text, Qt.TextElideMode.ElideMiddle, int(width))
        except Exception:
            return text

    # ---------- 展示 / 收起（位移动画 + 淡入淡出） ----------
    def _ensure_effect(self):
        if self._eff is None:
            self._eff = QGraphicsOpacityEffect(self)
            self._eff.setOpacity(1.0)
            self.setGraphicsEffect(self._eff)
        return self._eff

    def _run(self, anim):
        """启动动画并登记：运行期间抑制外部几何同步，避免与动画抢位置"""
        self._anims.append(anim)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    def animation_running(self) -> bool:
        """是否仍有动画在跑。

        用 state() 判定并顺手清理失效条目：动画以 DeleteWhenStopped 启动，结束后其
        C++ 对象已被销毁（Python 包装失效、finished 回调里再比较对象会拿不到结果），
        若不按需清理，列表会永远非空 → 浮层位置同步被永久抑制。"""
        alive = []
        for a in self._anims:
            try:
                if a.state() != QAbstractAnimation.State.Stopped:
                    alive.append(a)
            except RuntimeError:
                continue   # 底层对象已销毁 → 丢弃
        self._anims = alive
        return bool(alive)

    def popup_animated(self, rect: QRect):
        """丝滑弹出：透明度 0→1（OutCubic）同时自下方 SLIDE 上移到目标位置"""
        eff = self._ensure_effect()
        eff.setOpacity(0.0)
        start = QRect(rect.x(), rect.y() + self.SLIDE, rect.width(), rect.height())
        self.setGeometry(start)
        self.show()
        self.raise_()
        a_op = QPropertyAnimation(eff, b"opacity", self)
        a_op.setDuration(self.IN_MS)
        a_op.setStartValue(0.0)
        a_op.setEndValue(1.0)
        a_op.setEasingCurve(QEasingCurve.Type.OutCubic)
        a_geo = QPropertyAnimation(self, b"geometry", self)
        a_geo.setDuration(self.IN_MS)
        a_geo.setStartValue(start)
        a_geo.setEndValue(rect)
        a_geo.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._run(a_op)
        self._run(a_geo)

    def close_animated(self):
        """收起：透明度 1→0 同时轻微下移，动画结束后隐藏（可再次弹出复用本控件）"""
        if not self.isVisible() or self._closing:
            return
        self._closing = True
        eff = self._ensure_effect()
        cur = self.geometry()
        a_op = QPropertyAnimation(eff, b"opacity", self)
        a_op.setDuration(self.OUT_MS)
        a_op.setStartValue(eff.opacity())
        a_op.setEndValue(0.0)
        a_op.setEasingCurve(QEasingCurve.Type.InCubic)
        a_geo = QPropertyAnimation(self, b"geometry", self)
        a_geo.setDuration(self.OUT_MS)
        a_geo.setStartValue(cur)
        a_geo.setEndValue(QRect(cur.x(), cur.y() + self.SLIDE // 2,
                                cur.width(), cur.height()))
        a_geo.setEasingCurve(QEasingCurve.Type.InCubic)
        a_op.finished.connect(self._after_close)
        self._run(a_op)
        self._run(a_geo)

    def _after_close(self):
        self._closing = False
        self.hide()


class _PanelDragHandle(QWidget):
    """子面板顶部拖拽把手：按住可自由拖动窗口；释放时持久化位置到 QSettings。

    拖动结束后面板置 _user_moved=True，宿主各 _sync_*_win 对该面板只抬升不再
    强制归位（保留用户摆放位置）；跨重启在 showEvent 时恢复保存位置。
    简约风格：居中三条淡灰短线（拖拽语义），无 emoji。
    """

    _H = 22   # 把手高度（px）

    def __init__(self, panel: QWidget, parent=None):
        super().__init__(parent)
        self._panel = panel
        self.setFixedHeight(self._H)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setToolTip("按住拖动面板位置（自动保存）")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        dark = QColor(BG).lightness() < 128
        self._stripe = QColor(255, 255, 255, 90) if dark else QColor(0, 0, 0, 60)
        self._drag = False
        self._off = QPoint()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # 三条水平短线：居中于把手条带
        w, h = self.width(), self._H
        cx = w // 2
        for i in range(3):
            y = (h - 3 * 4 + 3) // 2 + i * 4
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self._stripe)
            p.drawRoundedRect(cx - 10, y, 20, 2, 1, 1)
        p.end()

    def _host(self):
        """宿主 AgentPanel：浮窗/融入 dock 两种父子关系下向上遍历定位"""
        p = self._panel
        for _ in range(5):
            p = p.parent()
            if p is None:
                return None
            if callable(getattr(p, "_panel_drag_start", None)):
                return p
        return None

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = True
            self._off = e.globalPosition().toPoint() - self._panel.frameGeometry().topLeft()
            host = self._host()
            if host is not None and callable(getattr(host, "_panel_drag_start", None)):
                host._panel_drag_start(self._panel, e.globalPosition().toPoint())
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag and (e.buttons() & Qt.MouseButton.LeftButton):
            # 融入主面板（dock）状态：不直接移动窗口，由宿主实时做栏内排序/换侧预览
            if self._panel.dock_state in ("left", "right"):
                host = self._host()
                if host is not None and callable(getattr(host, "_panel_drag_move", None)):
                    host._panel_drag_move(self._panel, e.globalPosition().toPoint())
                e.accept()
                return
            self._panel.move(e.globalPosition().toPoint() - self._off)
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._drag:
            self._drag = False
            self._panel._user_moved = True
            host = self._host()
            if host is not None and callable(getattr(host, "_panel_drag_end", None)):
                host._panel_drag_end(self._panel, e.globalPosition().toPoint())
            if self._panel.dock_state == "float":
                _save_panel_pos(self._panel)
            e.accept()
            return
        super().mouseReleaseEvent(e)


class _PanelResizeSlot(QWidget):
    """单个 resize 热区（边带 6px / 角块 12px）：鼠标按住即可把宿主面板拉大/缩小。

    支持全部 8 个方位（左右上下 + 四角）：右/下边只改宽高（贴附侧不动），
    左/上边拖动同时平移（保持对侧边缘贴附）。仅影响本面板大小，与其他面板独立。
    松开自动持久化到 QSettings（见宿主 _panel_size_changed persist=True）。"""

    _EDGE = 6
    _CORNER = 14

    def __init__(self, panel: QWidget, host, mode: str):
        super().__init__(panel)
        self._panel = panel
        self._host = host
        self._mode = mode
        cursors = {
            "L": Qt.CursorShape.SizeHorCursor, "R": Qt.CursorShape.SizeHorCursor,
            "T": Qt.CursorShape.SizeVerCursor, "B": Qt.CursorShape.SizeVerCursor,
            "TL": Qt.CursorShape.SizeFDiagCursor, "BR": Qt.CursorShape.SizeFDiagCursor,
            "TR": Qt.CursorShape.SizeBDiagCursor, "BL": Qt.CursorShape.SizeBDiagCursor,
        }
        self.setCursor(cursors.get(mode, Qt.CursorShape.SizeAllCursor))
        self.setToolTip("拖动调整面板大小（松开自动保存，仅本次面板）")
        self._drag = False
        self._start_g = QPoint()
        self._start_rect = QRect()

    def repin(self):
        """按宿主面板当前几何重排全部 8 个热区"""
        p = self._panel
        w, h = p.width(), p.height()
        e, c = self._EDGE, self._CORNER
        pos = {
            "L": QRect(0, c, e, h - 2 * c),
            "R": QRect(w - e, c, e, h - 2 * c),
            "T": QRect(c, 0, w - 2 * c, e),
            "B": QRect(c, h - e, w - 2 * c, e),
            "TL": QRect(0, 0, c, c),
            "TR": QRect(w - c, 0, c, c),
            "BL": QRect(0, h - c, c, c),
            "BR": QRect(w - c, h - c, c, c),
        }
        self.setGeometry(pos.get(self._mode, QRect(0, 0, c, c)))

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = True
            self._start_g = e.globalPosition().toPoint()
            self._start_rect = QRect(self._panel.geometry())
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if not self._drag or not (e.buttons() & Qt.MouseButton.LeftButton):
            super().mouseMoveEvent(e)
            return
        p = self._panel
        r = QRect(self._start_rect)
        # globalPosition() 是 QPointF（float），而 QRect.setLeft/Right/Top/Bottom 只接受 int：
        # 不取整会抛 TypeError（"argument 1 has unexpected type 'float'"）→ 拖动即崩溃弹窗。
        dx = int(e.globalPosition().x() - self._start_g.x())
        dy = int(e.globalPosition().y() - self._start_g.y())
        mode = self._mode
        if mode in ("L", "TL", "BL"):
            r.setLeft(r.left() + dx)
        elif mode in ("R", "TR", "BR"):
            r.setRight(r.right() + dx)
        if mode in ("T", "TL", "TR"):
            r.setTop(r.top() + dy)
        elif mode in ("B", "BL", "BR"):
            r.setBottom(r.bottom() + dy)
        nw = max(_PanelResizeSlot._EDGE + 40, min(1600, r.width()))
        nh = max(120, min(1400, r.height()))
        # 左边/上边联动平移：保持右侧/底部贴附边缘不动
        nx, ny = p.x(), p.y()
        if mode in ("L", "TL", "BL"):
            nx = self._start_rect.right() - nw
        if mode in ("T", "TL", "TR"):
            ny = self._start_rect.bottom() - nh
        p.move(nx, ny)
        self._panel_resize(p, nw, nh)
        e.accept()

    def _panel_resize(self, panel, w: int, h: int):
        try:
            panel.resize(w, h)
            host = self._host
            if host is not None and callable(getattr(host, "_panel_size_changed", None)):
                host._panel_size_changed(panel, w, h, persist=False)
        except Exception:
            pass

    def mouseReleaseEvent(self, e):
        if self._drag:
            self._drag = False
            host = self._host
            if host is not None and callable(getattr(host, "_panel_size_changed", None)):
                host._panel_size_changed(self._panel, self._panel.width(),
                                         self._panel.height(), persist=True)
            e.accept()
            return
        super().mouseReleaseEvent(e)


class _PanelResizeGrip(QWidget):
    """子面板四边/四角拖拽调节大小（聚合 8 个 _PanelResizeSlot 热区）。

    鼠标附着在面板任意边/角即可改变该面板单个大小，与其他面板相互独立；
    松开自动持久化（panel_size/<objectName> = w,h）。宿主 AgentPanel 通过
    _panel_size_changed 写入。"""

    MODES = ("L", "R", "T", "B", "TL", "TR", "BL", "BR")

    def __init__(self, panel: QWidget, host=None):
        super().__init__(panel)
        self._panel = panel
        self._host = host
        self._slots = {m: _PanelResizeSlot(panel, host, m) for m in self.MODES}
        self.repin()
        for s in self._slots.values():
            try:
                s.raise_()
            except Exception:
                pass

    def repin(self):
        """重排全部热区（面板 resizeEvent / 显示后调用）"""
        for s in self._slots.values():
            try:
                s.repin()
            except Exception:
                pass


def _save_panel_pos(w: QWidget) -> None:
    """持久化面板当前位置到 QSettings（随用户拖动结束触发）"""
    try:
        key = f"panel_pos/{getattr(w, 'objectName', lambda: '')() or 'panel'}"
        pos = w.pos()
        app_identity.qsettings().setValue(
            key, f"{pos.x()},{pos.y()}")
    except Exception:
        pass


def _restore_panel_pos(w: QWidget) -> bool:
    """读取面板保存位置并恢复；返回是否应用了保存位置。
    位置落在屏幕外（分辨率变化）时吸附回屏幕内，避免面板"消失"。
    """
    try:
        key = f"panel_pos/{getattr(w, 'objectName', lambda: '')() or 'panel'}"
        raw = app_identity.qsettings().value(key, "")
        if not raw:
            return False
        x, y = (int(v) for v in str(raw).split(",")[:2])
        scr = QApplication.screenAt(QPoint(x, y)) or w.screen()
        g = scr.availableGeometry() if scr else None
        if g is not None:
            x = max(g.left(), min(x, g.right() - w.width() // 2))
            y = max(g.top(), min(y, g.bottom() - 40))
        w.move(x, y)
        return True
    except Exception:
        return False


def _reset_panel_poses(panel) -> None:
    """清除全部子面板保存位置并复位手动移动标记（回到默认停靠）"""
    try:
        q = app_identity.qsettings()
        for name in ("todos_win", "git_win", "wt_win", "code_win"):
            w = getattr(panel, name, None)
            if w is None:
                continue
            q.remove(f"panel_pos/{getattr(w, 'objectName', lambda: '')() or name}")
            setattr(w, "_user_moved", False)
    except Exception:
        pass
    # 重置面板位置/大小后按当前面板偏好（贴附/融入）重排：保持用户选择的模式
    try:
        q = app_identity.qsettings()
        q.remove("dock_order/left")
        q.remove("dock_order/right")
        q.remove("panel_width/left_group")
        q.remove("panel_width/codeWin")
    except Exception:
        pass
    try:
        q = app_identity.qsettings()
        for w in (getattr(panel, "_ext_panels", None) or {}).values():
            q.remove(f"panel_pos/{getattr(w, 'objectName', lambda: '')() or 'ext'}")
            setattr(w, "_user_moved", False)
    except Exception:
        pass
    # 重置面板大小：清除持久化尺寸并恢复各面板的默认尺寸（DEFAULT_SIZE）。
    # 任务清单固定为「当前内容所需高度」定格值（reset 后不再随内容伸缩，超出即滚动），
    # 避免清单很短时被拉长留空、或清单很长时把左栏挤走。
    try:
        q = app_identity.qsettings()
        for p in (getattr(panel, "wt_win", None), getattr(panel, "git_win", None),
                  getattr(panel, "code_win", None)):
            if p is None:
                continue
            q.remove(f"panel_size/{getattr(p, 'objectName', lambda: '')() or 'panel'}")
            p._set_panel_size(*_default_panel_size(p))
        todos = getattr(panel, "todos_win", None)
        if todos is not None:
            q.remove(f"panel_size/{getattr(todos, 'objectName', lambda: '')() or 'todosWin'}")
            fit = getattr(todos, "fit_content_height", None)
            if callable(fit):
                fit()
            else:
                todos._set_panel_size(*_default_panel_size(todos))
    except Exception:
        pass
    # 按当前面板偏好重排（attach 恢复默认贴附停靠；dock 保持融入主面板栏内）
    try:
        if callable(getattr(panel, "_apply_panel_mode", None)):
            panel._applied_panel_mode = None
            panel._apply_panel_mode()
    except Exception:
        pass
    try:
        panel._request_side_sync(0)
    except Exception:
        pass


def _load_panel_size(w: QWidget) -> tuple:
    """读取持久化的面板尺寸（panel_size/<objectName> = w,h）。
    非法/越界值返回 (0, 0)（调用方用各自默认尺寸兜底）。"""
    try:
        key = "panel_size/" + (getattr(w, "objectName", lambda: "")() or "panel")
        raw = str(app_identity.qsettings().value(key, "") or "")
        if not raw:
            return (0, 0)
        a, b = (int(v) for v in raw.replace(" ", "").split(",")[:2])
        if a >= 120 and b >= 100:
            return (a, b)
    except Exception:
        pass
    return (0, 0)


def _default_panel_size(w: QWidget) -> tuple:
    """面板默认尺寸（无持久化记录时的兜底）：各面板类以 DEFAULT_SIZE 声明自己的默认值，
    缺失时回退通用默认（280x320）。"""
    d = getattr(w, "DEFAULT_SIZE", None)
    if isinstance(d, (tuple, list)) and len(d) == 2:
        try:
            dw, dh = int(d[0]), int(d[1])
            if dw > 0 and dh > 0:
                return dw, dh
        except (TypeError, ValueError):
            pass
    return 280, 320


def _panel_mode_setting() -> str:
    """面板偏好模式（QSettings panel_pref_mode）：attach=贴附主面板 / dock=融入主面板"""
    try:
        v = str(app_identity.qsettings().value(
            "panel_pref_mode", "attach"))
    except Exception:
        v = "attach"
    return v if v in ("attach", "dock") else "attach"


def _center_dialog_on_screen(w: QWidget) -> None:
    """把模态对话框居中于所在屏幕可用区（考虑任务栏，避免弹出偏下/越界）。"""
    try:
        scr = w.screen()
        if scr is None:
            parent = w.parentWidget()
            scr = parent.screen() if parent is not None else None
        g = scr.availableGeometry() if scr is not None else None
        if g is None:
            return
        # 尺寸超出可用区时先收缩（防止小屏幕下窗口溢出任务栏/屏幕底）
        gw, gh = g.width(), g.height()
        if w.width() > gw or w.height() > gh:
            w.resize(min(w.width(), gw - 20), min(w.height(), gh - 20))
        x = g.left() + max(0, (gw - w.width()) // 2)
        y = g.top() + max(0, (gh - w.height()) // 2)
        w.move(x, y)
    except Exception:
        pass


class _RoundedFloatWindow(QWidget):
    """无边框悬浮面板基类：统一切圆角，消除四角方形残留。
    覆盖 todos/git/worktree/code 四个停靠面板——它们是无边框纯色窗口，
    仅设背景色会留下方形边角，这里配合 agent_ui_ux.apply_rounded_window
    用 setMask 蒙版剪圆角，并在窗口尺寸变化/显示时重新套用。
    最大化/全屏时自动清蒙版（铺满屏幕无需圆角）。失败静默，绝不影响功能。"""

    _WINDOW_RADIUS = 12   # 停靠面板统一圆角半径（默认 UI 亦生效；子类可覆盖）

    # ---- 自由拖动 + 位置持久化 ----
    _drag_handle = None    # 子类构造时调用 _install_drag_handle() 创建把手

    def _install_drag_handle(self):
        """创建顶部拖拽把手：放当前布局最上方（子面板多为标题行在上）"""
        if getattr(self, "_drag_handle", None) is not None:
            return
        self._drag_handle = _PanelDragHandle(self._drag_parent())
        if self.layout() is not None:
            self.layout().insertWidget(0, self._drag_handle)

    # ---- 四边/四角拖拽调节大小 + 独立持久化 + 融入主面板（dock） ----
    _grip = None          # 8 热区 resize 手柄（_install_resize_edges 创建）
    dock_state = "float"  # float=独立悬浮贴附 / left=融入主面板左侧 / right=融入右侧
    _manual_h = 0         # 用户手动拖过的高度（0=未手动调过高，跟随宿主/内容）
    DEFAULT_SIZE = (280, 320)   # 默认尺寸（子类按需覆盖，见 _default_panel_size）
    MARGINS = (10, 10, 10, 10)  # 常规内容边距
    MARGINS_DOCK = (10, 0, 10, 0)  # 融入栏内：上下贴边（与相邻面板之间不留底色缝隙）

    def _install_resize_edges(self, host=None):
        """创建四边/四角 resize 热区（仅调节本面板大小，独立持久化）"""
        if getattr(self, "_grip", None) is not None:
            return
        self._grip = _PanelResizeGrip(self, host if host is not None else self.parent())
        self._grip.repin()
        QTimer.singleShot(0, self._grip.repin)

    def _set_panel_size(self, w: int, h: int = None):
        """应用面板宽高（拖拽/持久化恢复/融入布局），并触发子类内容适配"""
        if h is None:
            h = self.height()
        w, h = int(w), int(h)
        if w > 0 and h > 0 and (w, h) != (self.width(), self.height()):
            self.resize(w, h)
        if h != int(getattr(self, "_manual_h", 0) or 0):
            self._manual_h = int(h)
        try:
            self._on_size_changed(w, h)
        except Exception:
            pass

    def apply_saved_panel_size(self):
        """启动/重建/浮出时恢复面板尺寸：优先持久化记录，无记录回退类默认尺寸
        （DEFAULT_SIZE）—— 融入主面板再切回贴附时，尺寸必须回到「原有大小」，
        否则会停在 dock 栏里被布局分配的高度上，需手动拖拽才恢复。"""
        w, h = _load_panel_size(self)
        dw, dh = _default_panel_size(self)
        self._set_panel_size(w or dw, h or dh)

    def restore_pre_dock_size(self, w: int, h: int):
        """浮出（切回贴附）后恢复「融入前」的尺寸；子类可覆写
        （如任务清单只沿用宽度，高度由内容与默认上限决定）。"""
        self._set_panel_size(w, h)

    def _on_size_changed(self, w: int, h: int):
        """子类覆盖：尺寸变化后的内容重排（todos 高度重算 / git delegate 重建等）"""
        pass

    # ---- 融入主面板 dock 状态 ----
    def _dock_is_immersed(self) -> bool:
        return self.dock_state in ("left", "right")

    def _set_dock_ui(self, docked: bool):
        """融入主面板模式：隐藏顶部拖拽把手（三条横线标识）与四边/四角 resize 热区
        （dock 栏内位置固定、大小随栏，禁用拖动调整大小），让面板显示更多内容；
        贴附模式恢复把手与热区。"""
        try:
            h = getattr(self, "_drag_handle", None)
            if h is not None:
                h.setVisible(not docked)
        except Exception:
            pass
        try:
            g = getattr(self, "_grip", None)
            if g is not None:
                for s in g._slots.values():
                    s.setVisible(not docked)
        except Exception:
            pass

    def _drag_parent(self):
        """把手拖动目标 = 本窗口自身（子类可覆盖为内层容器）"""
        return self

    def _apply_window_round(self):
        # 窗口尚未有有效尺寸（未显示/尺寸为 0）时 setMask 无效，延迟到有画面再套；
        # 最大化/全屏时 apply_rounded_window 内部会自动清蒙版（铺满屏幕无需圆角）。
        try:
            if self._dock_is_immersed():
                # 融入主面板时本面板是子控件：SetWindowRgn 会落到宿主窗口/无效 HWND 上，
                # 既起不到圆角作用，又会在浮出后残留成一条透明带 → 融入期间不套窗口区域。
                return
            if not self.isVisible() or self.rect().isEmpty():
                return
            from zhuzhu_Copilot.core import agent_ui_ux
            agent_ui_ux.apply_rounded_window(self, self._WINDOW_RADIUS)
            # 液态玻璃包激活时补套 Acrylic 毛玻璃：子面板默认透明，需手动启用毛玻璃效果
            if agent_ui_ux.is_custom_package_active():
                agent_ui_ux.apply_acrylic(self)
        except Exception:
            pass

    def showEvent(self, e):
        super().showEvent(e)
        # 首次显示时 rect 可能仍为 0，延迟两档确保窗口有实际尺寸后套圆角，
        # 避免初始方角残留；此后由 resizeEvent 持续保持。
        self._apply_window_round()
        try:
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(30, self._apply_window_round)
            QTimer.singleShot(180, self._apply_window_round)
        except Exception:
            pass
        try:
            if self._grip is not None:
                self._grip.repin()
        except Exception:
            pass
        # 融入主面板（dock）状态：位置由宿主 dock 布局管理，不恢复浮窗位置
        if self._dock_is_immersed():
            return
        # 自由拖动位置持久化：未手动移动过且存在保存位置 → 恢复到上次摆放处，
        # 并标记 _user_moved 使宿主各 _sync_*_win 不再强制归位（保持用户布局）
        if not getattr(self, "_user_moved", False) and _restore_panel_pos(self):
            self._user_moved = True

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._apply_window_round()
        try:
            if self._grip is not None:
                self._grip.repin()
        except Exception:
            pass


class CodePreviewWindow(_RoundedFloatWindow):
    """多格式智能预览器（停靠 AI 面板右侧）。
    - Web：QWebEngineView（默认 Bing 首页，含后退/前进/刷新/地址栏）；
    - Markdown/Office(docx/pptx/xlsx)：渲染为主题配色的 HTML；
    - 图片(jpg/png/gif/webp/svg…)：缩放预览；
    - 文本/代码：语法高亮。
    未安装 QtWebEngine 时 Web 自动降级为静态页提示。"""

    WIDTH = int(280 * 1.5 * 1.5 * 0.7)   # 441
    DEFAULT_SIZE = (WIDTH, 600)          # 默认尺寸（无持久化记录时的兜底）

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self.setObjectName("codeWin")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"QWidget#codeWin {{ background: {BG}; }}")
        self.resize(self.WIDTH, 600)   # 宽度可调（右缘拖拽），初始 441
        self._web_tabs = None     # QTabWidget：多标签浏览器容器（WebEngine 可用时）
        self._web_err = ""        # Web 引擎创建失败的真实原因（供降级提示展示）
        self._current_url = "https://www.bing.com/"
        self._manual_mode = False  # 用户是否手动指定了预览模式（False=auto 按扩展名自动路由）
        self._media_full = False       # 视频是否处于预览面板全屏（融入面板而非独立窗口）
        self._media_ctrl_timer = None  # 全屏控制条自动隐藏定时器
        self._media_saved = None       # 全屏前保存的窗口/装饰状态（退出时恢复）
        self._diff_seq = 0             # AI 变更 diff 展示序号（防旧恢复定时器打断新 diff）
        self._diff_path = ""           # 当前 diff 展示的文件路径（同文件自动预览不得覆盖）
        self._diff_until = 0.0         # diff 展示截止时间（time.time()，期间保护 diff 视图）
        self._diff_scroll_to = None    # diff 待定位的首个变更行（面板延迟显示时由 showEvent 补定位）
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(6)
        # 标题栏 + 路径
        self.title = QLabel("预览 · Bing 搜索")
        self.title.setStyleSheet(f"color: {TEXT}; font-size: 13px; font-weight: 700;")
        self.path_lbl = QLabel("默认展示 Bing 搜索引擎；可在此预览网页 / Markdown / 图片 / Office / 代码")
        self.path_lbl.setStyleSheet(f"color: {TEXT_DIM}; font-size: 11px;")
        self.path_lbl.setWordWrap(True)
        for w, tt in ((self.title, Qt.TextInteractionFlag.TextSelectableByMouse),
                      (self.path_lbl, Qt.TextInteractionFlag.TextSelectableByMouse)):
            w.setTextInteractionFlags(tt)
        lay.addWidget(self.title)
        lay.addWidget(self.path_lbl)
        # 顶部拖拽把手：自由拖动 + 位置持久化（置于整个面板最上方）
        self._install_drag_handle()
        # 模式切换行（容器 widget：视频全屏时整体隐藏，仅保留视频+控制条）
        self.mode_row = QWidget()
        mode_row = QHBoxLayout(self.mode_row)
        mode_row.setContentsMargins(0, 0, 0, 0)
        mode_row.setSpacing(6)
        self.mode = QComboBox()
        for label, key in (("自动", "auto"), ("Web", "web"), ("Markdown", "md"),
                           ("图片", "img"), ("表格 xlsx", "xlsx"), ("文档 docx", "docx"),
                           ("幻灯片 pptx", "pptx"), ("文本/代码", "text"),
                           ("视频/音乐", "media")):
            self.mode.addItem(label, key)
        self.mode.setStyleSheet(_QCOMBO)   # 主题自适应下拉（浅色下避免默认黑底黑字）
        self.mode.currentIndexChanged.connect(self._on_mode_changed)
        mode_row.addWidget(self.mode, 1)
        self.open_btn = QPushButton("打开…")
        self.open_btn.setFixedHeight(26)
        self.open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.open_btn.clicked.connect(self._browse_file)
        mode_row.addWidget(self.open_btn)
        lay.addWidget(self.mode_row)
        # Web 地址栏（仅 Web 模式显示）
        self.web_bar = QWidget()
        web_row = QHBoxLayout(self.web_bar)
        web_row.setContentsMargins(0, 0, 0, 0)
        web_row.setSpacing(4)
        self.web_url = QLineEdit()
        self.web_url.setPlaceholderText("输入网址搜索，回车访问")
        # 显式主题配色：修复地址栏字体在任意模式下不可见（默认渲染受 app 级深色
        # palette 影响，浅色主题下文字与底对比不足）；placeholder 用淡灰
        self.web_url.setStyleSheet(
            f"QLineEdit {{ background: {PANEL}; color: {TEXT};"
            f" border: 1px solid {BORDER}; border-radius: 6px;"
            f" padding: 4px 8px; font-size: 12px; }}"
            f"QLineEdit:focus {{ border: 1px solid {ACCENT_HOVER}; }}"
            f"QLineEdit::placeholder {{ color: {TEXT_DIM}; }}")
        self.web_url.returnPressed.connect(self._go_url)
        web_row.addWidget(self.web_url, 1)
        self.go_btn = QPushButton("GO")
        self.go_btn.setFixedSize(36, 24)
        self.go_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.go_btn.clicked.connect(self._go_url)
        web_row.addWidget(self.go_btn)
        self.web_bar.setVisible(False)
        lay.addWidget(self.web_bar)
        # 堆叠页
        self.stack = QStackedWidget()
        self._build_web_page()
        self._build_html_page()
        self._build_text_page()
        self._build_img_page()
        self._build_empty_page()
        self._build_office_page()
        lay.addWidget(self.stack, 1)
        # 「打开…/GO」按钮：显式主题化样式（主色底 + 白字，悬停提亮）。
        # 不可依赖 app 级 palette/原生风格渲染：主窗口移除后 app palette 不再随
        # 主题更新，浅色主题下按钮被系统渲染为"白字 + 浅色按钮底"→ 文字不可见
        # （原深色分支的黑字补丁同样是对原生渲染的猜测）。显式样式与全局主按钮
        # 语义一致，深浅主题下均清晰可辨。
        _btn_qss = (f"QPushButton {{ background: {ACCENT}; color: #FFFFFF;"
                    "border: none; border-radius: 6px; padding: 2px 10px;"
                    "font-size: 12px; font-weight: 600; }"
                    f"QPushButton:hover {{ background: {ACCENT_HOVER}; }}")
        self.open_btn.setStyleSheet(_btn_qss)
        self.go_btn.setStyleSheet(_btn_qss)
        # 默认展示 Bing
        if agent_ui_ux.web_engine_available():
            try:
                from zhuzhu_Copilot.core import agent_browser_web
                agent_browser_web.wire_downloads(self)
            except Exception:
                pass
        QTimer.singleShot(0, self.show_bing)
        # 四边/四角拖拽调节大小（独立持久化）+ 持久化尺寸恢复
        self._install_resize_edges(host=self.parent())
        self.apply_saved_panel_size()

    def _on_size_changed(self, w: int, h: int):
        """尺寸变化：预留（内部 QStackedWidget/浏览器自适应）；标记已手动调高停用跟随"""
        pass

    # ---- 页面构建 ----
    def _btn(self, text, slot, tip=""):
        b = QToolButton()
        b.setText(text)
        if tip:
            b.setToolTip(tip)
        b.setFixedSize(24, 24)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setStyleSheet(f"QToolButton {{ background: transparent; border: none;"
                        f"color: {TEXT}; font-size: 13px; }}"
                        f"QToolButton:hover {{ background: {HOVER}; }}")
        b.clicked.connect(slot)
        return b

    def _build_web_page(self):
        w = QWidget()
        vl = QVBoxLayout(w)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(0)
        nav = QHBoxLayout()
        nav.setSpacing(4)
        nav.addWidget(self._btn("‹", self._web_back, "后退"))
        nav.addWidget(self._btn("›", self._web_forward, "前进"))
        nav.addWidget(self._btn("⟳", self._web_reload, "刷新"))
        nav.addWidget(self._btn("⌂", self.show_bing, "Bing 首页"))
        nav.addWidget(self._btn("＋", self._web_new_tab, "新建标签页"))
        nav.addStretch(1)
        self.web_info = QLabel("")
        self.web_info.setStyleSheet(f"color: {TEXT_DIM}; font-size: 10px;")
        nav.addWidget(self.web_info)
        vl.addLayout(nav)
        if agent_ui_ux.web_engine_available():
            try:
                # 多标签浏览器容器：每个标签都是「感知所有者」的 WebView（见
                # _make_web_tab）。target=_blank / window.open 等新窗口统一走
                # createWindow → 被所有者接管为新的标签页，绝不弹系统浏览器。
                self._web_tabs = QTabWidget()
                self._web_tabs.setTabsClosable(True)
                self._web_tabs.setMovable(True)
                self._web_tabs.setDocumentMode(True)
                self._web_tabs.setStyleSheet(
                    f"QTabWidget {{ background: {PANEL}; }}"
                    f"QTabWidget::pane {{ background: {PANEL}; border: 1px solid {BORDER}; }}"
                    f"QTabBar {{ background: {PANEL}; }}"
                    f"QTabBar::tab {{ background: transparent; color: {TEXT_DIM};"
                    f" font-size: 11px; padding: 3px 8px; margin: 1px;"
                    f" border: 1px solid {BORDER}; border-radius: 4px; }}"
                    f"QTabBar::tab:selected {{ background: {PANEL}; color: {TEXT}; }}"
                    f"QTabBar::tab:hover {{ background: {HOVER}; }}")
                self._web_tabs.tabCloseRequested.connect(self._web_close_tab)
                self._web_tabs.currentChanged.connect(self._on_web_tab_changed)
                # 首个标签页
                self._web_tabs.addTab(self._make_web_tab(), "Bing")
                vl.addWidget(self._web_tabs, 1)
            except Exception as e:
                self._web_tabs = None
                self._web_err = f"{type(e).__name__}: {e}"
        if self._active_web() is None:
            hint = QTextBrowser()
            hint.setOpenExternalLinks(True)
            # 区分「未安装」与「创建失败」：旧构建在打包环境下 find_spec 探测失败
            # 会误报未安装；这里给出真实原因，避免误导排查方向
            reason = self._web_err or "未检测到 PyQt6-WebEngine（QtWebEngine）"
            hint.setHtml(_preview_wrap_html(
                f"<p>Web 引擎不可用：{reason}</p>"
                "<p>安装 <b>PyQt6-WebEngine</b> 后即可在应用内直接浏览网页。</p>"
                "<p>Markdown / 图片 / Office / 代码预览功能不受影响。</p>",
                ""))
            vl.addWidget(hint, 1)
        self.stack.addWidget(w)

    def _make_web_tab(self):
        """创建一个托管标签页：连接地址变化回调并标记所有者（供新窗口接管）"""
        from PyQt6.QtWebEngineWidgets import QWebEngineView as _QWEV

        class _WebTabView(_QWEV):
            def createWindow(self, _type):
                view = type(self)(self)
                view.urlChanged.connect(
                    lambda url: self._steal_popup(view, url))
                return view

            def _steal_popup(self, view, url):
                s = url.toString()
                if not s or s.startswith("about:"):
                    view.deleteLater()
                    return
                if getattr(view, "_adopted", False):
                    return
                view._adopted = True
                owner = getattr(self, "_owner", None)
                if owner is not None:
                    owner._adopt_web_tab(view, s)
                else:
                    view.setParent(None)

        view = _WebTabView(self._web_tabs)
        view._owner = self
        view._owner_tabs = self._web_tabs   # 供内置浏览器桥列标签/切标签
        view.urlChanged.connect(lambda u: self._on_web_url(u))
        return view

    def _active_web(self):
        """当前活动标签的 Web 视图；WebEngine 不可用或无标签时返回 None"""
        tw = self._web_tabs
        if tw is None:
            return None
        i = tw.currentIndex()
        if i < 0:
            return None
        return tw.widget(i)

    def _tab_title(self, url: str) -> str:
        """由 URL 派生简洁标签名"""
        try:
            from urllib.parse import urlparse
            host = urlparse(url).hostname or ""
            return host[:14] or "标签"
        except Exception:
            return "标签"

    def _adopt_web_tab(self, view, url):
        """把一个新窗口请求接管为浏览器的新标签页"""
        tw = self._web_tabs
        if tw is None:
            view.deleteLater()
            return
        view._owner = self
        view._owner_tabs = tw   # 供内置浏览器桥列标签/切标签
        try:
            view.urlChanged.connect(lambda u: self._on_web_url(u))
        except Exception:
            pass
        idx = tw.addTab(view, self._tab_title(url))
        tw.setCurrentIndex(idx)
        self.path_lbl.setText(url)
        self.web_url.setText(url)

    def _web_new_tab(self):
        """新建标签页并打开 Bing 首页"""
        tw = self._web_tabs
        if tw is None:
            return
        idx = tw.addTab(self._make_web_tab(), "新标签")
        tw.setCurrentIndex(idx)
        self.show_url("https://www.bing.com/")

    def _web_close_tab(self, idx):
        """关闭标签页；始终保留至少一个"""
        tw = self._web_tabs
        if tw is None:
            return
        if tw.count() <= 1:
            return
        v = tw.widget(idx)
        tw.removeTab(idx)
        if v is not None:
            v.deleteLater()
        self._on_web_tab_changed()

    def _on_web_tab_changed(self, *_):
        """切换标签页时，地址栏/状态同步到当前标签"""
        v = self._active_web()
        if v is not None:
            self._on_web_url(v.url())
        else:
            self.web_url.clear()
            self.web_info.clear()

    # ---- 内置浏览器下载 ----
    def _download_dir(self) -> str:
        """下载保存目录：优先工作目录下 downloads；否则用户本地应用数据目录。"""
        try:
            from zhuzhu_Copilot.core import agent_tools
            wd = agent_tools.get_workdir()
            if wd and os.path.isdir(wd):
                return os.path.join(wd, "downloads")
        except Exception:
            pass
        return os.path.join(str(app_identity.data_root()), "downloads")

    def _status(self, text: str):
        """在 Web 状态栏提示（内部浏览器下载/完成/取消信息）"""
        try:
            s = str(text)
            self.web_info.setText(s[:46] + ("…" if len(s) > 46 else ""))
        except Exception:
            pass

    def _handle_download(self, item):
        """内置浏览器文件下载：先弹「保存位置」确认框，确定后才开始下载；取消则中止。
        避免自动落到工作目录/downloads 造成用户无感知落盘。"""
        try:
            item.pause()   # 先暂停，等用户确定保存位置后再正式接收
            suggested = item.downloadFileName() or item.suggestedFileName() or ""
            d = self._download_dir()
            try:
                os.makedirs(d, exist_ok=True)
            except Exception:
                d = os.path.expanduser("~")
            default_path = os.path.join(d, suggested or "download.bin")
            save_path, _ = QFileDialog.getSaveFileName(
                self, "保存下载文件", default_path)
            if not save_path:
                item.cancel()
                self._status("已取消下载")
                return
            target_dir, target_name = os.path.split(save_path)
            if not target_name:
                target_name = suggested or os.path.basename(default_path)
                target_dir = os.path.dirname(save_path) or target_dir
            item.setDownloadDirectory(target_dir or os.path.expanduser("~"))
            item.setDownloadFileName(target_name)
            item.resume()
            name = target_name or "文件"
            self._status(f"开始下载：{name} …")
            try:
                item.stateChanged.connect(
                    lambda st, it=item: self._on_download_state(st, it))
            except Exception:
                pass
        except Exception as e:
            self._status(f"下载失败：{e}")
            try:
                item.cancel()
            except Exception:
                pass

    def _on_download_state(self, state, item=None):
        """下载状态变化：完成时弹窗（打开保存位置/运行），取消时提示。"""
        from PyQt6.QtWebEngineCore import QWebEngineDownloadRequest
        try:
            if state == QWebEngineDownloadRequest.DownloadState.DownloadCompleted:
                path = ""
                if item is not None:
                    try:
                        path = item.downloadFilePath() or ""
                    except Exception:
                        path = ""
                self._status("下载完成")
                if path:
                    self._show_download_popup(path)
            elif state == QWebEngineDownloadRequest.DownloadState.DownloadCancelled:
                self._status("下载已取消")
        except Exception:
            pass

    def _show_download_popup(self, path):
        """下载完成后弹出小窗：点击「运行」打开文件 /「打开位置」打开保存目录。"""
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices
        from PyQt6.QtWidgets import (
            QDialog,
            QHBoxLayout,
            QLabel,
            QPushButton,
            QVBoxLayout,
        )
        try:
            prev = getattr(self, "_dl_popup", None)
            if prev is not None:
                try:
                    prev.close()
                    prev.deleteLater()
                except Exception:
                    pass
            self._dl_popup = None
            dlg = QDialog(self)
            dlg.setWindowFlags(Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
                               | Qt.WindowType.Tool)
            dlg.setObjectName("dlPopup")
            dlg.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            try:
                dlg.setStyleSheet(f"QDialog#dlPopup {{ background: {PANEL};"
                                  f"border: 1px solid {BORDER}; border-radius: 8px; }}")
            except Exception:
                pass
            lay = QVBoxLayout(dlg)
            lay.setContentsMargins(12, 10, 12, 12)
            lay.setSpacing(8)
            t = QLabel(f"下载完成：{os.path.basename(path) or path}")
            t.setWordWrap(True)
            t.setStyleSheet(f"color:{TEXT};font-size:12px;")
            lay.addWidget(t)
            row = QHBoxLayout()
            row.setSpacing(6)

            def _run():
                try:
                    QDesktopServices.openUrl(QUrl.fromLocalFile(path))
                except Exception:
                    pass
                dlg.close()

            def _open_dir():
                try:
                    os.startfile(os.path.dirname(path) or path)
                except Exception:
                    pass
                dlg.close()

            for text, fn in (("运行", _run), ("打开位置", _open_dir)):
                b = QPushButton(text)
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                b.setStyleSheet(f"QPushButton {{ background: {PANEL}; color: {TEXT};"
                                f"border: 1px solid {BORDER}; border-radius: 5px;"
                                f"padding: 4px 12px; }}"
                                f"QPushButton:hover {{ background: {HOVER}; }}")
                b.clicked.connect(fn)
                row.addWidget(b)
            lay.addLayout(row)
            dlg.adjustSize()
            g = self.frameGeometry()
            dlg.move(g.right() - dlg.width() - 8, g.y() + 8)
            dlg.show()
            dlg.raise_()
            dlg.activateWindow()
            self._dl_popup = dlg
            QTimer.singleShot(8000, dlg.close)   # 8s 后自动收起
        except Exception:
            pass
    def _build_office_page(self):
        """Office 保真预览页骨架：顶部放映控制条 + 内容占位。

        注意：**不在构建时创建 QWebEngineView**（WebEngine 会拉起渲染子进程，
        无谓占用事件循环与内存）；改为首次真正预览 Office 文件时由
        _ensure_office_web() 懒创建，未用到的会话零开销。
        """
        self.office_web = None
        self._office_web_err = ""
        page = QWidget()
        col = QVBoxLayout(page)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)
        self._office_col = col
        # 放映控制条（仅 pptx 预览时显示）
        self.slide_bar = QWidget()
        bar = QHBoxLayout(self.slide_bar)
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(4)
        self.slide_info = QLabel("")
        self.slide_info.setStyleSheet(f"color: {TEXT_DIM}; font-size: 11px;")
        bar.addWidget(self.slide_info)
        bar.addStretch(1)
        self._slide_btns = []
        for text, js, tip in (("上一页", "__deckPrev()", "切到上一页"),
                             ("下一步", "__deckStep()", "播放当前页的下一个动画元素"),
                             ("从头放映", "__deckPlay(1)", "进入放映态：带动画元素隐藏，从第 1 页逐条播放"),
                             ("全部显示", "__deckAll()", "退出放映态，本页元素一次性完整显示"),
                             ("下一页", "__deckNext()", "切到下一页")):
            b = QPushButton(text)
            b.setFixedHeight(24)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setToolTip(tip)
            b.setStyleSheet(
                f"QPushButton {{ background: {PANEL}; color: {TEXT};"
                f"border: 1px solid {BORDER}; border-radius: 5px;"
                f"padding: 2px 9px; font-size: 11px; }}"
                f"QPushButton:hover {{ background: {HOVER}; }}")
            b.clicked.connect(lambda _=False, code=js: self._deck_call(code))
            bar.addWidget(b)
            self._slide_btns.append(b)
        # 全屏放映/查看：铺满屏幕呈现（PPT 进入放映态逐条动画，其它放大适宽）
        self.full_btn = QPushButton("全屏放映")
        self.full_btn.setFixedHeight(24)
        self.full_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.full_btn.setToolTip("全屏呈现：← / → 翻页、空格下一步、Esc 退出、F11 切换全屏")
    def _set_slide_bar(self, is_deck: bool):
        """Office 预览顶部控制条可见性：
        - PPT：显示全部放映按钮 + 「全屏放映」
        - Word/Excel/PDF：只显示「全屏查看」（方便放大呈现，不做动画放映）
        """
        if getattr(self, "_office_html", ""):
            self.slide_bar.setVisible(True)
            for b in self._slide_btns:
                b.setVisible(is_deck)
            self.full_btn.setText("全屏放映" if is_deck else "全屏查看")
        else:
            self.slide_bar.setVisible(False)

    def _open_fullscreen(self):
        """全屏放映/查看当前 Office 预览（复用已生成的 HTML，不重新渲染）"""
        html = getattr(self, "_office_html", "")
        if not html:
            return
        try:
            from zhuzhu_Copilot.ui.slideshow import make_slideshow
            old = getattr(self, "_office_full_win", None)
            if old is not None:
                try:
                    old.close()
                except Exception:
                    pass
            self._office_full_win = make_slideshow(
                self, html, getattr(self, "_office_ext", ""),
                getattr(self, "_office_path", ""))
        except Exception as e:
            office_preview_diag("全屏放映失败: %s: %s" % (type(e).__name__, e))
            try:
                self.slide_info.setText("全屏不可用：%s" % type(e).__name__)
            except Exception:
                pass

    def _deck_call(self, js: str):
        self.full_btn.setStyleSheet(
            f"QPushButton {{ background: {ACCENT}; color: #FFFFFF; border: none;"
            f"border-radius: 5px; padding: 2px 11px; font-size: 11px; font-weight: 600; }}"
            f"QPushButton:hover {{ background: {ACCENT_HOVER}; }}")
        self.full_btn.clicked.connect(self._open_fullscreen)
        bar.addWidget(self.full_btn)
        self._office_full_win = None       # 当前全屏放映窗口（防多开）
        self.slide_bar.setVisible(False)
        col.addWidget(self.slide_bar)
        self._office_ph = QLabel("打开 Word / PPT / Excel / PDF 文件后在此保真预览")
        self._office_ph.setStyleSheet(f"color: {TEXT_DIM}; font-size: 12px;")
        self._office_ph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.addWidget(self._office_ph, 1)
        # 加载提示：渲染/载入期间显示明确进度（避免大文件时"一片空白"无反馈）
        self._office_loading = QLabel("")
        self._office_loading.setStyleSheet(
            f"color: {TEXT}; font-size: 12px; padding: 3px 8px;"
            f"background: {PANEL}; border: 1px solid {BORDER}; border-radius: 5px;")
        self._office_loading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._office_loading.setVisible(False)
        col.addWidget(self._office_loading)
        self._office_stack_index = self.stack.count()
        self.stack.addWidget(page)

    def _ensure_office_web(self) -> bool:
        """懒创建保真预览用的 WebEngine 视图（失败时记录原因并返回 False 以走降级）。

        每一步都独立兜底：视图创建成功但**布局插入/占位符清理**失败时，不应把
        整个保真路径否决掉（那样会静默降级为纯文本，用户只看到"没有样式"）。
        """
        if self.office_web is not None:
            return True
        try:
            from PyQt6.QtWebEngineWidgets import QWebEngineView
            view = QWebEngineView()
            view.setStyleSheet(f"QWebEngineView {{ background: {PANEL}; border: none; }}")
        except Exception as e:
            self._office_web_err = f"{type(e).__name__}: {e}"
            return False
        # 插入布局（失败不影响可用性：退化为把视图直接挂到页面上）
        try:
            col = getattr(self, "_office_col", None)
            if col is not None:
                col.insertWidget(1, view, 1)
            else:
                page = self.stack.widget(self._office_stack_index)
                if page is not None and page.layout() is not None:
                    page.layout().addWidget(view, 1)
        except Exception as e:
            self._office_web_err = f"布局插入失败: {type(e).__name__}: {e}"
            try:
                view.setParent(self.stack.widget(self._office_stack_index))
                view.show()
            except Exception:
                pass
        # 移除占位提示（失败也无碍）
        try:
            if getattr(self, "_office_ph", None) is not None:
                self._office_ph.setParent(None)
                self._office_ph = None
        except Exception:
            pass
        self.office_web = view
        return True

    def _deck_call(self, js: str):
        """执行放映脚本并把返回结果（页号/步号）回显到控制条"""
        view = getattr(self, "office_web", None)
        if view is None:
            return
        def _done(res):
            try:
                slide = res.get("slide") if isinstance(res, dict) else None
                step = res.get("step") if isinstance(res, dict) else None
                total = res.get("total") if isinstance(res, dict) else None
                playing = res.get("playing") if isinstance(res, dict) else None
                txt = ""
                if slide is not None and total is not None:
                    txt = f"第 {slide}/{total} 页"
                if playing:
                    txt += f" · 放映中（动画 {step or 0}/{total or 0}）"
                elif slide is not None:
                    txt += " · 完整样式"
                if txt:
                    self.slide_info.setText(txt)
            except Exception:
                pass
        try:
            view.page().runJavaScript(js, _done)
        except Exception:
            pass


    def _build_html_page(self):
        tb = QTextBrowser()
        tb.setOpenExternalLinks(True)
        tb.setStyleSheet(f"QTextBrowser {{ background: {PANEL}; color: {TEXT};"
                         f"border: 1px solid {BORDER}; border-radius: 6px; }}")
        self.html_view = tb
        self.stack.addWidget(tb)

    def _build_text_page(self):
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.text.setFont(QFont("Consolas", 10))
        self.text.setStyleSheet(
            f"QPlainTextEdit {{ background: {PANEL}; color: {TEXT};"
            f"border: 1px solid {BORDER}; border-radius: 6px; padding: 6px; }}")
        self._hl = None
        self.stack.addWidget(self.text)

    def _build_img_page(self):
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setStyleSheet(f"QScrollArea {{ background: {PANEL}; border: none; }}"
                         f"QScrollArea > QWidget > QWidget {{ background: {PANEL}; }}")
        # 可拖拽/缩放图片控件：左键拖拽平移、滚轮缩放、双击复位
        self.img_label = _ZoomableImageView()
        self.img_label.setStyleSheet(f"background: {PANEL};")
        sc.setWidget(self.img_label)
        self.stack.addWidget(sc)

    def _build_empty_page(self):
        lb = QLabel("请打开或由 AI 自动分发文件到此预览")
        lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lb.setStyleSheet(f"color: {TEXT_DIM}; font-size: 12px;")
        self.stack.addWidget(lb)

    # ---- 媒体（视频/音乐）----
    def _ensure_media_page(self) -> bool:
        """惰性构建媒体页（启动不创建 QMediaPlayer，首次播放媒体才初始化；
        构造失败静默返回 False，媒体文件走文本/外部兜底）。"""
        if getattr(self, "_media_built", False):
            return True
        try:
            self._build_media_page()
            # 记录媒体页在堆叠中的实际索引（empty 页已在媒体页前插入，
            # 硬编码索引会错位 → 双击视频停在空白页看似无法播放）
            self._media_stack_index = self.stack.count() - 1
            self._media_built = True
            return True
        except Exception:
            return False

    def _build_media_page(self):
        """视频 / 音乐预览页：QMediaPlayer + QVideoWidget + 控制条。
        视频支持全屏沉浸（双击进入，Esc/再次双击退出，无操作自动隐藏控制条）。"""
        from PyQt6.QtMultimedia import QMediaPlayer
        from PyQt6.QtMultimediaWidgets import QVideoWidget
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        # 视频画面（暗底铺满）与音乐占位（黑底 + 文件名），二选一显示
        self._media_stage = QStackedWidget()
        self.video_view = QVideoWidget()
        self.video_view.setStyleSheet("background: #000;")
        self.video_view.setMouseTracking(True)   # 全屏时移动鼠标唤出底部控制条
        self.media_holder = QLabel("音乐文件预览区")
        self.media_holder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.media_holder.setWordWrap(True)
        self.media_holder.setStyleSheet(
            f"background: #000; color: {TEXT_DIM}; font-size: 13px;")
        self._media_stage.addWidget(self.video_view)      # index 0
        self._media_stage.addWidget(self.media_holder)    # index 1
        v.addWidget(self._media_stage, 1)

        # 控制条：播放/暂停 · 进度 · 时间 · 音量 · 全屏
        bar = QWidget()
        self.media_ctrl_bar = bar   # 全屏时底部控制条（鼠标唤出/自动隐藏）
        bar.setStyleSheet(f"background: {PANEL}; border-radius: 6px;")
        h = QHBoxLayout(bar)
        h.setContentsMargins(8, 4, 8, 4)
        h.setSpacing(6)
        self.media_play = QPushButton()
        self.media_play.setIcon(_line_icon("play", 16, TEXT))
        self.media_play.setStyleSheet(_BTN_ICON)
        self.media_play.setFixedSize(28, 28)
        self.media_play.setToolTip("播放 / 暂停")
        self.media_play.setAutoDefault(False)
        self.media_play.clicked.connect(self._media_play_toggle)
        h.addWidget(self.media_play)

        self.media_progress = QSlider(Qt.Orientation.Horizontal)
        self.media_progress.setRange(0, 0)
        self.media_progress.setStyleSheet(
            f"QSlider::groove:horizontal {{ height: 4px; background: {BORDER};"
            "border-radius: 2px; }"
            f"QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}"
            f"QSlider::handle:horizontal {{ width: 12px; height: 12px; margin: -4px 0;"
            f"background: {ACCENT}; border-radius: 6px; }}")
        self.media_progress.sliderMoved.connect(self._media_seek)
        h.addWidget(self.media_progress, 1)

        self.media_time = QLabel("00:00 / 00:00")
        self.media_time.setStyleSheet(f"color: {TEXT_DIM}; font-size: 11px;")
        self.media_time.setFixedWidth(78)
        h.addWidget(self.media_time)

        self.media_vol = QSlider(Qt.Orientation.Horizontal)
        self.media_vol.setRange(0, 100)
        self.media_vol.setValue(80)
        self.media_vol.setFixedWidth(70)
        self.media_vol.setStyleSheet(self.media_progress.styleSheet())
        self.media_vol.sliderMoved.connect(self._media_volume)
        self.media_vol.setToolTip("音量")
        h.addWidget(self.media_vol)

        self.media_full = QPushButton()
        self.media_full.setIcon(_line_icon("full", 16, TEXT_DIM))
        self.media_full.setStyleSheet(_BTN_ICON)
        self.media_full.setFixedSize(28, 28)
        self.media_full.setToolTip("全屏沉浸（Esc 退出）")
        self.media_full.setAutoDefault(False)
        self.media_full.clicked.connect(self._media_fullscreen)
        h.addWidget(self.media_full)
        v.addWidget(bar)

        # 播放器：视频 + 音频统一
        from PyQt6.QtMultimedia import QAudioOutput
        self.media_player = QMediaPlayer(self)
        self.media_player.setVideoOutput(self.video_view)
        # 显式创建并持有 QAudioOutput：仅依赖默认 audioOutput() 时，
        # 部分 Windows 后端下音量/输出不生效，导致视频无声
        self.media_audio = QAudioOutput()
        self.media_audio.setVolume(min(1.0, self.media_vol.value() / 100.0))
        self.media_player.setAudioOutput(self.media_audio)
        self.media_player.positionChanged.connect(self._media_pos_changed)
        self.media_player.durationChanged.connect(self._media_dur_changed)
        self.media_player.playbackStateChanged.connect(self._media_state_changed)
        self.media_player.mediaStatusChanged.connect(self._media_status_changed)
        self.video_view.installEventFilter(self)
        self.stack.addWidget(w)

    # ---- 媒体公开方法 ----
    def show_media(self, path: str, ext: str = ""):
        """播放视频 / 音乐文件；视频可全屏沉浸"""
        if not self._ensure_media_page():
            # 媒体引擎不可用：降级为文本查看（非二进制）或提示
            try:
                self.title.setText(f"预览 · {os.path.basename(path)}")
                if os.path.isfile(path) and not _is_binary(path):
                    with open(path, "r", encoding="utf-8", errors="replace") as f:
                        self.show_text(path, ext)
                else:
                    self._switch_mode("text")
                    self.text.setPlainText(f"媒体引擎不可用，无法播放：{os.path.basename(path)}")
            except Exception:
                pass
            return
        name = os.path.basename(path)
        self._switch_mode("media")
        self.title.setText(f"预览 · {name}")
        self.path_lbl.setText(path)
        # 停掉设置页 pygame 播放器，避免双音源
        try:
            from zhuzhu_Copilot.core import music_player as _mp
            if _mp.get_player().is_playing():
                _mp.get_player().stop()
        except Exception:
            pass
        is_video = (ext or os.path.splitext(path)[1].lstrip(".").lower()) in _VIDEO_EXTS
        if is_video:
            self._media_stage.setCurrentIndex(0)   # 视频画面
            self.media_holder.setText("")
        else:
            self._media_stage.setCurrentIndex(1)   # 音乐占位
            self.media_holder.setText(f"♪  {name}\n\n播放中…（支持进度/音量调节）")
        from PyQt6.QtCore import QUrl
        self.media_player.stop()
        self._media_dur = 0          # 切换文件：清空旧时长缓存，等 durationChanged 更新
        self._media_pos = 0
        self._media_pos_last = 0.0
        self.media_player.setSource(QUrl.fromLocalFile(path))
        self.media_progress.setValue(0)
        self.media_player.play()

    def _media_play_toggle(self, *_):
        from PyQt6.QtMultimedia import QMediaPlayer
        if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.media_player.pause()
        else:
            self.media_player.play()

    def _media_pos_changed(self, pos):
        """播放进度回调节流：高帧率后端（Qt ffmpeg）下 positionChanged 可达每秒
        数十次，若每次都 setValue + 重建时间文本 + 调 duration()，长视频播放期间
        会持续占用 UI 线程（进度条重绘/字符串格式化均不便宜）。此处节流到 ~200ms
        刷新一次，进度条仍平滑且 UI 开销骤降。"""
        self._media_pos = pos
        now = time.monotonic()
        last = getattr(self, "_media_pos_last", 0.0)
        if now - last < 0.2:
            return
        self._media_pos_last = now
        if not self.media_progress.isSliderDown():
            self.media_progress.setValue(pos)
        dur = getattr(self, "_media_dur", 0)
        self.media_time.setText(f"{_fmt_sec(pos // 1000)} / {_fmt_sec(max(0, dur) // 1000)}")

    def _media_dur_changed(self, dur):
        self._media_dur = max(0, int(dur))   # 缓存时长，pos 回调不再反复调 duration()
        self.media_progress.setRange(0, max(1, self._media_dur))

    def _media_state_changed(self, state):
        from PyQt6.QtMultimedia import QMediaPlayer
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.media_play.setIcon(_line_icon("pause" if playing else "play", 16, TEXT))

    def _media_status_changed(self, status):
        from PyQt6.QtMultimedia import QMediaPlayer
        # 播放结束保持最后一帧（不循环）；出错提示
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            try:
                self.media_player.pause()
            except Exception:
                pass
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            self.media_time.setText("无法解码")
            try:
                self.media_holder.setText(
                    f"♪  {os.path.basename(str(self.path_lbl.text()))}\n\n"
                    "无法解码此文件：编码不受支持或文件损坏。\n"
                    "请尝试 H.264/AAC 编码的 MP4，或改用 WAV/MP3 音频。")
            except Exception:
                pass

    def shutdown_media(self):
        """关闭窗口/重建面板前优雅释放播放器，避免 ffmpeg 后台线程析构时序告警。
        （QObject::disconnect: wildcard call ... QFFmpeg::* 多由播放中销毁引起）"""
        # 视频全屏中先退出，避免窗口重建时残留全屏状态/控制条定时器
        if getattr(self, "_media_full", False):
            try:
                self._media_exit_fullscreen()
            except Exception:
                pass
        p = getattr(self, "media_player", None)
        if p is None:
            return
        try:
            p.stop()
        except Exception:
            pass
        try:
            p.setVideoOutput(None)
        except Exception:
            pass
        try:
            p.setSource("")
        except Exception:
            pass

    def is_media_active(self) -> bool:
        """预览面板是否正在播放媒体（视频/音乐，含暂停与加载/缓冲/停滞）：
        供宿主判断 AI 操作文件时是否跳过跳转预览，避免打断正在播放的媒体"""
        try:
            p = getattr(self, "media_player", None)
            if p is None:
                return False
            from PyQt6.QtMultimedia import QMediaPlayer
            if p.playbackState() in (
                    QMediaPlayer.PlaybackState.PlayingState,
                    QMediaPlayer.PlaybackState.PausedState):
                return True
            # 加载/缓冲/停滞也是进行中的媒体会话（此时 playbackState 为
            # StoppedState），同样视为激活，避免 AI 跳转打断
            return p.mediaStatus() in (
                QMediaPlayer.MediaStatus.LoadingMedia,
                QMediaPlayer.MediaStatus.LoadedMedia,
                QMediaPlayer.MediaStatus.BufferingMedia,
                QMediaPlayer.MediaStatus.BufferedMedia,
                QMediaPlayer.MediaStatus.StalledMedia)
        except Exception:
            return False

    def _media_seek(self, pos):
        self.media_player.setPosition(pos)
        # 拖动结束后强制下一次 positionChanged 立即刷新 UI（跳过节流窗口）
        self._media_pos_last = 0.0

    def _media_volume(self, v):
        ao = getattr(self, "media_audio", None)
        if ao is not None:
            try:
                ao.setVolume(v / 100.0)
            except Exception:
                pass

    def _media_fullscreen(self, *_):
        """进入 / 退出视频全屏（预览面板自身全屏，视频融入面板而非独立窗口）。
        全屏时底部控制条移动鼠标唤出、无操作自动隐藏。"""
        if getattr(self, "_media_full", False):
            self._media_exit_fullscreen()
            return
        # 当前播放音频时不进全屏
        if self._media_stage.currentIndex() != 0:
            return
        self._enter_media_fullscreen()

    def _media_host(self):
        """宿主 AgentPanel（含 todos/git/worktree 等子面板）"""
        h = self.parentWidget()
        while h is not None and not hasattr(h, "code_win"):
            h = h.parentWidget()
        return h

    def _enter_media_fullscreen(self):
        """预览面板全屏：隐藏非视频装饰与宿主/其它子面板，视频铺满整个屏幕"""
        if self._media_full:
            return
        self._media_full = True
        # 记录并隐藏装饰（标题/路径/把手/模式行/Web 地址栏），让视频最大化
        chrome = (self.title, self.path_lbl, getattr(self, "_drag_handle", None),
                  getattr(self, "mode_row", None), getattr(self, "web_bar", None))
        self._media_saved = {
            # 用 isHidden()（显式隐藏状态）而非 isVisible()（依赖父链，窗口未显示时误判）
            "chrome": [(w, not w.isHidden()) for w in chrome if w is not None],
            "min": (self.minimumWidth(), self.minimumHeight()),
            "max": (self.maximumWidth(), self.maximumHeight()),
        }
        for w, _ in self._media_saved["chrome"]:
            try:
                w.hide()
            except Exception:
                pass
        # 记录并隐藏宿主主面板与其它子面板（todos/git/worktree）：这些是独立
        # 顶层窗口，会悬浮在全屏视频之上 → 隐藏后才是真正的全屏只显示视频
        host = self._media_host()
        if host is not None:
            self._media_saved["host"] = host
            vis = []
            for w in (host, getattr(host, "todos_win", None),
                      getattr(host, "git_win", None),
                      getattr(host, "wt_win", None)):
                if w is not None and w is not self and not w.isHidden():
                    vis.append(w)
            self._media_saved["host_vis"] = vis
            for w in vis:
                try:
                    w.hide()
                except Exception:
                    pass
        # 记录并清空根布局边距，让视频铺满整个屏幕
        _lay = self.layout()
        if _lay is not None:
            self._media_saved["margins"] = _lay.contentsMargins()
            try:
                _lay.setContentsMargins(0, 0, 0, 0)
            except Exception:
                pass
        # 解除固定尺寸（无边框 Tool 窗口固定 441 宽会阻止全屏）
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)
        # 真正全屏：setWindowState 对无边框 Tool 窗口比 showFullScreen 更可靠
        self.setWindowState(self.windowState() | Qt.WindowState.WindowFullScreen)
        if not self.isVisible():
            self.show()
        # 显式清除圆角蒙版 + 关闭 DWM 圆角：全屏必须完整覆盖屏幕。
        # 依赖 resizeEvent 里的 isFullScreen() 判断存在时序滞后，
        # 残留蒙版/DWM 圆角（Win11）都会裁剪边缘导致覆盖不全
        try:
            import ctypes
            hwnd = int(self.winId())
            if hwnd != 0:
                ctypes.windll.user32.SetWindowRgn(hwnd, 0, True)
                try:
                    corner = ctypes.c_int(1)   # WCP_DONOTROUND
                    ctypes.windll.dwmapi.DwmSetWindowAttribute(
                        hwnd, 33, ctypes.byref(corner), ctypes.sizeof(corner))
                except Exception:
                    pass
        except Exception:
            pass
        # 圆角蒙版在全屏时由 apply_rounded_window 自动清除（铺满屏幕无需圆角）
        self.setMouseTracking(True)
        # QApplication 级监听鼠标移动：Qt 的 MouseMove 事件发往光标下最深层子控件
        # （父窗口收不到子事件；QVideoWidget 原生渲染还会吞掉 video_view 事件），
        # 只有 app 级过滤能稳定捕获 → 控制条隐藏后移动鼠标即可唤出
        from PyQt6.QtWidgets import QApplication as _NavApp
        _NavApp.instance().installEventFilter(self)
        self._show_media_ctrl(True)
        # 控制条自动隐藏定时器：无操作 3 秒隐藏
        from PyQt6.QtCore import QTimer as _FullTimer
        self._media_ctrl_timer = _FullTimer(self)
        self._media_ctrl_timer.setSingleShot(True)
        self._media_ctrl_timer.setInterval(3000)
        self._media_ctrl_timer.timeout.connect(
            lambda: self._show_media_ctrl(False))
        self._media_ctrl_timer.start()

    def _show_media_ctrl(self, show: bool):
        """全屏控制条显示/隐藏（同时切换光标）"""
        bar = getattr(self, "media_ctrl_bar", None)
        if bar is not None:
            try:
                bar.setVisible(show)
            except Exception:
                pass
        try:
            self.setCursor(Qt.CursorShape.ArrowCursor if show
                           else Qt.CursorShape.BlankCursor)
        except Exception:
            pass

    def eventFilter(self, obj, ev):
        # QApplication 级：全屏时任何控件的鼠标移动都唤出底部控制条并重启隐藏计时
        # （MouseMove 事件发往光标下最深层子控件，只有 app 级过滤能稳定捕获）。
        # 性能：鼠标连续移动会高频触发本过滤（每帧数十次），仅当控制条当前隐藏或
        # 隐藏计时已到期时才真正刷新，避免重复 setVisible/start 无谓开销。
        if self._media_full and ev.type() == QEvent.Type.MouseMove:
            bar = getattr(self, "media_ctrl_bar", None)
            shown = bar is not None and bar.isVisible()
            timer_active = (getattr(self, "_media_ctrl_timer", None) is not None
                            and self._media_ctrl_timer.isActive())
            if not shown:
                self._show_media_ctrl(True)
            if not timer_active and getattr(self, "_media_ctrl_timer", None) is not None:
                self._media_ctrl_timer.start()
        vv = getattr(self, "video_view", None)
        if vv is not None and obj is vv:
            if ev.type() == QEvent.Type.MouseButtonDblClick \
                    and ev.button() == Qt.MouseButton.LeftButton:
                if self._media_full:
                    self._media_exit_fullscreen()
                else:
                    self._enter_media_fullscreen()
                return True
            if ev.type() == QEvent.Type.MouseButtonPress \
                    and ev.button() == Qt.MouseButton.LeftButton:
                # 单击：播放/暂停
                from PyQt6.QtMultimedia import QMediaPlayer
                if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                    self.media_player.pause()
                else:
                    self.media_player.play()
                return True
            if ev.type() == QEvent.Type.KeyPress and ev.key() == Qt.Key.Key_Escape:
                if self._media_full:
                    self._media_exit_fullscreen()
                return True
        return super().eventFilter(obj, ev)

    def _media_exit_fullscreen(self):
        if not self._media_full:
            return
        self._media_full = False
        if self._media_ctrl_timer is not None:
            try:
                self._media_ctrl_timer.stop()
            except Exception:
                pass
            self._media_ctrl_timer = None
        # 恢复宿主主面板与其它子面板（全屏时被隐藏）
        saved = self._media_saved or {}
        for w in saved.get("host_vis", []):
            try:
                w.show()
            except Exception:
                pass
        try:
            self.showNormal()
        except Exception:
            pass
        # 兜底清除全屏窗口状态（个别平台 showNormal 可能未完全复位）
        try:
            self.setWindowState(self.windowState() & ~Qt.WindowState.WindowFullScreen)
        except Exception:
            pass
        # 恢复装饰可见性
        saved = self._media_saved or {}
        for w, vis in saved.get("chrome", []):
            try:
                w.setVisible(vis)
            except Exception:
                pass
        # 恢复根布局边距
        _lay = self.layout()
        if _lay is not None and saved.get("margins") is not None:
            try:
                _lay.setContentsMargins(saved["margins"])
            except Exception:
                pass
        # 恢复固定尺寸（预览面板固定宽度）
        mn = saved.get("min") or (0, 0)
        mx = saved.get("max") or (16777215, 16777215)
        try:
            self.setMinimumSize(*mn)
            self.setMaximumSize(*mx)
        except Exception:
            pass
        self._media_saved = None
        # 退出全屏后恢复底部播放菜单显示（全屏 3 秒沉浸时被隐藏，必须重新加载）
        self._show_media_ctrl(True)
        try:
            self.unsetCursor()
        except Exception:
            pass
        self.setMouseTracking(False)
        try:
            from PyQt6.QtWidgets import QApplication as _NavApp
            _NavApp.instance().removeEventFilter(self)
        except Exception:
            pass
        # 恢复圆角蒙版 + 请求宿主重新停靠
        self._apply_window_round()
        from PyQt6.QtCore import QTimer as _SyncTimer
        _SyncTimer.singleShot(120, self._host_sync)

    def _host_sync(self):
        """退出全屏后请求宿主重新停靠预览面板"""
        try:
            host = self.parentWidget()
            while host is not None and not hasattr(host, "_sync_code_win"):
                host = host.parentWidget()
            if host is not None and hasattr(host, "_sync_code_win"):
                host._sync_code_win()
        except Exception:
            pass

    # ---- 公开方法 ----
    def show_bing(self):
        """默认展示 Bing 搜索引擎"""
        self.title.setText("预览 · Bing 搜索")
        self.path_lbl.setText("在地址栏输入关键词或网址，回车即用 Bing 搜索")
        self._switch_mode("web")
        self.show_url("https://www.bing.com/")

    def show_url(self, url: str):
        from PyQt6.QtCore import QUrl
        self.path_lbl.setText(url)
        self.web_url.setText(url)
        view = self._active_web()
        if view is not None and url:
            try:
                view.setUrl(QUrl(url))
                return
            except Exception:
                pass
        # 无 WebEngine：仅提示，不再转交系统浏览器打开（访问页面一律留在应用内）
        self.web_info.setText("Web 引擎不可用" + (f"：{self._web_err}" if self._web_err else ""))

    def show_markdown(self, md_text: str):
        """Markdown 文本 → 主题 HTML 预览"""
        self.title.setText("预览 · Markdown")
        self._switch_mode("md")
        html = agent_ui_ux.render_markdown_html(md_text or "")
        self.html_view.setHtml(_preview_wrap_html(html, self.path_lbl.text()))

    def show_html(self, html: str):
        """原始 HTML 预览"""
        self._switch_mode("md")
        self.html_view.setHtml(html or "")

    def show_image(self, path: str):
        self.title.setText("预览 · 图片（左键拖拽移动 · 滚轮缩放 · 双击复位）")
        self._switch_mode("img")
        pix = QPixmap(path)
        if pix.isNull():
            self.img_label.set_pixmap_src(QPixmap())
            return
        self.img_label.set_pixmap_src(pix)

    def _office_begin_loading(self, text: str):
        """显示预览加载提示（渲染/载入阶段），并强制立即重绘以真正可见"""
        try:
            self._office_loading.setText("◌ " + text)
            self._office_loading.setVisible(True)
            if getattr(self, "_office_ph", None) is not None:
                self._office_ph.setVisible(False)
            from PyQt6.QtCore import QCoreApplication
            QCoreApplication.processEvents()      # 立即出画面（否则同步渲染期间看不到）
        except Exception:
            pass

    def _office_end_loading(self):
        try:
            self._office_loading.setVisible(False)
        except Exception:
            pass

    def _office_await_load(self, view, path: str, ext: str, size: int, render_ms: int):
        """等 WebEngine 载入完成再撤掉加载提示；超时兜底并留痕（避免提示卡住/静默）"""
        import time as _t
        state = {"t0": _t.time(), "done": False}

        def _finish(ok: bool = True):
            if state["done"]:
                return
            state["done"] = True
            ms = int((_t.time() - state["t0"]) * 1000)
            self._office_end_loading()
            office_preview_diag(f"[{ext}] {path} → WebEngine loadFinished={ok} "
                                f"载入 {ms}ms（渲染 {render_ms}ms，{size/1024:.0f}KB）")

        try:
            view.loadFinished.disconnect()
        except Exception:
            pass
        try:
            view.loadFinished.connect(lambda ok: _finish(bool(ok)))
        except Exception:
            pass
        try:
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(15000, lambda: _finish(False))   # 超时兜底
        except Exception:
            pass

    def show_office(self, path: str, ext: str):
        """Office 预览：优先保真渲染（样式/图片/动画），不可用时回退简化文本渲染。

        保真渲染由 office.preview.render_office_html 产出**自包含 HTML**（图片内联
        data URI、含放映脚本），故走 WebEngine 页；Word/Excel/PPT 均按真实结构还原。
        PPT 额外显示放映控制条（上一页/下一步/全部显示/下一页）。
        """
        self.title.setText(f"预览 · {ext.upper()}")
        self.path_lbl.setText(path)
        html = None
        reason = ""
        # 立即给出加载反馈：渲染 + 载入大文件期间不让面板看起来"空白无响应"
        self._office_begin_loading(f"正在渲染 {ext.upper()} …")
        try:
            import time as _t
            _t0 = _t.time()
            from zhuzhu_Copilot.office import render_office_html
            html = render_office_html(path, ext, _office_preview_theme())
            _render_ms = int((_t.time() - _t0) * 1000)
            if not html:
                reason = "该类型/文件无法生成保真 HTML"
        except Exception as e:
            html = None
            _render_ms = -1
            reason = f"保真渲染异常: {type(e).__name__}: {e}"
        if not self._ensure_office_web():
            reason = reason or "WebEngine 不可用"
            why = getattr(self, "_office_web_err", "") or "未知原因"
            self._office_end_loading()
            office_preview_diag(f"[{ext}] {path} → WebEngine 不可用，回退纯文本。原因: {why}")
        view = self.office_web
        if html and view is not None and "<!DOCTYPE html>" in html:
            try:
                self._switch_mode("office")
                self._office_begin_loading(
                    f"正在载入 {len(html)/1024:.0f} KB 预览…（渲染 {_render_ms} ms）")
                # 优先写临时文件 + setUrl：setHtml 有约 2MB 上限（含内联图片的
                # PPT 很容易超），超限会白屏；本地文件 URL 无此限制。
                url = _office_preview_url(html)
                if url is not None:
                    from PyQt6.QtCore import QUrl
                    view.setUrl(QUrl.fromLocalFile(url))
                else:
                    view.setHtml(html)
                self._office_await_load(view, path, ext, len(html), _render_ms)
                is_deck = ext in ("pptx", "pptm") and "__deckStep" in html
                # 记录当前 HTML，供「全屏放映/查看」复用（同一份渲染结果，不重复生成）
                self._office_html = html
                self._office_ext = ext
                self._office_path = path
                self._set_slide_bar(is_deck)
                if is_deck:
                    self.slide_info.setText("完整样式预览（点「从头放映」逐条播放动画）")
                office_preview_diag(f"[{ext}] {path} → 保真渲染 ok（{len(html)} 字符，"
                                    f"渲染 {_render_ms}ms，{'本地文件URL' if url else 'setHtml'}）")
                return
            except Exception as e:
                reason = f"WebEngine 加载失败: {type(e).__name__}: {e}"
        # 回退：QTextBrowser 简化渲染（保留文字与基础表格；无绝对定位/动画）
        # 绝不静默：把原因写日志并在标题栏标注，便于定位（历史上静默回退导致
        # 用户只能看到"没有样式"却无从排查）。
        office_preview_diag(f"[{ext}] {path} → 回退简化渲染。原因: {reason or '未进入保真分支'}")
        self._office_end_loading()
        self._office_html = ""          # 降级内容无法全屏呈现（无 data-fit/screen 机制）
        self._office_ext = ext
        self._office_path = path
        self._set_slide_bar(False)
        self._switch_mode("md")
        inner = _office_preview_html(path, ext)
        if inner is None:
            self.html_view.setHtml(_preview_wrap_html("<p>暂不支持该 Office 类型</p>", path))
            return
        hint = (f"<p style='color:{TEXT_DIM};font-size:11px;margin:2px 0 8px;'>"
                f"简化渲染（未启用保真预览）：{_esc(reason or '未进入保真分支')}"
                f"<br>诊断日志：%TEMP%\\zhuzhu_copilot_preview\\diag.log</p>")
        self.html_view.setHtml(_preview_wrap_html(hint + inner, path))

    def show_file(self, path: str):
        """打开并预览任意文件：按扩展名自动路由到 Web/Markdown/图片/Office/文本"""
        # AI 变更 diff 展示期间，同文件被引擎自动预览（preview 事件）重刷时
        # 不得覆盖红绿差异视图（3s 后由 _restore_text_view 自动恢复正常）
        if self._diff_protected(str(path)):
            return
        name = os.path.basename(path)
        ext = os.path.splitext(path)[1].lstrip(".").lower()
        self.path_lbl.setText(path)
        # 手动指定过模式则按所选类型渲染；否则（auto）按扩展名自动路由
        key = self.mode.currentData() if self._manual_mode else "auto"
        if ext in _IMAGE_EXTS and (key in ("auto", "img")):
            self.show_image(path)
            return
        if ext in _OFFICE_EXTS:
            if key in ("auto", "xlsx", "docx", "pptx"):
                self.show_office(path, ext)
                return
        # 媒体：视频 / 音乐（auto 或手动 media 模式）
        if ext in _MEDIA_EXTS and key in ("auto", "media"):
            self.show_media(path, ext)
            return
        # 本地 HTML：双击直接用内置 WebView（Web 引擎）渲染，保留脚本与样式
        if ext in ("html", "htm") and key in ("auto", "web"):
            if self._active_web() is not None:
                self.title.setText("预览 · 本地 HTML 网页")
                self._switch_mode("web")
                try:
                    from pathlib import Path as _Path
                    self.show_url(_Path(os.path.abspath(path)).as_uri())
                except OSError as e:
                    self.show_text(path, ext)
                return
            # 无 Web 引擎：退回纯文本展示源码
            self.show_text(path, ext)
            return
        if ext == "md" and key in ("auto", "md"):
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    self.show_markdown(f.read())
                return
            except OSError as e:
                self.title.setText("预览 · Markdown")
                self._switch_mode("md")
                self.html_view.setHtml(_preview_wrap_html(f"<p>读取失败: {e}</p>", path))
                return
        # 文本/代码（含手动 text、未知/二进制）
        self.show_text(path, ext)

    def show_text(self, path: str, ext: str = ""):
        self.title.setText(f"预览 · {os.path.basename(path)}")
        self._switch_mode("text")
        if _is_binary(path):
            self.text.setPlainText("二进制文件：请用「图片」模式或对应 Office 阅读器打开")
            self._hl = None
            return
        READ_LIMIT = 1_000_000
        truncated = False
        try:
            size = os.path.getsize(path)
            truncated = size > READ_LIMIT
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(READ_LIMIT + 1)
            if truncated:
                content = (content[:READ_LIMIT]
                           + "\n\n…（文件过大，仅显示前 1MB）")
        except OSError as e:
            self.text.setPlainText(f"读取失败: {e}")
            self._hl = None
            return
        self.text.setPlainText(content)
        self._hl = None if truncated else _CodeHighlighter(self.text.document(), ext)
        self.text.verticalScrollBar().setValue(0)

    def clear(self):
        """清空为默认 Bing"""
        self.show_bing()

    # ---- 内部 ----
    def _switch_mode(self, key: str):
        """内部渲染指定页面（不改动用户模式下拉；auto 保持自动路由）"""
        self._set_page(key)

    def _on_mode_changed(self, *_):
        """用户手动切换模式下拉：记下手动标记并按所选模式渲染"""
        self._manual_mode = True
        self._set_page(self.mode.currentData())

    def _set_page(self, key: str):
        page = key
        if key in ("xlsx", "docx", "pptx"):
            page = "md"
        mapping = {"auto": "md", "web": "web", "md": "md", "img": "img",
                   "text": "text", "media": "media", "office": "office"}
        target = mapping.get(page)
        if target == "office":
            # Office 保真预览页（WebEngine 懒创建）；页未建时回退 HTML 文本页
            idx = getattr(self, "_office_stack_index", None)
            if idx is not None:
                self.stack.setCurrentIndex(idx)
                self.web_bar.setVisible(False)
                return
            target = "md"
        if target == "web":
            self.stack.setCurrentIndex(0)
            self.web_bar.setVisible(True)
        elif target == "img":
            self.stack.setCurrentIndex(3)
            self.web_bar.setVisible(False)
        elif target == "media":
            if self._ensure_media_page():
                self.stack.setCurrentIndex(getattr(self, "_media_stack_index", 4))
            else:
                return
            self.web_bar.setVisible(False)
        elif target == "text":
            self.stack.setCurrentIndex(2)
            self.web_bar.setVisible(False)
        else:
            self.stack.setCurrentIndex(1)
            self.web_bar.setVisible(False)
        # Web 模式且从未加载时默认 Bing
        view = self._active_web()
        if target == "web" and view is not None and not view.url().toString():
            self.show_url(self._current_url or "https://www.bing.com/")

    def _go_url(self):
        text = (self.web_url.text() or "").strip()
        if not text:
            return
        url = text if re.match(r"^https?://", text, re.I) else f"https://www.bing.com/search?q={_urlencode(text)}"
        self.show_url(url)

    def _on_web_url(self, url):
        s = url.toString()
        self._current_url = s
        self.web_url.setText(s)
        self.web_info.setText(s[:46] + ("…" if len(s) > 46 else ""))

    def _web_back(self):
        view = self._active_web()
        if view is not None and view.history().canGoBack():
            view.back()

    def _web_forward(self):
        view = self._active_web()
        if view is not None and view.history().canGoForward():
            view.forward()

    def _web_reload(self):
        view = self._active_web()
        if view is not None:
            view.reload()

    def _browse_file(self):
        from PyQt6.QtWidgets import QFileDialog as _FD
        path, _ = _FD.getOpenFileName(self, "选择文件预览")
        if path:
            self.show_file(path)

    # ---- AI 文件变更差异高亮 ----
    def show_diff(self, path: str, old_text: str, new_text: str):
        """AI 写入/编辑文件后的差异视图：新增行绿色、删除行红色，行首 +/- 符号
        加粗着色，标题显示新增/删除行数（绿 +N / 红 -M），自动滚动到首个变更行；
        3 秒后恢复正常显示磁盘当前内容。

        防串扰：AI 连续多次写文件时，每次 show_diff 令 _diff_seq 自增，
        早先的恢复定时器捕获旧序号，到时不满足则放弃恢复（新 diff 不被旧
        定时器打断回普通视图）。展示期间同文件被引擎自动预览（preview 事件
        走 show_file）时不覆盖差异视图，保证红绿高亮完整展示后恢复正常。
        """
        import logging
        self._diff_seq = getattr(self, "_diff_seq", 0) + 1
        seq = self._diff_seq
        try:
            from PyQt6.QtGui import QTextBlockFormat
            self._switch_mode("text")
            self._hl = None
            self.path_lbl.setText(str(path))
            lines, first = _build_diff_lines(old_text, new_text)
            n_add = sum(1 for sign, _t in lines if sign == "+")
            n_del = sum(1 for sign, _t in lines if sign == "-")
            # 标题：红绿区分的新增/删除行数标识（QLabel 自动识别富文本）
            base = f"AI 变更 · {os.path.basename(str(path))}"
            if n_add or n_del:
                badge = (f"<span style='color:{OK}; font-weight:700;'>+{n_add}</span>"
                         f" <span style='color:{ERR}; font-weight:700;'>-{n_del}</span>")
                self.title.setText(f"{base}　{badge}")
            else:
                self.title.setText(base)
            self.text.setPlainText("\n".join(t for _, t in lines))
            # 红绿整行着色（新增绿 / 删除红）：用块级背景（QTextBlockFormat）铺满
            # 整行宽度——不能用 ExtraSelection（只覆盖选中文本的代码长度）。
            doc = self.text.document()
            bg_add = QColor(34, 120, 70, 110)
            bg_del = QColor(170, 45, 45, 120)
            for i, (sign, _t) in enumerate(lines):
                if sign not in ("+", "-"):
                    continue
                blk = doc.findBlockByNumber(i)
                if not blk.isValid():
                    continue
                cur = QTextCursor(blk)
                bfmt = QTextBlockFormat()
                bfmt.setBackground(bg_add if sign == "+" else bg_del)
                cur.mergeBlockFormat(bfmt)
                # 行首 +/- 符号（文本以 "+ "/"- " 开头）：加粗 + 绿/红着色，突出变更标记
                sign_ch = "+" if sign == "+" else "-"
                if _t.startswith(sign_ch + " "):
                    fc = QTextCursor(blk)
                    fc.setPosition(blk.position())
                    fc.setPosition(blk.position() + len(sign_ch),
                                  QTextCursor.MoveMode.KeepAnchor)
                    fmt = QTextCharFormat()
                    fmt.setFontWeight(QFont.Weight.Bold)
                    fmt.setForeground(QColor(OK if sign == "+" else ERR))
                    fc.mergeCharFormat(fmt)
            # 滚动到首个变更行（延迟多档重试：setPlainText 后立即滚动在长文档
            # 惰性布局/面板未就绪时会失效）；记录目标行，面板延迟显示时由
            # showEvent 再次定位（_on_file_diff 是「先 show_diff 再 show()」）。
            self._diff_scroll_to = first
            if first is not None:
                self._scroll_to_line(first)
            # 展示期保护：同文件自动预览（preview 事件）不得覆盖红绿差异视图
            self._diff_path = str(path)
            self._diff_until = time.time() + 3.0
            # 3 秒后恢复正常显示（序号匹配才恢复，防止覆盖更新的 diff）
            QTimer.singleShot(3000, lambda: self._restore_text_view(str(path), seq))
        except Exception as e:
            logging.getLogger("zhuzhu_Copilot.ui_ux").warning(
                "show_diff 渲染异常: %s", e)

    def _scroll_to_line(self, line):
        """精确滚动文本视图，把第 line（0-based）行定位到视口约 1/3 高度处：
        变更行上方保留上下文、红/绿 +/- 变更段完整可见（而非仅滚动到视口
        边缘的"微可见"）。QPlainTextEdit 长文档采用惰性布局，面板未显示时
        视口高度为 0、blockBoundingGeometry 不可靠——立即定位失败后用
        「80ms 起倍增」有界重试（最多 6 次 ≈5s），面板延迟显示也能坐实位置。"""
        if line is None:
            return
        line = int(line)
        attempts = [0]

        def _do() -> bool:
            try:
                if not self.isVisible() or not self.text.isVisible():
                    return False
                vp = self.text.viewport()
                vh = vp.height() if vp is not None else 0
                if vh <= 0:
                    return False
                doc = self.text.document()
                blk = doc.findBlockByNumber(line)
                if not blk.isValid():
                    return False
                sb = self.text.verticalScrollBar()
                c = QTextCursor(blk)
                # QPlainTextEdit 惰性布局：单次 ensureCursorVisible 只布局光标附近
                # 的块，滚动条范围是临时的（行数多时范围被低估、setValue 被钳制，
                # blockBoundingGeometry/cursorRect 对视口外块只返回占位坐标）。
                # 用「迭代逼近」：每轮把光标移到目标行强制其布局并滚入视口，再用
                # 实测光标矩形把目标行上移到视口约 1/3 处——范围随布局逐轮真实化，
                # 至多 8 轮收敛（目标在视口上部即提前完成）。
                for _i in range(8):
                    self.text.setTextCursor(c)
                    self.text.ensureCursorVisible()
                    r = self.text.cursorRect(c)
                    if r.isEmpty():
                        break
                    top = r.top()
                    if top <= vh * 0.60:
                        break          # 目标行已位于视口上部 → 定位完成
                    sb.setValue(sb.value() + int(top - vh * 0.30))
                # 兜底：无论迭代结果如何，确保目标行必在视口内（防上移过头被
                # 推出视口顶部；ensureCursorVisible 会把它拉回恰可见位置）
                self.text.setTextCursor(c)
                self.text.ensureCursorVisible()
                return True
            except Exception:
                return False

        def _retry(ms: int):
            if attempts[0] >= 6:
                return
            attempts[0] += 1
            if not _do():
                from PyQt6.QtCore import QTimer as _ScrollTimer
                _ScrollTimer.singleShot(ms, lambda: _retry(ms * 2))

        if not _do():
            _retry(80)

    def showEvent(self, e):
        """面板显示（尤其 _on_file_diff 先 show_diff 后 show 的场景）且 diff 仍
        在展示期内时，重新定位滚动到变更行，避免隐藏时首轮滚动全部落空。"""
        super().showEvent(e)
        target = getattr(self, "_diff_scroll_to", None)
        if target is not None:
            self._scroll_to_line(target)

    def _diff_protected(self, path: str) -> bool:
        """diff 展示期间，同文件的自动预览重刷是否应被拦截（保留红绿高亮视图）。"""
        try:
            if time.time() >= getattr(self, "_diff_until", 0.0):
                return False
            cur = os.path.abspath(getattr(self, "_diff_path", "") or "")
            return bool(cur) and os.path.abspath(str(path)) == cur
        except Exception:
            return False

    def _restore_text_view(self, path: str, seq: int = 0):
        """diff 高亮结束后复位为正常文本视图；仅当序号仍为最新才执行"""
        if seq and getattr(self, "_diff_seq", 0) != seq:
            return   # 已有更新的 diff 展示中，跳过本次恢复
        self._diff_until = 0.0
        self._diff_path = ""
        self._diff_scroll_to = None
        try:
            if os.path.isfile(path):
                self.show_text(path)
            else:
                self.title.setText("预览 · 文件已删除")
                self._switch_mode("text")
                self.text.setPlainText("（该文件已被 AI 删除）")
                self._hl = None
        except Exception:
            pass


def _urlencode(s: str) -> str:
    """URL 百分号编码（Bing 搜索查询词）"""
    from urllib.parse import quote
    return quote(s, safe="")


class TodosWindow(_RoundedFloatWindow):
    """TODOS 独立无边框悬浮窗口：停靠 AI 主窗口左侧、顶部与主窗口齐平。
    保留任务清单面板（标题+计数+右上角清空按钮），高度随清单长短自适应（不限高度）。
    纯黑+淡灰+白+深蓝四色极简风格，无 emoji。"""

    clear_requested = pyqtSignal()   # 用户手动清空任务清单

    WIDTH = 280   # 固定宽度，高度随内容

    def __init__(self, parent=None):
        super().__init__(parent)
        # 无边框 + Tool：独立悬浮窗口，不占任务栏、随主窗口隐藏/最小化。
        # 不加 WindowStaysOnTopHint：避免全局置顶遮挡其他应用；
        # 面板在应用激活时通过 _guard_panels 抬升，切换其他应用时随之退后
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.Tool)
        # 纯黑实心底（不透明）：面板铺满整个窗口，杜绝透出桌面
        self.setObjectName("todosWin")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"QWidget#todosWin {{ background: {BG}; }}")
        self.resize(self.WIDTH, 100)   # 宽度可调（右缘拖拽，左侧列联动）
        self.panel = TodosPanel(self)
        self.panel.clear_requested.connect(self.clear_requested.emit)
        self.panel.height_changed.connect(self._sync_height)
        # 初始同步：TodosPanel 构造时已完成首次测量并 setFixedHeight，
        # 但此时 height_changed 尚未连接 → 这里补一次，窗口高度跟随面板（不限高）
        self._sync_height(self.panel.height())
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.panel, 1)   # stretch=1：面板吃满窗口高度（清单超出在面板内滚动，
        #                                不再有窗口比面板高而摊出的空白条带）
        # 拖拽移动：记录是否正在拖拽及拖拽起始偏移
        self._dragging = False
        self._drag_offset = QPoint()
        self._dock_room = 0   # 融入栏内可分配的最大高度（0=不限；见 set_dock_room）
        # 顶部拖拽把手：自由拖动 + 位置持久化（位于「拖拽移动」之上，统一交互）
        self._install_drag_handle()
        # 把手安装后再同步一次窗口高度：首次 _sync_height 时把手尚未创建，
        # 高度未含把手，否则面板（固定高度）底部会溢出窗口被裁切（底部文字挤压）
        self._sync_height(self.panel.height())
        # 四边/四角拖拽调节大小（独立持久化）+ 持久化尺寸恢复
        self._install_resize_edges(host=self.parent())
        self.apply_saved_panel_size()

    def _on_size_changed(self, w: int, h: int):
        """尺寸变化：按最新宽度重算任务行高度（面板高度随之自适应）"""
        try:
            self.panel._resize_to_content()
        except Exception:
            pass

    def _set_dock_ui(self, docked: bool):
        """融入主面板：隐藏把手与 resize 热区，上下边距归零（贴边，与相邻面板之间不留
        底色缝隙），并按固定尺寸重钉窗口高度；贴附模式恢复常规边距与固定默认尺寸
        （清单超出即在面板内滚动，窗口不再随清单长短伸缩）。
        内容刷新由宿主（update_todos）负责，这里只重钉高度，不重读磁盘覆盖当前清单。"""
        super()._set_dock_ui(docked)
        try:
            self.panel.set_dock_tight(docked)
            self._sync_height(self.panel.required_height())
        except Exception:
            pass

    # ---- 固定尺寸（默认大小）+ 内容超出滚动 ----
    TODOS_MIN_H = 96      # 面板可用下限（标题行 + 至少一行任务，再小就没意义）

    def _fixed_height(self) -> int:
        """本面板的高度上限（=「默认大小」）：用户拖拽过则沿用其高度，否则用默认 320。
        贴附时高度按内容自适应且不超过该上限（清单超出即在面板内滚动），
        融入时另有按行数上限（见 _fit_dock_height）。"""
        manual = int(getattr(self, "_manual_h", 0) or 0)
        return max(self.TODOS_MIN_H, manual or self.DEFAULT_SIZE[1])

    def restore_pre_dock_size(self, w: int, h: int):
        """浮出（切回贴附）后只沿用融入前的宽度：高度交给 _sync_height 按「内容所需 +
        默认/拖拽上限」决定。若把融入前的贴合高度写进 _manual_h（= 高度上限），
        上限会被压到当时的清单长度，之后清单变长也长不高。"""
        cap = int(getattr(self, "_manual_h", 0) or 0)
        self._set_panel_size(w, cap or self._fixed_height())
        self._manual_h = cap                     # 复位上限，不被融入前的贴合高度覆盖
        self._sync_height(self.panel.required_height())

    def set_dock_room(self, room: int):
        """融入栏内可分配给本面板的最大高度（0=不限）。栏高不足时按此收缩
        （面板内滚动），避免整列溢出把清单底部裁掉、也不与同栏面板相互钳制。"""
        room = max(0, int(room or 0))
        if room != int(getattr(self, "_dock_room", 0) or 0):
            self._dock_room = room
            self._sync_height(self.panel.required_height())

    def _pin_height(self, need: int):
        """把窗口高度钉死为 need（min=max，布局无法拉伸 → 栏内不留空白条带）"""
        need = max(self.TODOS_MIN_H, int(need))
        if self.minimumHeight() != need or self.maximumHeight() != need:
            self.setMinimumHeight(need)
            self.setMaximumHeight(need)
        if self.height() != need:
            self.resize(self.width(), need)

    def _fit_dock_height(self):
        """融入主面板：窗口高度 = min(内容所需, 融入行数上限, 栏内可用高度)。

        融入行数上限 = 前 DOCK_MAX_ROWS 行的高度：融入栏里工作树/Git/任务清单共享栏高，
        清单再长也不该把同栏 Git 面板挤小（用户反馈「git 面板变小了」），超出部分在
        面板内滚动查看。窗口恰好吃满面板 → 不再有「窗口比面板高」的空白条带，
        释放的空间全部归 Git 面板吸收。"""
        need = min(self.panel.required_height(), self.panel.dock_max_height())
        room = int(getattr(self, "_dock_room", 0) or 0)
        if room:
            need = min(need, room)
        self._pin_height(need)

    def _sync_height(self, h: int):
        """任务清单窗口高度（h = 内容所需高度）：
        - 融入主面板：min(内容所需, DOCK_MAX_ROWS 行, 栏内可用)（见 _fit_dock_height）；
        - 贴附：随内容自适应，但**不超过固定默认尺寸**（_fixed_height；用户拖拽过则以
          拖拽高度为上限）—— 清单短时不至于撑着大片空白，清单长时停在默认高度并在
          面板内滚动查看（用户反馈「切换为贴附模式时 todos 面板太大」）。
        顶部拖拽把手固定占位（贴附时可见）。"""
        if self._dock_is_immersed():
            self._fit_dock_height()
            return
        need = int(h or 0) or self.panel.required_height()
        self._pin_height(min(need, self._fixed_height()))

    def fit_content_height(self):
        """按当前内容贴合一次并「定格」为该固定高度：供「重置全部面板位置」调用。
        重置后不再随内容伸缩（清单超出滚动查看），故取内容所需高度作为固定值，
        避免清单很短时留大片空白。"""
        try:
            need = self.panel.required_height()
            self._manual_h = need
            if self._dock_is_immersed():
                self._fit_dock_height()
                return
            self._pin_height(need)
        except Exception:
            pass

    def update_todos(self, todos: list):
        self.panel.update_todos(todos)

    # ---- 鼠标拖拽移动（仅贴附模式；融入主面板时位置由栏内布局管理 → 禁用拖动） ----
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and not self._dock_is_immersed():
            self._dragging = True
            self._drag_offset = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            e.accept()
        else:
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if (self._dragging and not self._dock_is_immersed()
                and (e.buttons() & Qt.MouseButton.LeftButton)):
            self.move(e.globalPosition().toPoint() - self._drag_offset)
            e.accept()
        else:
            super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._dragging:
            self._dragging = False
            e.accept()
        else:
            super().mouseReleaseEvent(e)


class _HtmlListDelegate(QStyledItemDelegate):
    """将 QListWidgetItem 的 UserRole(HTML) 渲染为多行富文本，支持多色/多行。
    用于 git 面板分支/提交列表：分支名、哈希、作者、说明可用不同颜色区分。"""

    def __init__(self, max_w: int = 0, parent=None):
        super().__init__(parent)
        self._max_w = max_w
        # (text, width) → QSize 缓存：app.setStyleSheet/主题重建会触发整表 relayout，
        # 每次都新建 QTextDocument 解析 HTML 极慢（切换主题后主面板无响应的诱因之一）
        self._size_cache = {}

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # 每条 Git 记录画独立长方形卡片（背景+边框）：彼此以边框分割开，
        # 避免整列填充同色背景看起来"合并成一个长方形"
        r = option.rect.adjusted(2, 2, -2, -2)
        painter.setPen(QPen(QColor(BORDER_SOFT), 1))
        painter.setBrush(QColor(PANEL))
        painter.drawRoundedRect(r, 6, 6)
        doc = QTextDocument()
        doc.setDefaultFont(option.font)
        doc.setHtml(index.data(Qt.ItemDataRole.UserRole)
                    or index.data(Qt.ItemDataRole.DisplayRole) or "")
        doc.setTextWidth(max(1, r.width() - 8))
        painter.translate(r.left() + 4, r.top() + 4)
        doc.documentLayout().draw(painter, QAbstractTextDocumentLayout.PaintContext())
        painter.restore()

    def sizeHint(self, option, index):
        """行高 = 实际渲染富文本高度 + 卡片内边距。

        关键：必须与 paint() 使用「相同宽度（w-12）＋相同字体（option.font）」测量，
        否则换行/行高不一致会导致最后一行（作者·时间）被裁切。实测 QListView 的行高
        取本 sizeHint（而非 item 的显式 setSizeHint），因此这里是唯一的高度裁决点。
        """
        text = (index.data(Qt.ItemDataRole.UserRole)
                or index.data(Qt.ItemDataRole.DisplayRole) or "")
        w = self._max_w or 240
        key = (text, w)
        sz = self._size_cache.get(key)
        if sz is None:
            doc = QTextDocument()
            doc.setDefaultFont(option.font)
            doc.setHtml(text)
            # 与 paint 完全一致的绘制宽度（item 宽 - 12px 内边距）
            doc.setTextWidth(max(1, w - 12))
            # 6px 顶 + 6px 底内边距 + 2px 容差；下限 40 兜底占位行
            h = int(doc.size().height()) + 14
            sz = QSize(w, max(h, 40))
            # 限制缓存规模：超限整体清空（单窗口 git 列表通常远小于此阈值）
            if len(self._size_cache) > 2000:
                self._size_cache.clear()
            self._size_cache[key] = sz
        return sz


class GitLogWindow(_RoundedFloatWindow):
    """Git 只读图形面板：展示仓库分支 / 提交历史，带颜色标识。
    顶部「分支 / 提交」切换；当前分支高亮、哈希/时间用不同颜色区分。
    无边框 + Tool（不占任务栏），非置顶、不可拖动；位于 todos 下方 10px。
    仅只读展示，不提供任何 push/pull/checkout 等写操作。
    git 数据获取放后台线程（起子进程可能耗时）；完成后通过 data_ready 信号
    回主线程填充，避免设置工作目录时阻塞 UI 造成卡死。"""

    WIDTH = 280   # 与 todos / worktree 面板同宽
    DEFAULT_SIZE = (WIDTH, 320)
    HEIGHT_MIN = 120   # 融入栏内最小高度（余量吸收者，避免被压成一条缝）

    data_ready = pyqtSignal(object)   # 后台线程 → 主线程：装载的 git 视图数据

    def __init__(self, parent=None):
        super().__init__(parent)
        # 无边框 + Tool：独立悬浮窗口，不占任务栏。不加 WindowStaysOnTopHint（避免
        # 全局置顶遮挡其他应用）；应用激活时由 _guard_panels 抬升，不随点击消失
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.Tool)
        self.setObjectName("gitLogWin")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"QWidget#gitLogWin {{ background: {BG}; }}")
        self._view = "commit"   # commit / branch，默认提交历史（最新在上、最旧在下）
        self.resize(self.WIDTH, 320)   # 宽高均可调（四边/四角拖拽，独立持久化）
        self._busy = False      # 后台加载进行中（防止连续切换重复起线程）
        self._pending_refresh = False  # 忙碌期间的刷新请求：加载完成后补一次（防切目录刷新被吞）
        self.data_ready.connect(self._on_data_ready)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        # 标题行：标题 + 分支/提交 视图切换
        head = QHBoxLayout()
        head.setSpacing(6)
        title = QLabel("Git 面板")
        title.setStyleSheet(f"color: {TEXT}; font-size: 13px; font-weight: 700;")
        head.addWidget(title)
        head.addStretch(1)
        self.branch_btn = QPushButton("分支")
        self.commit_btn = QPushButton("提交")
        for b in (self.branch_btn, self.commit_btn):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFixedHeight(22)
        self.branch_btn.clicked.connect(lambda: self._set_view("branch"))
        self.commit_btn.clicked.connect(lambda: self._set_view("commit"))
        head.addWidget(self.branch_btn)
        head.addWidget(self.commit_btn)
        lay.addLayout(head)

        self.list = QListWidget()
        self.list.setStyleSheet(
            f"QListWidget {{ background: {PANEL}; color: {TEXT};"
            f"border: 1px solid {BORDER}; border-radius: 6px; padding: 4px; }}")
        self.list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.list.setItemDelegate(_HtmlListDelegate(self.WIDTH - 40, self.list))
        lay.addWidget(self.list, 1)
        self._set_view(self._view, do_refresh=False)
        # 顶部拖拽把手：自由拖动 + 位置持久化 + 融入主面板（置于整个面板最上方）
        self._install_drag_handle()
        # 四边/四角拖拽调节大小（独立持久化）+ 持久化尺寸恢复
        self._install_resize_edges(host=self.parent())
        self.apply_saved_panel_size()

    def _set_dock_ui(self, docked: bool):
        """融入主面板：隐藏把手与 resize 热区，并给列表一个最小高度——
        本面板是同栏里唯一不封顶的成员（负责吸收任务清单/工作树定高后释放的余量），
        设底限避免主面板被压得很矮时列表被压成一条缝。
        融入时上下边距归零（贴边）：卡片占满窗口上下，不再留有与上下相邻面板之间的底色缝隙。"""
        super()._set_dock_ui(docked)
        try:
            self.setMinimumHeight(self.HEIGHT_MIN if docked else 0)
            lay = self.layout()
            if lay is not None:
                lay.setContentsMargins(*(self.MARGINS_DOCK if docked else self.MARGINS))
        except Exception:
            pass

    def _on_size_changed(self, w: int, h: int):
        """尺寸变化后重建列表 delegate（按新宽度量行高），避免文案行被挤压/裁切"""
        try:
            w = int(w)
            self.list.setItemDelegate(_HtmlListDelegate(max(120, w - 40), self.list))
            if self.list.count():
                self.list.doItemsLayout()
        except Exception:
            pass

    def _set_view(self, view: str, do_refresh: bool = True):
        self._view = view
        active = (f"QPushButton {{ background:{ACCENT}; color:#FFFFFF;"
                  f"border:none;border-radius:5px;padding:0 8px;"
                  f"font-size:11px;font-weight:700; }}"
                  f"QPushButton:hover {{ background:{ACCENT_HOVER}; }}")
        idle = (f"QPushButton {{ background:transparent;color:{TEXT_DIM};"
                f"border:1px solid {BORDER};border-radius:5px;padding:0 8px;"
                f"font-size:11px; }}"
                f"QPushButton:hover {{ color:{TEXT};border-color:{ACCENT_HOVER}; }}")
        self.branch_btn.setStyleSheet(active if view == "branch" else idle)
        self.commit_btn.setStyleSheet(active if view == "commit" else idle)
        if do_refresh:
            self.refresh()

    def _make_item(self, html: str, height: int = None) -> QListWidgetItem:
        # 纯文本占位（无 HTML 标签）包一层主题文字色：深色模式下为白字，
        # 否则 _HtmlListDelegate 用 QTextDocument 默认黑色渲染，深色底上看不清
        if not html.lstrip().startswith("<"):
            html = (f'<div style="color:{TEXT};font-size:12px;">'
                    f'{_esc(html)}</div>')
        it = QListWidgetItem()
        # 固定 item 字体 = 列表字体：使 _HtmlListDelegate 测量/绘制使用同一字体，
        # 行高与渲染内容完全一致（行高裁决在 delegate.sizeHint，见其注释）
        it.setFont(self.list.font())
        it.setData(Qt.ItemDataRole.UserRole, html)
        it.setSizeHint(QSize(self.WIDTH - 40, height or 40))
        return it

    def refresh(self):
        """异步刷新 git 面板：后台线程取数据（git 子进程），主线程填充，避免卡死。
        连续触发（切视图/切目录）复用单线程，数据装载通过 data_ready 信号返回。"""
        view = self._view
        if self._busy:
            # 上一次后台加载未完成：本次视图在完成后由 _on_data_ready 补一次刷新，
            # 避免切目录期间刷新的请求被吞导致面板长期停留在旧目录内容。
            self._pending_refresh = True
            return
        self._busy = True
        self.list.addItem(self._make_item("加载中…", 40))

        def _load():
            from zhuzhu_Copilot.core import agent_git, agent_tools
            payload = {"view": view}
            try:
                payload["workdir"] = agent_tools.get_workdir()
            except Exception:
                payload["workdir"] = ""
            try:
                if not agent_git.is_git_repo():
                    payload["error"] = "（当前工作目录不是 Git 仓库）"
                elif view == "commit":
                    payload["rows"] = agent_git.list_commits()
                else:
                    payload["rows"] = agent_git.list_branches()
            except Exception as e:
                payload["error"] = f"（读取失败：{e}）"
            try:
                self.data_ready.emit(payload)
            except RuntimeError:
                # 面板已随主题重建/会话切换销毁（后台线程仍在跑）：信号目标已
                # 死，静默丢弃即可——向已删除控件 emit 会把异常抛进线程
                # excepthook 弹错误提示（切主题时报错的次要来源）
                pass

        th = threading.Thread(target=_load, daemon=True)
        th.start()

    def _on_data_ready(self, payload):
        """主线程装载后台线程取回的 git 数据（切视图期间视图可能已变，按最新视图重建）。"""
        self._busy = False
        # 视图在后台加载期间被切换：忽略过期响应，重取当前视图
        if not payload or payload.get("view") != self._view:
            self.refresh()
            return
        # 加载期间工作目录已变更：本次数据属于旧目录，跳过填充并立即按新目录重拉
        cur_wd = ""
        try:
            from zhuzhu_Copilot.core import agent_tools
            cur_wd = agent_tools.get_workdir()
        except Exception:
            pass
        p_wd = payload.get("workdir")
        if p_wd is not None and p_wd != cur_wd:
            self.refresh()
            return
        # 忙碌期间挂起的刷新请求：填充后补一次（保证最后一次请求的数据落地）
        if self._pending_refresh:
            self._pending_refresh = False
            self.refresh()
            return
        self.list.clear()
        error = payload.get("error")
        if error:
            self.list.addItem(self._make_item(error, 40))
            return
        rows = payload.get("rows") or []
        if self._view == "commit":
            self._fill_commits(rows)
        else:
            self._fill_branches(rows)

    def _fill_branches(self, branches: list):
        if not branches:
            self.list.addItem(self._make_item("（无本地分支）", 40))
            return
        for b in branches:
            if b.get("current"):
                dot, name_color = "●", ACCENT_HOVER
                tag = f'<span style="color:{OK};font-size:10px;"> 当前</span>'
            else:
                dot, name_color, tag = "○", TEXT, ""
            html = (
                f'<div style="color:{name_color};font-weight:700;font-size:12px;">'
                f'{dot} {_esc(b["branch"])}{tag}</div>'
                f'<div style="color:{ACCENT};font-size:10px;margin-top:2px;">'
                f'{_esc(b["hash"])}</div>'
                f'<div style="color:{TEXT_DIM};font-size:10px;">'
                f'{_esc(b["author"])} · {_esc(b["date"])}</div>'
                f'<div style="color:{TEXT};font-size:11px;">{_esc(b["subject"])}</div>')
            it = self._make_item(html)
            it.setToolTip(f"分支: {b['branch']}\n作者: {b['author']}\n时间: {b['date']}")
            self.list.addItem(it)

    def _fill_commits(self, commits: list):
        if not commits:
            self.list.addItem(self._make_item("（暂无提交记录）", 40))
            return
        # 全量历史可能成百上千条：批量插入期间关闭重绘，避免逐条刷新卡顿
        self.list.setUpdatesEnabled(False)
        try:
            for c in commits:
                html = (
                    f'<div style="color:{ACCENT};font-family:Consolas;font-size:11px;">'
                    f'{_esc(c["hash"])}</div>'
                    f'<div style="color:{TEXT};font-size:11px;">{_esc(c["subject"])}</div>'
                    f'<div style="color:{TEXT_DIM};font-size:10px;">'
                    f'{_esc(c["author"])} · {_esc(c["date"])}</div>')
                # 自动按渲染高度计项高（long subject/author 换行后不遮挡作者时间行）
                self.list.addItem(self._make_item(html))
        finally:
            self.list.setUpdatesEnabled(True)
            self.list.viewport().update()


class _ContentSelectionDelegate(QStyledItemDelegate):
    """工作树选中 / 悬停态：仅高亮「图标 + 文件名」内容区域，不整行变色。

    胶囊宽度 = 内容起点（含树缩进）+ 图标宽 + 间距 + 文本实际渲染宽 + 左右内边距，
    随目录 / 文件名长度自适应（短名短胶囊、长名长胶囊），仅在超出整行可用宽时截断。
    为此同时接管 selected 与 hover：Qt 默认的整行 selected 背景、QSS 的整行 hover
    背景均已置空（见 WorktreeWindow 的 item:hover: transparent），统一由本委托自绘，
    确保两种状态的胶囊宽度一致且都贴合文本长度。"""

    H_PAD = 7        # 胶囊左右内边距（px）
    ICON_GAP = 6     # 图标与文字之间的间距（px）
    RADIUS = 5       # 胶囊圆角（px）
    MIN_W = 26       # 最小胶囊宽（纯图标 / 空文本行仍可见）
    HOVER_ALPHA = 0.40   # 鼠标悬停胶囊的不透明度（40%）；集中一处，便于按需调整

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        sel = bool(opt.state & QStyle.StateFlag.State_Selected)
        hov = bool(opt.state & QStyle.StateFlag.State_MouseOver)
        if sel or hov:
            # 清除 Qt 默认整行背景，改由下面自绘「内容宽度」胶囊
            opt.state &= ~QStyle.StateFlag.State_Selected
            opt.state &= ~QStyle.StateFlag.State_MouseOver
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(ACCENT) if sel else _fade(ACCENT, self.HOVER_ALPHA))
            painter.drawRoundedRect(self._capsule(opt), self.RADIUS, self.RADIUS)
            painter.restore()
            if sel:
                opt.palette.setColor(QPalette.ColorRole.Text, QColor("#FFFFFF"))
                opt.palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
        super().paint(painter, opt, index)

    def _capsule(self, opt: QStyleOptionViewItem) -> QRectF:
        """胶囊矩形：宽度贴合「图标 + 文本」实际长度（自适应），不铺满整行。"""
        fm = opt.fontMetrics
        text_w = fm.horizontalAdvance(opt.text) if opt.text else 0
        icon_w = 0
        if opt.icon is not None and not opt.icon.isNull():
            isz = opt.decorationSize
            iw = int(isz.width()) if isz is not None and isz.isValid() else 16
            icon_w = iw + self.ICON_GAP
        content_w = icon_w + text_w + self.H_PAD * 2
        avail = max(self.MIN_W, int(opt.rect.width()) - 4)
        w = float(max(self.MIN_W, min(content_w, avail)))
        return QRectF(float(opt.rect.left() + 2), float(opt.rect.top() + 2), w,
                      float(max(1, opt.rect.height() - 4)))


class _AnimatedFileTree(QTreeWidget):
    """工作树：自定义分支箭头，展开/收起时旋转动画（右→下，130ms）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(130)
        self._anim.valueChanged.connect(lambda v: self.viewport().update())
        self._rot = {}      # id(item) -> 当前角度
        self._cur = None    # 当前动画项
        self.itemExpanded.connect(lambda it: self._anim_to(it, 90))
        self.itemCollapsed.connect(lambda it: self._anim_to(it, 0))

    def _anim_to(self, item, target):
        self._cur = id(item)
        self._anim.stop()
        self._anim.setStartValue(self._rot.get(id(item), 0.0))
        self._anim.setEndValue(float(target))
        self._anim.start()

    def drawBranches(self, painter, rect, index):
        item = self.itemFromIndex(index)
        if item is None or item.childCount() == 0:
            return
        ind = self.indentation()
        cx = rect.right() - ind / 2
        cy = rect.center().y()
        if self._cur == id(item) and self._anim.state() != QAbstractAnimation.State.Stopped:
            rot = float(self._anim.currentValue())
        else:
            rot = 90.0 if self.isExpanded(index) else 0.0
        self._rot[id(item)] = rot
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(cx, cy)
        painter.rotate(rot)
        pen = QPen(QColor(TEXT_DIM), 1.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        r = 3.0
        # 简洁 chevron：折叠为「›」，展开旋转 90° 变「v」，纤细圆润
        painter.drawLine(QPointF(-r * 0.7, -r * 0.7), QPointF(r * 0.3, 0))
        painter.drawLine(QPointF(-r * 0.7, r * 0.7), QPointF(r * 0.3, 0))
        painter.restore()


class WorktreeWindow(_RoundedFloatWindow):
    """工作树面板：展示 git 仓库根目录（或工作目录就近向上）的文件/目录树。
    目录/文件矢量图标 + 展开/收起箭头动画 + 单击选中（深蓝）+ 右键删除/上传菜单。
    无边框 + Tool（不占任务栏），非置顶、不可拖动；停靠 AI 面板左侧顶部齐平。
    颜色与 todos/git 面板统一（纯黑+淡灰+白+深蓝）。"""

    WIDTH = 280      # 与 todos / git 面板同宽
    DEFAULT_SIZE = (WIDTH, 360)
    MARGINS_DOCK = (10, 2, 10, 0)   # 融入：顶部留 2px 防标题裁切，底边贴边（缝隙并入下方 Git）

    file_open_requested = pyqtSignal(str)   # 双击文件 → 请求打开代码预览（文件路径）
    thumb_ready = pyqtSignal(str, QIcon)    # 图片缩略图后台解码完成 → 主线程回填（path, icon）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._root = None
        # 不加 WindowStaysOnTopHint（避免全局置顶遮挡其他应用）；
        # 应用激活时由 _guard_panels 抬升，不随点击消失
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.Tool)
        self.setObjectName("wtWin")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"QWidget#wtWin {{ background: {BG}; border-radius: 18px; }}")
        self.resize(self.WIDTH, 360)   # 宽高均可调（四边/四角拖拽，独立持久化）
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)
        title = QLabel("工作树（双击文件预览）")
        title.setStyleSheet(f"color: {TEXT}; font-size: 13px; font-weight: 700;")
        lay.addWidget(title)
        self.tree = _AnimatedFileTree(self)
        self.tree.setHeaderHidden(True)
        self.tree.setColumnCount(1)
        # 长文件名可横向滚动：内容超出面板宽度时出现横向滚动条，不截断文本
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.tree.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.tree.header().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setStretchLastSection(False)
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self.tree.setStyleSheet(
            f"QTreeWidget {{ background: {PANEL}; color: {TEXT};"
            f"border: 1px solid {BORDER}; border-radius: 6px; padding: 4px 4px 4px 1px;"
            f"outline: none; }}"
            f"QTreeWidget::item {{ padding: 3px 4px; border-radius: 4px; }}"
            f"QTreeWidget::item:selected {{ background: transparent; }}"
            f"QTreeWidget::item:hover {{ background: transparent; }}"
            f"QTreeWidget::item:focus {{ outline: none; }}")
        # 窗口失焦（如拖拽移动）时 Qt 会在选中项上画白色焦点边框，禁止其抢焦
        self.tree.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        # 选中态仅高亮「图标+文件名」内容胶囊，不整行变蓝
        self.tree.setItemDelegate(_ContentSelectionDelegate(self.tree))
        self.tree.itemExpanded.connect(self._on_expand)
        self.tree.itemDoubleClicked.connect(self._on_double_click)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_context_menu)
        # 工作树不接收拖放（上传走右键菜单）：禁用拖放高亮，避免拖到窗口时出现白色选中边框
        self.tree.setAcceptDrops(False)
        self.tree.setDropIndicatorShown(False)
        self.tree.setDragEnabled(False)
        lay.addWidget(self.tree, 1)
        # 顶部拖拽把手：自由拖动 + 位置持久化（置于整个面板最上方）
        self._install_drag_handle()
        # 图片缩略图异步回填：解码在后台线程完成，回主线程仅 setIcon（不阻塞扫描）
        self.thumb_ready.connect(self._on_thumb_ready)
        # 四边/四角拖拽调节大小（独立持久化）+ 持久化尺寸恢复
        self._install_resize_edges(host=self.parent())
        self.apply_saved_panel_size()
        # 文件树实时更新：监听根目录与已展开目录，外部变更（AI 生成/编辑文件、
        # 用户在资源管理器增删）后自动防抖刷新并恢复展开状态（原生文件事件，无轮询）
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._on_fs_changed)
        self._fs_timer = QTimer(self)
        self._fs_timer.setSingleShot(True)
        self._fs_timer.setInterval(500)     # 防抖：批量生成文件时只重建一次树
        self._fs_timer.timeout.connect(self._reload_preserving_expand)

    def _set_dock_ui(self, docked: bool):
        """融入主面板：隐藏把手与 resize 热区；顶部留白收窄（内容上移）并抬高最小高度
        （向下增高一点点），充分利用 dock 栏垂直空间；贴附模式恢复原布局。"""
        super()._set_dock_ui(docked)
        try:
            lay = self.layout()
            if lay is not None:
                lay.setContentsMargins(*(self.MARGINS_DOCK if docked else self.MARGINS))
            if docked:
                self.setMinimumHeight(400)
            else:
                self.setMinimumHeight(0)
        except Exception:
            pass

    # ---------- 刷新 / 填充 ----------
    def refresh(self):
        """刷新工作树：以「实际工作目录」为树根，切换工作目录立即反映新目录结构；
        工作目录无效时回退到 git 仓库根；两者均无则显示提示。
        注：工作树为逐层懒加载——根层只列一层子项，目录展开时才填充其子层，
        避免同步递归建全部子节点/图标造成主线程卡顿（曾 758 次图标渲染/2.7s）；
        真正的 git 子进程刷新已由 git 面板走后台线程。"""
        from zhuzhu_Copilot.core import agent_git, agent_tools
        # 工作树以「实际工作目录」为树根：展示当前目录结构，切换工作目录立即反映。
        root = agent_tools.get_workdir()
        if not root or not os.path.isdir(root):
            # 工作目录无效时回退到 git 仓库根
            root = agent_git.repo_dir()
        self._root = root or None
        self.tree.clear()
        if not root or not os.path.isdir(root):
            self.tree.addTopLevelItem(
                QTreeWidgetItem(["（未找到工作目录 / git 仓库）"]))
            return
        top = QTreeWidgetItem([os.path.basename(root.rstrip("\\/")) or root])
        top.setData(0, Qt.ItemDataRole.UserRole, str(root))
        top.setToolTip(0, root)
        top.setIcon(0, _line_icon("folder", 18, TEXT_DIM))
        self.tree.addTopLevelItem(top)
        self._fill(top, Path(root))
        top.setExpanded(True)
        self._sync_watch()

    def _watch_dirs(self) -> list:
        """需要监听的目录：树根 + 所有已展开目录（懒加载目录展开后才监听其内容）"""
        out = []
        if self._root and os.path.isdir(self._root):
            out.append(str(self._root))
        stack = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while stack:
            it = stack.pop()
            if it is None:
                continue
            p = it.data(0, Qt.ItemDataRole.UserRole)
            if p and os.path.isdir(p) and it.isExpanded():
                out.append(str(p))
            stack.extend(it.child(i) for i in range(it.childCount()))
        return out

    def _sync_watch(self):
        """同步 QFileSystemWatcher 监听集合（只监听目录，避免监听巨量文件句柄）"""
        try:
            want = set(self._watch_dirs())
            cur = set(self._watcher.directories())
            if want - cur:
                self._watcher.addPaths(sorted(want - cur))
            if cur - want:
                self._watcher.removePaths(sorted(cur - want))
        except Exception:
            pass

    def _on_fs_changed(self, _path: str = ""):
        """目录内容变化回调：防抖启动刷新（连续写入只刷一次）"""
        try:
            self._fs_timer.start()
        except Exception:
            pass

    def _expanded_paths(self) -> set:
        """当前所有展开项的路径集合（刷新后据此恢复浏览位置）"""
        out = set()
        stack = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while stack:
            it = stack.pop()
            if it is None:
                continue
            p = it.data(0, Qt.ItemDataRole.UserRole)
            if it.isExpanded() and p:
                out.add(str(p))
            stack.extend(it.child(i) for i in range(it.childCount()))
        return out

    def _expand_paths(self, keep: set):
        """按路径集合逐层重新展开（懒加载目录在此处按需填充一层）"""
        stack = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while stack:
            it = stack.pop()
            if it is None:
                continue
            p = it.data(0, Qt.ItemDataRole.UserRole)
            if p and str(p) in keep and os.path.isdir(p):
                if it.childCount() == 1 and it.child(0).data(0, Qt.ItemDataRole.UserRole) is None:
                    ph = it.child(0)
                    it.removeChild(ph)
                    self._fill(it, Path(str(p)))
                it.setExpanded(True)
                stack.extend(it.child(i) for i in range(it.childCount()))
        self._sync_watch()

    def _reload_preserving_expand(self):
        """实时刷新：重建工作树后恢复展开项，用户/AI 操作不丢失浏览位置"""
        keep = self._expanded_paths()
        self.refresh()
        self._expand_paths(keep)

    def _fill(self, parent_item, path: Path, depth: int = 0):
        """填充 path 的直接子项到 parent_item；目录节点只挂「展开占位符」，
        用户点开目录（itemExpanded → _on_expand）时才真正填充其子层。
        逐层懒加载避免一次性创建整棵树的全部节点与图标：未展开的深层目录
        本不可见，预建纯属浪费（曾 758 次图标渲染/2.7s 主线程卡顿）。
        scandir 一次性取名称与类型，避免逐条 is_file() 重复系统调用。
        图片文件先以类型图标占位（不解码磁盘），真实缩略图由后台线程解码后
        经 thumb_ready 信号回填 —— 大目录含大量图片时根层填充不再卡死主线程。"""
        try:
            with os.scandir(path) as it:
                items = [(e.name, e.is_dir(follow_symlinks=False)) for e in it]
        except OSError:
            return
        items.sort(key=lambda t: (not t[1], t[0].lower()))
        pending_thumbs = []   # 需要后台解码的图片文件路径列表
        for name, is_dir in items:
            if name == ".git":
                continue
            p = path / name
            if is_dir:
                item = QTreeWidgetItem([name + "/"])
                item.setIcon(0, _line_icon("folder", 18, TEXT_DIM))
                # 目录默认只挂一个占位符子项：让箭头可展开；_on_expand 时替换为真实子项
                ph = QTreeWidgetItem(["(点击加载…)"])
                ph.setData(0, Qt.ItemDataRole.UserRole, None)
                item.addChild(ph)
            else:
                ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
                item = QTreeWidgetItem([name])
                if ext in _IMG_EXTS:
                    # 图片：先用图片类型矢量图标占位（零磁盘 I/O），后台解码后回填缩略图
                    item.setIcon(0, _line_icon("img", 18, TEXT_DIM))
                    pending_thumbs.append(str(p))
                else:
                    item.setIcon(0, _file_thumb(p, ext, 18, TEXT_DIM))
            item.setData(0, Qt.ItemDataRole.UserRole, str(p))
            item.setToolTip(0, str(p))
            parent_item.addChild(item)
        if pending_thumbs:
            threading.Thread(target=self._load_thumbs, args=(pending_thumbs,),
                             daemon=True).start()

    def _load_thumbs(self, paths: list):
        """后台线程解码图片缩略图（QPixmap 解码可能慢，尤其大图）；
        每张解码后经 thumb_ready 信号回主线程 setIcon，不阻塞工作树扫描。"""
        for p in paths:
            try:
                icon = _file_thumb(p, p.rsplit(".", 1)[-1].lower(), 18, TEXT_DIM)
                self.thumb_ready.emit(p, icon)
            except Exception:
                pass

    def _on_thumb_ready(self, path: str, icon: QIcon):
        """主线程回填缩略图：按路径定位树节点并 setIcon（节点已刷新/删除则静默跳过）"""
        it = self.tree.findItems(os.path.basename(path), Qt.MatchFlag.MatchExactly
                                 | Qt.MatchFlag.MatchRecursive)
        for x in it:
            if x.data(0, Qt.ItemDataRole.UserRole) == path:
                x.setIcon(0, icon)
                break

    def _on_expand(self, item):
        """懒加载：展开含占位子项的目录时，用其真实子项替换占位（一次一层）"""
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if not path or not os.path.isdir(path):
            return
        if item.childCount() == 1 and item.child(0).data(0, Qt.ItemDataRole.UserRole) is None:
            placeholder = item.child(0)
            item.removeChild(placeholder)
            self._fill(item, Path(path))
        self._sync_watch()

    def _on_double_click(self, item, column):
        """双击文件 → 请求打开代码预览；双击目录超时还原为展开/收起"""
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if path and os.path.isfile(path):
            self.file_open_requested.emit(path)

    # ---------- 右键菜单：打开 / 删除 / 上传 ----------
    def _on_context_menu(self, pos):
        item = self.tree.itemAt(pos)
        if item is None:
            return
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if not path:
            return
        self.tree.setCurrentItem(item)
        menu = QMenu(self)
        open_ex = menu.addAction("打开于资源管理器")
        open_ex.triggered.connect(lambda: self._open_in_explorer(path))
        if os.path.isdir(path):
            up = menu.addAction("上传文件到此目录…")
            up.triggered.connect(lambda: self._upload_to(path))
        dl = menu.addAction("删除")
        dl.triggered.connect(lambda: self._delete_path(path, item))
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _open_in_explorer(self, path: str):
        """在资源管理器中定位：目录直接打开；文件用 explorer /select 选中"""
        try:
            if os.path.isdir(path):
                os.startfile(path)
            else:
                import subprocess
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        except Exception:
            pass

    def _upload_to(self, target_dir: str):
        """上传文件到指定目录（真实文件选择 + 复制，无 mock）"""
        paths, _ = QFileDialog.getOpenFileNames(self, "选择要上传的文件", "",
                                                "所有文件 (*.*)")
        if not paths:
            return
        ok, fail = 0, 0
        for src in paths:
            try:
                shutil.copy2(src, os.path.join(target_dir, os.path.basename(src)))
                ok += 1
            except OSError:
                fail += 1
        msg = f"已上传 {ok} 个文件到 {target_dir}"
        if fail:
            msg += f"，{fail} 个失败"
        QMessageBox.information(self, "上传完成", msg)
        self.refresh()

    def _delete_path(self, path: str, item):
        """删除所选文件/目录（弹窗确认；根目录与 .git 不可删）"""
        if self._root and os.path.abspath(path) == os.path.abspath(self._root):
            _warn_box(self, "不可删除", "不能删除工作树根目录")
            return
        if os.path.basename(path) == ".git":
            _warn_box(self, "不可删除", "不能删除 .git 目录")
            return
        ret = QMessageBox.question(
            self, "确认删除", f"确定删除“{os.path.basename(path)}”？不可恢复。")
        if ret != QMessageBox.StandardButton.Yes:
            return
        try:
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            else:
                os.remove(path)
        except OSError as e:
            QMessageBox.warning(self, "删除失败", str(e))
            return
        parent = item.parent()
        if parent is not None:
            parent.removeChild(item)
        else:
            idx = self.tree.indexOfTopLevelItem(item)
            self.tree.takeTopLevelItem(idx)


class ExtPanelWindow(_RoundedFloatWindow):
    """扩展功能面板浮窗：承载插件/工作流/代码注册的自定义 UI 面板。

    无边框 + Tool（不占任务栏，随主窗口隐藏/最小化）；固定宽度、高度随内容，
    支持鼠标拖拽移动（拖走后守卫仅抬升不强制归位）。内容由面板注册表的
    build_panel(owner) 工厂构建，纯黑+淡灰+白+深蓝极简风格，无 emoji。"""

    def __init__(self, name: str, title: str, widget, width: int, height: int,
                 parent=None):
        super().__init__(parent)
        self.name = name
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.Tool)
        self.setObjectName("extWin")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"QWidget#extWin {{ background: {BG}; }}")
        self.setFixedWidth(width)
        self.resize(width, max(int(height), 120))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(6)
        if title:
            head = QLabel(str(title))
            head.setStyleSheet(f"color: {ACCENT}; font-size: 13px; font-weight: 700;")
            head.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            lay.addWidget(head)
        if widget is not None:
            lay.addWidget(widget, 1)
        # 拖拽移动：记录是否正在拖拽及拖拽起始偏移
        self._dragging = False
        self._drag_offset = QPoint()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_offset = (e.globalPosition().toPoint()
                                 - self.frameGeometry().topLeft())
            e.accept()
        else:
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._dragging and (e.buttons() & Qt.MouseButton.LeftButton):
            self.move(e.globalPosition().toPoint() - self._drag_offset)
            e.accept()
        else:
            super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._dragging:
            self._dragging = False
            e.accept()
        else:
            super().mouseReleaseEvent(e)


class QueuePanel(QWidget):
    """排队消息面板：任务运行中发送的多条消息在此排队显示，支持逐条编辑/删除。
    高度自适应：单条单行、多条封顶限高内部滚动；无消息自动隐藏。
    纯黑+淡灰+白+深蓝四色极简风格，无 emoji。"""

    edit_clicked = pyqtSignal(int)     # 编辑第 idx 条排队消息
    delete_clicked = pyqtSignal(int)   # 删除第 idx 条排队消息
    clear_clicked = pyqtSignal()       # 清空全部排队消息

    _ROW_H = 26        # 每条排队消息行高
    _SPACING = 4       # 行间距

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("queuePanel")
        self.setStyleSheet(
            f"QWidget#queuePanel {{ background: {PANEL};"
            f"border: 1px solid {BORDER}; border-radius: 10px; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(8)
        tag = QLabel("排队中")
        tag.setStyleSheet(f"color: {ACCENT_HOVER}; font-size: 12px; font-weight: 700;")
        head.addWidget(tag)
        self._count = QLabel("")
        self._count.setStyleSheet(f"color: {TEXT_DIM}; font-size: 11px;")
        head.addWidget(self._count)
        head.addStretch(1)
        clear_btn = QPushButton("清空")
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.setAutoDefault(False)
        clear_btn.setStyleSheet(_BTN_COMPACT)
        clear_btn.setFixedHeight(22)
        clear_btn.setToolTip("取消全部排队消息")
        clear_btn.clicked.connect(self.clear_all)
        head.addWidget(clear_btn)
        lay.addLayout(head)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            + _scrollbar_css(6, 3, both=False))
        self._list = QWidget()
        self._list.setStyleSheet("background: transparent;")
        self._list_lay = QVBoxLayout(self._list)
        self._list_lay.setContentsMargins(2, 0, 2, 0)
        self._list_lay.setSpacing(4)
        self._scroll.setWidget(self._list)
        lay.addWidget(self._scroll, 1)
        self.update_queue([])

    def update_queue(self, items: list):
        """全量刷新排队消息列表；无消息时自动隐藏面板"""
        while self._list_lay.count():
            item = self._list_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        n = len(items or [])
        for i, q in enumerate(items or []):
            self._list_lay.addWidget(self._item(i, q))
        # 末尾单一弹性：面板高度恰好等于内容时无空隙；略高于内容时消息顶部排列，行间不拉大间距
        self._list_lay.addStretch(1)
        self._rows_h = n * self._ROW_H + max(0, n - 1) * self._SPACING
        # 关键：widgetResizable 会把内部 widget 压缩到视口，必须把 minimum 高度设为内容高度，
        # 视口不足时才会出现滚动条（内容不被裁剪），视口充足时消息顶部紧凑排列。
        self._list.setMinimumHeight(self._rows_h)
        self._count.setText(f"{n} 条" if n else "")
        self.setVisible(bool(n))
        if items:
            # 排队消息从下至上：自动滚动到底部，最新排队消息始终可见
            QTimer.singleShot(0, self._scroll_to_bottom)

    def _scroll_to_bottom(self):
        try:
            bar = self._scroll.verticalScrollBar()
            bar.setValue(bar.maximum())
        except RuntimeError:
            pass

    def _item(self, idx: int, q: dict) -> QWidget:
        text = (q.get("text") or "").strip().replace("\n", " ")
        if len(text) > 40:
            text = text[:40] + "…"
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        rl = QHBoxLayout(row)
        rl.setContentsMargins(4, 0, 4, 0)
        rl.setSpacing(8)
        no = QLabel(f"{idx + 1}.")
        no.setFixedWidth(20)
        no.setStyleSheet(f"color: {TEXT_DIM}; font-size: 12px;")
        rl.addWidget(no)
        lbl = QLabel(f"“{_esc(text)}”")
        lbl.setStyleSheet(f"color: {TEXT}; font-size: 12px;")
        lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        rl.addWidget(lbl, 1)
        edit = QPushButton("编辑")
        edit.setCursor(Qt.CursorShape.PointingHandCursor)
        edit.setAutoDefault(False)
        edit.setStyleSheet(_BTN_COMPACT)
        edit.setFixedHeight(22)
        edit.setFixedWidth(56)
        edit.setToolTip("回填到输入框修改，发送后回到原队列位置")
        edit.clicked.connect(lambda _=False, i=idx: self.edit_clicked.emit(i))
        rl.addWidget(edit)
        dele = QPushButton("删除")
        dele.setCursor(Qt.CursorShape.PointingHandCursor)
        dele.setAutoDefault(False)
        dele.setStyleSheet(_BTN_COMPACT)
        dele.setFixedHeight(22)
        dele.setFixedWidth(56)
        dele.setToolTip("取消该条排队消息")
        dele.clicked.connect(lambda _=False, i=idx: self.delete_clicked.emit(i))
        rl.addWidget(dele)
        row.setFixedHeight(self._ROW_H)
        return row

    def clear_all(self):
        """清空全部排队消息（仅发信号，由 AgentPanel 负责数据与 UI 同步）"""
        self.clear_clicked.emit()


class _SessionStatusDelegate(QStyledItemDelegate):
    """会话下拉项状态绘制：运行中显示转圈动画（深蓝弧线），待确认显示黄色圆点"""

    def __init__(self, panel, parent=None):
        super().__init__(parent)
        self.panel = panel

    def paint(self, painter, option, index):
        sid = index.data(Qt.ItemDataRole.UserRole)
        st = self.panel._sess.get(sid) if isinstance(sid, str) else None
        running = bool(st and st.get("task_active"))
        pending = bool(st and (st.get("pending_confirm") or st.get("pending_ask")))
        name = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        painter.save()
        # 背景：选中 / 悬停
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())
        elif option.state & QStyle.StateFlag.State_MouseOver:
            painter.fillRect(option.rect, QColor(HOVER))
        rect = QRect(option.rect)
        left = rect.left() + 4
        cy = rect.center().y()
        # 待确认黄色圆点（最左侧）
        if pending:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(WARN))
            painter.drawEllipse(QRect(left, cy - 4, 8, 8))
            left += 14
        # 运行中转圈（深蓝弧线，随 spin 角度转动）
        if running:
            cx = left + 6
            pen = QPen(QColor(ACCENT_HOVER), 2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawArc(QRect(cx - 6, cy - 6, 12, 12),
                            self.panel._spin_angle * 16, 120 * 16)
            left += 16
        # 文本
        painter.setFont(option.font)
        painter.setPen(option.palette.text().color())
        painter.drawText(QRect(left, rect.top(), max(0, rect.width() - (left - rect.left())),
                               rect.height()),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, name)
        painter.restore()

    def sizeHint(self, option, index):
        sz = super().sizeHint(option, index)
        return QSize(sz.width() + 44, sz.height())


class _CuteBubble(QLabel):
    """小熊糖果风锐评气泡（趣味联动特例，不受全局简约/禁 emoji 约束）。
    QLabel 的 QSS 背景在 WA_TranslucentBackground 下不会绘制，故全部用 paintEvent 手动绘制：
      - 奶白→奶油轻柔竖向渐变 + 蜜桃粉低饱和描边 + 柔和超大圆角（果冻磨砂玻璃感）
      - 软糯投影（主体下方偏移叠加的多层低透明度圆角）+ 顶部柔和高光带（玻璃反光）
      - 糖果碎小装饰：低饱和马卡龙色小糖果，散落在左上/左下角
   文字恒纯黑；装饰色均由传入色板派生（tint 之外的少量固定点缀色少数可调，无硬编码业务逻辑）。"""

    def __init__(self, text="", radius=26, bg_top="#FFFDF8", bg_bottom="#FFEFDC",
                 border="#FFCBBF", text_color="#000000", tail=15, decor=True):
        super().__init__(text, None)
        self._radius = radius
        self._bg_top = QColor(bg_top)
        self._bg_bottom = QColor(bg_bottom)
        self._border = QColor(border)
        self._text_color = QColor(text_color)
        self._tail = tail
        self._decor = decor
        # 低饱和马卡龙点缀色（糖果碎用，供外部覆盖）
        self._candy = [QColor("#FFC9D4"), QColor("#FFE3B3"),
                       QColor("#CBE7DC"), QColor("#E7CEB8")]
        self._shadow = QColor("#E7CDBE")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAutoFillBackground(False)
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setContentsMargins(34, 18, 34, 18)
        self.setFont(QFont("Microsoft YaHei UI", 13, QFont.Weight.DemiBold))
        # 纯黑文字：QLabel 绘制文本时用 WindowText 色，这里固定成纯黑（恒不受主题影响）
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.WindowText, self._text_color)
        self.setPalette(pal)
        # 星光呼吸特效：仅显示时抖动微闪（星星亮度随相位变化），隐藏即停，杜绝常驻空转
        self._phase = 0.0
        self._blink = QTimer(self)
        self._blink.setInterval(180)
        self._blink.timeout.connect(self._tick)

    def sizeHint(self):
        """在文本基础上多留左右装饰区、耗尾部加高（尾高+内边距）"""
        return QSize(super().sizeHint().width() + 44, super().sizeHint().height() + self._tail + 14)

    @staticmethod
    def _mix(c1: QColor, c2: QColor, t: float) -> QColor:
        """线性插值混色，从基础色板派生装饰色"""
        return QColor(int(round(c1.red() + (c2.red() - c1.red()) * t)),
                      int(round(c1.green() + (c2.green() - c1.green()) * t)),
                      int(round(c1.blue() + (c2.blue() - c1.blue()) * t)))

    def _tick(self):
        """星光呼吸相位推进并触发重绘；仅可见时刷新，隐藏后定时器已停"""
        self._phase += 0.5
        if self.isVisible():
            self.update()

    def showEvent(self, ev):
        super().showEvent(ev)
        self._blink.start()

    def hideEvent(self, ev):
        self._blink.stop()
        super().hideEvent(ev)

    def _draw_sparkle(self, p, cx, cy, size, color):
        """四芒小星星：中心(cx,cy)、半径 size，主轴=size、对角=size*0.38（填充）"""
        import math

        from PyQt6.QtGui import QPainterPath
        path = QPainterPath()
        d = size * 0.38
        for i, ang in enumerate((0, 45, 90, 135, 180, 225, 270, 315)):
            r = size if ang % 90 == 0 else d
            x = cx + r * math.cos(math.radians(ang))
            y = cy + r * math.sin(math.radians(ang))
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        path.closeSubpath()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawPath(path)

    def _draw_heart(self, p, cx, cy, s, color):
        """爱心贴纸：三次贝塞尔勾勒，填充色（无 emoji，纯矢量）"""
        from PyQt6.QtGui import QPainterPath
        path = QPainterPath()
        path.moveTo(cx, cy + s * 0.55)
        path.cubicTo(cx - s * 0.80, cy - s * 0.15, cx - s * 0.35, cy - s * 0.85,
                     cx, cy - s * 0.32)
        path.cubicTo(cx + s * 0.35, cy - s * 0.85, cx + s * 0.80, cy - s * 0.15,
                     cx, cy + s * 0.55)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawPath(path)

    def _draw_lollipop(self, p, cx, cy, s, body_c, stick_c):
        """棒棒糖贴纸：短棍 + 糖果圆（带一小圈高光，低饱和马卡龙）"""
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(stick_c)
        p.drawRoundedRect(QRectF(cx - s * 0.10, cy + s * 0.28, s * 0.20, s * 0.42), 0.9, 0.9)
        p.setBrush(body_c)
        p.drawEllipse(QPointF(cx, cy - s * 0.05), s * 0.42, s * 0.42)
        p.setBrush(self._mix(body_c, QColor("#FFFFFF"), 0.55))
        p.drawEllipse(QPointF(cx - s * 0.14, cy - s * 0.18), s * 0.12, s * 0.12)

    def _draw_flower(self, p, cx, cy, s, petal_c, center_c):
        """小花贴纸：六瓣圆点 + 花心（低饱和马卡龙）"""
        import math
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(petal_c)
        for i in range(6):
            a = math.radians(i * 60)
            p.drawEllipse(QPointF(cx + s * 0.55 * math.cos(a), cy + s * 0.55 * math.sin(a)),
                          s * 0.28, s * 0.28)
        p.setBrush(center_c)
        p.drawEllipse(QPointF(cx, cy), s * 0.20, s * 0.20)

    def _draw_leaf(self, p, cx, cy, s, color):
        """小叶片贴纸：椭圆叶面 + 浅色叶脉（简约矢量）"""
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawEllipse(QPointF(cx, cy), s * 0.46, s * 0.24)
        p.setPen(QPen(self._mix(color, QColor("#FFFFFF"), 0.6), 1))
        p.drawLine(int(cx - s * 0.46), int(cy), int(cx + s * 0.46), int(cy))

    def _silhouette(self) -> QPainterPath:
        """主体圆角矩形 + 底部中括号状小舌尖剪影"""
        from PyQt6.QtGui import QPainterPath
        w, h = self.width(), self.height()
        tail = 0 if not self._decor else self._tail
        body = QRectF(1, 1, w - 2, max(1, h - tail - 2))
        path = QPainterPath()
        path.addRoundedRect(body, self._radius, self._radius)
        if tail:
            tw = tail * 1.8
            tcx = w / 2.0
            tp = QPainterPath()
            tp.moveTo(tcx - tw / 2, body.bottom())
            tp.quadTo(tcx, body.bottom() + tail * 0.35, tcx + tw / 2, body.bottom())
            tp.closeSubpath()
            path = path.united(tp)
        self._body = body
        return path

    def _draw_candies(self, p, body):
        """左上/左下撒几颗低饱和马卡龙糖果碎（豆粒/短横/小三角，均匀分布不穿帮）"""
        import random
        rng = random.Random(20260823)  # 固定种子保证布局稳定
        p.setPen(Qt.PenStyle.NoPen)
        spots = ((18, 24, 2.4), (34, 15, 2.0), (27, 38, 1.8), (46, 26, 1.6),
                 (20, body.height() - 28, 2.2), (38, body.height() - 18, 1.8),
                 (16, body.height() - 44, 1.6), (46, body.height() - 38, 2.0))
        for i, (x, y, r) in enumerate(spots):
            c = QColor(self._candy[i % len(self._candy)])
            c.setAlpha(150)
            p.setBrush(c)
            if i % 2 == 0:  # 圆点
                p.drawEllipse(QPointF(x, y), r, r)
            else:  # 短横糖果
                p.drawRoundedRect(QRectF(x - r, y - r * 0.6, r * 2, r * 1.2), r, r)

    def paintEvent(self, ev):
        import math

        from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        path = self._silhouette()
        body = self._body
        # 底层软糯投影：剪影向下偏移、随步进淡出的多层叠加（柔和细腻阴影，超出主体底边被 clip 也 OK）
        if self._decor:
            steps = 6
            for i in range(steps, 0, -1):
                sh = QColor(self._shadow)
                sh.setAlpha(int(22 * (i / steps)))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(sh)
                p.drawPath(QPainterPath(path).translated(i * 0.35, 2.6))
        # 边缘呼吸光晕（边缘闪烁）：轮廓外圈半透明光边，亮度随相位脉动；
        # 先描后填——内半被奶白渐变覆盖，只露出外圈柔和光晕
        if self._decor:
            glow = QColor(self._mix(self._border, QColor("#FFE0D6"), 0.30))
            glow.setAlpha(int(50 + 55 * (0.5 + 0.5 * math.sin(self._phase * 0.8))))
            p.setPen(QPen(glow, 3.5))
            p.drawPath(QPainterPath(path))
        # 奶白→奶油渐变底（轻柔渐层）
        grad = QLinearGradient(0, body.top(), 0, body.bottom() + self._tail)
        grad.setColorAt(0.0, self._bg_top)
        grad.setColorAt(1.0, self._bg_bottom)
        p.fillPath(path, grad)
        if self._decor:
            # 顶部柔和高光带：果冻磨砂玻璃反光
            hl = QRectF(body.left() + self._radius * 0.7, body.top() + 3,
                        body.width() - self._radius * 1.4, max(2, body.height() * 0.34))
            p.save()
            p.setClipPath(path)
            hg = QLinearGradient(0, hl.top(), 0, hl.bottom())
            hg.setColorAt(0.0, QColor(255, 255, 255, 90))
            hg.setColorAt(1.0, QColor(255, 255, 255, 0))
            p.fillRect(hl, hg)
            p.restore()
            # 糖果碎
            self._draw_candies(p, body)
            # 更多贴纸：爱心（右下）、棒棒糖（左下）、星光微闪（两处，随相位呼吸）
            heart = QColor(self._candy[0]); heart.setAlpha(175)
            self._draw_heart(p, w - 56, body.height() - 40, 9.0, heart)
            self._draw_lollipop(p, 44, body.height() - 40, 10.0,
                                self._candy[1], self._candy[3])
            tw = 0.5 + 0.5 * math.sin(self._phase)
            sp = QColor(self._mix(self._border, QColor("#FFFFFF"), 0.45))
            sp.setAlpha(int(110 + 90 * tw))
            self._draw_sparkle(p, w - 64, 30, 7.0, sp)
            self._draw_sparkle(p, 52, 40, 5.5, sp)
            # 顶边沿闪烁小光点：沿上沿随机固定位置，相位依次点亮（边缘闪烁增强）
            for gx, go in ((int(w * 0.16), 0.0), (int(w * 0.82), 1.3),
                           (int(w * 0.50), 2.6), (24, 0.6), (w - 52, 1.9)):
                ga = int(35 + 120 * (0.5 + 0.5 * math.sin(self._phase + go)))
                gc = QColor(self._mix(self._border, QColor("#FFFFFF"), 0.55))
                gc.setAlpha(ga)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(gc)
                p.drawEllipse(QPointF(gx, 9), 1.7, 1.7)
            # 更多贴纸：小花（右上）、小叶片（右缘中上）
            fl = QColor(self._candy[2]); fl.setAlpha(180)
            self._draw_flower(p, w - 34, 34, 7.0, fl, self._mix(fl, QColor("#FFFFFF"), 0.5))
            lf = QColor(self._candy[3]); lf.setAlpha(190)
            self._draw_leaf(p, w - 40, body.height() - 82, 9.0, lf)
        # 蜜桃粉低饱和描边
        p.setPen(QPen(self._border, 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        p.end()
        super().paintEvent(ev)


class AgentPanel(QDialog):
    confirm_signal = pyqtSignal(str, str, str, str)  # sid, name, args_json, risk
    eval_signal = pyqtSignal(str)          # agnes-2.5-flash 任务难度评估结果（后台线程 → 主线程）
    switch_ready = pyqtSignal(str, object, object, object, object, object, object, object, object, object, object, object)  # 会话切换: sid, segs, ums, rows, workflow, agent, queued, sub_history, sub_synced, last_payload, regen_idx, space（带 sid，防止过期加载覆盖当前视图）
    ask_signal = pyqtSignal(str, str)      # sid, args_json（ask_user 提问）
    mcp_signal = pyqtSignal(str)
    compact_signal = pyqtSignal(int)   # /compact 压缩完成（后台线程 → 主线程，参数=合并条数）
    evt_signal = pyqtSignal(str, str, object)  # 会话事件路由: sid, kind(delta/status/result/reasoning/sub), payload
    sess_name_signal = pyqtSignal(str, str)  # 会话命名: sid, title（后台 AI 起名 / set_session_name 工具 → 主线程改名）
    optimize_signal = pyqtSignal(str, str)   # 提示词优化完成（ok, 结果/错误信息，后台线程 → 主线程）
    fun_signal = pyqtSignal(str)   # 趣味互动锐评完成（主线程展示底部气泡；空串表示失败静默重排）
    _init_done = pyqtSignal()   # 后台初始化完成 → 主线程继续 UI 就绪
    file_diff_signal = pyqtSignal(str, str, str)  # AI 写/改文件差异(工作线程→主线程): path, old, new
    git_probe_signal = pyqtSignal(str, float)     # git 仓库根/时间戳后台探测完成（→ 主线程更新已知值）

    # ---------- 会话感知状态（属性读写当前会话，支撑多对话并发） ----------
    def _cur(self) -> dict:
        """当前会话状态（无则返回空 dict 占位，读默认值）"""
        return self._sess.get(self._session_id) or {}

    @property
    def _task_active(self):
        return bool(self._cur().get("task_active"))

    @_task_active.setter
    def _task_active(self, v):
        st = self._sess.get(self._session_id)
        if st is not None:
            st["task_active"] = bool(v)

    @property
    def _user_stopped(self):
        return bool(self._cur().get("user_stopped"))

    @_user_stopped.setter
    def _user_stopped(self, v):
        st = self._sess.get(self._session_id)
        if st is not None:
            st["user_stopped"] = bool(v)

    @property
    def _end_badge_shown(self):
        return bool(self._cur().get("end_badge_shown"))

    @_end_badge_shown.setter
    def _end_badge_shown(self, v):
        st = self._sess.get(self._session_id)
        if st is not None:
            st["end_badge_shown"] = bool(v)

    @property
    def _think_done(self):
        return bool(self._cur().get("think_done"))

    @_think_done.setter
    def _think_done(self, v):
        st = self._sess.get(self._session_id)
        if st is not None:
            st["think_done"] = bool(v)

    @property
    def _think_start(self):
        return float(self._cur().get("think_start") or 0.0)

    @_think_start.setter
    def _think_start(self, v):
        st = self._sess.get(self._session_id)
        if st is not None:
            st["think_start"] = float(v)

    def __init__(self, parent=None):
        # zhuzhu Copilot 浮层（应用管理 / 防护 / 通用 / 工具）由本面板按需懒创建并托管，
        # 见 _ensure_copilot_panel/_toggle_copilot_panel；不再依赖独立主窗口。
        self._copilot_panel = None
        self._copilot_open = False
        super().__init__(parent)
        self.setWindowTitle("zhuzhu Copilot")
        self.setWindowIcon(QIcon(_app_icon_path()))
        self.setAcceptDrops(True)   # 支持把图片/文件拖入对话框
        # 排队消息编辑状态（_init_sessions 的 _switch_to 早期即可能访问，须最先初始化）
        self._queue_edit_idx = None
        # 底部缩放缓存与按钮图标尺寸：resizeEvent/右键菜单等 Qt 回调早期即可能读取，
        # 必须在 __init__ 初始化，避免首次访问 AttributeError（否则会误触发崩溃弹窗）。
        self._last_scale = 1.0
        self._btn_icon_sz = 16
        # 气泡宽度同步缓存与 resize 防抖定时器：resizeEvent 在 _build_ui 期间即可能触发
        # （控件创建/布局激活会发 resize 事件，早于本方法尾部 7502/7506 行的初始化），
        # 必须先于 _build_ui 就绪，否则早期 resizeEvent 访问 _last_bw 时 AttributeError
        # （与 _last_scale 同模式：exe 打包环境下必现，见 resizeEvent 8955 行崩溃日志）。
        self._last_bw = -1
        self._last_ai_bw = -1     # AI 回合最大宽度缓存（回合铺满内容宽度，与用户气泡分开算）
        self._last_img_w = -1
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(160)
        self._resize_timer.timeout.connect(self._rebuild_bubbles_after_resize)
        self._input_placeholder = "/ for commad @ for agent"
        # 自定义主题（如液态玻璃）：无边框，改用自定义标题栏（Acrylic 透桌面 + 拖动/最小化/最大化/关闭）
        # 内置默认主题：保留系统标题栏，不启用玻璃效果，避免污染默认外观。
        _glass = agent_ui_ux.is_custom_package_active()
        if _glass:
            self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                                | Qt.WindowType.Window)
        else:
            self.setWindowFlags(self.windowFlags()
                                | Qt.WindowType.Window
                                | Qt.WindowType.WindowMinMaxButtonsHint
                                | Qt.WindowType.WindowMaximizeButtonHint
                                | Qt.WindowType.WindowMinimizeButtonHint)
        # 优先应用包主题色板：在控件创建前更新模块级颜色常量，
        # 避免 _build_ui 内 createPreviewWidget 等函数用到旧色板。
        try:
            agent_ui_ux.apply_package_theme()
        except Exception:
            pass
        # 主面板最小尺寸：普通（贴附）模式 1074x692；融入主面板 dock 模式左右栏占位
        # 后需放宽最小宽，保证聊天/输入内容区不被挤压（_apply_panel_mode 动态切换）
        self.setMinimumSize(1074, 692)
        self.resize(1074, 692)
        self.setFont(QFont("Microsoft YaHei UI", 10))
        _dialog_bg = ("transparent" if _glass else
                      f"qlineargradient(x1:0, y1:0, x2:0, y2:1,"
                      f"stop:0 {BG}, stop:1 {BG_BOTTOM})")
        self.setStyleSheet(
            f"QDialog {{ background: {_dialog_bg}; border-radius: 18px; }}"
            + _QCOMBO
            # 滚动条主题自适应（轨道用背景色、滑块用边框色），避免浅色模式残留纯黑轨道
            + _scrollbar_css(8, 4, both=True))
        # 全局悬浮提示：液态玻璃下用浅色底 + 深字，避免系统默认纯黑底看不清。
        # palette 必须无条件设置（Windows 原生 QToolTip 用 palette 渲染、QSS 常被忽略），
        # 不能因为 app QSS 已有 QToolTip 规则而跳过——否则启动后（未切主题）提示框仍纯黑。
        app = QApplication.instance()
        if app is not None:
            if agent_ui_ux.is_custom_package_active():
                _tip_bg = "#F7F9FC"
                _tip_bd = "rgba(255,255,255,220)"
                try:
                    from PyQt6.QtGui import QColor, QPalette
                    from PyQt6.QtWidgets import QToolTip
                    _tp = QToolTip.palette()
                    _tp.setColor(QPalette.ColorRole.ToolTipBase, QColor(247, 249, 252))
                    _tp.setColor(QPalette.ColorRole.ToolTipText, QColor(11, 15, 20))
                    _tp.setColor(QPalette.ColorRole.Window, QColor(247, 249, 252))
                    _tp.setColor(QPalette.ColorRole.Text, QColor(11, 15, 20))
                    QToolTip.setPalette(_tp)
                except Exception:
                    pass
            else:
                _tip_bg = PANEL
                _tip_bd = ACCENT
            if "QToolTip {" not in (app.styleSheet() or ""):
                app.setStyleSheet(
                    (app.styleSheet() or "") +
                    f"QToolTip {{ background: {_tip_bg}; color: {TEXT};"
                    f"border: 1px solid {_tip_bd}; border-radius: 6px;"
                    "padding: 6px 10px; font-size: 12px; }}")

        self._settings = app_identity.qsettings()
        self._engine: agent_engine.AgentEngine = None
        self._confirm_evt = threading.Event()
        self._confirm_result = False
        self._ask_evt = threading.Event()
        self._ask_result = ""
        self._mcp = McpManager()
        # 用户设置：多模型/工作力度/纯文本模型自动识别、记忆开关
        _s = agent_skills.load_settings()
        self._model_cfg = agent_llm.load_model_config()   # 规范化多模型/力度路由配置
        self._effort = self._model_cfg.get("effort", "medium")
        self._auto_effort = bool(self._model_cfg.get("auto_effort", True))
        self._model_override = None      # 输入框右侧手动指定的模型（None=按力度路由）
        self._model_override_provider = ""   # 手动指定模型所属服务商名（跨服务商切换连接参数）
        # 记住上次手动选择的模型，重启自动恢复
        _last_model = str(self._settings.value("agent_last_model", "")).strip()
        _last_provider = str(self._settings.value("agent_last_provider", "")).strip()
        if _last_model:
            self._model_override = _last_model
            # 优先按上次记住的服务商恢复：同名模型可存在于多个服务商（如 DeepSeek 与
            # 火山都有 deepseek-v4-flash），只按模型名反查会落到 providers 顺序的第一个
            # 而非用户实际选的；服务商仍在且包含该模型才采用，否则回退反查
            _ok = bool(_last_provider) and any(
                p.get("name") == _last_provider
                and _last_model in (p.get("models") or [])
                for p in (self._model_cfg.get("providers") or []))
            self._model_override_provider = _last_provider if _ok \
                else self._provider_for_model(_last_model)
        self._refresh_text_only()
        self._memory_enabled = bool(_s.get("memory_enabled", True))
        # 执行模式（ask/edit/yolo）在设置页调整，此处仅从 QSettings 读取
        self._mode = str(self._settings.value("agent_mode", "ask"))

        # 拖入的附件：图片（data URL，发给模型）与非图片文件（路径文本）
        self._pending_images: list = []
        self._pending_files: list = []

        # 当前 AI 气泡段落序列（交织渲染：思考 → 操作 → 正文 → 操作 → 正文…）
        self._ai_bubble = None
        self._segments = []   # [{"type": "think|op|result|text|mark", "html"/"raw": ...}]
        # 已归档的历史段（与 _segments 拼接渲染）。必须在 __init__ 初始化：
        # resizeEvent 回调可能在 _bind_sess 前触发，缺此属性会 AttributeError。
        self._history_segments = []
        self._seg_cache = {}  # 段内容签名 -> 渲染HTML：流式刷新只重算增长的段，跨会话复用

        # 任务进行中的转圈动画行（显示在消息流顶部）
        self._spinner_row = None
        self._spinner = None
        self._spinner_lbl = None
        self._reasoning_lbl = None

        # /compact 压缩中的打字指示器行（与任务转圈独立，互不干扰）
        self._compact_row = None

        # 会话运行时状态存储：属性 _task_active/_user_stopped/_think_* 读写当前会话，
        # 必须先于其初始化（多对话并发：每会话独立引擎/段缓冲/排队消息）
        self._session_id = ""          # 当前会话 id
        self._session_name = "新对话"  # 当前会话名称
        self._name_ai_started = None   # 已发起过 AI 起名的会话 id（每会话只试一次）
        # st = {engine, loaded, segments, history_segments, rows, user_msgs,
        #       sub_segs, user_stopped, end_badge_shown, think_done, think_start,
        #       task_active, queued}
        self._sess: dict = {}
        self._user_msgs: list = []     # 当前会话的用户消息文本（用于切换时重绘）
        self._rows: list = []          # 用户消息与 AI 回复的交错顺序行（[{"type": "user"/"ai", ...}]）

        # 任务结束徽章状态（按会话存于 _sess，属性读写当前会话）
        self._user_stopped = False     # 用户手动点击停止
        self._end_badge_shown = False  # 防止重复显示结束徽章

        # 流式思考过程状态（按会话存于 _sess）
        self._think_start = 0.0        # 本轮思考开始时间（time.time）
        self._think_done = False       # 思考是否已完成（已输出"已思考 x 秒"）

        # 卡死兜底 hooks：最近一次有输出/状态的时间戳（供 UI 反馈，不再自动强停）
        self._last_activity = 0.0      # 最近一次有输出/状态的时间戳
        self._task_active = False      # 是否有任务在执行（结束收尾的可靠依据）
        self._turn_started_at = 0.0    # 回合计时起点（发送/直呼那一刻，供耗时徽章与时间行）
        # @子Agent 直接调用状态（独立于主引擎）：_sub_stop=None 表示无子 Agent 任务
        self._sub_stop = None          # threading.Event：运行中创建，结束后置 None
        self._subagent_mode = False    # 当前任务是否为 @子Agent 直接调用（结束徽章判定）
        self._subagent_ok = True       # 子 Agent 是否正常完成（未用户停止）
        self._subagent_result = ""     # 子 Agent 最终总结文本
        self._task_auto_ids = set()   # 本次任务内"全部允许"的会话集合（任务结束自动失效）
        self._pending_rebuild = {}  # sid -> 待应用的工作流切换目标（None/""/工作流名），任务结束后再重建引擎
        self._uiux_rebuild_pending = False  # UI/UX 包切换待应用（任务结束后重建面板）
        # 自定义按钮注册表（与 UI/UX 解耦）：btn_id -> {text, tooltip, callback, order, _btn}
        self._btn_registry: dict = {}
        self._custom_btn_bar = None      # 顶部按钮栏 QWidget（任意 UI/UX 下渲染注册按钮）
        self._custom_btn_lay = None      # 按钮栏布局
        self._panel_minimized = False  # 面板最小化状态：最小化时同步收起 4 个子面板
        # 液态玻璃自定义标题栏的手动最大化状态（无边框窗口用 setGeometry 全屏，
        # 不依赖不可靠的 isMaximized()，一次点击即生效；还原几何备份在此）
        self._glass_maximized = False
        self._glass_normal_geo = None
        self._eval_pending = None      # 任务难度评估待启动参数 (sid, ai_text, send_images, skill_names)
        self._eval_pending_at = 0.0    # _eval_pending 置位时间戳（卡死看门狗：评估超时未完成则复位按钮）
        # 趣味互动：全屏截屏 → 生成俏皮锐评 → 底部气泡，5 秒后消失；不计入上下文/本地记忆
        self._fun_bubble = None          # 底部可爱风气泡（懒创建）
        self._fun_last_input_at = 0.0    # 用户最近一次输入的时间戳（输入即重置空闲判定）
        self._fun_timer = QTimer(self)
        self._fun_timer.setSingleShot(True)
        self._fun_timer.timeout.connect(self._fun_tick)
        self.fun_signal.connect(self._fun_show)
        if self._fun_switch_enabled():
            self._fun_schedule()
        # 管理员权限下的原生拖放（UIPI 绕行，仅提权时启用）
        self._admin_dnd = False
        self._admin_drop_filter = None
        self._scroll_pending = False   # 滚动调度去重标志
        self._bubble_widgets: list = []  # 所有气泡 QLabel（窗口缩放时同步宽度）
        self._bubble_segs: dict = {}     # 气泡 id → 其 AI 段列表（思考折叠/展开局部重渲染用）
        self._msg_nav: list = []         # 对话定位器：最近 10 条用户消息 [{"btn","bubble","text"}]
        self._html_dirty = False         # 流式刷新节流标志（60ms 批量 setText）
        self._optimizing = False         # 提示词优化进行中（防重复点击）

        # 发送/停止按钮转圈动画
        self._action_anim_angle = 0

        self._build_ui()
        # AI 写/改文件 → 预览面板差异高亮观察者（幂等注册，常驻实例）
        try:
            from zhuzhu_Copilot.core import agent_tools as _at
            _at.register_file_observer(self._on_ai_file_changed)
            self.file_diff_signal.connect(self._on_file_diff,
                                          Qt.ConnectionType.QueuedConnection)
        except Exception:
            pass
        # 用户输入即时重置趣味互动空闲判定（输入框在 build_ui 内创建，故在此连接）
        try:
            self.input.textChanged.connect(self._on_fun_input)
        except Exception:
            pass
        # 液态玻璃包激活后补套 Acrylic 毛玻璃：build_ui 内部不调用 apply_acrylic
        # （DWM 矩形背景与 Qt paintEvent 圆角冲突），故在此手动补套，确保启动即生效。
        try:
            if agent_ui_ux.is_custom_package_active():
                agent_ui_ux.apply_acrylic(self)
        except Exception:
            pass
        # _build_ui 同步构建大量控件（尤其液态玻璃包），期间主线程事件循环被阻塞。
        # 立即 flush 一次事件让窗口先显示出来，后续初始化（信号连接/会话加载）在
        # 已可见的面板上进行，感知上"打开更顺畅"。
        try:
            from PyQt6.QtCore import QCoreApplication
            QCoreApplication.processEvents()
        except Exception:
            pass
        # 启动性能优化：把会阻塞首帧的初始化（会话文档扫描/模型下拉/工作目录/信号连接）
        # 推迟到调用方 show() + 事件循环启动、窗口首帧绘制之后再执行（singleShot(0) 在主线程）。
        # 这样显卡先出画面、再加载会话内容，感知上启动明显更顺畅；同时移除了原先仅读 2 个
        # QSettings 默认值、信号无人接收的无效后台线程（读进程里 QSettings 本就轻量，无需跨线程）。
        def _finish_startup_init():
            self._connect_signals()
            self._sync_model_combo()   # 填充输入框右侧模型下拉（设置里的模型列表）
            self._init_sessions()      # 会话列表 + 内容扫描 + 恢复上次会话
            self._update_wf_label()
            try:
                self._restore_workdir()   # 恢复当前会话的独立工作目录（含全局默认回退）
            except Exception:
                pass
        QTimer.singleShot(0, _finish_startup_init)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_meta)
        self._timer.start(400)

        # git 分支面板周期刷新：工作目录可能延迟设置，首次未识别到仓库时周期性重试；
        # 用 .git 文件 mtime 判断是否有新提交/切分支，避免每 5s 起 git 子进程造成卡顿
        self._known_git_root = None
        self._known_git_mtime = 0.0
        self._rendered_workdir = None   # 最近一次真正渲染面板所依据的工作目录（周期自愈用）
        self._git_timer = QTimer(self)
        self._git_timer.timeout.connect(self._periodic_git_refresh)
        self._git_timer.setInterval(5000)
        self._git_timer.start()

        # 会话自动备份：任务运行中周期落盘当前会话（防崩溃/非正常退出丢失未完成任务）
        self._autosave_timer = QTimer(self)
        self._autosave_timer.timeout.connect(self._autosave_flush)
        self._autosave_timer.setInterval(4000)
        self._autosave_timer.start()
        # 应用退出（含主窗口 X 关闭——此时不会触发本面板 closeEvent，closeEvent 只在
        # 直接关闭本面板时调用）通过 aboutToQuit 全量持久化所有会话，否则「任务未完成」
        # 的对话因从未走到任务结束分支的 _persist_current 而根本不落盘，重启后记录丢失。
        _app = QApplication.instance()
        if _app is not None:
            def _quit_persist(_p=self):
                try:
                    # 先把当前会话实时内存态（流式回复/排队消息/最新气泡）回写会话状态，
                    # 再全量落盘：主窗口 X 关闭不触发面板 closeEvent，缺此步时
                    # 任务未完成会话的最后几轮内容可能未被持久化
                    _p._commit_sess()
                    _p._persist_all_on_close()
                except Exception:
                    pass
            try:
                _app.aboutToQuit.connect(_quit_persist)
            except Exception:
                pass

        # 面板自愈守卫生效：系统/焦点切换可能把 owned Tool 面板隐藏，周期性把
        # 「应显示却意外隐藏」的面板重新显示并抬升，避免点击对话区后面板消失
        self._guard_timer = QTimer(self)
        self._guard_timer.timeout.connect(self._guard_panels)
        self._guard_timer.setInterval(800)
        self._guard_timer.start()

        # 功能面板热更新：周期性检测扩展面板源文件（panel.py）是否被修改，
        # 被修改的面板原位重建（保留几何/拖拽位置），实现"改动即热更"无需重启。
        self._ext_hot_timer = QTimer(self)
        self._ext_hot_timer.timeout.connect(self._hot_reload_ext_panels)
        self._ext_hot_timer.setInterval(2000)
        self._ext_hot_timer.start()

        # 工作目录/仓库刷新防抖：多次触发（设置保存路径会连续调用 git/文件树刷新，
        # 每次起 git 子进程+扫目录都会卡顿）合并为单次单帧刷新，让出 UI 事件循环。
        self._git_refresh_timer = QTimer(self)
        self._git_refresh_timer.setSingleShot(True)
        self._git_refresh_timer.setInterval(0)
        self._git_refresh_timer.timeout.connect(self._refresh_git_and_worktree)

        # 会话下拉转圈动画：全局角度递进，有运行中会话时刷新下拉视图
        self._spin_angle = 0
        self._combo_anim = QTimer(self)
        self._combo_anim.timeout.connect(self._tick_combo_spin)
        self._combo_anim.setInterval(120)
        self._combo_anim.start()

        self._action_anim = QTimer(self)
        self._action_anim.timeout.connect(self._tick_action_anim)
        self._action_anim.setInterval(80)
        # resize 防抖：窗口尺寸变化停止后统一重渲染气泡（合并连续 resize，避免卡顿）
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(160)
        self._resize_timer.timeout.connect(self._rebuild_bubbles_after_resize)
        self._last_bw = self._last_ai_bw = -1   # 上次已同步的气泡宽度缓存
        self._last_img_w = -1                   # 上次重渲染时的截图宽度缓存

        threading.Thread(target=self._init_mcp, daemon=True).start()

    # ---------- UI ----------
    def _build_ui(self):
        """构建面板 UI：优先加载活跃的自定义 UI/UX 包（热插拔），
        加载失败或未启用自定义时回退到内置默认 UI。
        若包携带自定义主题色板，先重置全局主题再应用包覆盖（深色/浅色均支持）。"""
        # 重建前先丢弃统计浮层：其尺寸/配色随新主题与新顶栏重算，
        # 避免旧浮层残留（含解绑全局失焦守卫）。
        self._reset_token_pop()
        try:
            agent_ui_ux.apply_package_theme()
        except Exception:
            pass
        active = agent_ui_ux.get_active_package()
        if active and active != agent_ui_ux.DEFAULT_PACKAGE:
            try:
                if agent_ui_ux.load_and_build_ui(self):
                    # 应用包级样式表 panel.qss（深度自定义组件样式），无则跳过
                    try:
                        agent_ui_ux.apply_panel_qss(self)
                    except Exception:
                        pass
                    self._ensure_custom_btn_bar()   # 注册的自定义按钮与 UI/UX 解耦，恒渲染
                    self._ensure_ext_panels()       # 扩展面板（插件/工作流/代码注册）恒挂载
                    return
            except Exception:
                pass
            # 自定义包加载失败：已自动回退默认界面，提示用户真实原因
            try:
                err = agent_ui_ux.get_last_build_error()
                tip = f"原因：{err}" if err else "请编辑该包代码后重试"
                try:
                    self._toast("UI/UX 加载失败",
                                f"自定义包「{active}」无法加载，已回退默认界面。\n{tip}",
                                warn=True)
                except Exception:
                    pass
            except Exception:
                pass
        self.build_default_ui()
        self._ensure_custom_btn_bar()   # 注册的自定义按钮与 UI/UX 解耦，恒渲染
        self._ensure_ext_panels()       # 扩展面板（插件/工作流/代码注册）恒挂载

    def build_default_ui(self):
        """内置默认面板构建（public，供自定义 UI/UX 包调用）"""
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)
        self._root_lay = root   # 保留根布局引用：最大化时左移边距为左侧三面板腾出空间

        # 顶栏：标题 + Agent/会话/模式 + MCP + tokens + 清空（随窗口宽度自适应，宽窗完整/窄窗紧凑）
        top = QHBoxLayout()
        top.setSpacing(8)
        self.title = QLabel("AI AGENT")
        self.title.setStyleSheet(f"color: {ACCENT}; font-size: 16px; font-weight: 800;")
        top.addWidget(self.title)

        # 会话选择：切换对话（上下文隔离）+ 新对话按钮
        # 用 _ArrowComboBox：样式表一旦接管 ::drop-down，原生下拉箭头就不画了，
        # 会话下拉必须与模型下拉一样自绘箭头，否则箭头整块消失（用户反馈）。
        self.session_combo = _ArrowComboBox()
        self.session_combo.setMinimumWidth(150)
        self.session_combo.setMaximumWidth(280)   # 上限给足：会话名（AI 起名 ≤12 字）收起态不省略号截断
        # 自定义 delegate：运行中会话显示转圈、待确认显示黄色圆点
        self.session_combo.setItemDelegate(_SessionStatusDelegate(self))
        # 右键菜单守卫：QComboBox popup 的 view 对右键 press/release 也会把点击项同步为
        # currentIndex（触发 _on_session_selected 切会话），右击删除前应只弹菜单不切换。
        # 拦截右键鼠标事件（不吞 context menu 事件），防止右键一下就切进被点的对话。
        self._session_ctx_guard = QObject(self)
        self.session_combo.view().viewport().installEventFilter(self._session_ctx_guard)
        self.session_combo.view().installEventFilter(self._session_ctx_guard)
        self._session_ctx_guard.eventFilter = self._session_right_click_filter
        if agent_ui_ux.is_custom_package_active():
            # 液态玻璃：透明玻璃底 + 白受光边 + 玻璃渐变/软件毛玻璃弹出视图（同模型下拉）；
            # with_arrow=False —— 箭头由 _ArrowComboBox 自绘，避免出现双箭头
            self.session_combo.setStyleSheet(
                _glass_combo_qss(TEXT, TEXT_DIM, radius="8px", with_arrow=False)
                + _glass_combo_view_qss(TEXT))
            agent_ui_ux.glassify_combo_view(self.session_combo, radius=12)
        else:
            self.session_combo.setStyleSheet(_QCOMBO)
        self.session_combo.currentIndexChanged.connect(self._on_session_selected)
        # 下拉列表右键菜单：删除对话
        self.session_combo.view().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.session_combo.view().customContextMenuRequested.connect(self._on_session_context_menu)
        top.addWidget(self.session_combo)

        self.new_btn = QPushButton(_line_icon("new", color=TEXT_DIM), "")
        self.new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.new_btn.setAutoDefault(False)
        self.new_btn.setFixedSize(34, 34)
        self.new_btn.setIconSize(QSize(18, 18))
        self.new_btn.setToolTip("新对话")
        self.new_btn.setStyleSheet(_BTN_ICON)
        self.new_btn.clicked.connect(self._new_session)
        top.addWidget(self.new_btn)

        self.settings_btn = QPushButton(_svg_icon(_GEAR_SVG, 20, TEXT_DIM), "")
        self.settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.settings_btn.setAutoDefault(False)
        self.settings_btn.setFixedSize(34, 34)
        self.settings_btn.setIconSize(QSize(18, 18))
        self.settings_btn.setToolTip("AI 设置：执行模式 / 工作目录 / 工作力度 / 规则 / 提示词 / 模型接入")
        self.settings_btn.setStyleSheet(_BTN_ICON)
        self.settings_btn.clicked.connect(self._open_settings)
        top.addWidget(self.settings_btn)

        top.addStretch(1)

        # tokens / 工作流 / 模型统一并入「上下文统计」浮层（顶栏只留一个图表入口，
        # 减少常显按钮）。token_label / wf_label 仍保留为属性：承载既有 20+ 处更新逻辑，
        # 同时作为浮层展示工作流/模型的数据源（见 _token_stats）。
        self.token_label = QLabel("0 tk")
        self.token_label.setStyleSheet(f"color: {TEXT_DIM}; font-size: {FONT_SMALL}px;")
        self.token_label.setToolTip("已用 tokens")
        self.wf_label = QLabel("")
        self.wf_label.setStyleSheet(f"color: {TEXT_DIM}; font-size: {FONT_SMALL}px;")
        self.wf_label.setToolTip("当前对话使用的工作流")
        # zhuzhu Copilot 入口：点击在其下方丝滑弹出紧凑浮层（应用迁移/卸载 + 防护 +
        # 通用设置 + 工具），浮层由本面板懒创建并托管，见 _ensure_copilot_panel
        self.copilot_btn = QPushButton(_line_icon("apps", 16, TEXT_DIM), "")
        self.copilot_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copilot_btn.setAutoDefault(False)
        self.copilot_btn.setFixedSize(34, 34)
        self.copilot_btn.setIconSize(QSize(18, 18))
        self.copilot_btn.setToolTip("zhuzhu Copilot：应用迁移与卸载、安全防护、通用设置与工具")
        self.copilot_btn.setStyleSheet(_BTN_GHOST)
        self.copilot_btn.clicked.connect(self.toggle_copilot_panel)
        top.addWidget(self.copilot_btn)

        # token / 上下文统计入口：点击在其下方丝滑弹统计浮层（见 _TokenStatsPopover）
        self.token_btn = QPushButton(_line_icon("chart", 16, TEXT_DIM), "")
        self.token_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.token_btn.setAutoDefault(False)
        self.token_btn.setFixedSize(34, 34)
        self.token_btn.setIconSize(QSize(18, 18))
        self.token_btn.setToolTip("Token / 上下文统计：占用、阈值、工作流、模型与累计消耗")
        self.token_btn.setStyleSheet(_BTN_GHOST)
        self.token_btn.clicked.connect(self.toggle_token_stats)
        top.addWidget(self.token_btn)

        clear_btn = QPushButton(_line_icon("trash", color=TEXT_DIM), "")
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.setAutoDefault(False)
        clear_btn.setFixedSize(34, 34)
        clear_btn.setIconSize(QSize(18, 18))
        clear_btn.setToolTip("清空上下文并永久删除该对话（二次弹窗确认，不可恢复）")
        clear_btn.setAutoDefault(False)
        clear_btn.setStyleSheet(_BTN_GHOST)
        clear_btn.clicked.connect(self._clear_chat)
        top.addWidget(clear_btn)

        root.addLayout(top)

        # 主体：聊天区占满剩余宽度（TODOS 已移出为独立无边框窗口 TodosWindow，
        # 停靠 AI 主窗口左侧、顶部齐平，不占主面板布局；可融入主面板 dock 侧栏）。
        body = QVBoxLayout()
        body.setSpacing(10)
        # 融入主面板的 dock 侧栏：左栏（工作树/Git/任务清单）与右栏（代码预览等），
        # 默认隐藏（宽度 0 不占空间），面板融入后显示；见 _sync_dock_layout
        self._dock_left = self._make_dock_column("left")
        self._dock_right = self._make_dock_column("right")
        self.todos_win = TodosWindow(self)
        self.todos_win.clear_requested.connect(self._on_todos_clear)
        self.git_win = GitLogWindow(self)
        self.wt_win = WorktreeWindow(self)
        self.wt_win.file_open_requested.connect(self._open_code_preview)
        self.code_win = CodePreviewWindow(self)
        self._setup_builtin_browser()
        right = QVBoxLayout()
        right.setSpacing(10)

        # 聊天区（气泡）与欢迎页（无对话时居中介绍 AI 功能）用堆叠切换
        self.msg_area = QScrollArea()
        self.msg_area.setWidgetResizable(True)
        # QScrollArea 默认是 QFrame(StyledPanel) 且 viewport 自绘背景，会把聊天区框成
        # 一片"长方形"、破坏与顶部/底部输入区的无缝过渡 → 强制 NoFrame + viewport 透明
        self.msg_area.setFrameShape(QFrame.Shape.NoFrame)
        self.msg_area.viewport().setAutoFillBackground(False)
        # 滚动条主题自适应（轨道用背景色、滑块用边框色）：垂直（右侧）+ 水平（底部）
        self.msg_area.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            + _scrollbar_css(8, 4, both=True))
        container = QWidget()
        container.setStyleSheet("background: transparent;")
        self.msg_lay = QVBoxLayout(container)
        self.msg_lay.setContentsMargins(6, 6, 6, 6)
        self.msg_lay.setSpacing(10)
        self.msg_lay.addStretch(1)   # 末尾弹性空间，消息自顶向下堆叠
        self.msg_area.setWidget(container)

        self._welcome_page = self._build_welcome()
        self.msg_stack = QStackedWidget()
        self.msg_stack.addWidget(self.msg_area)
        self.msg_stack.addWidget(self._welcome_page)
        # 聊天区 + 右侧对话定位器（半透明圆形列，最近 10 条用户消息；无定位点自动隐藏）
        chat_row = QHBoxLayout()
        chat_row.setSpacing(4)
        chat_row.addWidget(self.msg_stack, 1)
        self._nav_bar = self._build_msg_nav_bar()
        chat_row.addWidget(self._nav_bar, 0, Qt.AlignmentFlag.AlignTop)
        right.addLayout(chat_row, 1)   # 聊天区：在底部区域内弹性占满

        # 命令提示条：输入 / 时展示可用 skill/命令（高度随显示条数自适应）
        self.cmd_list = QListWidget()
        self.cmd_list.setStyleSheet(self._cmd_list_qss())
        self.cmd_list.hide()
        self.cmd_list.itemClicked.connect(self._on_cmd_selected)
        right.addWidget(self.cmd_list)

        # 命令候选缓存 + 防抖： "@"/"/" 提示按暂停后刷新，避免每键重扫磁盘/重算列表
        self._wf_cache = None     # list_workflows() 缓存（候选框主开销：每键重读各工作流 meta）
        self._agent_cache = None  # 自定义 Agent 列表缓存（@agent 会话级切换候选）
        self._cmd_cache = None    # "/" 全部命令串缓存（技能集合/工作流变化后失效）
        self._skill_map = None    # 当前工作流技能 {name.lower(): skill} 缓存
        self._plugin_map = None   # 可手动调用的插件 {name.lower(): plugin} 缓存（/插件名）
        self._cmd_debounce = QTimer(self)
        self._cmd_debounce.setSingleShot(True)
        self._cmd_debounce.setInterval(150)
        self._cmd_debounce.timeout.connect(self._update_cmd_suggestions)

        # 附件缩略图条：拖入的图片/文件在此预览（隐藏时无高度；子项自动换行不挤压）
        self._attach_bar = QWidget()
        self._attach_bar.setStyleSheet("background: transparent;")
        self._attach_lay = FlowLayout(self._attach_bar, margin=0, spacing=8)
        self._attach_bar.setVisible(False)
        right.addWidget(self._attach_bar)

        # 排队消息面板：任务运行中发送的多条消息在此排队显示，可逐条编辑/删除。
        # 高度自适应（单条单行、多条限高滚动），无消息自动隐藏。
        # 注：添加位置在底部输入行之前（见 _build_ui 末尾），此处只做连接。
        self.queue_panel = QueuePanel(self)
        self.queue_panel.hide()
        self.queue_panel.edit_clicked.connect(self._on_queue_edit)
        self.queue_panel.delete_clicked.connect(self._on_queue_delete)
        self.queue_panel.clear_clicked.connect(self._on_queue_clear)

        # 输入栏
        bottom = QHBoxLayout()
        bottom.setSpacing(6)
        self.input = _DropTextEdit()
        self.input.setPlaceholderText("/ for commad @ for agent")
        self.input.setMinimumHeight(32)
        self.input.setMaximumHeight(110)
        self.input.setStyleSheet(
            f"QPlainTextEdit {{ background: {PANEL}; color: {TEXT}; border: 1px solid {BORDER};"
            "border-radius: 10px; padding: 5px 10px; font-size: 14px; }}"
            f"QPlainTextEdit:focus {{ border: 1px solid {ACCENT}; }}")
        self.input.submit.connect(self._send)   # Enter 发送（Shift+Enter 换行）
        self.input.textChanged.connect(self._cmd_debounce.start)   # 防抖：暂停后再刷候选
        self.input.textChanged.connect(self._sync_action_style)
        self.input.textChanged.connect(self._on_input_cancel_edit)
        self.input.installEventFilter(self)   # 拦截 Ctrl+V：剪贴板图片转附件
        self.input.fileDropped.connect(self._on_input_files_dropped)   # 文件拖入 → 附件
        bottom.addWidget(self.input, 1)

        # TTS 语音合成快捷入口


        # 输入框右侧「+」上传按钮：文件选择器多选（也支持拖拽 / Ctrl+V 粘贴）
        self.attach_btn = QPushButton(_line_icon("plus", 20, ACCENT), "")
        self.attach_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.attach_btn.setAutoDefault(False)
        self.attach_btn.setFixedSize(_ROUND_BTN_D, _ROUND_BTN_D)
        self.attach_btn.setIconSize(QSize(20, 20))
        self.attach_btn.setToolTip("上传文件/图片给 AI（也可拖拽文件到输入框或 Ctrl+V 粘贴截图）")
        self.attach_btn.setStyleSheet(_round_icon_btn_qss(_ROUND_BTN_D))
        self.attach_btn.clicked.connect(self._pick_attachments)
        bottom.addWidget(self.attach_btn)

        # 输入框右侧：提示词优化（基于上下文优化当前输入，异步后台调用 LLM）
        self.optimize_btn = QPushButton(_line_icon("magic", 20, ACCENT), "")
        self.optimize_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.optimize_btn.setAutoDefault(False)
        self.optimize_btn.setFixedSize(_ROUND_BTN_D, _ROUND_BTN_D)
        self.optimize_btn.setIconSize(QSize(20, 20))
        self.optimize_btn.setToolTip("优化提示词：结合当前对话上下文，润色输入框中的提示词")
        # 与「+」上传按钮同款皮肤（用户要求）：实心面板底 + 描边正圆，hover 描边转强调色
        self.optimize_btn.setStyleSheet(_round_icon_btn_qss(_ROUND_BTN_D))
        self.optimize_btn.clicked.connect(self._on_optimize_clicked)
        bottom.addWidget(self.optimize_btn)

        # 输入框右侧：手动切换本次使用的模型（选「自动」则按工作力度路由）
        self.model_combo = _ArrowComboBox()
        self.model_combo.setMinimumWidth(150)
        self.model_combo.setMaximumWidth(230)
        if agent_ui_ux.is_custom_package_active():
            # 液态玻璃：透明玻璃底 + 白受光边 + 玻璃渐变弹出视图（低磨砂高液态），
            # 箭头由 _ArrowComboBox 自绘（with_arrow=False 避免双箭头）；并加圆角蒙版
            self.model_combo.setStyleSheet(
                _glass_combo_qss(TEXT, TEXT_DIM, radius="8px", with_arrow=False)
                + _glass_combo_view_qss(TEXT))
            agent_ui_ux.glassify_combo_view(self.model_combo, radius=12)
        else:
            self.model_combo.setStyleSheet(_QCOMBO)
        self.model_combo.setToolTip("手动切换本次使用的模型；「自动选择」= 按工作力度路由")
        self.model_combo.currentIndexChanged.connect(self._on_model_combo)
        self.model_combo.setAcceptDrops(False)   # 文件拖放由面板统一接收
        bottom.addWidget(self.model_combo)

        # 发送/停止融合按钮：空闲=发送（深蓝），运行中=转圈可点击停止，停止中=红底转圈
        self.action_btn = QPushButton(_line_icon("send", 16, "#FFFFFF"), "")
        self.action_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.action_btn.setAutoDefault(False)
        self.action_btn.setFixedSize(34, 34)
        self.action_btn.setIconSize(QSize(16, 16))
        self.action_btn.setStyleSheet(_BTN_PRIMARY)
        self.action_btn.setToolTip("发送")
        self.action_btn.clicked.connect(self._on_action_clicked)
        bottom.addWidget(self.action_btn)
        # 排队面板：内容自适应高度（消息行数决定），紧贴输入行上方，无底部留白
        right.addWidget(self.queue_panel)
        # 工作目录提示条：当前对话未设置专属工作目录时显示「去设置」按钮，点击跳转
        # 对话流设置页对应行并闪烁边框提醒位置（紧贴输入行上方，见 _update_wd_hint）
        self.wd_hint = QWidget()
        self.wd_hint.setVisible(False)
        wd_hint_lay = QHBoxLayout(self.wd_hint)
        wd_hint_lay.setContentsMargins(2, 2, 2, 2)
        wd_hint_lay.setSpacing(8)
        self.wd_hint_bar = QFrame()
        self.wd_hint_bar.setObjectName("wdHintBar")
        self.wd_hint_bar.setStyleSheet(
            f"QFrame#wdHintBar {{ background: rgba(0,0,0,0); border: 1px solid {BORDER};"
            f"border-radius: {RADIUS_SM}px; }}")
        _wh_lay = QHBoxLayout(self.wd_hint_bar)
        _wh_lay.setContentsMargins(10, 5, 6, 5)
        _wh_lay.setSpacing(8)
        _wh_txt = QLabel("当前对话流未设置专属工作目录")
        _wh_txt.setStyleSheet(f"color: {TEXT_DIM}; font-size: 12px;")
        _wh_lay.addWidget(_wh_txt)
        _wh_lay.addStretch(1)
        _wh_btn = QPushButton(_line_icon("folder", 14, ACCENT), "去设置")
        _wh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        _wh_btn.setAutoDefault(False)
        _wh_btn.setFixedHeight(26)
        _wh_btn.setStyleSheet(
            f"QPushButton {{ background: {PANEL}; color: {TEXT};"
            f"border: 1px solid {ACCENT}; border-radius: 6px; padding: 0 10px;"
            "font-size: 12px; font-weight: 600; }}"
            f"QPushButton:hover {{ background: {ACCENT_HOVER}; color: #FFFFFF; }}")
        _wh_btn.setToolTip("跳转到 AI 设置 → 对话流，为该对话配置专属工作目录")
        _wh_btn.clicked.connect(self._open_workdir_settings)
        _wh_lay.addWidget(_wh_btn)
        wd_hint_lay.addWidget(self.wd_hint_bar)
        right.addWidget(self.wd_hint)
        right.addLayout(bottom)
        body.addLayout(right, 1)
        # 主内容外套 dock 行：左 dock 侧栏 | 主内容 | 右 dock 侧栏（隐藏时宽度 0 不占空间）
        self._dock_row = QHBoxLayout()
        self._dock_row.setContentsMargins(0, 0, 0, 0)
        self._dock_row.setSpacing(6)
        self._dock_row.addWidget(self._dock_left, 0)
        self._dock_row.addLayout(body, 1)
        self._dock_row.addWidget(self._dock_right, 0)
        root.addLayout(self._dock_row, 1)   # 底部区域占满窗口：聊天区弹性，输入行贴底
        add_brand_footer(self)
        # 恢复子面板与主面板的关系（贴附/融入，随设置即时应用）
        self._applied_panel_mode = None
        # 融入（dock）前的「原有大小」记忆：{面板 objectName: (w,h)} + 主窗口 (w,h)，
        # 切回贴附时按此还原（见 _remember_pre_dock_state / _undock_panel）
        self._pre_dock_sizes = {}
        self._pre_dock_geo = None
        self._apply_panel_mode()

    def _connect_signals(self):
        self.confirm_signal.connect(self._on_confirm)
        self.ask_signal.connect(self._on_ask)
        self.eval_signal.connect(self._on_assess_done)
        self.compact_signal.connect(self._on_compact_done)
        self.switch_ready.connect(self._finish_switch)
        self.evt_signal.connect(self._route)
        self.optimize_signal.connect(self._on_optimize_done)
        self.sess_name_signal.connect(self._on_ai_session_title)   # AI 起名结果回主线程改名
        self.git_probe_signal.connect(self._on_git_probe)

    # ---------- 欢迎页（无对话时居中介绍 AI 功能） ----------

    def _build_welcome(self) -> QWidget:
        # 优先使用自定义 UI/UX 包的欢迎页（热插拔）
        try:
            w = agent_ui_ux.load_and_build_welcome(self)
            if w is not None:
                return w
        except Exception:
            pass
        # 无卡片/无边框：直接居中排版文字，保持纯黑四色极简风格
        page = QWidget()
        page.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(40, 40, 40, 40)
        lay.addStretch(1)
        box = QVBoxLayout()
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(10)
        t = QLabel("zhuzhu Copilot")
        t.setStyleSheet(f"color: {TEXT}; font-size: {FONT_HERO}px; font-weight: 800;")
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.addWidget(t)
        sub = QLabel(_welcome_greeting())
        sub.setStyleSheet(f"color: {TEXT_DIM}; font-size: {FONT_BASE}px;")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.addWidget(sub)
        lay.addLayout(box)
        lay.addStretch(1)
        # 欢迎语按本地时间自动刷新（欢迎页驻留时跨时段自动切换）
        self._welcome_greet = sub
        self._welcome_timer = QTimer(self)
        self._welcome_timer.timeout.connect(self._refresh_welcome_greeting)
        self._welcome_timer.start(30_000)
        return page

    def _refresh_welcome_greeting(self) -> None:
        """按本地时间刷新欢迎页欢迎语（欢迎页驻留时跨时段自动切换）"""
        label = getattr(self, "_welcome_greet", None)
        if label is None:
            return
        text = _welcome_greeting()
        if label.text() != text:
            label.setText(text)

    def _update_welcome(self):
        """无对话内容时显示欢迎页，否则显示聊天区（发消息后立即切换）"""
        has_msg = bool(self._segments or self._history_segments or self._user_msgs)
        self.msg_stack.setCurrentWidget(
            self.msg_area if has_msg else self._welcome_page)
        # 对话定位器：无对话（欢迎页）时隐藏
        nav = getattr(self, "_nav_bar", None)
        if nav is not None:
            nav.setVisible(bool(has_msg) and bool(self._msg_nav))

    # ---------- 对话定位器（右侧半透明圆形，最近 30 条用户消息） ----------
    _MSG_NAV_MAX = 30

    def _build_msg_nav_bar(self) -> QWidget:
        """右侧半透明圆形定位列：垂直排布圆点，自顶部对齐，无定位点自动隐藏"""
        bar = QWidget()
        bar.setFixedWidth(26)
        bar.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(bar)
        lay.setContentsMargins(0, 6, 0, 6)
        lay.setSpacing(8)
        lay.addStretch(1)   # 圆点自顶部对齐
        self._nav_lay = lay
        bar.hide()
        return bar

    def _msg_nav_push(self, bubble, text: str):
        """为一条用户消息添加定位圆点：悬停 tooltip 显示用户提问，点击滚动定位。
        最多保留最近 30 条：超出后最旧圆点被移除（新对话覆盖旧），列高封顶不随会话无限增长。"""
        from PyQt6.QtWidgets import QPushButton as _NavBtn
        plain = re.sub(r"<[^>]+>", "", str(text or "")).strip()
        if not plain:
            plain = "（图片/文件消息）"
        tip = plain.replace("\n", " ")
        btn = _NavBtn("")
        btn.setFixedSize(12, 12)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        # 主题自适应圆点：深色下为透明白灰（原风格），浅色下加深灰底+深蓝描边（明显可见）。
        # 悬停时深色增强透明度、浅色转深蓝实心；并显式给 QToolTip 配色，杜绝浅色下黑底深字。
        if _resolve_theme() == "light":
            _dot_qss = (f"QPushButton {{ background: rgba(71,85,105,104);"
                        "border: 1px solid rgba(30,64,175,70); border-radius: 6px; }"
                        f"QPushButton:hover {{ background: {ACCENT_HOVER}; }}")
        else:
            _dot_qss = ("QPushButton { background: rgba(203,208,218,76);"
                        "border: 1px solid rgba(255,255,255,70); border-radius: 6px; }"
                        "QPushButton:hover { background: rgba(203,208,218,140); }")
        btn.setStyleSheet(
            _dot_qss
            + f"QToolTip {{ background-color: {PANEL}; color: {TEXT};"
            f" border: 1px solid {BORDER_SOFT}; padding: 4px 8px; }}")
        btn.setToolTip(tip[:120] + ("…" if len(tip) > 120 else ""))
        btn.clicked.connect(lambda _, b=bubble: self._msg_nav_go(b))
        self._msg_nav.append({"btn": btn, "bubble": bubble, "text": plain})
        self._nav_lay.insertWidget(self._nav_lay.count() - 1, btn)
        while len(self._msg_nav) > self._MSG_NAV_MAX:
            old = self._msg_nav.pop(0)
            try:
                old["btn"].deleteLater()
            except Exception:
                pass
        if hasattr(self, "_nav_bar"):
            self._nav_bar.show()

    def _msg_nav_go(self, bubble):
        """点击定位圆点：滚动消息流使目标对话可见"""
        try:
            self.msg_area.ensureWidgetVisible(bubble, 0, 16)
        except Exception:
            pass

    def _msg_nav_clear(self):
        """清空定位圆点（会话切换/全量重建/清空对话时调用）"""
        for e in self._msg_nav:
            try:
                e["btn"].deleteLater()
            except Exception:
                pass
        self._msg_nav = []
        nav = getattr(self, "_nav_bar", None)
        if nav is not None:
            nav.hide()

    # ---------- 多对话（会话）管理 ----------
    def _sessions_dir(self) -> Path:
        return agent_skills.CONFIG_DIR / "sessions"

    def _load_session_list(self) -> list:
        try:
            with open(self._sessions_dir() / "sessions.json", encoding="utf-8") as f:
                lst = json.load(f)
            return lst if isinstance(lst, list) else []
        except Exception:
            return []

    def _save_session_list(self, lst: list):
        try:
            d = self._sessions_dir()
            d.mkdir(parents=True, exist_ok=True)
            with open(d / "sessions.json", "w", encoding="utf-8") as f:
                json.dump(lst, f, ensure_ascii=False)
        except Exception:
            pass

    def _init_sessions(self):
        """启动时默认回到「最后一次使用且有内容」的对话；否则按「最近使用且有内容」的旧对话；
        无任何会话才新建。避免重启落在空白「新对话」上（用户上次误开/关闭前停留的空会话
        updated 较高）。"""
        lst = self._load_session_list()
        if not lst:
            self._create_session()
            lst = self._load_session_list()
        lst.sort(key=lambda s: s.get("updated", 0), reverse=True)   # 最近更新在前
        sid = self._read_last_session(lst)   # 优先恢复上次停靠的会话页面
        if not sid:
            sid = self._pick_last_session(lst)
        self._switch_to(sid)
        self._update_welcome()

    def _read_last_session(self, lst: list) -> str:
        """恢复「最后一次使用」的会话页面：上次关闭/切换停留的会话 id（last_session.txt）。
        仅在该会话存在且「有内容」时采用——若最后停留的是空白新对话，仍按旧规则选最近的有
        内容会话，避免重启落回空白页。"""
        try:
            sid = (self._sessions_dir() / "last_session.txt").read_text(
                encoding="utf-8").strip()
        except Exception:
            sid = ""
        if not sid or not any(s.get("id") == sid for s in lst):
            return ""
        d = self._sessions_dir()
        try:
            with open(d / f"{sid}.ui.json", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("user_msgs") or data.get("rows"):
                return sid
        except Exception:
            pass
        return ""

    def _record_last_session(self):
        """持久化「最后一次使用」的会话：重启时优先恢复到该对话页面。
        关停时全部会话 updated 会被统一刷新，单靠 updated 无法区分最后停留的会话，故显式记录。"""
        if not self._session_id:
            return
        try:
            d = self._sessions_dir()
            d.mkdir(parents=True, exist_ok=True)
            (d / "last_session.txt").write_text(self._session_id, encoding="utf-8")
        except Exception:
            pass

    def _pick_last_session(self, lst: list) -> str:
        """在按 updated 降序的会话列表中，优先挑选最近且有内容（用户消息/气泡）的会话；
        全部为空时回退最近更新的会话。"""
        d = self._sessions_dir()
        for s in lst:
            sid = s.get("id")
            if not sid:
                continue
            try:
                with open(d / f"{sid}.ui.json", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("user_msgs") or data.get("rows"):
                    return sid
            except Exception:
                continue
        return (lst[0] or {}).get("id", "")

    def _create_session(self, workdir: str = "") -> dict:
        s = {"id": uuid.uuid4().hex[:12],
             "name": "新对话", "created": time.time(), "updated": time.time(),
             "workdir": (workdir or "").strip()}
        lst = self._load_session_list()
        lst.append(s)
        self._save_session_list(lst)
        return s

    # ---------- 多对话并发：会话状态存储 / 引擎路由 / 排队消息 ----------
    def _new_sess_state(self, sid: str) -> dict:
        """新建会话运行时状态（内存段缓冲 + 独立引擎 + 排队消息）"""
        return {
            "engine": None,
            "workflow": None,         # 会话工作流（@工作流 会话级切换；None=跟随全局激活）
            "agent": None,            # 会话自定义 Agent（@agent 会话级切换；None=用工作流人格）
            "loaded": False,          # 是否已建立内存态（首次从磁盘加载后置 True）
            "segments": [],
            "history_segments": [],
            "rows": [],
            "user_msgs": [],
            "sub_segs": {},
            "sub_history": {},    # @子Agent 多轮上下文：{子Agent名: 对话消息列表（不含 system）}
            "sub_synced": {},     # @子Agent 已同步进主 Agent 引擎上下文的消息条数游标：{子Agent名: 条数}
            "space": "",          # 会话共享上下文空间 id（@工作流 切换时继承原工作流的活跃空间）
            "user_stopped": False,
            "end_badge_shown": False,
            "think_done": False,
            "think_start": 0.0,
            "task_active": False,
            "queued": [],         # 排队消息列表（按序发送）：[{text, images, files, ai_text, skill_names, plugin_names, shot}]
            "pending_confirm": None,  # 后台待确认命令：{name, args, risk, answered}（不弹窗打扰，切过去处理）
            "confirm_evt": None,      # 该会话确认等待事件
            "confirm_result": False,  # 该会话最近一次确认结果
            "pending_ask": None,      # 后台待回答提问：{args, answered}
            "ask_evt": None,          # 该会话提问等待事件
            "ask_result": "",         # 该会话最近一次提问回答
        }

    def _bind_sess(self, sid: str):
        """把当前工作属性指向指定会话的内存缓冲（切换会话时调用）。
        同时把「当前对话」作用域切换到该会话：共同上下文空间按对话隔离——界面上正在
        查看哪个对话，主线程内的共享空间读写就落在哪个对话上（后台会话各自的引擎线程
        在任务入口另行落地自己的对话作用域，互不干扰）。"""
        st = self._sess.setdefault(sid, self._new_sess_state(sid))
        self._segments = st["segments"]
        self._history_segments = st["history_segments"]
        self._rows = st["rows"]
        self._user_msgs = st["user_msgs"]
        self._sub_segs = st["sub_segs"]
        try:
            from zhuzhu_Copilot.core import agent_context
            agent_context.set_conversation_global(sid)
        except Exception:
            pass

    def _commit_sess(self):
        """把当前工作属性回写进当前会话状态（在 reassign 后/切换前调用）"""
        st = self._sess.get(self._session_id)
        if st is None:
            return
        st["segments"] = self._segments
        st["history_segments"] = self._history_segments
        st["rows"] = self._rows
        st["user_msgs"] = self._user_msgs
        st["sub_segs"] = self._sub_segs

    def _write_ui_json(self, sid: str, st: dict) -> None:
        """把会话 UI 气泡（rows）写入磁盘。关键防护：内存态无内容但磁盘已有历史时跳过，
        避免关闭/未加载完成的会话用空态把历史清空，导致重启后会话记录消失。"""
        if not sid or not st:
            return
        d = self._sessions_dir()
        d.mkdir(parents=True, exist_ok=True)
        ds = st.get("segments") or []
        clean_segments = [
            _strip_seg_render_cache(seg)
            for seg in (st.get("history_segments") or []) + ds
            if not (seg.get("type") == "mark" and seg.get("html") == "已停止")
        ]
        # 未归档的当前回复段（ds）必须确保在 rows 中有对应 AI 行：
        # 旧逻辑「ds 非空且 rows 末尾不是 ai 才追加」在「上一轮 AI 已归档（rows 末尾是
        # ai）、本轮流式回复仍在 ds」时漏追加 → 未完成任务关闭后该轮 AI 气泡重启丢失。
        # 改为内容指纹判等：末尾已是同一轮则原位刷新（幂等，防重复追加），否则追加。
        rows_src = st.get("rows") or []
        if ds:
            _tail = rows_src[-1] if rows_src and rows_src[-1].get("type") == "ai" else None
            if _tail is not None and _segs_same(_tail.get("segs") or [], ds):
                rows_src = rows_src[:-1] + [dict(_tail, segs=list(ds))]
            else:
                rows_src = rows_src + [{"type": "ai", "segs": list(ds)}]
        rows = [
            dict(r, segs=[_strip_seg_render_cache(s) for s in r.get("segs") or []
                          if not (s.get("type") == "mark" and s.get("html") == "已停止")])
            if r.get("type") == "ai" else r
            for r in rows_src
        ]

        def _disk_has_history() -> bool:
            try:
                with open(d / f"{sid}.ui.json", encoding="utf-8") as f:
                    old = json.load(f)
                return bool(old.get("rows") or old.get("segments") or old.get("user_msgs"))
            except Exception:
                return False

        # 防护①：内存态（气泡/分段/用户消息）全空但磁盘已有历史 → 跳过，防止覆盖丢失
        if not rows and not clean_segments and not (st.get("user_msgs") or []):
            if _disk_has_history():
                return
        # 防护②：内存态尚未从磁盘加载完成（切换会话后立即发消息/任务结束的竞态，
        # st["loaded"]=False）而磁盘已有历史 → 跳过写盘。否则"只有新消息的部分内存态"
        # 会覆盖磁盘完整历史 → 切换后历史消失。加载完成后（_finish_switch 置 loaded）
        # 再由后续持久化写入合并后的完整内容。
        # 注意：新创建的会话（_new_session/_clear_chat）必须立即置 loaded=True——
        # 新会话无"磁盘旧历史"，若保持 False 且磁盘已有首条消息历史，则所有后续
        # 落盘会被本防护全部拦截，磁盘永远停在首条消息快照（长对话重启只剩首条）。
        # 自愈兜底：内存态 user_msgs 已覆盖磁盘全部（数量≥磁盘）时，视为内容完整，
        # 允许写盘——即使未来有路径遗漏 loaded=True，长对话也不会再被永久冻结。
        if not st.get("loaded") and (rows or clean_segments or st.get("user_msgs")):
            if _disk_has_history():
                try:
                    with open(d / f"{sid}.ui.json", encoding="utf-8") as f:
                        _old = json.load(f)
                    _disk_n = len(_old.get("user_msgs") or [])
                    _mem_n = len(st.get("user_msgs") or [])
                    # 内存态消息数少于磁盘 = 切换会话竞态的部分内存态 → 拦截防覆盖；
                    # 否则（新会话正常累积，磁盘停在旧快照）放行
                    if _mem_n < _disk_n:
                        return
                except Exception:
                    return
        with open(d / f"{sid}.ui.json", "w", encoding="utf-8") as f:
            json.dump({"segments": clean_segments, "user_msgs": st.get("user_msgs") or [],
                       "rows": rows, "workflow": st.get("workflow"),
                       "agent": st.get("agent"),
                       "queued": st.get("queued") or [],
                       "sub_history": st.get("sub_history") or {},
                       "sub_synced": st.get("sub_synced") or {},
                       "space": st.get("space") or "",
                       "last_payload": st.get("last_payload") or None,
                       "regenerate_index": st.get("regenerate_index") or None},
                      f, ensure_ascii=False)

    def _persist_sid(self, sid: str, st: dict):
        """持久化指定会话到磁盘（后台任务结束/切换时调用，UI 线程轻量快存）"""
        if not sid or not st:
            return
        d = self._sessions_dir()
        d.mkdir(parents=True, exist_ok=True)
        eng = st.get("engine")
        if eng:
            threading.Thread(target=lambda: eng.save_context(d / f"{sid}.json"),
                             daemon=True).start()
        self._write_ui_json(sid, st)
        lst = self._load_session_list()
        for x in lst:
            if x.get("id") == sid:
                x["updated"] = time.time()
        self._save_session_list(lst)

    def _engine_for(self, sid: str):
        """返回指定会话的引擎（不存在则创建）；当前会话同步 self._engine 引用"""
        st = self._sess.setdefault(sid, self._new_sess_state(sid))
        eng = st.get("engine")
        if eng is None:
            eng = self._build_engine(sid)
            st["engine"] = eng
        if sid == self._session_id:
            self._engine = eng
        return eng

    def _build_engine(self, sid: str):
        """按会话创建独立引擎：事件回调携带 sid 经信号路由到对应会话缓冲
        （后台会话生成不中断、不污染当前视图，实现多对话并发）。
        Cordis：LLM 客户端与自定义工具按会话所属工作流加载（@工作流 会话级切换）。"""
        cfg = self._llm_config()
        st = self._sess.get(sid) or {}
        raw_wf = str(st.get("workflow") or "").strip()
        # 陈旧绑定校验：绑定的工作流若已被删除/禁用，就地纠正为默认——
        # 否则引擎会按不存在的名字派生出「<名> 工作流专属助手」人设，使已删除的工作流
        # 身份被"复活"（AI 反复提及/推销该工作流）。
        wf = agent_workflow.resolve_workflow(raw_wf or agent_workflow.active_workflow())
        if raw_wf and raw_wf != wf:
            st["workflow"] = wf
        try:
            agent_workflow.apply_tools(workflow=wf)   # 注册工作流自定义工具 + 技能目录
        except Exception:
            pass
        client, _src = agent_workflow.load_llm_client(cfg, workflow=wf)
        return agent_engine.AgentEngine(
            client,
            mcp_manager=self._mcp,
            on_delta=lambda s, _sid=sid: self.evt_signal.emit(_sid, "delta", s),
            on_status=lambda s, _sid=sid: self.evt_signal.emit(_sid, "status", s),
            on_result=lambda n, t, im, _sid=sid: self.evt_signal.emit(_sid, "result", (n, t, im)),
            on_reasoning=lambda s, _sid=sid: self.evt_signal.emit(_sid, "reasoning", s),
            # 第 5 项 AgentControl id 必须一起转发：子块「暂停/恢复」要用它命中注册句柄
            on_sub_event=lambda k, i, t, s, a="", _sid=sid:
                self.evt_signal.emit(_sid, "sub", (k, i, t, s, a)),
            confirm=lambda n, a, _sid=sid: self._confirm_tool(_sid, n, a),
            ask_user=lambda a, _sid=sid: self._ask_user_tool(_sid, a),
            text_only=self._text_only,
            memory_enabled=self._memory_enabled,
            direct=self._mode == "yolo",
            # 子 Agent 准入：沿用该会话上一次任务的判定（新任务在 _launch_task 按当轮
            # 力度/显式要求重算），避免引擎重建后门槛突然放开
            allow_subagents=bool(st.get("allow_subagents", True)),
            on_engine_rebuild=lambda _wf=None, _sid=sid: self.evt_signal.emit(_sid, "rebuild", _wf),
            on_uiux_rebuild=lambda _sid=sid: self.evt_signal.emit(_sid, "uiux_rebuild", None),
            on_btn_reg=lambda payload, _sid=sid: self.evt_signal.emit(_sid, "btn_reg", payload),
            on_session_name=lambda title, _sid=sid: self.evt_signal.emit(_sid, "sess_name", title),
            on_preview=lambda pth, _sid=sid: self.evt_signal.emit(_sid, "preview", pth),
            workflow=wf,
            persona=self._agent_persona(st.get("agent")),
            # 上下文决策（1M 开关 / 上游服务商声明 / 手填 / 内置表 / 推断）由
            # agent_llm.resolve_context 统一解析，引擎只消费结果：
            # window=窗口，max_output=预留输出，long_1m 决定压缩阈值 0.80/0.75
            context_window=cfg.get("context_window"),
            max_output_tokens=(cfg.get("context") or {}).get("max_output"),
            long_context_1m=bool((cfg.get("context") or {}).get("long_1m")),
            window_source=str((cfg.get("context") or {}).get("source") or ""),
            # 对话作用域：本引擎的共同上下文空间限定在其所属会话内（跨对话隔离）
            conversation=sid)

    def _rebuild_engine(self, sid: str, display_wf: str = None):
        """工作流切换后热插拔：主线程按目标工作流重建引擎并保留对话历史。
        display_wf：None=无显式目标（switch_workflow 全局切换/会话恢复/设置变更 →
        沿用会话绑定或全局激活）；""=切回默认 _default；其他=目标工作流名
        （@工作流 会话切换 / use_workflow_agent 对话内切换）。"""
        import logging
        _log = logging.getLogger(app_identity.APP_SLUG)
        try:
            self._invalidate_cmd_cache()   # 引擎按新工作流重建：命令/技能候选随之下次重建
            st = self._sess.get(sid)
            if st is None:
                _log.warning("_rebuild_engine: 会话 %s 不存在，无法重建引擎", sid)
                return
            if display_wf is not None:
                target = display_wf if display_wf else agent_workflow.DEFAULT_WORKFLOW
            else:
                target = (st.get("workflow") or agent_workflow.active_workflow())
            # 目标工作流已删除/禁用 → 回退默认，避免派生已删工作流的专属人设
            wf = agent_workflow.resolve_workflow(target)
            # 更新会话记录，确保 _build_engine 与后续切换/重建一致
            st["workflow"] = wf
            try:
                agent_workflow.apply_tools(workflow=wf)
            except Exception as e:
                _log.warning("_rebuild_engine: apply_tools 失败: %s", e)
            old = st.get("engine")
            history = list(old._messages) if old is not None else []
            old_tokens = dict(old.tokens) if old is not None else None
            new_eng = self._build_engine(sid)
            if history:
                new_eng._messages = history
            # 热插拔重建保留已累计的 token 统计，避免工作流/Agent 切换后计数归零
            if old_tokens:
                new_eng.tokens.update(old_tokens)
            st["engine"] = new_eng
            if sid == self._session_id:
                self._engine = new_eng
        except Exception as e:
            _log.exception("_rebuild_engine 失败: %s", e)
            # 重建失败时尝试恢复：保持旧引擎不变，不清除 _pending_rebuild（由调用方管理）

    def _route(self, sid: str, kind: str, payload):
        """会话事件路由（主线程）：前台会话走渲染，后台会话只更新内存缓冲"""
        if kind == "uiux_rebuild":
            # manage_uiux 切换/更新 UI/UX 包：记录待应用，任务结束后重建面板（热插拔）
            self._uiux_rebuild_pending = True
            return
        if kind == "btn_reg":
            # register_panel_btn 工具：主线程应用按钮注册/注销（UI 操作必须回主线程）
            self._apply_btn_reg(payload)
            return
        if kind == "sess_name":
            # set_session_name 工具：AI Agent 给对话起名/改名（含后台会话，按 sid 落库）
            self._apply_session_name(sid, str(payload or ""), source="ai")
            return
        if kind == "pending":
            # 后台会话挂起确认/提问：主线程刷新下拉标记 + 优雅通知（不弹窗）
            self._refresh_session_combo()
            self._notify_background(payload[0], payload[1])
            return
        if sid != self._session_id:
            self._bg_event(sid, kind, payload)
            return
        if kind == "preview":
            # AI 操作文件后自动在右侧预览（仅前台会话触发，主线程更新 UI）。
            # 预览面板正在播放媒体时不打断：跳过自动预览跳转，保持媒体播放
            cw = getattr(self, "code_win", None)
            if (cw is not None and callable(getattr(cw, "is_media_active", None))
                    and cw.is_media_active()):
                return
            self._open_code_preview(payload)
            return
        if kind == "tool_input":
            # 引擎执行 run_command 前的 confirm 钩子 → 记录命令全文；
            # 同时把它写进对应工具行的 op 段：该行立刻成为命令块（`$ 命令`），
            # 执行结果到达时再续写为同一块的输出（同一气泡，上下紧贴）。
            name, args_json = payload
            st = self._sess.get(self._session_id) or {}
            _remember_cmd(st, args_json)
            if self._remember_cmd_seg(self._segments, args_json):
                self._refresh_ai_html()
                self._scroll_bottom()
            return
        if kind == "delta":
            self._on_delta(payload)
        elif kind == "status":
            self._on_status(payload)
        elif kind == "result":
            name, text, images = payload
            self._on_result(name, text, images)
        elif kind == "reasoning":
            self._on_reasoning(payload)
        elif kind == "sub":
            k, i, t, s, *rest = payload       # 第 5 项 = 子 Agent 控制注册 id
            self._on_sub_event(k, i, t, s, rest[0] if rest else "")
        elif kind == "subagent_done":
            # @子Agent 直接调用完成：记录结果并触发统一收尾（_refresh_meta）
            self._on_subagent_done(payload)
        elif kind == "rebuild":
            # Cordis 工作流切换：重建会话引擎（热插拔）。payload 为 use_workflow_agent
            # 指定的目标工作流名（"" 表示切回默认 _default；switch_workflow 全局切换时为 None）。
            # 若该会话引擎线程仍在运行，立即替换会造成"旧引擎线程继续跑 + 新引擎占位"的
            # 同会话双引擎并发（输出重复/token 双计/结束态失真）。故改为记录待应用目标，
            # 等本会话任务真正结束（_refresh_meta）后再重建，避免中途撕裂在途任务。
            _eng = (self._sess.get(sid) or {}).get("engine")
            _running = bool(_eng is not None and getattr(_eng, "_thread", None)
                            and _eng._thread.is_alive())
            if _running:
                # 合并覆盖：同一会话多次切换只保留最后一次目标（避免旧 _pending_rebuild 残留）
                self._pending_rebuild[sid] = payload if isinstance(payload, str) else None
                return
            # 引擎空闲：直接重建。失败则记录 _pending_rebuild 等待 _refresh_meta 兜底重试
            try:
                self._rebuild_engine(sid, display_wf=payload if isinstance(payload, str) else None)
            except Exception:
                import logging
                logging.getLogger(app_identity.APP_SLUG).exception(
                    "_route.rebuild 直接重建失败，记录 _pending_rebuild 等待兜底重试")
                self._pending_rebuild[sid] = payload if isinstance(payload, str) else None

    def _bg_event(self, sid: str, kind: str, payload):
        """后台会话任务事件：只更新该会话内存缓冲并在结束落盘，不渲染 UI"""
        st = self._sess.get(sid)
        if st is None:
            return
        segs = st["segments"]
        if kind == "delta":
            if segs and segs[-1].get("type") == "text":
                segs[-1]["raw"] += payload
            else:
                segs.append({"type": "text", "raw": payload})
            return
        if kind == "reasoning":
            # 与前台一致：末段是思考段则追加（同一轮），否则新建一段（新一轮），
            # 保证后台会话切回后仍能看到每一轮独立的思考过程（转义为富文本）
            if segs and segs[-1].get("type") == "think":
                segs[-1]["html"] += _esc(payload)
            else:
                segs.append({"type": "think", "html": _esc(payload)})
            return
        if kind == "result":
            name, text, images = payload
            cmd = ""
            if name == "ask_user":
                # 后台会话同理：AI 提问拼接到回答前，作为灰色小字执行结果入缓冲
                q = str(st.get("last_ask_q") or "").strip()
                st["last_ask_q"] = ""
                body = (str(text) or "").strip()
                text = (f"AI 提问：{q}\n\n你的回答：{body}" if q
                        else (f"你的回答：{body}" if body else ""))
            elif name == "run_command":
                # 命令全文拼接到输出前（同前台）
                cmd = str(st.get("last_cmd") or "").strip()
                st["last_cmd"] = ""
            shown = (text or "").strip()
            if not shown:
                return
            if len(shown) > 20000:
                shown = shown[:20000] + " …（输出过长已截断显示，完整内容已返回模型）"
            shown = _esc(shown).replace("\n", "<br/>")
            # 与前台同构：输出就地续写到对应工具行的 op 段（同区块「工具在上、输出在下」）
            if not self._attach_out_seg(segs, name, shown, cmd):
                segs.append({"type": "result", "html": shown, "collapsed": False,
                             "cmd": cmd})
            for u in images or []:
                segs.append({"type": "image", "url": u, "caption": "已截屏"})
            return
        if kind == "sub":
            k, idx, title, text, *rest = payload    # 第 5 项 = 子 Agent 控制注册 id
            aid = rest[0] if rest else ""
            if k == "start":
                seg = {"type": "sub", "title": title, "raw": "", "steps": [],
                       "agent_id": aid}
                st["sub_segs"][idx] = seg
                segs.append(seg)
            elif k in ("delta", "tool", "output"):
                seg = st["sub_segs"].get(idx)
                if seg is None:
                    for s in reversed(segs):
                        if s.get("type") == "sub" and s.get("title") == title:
                            seg = s
                            break
                if seg is None:
                    seg = {"type": "sub", "title": title, "raw": "", "steps": [],
                           "agent_id": aid}
                    st["sub_segs"][idx] = seg
                    segs.append(seg)
                elif aid and not seg.get("agent_id"):
                    seg["agent_id"] = aid   # 兜底建的段补上控制 id（老路径可能没带）
                if k == "delta":
                    seg["raw"] += text
                else:
                    seg.setdefault("steps", []).append({"kind": k, "text": text})
            return
        if kind == "tool_input":
            # 后台会话工具执行前：记录命令全文并写进对应 op 段（结果到达时续写为输出）
            name, args_json = payload
            _remember_cmd(st, args_json)
            self._remember_cmd_seg(segs, args_json)
            return
        if kind == "status":
            s = payload
            if s == "正在思考…":
                return
            if s.startswith(("正在调用插件:", "插件已调用:")):
                done = s.startswith("插件已调用:")
                name = s.split(":", 1)[1].strip()
                segs.append({"type": "op",
                             "name": "已调用插件" if done else "调用插件",
                             "meta": name, "ico": "plugin",
                             "tip": self._plugin_tip(name, "能力"),
                             "html": ("✓ 已调用插件 " if done else "▎调用插件 ") + _esc(name)})
                return
            if s.startswith(("正在调用技能:", "技能已调用:")):
                name = s.split(":", 1)[1].strip()
                done = s.startswith("技能已调用:")
                # 后台会话不渲染转圈行：仅记录操作段，切回前台时统一渲染。
                # 与前台 _on_status 同构：标题固定、技能名入 meta、ico 稳定为 skill/plugin。
                segs.append(self._skill_op_seg(
                    "已调用技能" if done else "调用技能", name,
                    ("✓ 已调用技能 " if done else "▎调用技能 ") + _esc(name), sid))
                return
            if s.startswith(("待执行工具:", "正在执行:")):
                name = s.split(":", 1)[1].strip()
                tip, ico = self._tool_op_tip(name, sid)
                seg = {"type": "op", "html": f"▎{_esc(name)}", "name": name, "tip": tip}
                if ico:
                    seg["ico"] = ico
                segs.append(seg)
                return
            if s.startswith("正在并行执行"):   # 并发编辑批量状态
                segs.append({"type": "op", "html": f"▎{_esc(s)}", "name": s,
                             "ico": "parallel"})
                return
            if s == "完成" or s == "已停止" or s.startswith("错误"):
                if st.get("task_active"):   # 后台任务真实结束才通知一次（完成/停止/出错）
                    label = self._session_label(sid)
                    if s == "已停止":
                        self._toast("AI 任务已停止", f"对话「{label}」的任务已被停止", True)
                    elif s.startswith("错误"):
                        self._toast("AI 任务出错", f"对话「{label}」的任务执行出错", True)
                    else:
                        self._toast("AI 任务完成", f"对话「{label}」的任务已成功完成", False)
                st["task_active"] = False
                self._persist_sid(sid, st)   # 后台任务结束立即落盘，切回即完整
                self._flush_queue(sid)       # 本轮完成 → 自动发送排队消息
            return

    # ---------- 消息排队：任务运行中发消息 → 排队，本轮完成后按序自动发送 ----------
    def _update_queue_bar(self):
        """刷新当前会话的排队消息面板（有多条显示，无则隐藏）"""
        st = self._sess.get(self._session_id)
        self.queue_panel.update_queue((st or {}).get("queued") or [])
        self._sync_queue_height()

    def _sync_queue_height(self, *_):
        """排队面板高度 = 消息内容自然高度，上限为主面板 20%（内部滚动）：
        消息少时紧凑、行间无大间距、最上方消息不被顶出；
        消息多时随消息增多而增高并内部滚动（从下至上，最新消息始终可见）。
        todos 已独立成窗口，排队面板不再联动 todos 高度。"""
        qp = self.queue_panel
        if not qp.isVisible():
            return
        content = getattr(qp, "_rows_h", 0) + 40   # 表头 + 上下内边距 + 间距
        limit = max(80, int(self.height() * 0.2))
        qp.setFixedHeight(max(56, min(content, limit)))

    def _on_queue_edit(self, idx: int):
        """编辑第 idx 条排队消息：回填输入框并移除该条，发送后按原队列位置重新排队"""
        st = self._sess.get(self._session_id)
        qs = (st or {}).get("queued") or []
        if idx < 0 or idx >= len(qs):
            return
        q = qs.pop(idx)
        self._queue_edit_idx = idx
        self.input.setPlainText(q.get("text") or "")
        self._update_queue_bar()
        self.input.setFocus()

    def _on_queue_delete(self, idx: int):
        """删除第 idx 条排队消息"""
        st = self._sess.get(self._session_id)
        qs = (st or {}).get("queued") or []
        if 0 <= idx < len(qs):
            qs.pop(idx)
        if self._queue_edit_idx is not None and idx < self._queue_edit_idx:
            self._queue_edit_idx -= 1   # 列表左移，编辑目标位置同步前移
        self._update_queue_bar()

    def _on_queue_clear(self):
        """清空全部排队消息并复位编辑状态"""
        st = self._sess.get(self._session_id)
        if st:
            st["queued"] = []
        self._cancel_queue_edit()
        self._update_queue_bar()

    def _on_todos_clear(self):
        """用户手动清空 todos：清空**当前会话**的清单文件并刷新窗口
        （任务清单按会话独享，不影响其他会话；AI 下次 update_todo 全量恢复）。
        1 秒内连续清空 3 次 → 提示可前往设置关闭任务清单窗口。"""
        now = time.time()
        clicks = [t for t in getattr(self, "_clear_clicks", []) if now - t < 1.0]
        clicks.append(now)
        self._clear_clicks = clicks
        try:
            agent_tools.clear_todos()      # 作用域 = 界面当前会话（session 独享）
        except Exception:
            pass
        self.todos_win.update_todos([])
        if len(clicks) >= 3:
            self._clear_clicks = []
            self._suggest_disable_todos()

    def _suggest_disable_todos(self):
        """弹窗提示可关闭任务清单窗口，并提供一键跳转设置（深色底 + 白字）"""
        box = QMessageBox(self)
        box.setWindowTitle("提示")
        box.setStyleSheet(
            f"QMessageBox {{ background: {PANEL}; }}"
            f"QMessageBox QLabel {{ color: {TEXT}; font-size: 13px; }}"
            f"QMessageBox QPushButton {{ color: {TEXT}; background: {CARD};"
            f"border: 1px solid {BORDER}; border-radius: 8px;"
            f"padding: 6px 14px; font-size: 13px; }}"
            f"QMessageBox QPushButton:hover {{ background: {HOVER}; }}")
        box.setText("1 秒内连续清空了 3 次任务清单。\n如不需要该窗口，可在设置中关闭任务清单窗口。")
        go = box.addButton("前往设置", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is go:
            self._open_settings()

    def _cancel_queue_edit(self):
        """取消编辑状态并恢复输入框占位提示"""
        self._queue_edit_idx = None
        self.input.setPlaceholderText(self._input_placeholder)

    def _on_input_cancel_edit(self):
        """输入框被清空时自动取消编辑状态（避免后续误替换原队列位置）"""
        if self._queue_edit_idx is not None and not self.input.toPlainText().strip():
            self._cancel_queue_edit()

    def _flush_queue(self, sid: str):
        """该会话本轮任务完成后：发送排队消息中最新的一条（一次一条，下轮继续）"""
        st = self._sess.get(sid)
        qs = (st or {}).get("queued") or []
        if not qs:
            return
        eng = st.get("engine")
        if eng and eng._thread and eng._thread.is_alive():
            return     # 该会话仍有任务在跑，继续等待
        q = qs.pop()   # 最新一条优先发送
        if sid == self._session_id:
            self._update_queue_bar()
            self._do_send(q)
        else:
            self._bg_send(sid, q)

    def _build_payload(self, text: str, images: list, files: list):
        """解析输入为发送 payload（排队/直接发送共用）；无法解析返回 None"""
        ai_text = text
        skill_names = []
        plugin_names = []
        skill, skill_prompt = self._match_skill(text)
        if skill:
            skill_names = [skill.get("name")]
            ai_text = skill_prompt or f"请严格按技能「{skill.get('name')}」的流程执行。"
        plugin, plugin_prompt = self._match_plugin(text)
        if plugin:
            pname = plugin.get("name")
            plugin_names = [pname]
            ai_text = plugin_prompt or f"请调用插件「{pname}」完成本任务。"
        tool = self._match_tool(text)
        shot = None
        if tool:
            tname, targs = tool
            if tname == "screenshot":
                try:
                    shot = agent_screen.capture_screen_data_url(grid=False)
                    ai_text = "已截取当前屏幕并展示在对话中，请基于截图内容回答或继续执行。"
                except Exception:
                    shot = None
            else:
                ai_text = (f"请调用工具「{tname}」完成以下任务，参数必须按 JSON 传入。\n"
                           f"工具参数说明：{self._tool_params_hint(tname)}\n"
                           f"参数原始文本：{targs or '(无，可自行确定合理参数，不确定时先 ask_user 澄清)'}")
        if files:
            note = "以下为拖入的附件文件，请按需读取内容：\n" + \
                "\n".join(f"- {p}" for p in files)
            ai_text = (ai_text + "\n\n" if ai_text else "") + note
        return {"text": text, "images": images, "files": files,
                "ai_text": ai_text, "skill_names": skill_names,
                "plugin_names": plugin_names, "shot": shot}

    def _do_send(self, p: dict):
        """按已解析的 payload 发送消息（前台：渲染用户气泡 + 启动任务）"""
        text = p.get("text") or ""
        ai_text = p.get("ai_text") or text
        skill_names = p.get("skill_names") or []
        plugin_names = p.get("plugin_names") or []
        images = list(p.get("images") or [])
        files = list(p.get("files") or [])
        shot = p.get("shot")
        # 记录重试所需信息：原 payload + 引擎上下文回退点（最后一条用户消息之前）
        st = self._sess.setdefault(self._session_id, self._new_sess_state(self._session_id))
        eng = self._engine_for(self._session_id)
        st["last_payload"] = dict(p)
        st["regenerate_index"] = len(getattr(eng, "_messages", []) or [])
        self._auto_name_session(ai_text)
        # 归档上一轮 AI 回复到历史（须在追加新用户消息前完成，保证交错行顺序正确）
        if self._segments:
            archived = list(self._segments)
            self._history_segments.extend(self._segments)
            self._history_segments.append({"type": "split"})
            # 归档行同时持久化耗时徽章与回合时间行（重启/切回后事件流回合信息完整）
            _prev = self._ai_bubble if self._bubble_alive(self._ai_bubble) else None
            self._rows.append({
                "type": "ai", "segs": archived,
                "cost": getattr(_prev, "cost", None),
                "meta": getattr(_prev, "turn_meta", "") or "",
            })
            # 关键：把仍指向活动段列表的气泡注册重定向到归档副本（就地 clear 复用前），
            # 否则旧气泡折叠/展开会渲染下一轮最新内容（气泡被最新输出覆盖）
            self._repoint_live_bubble_segs(archived)
        self._ai_bubble = None
        self._segments.clear()
        self._user_msgs.append(text)
        self._rows.append({"type": "user", "text": text})
        self._update_welcome()          # 发消息后欢迎介绍立即消失
        # 用户气泡：文字与拖入的图片/文件一并渲染进同一气泡
        if images or files:
            parts = ([f'<div style="font-size:14px;">{_esc(text).replace(chr(10), "<br/>")}</div>']
                     if text else [])
            parts += [f'<img src="{u}" width="200" style="border-radius:10px;'
                      f'border:1px solid {BORDER};display:block;margin:10px 0;">' for u in images]
            for pth in files:
                fname = os.path.basename(pth)
                fsize = self._file_size_text(pth)
                parts.append(
                    f'<div style="display:inline-block;vertical-align:middle;'
                    f'background:{CARD};border:1px solid {BORDER};border-radius:10px;'
                    'padding:7px 10px;margin:10px 8px 10px 0;">'
                    f'<img src="{self._file_thumb_data_url(pth)}" width="34" height="34" '
                    'style="vertical-align:middle;border-radius:6px;">'
                    f'<span style="vertical-align:middle;margin-left:8px;">'
                    f'<span style="color:{TEXT};font-size:13px;">{_esc(fname[:18])}</span>'
                    f'<br><span style="color:{TEXT_DIM};font-size:10px;">{_esc(fsize or "文件")}</span>'
                    f'</span></div>')
            src = "<br/>".join(parts)
            b = self._add_bubble(self._scale_user_html(src, self._font_scale()), "user", rich=True)
            b.setProperty("rich_src", src)
        else:
            self._add_bubble(text, "user")
        send_images = list(images)
        if shot:
            send_images.append(agent_screen.capture_screen_data_url(grid=True))
        if shot:
            self._segments.append({"type": "image", "url": shot, "caption": "已截屏"})
        self._user_stopped = False
        self._end_badge_shown = False
        self._think_done = False
        self._think_start = 0.0
        self._last_activity = time.time()
        self.input.clear()
        self.input.setFocus()
        est = agent_llm.estimate_tokens(text) + \
            agent_llm.estimate_image_tokens() * len(send_images)
        self.token_label.setText(f"~{est} tk")
        self._set_action_busy()   # 发送后按钮变转圈（可点击停止）
        self._task_active = True  # 先置位：后续任何步骤失败都会触发 _refresh_meta 兜底清理
        self._turn_started_at = time.time()   # 回合计时起点（供耗时徽章/时间行，见 _ensure_ai_bubble）
        # 模型路由：自动选择模式（下拉「自动选择」未手动指定模型）先由内置
        # agnes 评估任务难度（后台线程），评估后按难度+视觉需求自动选合适模型。
        # 发送即落盘 + 启动任务全部兜底 try：任何失败都复位按钮/任务状态并提示，
        # 绝不让发送按钮卡在转圈。
        try:
            self._clear_attachments()   # 发送后清空附件条
            self._commit_sess()       # segments 已重建，回写当前会话状态
            # 发送即落盘：即使任务立即失败/应用被强杀，用户消息与气泡也已保存，
            # 避免"发送后没跑完就关闭 → 该会话聊天记录丢失"
            self._write_ui_json(self._session_id, self._sess.get(self._session_id))
            if not self._model_override:
                self._eval_pending = (self._session_id, ai_text, send_images,
                                      skill_names, plugin_names)
                self._eval_pending_at = time.time()
                threading.Thread(target=self._assess_worker, daemon=True).start()
            else:
                self._launch_task(ai_text, send_images, skill_names,
                                  self._resolve_effort(ai_text), sid=self._session_id,
                                  plugin_names=plugin_names)
        except Exception as e:
            import logging
            logging.getLogger(app_identity.APP_SLUG).exception("任务启动失败: %s", e)
            self._eval_pending = None
            self._task_active = False
            self._hide_spinner()
            self._set_action_idle()
            try:
                self._toast("任务启动失败", str(e), warn=True)
            except Exception:
                pass

    def _bg_send(self, sid: str, q: dict):
        """后台会话：直接在该会话引擎上启动排队消息任务（不渲染 UI）"""
        st = self._sess.get(sid)
        if not st:
            return
        segs = st["segments"]
        if segs:
            st["history_segments"].extend(segs)
            st["history_segments"].append({"type": "split"})
            st["rows"].append({"type": "ai", "segs": list(segs)})
        st["segments"] = []
        st["user_msgs"].append(q.get("text") or "")
        st["rows"].append({"type": "user", "text": q.get("text") or ""})
        st["task_active"] = True
        # 后台会话同样记录重试信息：切回该会话后重试按钮可正常重发
        st["last_payload"] = dict(q)
        st["regenerate_index"] = len(getattr(st.get("engine"), "_messages", []) or [])
        ai_text = q.get("ai_text") or q.get("text") or ""
        send_images = list(q.get("images") or [])
        if q.get("shot"):
            try:
                send_images.append(agent_screen.capture_screen_data_url(grid=True))
            except Exception:
                pass
        try:
            self._launch_task(ai_text, send_images, q.get("skill_names") or [],
                              self._resolve_effort(ai_text), sid=sid,
                              plugin_names=q.get("plugin_names") or [])
        except Exception as e:
            import logging
            logging.getLogger(app_identity.APP_SLUG).exception("后台会话任务启动失败: %s", e)
            st["task_active"] = False
            if sid == self._session_id:
                self._task_active = False
                self._hide_spinner()
                self._set_action_idle()
                self._notify_blocked(f"后台任务启动失败: {e}")

    def _autosave_flush(self):
        """周期自动备份：任一会话有任务运行中时，把当前会话 UI 气泡轻量落盘
        （_write_ui_json 的保护性守卫仍生效，不会用不完整内存态覆盖磁盘历史）。
        用于防崩溃/非正常退出：『任务未完成』的对话在退出前就能恢复聊天记录。
        只写 UI json，不启动引擎 save_context 线程、不更新会话列表，保持轻量。"""
        try:
            running = self._task_active or any(
                (st or {}).get("task_active") for st in self._sess.values())
            if not running or not self._session_id:
                return
            _cur = self._sess.get(self._session_id) or {}
            st = {
                "history_segments": self._history_segments,
                "segments": self._segments,
                "rows": self._rows or self._reconstruct_rows(),
                "user_msgs": self._user_msgs,
                "loaded": bool(_cur.get("loaded")),
                "workflow": _cur.get("workflow"),
                "queued": _cur.get("queued") or [],   # 排队消息随自动备份落盘
                "last_payload": _cur.get("last_payload") or None,   # 备份不置空重试信息
                "regenerate_index": _cur.get("regenerate_index") or None,
            }
            self._write_ui_json(self._session_id, st)
        except Exception:
            pass

    def _persist_current(self):
        """保存当前会话：模型消息 + 界面气泡（segments/用户消息）+ 更新时间。
        模型上下文（含图片压缩，耗时）后台线程异步落盘，UI 线程只做轻量 JSON 快存，
        避免任务结束/切换会话时主线程阻塞。"""
        if not self._session_id:
            return
        d = self._sessions_dir()
        d.mkdir(parents=True, exist_ok=True)
        if self._engine:
            eng, sid = self._engine, self._session_id
            threading.Thread(target=lambda: eng.save_context(d / f"{sid}.json"),
                             daemon=True).start()
        _cur = self._sess.get(self._session_id) or {}
        st = {
            "history_segments": self._history_segments,
            "segments": self._segments,
            "rows": self._rows or self._reconstruct_rows(),
            "user_msgs": self._user_msgs,
            "loaded": bool(_cur.get("loaded")),   # 未加载完成时不落盘（防护②防竞态覆盖）
            "workflow": _cur.get("workflow"),
            "queued": _cur.get("queued") or [],   # 排队消息随持久化落盘（关闭不丢）
            "sub_history": _cur.get("sub_history") or {},  # @子Agent 多轮上下文随会话落盘
            "sub_synced": _cur.get("sub_synced") or {},    # @子Agent 同步主 Agent 游标随会话落盘
            "last_payload": _cur.get("last_payload") or None,  # 重试所需信息随会话落盘
            "regenerate_index": _cur.get("regenerate_index") or None,
        }
        self._write_ui_json(self._session_id, st)
        lst = self._load_session_list()
        for x in lst:
            if x.get("id") == self._session_id:
                x["updated"] = time.time()
                x["name"] = self._session_name
        self._save_session_list(lst)

    def _refresh_session_combo(self):
        lst = self._load_session_list()
        self.session_combo.blockSignals(True)
        self.session_combo.clear()
        for s in lst:
            self.session_combo.addItem(s.get("name", "新对话"), s.get("id"))
        idx = self.session_combo.findData(self._session_id)
        if idx >= 0:
            self.session_combo.setCurrentIndex(idx)
        self.session_combo.blockSignals(False)
        self._fit_session_combo_width()   # 会话名变化后收起态宽度随之自适应

    def _on_session_selected(self, idx):
        sid = self.session_combo.itemData(idx)
        if sid and sid != self._session_id:
            self._switch_to(sid)

    def _switch_to(self, sid: str):
        """切换会话（多对话并发）：当前会话后台任务不中断；目标会话若已有内存态
        （可能仍在后台生成）直接恢复渲染，首次进入则后台读盘后一次性渲染。"""
        self._commit_sess()
        self._persist_current()
        self._session_id = sid
        self._record_last_session()   # 记录最后停靠的会话页面（重启优先恢复）
        lst = self._load_session_list()
        s = next((x for x in lst if x.get("id") == sid), None)
        self._session_name = s.get("name", "新对话") if s else "新对话"
        # 对话流：切换到该对话后应用其独立工作目录（无则回退全局默认）
        try:
            self._apply_workdir(self._effective_workdir(sid))
            self._update_wd_hint()   # 切换会话后刷新工作目录提示条显隐
        except Exception:
            pass
        self._hide_spinner()
        while self.msg_lay.count() > 1:  # 清空消息流（保留末尾 stretch）
            item = self.msg_lay.takeAt(0)
            self._free_layout_item(item)
        self._bubble_widgets = []
        self._bubble_segs = {}
        st = self._sess.get(sid)
        if st is not None and (st["segments"] or st["rows"] or st["queued"]):
            # 内存态有实时内容（新建会话的任务/排队/后台生成中）：直接恢复，不覆盖
            st["loaded"] = True
            self._bind_sess(sid)
            self._engine_for(sid)   # 同步 self._engine 指向该会话引擎，避免旧引擎串台
            self._render_history_all()
            self._end_badge_shown = False
            self._refresh_session_combo()
            self._update_welcome()
            self._scroll_bottom()
            self._update_queue_bar()
        else:
            # 首次进入（或内存态为空）：新建/复用内存态并后台读盘补齐历史
            st = self._new_sess_state(sid) if st is None else st
            self._sess[sid] = st
            self._bind_sess(sid)
            d = self._sessions_dir()
            eng = self._engine_for(sid)

            def _load():
                try:
                    eng.load_context(d / f"{sid}.json")
                except Exception:
                    pass
                segs, ums = [], []
                data = {}
                try:
                    with open(d / f"{sid}.ui.json", encoding="utf-8") as f:
                        data = json.load(f)
                    segs, ums = data.get("segments") or [], data.get("user_msgs") or []
                except Exception:
                    pass
                rows = [r for r in (data.get("rows") or [])
                        if isinstance(r, dict) and r.get("type") in ("user", "ai")]
                wf = (data.get("workflow") or "").strip()
                ag = data.get("agent") or None
                queued = [q for q in (data.get("queued") or []) if isinstance(q, dict)]
                sub_history = {k: v for k, v in (data.get("sub_history") or {}).items()
                               if isinstance(v, list)}
                sub_synced = {k: v for k, v in (data.get("sub_synced") or {}).items()}
                space = (data.get("space") or "").strip()
                # 重试信息随会话落盘/恢复：重启后最后一条 AI 气泡的重试按钮可正常重发
                last_payload = data.get("last_payload") or None
                regen_idx = data.get("regenerate_index") or None
                self.switch_ready.emit(sid, segs, ums, rows, wf, ag, queued,
                                       sub_history, sub_synced, last_payload,
                                       regen_idx, space)

            threading.Thread(target=_load, daemon=True).start()
        # 切到该会话后处理其挂起的确认/提问（后台会话不弹窗，切到前台才弹）
        self._flush_pending(sid)
        # 切换对话：复位排队编辑状态；todos 独立窗口刷新为该会话自己的任务清单
        self._cancel_queue_edit()
        self._call_panel("todos_win", "update_todos", agent_tools.load_todos())
        self._update_wf_label()   # 切换会话后刷新顶部工作流标签
        # 切回运行中的会话：恢复打字指示器与发送/停止按钮（见 _sync_task_ui）
        self._sync_task_ui()

    def _sync_task_ui(self):
        """会话切换/恢复后同步“任务进行中”的 UI 占位：打字指示器 + 发送/停止按钮。
        切换会话会清空消息流的临时控件（_hide_spinner）并把按钮复位为灰色发送；
        若切回时该会话任务仍在后台运行（引擎线程/评估/子 Agent 存活），需恢复
        打字指示器与「可停止」按钮，避免“任务在跑却无指示器、按钮为灰色”。"""
        st = self._sess.get(self._session_id) or {}
        eng = st.get("engine") if isinstance(st, dict) else None
        running = bool(
            (self._eval_pending is not None
             and self._eval_pending[0] == self._session_id)
            or (eng is not None and eng._thread is not None
                and eng._thread.is_alive())
            or self._subagent_running())
        if not running:
            return   # 空闲会话：按钮/指示器本就该在空闲态，无需处理
        # 恢复打字指示器（思考中 → 转圈；已开始输出 → 显示已思考时长）
        if self._think_done:
            self._ensure_spinner()
            if self._spinner_lbl is not None and self._think_start:
                self._spinner_lbl.setText(
                    f"已思考 {int(time.time() - self._think_start)} 秒")
        else:
            self._start_think()
        # 恢复发送/停止按钮：停止中→红底禁用；已输出→停止图标；仍思考→转圈
        if self._user_stopped:
            self._set_action_stopping()
        elif self._segments:
            self._ensure_stop_btn()
        else:
            self._set_action_busy()

    def _finish_switch(self, sid: str, segs: list, ums: list, rows: list, wf: str = "", ag=None, queued=None, sub_history=None, sub_synced=None, last_payload=None, regen_idx=None, space: str = ""):
        """会话切换收尾（主线程）：用后台线程读到的数据一次性渲染"""
        if sid != self._session_id:
            return   # 用户已切走，丢弃过期加载，防止覆盖当前视图
        st = self._sess[sid]
        # 恢复排队消息（任务运行中关闭窗口时 queued 已持久化，重启后继续排队发送）
        _qd = [q for q in (queued or []) if isinstance(q, dict)]
        if _qd:
            _cur_q = st.get("queued") or []
            st["queued"] = _qd + [q for q in _cur_q if q not in _qd]
        # 恢复 @子Agent 多轮上下文（会话级持久化，重启/切回后可继续追问同一子 Agent）
        if sub_history:
            _cur_sh = st.get("sub_history") or {}
            for _k, _v in sub_history.items():
                if isinstance(_v, list) and _v and _v != _cur_sh.get(_k):
                    _cur_sh[_k] = _v
            st["sub_history"] = _cur_sh
        # 恢复 @子Agent → 主 Agent 上下文的同步游标（与 sub_history 同步落盘恢复）
        if sub_synced:
            _cur_ss = st.get("sub_synced") or {}
            for _k, _v in sub_synced.items():
                if isinstance(_v, int) or (isinstance(_v, float) and _v.is_integer()):
                    if int(_v) > int(_cur_ss.get(_k) or 0):
                        _cur_ss[_k] = int(_v)
            st["sub_synced"] = _cur_ss
        # 恢复会话所属工作流（@工作流 会话级切换后持久化，重启/切回沿用）
        if wf and wf != st.get("workflow"):
            st["workflow"] = wf
        # 恢复会话自定义 Agent（@agent 会话级切换后持久化，重启/切回沿用）
        if ag and ag != st.get("agent"):
            st["agent"] = ag
        # 恢复会话共享上下文空间 id（工作团：@工作流 切换/团队激活后持久化）
        if space and space != st.get("space"):
            st["space"] = space
            try:
                from zhuzhu_Copilot.core import agent_context
                if agent_context.has_space(space):
                    agent_context.set_active(space)
            except Exception:
                pass
        # 恢复重试所需信息（重启/切回后最后一条 AI 气泡的重试按钮可正常重发）
        if last_payload:
            st["last_payload"] = last_payload
        if isinstance(regen_idx, int) and regen_idx >= 0:
            st["regenerate_index"] = regen_idx
        # 恢复的工作流与已建引擎不一致 → 热插拔重建（保留已读历史）
        eng = st.get("engine")
        if st.get("workflow") and eng is not None \
                and getattr(eng, "workflow", None) != st["workflow"]:
            self._rebuild_engine(sid)
            eng = st.get("engine")
        # 恢复的自定义 Agent 与引擎 persona 不一致 → 更新引擎人格
        _persona = self._agent_persona(st.get("agent"))
        if eng is not None and getattr(eng, "persona", None) != _persona:
            eng.persona = _persona
        # 读盘期间用户已发送/排队/后台生成产生了实时内容 → 以实时内容为准，不覆盖。
        # 竞态合并：若内存态 rows 中尚无任何 AI 轮次（历史未加载、只有读盘期间新增的
        # 用户消息/排队内容）而磁盘有完整历史 → 磁盘历史 + 内存态增量合并，
        # 避免"切换后立刻发消息"把磁盘历史丢弃（否则该会话历史消失）。
        if st["segments"] or st["rows"] or st["queued"]:
            if rows and not any(r.get("type") == "ai" for r in st["rows"]):
                disk_msgs = list(ums)
                # 内存态 user_msgs 在竞态下可能不含磁盘旧消息（切换后立即发送），
                # 也可能以磁盘旧消息开头（加载后新增）→ 按内容过滤出磁盘没有的新增
                extra = [u for u in st["user_msgs"] if u not in set(disk_msgs)]
                merged = [dict(r) for r in rows]
                for u in extra:
                    merged.append({"type": "user", "text": u})
                st["user_msgs"] = disk_msgs + list(extra)
                st["rows"] = merged
                st["history_segments"] = [
                    seg for seg in (segs or [])
                    if not (seg.get("type") == "mark" and seg.get("html") == "已停止")
                ]
            st["loaded"] = True
            self._bind_sess(sid)
            self._render_history_all()
            self._end_badge_shown = False
            self._refresh_session_combo()
            self._update_welcome()
            self._scroll_bottom()
            self._update_queue_bar()
            self._sync_task_ui()   # 该会话可能有正在后台运行的任务：恢复其进行中 UI
            return
        st["user_msgs"] = list(ums)
        st["rows"] = list(rows)
        # 加载时过滤掉旧版本中持久化的“已停止”提示小字，避免重启后仍显示
        st["history_segments"] = [
            seg for seg in (segs or [])
            if not (seg.get("type") == "mark" and seg.get("html") == "已停止")
        ]
        st["segments"] = []
        st["loaded"] = True
        self._bind_sess(sid)
        # 旧版文件无 rows / rows 缺最后一轮（旧版 _write_ui_json 漏追加未归档回复）：
        # 按段流重建交错行自愈，保证本会话后续持久化不回退
        _hist = st.get("history_segments") or []
        if not self._rows or (_hist and _hist[-1].get("type") != "split"
                              and self._rows and self._rows[-1].get("type") != "ai"):
            self._rows = self._reconstruct_rows()
        self._commit_sess()
        self._render_history_all()   # 用户气泡与 AI 回复按轮次交错重绘（每条 AI 回复一个气泡）
        self._end_badge_shown = False
        self._refresh_session_combo()
        self._update_welcome()
        self._scroll_bottom()
        self._update_queue_bar()
        self._update_wf_label()   # 恢复会话所属工作流后刷新顶部标签
        self._sync_task_ui()      # 该会话可能有正在后台运行的任务：恢复其进行中 UI

    def _is_blank_session(self) -> bool:
        """当前会话是否为空白「新对话」：初始名且没有任何内容/任务/排队/历史。
        用于阻止重复新建空会话（连点新对话按钮会堆出一串同名空会话）。"""
        if not self._session_id or self._task_active or self._session_name != "新对话":
            return False
        if self._user_msgs or self._segments or self._history_segments or self._rows:
            return False
        st = self._sess.get(self._session_id) or {}
        if st.get("queued") or st.get("task_active"):
            return False
        eng = st.get("engine") or self._engine
        if eng is not None and eng._messages and len(eng._messages) > 1:
            return False   # 引擎已积累对话历史（首条为 system prompt）
        return True

    def _new_session(self, *_):
        """新开对话：保存当前 → 创建空会话（当前会话后台任务不中断，多对话并发）。
        新会话沿用上一个对话实际生效的工作流（会话级 @工作流 绑定优先，否则全局激活），
        无需在新对话里再次 @切换；要换工作流仍可用 @工作流 或在设置页切换。
        当前已是空白「新对话」时直接忽略，避免无限堆出空会话。"""
        if self._is_blank_session():
            return
        self._commit_sess()
        self._persist_current()
        # 继承工作流须在切换 self._session_id 之前取（否则读到的是新会话的空状态）
        prev_wf = agent_workflow.resolve_workflow(
            (self._sess.get(self._session_id) or {}).get("workflow")
            or agent_workflow.active_workflow())
        # 新建对话沿用上一个对话的工作目录（对话流：每对话独立工作目录）
        s = self._create_session(workdir=self._effective_workdir(self._session_id))
        self._session_id = s["id"]
        self._record_last_session()   # 新开对话也作为最后停靠页面记录
        self._session_name = "新对话"
        st = self._new_sess_state(s["id"])
        # 新会话从零开始，不存在"未加载完成覆盖磁盘历史"的竞态：
        # 立即置 loaded=True，否则首条消息后防护②会把所有后续落盘（任务结束/
        # 自动备份/关闭）全部拦截，磁盘永远停在首条消息快照 → 重启后长对话消失。
        st["loaded"] = True
        # 新对话沿用上一对话的工作流（会话级绑定则继续绑定，原为跟随全局则跟随全局）
        st["workflow"] = prev_wf
        self._sess[s["id"]] = st
        self._bind_sess(s["id"])
        self._engine = None   # 新会话暂无引擎：复位引用，避免旧引擎上下文写入画到新会话
        self.token_label.setText("0 tk")   # 新对话无任何 token 消耗：立即复位计数
        self._ai_bubble = None
        self._seg_cache.clear()
        self._hide_spinner()
        while self.msg_lay.count() > 1:
            item = self.msg_lay.takeAt(0)
            self._free_layout_item(item)
        self._bubble_widgets = []
        self._bubble_segs = {}
        self._msg_nav_clear()   # 新会话清空定位圆点
        self._refresh_session_combo()
        self._update_welcome()
        self._scroll_bottom()
        self._update_queue_bar()
        # 新建对话：todos 独立窗口刷新为该会话自己的任务清单（无任务显示提示语）
        self.todos_win.update_todos(agent_tools.load_todos())
        self._update_wf_label()   # 新对话已沿用工作流，刷新顶部标签

    def _session_name_source(self, sid: str) -> str:
        """该会话名字的来源：ai=AI 起名 / manual=用户或 Agent 指定 / local=首条消息截断"""
        for x in self._load_session_list():
            if x.get("id") == sid:
                return str(x.get("name_source") or "")
        return ""

    def _apply_session_name(self, sid: str, title: str, source: str = "ai"):
        """把标题写入指定会话（AI 起名 / Agent 改名 / 本地兜底共用），并持久化 + 刷新下拉。
        name_source 记录来源：之后再自动命名时不会覆盖 AI/人工给定的名字。

        长度策略：本地兜底名（source=local）是首条消息摘要，保留 20 字软截断防超长；
        AI 起名 / 用户或 Agent 改名（source=ai/manual）完整保留，不做长度截断。"""
        lim = 20 if source == "local" else 0
        name = agent_llm.clean_title(title, lim)
        if not sid or not name:
            return False
        lst = self._load_session_list()
        hit = False
        for x in lst:
            if x.get("id") == sid:
                x["name"] = name
                x["name_source"] = source
                hit = True
        if not hit:
            return False
        self._save_session_list(lst)
        if sid == self._session_id:
            self._session_name = name
        self._refresh_session_combo()   # 下拉列出全部会话名：后台会话改名也要刷新
        return True

    def _auto_name_session(self, text: str):
        """会话命名：先用本地规则即时起一个可读名字（下拉立刻有名字，无需等网络），
        再后台调真实 API 让 AI 起更贴切的标题，成功则覆盖（失败保持本地名）。

        不覆盖 AI/人工已定的名字（name_source=ai/manual）：用户或 Agent 改名后
        后续消息不再触发自动改名。每个会话只尝试一次 AI 起名，避免反复打扰上游。"""
        if not (text or "").strip():
            return
        sid = self._session_id
        src = self._session_name_source(sid)
        if src in ("ai", "manual"):
            return
        # 本地兜底名 = 首条消息摘要：保留 20 字软截断防超长（AI 起名不受此限制）
        name = agent_llm.clean_title(text, 20)
        if name and src != "local":
            self._apply_session_name(sid, name, source="local")
        if src == "local" or getattr(self, "_name_ai_started", None) == sid:
            return                      # 本会话已试过 AI 起名：不再重复请求
        self._name_ai_started = sid
        threading.Thread(target=self._ai_title_worker, args=(sid, text),
                         daemon=True).start()

    def _ai_title_worker(self, sid: str, text: str):
        """后台线程：真实 API 生成会话标题 → 经信号回主线程写入（失败静默保持本地名）"""
        try:
            title = agent_llm.generate_session_title(text)
        except Exception:
            title = ""
        if title:
            self.sess_name_signal.emit(sid, title)

    def _on_ai_session_title(self, sid: str, title: str):
        """主线程：AI 起名结果落库（完整保留，不截断；仅覆盖本地兜底名，不覆盖用户/Agent 已定的名字）"""
        src = self._session_name_source(sid)
        if src in ("manual",):
            return
        self._apply_session_name(sid, title, source="ai")

    # ---------- 工作目录（会话级持久化 + 全局默认，设置在设置页调整） ----------
    def _session_workdir(self, sid: str) -> str:
        """读取指定会话的独立工作目录（未分配返回空串）"""
        lst = self._load_session_list()
        s = next((x for x in lst if x.get("id") == sid), None)
        return str((s or {}).get("workdir") or "").strip()

    def _effective_workdir(self, sid: str) -> str:
        """某会话实际生效的工作目录：会话专属优先，否则回退全局默认"""
        wd = self._session_workdir(sid)
        if wd:
            return wd
        return str(self._settings.value("agent_workdir", "")).strip()

    def _set_session_workdir(self, sid: str, wd: str):
        """写入指定会话的独立工作目录（设置页-对话流保存时调用）"""
        lst = self._load_session_list()
        for x in lst:
            if x.get("id") == sid:
                x["workdir"] = (wd or "").strip()
                break
        self._save_session_list(lst)

    def _restore_workdir(self):
        """启动/设置保存后恢复当前会话的工作目录；无有效目录则使用默认提示"""
        wd = self._effective_workdir(self._session_id)
        if wd and os.path.isdir(wd):
            self._apply_workdir(wd)
        else:
            self._apply_workdir("")
        self._update_wd_hint()

    def _update_wd_hint(self):
        """当前对话未设置专属工作目录时，在输入框上方显示「去设置」提示条
        （会话切换 / 设置保存 / 启动恢复后刷新显隐）"""
        try:
            w = self.wd_hint
        except AttributeError:
            return
        try:
            has = bool(self._session_workdir(self._session_id).strip())
        except Exception:
            has = True
        w.setVisible(not has)

    def _open_workdir_settings(self, *_):
        """提示条「去设置」按钮：打开设置对话框并定位到「对话流」页当前会话行闪烁提醒"""
        try:
            self._open_settings(highlight_sid=self._session_id)
        except Exception:
            self._open_settings()

    def _request_git_refresh(self):
        """防抖请求工作目录/仓库刷新：合并连续触发为单次，让出 UI 事件循环，
        避免设置保存路径多次起 git 子进程/扫目录造成卡顿。
        __init__ 早期（_restore_workdir 先于 timer 创建）调用时 timer 尚不存在，
        直接同步刷新一次兜底，保证启动恢复的工作目录必定渲染到 git/文件树面板。"""
        t = getattr(self, "_git_refresh_timer", None)
        if t is not None:
            t.start()
        else:
            self._refresh_git_and_worktree()

    def _apply_workdir(self, d: str):
        """应用工作目录：写入 agent_tools 全局，并立即刷新 git 面板与左侧文件树
        （工作目录变更后仓库根/文件列表随之改变，必须及时更新）。
        同时立即刷新各会话引擎的系统提示（含【当前工作目录】行），确保正在进行的
        AI 对话下一次决策就作用于新目录，而非停留在旧目录上下文里继续用旧绝对路径。"""
        d = (d or "").strip()
        prev = agent_tools.get_workdir()
        # 同目录且已渲染成功 → 直接跳过，避免设置保存双路径（_AgentSettingsDialog 保存 +
        # _open_settings→_apply_agent_settings→_restore_workdir）重复扫描/起 git 子进程造成卡顿
        if d and d == prev and getattr(self, "_rendered_workdir", None) == d:
            return
        agent_tools.set_workdir(d)
        self._refresh_engines_workdir()
        # 工作目录已变更：先置为"未渲染"（stale），真正刷新成功后才更新；
        # 若刷新未完成（面板未就绪等），周期刷新会在 5s 内据此自愈重拉。
        self._rendered_workdir = None
        # 统一走防抖 timer：避免 _refresh_git_and_worktree（同步）与 _request_git_refresh
        # （异步 timer）同时触发 git_win.refresh() 造成并发竞态崩溃；
        # _sync_wt_win / _sync_git_win 内部已含 show 时的 refresh，无需此处再调。
        self._request_git_refresh()
        try:
            self._sync_wt_win()
            self._sync_git_win()
        except Exception:
            pass

    def _refresh_engines_workdir(self):
        """工作目录变更后，立即把新目录写进各会话引擎的 system 提示（原位替换 messages[0]）。
        引擎每轮本就会重算系统提示，这里提前刷新避免"切目录后 AI 仍沿用旧目录"的观感，
        且对进行中的任务下一轮工具调用立即生效。空闲/无引擎时静默跳过。"""
        for st in self._sess.values():
            eng = st.get("engine")
            if eng is None:
                continue
            try:
                new_prompt = eng._system_prompt()
                msgs = getattr(eng, "_messages", None)
                if msgs and msgs[0].get("role") == "system":
                    if msgs[0].get("content") != new_prompt:
                        msgs[0]["content"] = new_prompt
            except Exception:
                pass

    def _refresh_git_and_worktree(self):
        """工作目录变更后：立即刷新 git 分支/提交面板与左侧文件树。
        无论面板是否可见都刷新（隐藏时仅更新模型，显示即呈现新内容）——
        否则弹窗/面板隐藏期间切换目录，恢复显示后仍是旧内容。
        同时同步已知仓库根/时间戳，避免周期刷新重复重建。

        git 仓库探测（向上逐层找 .git）与工作树根层扫描放后台线程，
        避免设置工作目录（含大型/多图片目录）时同步扫描卡死主线程。"""
        from zhuzhu_Copilot.core import agent_git, agent_tools
        # _retheme 重建期跳过刷新：4 个子窗口已被关闭隐藏，步骤 9 重新显示时会各自
        # refresh 一次（避免同一份工作树/git 数据在重建期间被重复扫描/拉取子进程）
        if getattr(self, "_retheming", False):
            return
        # 后台线程：git 根/时间戳探测（可能向上 walk 多层目录 + stat），
        # 完成后经信号回主线程更新已知值（周期刷新据此判断是否需要重建）
        def _probe():
            try:
                root = agent_git.repo_dir()
                mtime = agent_git.repo_mtime()
            except Exception:
                root, mtime = "", 0.0
            try:
                self.git_probe_signal.emit(root, mtime)
            except Exception:
                pass
        threading.Thread(target=_probe, daemon=True).start()
        ww = getattr(self, "wt_win", None)
        if ww is not None:
            try:
                ww.refresh()
            except Exception:
                pass
        gw = getattr(self, "git_win", None)
        if gw is not None:
            try:
                gw.refresh()
            except Exception:
                pass
        # 记录本次实际渲染所依据的工作目录，供周期刷新检测目录漂移并自愈
        try:
            self._rendered_workdir = agent_tools.get_workdir()
        except Exception:
            self._rendered_workdir = None

    # ---------- 消息气泡 ----------
    @staticmethod
    def _fade_in(widget: QWidget, parent: QWidget):
        """气泡淡入动画（增强体验）。

        动画结束后**必须摘掉不透明度效果**：QGraphicsOpacityEffect 会把整个控件子树
        重定向到离屏合成，常驻在一个含几十个子控件的回合容器上，会让每次重绘
        （滚动、流式刷新、输入）都变得很贵 —— 这是「小任务也卡顿」的主要来源之一。
        """
        eff = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(eff)
        anim = QPropertyAnimation(eff, b"opacity", parent)
        anim.setDuration(220)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)

        def _drop_effect():
            try:
                widget.setGraphicsEffect(None)   # 传 None 会顺带销毁 effect
            except RuntimeError:
                pass      # 控件已被回收（会话切换/清空）：无需处理

        anim.finished.connect(_drop_effect)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    def _bubble_max_width(self) -> int:
        """聊天气泡最大宽度：随窗口自适应（至少 560，最大化时放大）"""
        return max(560, int(self.width() * 0.72))

    # 左侧停靠面板列宽（worktree/git/todos 均为 280px）+ 右侧留白
    _PANEL_COL = 280 + 14
    _DOCK_PANEL_GAP = 8   # 栏内「名义间距」：仅用于份额口径（实际间距为 0，
                          # 释放出的空间全部由 Git 面板吸收占满）
    # 右侧代码预览面板宽（git 面板 1.5 倍再扩大 1.5 倍减 30% = 441px）+ 左侧留白
    _CODE_COL = int(280 * 1.5 * 1.5 * 0.7) + 14

    def _side_panels_active(self) -> bool:
        """是否有任一左侧停靠面板（工作树/git/任务清单）启用"""
        return self._wt_enabled() or self._git_enabled() or self._todos_enabled()

    def _code_enabled(self) -> bool:
        """代码预览面板是否启用（设置-通用页开关，默认启用）"""
        return str(app_identity.qsettings()
                   .value("agent_show_code", "1")).strip().lower() in ("1", "true", "yes")

    def _apply_reserve(self):
        """最大化/全屏时让主面板内容为左右停靠面板让位：左侧给工作树/git/todos
        三面板的停靠列、右侧给代码预览面板，实现「分割主面板左右区域」让子面板
        各自停在预留条带内、与主内容互不重合（见 _left_stack_origin 在最大化的
        钉左缘语义）。普通窗口/还原时清零边距（子面板贴在主面板外侧，无需让位）。
        幂等：仅在边距变化时修改，主题重建/重复调用无副作用。"""
        root = getattr(self, "_root_lay", None)
        if root is None:
            return
        fullscreen = self._is_fullscreen()
        docked = self._panel_mode() == "dock"
        # dock（融入主面板）模式下子面板已嵌入主窗口内部，无需再为外部浮窗让位，
        # 否则根布局 margin 与 dock 栏宽度叠加会把中间对话区严重挤压
        left = self._PANEL_COL if (fullscreen and not docked and self._side_panels_active()) else 0
        right = self._CODE_COL if (fullscreen and not docked and self._code_enabled()) else 0
        try:
            cur = root.contentsMargins()
            if (cur.left(), cur.right()) != (left, right):
                root.setContentsMargins(left, cur.top(), right, cur.bottom())
        except Exception:
            pass

    def _panel_frameless(self) -> bool:
        """主面板是否为无边框窗口（自定义玻璃模式）。系统标题栏模式下套 setMask
        会破坏原生标题栏/阴影/缩放，需由系统圆角承载，故此时不切蒙版。"""
        try:
            return bool(self.windowFlags() & Qt.WindowType.FramelessWindowHint)
        except Exception:
            return False

    def _apply_window_round(self):
        """主面板圆角：无边框模式下应用 SetWindowRgn（影响 DWM 窗口形状）。
        配合 paintEvent 绘制圆角背景，确保四角真正圆角化。
        拖动结束后补套 Acrylic 毛玻璃，修复启动时/切换主题后面板透明问题。"""
        if not self._panel_frameless():
            return
        try:
            from zhuzhu_Copilot.core import agent_ui_ux
            agent_ui_ux.apply_rounded_window(self, 18)
            # 补套 Acrylic 毛玻璃：setWindowFlags/setStyleSheet 可能清除 Acrylic 状态
            agent_ui_ux.apply_acrylic(self)
        except Exception:
            pass

    def _bubble_min_width(self) -> int:
        """聊天气泡最小宽度：全屏时至少覆盖半页宽（不超过最大宽度）"""
        return min(self._bubble_max_width(), int(self.width() * 0.5))

    def _ai_turn_max_width(self) -> int:
        """AI 回合容器最大宽度：铺满消息区内容宽度（demo 中 .ai-turn 铺满线程宽度，
        与用户气泡的 84% 窄气泡形成对比）。取不到视口时回退面板宽度。"""
        try:
            avail = int(self.msg_area.viewport().width())
        except Exception:
            avail = int(self.width())
        try:
            m = self.msg_lay.contentsMargins()
            avail -= m.left() + m.right()
        except Exception:
            pass
        return max(240, avail - 8)

    def _ai_img_width(self) -> int:
        """AI 回合内截图缩略图宽度：回合内容宽度的 40%（旧实现按「气泡最大宽度」折算，
        回合改为铺满宽度后同比换算，避免截图在宽回合里显得过小）。"""
        return max(200, int(self._ai_turn_max_width() * 0.4))

    def _chat_icon(self, kind: str, size: int, color: str) -> QIcon:
        """事件流气泡的图标入口：按 kind 显式分流，**绝不允许渲染空白图标**。

        参数名 `kind` 对工具调用行传的是真实工具名（如 `run_command`），技能/并行等行
        传的是 UI 伪 kind（如 `skill`/`parallel`）。分流顺序：
          1. 工具名或 UI 伪 kind（`tool_icons.has_icon`）→ 族底图 + 动作角标组合图标；
          2. `_line_icon` 已实现的通用线条 kind（clock/chev/think/tool 等）→ 既有线条图标；
          3. 其余未登记 kind（含历史段里未带 ico 的展示文案）→ 组合图标兜底，不会空壳。
        """
        if tool_icons.has_icon(kind):
            return tool_icons.tool_icon(kind, size, color)
        if kind in _LINE_ICON_KINDS:
            return _line_icon(kind, size, color)
        return tool_icons.tool_icon(kind, size, color)

    def _chat_style(self) -> "chat_bubbles.ChatStyle":
        """事件流气泡的样式快照：几何取自 tokens/demo，颜色取当前主题色板。

        与 demo CSS 变量的映射（按应用现有 dark/light 色板映射，不引入新配色）：
        card=AI_BG · border=BORDER · line/dash=BORDER_SOFT · title-fg=TEXT ·
        body-fg=TEXT_DIM · accent=ACCENT · icon-shell=HOVER · user-bg=USER_BG ·
        cmd-fg=TEXT · ok-fg=LINK_COLOR（demo 用蓝，遵守四色约束不用绿）· bg=BG。

        层次色（think_* / tag_* / tool_shell / out_*）同样**全部由色板派生**，公式统一为
        「把深蓝或描边色按固定比例压在目标底色上」，模块内依旧没有任何颜色字面量：
        · 思考气泡：底/描边各掺一点深蓝，与正文所在的 BG 面拉开层次、可一眼区分；
        · tag 胶囊：PLANNING 用中性描边色、EXEC 用深蓝 —— 两个阶段不再同色；
        · 工具/技能/插件图标：淡灰（色板里的中性灰 TEXT_DIM），配合中性描边灰微调的图标壳，
          与深蓝只留给「状态/强调」保持一致；
        · 输出区：淡蓝字（LINK_COLOR）+ 深蓝竖线（ACCENT），让「工具调用 → 输出」有归属感。
        """
        return chat_bubbles.ChatStyle(
            card=AI_BG,
            border=BORDER,
            border_soft=BORDER_SOFT,
            dash=BORDER_SOFT,
            text=TEXT,
            text_dim=TEXT_DIM,
            accent=ACCENT,
            muted=TEXT_DIM,
            icon_shell=HOVER,
            icon_color=ICON_GRAY,
            tag_bg=_mix_hex(BORDER_SOFT, AI_BG, 0.55),
            tag_fg=TEXT_DIM,
            user_bg=USER_BG,
            user_fg="#FFFFFF",
            cmd_fg=TEXT,
            ok_fg=LINK_COLOR,
            hover=HOVER,
            panel=PANEL,
            bg=BG,
            think_bg=_mix_hex(ACCENT, AI_BG, 0.10),
            think_border=_mix_hex(ACCENT, BORDER, 0.30),
            tag_plan_bg=_mix_hex(BORDER_SOFT, AI_BG, 0.55),
            tag_exec_bg=_mix_hex(ACCENT, AI_BG, 0.32),
            tool_shell=_mix_hex(BORDER_SOFT, HOVER, 0.22),
            out_fg=LINK_COLOR,
            out_line=ACCENT,
        )

    def _topbar_wide(self) -> bool:
        """窗口足够宽（≥1100px）时顶栏显示完整文案，否则紧凑防挤压"""
        return self.width() >= 1100

    def _apply_topbar_layout(self):
        """顶部工具栏随窗口宽度自适应：宽窗口显示完整文字，窄窗口紧凑。
        会话名下拉宽度跟随当前会话名（收起态省略号截断一起消除）"""
        wide = self._topbar_wide()
        if wide:
            self.session_combo.setMinimumWidth(200)
            self.session_combo.setMaximumWidth(360)
        else:
            self.session_combo.setMinimumWidth(130)
            self.session_combo.setMaximumWidth(280)
        self._fit_session_combo_width()
        self._refresh_meta()   # token 文本按当前模式重渲染

    def _fit_session_combo_width(self):
        """会话名下拉按当前文本自适应宽度：QComboBox 收起态文本超宽会用省略号截断，
        这里用字体宽度动态抬高最小宽度（封顶当档位最大宽度），保证名字完整显示"""
        try:
            name = self.session_combo.currentText() or ""
            need = self.session_combo.fontMetrics().horizontalAdvance(name) + 56  # 下拉箭头+左右内边距
            floor = self.session_combo.minimumWidth()
            cap = self.session_combo.maximumWidth()
            self.session_combo.setMinimumWidth(min(max(need, floor), cap))
        except Exception:
            pass

    def paintEvent(self, event):
        """主面板圆角背景 + 增强边缘高光：自绘圆角半透明背景，
        扩大绘制区域以覆盖 DWM 圆角抗锯齿边缘，消除灰色边框。
        边缘高光增强玻璃质感，模拟 iOS 26 液态玻璃效果。"""
        if not self._panel_frameless():
            super().paintEvent(event)
            return
        try:
            from PyQt6.QtCore import QRectF
            from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            # 1. 扩大绘制区域，超出窗口边界 2px，确保 DWM 圆角抗锯齿区域被覆盖
            path = QPainterPath()
            enlarged = self.rect().adjusted(-2, -2, 2, 2)
            path.addRoundedRect(QRectF(enlarged), 20, 20)
            p.setClipPath(path)
            p.fillRect(enlarged, QColor(BG))
            p.end()

            # 2. 绘制增强边缘高光（玻璃质感）
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            # 外缘亮线（玻璃边缘反光）
            pen1 = QPen(QColor(255, 255, 255, 80), 1.5)
            p.setPen(pen1)
            p.drawRoundedRect(QRectF(self.rect().adjusted(0.5, 0.5, -0.5, -0.5)), 18, 18)
            # 内缘亮线（玻璃厚度感）
            pen2 = QPen(QColor(255, 255, 255, 35), 1)
            p.setPen(pen2)
            p.drawRoundedRect(QRectF(self.rect().adjusted(2, 2, -2, -2)), 16, 16)
            # 顶部受光边线（更亮）
            pen3 = QPen(QColor(255, 255, 255, 120), 1)
            p.setPen(pen3)
            p.drawLine(20, 1, self.width() - 20, 1)
            p.end()
        except Exception:
            super().paintEvent(event)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        # 无边框（自定义玻璃）模式：窗口本身切圆角；系统标题栏模式不清蒙版
        # （setMask 会破坏原生标题栏/阴影/缩放，需由系统圆角承载）。
        self._apply_window_round()
        # 最大化/还原触发的是 resize 而非 move → 也同步 4 个子面板位置；
        # 多来源反复触发合并为一次，避免每次 resize 都 4×move+raise 造成闪烁
        self._request_side_sync(0)
        # dock 融入模式：栏高随窗口变化 → 合并为一次重算高度封顶
        # （Git 面板持续吸收工作树/任务清单定高后释放的余量，栏内不留空白）
        if getattr(self, "_applied_panel_mode", "") == "dock" \
                and not getattr(self, "_dock_resize_armed", False):
            self._dock_resize_armed = True
            QTimer.singleShot(0, self._dock_resize_flush)
        self._apply_topbar_layout()
        self._apply_reserve()   # 最大化/还原时根布局左右边距
        # 底部输入行等比缩放（随窗口高度变化，全屏自动放大）
        self._apply_bottom_scale()
        # 气泡宽度同步（轻量 setter）；文本/截图重渲染交给防抖定时器合并。
        # 宽度未变化（仅高度变动）时直接跳过，避免最大化↔正常来回切换时反复遍历气泡
        mw = self._bubble_max_width()
        ai_mw = self._ai_turn_max_width()
        if mw == self._last_bw and ai_mw == self._last_ai_bw:
            return
        self._last_bw, self._last_ai_bw = mw, ai_mw
        for b in self._bubble_widgets:
            try:
                if b.property("align") == "ai":
                    # AI 回合铺满内容宽度（demo .ai-turn 铺满线程宽度）：固定宽度，
                    # 并立刻按新宽度重算内容高度（resizeEvent 里也会兜底重算一次）
                    b.setMaximumWidth(ai_mw)
                    if hasattr(b, "relayout_heights"):
                        b.relayout_heights(b.width() or ai_mw)
                else:
                    # 用户气泡按内容自适应宽度（demo .msg 为右侧收窄气泡）
                    b.setMaximumWidth(mw)
            except RuntimeError:
                pass
        self._sync_bubble_heights()   # 宽度变化后按新宽度重算换行高度（含最大化/还原）
        self._resize_timer.start()

    def _apply_bottom_scale(self):
        """底部输入行等比缩放：普通窗口(基准高660)为紧凑值，窗口越高按比例放大"""
        k = max(1.0, self.height() / 660.0)
        if abs(k - self._last_scale) < 0.01:
            return
        self._last_scale = k
        # 仅输入框高度随窗口缩放；输入行右侧圆形按钮（上传/优化/发送）保持
        # 初始固定尺寸，不随窗口缩放——否则 border-radius 固定值不再等于边长
        # 一半，圆形按钮会变形，且窗口最大化时按钮被不合理放大。
        self.input.setMinimumHeight(int(32 * k))
        self.input.setMaximumHeight(int(110 * k))

    def _rebuild_bubbles_after_resize(self):
        """resize 停止后重渲染。字体固定 14px 不随窗口缩放（_font_scale 恒 1.0），
        唯一随宽度变化的是截图缩略图宽度（img_w），因此：
        1. img_w 未跨阈值 → 全部跳过；
        2. 只重建含截图（image 段）的 AI 气泡，其余气泡交给 QLabel 自动重排。"""
        img_w = self._ai_img_width()
        if img_w == self._last_img_w:
            return
        self._last_img_w = img_w
        ai_bubbles = [b for b in self._bubble_widgets
                      if b.property("align") == "ai" and self._bubble_alive(b)]
        for b, g in zip(ai_bubbles, self._split_groups()):
            if not any(seg["type"] == "image" for seg in g):
                continue
            self._render_ai_frame(b, g)   # 截图宽度随回合内容宽度变化 → 重渲染该回合

    def _ensure_taskbar_entry(self):
        """强制任务栏缩略图：带 parent 的顶层窗口在 Windows 属于 owned window，
        默认不显示任务栏按钮；显式追加 WS_EX_APPWINDOW 扩展样式后，
        最小化也会保留独立任务栏缩略图，可点击定位/恢复面板。"""
        try:
            GWL_EXSTYLE = -20
            WS_EX_APPWINDOW = 0x00040000
            hwnd = int(self.winId())
            user32 = ctypes.windll.user32
            ex = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
            if not (ex & WS_EX_APPWINDOW):
                user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ex | WS_EX_APPWINDOW)
        except Exception:
            pass

    def _todos_enabled(self) -> bool:
        """任务清单窗口是否启用（设置-通用页开关，默认启用）"""
        return str(app_identity.qsettings()
                   .value("agent_show_todos", "1")).strip().lower() in ("1", "true", "yes")

    def _git_enabled(self) -> bool:
        """Git 分支面板是否启用（设置-通用页开关，默认启用）"""
        return str(app_identity.qsettings()
                   .value("agent_show_git", "1")).strip().lower() in ("1", "true", "yes")

    def _wt_enabled(self) -> bool:
        """工作树面板是否启用（设置-通用页开关，默认启用）"""
        return str(app_identity.qsettings()
                   .value("agent_show_worktree", "1")).strip().lower() in ("1", "true", "yes")

    def _top_bar_offset(self) -> int:
        """顶部菜单栏高度（面板坐标底边 + 留白）：最大化时 todos 下移到其下方"""
        try:
            return self.session_combo.mapTo(self, self.session_combo.rect().bottomLeft()).y() + 14
        except Exception:
            return 58

    def _is_fullscreen(self) -> bool:
        """是否全屏/最大化：面板自身、顶层窗口或父窗口（MainWindow）任一最大化即视为全屏。
        真实场景中用户常最大化主窗口，面板（子窗口）的 isMaximized 可能为 False。"""
        if getattr(self, "_glass_maximized", False):
            return True
        if self.isMaximized():
            return True
        tl = self.window()
        if tl is not None and tl is not self:
            try:
                if tl.isMaximized():
                    return True
            except Exception:
                pass
        parent = self.parentWidget()
        while parent is not None:
            try:
                if parent.isMaximized():
                    return True
            except Exception:
                pass
            parent = parent.parentWidget()
        return False

    def _left_stack_width(self) -> int:
        """左侧三个子面板（wt/git/todos）的实际最大宽度（含 UI/UX 包自定义宽度）。
        左锚点须按最宽子面板计算，否则子面板右移、覆盖并挤压主面板。"""
        widths = []
        for name in ("wt_win", "git_win", "todos_win"):
            w = getattr(self, name, None)
            if w is None:
                continue
            wd = w.width()
            if wd > 0:
                widths.append(wd)
            else:
                widths.append(getattr(w, "WIDTH", 280))
        return max(widths) if widths else 280

    def _left_stack_origin(self) -> tuple:
        """左侧面板列锚点(x, y)：git/worktree/todos 共列于此锚点下方堆叠。
        统一以**主面板自身**的全局左上角为锚计算左停靠位：
        - 主面板真正全屏/最大化（独自铺满屏幕）时，其左上角即屏幕左上角，锚定屏幕
          菜单栏下方的左缘；此时根布局已由 _apply_reserve 让出左列位，面板停在
          预留条带内、与主内容互不重合。
        - 主面板为普通窗口（即使父窗口 MainWindow 最大化）时，停靠在其左侧外；当
          左侧空间放不下整列（撞到屏幕左缘）时，模拟**物理碰撞**——整列被屏幕左缘
          "顶住"，始终保持左对齐钉在屏幕最左缘（max(0, …) 钳制）。
        左锚点按子面板实际宽度计算，避免自定义 UI/UX 加宽子面板后右移挤压主面板。
        注意：上一版在祖先（MainWindow）最大化时误返回屏幕左上角，使工作树飞出面板
        左侧停在屏幕角落；本版改为只要主面板自身未铺满屏幕就锚定面板左侧。"""
        base = self.mapToGlobal(self.rect().topLeft())
        stack_w = self._left_stack_width()
        left_x = max(0, base.x() - stack_w - 5)
        if self._is_fullscreen():
            # 主面板铺满屏幕：其左上角≈屏幕左上角，y 落在菜单栏下方；x 让出左列位
            y = base.y() + self._top_bar_offset() + 5
            # base.x() 已是屏幕左缘（0），left_x=max(0,…)=0，贴合屏幕左缘
            return left_x, y
        return left_x, base.y()

    # ---------- 自定义按钮注册 API（与 UI/UX 解耦） ----------
    # 注册的自定义按钮始终渲染在面板顶部按钮栏：无论当前/以后切换任何 UI/UX 包、
    # 甚至完全重构界面，按钮都保持可见且回调可用。注册表存放在面板实例上，
    # UI 重建（切换主题/切换 UI/UX 包）不会清空，重建后自动重新渲染。
    # 面板侧 action → 方法名映射（供 register_panel_btn 工具的 action 解析）：
    # 必须与 agent_tools._PANEL_BTN_ACTIONS 的 action 集合保持一致，
    # 新增 action 需同时更新两侧；不一致时 _apply_btn_reg 会跳过注册并告警。
    _BTN_ACTIONS = {
        "send": "_on_action_clicked",     # 发送 / 停止当前任务（随状态切换）
        "new_session": "_new_session",    # 新建对话
        "settings": "_open_settings",     # 打开设置
        "attach": "_pick_attachments",    # 选择附件
        "optimize": "_on_optimize_clicked",  # 优化提示词
        "clear_chat": "_clear_chat",      # 清空并删除当前对话
    }

    def register_btn(self, btn_id: str, text: str = "", callback=None,
                     tooltip: str = "", order: int = 0, icon: str = "",
                     stylesheet: str = "", fixed_size=None, visible: bool = True):
        """注册自定义按钮（稳定接口，供插件/工作流/AI build_ui 调用）：
        - btn_id 唯一标识；同一 id 重复注册仅更新参数，不重复创建按钮；
        - callback：点击回调（无参数 callable）。按钮渲染与回调均与 UI/UX 解耦，
          任何界面下都自动出现在面板顶部按钮栏；
        - icon：可选 Lucide 线条图标名（如 "send"/"trash"/"new"/"settings"/"magic"/"plus"），
          经 _line_icon 渲染并着当前主题色；
        - stylesheet：可选自定义 QSS，覆盖默认玻璃样式（深度自定义开发者按钮）；
        - fixed_size：(w, h) 可选固定尺寸；
        - visible：False 时注册但隐藏，之后可再 register 更新为 True 显示；
        - 返回按钮注册数据 dict。
        """
        if not btn_id:
            raise ValueError("register_btn 需要非空 btn_id")
        d = self._btn_registry.get(btn_id) or {}
        d.update({"text": text or "", "callback": callback,
                  "tooltip": tooltip or "", "order": int(order or 0),
                  "icon": icon or "", "stylesheet": stylesheet or "",
                  "fixed_size": fixed_size, "visible": bool(visible)})
        b = d.get("_btn")
        if b is not None:   # 已渲染：仅更新文字/图标/提示/回调/样式
            try:
                if d["icon"]:
                    b.setIcon(_line_icon(d["icon"], 18, TEXT))
                    b.setText(d["text"] or "")
                else:
                    b.setText(d["text"] or "·")
                if d["tooltip"]:
                    b.setToolTip(d["tooltip"])
                if d["stylesheet"]:
                    b.setStyleSheet(d["stylesheet"])
                if d["fixed_size"]:
                    try:
                        b.setFixedSize(int(d["fixed_size"][0]),
                                       int(d["fixed_size"][1]))
                    except Exception:
                        pass
                try:
                    b.clicked.disconnect()
                except TypeError:
                    pass
                if callback:
                    b.clicked.connect(lambda _=False, f=callback: self._safe_btn_cb(f))
                b.setVisible(d["visible"])
            except Exception:
                pass
            self._btn_registry[btn_id] = d
            return d
        self._btn_registry[btn_id] = d
        self._ensure_custom_btn_bar()   # 增量渲染新按钮
        return d

    def unregister_btn(self, btn_id: str):
        """注销按钮：从注册表与按钮栏移除（同一 id 重新 register 即恢复）。"""
        d = self._btn_registry.pop(btn_id, None)
        if d is None:
            return
        b = d.get("_btn")
        if b is not None:
            lay = getattr(self, "_custom_btn_lay", None)
            if lay is not None:
                try:
                    lay.removeWidget(b)
                except Exception:
                    pass
            try:
                b.deleteLater()
            except Exception:
                pass
        # 注册表清空时隐藏空按钮栏（避免残留空行）；再次注册时 _ensure 会重新显示
        if not self._btn_registry:
            bar = getattr(self, "_custom_btn_bar", None)
            if bar is not None:
                try:
                    bar.hide()
                except Exception:
                    pass

    def list_registered_btns(self) -> list:
        """已注册按钮 id 列表（按 order 升序）。"""
        return sorted(self._btn_registry,
                      key=lambda i: (self._btn_registry[i].get("order", 0), i))

    def _safe_btn_cb(self, f):
        try:
            f()
        except Exception as e:
            import logging
            logging.getLogger(app_identity.APP_SLUG).warning("自定义按钮回调失败: %s", e)

    def _ensure_custom_btn_bar(self):
        """把已注册的自定义按钮渲染为面板顶部按钮栏（与 UI/UX 解耦）：
        任意构建（默认/自定义/完全重构）结束都会调用本方法，保证按钮始终可见。
        按 order 升序渲染（order 越小越靠前，同序按 id 字典序），按钮栏存在时全量
        重建以维持顺序语义（按钮数量极少，重建开销可忽略）。幂等。"""
        if not getattr(self, "_btn_registry", None):
            return
        root = getattr(self, "_root_lay", None)
        if root is None:
            return
        from PyQt6 import sip
        bar = getattr(self, "_custom_btn_bar", None)
        if bar is not None:
            try:
                if sip.isdeleted(bar):   # 已被 _clear_layout/重建清理 → 重建
                    bar = None
            except Exception:
                bar = None
        # 清理已失效的按钮引用（旧栏/旧按钮在重建时已被删除，重新渲染）
        for d in self._btn_registry.values():
            b = d.get("_btn")
            if b is not None:
                try:
                    if sip.isdeleted(b):
                        d["_btn"] = None
                except Exception:
                    d["_btn"] = None
        if bar is None:
            bar = QWidget(self)
            bar.setObjectName("customBtnBar")
            bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            bar.setStyleSheet("QWidget#customBtnBar { background: transparent; }")
            lay = QHBoxLayout(bar)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(6)
            self._custom_btn_bar = bar
            self._custom_btn_lay = lay
            root.insertWidget(0, bar)   # 顶部按钮栏：任何布局都可见
        else:
            lay = self._custom_btn_lay
            # 全量重建按钮并按其 order 排序，保证 order 顺序语义恒生效
            while lay.count():
                item = lay.takeAt(0)
                w = item.widget()
                if w is not None:
                    try:
                        w.deleteLater()
                    except Exception:
                        pass
        lay = self._custom_btn_lay
        for btn_id in sorted(self._btn_registry,
                             key=lambda i: (self._btn_registry[i].get("order", 0), i)):
            d = self._btn_registry[btn_id]
            if not d.get("visible", True):
                continue
            icon = d.get("icon") or ""
            text = d.get("text") or ""
            if icon:
                b = QPushButton(_line_icon(icon, 18, TEXT), text)
            else:
                b = QPushButton(text or "·")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setAutoDefault(False)
            if d.get("stylesheet"):
                b.setStyleSheet(d["stylesheet"])
            else:
                b.setStyleSheet(_BTN_GHOST)
            if d.get("tooltip"):
                b.setToolTip(d["tooltip"])
            fs = d.get("fixed_size")
            if fs:
                try:
                    b.setFixedSize(int(fs[0]), int(fs[1]))
                except Exception:
                    pass
            cb = d.get("callback")
            if cb:
                b.clicked.connect(lambda _=False, f=cb: self._safe_btn_cb(f))
            lay.addWidget(b)
            d["_btn"] = b
        try:
            bar.show()
        except Exception:
            pass

    def _apply_btn_reg(self, payload):
        """主线程应用按钮注册/注销请求（来自 register_panel_btn 工具，经引擎回调路由）。"""
        if not isinstance(payload, dict):
            return
        op = payload.get("op")
        try:
            if op == "register":
                cb = None
                act = payload.get("action")
                m = self._BTN_ACTIONS.get(act) if act else None
                f = getattr(self, m, None) if m else None
                cb = f if callable(f) else None
                if cb is None:
                    # 防白名单漂移死按钮：register 必须带可解析成面板已有方法的 action；
                    # action 缺失或无法解析时跳过注册并告警，避免注册一个点击无反应的
                    # "死按钮"（工具侧 _PANEL_BTN_ACTIONS 与本侧 _BTN_ACTIONS 应一致）。
                    import logging
                    logging.getLogger(app_identity.APP_SLUG).warning(
                        "register_panel_btn: action %r 无法解析到面板方法，已跳过注册", act)
                    return
                self.register_btn(str(payload.get("id") or ""),
                                  text=payload.get("text") or "",
                                  callback=cb,
                                  tooltip=payload.get("tooltip") or "")
            elif op == "unregister":
                self.unregister_btn(str(payload.get("id") or ""))
        except Exception:
            pass

    def _call_panel(self, name: str, method: str, *args):
        """防御式调用子面板方法：AI 自定义子窗口可能未实现对应方法
        （如 todos_win.update_todos），存在才调用，避免运行时崩溃。"""
        w = getattr(self, name, None)
        if w is None:
            return None
        f = getattr(w, method, None)
        if not callable(f):
            return None
        try:
            return f(*args)
        except Exception:
            return None

    def _place_owned(self, w, x, y, force_raise=True):
        """把 owned 子窗口移动到 (x, y)，并抑制「坐标未变仍反复 move」的布局抖动：
        - 坐标变化或当前不可见 → 才 move/show；
        - 始终 raise（owned 窗口可能被最大化主窗压到下层，必须保持可见）。
        频繁的「多次 raise 造成的闪烁」由调用方 _request_side_sync 合并为一次。"""
        try:
            cur = (int(x), int(y))
            prev = getattr(w, "_placed", None)
            moved = prev != cur
            need_show = not w.isVisible()
            if moved and not need_show:
                w.move(*cur)
            elif need_show:
                w.move(*cur)
                w.show()
                moved = True
            w._placed = cur
            if moved or need_show or force_raise:
                w.raise_()
        except Exception:
            pass

    def _request_side_sync(self, delay=0):
        """合并多次独立触发的面板重新定位/抬升为一次：最大化/还原/焦点/二次同步常
        在极短时间内连续调用，若每次都 4×move+raise 会造成左面板消失与持续闪烁。
        用单次定时器聚合，去重后一次性归位。"""
        if getattr(self, "_side_sync_armed", False):
            return
        self._side_sync_armed = True
        QTimer.singleShot(delay, self._flush_side_sync)

    def _panel_size_changed(self, panel, w, h=None, persist: bool = False):
        """面板四边/四角拖拽回调：更新该面板大小（与其他面板相互独立），
        persist=True 时把尺寸写入 QSettings（拖拽释放触发）。

        dock（融入主面板）模式下：拖拽中先临时解除栏内固定宽以便 resize 真正生效
        （避免 setFixedWidth 钳制导致每 tick 布局抖动/卡顿），松手后恢复固定宽并
        重新计算主面板 dock 最小宽（列宽变化同步内容区最小约束）。"""
        try:
            if panel is None:
                return
            w = max(120, min(1600, int(w)))
            h = max(100, min(1400, int(h if h else panel.height())))
            if not persist:
                # 拖拽中：解除固定宽高约束（含 dock 栏内固定宽），让 resize 直接生效
                try:
                    panel.setMinimumWidth(0)
                    panel.setMaximumWidth(65535)
                    panel.setMinimumHeight(0)
                    panel.setMaximumHeight(65535)
                except Exception:
                    pass
            panel._set_panel_size(w, h)
            if persist:
                name = getattr(panel, "objectName", lambda: "")() or "panel"
                app_identity.qsettings().setValue(
                    f"panel_size/{name}", f"{w},{h}")
                if getattr(panel, "dock_state", "float") != "float":
                    # 松手：恢复 dock 栏内固定宽度并同步 dock 布局与主面板最小宽
                    panel.setFixedWidth(int(w))
                    self._sync_dock_layout()
                    self._apply_dock_min_size()
        except Exception:
            pass

    # ---------- 融入主面板 dock（浮入/浮出/换侧/栏内排序 + 持久化） ----------
    def _make_dock_column(self, side: str) -> QWidget:
        """创建 dock 侧栏容器：容纳融入主面板的子面板（贴附样式：纯黑底 + 圆角卡片）。
        背景用不透明 BG（与主面板同色），面板 resize 拖动时缝隙被同色覆盖，不露黑块。"""
        col = QWidget()
        col.setObjectName(f"dockCol{side}")
        col.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        col.setStyleSheet(
            f"QWidget#dockCol{side} {{ background: {BG}; border: none; }}")
        c_lay = QVBoxLayout(col)
        c_lay.setContentsMargins(0, 0, 0, 0)
        # 栏内面板首尾相接（间距 0）：面板间的竖直空隙全部并入余量、由同栏的 Git 面板
        # 吸收占满（用户要求「任务清单上方 / 工作树下方的空隙都给 Git 面板」）
        c_lay.setSpacing(0)
        c_lay.addStretch(1)
        col.setProperty("_dock_lay", c_lay)
        col.setProperty("_walked", False)
        col.hide()
        return col

    def _dock_column(self, side: str) -> QWidget:
        return self._dock_left if side == "left" else self._dock_right

    def _dock_layout(self, side: str) -> QVBoxLayout:
        col = self._dock_column(side)
        return col.property("_dock_lay") if col is not None else None

    def _dock_panel_order(self, side: str) -> list:
        """当前 dock 侧栏内的面板顺序（按布局自上而下）"""
        lay = self._dock_layout(side)
        if lay is None:
            return []
        out = []
        for i in range(lay.count()):
            w = lay.itemAt(i).widget()
            if w is not None and isinstance(w, _RoundedFloatWindow):
                out.append(w)
        return out

    def _dock_panel(self, panel, side: str):
        """把面板融入主面板（同一窗口属性）：reparent 到 dock 栏（去掉 Window 类型
        flag 转为普通子控件）后在栏内显示，随主面板同显同隐。
        修复旧实现：独立 Tool 窗口直接 addWidget 无法被主面板嵌入（真实窗口系统下
        仍保持 native 子窗口），必须先 setParent 去掉窗口 flag 再放入布局。"""
        try:
            if panel is None:
                return
            col = self._dock_column(side)
            lay = self._dock_layout(side)
            if col is None or lay is None:
                return
            if panel.dock_state != "float":
                old = self._dock_layout(panel.dock_state)
                if old is not None and old is not lay:
                    old.removeWidget(panel)
            panel.setParent(col)             # 关键：先转普通子控件（默认 Widget flag）
            panel.dock_state = side
            if panel.parent() is col and panel not in self._dock_panel_order(side):
                lay.addWidget(panel)
            panel._set_dock_ui(True)   # 融入：隐藏把手/resize 热区，todos 限行滚动
            # dock 栏内宽度按各自持久化尺寸固定（保持贴附样式列宽），浮出时解除
            try:
                _w = _load_panel_size(panel)[0]
                _fw = _w if _w > 0 else getattr(panel, "WIDTH", 280)
                panel.setFixedWidth(int(_fw))
            except Exception:
                pass
            panel.show()
            self._sync_dock_layout()
        except Exception:
            pass

    def _undock_panel(self, panel, gpos: QPoint = None):
        """把面板从 dock 栏浮出为独立悬浮窗口（恢复 Window flag 与位置持久化）"""
        try:
            if panel is None or panel.dock_state == "float":
                return
            lay = self._dock_layout(panel.dock_state)
            if lay is not None:
                lay.removeWidget(panel)
            panel.dock_state = "float"
            panel.setParent(self, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
            panel._set_dock_ui(False)   # 贴附：恢复把手/resize 热区，todos 恢复固定默认尺寸
            # 解除 dock 期间的固定宽度与高度封顶，恢复可拖拽调整大小
            try:
                panel.setMinimumWidth(0)
                panel.setMaximumWidth(65535)
                panel.setMaximumHeight(16777215)
            except Exception:
                pass
            # 恢复「融入前」的原有大小：优先融入前的记忆尺寸，其次持久化尺寸/面板默认尺寸。
            # 不还原的话窗口会停在 dock 栏里被布局分配的高度上，需手动拖拽才复原。
            remembered = (getattr(self, "_pre_dock_sizes", None) or {}).get(panel.objectName())
            try:
                if remembered:
                    panel.restore_pre_dock_size(int(remembered[0]), int(remembered[1]))
                else:
                    panel.apply_saved_panel_size()
            except Exception:
                pass
            panel.show()
            try:
                panel.raise_()
            except Exception:
                pass
            # 重新套圆角蒙版/玻璃：setParent 后原生窗口（HWND）重建，dock 期间按子控件
            # 算出的窗口区域会残留 → 面板下缘/四角露出透明带。显示后再套一次并延迟一档兜底。
            try:
                panel._apply_window_round()
                QTimer.singleShot(0, panel._apply_window_round)
            except Exception:
                pass
            if gpos is not None:
                panel.move(gpos)
                _save_panel_pos(panel)
            self._sync_dock_layout()
        except Exception:
            pass

    def _sync_dock_layout(self):
        """按 dock 状态排列侧栏：有面板的侧栏显示，空侧栏隐藏；
        dock 内面板宽度跟随各自持久化尺寸（贴附样式保留：纯黑圆角卡片）。
        左侧三面板（工作树/Git/任务清单）垂直占比 5:3:2，其中：
        - 工作树按「名义份额（5 份）」封顶 → 保持其原有高度，不被余量撑高；
        - 任务清单按固定默认尺寸（上限）钉住，面板内滚动查看超出部分（见 TodosWindow
          ._fit_dock_height / _pin_height），窗口恰好吃满内容 → 不再留空白条带；
        - 二者释放的余量全部由 Git 面板吸收 → 栏内无空白，Git 列表显示更多条目。
        侧栏宽度固定为该侧面板持久化最大宽，预览页面 sizeHint 波动不致列宽突变挤压。"""
        if getattr(self, "_dock_sync_running", False):
            return                      # 重入保护：设置封顶高度会触发子控件重排
        self._dock_sync_running = True
        try:
            _factors = {"wtWin": 5, "gitLogWin": 3, "todosWin": 2}
            for side in ("left", "right"):
                col = self._dock_column(side)
                if col is None:
                    continue
                lay = col.property("_dock_lay")
                if lay is None:
                    continue
                order = self._dock_panel_order(side)
                # 垂直占比：左侧三面板 5:3:2；右侧均分
                widths = []
                for i, p in enumerate(order):
                    lay.setStretch(lay.indexOf(p), _factors.get(p.objectName(), 1))
                    _w0 = _load_panel_size(p)[0]
                    widths.append(_w0 if _w0 > 0 else getattr(p, "WIDTH", 280))
                    if p.width() < 120:
                        p.apply_saved_panel_size()
                # 尾部 spacer 伸展置 0：面板按权重占满列高（不留不可控空白区）
                for j in range(lay.count()):
                    if lay.itemAt(j).spacerItem() is not None:
                        lay.setStretch(j, 0)
                # 高度封顶：左侧工作树按名义份额封顶（保持原高度）、任务清单按内容定高
                # （TodosWindow 自管），余量全部落给 Git 面板 → 卡片填满栏高，无空白条
                self._apply_dock_height_caps(side, order, lay, col, _factors)
                # 列宽固定为该侧面板持久化最大宽：预览/页面切换引起的 sizeHint 波动
                # 不再把代码预览等面板挤成半宽（旧动态列宽随子控件 sizeHint 突变）
                try:
                    col.setFixedWidth(max(widths or [280]))
                except Exception:
                    pass
                has = any(p.dock_state == side for p in
                          (self.todos_win, self.git_win, self.wt_win, self.code_win))
                col.setVisible(has)
                col.layout().activate()
            # 非 dock 侧/未融入的面板：解除封顶，避免残留上限影响贴附模式
            for name in ("wt_win", "git_win", "todos_win", "code_win"):
                p = getattr(self, name, None)
                if p is None or p.dock_state in ("left", "right"):
                    continue
                try:
                    p.setMaximumHeight(16777215)
                except Exception:
                    pass
            if hasattr(self, "_request_side_sync"):
                self._request_side_sync(0)
        except Exception:
            pass
        finally:
            self._dock_sync_running = False

    def _dock_resize_flush(self):
        """窗口尺寸变化后的 dock 栏重算（由 resizeEvent 单次定时器触发，合并连续 resize）"""
        self._dock_resize_armed = False
        if getattr(self, "_applied_panel_mode", "") == "dock":
            self._sync_dock_layout()

    def _apply_dock_height_caps(self, side, order, lay, col, factors):
        """dock 栏高度分工（仅左侧）：任务清单按固定默认尺寸（内容超出在面板内滚动）、
        工作树按其名义份额封顶、释放的余量全部由 Git 面板吸收——面板填满栏高，
        卡片底部不再出现空白条（窗口尺寸变化时由 resizeEvent 重新调用，值随栏高重算）。
        栏高不足时先收缩工作树，仍不足才压缩任务清单，保证 Git 面板不小于其最小高度，
        避免整列溢出把清单底部裁掉。"""
        if side != "left" or not order:
            return
        try:
            # 份额口径按「名义间距」计（栏内实际间距为 0）：工作树/任务清单的高度份额
            # 与改造前一致，间距释放出的空间全部由余量吸收者（Git 面板）占满。
            gaps = self._DOCK_PANEL_GAP * max(0, len(order) - 1)
            avail = max(0, col.height() - gaps)
            total = sum(factors.get(p.objectName(), 1) for p in order) or 1
            by_name = {p.objectName(): p for p in order}
            wt = by_name.get("wtWin")
            git = by_name.get("gitLogWin")
            todos = by_name.get("todosWin")
            git_min = int(getattr(git, "HEIGHT_MIN", 120) or 120) if git is not None else 0
            todos_need = todos.minimumHeight() if todos is not None else 0
            cap = int(avail * factors.get("wtWin", 1) / total)      # 工作树名义份额
            if todos is not None:
                # 留给任务清单（固定尺寸）与 Git 最小高度后再封顶工作树
                cap = min(cap, max(0, avail - todos_need - git_min))
            if wt is not None:
                wt.setMaximumHeight(cap if cap >= wt.minimumHeight() else 16777215)
            if git is not None:
                git.setMaximumHeight(16777215)   # 余量吸收者：不封顶
            if todos is not None:
                # 任务清单的栏内可用高度 = 栏高 - 工作树实占 - Git 最小高度
                wt_used = max(cap, wt.minimumHeight()) if wt is not None else 0
                todos.set_dock_room(max(0, avail - wt_used - git_min))
        except Exception:
            pass

    # ---- 面板偏好（贴附 / 融入主面板，模式开关驱动） ----
    def _panel_mode(self) -> str:
        """面板偏好模式：attach=贴附主面板(独立窗口靠边) / dock=融入主面板(同一窗口)"""
        return _panel_mode_setting()

    def _apply_panel_mode(self, mode: str = None):
        """按面板偏好应用模式：dock → 四面板全部融入主面板(dock 栏)；
        attach → 全部浮出为独立贴心窗口。幂等（已在该模式时跳过）。"""
        try:
            mode = mode or self._panel_mode()
            if getattr(self, "_applied_panel_mode", None) == mode \
                    and self._panels_in_mode(mode):
                return
            if mode == "dock":
                self._remember_pre_dock_state()   # 记录融入前的面板尺寸与主窗口尺寸
                for p in (getattr(self, "wt_win", None), getattr(self, "git_win", None),
                          getattr(self, "todos_win", None)):
                    if p is not None:
                        self._dock_panel(p, "left")
                if getattr(self, "code_win", None) is not None:
                    self._dock_panel(self.code_win, "right")
                self._apply_dock_min_size()   # 放宽主面板最小宽，保证内容区自适应
            else:
                for name in ("todos_win", "git_win", "wt_win", "code_win"):
                    p = getattr(self, name, None)
                    if p is not None and p.dock_state != "float":
                        self._undock_panel(p)
                self._dock_left.setVisible(False)
                self._dock_right.setVisible(False)
                # 恢复贴附模式的最小宽（可再次自由缩放）+ 融入前的主窗口尺寸
                self.setMinimumWidth(1074)
                self._restore_pre_dock_geometry()
            self._applied_panel_mode = mode
            self._sync_dock_layout()
        except Exception:
            pass

    def _remember_pre_dock_state(self):
        """融入（dock）前记录「原有大小」：仍处于贴附态的子面板尺寸 + 主窗口尺寸。
        窗口不可见时不记录（启动即以 dock 偏好建面板时几何尚未落定，记下会把
        未定尺寸当成原尺寸）——此时浮出会回退到持久化尺寸/面板默认尺寸。"""
        if not self.isVisible():
            return
        self._pre_dock_sizes = dict(getattr(self, "_pre_dock_sizes", None) or {})
        for name in ("todos_win", "git_win", "wt_win", "code_win"):
            p = getattr(self, name, None)
            if p is None or getattr(p, "dock_state", "float") != "float":
                continue
            if p.width() >= 120 and p.height() >= 100:
                self._pre_dock_sizes[p.objectName()] = (int(p.width()), int(p.height()))
        if not self.isMaximized() and not self.isFullScreen():
            self._pre_dock_geo = (int(self.width()), int(self.height()))

    def _restore_pre_dock_geometry(self):
        """切回贴附时恢复主面板原有尺寸。
        - 有融入前尺寸记录 → 还原它；
        - 没有（例如启动即为融入模式）→ 直接收缩到贴附模式的最小宽：融入时窗口被
          _apply_dock_min_size 放宽过，不收缩就会一直停在那个偏宽的尺寸上
          （用户反馈「ai 主面板应自动缩小」）。
        最大化/全屏时不改尺寸（避免把全屏尺寸当原尺寸）。"""
        if self.isMaximized() or self.isFullScreen():
            return
        try:
            geo = getattr(self, "_pre_dock_geo", None)
            if geo:
                w, h = int(geo[0]), int(geo[1])
                if w >= self.minimumWidth() and w > 0 and h > 0:
                    if (w, h) != (self.width(), self.height()):
                        self.resize(w, h)
                    return
            need = max(self.minimumWidth(), 1)     # 贴附最小宽 = 内容区最小宽
            if self.width() > need:
                self.resize(need, self.height())
        except Exception:
            pass

    def _apply_dock_min_size(self):
        """融入主面板（dock）模式：左右 dock 栏占用主面板宽度，必须放宽窗口最小宽度，
        使聊天区/输入行等主内容在「内容区最小宽 + 两侧栏 + 间距」下不被挤压。"""
        try:
            left_w = max((p.width() for p in self._dock_panel_order("left")), default=280)
            right_w = max((p.width() for p in self._dock_panel_order("right")), default=441)
            need = 1074 + left_w + right_w + 30   # 内容区维持原最小宽 + 两侧栏 + 间距边距
            scr = self.screen()
            use = need
            if scr is not None:
                gw = scr.availableGeometry().width()
                if gw > 0:
                    use = min(need, max(1074, gw - 20))   # 屏幕不足时保内容区、不溢出屏幕
            use = int(use)
            self.setMinimumWidth(use)
            if self.width() < use:
                self.resize(use, self.height())
        except Exception:
            pass

    def _panels_in_mode(self, mode: str) -> bool:
        """四面板是否整体处于给定模式（attach=全部 float / dock=全部已融入）"""
        try:
            states = [getattr(getattr(self, n, None), "dock_state", "float")
                      for n in ("todos_win", "git_win", "wt_win", "code_win")]
            if mode == "dock":
                return bool(states) and all(s in ("left", "right") for s in states)
            return bool(states) and all(s == "float" for s in states)
        except Exception:
            return False

    def _cleanup_legacy_panel_keys(self):
        """清理旧版面板设置遗留键（逐面板 dock_state/dock_order/panel_width/*），
        新版以单一 panel_pref_mode 驱动，避免残留键干扰排序。"""
        try:
            q = app_identity.qsettings()
            for name in ("todos_win", "git_win", "wt_win", "code_win"):
                q.remove(f"dock_state/{getattr(self, name, None).objectName()}"
                         if getattr(self, name, None) is not None else f"dock_state/{name}")
            q.remove("dock_order/left")
            q.remove("dock_order/right")
            q.remove("panel_width/left_group")
            q.remove("panel_width/codeWin")
        except Exception:
            pass

    # ---- 把手拖拽：dock 模式由设置驱动（面板位置固定于栏内，不允许拖浮） ----
    def _panel_drag_start(self, panel, gpos: QPoint):
        """面板把手按下：dock 模式下面板位置固定，不启动拖拽；attach 模式正常拖拽移动"""
        if self._panel_mode() == "dock":
            return

    def _panel_drag_move(self, panel, gpos: QPoint):
        """attach 模式由把手自身移动窗口；dock 模式无操作。"""
        return

    def _panel_drag_end(self, panel, gpos: QPoint):
        """attach 模式：保持在独立悬浮状态（把手已移动），仅记录用户拖动标记"""
        try:
            panel._user_moved = True
        except Exception:
            pass

    def _flush_side_sync(self):
        self._side_sync_armed = False
        try:
            self._apply_reserve()
        except Exception:
            pass
        for _fn in ("_sync_wt_win", "_sync_git_win", "_sync_todos_win", "_sync_code_win",
                    "_sync_ext_panels"):
            _f = getattr(self, _fn, None)
            if callable(_f):
                try:
                    _f()
                except Exception:
                    pass
        # 延迟再抬升一次，确保最大化/焦点竞争结束后各面板都保持可见、不抖
        QTimer.singleShot(120, self._raise_side_panels)

    def _raise_side_panels(self):
        for _name in ("wt_win", "git_win", "todos_win", "code_win"):
            _w = getattr(self, _name, None)
            if _w is None:
                continue
            if _w.dock_state != "float":
                continue   # 已融入主面板：由 dock 布局盏管可见性，无需浮动抬升
            if _w.isVisible():
                try:
                    _w.raise_()
                except Exception:
                    pass
        for _w in (getattr(self, "_ext_panels", None) or {}).values():
            if _w is not None and _w.isVisible():
                try:
                    _w.raise_()
                except Exception:
                    pass

    def _sync_ext_panels(self):
        """扩展面板定位：按注册顺序堆叠于左侧列底部（todos 之下）。
        仅创建/重新显示/守卫触发时归位；用户拖走后守卫只抬升不强制归位
        （与 todos 面板行为一致）。"""
        if self._panel_minimized:
            return
        wins = getattr(self, "_ext_panels", None) or {}
        if not wins:
            return
        x, y = self._left_stack_origin()
        tw = getattr(self, "todos_win", None)
        if tw is not None and tw.isVisible():
            y = tw.frameGeometry().y() + tw.height() + 10
        for w in wins.values():
            if w is None:
                continue
            # AI 接管（_ai_managed=True）：位置/显隐由 AI 全权控制，归位只恢复隐藏前状态
            if getattr(w, "_ai_managed", False):
                if (not w.isVisible() and getattr(w, "_ai_was_visible", False)):
                    w.show()
                    w.raise_()
                    w._ai_was_visible = False
                continue
            if getattr(w, "_user_moved", False):
                # 用户手动拖走：只保证可见与置于前台，不强制归位（保持自定义布局）
                if not w.isVisible():
                    w.show()
                w.raise_()
                continue
            self._place_owned(w, x, y)
            y = w.frameGeometry().y() + w.height() + 10

    def _ensure_ext_panels(self):
        """扫描并挂载扩展面板（插件/工作流/代码注册），幂等：
        仅创建新增面板，已存在的保留原窗口引用（含用户拖拽位置）。
        任意构建（默认/自定义/完全重构）结束都会调用，保证面板恒挂载。"""
        if getattr(self, "_ext_panels", None) is None:
            self._ext_panels = {}
        try:
            from zhuzhu_Copilot.core import agent_panels
        except Exception:
            return
        try:
            agent_panels.scan_panels()
            built = agent_panels.build_panel_widgets(self)
        except Exception:
            return
        created = False
        for name, title, width, height, widget in built:
            if name in self._ext_panels:
                if widget is not None:
                    # 已存在同窗：丢弃本次新建的控件，保留原窗口内容
                    try:
                        widget.deleteLater()
                    except Exception:
                        pass
                continue
            if widget is None:
                continue
            self._ext_panels[name] = ExtPanelWindow(
                name, title, widget, width, height, self)
            created = True
        if created:
            self._sync_ext_panels()

    def _hot_reload_ext_panels(self):
        """功能面板热更新（由 _ext_hot_timer 周期性触发）：检测扩展面板源文件
        （panel.py）变更，被修改的面板重新加载并原位重建窗口（保留几何/拖拽位置），
        不改动其他面板。代码注册/未变更面板自动跳过，避免无谓重建。"""
        try:
            from zhuzhu_Copilot.core import agent_panels
        except Exception:
            return
        try:
            agent_panels.scan_panels()          # 新建面板即时发现（幂等，已注册跳过）
            changed = agent_panels.reload_panels()
        except Exception:
            return
        if not changed:
            return
        wins = getattr(self, "_ext_panels", None)
        if wins is None:
            self._ext_panels = {}
            wins = self._ext_panels
        for name in changed:
            title, width, height, widget = agent_panels.build_panel(name, self)
            if widget is None:
                continue
            old = wins.get(name)
            geo = None
            was_visible = True
            if old is not None:
                geo = (old.pos().x(), old.pos().y(), old.width(), old.height())
                was_visible = old.isVisible()
                try:
                    old.close()
                    old.deleteLater()
                except Exception:
                    pass
            nw = ExtPanelWindow(name, title, widget, width, height, self)
            if geo is not None:
                nw.move(geo[0], geo[1])
                nw.resize(geo[2], geo[3])
                if was_visible:
                    nw.show()
                    nw.raise_()
            wins[name] = nw

    def _sync_todos_win(self):
        """todos 独立窗口定位：工作树 → git → todos 自上而下堆叠于左侧锚点列；
        设置中关闭任务清单窗口时隐藏。全屏/最大化同样显示（避免被遮挡）。
        AI 置 _ai_managed=True 时完全接管该窗口（位置/大小/显隐由 AI 决定），
        守卫不再重定位/改变大小/按设置隐藏，仅在主面板最小化后恢复时，
        按隐藏前可见状态恢复显示。"""
        if self._panel_minimized:
            return
        tw = getattr(self, "todos_win", None)
        if tw is None:
            return
        if tw.dock_state != "float":
            return   # 已融入主面板：位置由 dock 布局管理，不做停靠定位
        if getattr(tw, "_ai_managed", False):
            if (not tw.isVisible() and getattr(tw, "_ai_was_visible", False)):
                tw.show()
                tw.raise_()
                tw._ai_was_visible = False
            return
        if getattr(tw, "_user_moved", False):
            # 用户手动拖走：只保证可见与置于前台，不强制归位（保持自定义布局）
            if not tw.isVisible():
                tw.show()
            tw.raise_()
            return
        if not self._todos_enabled():
            tw.hide()
            if hasattr(tw, "_dragging"):
                tw._dragging = False   # 隐藏时清除拖拽状态
            return
        # 重新启用（从隐藏变为显示）：重置拖拽位置，回到默认停靠位置
        if not tw.isVisible() and hasattr(tw, "_dragging"):
            tw._dragging = False
        x, y = self._left_stack_origin()
        gw = getattr(self, "git_win", None)
        if gw is not None and gw.isVisible():
            y = gw.frameGeometry().y() + gw.height() + 10
        else:
            ww = getattr(self, "wt_win", None)
            if ww is not None and ww.isVisible():
                y = ww.frameGeometry().y() + ww.height() + 10
        self._place_owned(tw, x, y)

    def _sync_git_win(self):
        """Git 只读面板定位：位于工作树下方 10px；工作树之下、任务清单之上。
        拖拽/移动时仅定位（不刷新），仅首次显示时刷新一次，避免移动过程反复触发
        git 子进程造成卡顿；空列表重试由 _periodic_git_refresh（5s）负责。
        AI 置 _ai_managed=True 时完全接管（同 _sync_todos_win）。"""
        if self._panel_minimized:
            return
        gw = getattr(self, "git_win", None)
        if gw is None:
            return
        if gw.dock_state != "float":
            return   # 已融入主面板：位置由 dock 布局管理，不做停靠定位
        if getattr(gw, "_ai_managed", False):
            if (not gw.isVisible() and getattr(gw, "_ai_was_visible", False)):
                gw.show()
                gw.raise_()
                gw._ai_was_visible = False
            return
        if getattr(gw, "_user_moved", False):
            # 用户手动拖走：只保证可见与置于前台，不强制归位（保持自定义布局）
            if not gw.isVisible():
                gw.show()
            gw.raise_()
            return
        if not self._git_enabled():
            gw.hide()
            return
        show = not gw.isVisible()
        x, y = self._left_stack_origin()
        ww = getattr(self, "wt_win", None)
        if ww is not None and ww.isVisible():
            y = ww.frameGeometry().y() + ww.height() + 10
        self._place_owned(gw, x, y)
        if show and callable(getattr(gw, "refresh", None)):
            from zhuzhu_Copilot.core import agent_git
            self._known_git_root = agent_git.repo_dir()
            self._known_git_mtime = agent_git.repo_mtime()
            gw.refresh()

    def _on_git_probe(self, root: str, mtime: float):
        """git 仓库根/时间戳后台探测结果：更新已知值供周期刷新判断。
        （探测已后台化，避免设置工作目录时同步 walk 找 .git 卡死主线程）"""
        if root:
            self._known_git_root = root
        if mtime:
            self._known_git_mtime = mtime

    def _periodic_git_refresh(self):
        """周期刷新 git 分支/提交面板与工作树：工作目录可能延迟设置，识别到工作目录
        （或仓库根/.git 变化）才重建，避免每 5s 起 git 子进程造成卡顿；
        面板不可见时跳过。AI 接管（_ai_managed）的 git 窗口由 AI 自行刷新。"""
        gw = getattr(self, "git_win", None)
        if gw is None:
            return
        if getattr(gw, "_ai_managed", False):
            return
        from zhuzhu_Copilot.core import agent_git, agent_tools
        # 工作目录变更而面板未同步 → 立即自愈刷新（工作树 + git 面板）。
        # 即时刷新被跳过/晚到时的兜底，保证切目录后最多 ~5s 内呈现新内容。
        if agent_tools.get_workdir() != getattr(self, "_rendered_workdir", None):
            self._refresh_git_and_worktree()
            return
        if not gw.isVisible():
            return
        root = agent_git.repo_dir()
        gw_list = getattr(gw, "list", None)
        if root != self._known_git_root:
            self._known_git_root = root
            self._known_git_mtime = agent_git.repo_mtime()
            if callable(getattr(gw, "refresh", None)):
                gw.refresh()
        elif gw_list is not None and gw_list.count() == 0:
            self._known_git_mtime = agent_git.repo_mtime()
            if callable(getattr(gw, "refresh", None)):
                gw.refresh()
        elif agent_git.repo_mtime() > self._known_git_mtime:
            self._known_git_mtime = agent_git.repo_mtime()
            if callable(getattr(gw, "refresh", None)):
                gw.refresh()

    def _guard_panels(self):
        """面板守卫：应用激活时把面板抬升到本应用之上（避免点击对话区后消失），
        但不用全局置顶，切到其他应用时面板随本应用退后、不遮挡其他内容。
        仅对隐藏面板调用 sync 重新显示；可见面板在应用激活时 raise_。
        AI 接管（_ai_managed）的窗口由 AI 全权控制显隐/层级，守卫跳过。"""
        if not self.isVisible() or self._panel_minimized:
            return
        active = self.isActiveWindow() or (self.window() is not None
                                           and self.window().isActiveWindow())
        for name, enabled, sync in (
                ("todos_win", self._todos_enabled, self._sync_todos_win),
                ("git_win", self._git_enabled, self._sync_git_win),
                ("wt_win", self._wt_enabled, self._sync_wt_win),
                ("code_win", self._code_enabled, self._sync_code_win)):
            w = getattr(self, name, None)
            if w is None or not enabled():
                continue
            if w.dock_state != "float":
                continue   # 已融入主面板：可见性由 dock 布局管理，守卫不干预
            if getattr(w, "_ai_managed", False):
                continue
            if not w.isVisible():
                sync()
            elif active:
                w.raise_()
        # 扩展面板（插件/工作流/代码注册）：无独立开关，默认恒显示；
        # 可见则抬升（保持用户拖拽位置），不可见则重新归位显示
        for w in (getattr(self, "_ext_panels", None) or {}).values():
            if w is None or getattr(w, "_ai_managed", False):
                continue
            if not w.isVisible():
                w.show()
                w.raise_()
            elif active:
                w.raise_()

    def _sync_wt_win(self):
        """工作树只读面板定位：停靠左侧锚点列顶部（git / todos 依次排在其下方）。
        拖拽/移动时仅定位（不刷新），仅首次显示时刷新一次，避免移动过程反复扫描目录卡顿。
        AI 置 _ai_managed=True 时完全接管（同 _sync_todos_win）。"""
        if self._panel_minimized:
            return
        ww = getattr(self, "wt_win", None)
        if ww is None:
            return
        if ww.dock_state != "float":
            return   # 已融入主面板：位置由 dock 布局管理，不做停靠定位
        if getattr(ww, "_ai_managed", False):
            if (not ww.isVisible() and getattr(ww, "_ai_was_visible", False)):
                ww.show()
                ww.raise_()
                ww._ai_was_visible = False
            return
        if getattr(ww, "_user_moved", False):
            # 用户手动拖走：只保证可见与置于前台，不强制归位（保持自定义布局）
            if not ww.isVisible():
                ww.show()
                if callable(getattr(ww, "refresh", None)):
                    ww.refresh()
            ww.raise_()
            return
        if not self._wt_enabled():
            ww.hide()
            return
        show = not ww.isVisible()
        x, y = self._left_stack_origin()
        self._place_owned(ww, x, y)
        if show and callable(getattr(ww, "refresh", None)):
            ww.refresh()

    def _sync_code_win(self):
        """代码预览面板定位：停靠 AI 面板右侧、顶部与面板齐平，高度随面板。
        最大化/全屏时 AI 面板占满屏幕，改锚定屏幕右缘的预留空间（_apply_reserve
        已把内容左移让位），保证预览面板可见；非最大化贴在面板右侧。
        AI 置 _ai_managed=True 时完全接管（同 _sync_todos_win）。"""
        if self._panel_minimized:
            return
        cw = getattr(self, "code_win", None)
        if cw is None:
            return
        if cw.dock_state != "float":
            return   # 已融入主面板：位置由 dock 布局管理，不做停靠定位
        if getattr(cw, "_ai_managed", False):
            if (not cw.isVisible() and getattr(cw, "_ai_was_visible", False)):
                cw.show()
                cw.raise_()
                cw._ai_was_visible = False
            return
        if getattr(cw, "_media_full", False):
            # 视频全屏中：宿主不干预定位，避免把全屏预览面板拉回停靠位置
            cw.raise_()
            return
        if getattr(cw, "_user_moved", False):
            # 用户手动拖走：只保证可见与置于前台，不强制归位且不再跟随主面板高度
            if not cw.isVisible():
                cw.show()
            cw.raise_()
            return
        if not self._code_enabled():
            cw.hide()
            return
        base = self.mapToGlobal(self.rect().topLeft())
        # 高度跟随主面板：用户手动拖过高度（_manual_h）后保持其自定义高度
        if not (getattr(cw, "_manual_h", 0) or 0):
            cw.setFixedHeight(self.height())
        # 用实际窗口宽度定位：_GlassCode 为 setFixedWidth(540) 且无 WIDTH 属性，
        # 旧逻辑 getattr(cw,"WIDTH",441) 取到默认 441 会导致 code_win 定位偏右超屏、
        # 远离主面板（右缘超出屏幕）。改用 cw.width() 使面板完整在屏内、贴近主面板。
        cw_width = cw.width() if (cw is not None and cw.width() > 0) \
            else getattr(cw, "WIDTH", 441)
        if self._is_fullscreen():
            scr = self.screen()
            g = scr.availableGeometry() if scr else None
            if g is not None and g.width() > 0:
                # 最大化：右缘对齐基础上向左 10px 靠近主面板，再往右平移 3px
                # 缓解对主面板右侧的遮挡（净向左 7px）
                x = g.right() - cw_width - 5 - 10 + 3
            else:
                x = base.x() + self.width() + 5
            x = max(0, x)
        else:
            x = base.x() + self.width() + 5
        self._place_owned(cw, x, base.y())

    def _setup_builtin_browser(self):
        """AI browser_* 工具在内置多标签 WebView 内操作（QtWebEngine 可用时装配）。
        未装配（无 WebEngine）时回退为独立外部浏览器实例。"""
        self._builtin_browser = None
        if not agent_ui_ux.web_engine_available():
            return
        from zhuzhu_Copilot.core import agent_tools as _at
        from zhuzhu_Copilot.core.agent_browser_web import (
            BuiltinBrowser,
            BuiltinBrowserController,
        )
        bb = BuiltinBrowser()

        def _provider():
            cw = getattr(self, "code_win", None)
            return cw._active_web() if cw is not None else None

        def _ensure():
            cw = getattr(self, "code_win", None)
            if cw is None:
                return
            try:
                cw.show()
                cw.raise_()
                cw._switch_mode("web")
            except Exception:
                pass
            try:
                self._sync_code_win()
            except Exception:
                pass

        bb.attach(_provider, _ensure)
        try:
            _at.register_builtin_browser(BuiltinBrowserController(bb))
            self._builtin_browser = bb
        except Exception:
            pass

    def _on_ai_file_changed(self, path: str, old_text: str, new_text: str):
        """文件变更观察者回调（可被引擎/工作线程调用）：转发到主线程处理"""
        try:
            self.file_diff_signal.emit(str(path), str(old_text or ""), str(new_text or ""))
        except Exception:
            pass

    def _on_file_diff(self, path: str, old_text: str, new_text: str):
        """主线程：AI 写/改文件后在代码预览面板展示增删差异（红绿 +/- 高亮，2s 恢复）"""
        import logging
        try:
            cw = getattr(self, "code_win", None)
            if cw is None or not callable(getattr(cw, "show_diff", None)):
                return
            # 预览面板正在播放媒体时不打断：跳过差异跳转，保持媒体播放
            if callable(getattr(cw, "is_media_active", None)) and cw.is_media_active():
                return
            # 超大文件跳过差异展示（渲染会卡顿），仅正常化
            try:
                if os.path.getsize(path) > 1024 * 1024:
                    return
            except OSError:
                pass
            # 文件不存在视为纯新增；内容为空视为纯删除
            if not old_text and not os.path.isfile(path):
                try:
                    with open(path, "r", encoding="utf-8", errors="replace") as f:
                        new_text = f.read()
                except OSError:
                    pass
            cw.show_diff(path, old_text, new_text)
            if not cw.isVisible():
                cw.show()
            cw.raise_()
        except Exception as e:
            logging.getLogger("zhuzhu_Copilot.ui_ux").warning(
                "_on_file_diff 异常: %s", e)

    def _open_code_preview(self, path: str):
        """双击工作树文件 → 在右侧代码预览器打开并显示"""
        cw = getattr(self, "code_win", None)
        if cw is None or not path or not callable(getattr(cw, "show_file", None)):
            return
        cw.show_file(path)
        if getattr(cw, "_ai_managed", False):
            # AI 接管：用户双击预览是明确意图，仍显示并抬升，但不重定位
            cw.show()
            cw.raise_()
            return
        if not self._code_enabled():
            return
        self._sync_code_win()
        cw.show()
        cw.raise_()

    def showEvent(self, e):
        super().showEvent(e)   # 统一补丁已为 QDialog 深色化标题栏
        # 首次运行：UI 就绪后展示新手指南（一次性，之后可在设置里重新打开）。
        # 【必须最先调度】后续原生调用（DWM 圆角 / 任务栏图标 / 停靠面板同步 / 管理员拖放等）
        # 任一抛异常都不能让引导调度被跳过 —— 此前该调度位于 showEvent 末尾，前面出错即
        # 静默不弹（安装后「新手指南无法弹出」的成因之一）。调度的定时器会在 showEvent
        # 返回、事件循环空闲时才触发，提前调度不影响其余初始化顺序。
        QTimer.singleShot(650, self._maybe_show_onboarding)
        # 无边框（自定义玻璃）模式：窗口显示时切圆角（最大化时自动清除）
        self._apply_window_round()
        # 强制任务栏缩略图（owned window 默认不显示，需手动加 WS_EX_APPWINDOW）
        self._ensure_taskbar_entry()
        # 原生窗口显示后强制设置任务栏大/小图标，修复安装后任务栏显示默认图标
        set_native_window_icon(self, _app_icon_path())
        # 打开面板时同步显示左侧停靠面板（worktree→git→todos 自上而下堆叠）；
        # 先 sync worktree 再 git，避免 git 先放锚点、worktree 随后压住 git 顶部。
        # 延迟再同步一次兜底面板完全就绪前的时序问题
        self._sync_wt_win()
        self._sync_git_win()
        self._sync_todos_win()
        self._sync_code_win()
        self._sync_ext_panels()
        QTimer.singleShot(50, self._sync_wt_win)
        QTimer.singleShot(50, self._sync_git_win)
        QTimer.singleShot(50, self._sync_todos_win)
        QTimer.singleShot(50, self._sync_code_win)
        QTimer.singleShot(50, self._sync_ext_panels)
        # 启动/复用打开时主面板可能稍晚才被 MainWindow 布局到最终位置，50ms 时锚点
        # 仍是旧位置 → 稍后经 _request_side_sync 聚合再归位一次，避免停靠面板
        # （worktree/git/todos）在左上角原处停留。
        QTimer.singleShot(250, self._request_side_sync)
        # 面板复用（关闭再打开不销毁），滚动位置不会自动重置 → 打开时自动滚到对话底部
        QTimer.singleShot(0, self._scroll_bottom)
        QTimer.singleShot(300, self._scroll_bottom)
        # 布局自愈：面板显示前渲染的消息流几何可能陈旧（聊天被挤压），
        # 首次显示后以真实宽度复核一次气泡高度
        QTimer.singleShot(0, self._relayout_messages)
        # 离屏渲染预热（见 _warm_offscreen_render）：浮现层每帧都要把区块渲进图像，
        # 而 Qt 首次 QWidget.render(...) 有一次性懒初始化，必须赶在流式开始前消化掉
        QTimer.singleShot(0, self._warm_offscreen_render)
        # 音乐：开关开启时打开面板自动播放/续播（延迟触发，避免启动期抢占资源）
        try:
            def _boot_music_and_desktop():
                from zhuzhu_Copilot.core import music_player as _mp
                from zhuzhu_Copilot.ui.desktop_lyrics import get_desktop_lyrics
                p = _mp.get_player()
                p.autoplay_on_panel_open()
                # 自动播放生效后，若用户曾开启桌面歌词则同步显示（无需进入音乐设置页）
                if p.is_playing():
                    get_desktop_lyrics().bind(p).ensure_enabled()
            QTimer.singleShot(400, _boot_music_and_desktop)
        except Exception:
            pass
        # 默认正常窗口大小（__init__ 中已 resize），不再强制最大化
        # 管理员权限：Windows UIPI 拦截普通 Explorer 的 OLE 拖放，改用 WM_DROPFILES 原生通道
        print(f"[dnd] showEvent is_admin={is_admin()} _admin_dnd={self._admin_dnd}", flush=True)
        if is_admin() and not self._admin_dnd:
            QTimer.singleShot(150, self._setup_admin_dnd)
            QTimer.singleShot(600, self._setup_admin_dnd)   # 兜底重试：窗口完全就绪后再注册一次
        else:
            print("[dnd] 非管理员运行：走 Qt 原生拖放", flush=True)

    def _warm_offscreen_render(self, _attempt: int = 0):
        """预热「控件 → 图像」渲染路径（浮现层每帧都要走这条路径）。

        Qt 首次把一个**带富文本子控件的区块**渲进图像时有一次性懒初始化：实测首帧
        550~600ms、之后 1ms。不预热的话这笔开销正好压在**第一段流式文字的第一帧**上 ——
        观感就是开场卡半秒（用户反馈的「不丝滑」）。

        触发条件实测有两个，缺一不可（四种组合逐一量过）：
          · 4 参重载 `render(图像, 偏移, QRegion, RenderFlags)` —— 2 参重载 0ms，不触发；
          · 目标控件必须处于 **shown** 状态 —— 只建不 show 的块 2ms，也不触发。
        所以预热对象是一个**挂在消息区里、已 show** 的临时 StreamBlock。为了不真的上屏
        （用户会看到一条闪现的空块），给它挂 `WA_DontShowOnScreen`：逻辑上已 show
        （该走的初始化照走，实测同样吃掉这 560ms），但不映射到屏幕。
        """
        if self.__dict__.get("_offscreen_warmed"):
            return
        try:
            if not self.isVisible():
                return      # 面板不可见 → 放弃（不排队重试）：隐藏状态下排重试只会白造控件
        except Exception:
            pass
        blk = None
        try:
            host = self.msg_area.widget() or self
            blk = chat_bubbles.StreamBlock(self._chat_style(), host)
            blk.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
            blk.set_html("预热")
            blk.resize(240, 48)
            blk.show()
            if not blk.isVisible():
                # 面板还没真正上屏（showEvent 阶段子控件尚不可见）→ 这个块不算 shown，
                # 渲过去吃不到初始化。退避重试，绝不浪费掉这一次机会。
                blk.deleteLater()
                if _attempt < 8:      # 有界重试（≤400ms）：不把自己挂成常驻定时器
                    QTimer.singleShot(
                        50, lambda: self._warm_offscreen_render(_attempt + 1))
                return
            img = QImage(64, 32, QImage.Format.Format_ARGB32_Premultiplied)
            img.fill(Qt.GlobalColor.transparent)
            blk.render(img, QPoint(0, 0), QRegion(QRect(0, 0, 64, 32)),
                       QWidget.RenderFlag.DrawWindowBackground
                       | QWidget.RenderFlag.DrawChildren)
            self._offscreen_warmed = True     # 只有真渲过才记（提前置位等于放弃预热）
        except Exception:
            pass
        finally:
            if blk is not None:
                blk.hide()
                blk.deleteLater()

    def _maybe_show_onboarding(self, _attempt: int = 0):
        """首次运行自动弹出新手指南（一次性；已标记/已打开过则跳过）。

        失败必须留痕并退避重试：此前判定异常被 `except: return` 静默吞掉，
        安装后「指南不弹」既无提示也无日志，无法排查。
        """
        if getattr(self, "_onboarding_opened", False):
            return
        if _attempt == 0:
            if getattr(self, "_onboarding_triggered", False):
                return
            self._onboarding_triggered = True
        try:
            from zhuzhu_Copilot.ui.onboarding import is_first_run
            first = bool(is_first_run())
        except Exception as _e:
            print(f"[onboarding] 首次运行判定失败（第 {_attempt + 1} 次）: {_e!r}",
                  flush=True)
            if _attempt < 2:
                self._schedule_onboarding_retry(_attempt + 1)
            return
        if not first:
            return
        self._open_onboarding()

    def _schedule_onboarding_retry(self, attempt: int):
        """退避重试：判定失败后延迟再试一次（独立方法，便于测试注入同步重试）。

        定时器由 Qt 事件循环驱动（不阻塞 UI）；重试次数很少（最多 2 次），
        避免引导因一次瞬时失败（模块加载/磁盘抖动）而永久不弹。
        """
        QTimer.singleShot(1200, lambda: self._maybe_show_onboarding(attempt))

    def _open_onboarding(self):
        """打开新手指南向导（首次运行或设置里「重新查看」入口共用）。"""
        try:
            from zhuzhu_Copilot.ui.onboarding import build_wizard, mark_first_run_done
        except Exception as _e:
            print(f"[onboarding] 加载向导失败: {_e!r}", flush=True)
            return
        try:
            # 外观（毛玻璃 / 尺寸下限 / 居中）统一由 build_wizard 处理，
            # 与启动流程里「首次安装先走指南」那一次保持完全一致
            dlg = build_wizard(parent=self)
        except Exception as _e:
            print(f"[onboarding] 构造向导失败: {_e!r}", flush=True)
            return

        self._onboarding_opened = True   # 自动弹出的一次性守卫（设置里「重新查看」不受限制）
        if dlg.exec():
            mark_first_run_done()
            # 向导里写入的偏好即时生效
            try:
                self._apply_agent_settings()
            except Exception:
                pass
            try:
                self._sync_todos_win()
            except Exception:
                pass
            try:
                self._on_fun_settings_changed()
            except Exception:
                pass
            if getattr(dlg, "_theme_changed", False):
                try:
                    self._retheme()
                except Exception:
                    pass

    def moveEvent(self, e):
        super().moveEvent(e)
        # 原生系统拖动（startSystemMove）期间，每帧同步 4 个子面板会反复 move()+raise_()
        # 多个 Acrylic 毛玻璃 + 阴影窗口，导致拖动严重卡顿 → 拖动期间跳过逐帧同步，
        # 拖动结束（标题栏在 _dragging 复位后）再一次性重新定位，消除延迟。
        if getattr(self, "_dragging", False):
            return
        # 默认模式（系统标题栏拖动）无 _dragging 标志：每帧 moveEvent 只重挂 160ms
        # 防抖定时器，停止移动后才一次性归位 —— 避免拖动过程中逐帧 move+raise 子面板
        # 造成顶部闪烁；移动持续时定时器被不断重置，不会中途触发。
        self._arm_move_side_sync()

    def _arm_move_side_sync(self):
        """拖动中移动事件防抖：最后一次 move 后 160ms 才执行一次子面板归位"""
        t = getattr(self, "_move_side_timer", None)
        if t is None:
            _t = QTimer(self)
            _t.setSingleShot(True)
            _t.setInterval(160)
            _t.timeout.connect(self._move_side_flush)
            self._move_side_timer = _t
            t = _t
        t.start()

    def _move_side_flush(self):
        """防抖到点：左键仍按住视为拖动未结束，继续等待；松开后一次性归位"""
        try:
            _pressed = bool(QApplication.mouseButtons() & Qt.MouseButton.LeftButton)
        except Exception:
            _pressed = False
        if _pressed:
            t = getattr(self, "_move_side_timer", None)
            if t is not None:
                t.start()
            return
        self._flush_side_sync()

    def _set_glass_drag(self, active: bool):
        """进入/退出系统移动或缩放。移动期间临时关闭所有窗口（主面板 + 4 个子面板）
        的 Acrylic（DWM 逐帧重算模糊是拖动严重卡顿的主因），并置全局拖动标志，跳过
        鼠标波纹重绘与子面板逐帧同步；结束后恢复毛玻璃并一次性归位。"""
        app = QApplication.instance()
        if app is not None:
            try:
                app._liquid_dragging = bool(active)
            except Exception:
                pass
        self._dragging = bool(active)
        targets = [self]
        for name in ("todos_win", "git_win", "wt_win", "code_win"):
            w = getattr(self, name, None)
            if w is not None:
                targets.append(w)
        for w in targets:
            try:
                agent_ui_ux.set_acrylic_enabled(w, not active)
            except Exception:
                pass
        if not active:
            for _fn in ("_sync_wt_win", "_sync_git_win", "_sync_todos_win", "_sync_code_win"):
                _f = getattr(self, _fn, None)
                if callable(_f):
                    try:
                        _f()
                    except Exception:
                        pass

    def _sync_panels_after(self):
        """窗口最大化/还原（手动 setGeometry 全屏）后，重新计算根布局左右预留边距并
        重定位 4 个子面板。多来源反复触发合并为一次（见 _request_side_sync），
        避免最大化时多次 move+raise 造成左面板消失与持续闪烁。"""
        self._request_side_sync(0)

    def _start_system_move(self):
        """用 Win32 SendMessage(WM_SYSCOMMAND, SC_MOVE|HTCAPTION) 触发原生阻塞式拖动。
        SendMessage 会阻塞到鼠标释放（DefWindowProc 模态循环），确保调用方包裹的
        Acrylic 开关覆盖整个拖动过程（QWindow.startSystemMove 在部分平台可能非阻塞，
        导致开关提前复位、拖动期间仍逐帧重算模糊而卡顿）。"""
        try:
            if sys.platform != "win32":
                wh = self.windowHandle()
                if wh is not None:
                    wh.startSystemMove()
                return
            hwnd = int(self.winId())
            if hwnd == 0:
                return
            user32 = ctypes.windll.user32
            # 关键：SendMessageW 的 HWND 是 64 位指针，必须显式声明参数类型，
            # 否则 ctypes 默认按 c_int（32 位）截断句柄 → 无效 HWND → 拖动无效
            # （默认 UI 用系统标题栏拖动不受影响，液态玻璃自定义标题栏才暴露）。
            try:
                user32.SendMessageW.argtypes = [
                    ctypes.c_void_p, ctypes.c_uint, ctypes.c_uintptr, ctypes.c_intptr]
                user32.SendMessageW.restype = ctypes.c_intptr
            except Exception:
                pass
            user32.ReleaseCapture()
            # WM_SYSCOMMAND(0x0112), SC_MOVE(0xF010) | HTCAPTION(0x0002) = 0xF012
            user32.SendMessageW(hwnd, 0x0112, 0xF012, 0)
        except Exception:
            pass

    def changeEvent(self, e):
        super().changeEvent(e)
        # 标题栏最大化/还原、父窗口（MainWindow）状态变化 → 同步面板全屏定位
        if e.type() == QEvent.Type.WindowStateChange:
            minimized = bool(self.windowState() & Qt.WindowState.WindowMinimized)
            self._panel_minimized = minimized
            if minimized:
                # 面板最小化/隐藏：收起浮出的独立子窗口（hide 而非 showMinimized：
                # 无边框 Tool 窗口最小化后无标题栏可恢复，隐藏最稳妥）。
                self._collapse_float_windows()
                self._collapse_ext_panels()
                # Copilot 浮层同样是独立顶层窗口：面板最小化时一并收起，避免孤立在桌面
                try:
                    self.close_copilot_panel()
                except Exception:
                    pass
                return
            # 恢复/最大化/还原：重新定位显示子面板（合并为一次）
            self._request_side_sync(0)

    def _collapse_float_windows(self):
        """收起「浮出（float）」的四个子面板 —— 独立窗口不会随主面板最小化/隐藏自动消失。

        只处理 float：融入主面板（dock）的子面板是主面板的普通子控件，随主面板同隐同显
        （父窗口最小化/隐藏时 Qt 自动隐藏）。若连同 dock 面板一起 hide()，其显隐状态会被
        丢掉：恢复路径上的 _sync_wt_win/_sync_git_win/_sync_todos_win/_sync_code_win 对
        dock_state != "float" 一律提前返回（位置由 dock 布局管理），无人重新 show()
        → 表现为「最小化再返回后左右两侧子面板整体消失」。"""
        for name in ("todos_win", "git_win", "wt_win", "code_win"):
            w = getattr(self, name, None)
            if w is None or getattr(w, "dock_state", "float") != "float":
                continue
            # AI 接管窗口记录隐藏前可见状态，恢复时据此重新显示
            if getattr(w, "_ai_managed", False):
                w._ai_was_visible = w.isVisible()
            w.hide()

    def _collapse_ext_panels(self):
        """收起扩展面板（独立浮窗，同样不随主面板最小化自动消失）"""
        for w in (getattr(self, "_ext_panels", None) or {}).values():
            if w is None:
                continue
            if getattr(w, "_ai_managed", False):
                w._ai_was_visible = w.isVisible()
            w.hide()

    def hideEvent(self, e):
        super().hideEvent(e)
        self._collapse_float_windows()

    def focusInEvent(self, e):
        super().focusInEvent(e)
        # 用户点击主窗口或设置页时，面板获得焦点 → 重新显示并定位（合并为一次避免闪烁）
        self._request_side_sync(0)

    def _add_bubble(self, text: str, align: str, rich: bool = False,
                    animate: bool = True) -> QWidget:
        """新建一条消息行。

        - user：demo `.msg` 用户气泡（深蓝底 + 非对称圆角 18/18/6/18）
        - ai  ：demo `.ai-turn` 事件流回合（无填充气泡，内容由 _render_ai_frame 填充）
        """
        if align == "user":
            bubble = chat_bubbles.UserBubble(self._chat_style())
            bubble.setMaximumWidth(self._bubble_max_width())
        else:
            bubble = chat_bubbles.ChatTurn(self._chat_style(), self._chat_icon)
            # 先用目标宽度做一次高度预估；真实宽度由布局给出后，回合自己的 resizeEvent
            # 会按实际宽度重算（min-only 写回，可增可减，所以预估偏大也会自愈）
            bubble.setMaximumWidth(self._ai_turn_max_width())
            bubble.relayout_heights(self._ai_turn_max_width())
            bubble.set_link_handler(lambda url, b=bubble: self._on_ai_turn_link(b, url))
            bubble.set_menu_handler(lambda pos, b=bubble: self._on_ai_bubble_menu(b, pos))
            bubble.set_toggle_handler(lambda b=bubble: self._sync_after_toggle(b))
            # 思考气泡「继续查看」展开/收起：块高度就地变化，回合已重钉自身高度，
            # 面板只需让滚动区跟手重排（否则展开的长正文被视口几何遮挡）
            bubble.set_block_resize_handler(
                lambda b=bubble: QTimer.singleShot(0, self._relayout_messages))
            # 右键菜单：朗读这条回复（从气泡段中提取正文文本后台合成播放）。
            # 接线只在 ChatTurn 内部做一次（set_menu_handler → CustomContextMenu 信号）：
            # 这里若再连一遍同一信号，信号会同时打到两个槽，右键会弹出两次菜单。
            # 重试按钮：透明隐形热区 + 局部 QToolTip（跟随主题色，杜绝浅色下黑字黑底）。
            # 鼠标附着到按钮位置或气泡本体时浮现 retry 矢量图标；点击重新生成。
            retry = QPushButton("")
            retry.setFixedSize(32, 32)          # 热区即按钮：移入即显示，点击可重试
            retry.setIconSize(QSize(22, 22))
            retry.setCursor(Qt.CursorShape.PointingHandCursor)
            retry.setToolTip("重试")
            retry.setAutoDefault(False)
            # 局部 QToolTip：用当前主题面板/文字色显式配色（浅色=浅底深字、深色=深底浅字）
            retry.setStyleSheet(
                "QPushButton { background: transparent; border: none; border-radius: 8px; }"
                f"QPushButton:hover {{ background: {HOVER}; }}"
                f"QToolTip {{ background-color: {PANEL}; color: {TEXT};"
                f" border: 1px solid {ACCENT}; border-radius: 6px;"
                f" padding: 4px 8px; font-size: 12px; }}")
            retry.setVisible(False)             # arm 前不占位
            retry.clicked.connect(self._regenerate_last)
            retry._is_retry_btn = True
            retry._host_bubble = bubble         # 反向引用：按钮 leave 时判断是否移回气泡
            retry.installEventFilter(self)
            # 附着到回合容器本体也可触发显示
            bubble.installEventFilter(self)
            bubble._retry_btn = retry
            bubble._retry_armed = False
        bubble.setProperty("align", align)
        self._bubble_widgets.append(bubble)
        if align == "user":
            # 对话定位器：为每条用户消息添加右侧圆点（悬停显示提问、点击滚动定位）
            self._msg_nav_push(bubble, text)
            # 用户消息：默认纯文本；带图片时用富文本渲染缩略图（不显示源文本）
            bubble.setTextFormat(Qt.TextFormat.RichText if rich else Qt.TextFormat.PlainText)
            if agent_ui_ux.is_custom_package_active():
                # 液态玻璃用户气泡：与自定义包主题同参数（玻璃底 + 主题字色 + 亮白受光边）
                bubble.setStyleSheet(f"background: {AI_BG}; color: {TEXT};"
                                     "border: 1px solid rgba(255,255,255,110);"
                                     "border-top: 1px solid rgba(255,255,255,225);"
                                     f"border-radius: {RADIUS_LG}px;"
                                     f"padding: {SPACING_MD}px {SPACING_MD}px;"
                                     f"font-size: {FONT_BASE}px;")
            bubble.setText(text)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        if align == "user":
            row.addStretch(1)
            row.addWidget(bubble, 0, Qt.AlignmentFlag.AlignRight)
        else:
            # AI 回合并列铺满内容宽度（demo 中 .ai-turn 铺满线程宽度）；重试按钮位于
            # 回合正下方、右下角（右缘略偏左内缩）
            wrap = _TurnWrap()
            v = QVBoxLayout(wrap)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(3)
            top = QHBoxLayout()
            top.setContentsMargins(0, 0, 0, 0)
            top.addWidget(bubble, 1)
            v.addLayout(top)
            btm = QHBoxLayout()
            btm.setContentsMargins(0, 0, 10, 0)   # 右侧内缩：按钮比气泡右缘略偏左
            btm.addStretch(1)
            btm.addWidget(bubble._retry_btn, 0, Qt.AlignmentFlag.AlignVCenter)
            v.addLayout(btm)
            # 包裹层按「回合实际高度」自报高度（见 _TurnWrap）：否则 Qt 的 HFW 估算
            # 会随正文增长虚高，在回合上下留出越来越大的空白
            wrap.set_turn(bubble)
            # 包裹层与重试行反向引用：任务结束徽章插入重试行首，与重试按钮同行
            bubble._wrap_lay = v
            bubble._retry_row = btm
            # 回合高度变化时同步钉住包裹层：等事件循环里的 LayoutRequest 会晚一帧，
            # 那一帧包裹层偏矮会把回合（含新落地的那行字）裁掉十几个像素
            bubble._wrap_sync = wrap.sync_height
            row.addWidget(wrap, 1)
        self.msg_lay.insertLayout(self.msg_lay.count() - 1, row)
        self._place_spinner_bottom()   # 新气泡加入后动画行移到最底部（AI 气泡下方外侧）
        # 淡入只用于用户气泡（小 QLabel）。AI 回合不做淡入：QGraphicsOpacityEffect 会把
        # 整棵子树重定向到离屏合成，而生成中每帧内容都在变 → 观感就是闪烁还掉帧；
        # 正文的流式落字本身已经是动画，不需要再叠一层（历史批量加载同样跳过）。
        if animate and align == "user":
            self._fade_in(bubble, self)
        self._scroll_bottom()
        return bubble

    def _effective_workflow(self) -> str:
        """当前会话实际生效的工作流：会话级@切换优先，否则跟随全局激活工作流"""
        st = self._sess.get(self._session_id) or {}
        if st.get("workflow"):
            return st["workflow"]
        return agent_workflow.active_workflow()

    def _update_wf_label(self):
        """刷新顶部「当前工作流」标签：会话专属用深蓝高亮，跟随全局用灰。
        新对话绑定 _default（内置默认）时用灰色提示，只有 @工作流 会话级切换才深蓝。"""
        if not hasattr(self, "wf_label"):
            return
        wf = self._effective_workflow()
        st = self._sess.get(self._session_id) or {}
        # 工作团：产品经理（领导者）工作流激活时标记团队模式
        team_tag = ""
        try:
            from zhuzhu_Copilot.core import agent_team
            if agent_team.is_leader_workflow(wf) and agent_team.team_active():
                team_tag = " · 团队模式"
        except Exception:
            pass
        if st.get("workflow") and st.get("workflow") != agent_workflow.DEFAULT_WORKFLOW:
            self.wf_label.setStyleSheet(f"color: {ACCENT}; font-size: 12px; font-weight: 700;")
            self.wf_label.setToolTip(f"当前对话使用专属工作流「{wf}」（@工作流 切换）")
        elif st.get("workflow"):
            self.wf_label.setStyleSheet(f"color: {TEXT_DIM}; font-size: 12px;")
            self.wf_label.setToolTip("当前对话使用内置默认工作流（_default）")
        else:
            self.wf_label.setStyleSheet(f"color: {TEXT_DIM}; font-size: 12px;")
            self.wf_label.setToolTip("当前对话跟随全局激活工作流")
        self.wf_label.setText(f"当前工作流: {wf}{team_tag}")

    def _token_stats_ui_context(self) -> dict:
        """统计浮层所需的界面侧信息（工作流 / 模型）。

        原顶栏「状态」按钮的悬停详情（tokens · 工作流 · 模型）已整体并入上下文统计浮层：
        tokens 取自引擎快照，工作流与模型属界面/会话状态，故在此注入。
        工作流优先取生效名（`_effective_workflow`，不带"当前工作流:"展示前缀），
        取不到时回退 wf_label 文本；全部用 __dict__/异常兜底：Qt 派生类 getattr 缺失会触达
        类级访问器而报 RuntimeError，骨架期或自定义 UI/UX 包缺控件时必须安全跳过。"""
        try:
            wf = str(self._effective_workflow() or "")
        except Exception:
            wf = ""
        if not wf:
            wl = self.__dict__.get("wf_label")
            wf = (wl.text() if wl is not None else "") or "内置默认工作流"
        mcb = self.__dict__.get("model_combo")
        mc = (mcb.currentText() if mcb is not None else "") or ""
        return {"workflow": wf, "model": mc}

    # ---------- token / 上下文统计浮层 ----------
    def _active_engine(self):
        """当前会话实际使用的引擎：优先会话内引擎（任务实际累计的那个），
        回退 self._engine（会话切换/后台任务时避免引用漂移）。

        用 __dict__ 取值：Qt 派生类的属性缺失会触达类级访问器并抛 RuntimeError
        （骨架期/测试轻代理），按仓内既有约定安全兜底。"""
        sess = self.__dict__.get("_sess") or {}
        sid = self.__dict__.get("_session_id") or ""
        st = sess.get(sid) or {}
        return st.get("engine") or self.__dict__.get("_engine")

    def _token_stats(self) -> dict:
        """统计浮层的完整数据快照 = 引擎侧 context_stats()（上游真实用量 / 窗口 / 阈值 /
        缓存命中 / 累计）+ 界面侧工作流与模型。

        无引擎（新对话、或引擎不支持统计）时仍返回 dict：浮层按"暂无请求数据"渲染数值，
        但工作流与模型照常可见 —— 这正是原「状态」按钮被并入后必须保留的信息。"""
        stats = {}
        eng = self._active_engine()
        fn = getattr(eng, "context_stats", None)
        if callable(fn):
            try:
                stats = dict(fn())
            except Exception:
                stats = {}
        ui = self._token_stats_ui_context()
        # 界面当前选择优先于引擎记录的模型名；两个键恒定存在（浮层与调用方无需再判缺省）
        stats["model"] = ui["model"] or stats.get("model") or ""
        stats["workflow"] = ui["workflow"]
        return stats

    def _reset_token_pop(self):
        """丢弃统计浮层（UI 重建/主题切换时调用）：尺寸与配色随新主题重算，
        同时解绑全局失焦守卫，避免重复挂载。"""
        pop = self.__dict__.pop("_token_pop", None)
        if pop is not None:
            try:
                from PyQt6 import sip
                if not sip.isdeleted(pop):
                    pop.hide()
                    pop.setParent(None)
                    pop.deleteLater()
            except Exception:
                pass
        self._token_dismiss_attach(False)
        self.__dict__.pop("_token_dismiss", None)
        self._token_pop_open = False

    def _ensure_token_pop(self):
        """惰性创建统计浮层（自由子控件，不进布局：位置由 _token_pop_geometry 计算）"""
        from PyQt6 import sip
        pop = self.__dict__.get("_token_pop")
        if pop is not None and not sip.isdeleted(pop):
            return pop
        pop = _TokenStatsPopover(self)
        pop.hide()
        self._token_pop = pop
        self._token_dismiss = _PopoverDismissFilter(
            pop, self.__dict__.get("token_btn"), self)
        return pop

    def _token_pop_size(self, pop) -> tuple:
        """浮层尺寸（宽, 高）：宽度 = 首选宽与"面板可用宽"取小（窄面板下不越界）；
        高度取布局 heightForWidth —— 卡片里有多个自动换行标签（阈值图例、缓存明细、
        提示语），QLabel 的 sizeHint 在换行时不准确，直接用会把底部文字裁掉
        （表现为文字被遮挡/只剩半行）。"""
        margin = SPACING_SM
        try:
            avail = max(240, int(self.width()) - 2 * margin)
        except Exception:
            avail = int(pop.PREFERRED_WIDTH)
        w = max(240, min(int(pop.PREFERRED_WIDTH), avail))
        h = 0
        lay = pop.layout()
        try:
            if lay is not None and lay.hasHeightForWidth():
                h = int(lay.heightForWidth(w))
        except Exception:
            h = 0
        if h <= 0:
            h = int(pop.sizeHint().height())
        return int(w), max(1, int(h))

    def _token_pop_geometry(self, pop) -> QRect:
        """浮层位置：默认贴触发按钮正下方并右对齐；下方空间不足则上翻到按钮之上，
        四周留出面板边距，任何窗口尺寸下都不出界。
        锚点判定用 isHidden（而非 isVisible）：面板尚未 show 时程序化唤起也要能定位。"""
        btn = self.__dict__.get("token_btn")
        w, h = self._token_pop_size(pop)
        gap = SPACING_SM
        margin = SPACING_SM
        if btn is not None and not btn.isHidden():
            anchor = btn.mapTo(self, QPoint(0, 0))
            x = anchor.x() + btn.width() - w
            below = anchor.y() + btn.height() + gap
        else:   # 自定义 UI/UX 包未提供按钮（由 API 直接唤起）：左上角落位
            anchor, x, below = QPoint(margin, margin), margin, margin
        x = max(margin, min(x, max(margin, self.width() - w - margin)))
        y = below
        if y + h > self.height() - margin:
            y = max(margin, anchor.y() - h - gap)   # 下方放不下 → 上翻
        return QRect(int(x), int(y), w, h)

    def _token_dismiss_attach(self, on: bool):
        """挂载/卸载全局失焦守卫（点击浮层外或 Esc → 收起浮层）。
        挂载状态由 _token_dismiss_on 记录，保证幂等（重复安装会被重复回调）。"""
        f = self.__dict__.get("_token_dismiss")
        app = QApplication.instance()
        if f is None or app is None:
            return
        if bool(self.__dict__.get("_token_dismiss_on")) == bool(on):
            return
        try:
            if on:
                app.installEventFilter(f)
            else:
                app.removeEventFilter(f)
        except Exception:
            return
        self._token_dismiss_on = bool(on)

    def open_token_stats(self):
        """打开统计浮层（按钮点击 / 自定义 UI/UX 包 / AI 均可调用）：
        先注入最新数据再算位置，避免首帧尺寸跳变"""
        try:
            pop = self._ensure_token_pop()
            pop.set_stats(self._token_stats())
            pop.apply_theme()
            _w, _h = self._token_pop_size(pop)
            pop.resize(_w, _h)
            pop.popup_animated(self._token_pop_geometry(pop))
            self._token_pop_open = True
            self._token_dismiss_attach(True)
        except Exception as e:
            import logging
            logging.getLogger(app_identity.APP_SLUG).warning("统计浮层打开失败: %s", e)

    def close_token_stats(self):
        """收起统计浮层（带淡出动画）；未打开时静默返回"""
        pop = self.__dict__.get("_token_pop")
        self._token_pop_open = False
        self._token_dismiss_attach(False)
        if pop is None:
            return
        try:
            from PyQt6 import sip
            if not sip.isdeleted(pop):
                pop.close_animated()
        except Exception:
            pass

    def toggle_token_stats(self):
        """统计浮层开合（入口按钮点击）"""
        if self.__dict__.get("_token_pop_open"):
            self.close_token_stats()
        else:
            self.open_token_stats()

    # ---------- zhuzhu Copilot 浮层（应用管理 / 防护 / 通用 / 工具） ----------
    COPILOT_W = 900     # 浮层首选尺寸（超出屏幕可用区时自动收窄）
    COPILOT_H = 580

    def _ensure_copilot_panel(self):
        """懒创建 Copilot 浮层并接好与 AI 面板的双向信号（创建一次，反复开合复用）"""
        cp = self.__dict__.get("_copilot_panel")
        if cp is not None:
            return cp
        from zhuzhu_Copilot.ui.main_window import CopilotPanel
        cp = CopilotPanel(None)
        cp.close_requested.connect(self.close_copilot_panel)
        cp.open_agent_requested.connect(self._show_self_front)
        cp.theme_changed.connect(self._on_copilot_theme_changed)
        self._copilot_panel = cp
        return cp

    def prewarm_copilot_panel(self):
        """启动时预创建浮层（不显示）：桌宠、托盘与首次应用扫描随程序启动，
        与浮层是否打开无关；同时让用户首次点击入口按钮时无构造延迟。"""
        try:
            self._ensure_copilot_panel()
        except Exception:
            import logging
            logging.getLogger(app_identity.APP_SLUG).warning("Copilot 浮层预创建失败", exc_info=True)

    def _show_self_front(self):
        """托盘 / 桌宠请求：确保 AI 面板可见并置前（主窗口已移除，它是唯一界面）"""
        self.show()
        self.raise_()
        self.activateWindow()

    def _on_copilot_theme_changed(self):
        """Copilot 浮层切换主题 → AI 面板就地重建，两界面保持同源"""
        try:
            self._retheme()
        except Exception:
            import logging
            logging.getLogger(app_identity.APP_SLUG).warning("Copilot 主题同步失败", exc_info=True)

    def _copilot_size(self) -> tuple:
        """浮层尺寸：取首选尺寸与屏幕可用区的较小者，保证不越界"""
        scr = self.screen() or QApplication.primaryScreen()
        avail = scr.availableGeometry() if scr is not None else QRect(0, 0, 1280, 800)
        w = int(min(self.COPILOT_W, max(680, avail.width() - 80)))
        h = int(min(self.COPILOT_H, max(460, avail.height() - 120)))
        return w, h

    def _copilot_geometry(self, cp) -> QRect:
        """浮层位置：对齐入口按钮左缘、位于其下方；越界时向屏内收拢，下方不足则上翻。
        锚点用 isHidden 判定（面板尚未 show 时程序化唤起也能定位）。"""
        btn = self.__dict__.get("copilot_btn")
        w, h = int(cp.width()), int(cp.height())
        gap, margin = 6, 8
        scr = self.screen() or QApplication.primaryScreen()
        avail = scr.availableGeometry() if scr is not None else QRect(0, 0, 1280, 800)
        if btn is not None and not btn.isHidden():
            anchor = btn.mapToGlobal(QPoint(0, btn.height() + gap))
        else:
            anchor = QPoint(avail.left() + margin, avail.top() + 60)
        x = min(max(anchor.x(), avail.left() + margin), avail.right() - w - margin)
        y = anchor.y()
        if y + h > avail.bottom() - margin:
            y = max(avail.top() + margin, anchor.y() - btn.height() - gap - h - gap) \
                if btn is not None else max(avail.top() + margin, avail.bottom() - h - margin)
        y = min(max(y, avail.top() + margin), avail.bottom() - h - margin)
        return QRect(int(x), int(y), w, h)

    def _copilot_dismiss_attach(self, on: bool):
        """挂载/卸载 Copilot 浮层的全局失焦守卫（点击浮层外或 Esc → 收起）。
        幂等：重复安装会被重复回调，故用 _copilot_dismiss_on 记录挂载状态。"""
        app = QApplication.instance()
        if app is None:
            return
        f = self.__dict__.get("_copilot_dismiss")
        if f is None:
            cp = self.__dict__.get("_copilot_panel")
            if cp is None:
                return
            # on_dismiss 交给宿主方法：外部收起时同步 _copilot_open 与守卫挂载状态
            f = _PopoverDismissFilter(cp, self.__dict__.get("copilot_btn"), self,
                                      on_dismiss=self.close_copilot_panel)
            self._copilot_dismiss = f
        if bool(self.__dict__.get("_copilot_dismiss_on")) == bool(on):
            return
        try:
            if on:
                app.installEventFilter(f)
            else:
                app.removeEventFilter(f)
        except Exception:
            return
        self._copilot_dismiss_on = bool(on)

    def open_copilot_panel(self):
        """在入口按钮下方丝滑弹出 Copilot 浮层（渐变 + 位移，与统计浮层同一手感）"""
        try:
            cp = self._ensure_copilot_panel()
            _w, _h = self._copilot_size()
            cp.resize(_w, _h)
            cp.popup_animated(self._copilot_geometry(cp))
            self._copilot_open = True
            self._copilot_dismiss_attach(True)
        except Exception:
            import logging
            logging.getLogger(app_identity.APP_SLUG).warning("Copilot 浮层打开失败", exc_info=True)

    def close_copilot_panel(self):
        """收起 Copilot 浮层（带淡出动画）；未打开时静默返回"""
        cp = self.__dict__.get("_copilot_panel")
        self._copilot_open = False
        self._copilot_dismiss_attach(False)
        if cp is None:
            return
        try:
            from PyQt6 import sip
            if not sip.isdeleted(cp):
                cp.close_animated()
        except Exception:
            pass

    def _copilot_visible(self) -> bool:
        """浮层当前是否处于"已打开"状态。

        以浮层实际可见性为准，而非仅看宿主状态位：
        正在收起动画中的窗口视为"未打开"，这样连点两次也能立刻重新弹出；
        被外部（点击别处 / Esc）收起后状态天然一致，不会出现"第一次点击只纠正状态"。
        """
        cp = self.__dict__.get("_copilot_panel")
        if cp is None:
            return False
        try:
            from PyQt6 import sip
            if sip.isdeleted(cp):
                return False
        except Exception:
            return False
        if callable(getattr(cp, "is_closing", None)) and cp.is_closing():
            return False
        return bool(cp.isVisible())

    def toggle_copilot_panel(self):
        """Copilot 浮层开合（入口按钮点击）"""
        if self._copilot_visible():
            self.close_copilot_panel()
        else:
            self.open_copilot_panel()

    def _refresh_token_pop(self):
        """浮层可见时随 400ms 状态轮询刷新数据与位置（不可见则零开销返回）；
        位置仅在无动画进行时同步，避免与弹出动画抢坐标。"""
        pop = self.__dict__.get("_token_pop")
        if pop is None:
            return
        from PyQt6 import sip
        if sip.isdeleted(pop):
            self.__dict__.pop("_token_pop", None)
            return
        if not pop.isVisible():
            if self.__dict__.get("_token_pop_open"):
                # 已自行收起（点击外部/Esc）→ 同步开合状态并解绑守卫
                self._token_pop_open = False
                self._token_dismiss_attach(False)
            return
        try:
            pop.set_stats(self._token_stats())
            if not pop.animation_running():
                # 数据变化可能改行数（阈值图例两条/缓存明细换行）→ 尺寸也按内容重算，
                # 只同步位置会让卡片高度停在旧值，底部文字被裁掉（文字被遮挡的观感）
                _w, _h = self._token_pop_size(pop)
                if pop.width() != _w or pop.height() != _h:
                    pop.resize(_w, _h)
                geo = self._token_pop_geometry(pop)
                if pop.geometry() != geo:
                    pop.setGeometry(geo)
        except Exception:
            pass

    def _notify_blocked(self, text: str, title: str = "操作未完成"):
        """阻断/失败提示改走右下角通知：气泡底部外侧不再出现任何提示小字（那里只保留
        打字指示器），但「点了没反应 / 任务没跑起来」必须有明确回执 —— 通知不浮在消息流里、
        也不会随任务推进不断累积。"""
        try:
            self._toast(title, str(text), warn=True)
        except Exception:
            pass

    def _add_badge(self, text: str, color: str):
        """AI 气泡外的任务结束徽章（Stop by user / Successfully / Error）。
        徽章插入最后一条 AI 气泡的重试行首：与重试按钮同行（徽章左、重试右），
        紧贴气泡下方；附着未生效（异常布局情形）则退回消息流独立行（原行为）。"""
        lbl = QLabel(f"<span>{_esc(text)}</span>")
        lbl.setStyleSheet(
            f"color: {color};"
            f"background: {color}22;"
            "border: 1px solid " + color + ";"
            "border-radius: 7px;"    # 边框圆角随框体再次收小(9×0.8≈7)；字号保持12
            "padding: 0 6px; font-size: 12px; font-weight: 600; line-height: 16px;")
        # 胶囊按内容自适应宽度：保持在水平行内不拉伸
        lbl.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self._attach_capsule(lbl, 0)

    def _attach_capsule(self, lbl: QLabel, at: int):
        """把胶囊徽章附着到最后一条 AI 气泡重试行（气泡末尾外侧）。
        at=0 行首（状态徽章）；at=1 紧随状态徽章（文件变更徽章，-N 红 / +M 绿）。
        附着失败退回消息流独立行。"""
        b = self._ai_bubble
        wrap_lay = getattr(b, "_wrap_lay", None) if (b is not None
                                                     and self._bubble_alive(b)) else None
        if wrap_lay is not None:
            retry_row = getattr(b, "_retry_row", None)
            if retry_row is not None:
                retry_row.insertWidget(at, lbl)   # 徽章左、重试右，同一行
                if retry_row.indexOf(lbl) >= 0:
                    self._scroll_bottom()
                    return
            wrap_lay.insertWidget(1, lbl)   # 无重试行：退回附着到包裹层扩展位置
            if wrap_lay.indexOf(lbl) >= 0:
                self._scroll_bottom()
                return
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(lbl)
        row.addStretch(1)
        self.msg_lay.insertLayout(self.msg_lay.count() - 1, row)
        self._place_spinner_bottom()   # 动画行始终保持在消息流最底部
        self._scroll_bottom()

    def _add_change_badge(self, delta: dict):
        """气泡末尾外侧的文件变更徽章：-N 删除行（红色）/ +M 新增行（绿色）。
        仅本轮有文件写入时显示，tooltip 提供逐文件增删明细。"""
        delta = delta or {}
        added = int(delta.get("added") or 0)
        removed = int(delta.get("removed") or 0)
        if not added and not removed:
            return
        html = (f'<span style="color:{ERR};font-weight:700;">-{removed}</span>'
                f' <span style="color:{OK};font-weight:700;">+{added}</span>')
        lbl = QLabel(f"<span>{html}</span>")
        lbl.setStyleSheet(
            f"color: {TEXT_DIM}; background: {BORDER}22;"
            f"border: 1px solid {BORDER}; border-radius: 7px;"
            "padding: 0 6px; font-size: 12px; font-weight: 600; line-height: 16px;")
        lbl.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        files = delta.get("files") or {}
        if files:
            tip_lines = [f"{p}  +{a} -{r}" for p, (a, r) in sorted(files.items())]
            if len(tip_lines) > 12:
                tip_lines = tip_lines[:11] + [f"…共 {len(files)} 个文件"]
            lbl.setToolTip("文件变更：\n" + "\n".join(tip_lines))
        self._attach_capsule(lbl, 1)

    def _place_spinner_bottom(self):
        """把任务动画行移到消息流最底部（AI 气泡在下方滚动不会盖住它）"""
        if self._spinner_row is None:
            return
        # 引用失效防护：主题重建/会话切换后旧布局可能已被销毁，
        # 对已删除的 QVBoxLayout 执行 insertLayout 会抛 RuntimeError，
        # 进而中断 _render_history_all（气泡清空的根因）——此处安全清理并退出
        try:
            # 已在目标位置（倒数第二项，最后一项为尾部 stretch）→ 直接跳过：
            # 状态更新很频繁，反复 takeAt/insertLayout 会让整条消息区重排一次，
            # 生成过程中表现为闪烁（这是「聊天气泡闪烁」的主要来源之一）。
            _n = self.msg_lay.count()
            if _n >= 2 and self.msg_lay.itemAt(_n - 2).layout() is self._spinner_row:
                return
            for i in range(_n):
                if self.msg_lay.itemAt(i).layout() is self._spinner_row:
                    self.msg_lay.takeAt(i)
                    break
            self.msg_lay.insertLayout(self.msg_lay.count() - 1, self._spinner_row)
        except RuntimeError:
            self._spinner = None
            self._spinner_lbl = None
            self._spinner_row = None
            return
        self._scroll_bottom()   # 移动后确保滚到底部，动画行不被遮挡

    # ---------- 发送/停止融合按钮状态与转圈动画 ----------
    @staticmethod
    def _spinner_icon(angle: int, color: str, size: int = 16) -> QIcon:
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(color), 2.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawArc(1, 1, size - 2, size - 2, -(angle % 360) * 16, 270 * 16)
        p.end()
        return QIcon(pm)

    def _set_action_idle(self):
        """空闲：输入框为空显示灰蓝发送按钮，有内容切换深蓝（可发送）"""
        self._action_anim.stop()
        self.action_btn.setIcon(_line_icon("send", self._btn_icon_sz, "#FFFFFF"))
        self.action_btn.setStyleSheet(
            _BTN_PRIMARY if self.input.toPlainText().strip() else _BTN_DIM)
        self.action_btn.setEnabled(True)
        self.action_btn.setToolTip("发送")

    def _ensure_stop_btn(self):
        """确保发送/停止融合按钮处于「停止」状态（不重启转圈动画，避免误导）"""
        if self._action_anim.isActive():
            self._action_anim.stop()
        self.action_btn.setIcon(_line_icon("stop", self._btn_icon_sz, "#FFFFFF"))
        self.action_btn.setStyleSheet(_BTN_PRIMARY)
        self.action_btn.setEnabled(True)
        self.action_btn.setToolTip("停止当前任务")

    def _sync_action_style(self, *_):
        """输入框内容变化：任务空闲时刷新发送按钮配色（空→灰蓝，有内容→深蓝）。
        任务运行中（task_active / 评估中 / 引擎线程存活）保持「停止」按钮状态——
        即使按钮曾被误复位为发送，输入文字也会立即恢复为停止，绝不变为发送。"""
        running = bool(self._task_active)
        if not running:
            ep = self._eval_pending
            if ep is not None and ep[0] == self._session_id:
                running = True
        if not running:
            st = self._cur()
            eng = st.get("engine") if isinstance(st, dict) else None
            if eng is not None and eng._thread is not None and eng._thread.is_alive():
                running = True
        if not running:
            self._set_action_idle()
            return
        # 任务运行中：若按钮不在「停止」态则立即恢复（用户点停止后保持禁用态不覆盖）
        if not self._user_stopped and self.action_btn.toolTip() != "停止当前任务":
            self._ensure_stop_btn()

    def _set_action_busy(self):
        """运行中：白色转圈动画（可点击停止）"""
        self._action_anim_angle = 0
        self.action_btn.setStyleSheet(_BTN_PRIMARY)
        self.action_btn.setEnabled(True)
        self.action_btn.setToolTip("停止当前任务")
        self._action_anim.start()

    def _set_action_stopping(self):
        """停止中：红底转圈（禁用）"""
        self._action_anim_angle = 0
        self.action_btn.setStyleSheet(_BTN_DANGER)
        self.action_btn.setEnabled(False)
        self.action_btn.setToolTip("停止中…")
        self._action_anim.start()

    def _tick_action_anim(self):
        self._action_anim_angle += 30
        self.action_btn.setIcon(self._spinner_icon(self._action_anim_angle, "#FFFFFF"))

    def _tick_combo_spin(self):
        """会话下拉转圈动画：有运行中/待确认会话才刷新视图（避免无谓重绘）"""
        busy = False
        for st in self._sess.values():
            if st.get("task_active") or st.get("pending_confirm") or st.get("pending_ask"):
                busy = True
                break
        if not busy:
            return
        self._spin_angle = (self._spin_angle + 30) % 360
        try:
            self.session_combo.view().viewport().update()
        except Exception:
            pass

    def _stop_button_anim(self):
        """任务结束：停止动画并恢复空闲发送状态"""
        self._set_action_idle()

    def _on_action_clicked(self, *_):
        """融合按钮点击：空闲→发送；运行中→停止"""
        ep = self._eval_pending
        if self._task_active or (ep is not None and ep[0] == self._session_id):
            self._stop()
        else:
            self._send()

    # ---------- 提示词优化（输入框右侧魔法棒按钮，基于上下文异步优化） ----------
    def _optimize_context(self, max_rounds: int = 6) -> str:
        """构建当前对话上下文摘要（最近若干轮用户/助手消息），用于优化提示词"""
        st = self._sess.get(self._session_id) or {}
        eng = st.get("engine") or self._engine
        msgs = getattr(eng, "_messages", None) if eng else None
        if not msgs:
            return ""
        out = []
        for m in msgs:
            role = m.get("role")
            if role not in ("user", "assistant"):
                continue
            content = str(m.get("content") or "").strip()
            if not content:
                continue
            if len(content) > 400:
                content = content[:400] + "…"
            out.append(f"{'用户' if role == 'user' else '助手'}: {content}")
        return "\n".join(out[-max_rounds * 2:])

    def _on_optimize_clicked(self, *_):
        """优化输入框中的提示词（结合当前对话上下文，后台异步调用 LLM）"""
        text = self.input.toPlainText().strip()
        if not text:
            self._notify_blocked("请先输入内容，再点击优化提示词")
            return
        if getattr(self, "_optimizing", False):
            return
        self._optimizing = True
        self.optimize_btn.setEnabled(False)
        context = self._optimize_context()
        cfg = dict(self._llm_config() or {})
        threading.Thread(target=self._optimize_worker,
                         args=(text, context, cfg), daemon=True).start()

    def _optimize_worker(self, text: str, context: str, cfg: dict):
        """后台线程：调用 LLM 优化提示词，完成后经信号回主线程"""
        try:
            from zhuzhu_Copilot.core import agent_llm
            client = agent_llm.LLMClient(
                cfg.get("base_url") or agent_llm.DEFAULT_BASE_URL,
                cfg.get("api_key") or agent_llm.DEFAULT_API_KEY,
                cfg.get("model") or agent_llm.DEFAULT_MODEL)
            sys_p = (
                "你是提示词优化专家。根据用户的原始输入与对话上下文，把提示词优化为更清晰、"
                "具体、可执行的形式，保留用户核心意图，不改变其目标。"
                "只输出优化后的提示词本身，不要任何解释、前缀或 markdown 代码块包裹。"
            )
            msgs = [{"role": "system", "content": sys_p}]
            if context:
                msgs.append({"role": "user",
                             "content": f"对话上下文：\n{context}\n\n---\n原始输入：\n{text}"})
            else:
                msgs.append({"role": "user", "content": f"原始输入：\n{text}"})
            resp = client.chat(msgs, max_tokens=1024, timeout=60)
            out = ((resp or {}).get("text") or "").strip()
            out = out.strip("`")
            if out.lower().startswith("markdown"):
                out = out[len("markdown"):].strip()
            out = out.strip("`").strip()
            if not out:
                self.optimize_signal.emit("0", "AI 未返回优化结果，请重试")
                return
            self.optimize_signal.emit("1", out)
        except Exception as e:
            self.optimize_signal.emit("0", f"优化失败: {e}")

    def _on_optimize_done(self, ok: str, result: str):
        """优化完成：成功则替换输入框内容，失败则提示"""
        self._optimizing = False
        self.optimize_btn.setEnabled(True)
        if ok == "1":
            self.input.setPlainText(result)
            self.input.setFocus()
        else:
            self._notify_blocked(result, "提示词优化失败")

    # ---------- AI 实时趣味互动（俏皮锐评气泡，不计入上下文/本地记忆） ----------
    _FUN_INTERVALS = {
        "frequent": (60, 180),    # 1~3 分钟
        "normal":   (180, 360),   # 3~6 分钟（默认）
        "relaxed":  (300, 600),   # 5~10 分钟
    }

    def _fun_switch_enabled(self) -> bool:
        """趣味互动默认开启；设置页可关闭（QSettings agent_fun）"""
        try:
            v = app_identity.qsettings().value("agent_fun", "1")
            return str(v).strip().lower() in ("1", "true", "yes", "on")
        except Exception:
            return True

    def _fun_interval_seconds(self) -> int:
        """按设置档位在当前区间内随机取一个截屏分析间隔（秒）"""
        import random
        mode = str(app_identity.qsettings()
                   .value("agent_fun_interval", "normal")).strip().lower()
        lo, hi = self._FUN_INTERVALS.get(mode, self._FUN_INTERVALS["normal"])
        return random.randint(lo, hi)

    def _fun_schedule(self):
        """随机间隔排定下一次趣味互动；用户近 20 秒内有输入则顺延到 60 秒后"""
        if not self._fun_switch_enabled():
            self._fun_timer.stop()
            return
        delay = 60 if time.time() - self._fun_last_input_at < 20 \
            else self._fun_interval_seconds()
        self._fun_timer.start(int(delay * 1000))

    def _fun_capture(self) -> str:
        """主线程截全屏 → PNG data URL（无网格/无准星），失败返回空串"""
        try:
            png = agent_screen.capture_screen_png()
            if not png:
                return ""
            return "data:image/png;base64," + base64.b64encode(png).decode("ascii")
        except Exception:
            return ""

    def _fun_analyze_worker(self, data_url: str, cfg: dict):
        """后台线程：对截屏生成俏皮锐评（一次性，不进入任何上下文/本地记忆）"""
        from zhuzhu_Copilot.core import agent_llm as _llm
        try:
            client = _llm.LLMClient(
                cfg.get("base_url") or _llm.DEFAULT_BASE_URL,
                cfg.get("api_key") or _llm.DEFAULT_API_KEY,
                cfg.get("model") or _llm.DEFAULT_MODEL)
            msgs = [
                {"role": "system", "content": (
                    "你是超可爱又带点损的屏幕锐评小助手。用户把实时屏幕截图发给你。"
                    "请用 1~3 句口语化的俏皮小短评点评屏幕上正在发生的事，语气可爱幽默、"
                    "有梗、轻松；最多 1 个 emoji 点缀。不要提'截图''屏幕''作为AI'等词，"
                    "直接以朋友口吻对用户说话。只输出锐评本身，不要任何解释或前缀。")},
                {"role": "user",
                 "content": _llm.build_content("看看我在干啥，给我个可爱又有梗的锐评～", [data_url])},
            ]
            resp = client.chat(msgs, max_tokens=150, timeout=45)
            text = ((resp or {}).get("text") or "").strip()
            self.fun_signal.emit(text if text else "")
        except Exception:
            self.fun_signal.emit("")

    def _fun_tick(self):
        """定时器到点：任务空闲且面板可见时截屏分析并弹气泡，否则顺延到下次"""
        if not self._fun_switch_enabled():
            self._fun_timer.stop()
            return
        # 任务执行中或面板隐藏时不打扰，顺延
        if self._task_active or not self.isVisible():
            self._fun_schedule()
            return
        data_url = self._fun_capture()
        self._fun_schedule()
        if not data_url:
            return
        cfg = dict(self._llm_config() or {})
        threading.Thread(target=self._fun_analyze_worker,
                         args=(data_url, cfg), daemon=True).start()

    def _fun_show(self, text: str):
        """主线程：在屏幕底部正中弹出小熊糖果风锐评气泡，8 秒后淡出消失。空串=失败，静默重排。"""
        # 本气泡为趣味联动的特例：小熊糖果可爱风（不受全局简约/禁 emoji 规则约束）
        if not text:
            self._fun_schedule()
            return
        app = QApplication.instance()
        if app is None:
            self._fun_schedule()
            return
        try:
            from PyQt6.QtCore import QEasingCurve, QPropertyAnimation
            from PyQt6.QtWidgets import QGraphicsOpacityEffect
            b = self._fun_bubble
            if b is None:
                b = _CuteBubble("")
                b.setWindowFlags(Qt.WindowType.Tool
                                 | Qt.WindowType.FramelessWindowHint
                                 | Qt.WindowType.WindowStaysOnTopHint)
                self._fun_bubble = b
            b.setText(text)
            b.adjustSize()
            scr = app.primaryScreen()
            sg = scr.availableGeometry() if scr else app.screenAt(QCursor.pos()).availableGeometry()
            bw, bh = b.width(), b.height()
            b.move(sg.x() + (sg.width() - bw) // 2, sg.y() + sg.height() - bh - 40)
            # 淡入
            op = QGraphicsOpacityEffect(b)
            b.setGraphicsEffect(op)
            anim = QPropertyAnimation(op, b"opacity", b)
            anim.setDuration(280)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.start()
            b.show()
            b.raise_()
            # 8 秒后淡出并隐藏
            def _fade():
                op2 = QGraphicsOpacityEffect(b)
                b.setGraphicsEffect(op2)
                fo = QPropertyAnimation(op2, b"opacity", b)
                fo.setDuration(300)
                fo.setStartValue(1.0)
                fo.setEndValue(0.0)
                fo.setEasingCurve(QEasingCurve.Type.InCubic)
                fo.finished.connect(b.hide)
                fo.start()
            QTimer.singleShot(8000, _fade)
        except Exception:
            pass
        finally:
            self._fun_schedule()

    def _on_fun_input(self, *_):
        """用户对输入框有任何输入即重置空闲判定（趣味互评避让正在打字）"""
        self._fun_last_input_at = time.time()
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, self._restore_to_front)

    def _restore_to_front(self):
        """用户正在交互 → 主动抬升窗口到最前（防止被其他程序遮挡）"""
        self.raise_()
        self.activateWindow()

    def _on_fun_settings_changed(self):
        """设置页保存趣味互动后即时生效：按新开关重排或停止"""
        if self._fun_switch_enabled():
            self._fun_timer.stop()
            self._fun_schedule()
        else:
            self._fun_timer.stop()
            if self._fun_bubble is not None:
                self._fun_bubble.hide()

    def _scroll_bottom(self):
        # 流式高频调用时合并：40ms 批量滚动一次（≈刷新节拍，跟手不跳）+ 400ms 兜底
        # （气泡高度与布局异步稳定后确保滚到最底部），避免每个 token 触发滚动重排
        if self._scroll_pending:
            return
        self._scroll_pending = True
        QTimer.singleShot(_SCROLL_TICK_MS, self._do_scroll_bottom)
        QTimer.singleShot(400, self._do_scroll_bottom)

    def _do_scroll_bottom(self):
        self._scroll_pending = False
        bar = self.msg_area.verticalScrollBar()
        bar.setValue(bar.maximum())

    # ---------- AI 气泡内容（思考 / 操作 / 正文 一体化） ----------
    def _ensure_ai_bubble(self):
        if self._ai_bubble is None or not self._bubble_alive(self._ai_bubble):
            self._ai_bubble = self._add_bubble("", "ai")
            # 回合开始计时（耗时徽章实时刷新；链接与菜单在 _add_bubble 内已接入）。
            # 取**任务起点**（用户发送 / @子Agent 直呼那一刻，见 _turn_started_at），
            # 而不是「第一个事件到达」—— 否则慢首字与「派发子 Agent 后长时间无输出」
            # 这一段静默期不计入时长（用户反馈的「子 Agent 调用时不计入时长」）。
            start = self.__dict__.get("_turn_started_at")
            self._turn_started_at = 0.0      # 用一次即清零，绝不串到下一回合
            self._ai_bubble.t_start = start or time.time()
            self._ai_bubble.turn_meta = ""
        # 每次同步段列表引用：新回复可能复用旧回合，注册表必须指向当前 _segments，
        # 否则子 Agent 块折叠/展开会在过期列表里找不到段而失效
        self._bubble_segs[id(self._ai_bubble)] = self._segments
        return self._ai_bubble

    def _repoint_live_bubble_segs(self, archived: list) -> None:
        """归档当前回复段时调用：把注册表里仍指向活动段列表（self._segments）
        的气泡重定向到归档副本。

        背景：流式期间 _ensure_ai_bubble/_add_ai_group_bubble 会把气泡注册到
        self._segments 这【同一个活动列表对象】上，而归档路径是就地
        _segments.clear() 复用——不重指向的话，所有历史气泡的折叠/展开注册
        都指向被清空又累积了新一轮内容的列表，点击折叠时整条对话的气泡内容
        都会被替换成最新一轮的输出（已修复的严重 BUG）。"""
        for bid, segs in list(self._bubble_segs.items()):
            if segs is self._segments:
                self._bubble_segs[bid] = archived

    @staticmethod
    def _bubble_alive(widget) -> bool:
        """控件是否仍可用（未被 deleteLater 真正销毁）。

        消息流里既有 QLabel（用户气泡/徽章）也有事件流回合容器（QWidget 组合），
        不能再靠 QLabel.text() 探活，统一用 sip 判定 C++ 对象是否已删除。
        """
        if widget is None:
            return False
        try:
            from PyQt6 import sip
            return not sip.isdeleted(widget)
        except Exception:
            return False

    def _font_scale(self) -> float:
        """全屏/非全屏字体始终保持原 5 号大小（14px），不随窗口缩放"""
        return 1.0

    def _scale_user_html(self, src: str, s: float) -> str:
        """把用户气泡原始富文本按缩放系数放大（字号 14px、图片 200px）"""
        return src.replace("font-size:14px", f"font-size:{int(14 * s)}px") \
                  .replace('width="200"', f'width="{int(200 * s)}"')

    def _seg_block_cached(self, seg: dict, i: int, t: str, f_main: int, f_sm: int,
                          f_op: int, img_w: int, sig: tuple = None):
        """按「内容签名」缓存「段 → 事件流区块 (kind, payload)」的映射结果。

        性能：流式刷新只重算正在增长的段（含命令转义、超长截断等 O(n) 工作），
        其余段直接命中缓存，避免每 60ms 全量重算（长回复时 O(n²) 导致输出明显卡顿）。
        缓存键为内容签名而非对象 id：会话切换/历史重载时相同内容的段可直接复用。
        调用方已算好签名时经 sig 传入，避免同一段每 tick 重复求签名。
        返回值允许为 None（该段不产生区块）。"""
        key = sig if sig is not None else (i, img_w, _THEME_VERSION) + _seg_sig(seg)
        if key in self._seg_cache:
            return self._seg_cache[key]
        block = self._seg_block(seg, i, t, f_main, f_sm, f_op, img_w)
        self._seg_cache[key] = block
        if len(self._seg_cache) > 200:
            # 按插入序裁剪只保留最近 100 项：长会话反复加载时缓存不再无限膨胀，
            # 避免大 dict 拖慢哈希查找与占内存（当前回合的热段始终会重新入缓存）
            for _k in list(self._seg_cache)[: len(self._seg_cache) - 100]:
                del self._seg_cache[_k]
        return block

    def _seg_block(self, seg: dict, i: int, t: str, f_main: int, f_sm: int,
                   f_op: int, img_w: int):
        """单段 → 事件流区块 (kind, payload)：demo 结构映射的唯一入口。

        新增段类型只需在此登记，组件侧自动获得对应外壳：
          think                    → 思考气泡（正文在此渲染，外壳/折叠由组件提供）
          op（含命令全文）          → 命令块（标题栏工具名 + `$ 命令` + 输出）
          op（仅工具名，可带 out）  → 工具调用行（图标壳 + 工具名 + 输出紧贴下一行）
          result                   → 命令块（标题栏「执行结果」 + 输出）
          text                     → 正文纯文本（永不参与过程区折叠）
          其余(ask/sub/progress/image/mark) → 通用富文本块

        关于输出挂载：工具/命令的输出由 `_on_result` **就地续写进对应 op 段**（见
        `_attach_out_seg`），因此「调用在上、输出在下」出现在同一个区块内；只有找不到
        对应工具行（历史数据 / 无状态事件）时才落回独立的 result 段。
        """
        cb = chat_bubbles
        if t == "think":
            body = self._render_seg_html(seg, i, t, f_main, f_sm, f_op, img_w) or ""
            # sid = 段序号：思考气泡据此判断「还是不是同一段思考」。同一段持续落字时
            # 序号不变，气泡必须保留用户手动展开的折叠态；换段才回到自动折叠判定。
            return (cb.KIND_THINK, {"tag": "", "body": body, "sid": i})
        if t == "op":
            name = self._op_display(seg)
            cmd = str(seg.get("cmd") or "").strip()
            out = str(seg.get("out") or "")
            if cmd:
                return (cb.KIND_CMD,
                        {"label": name or "命令", "cmd": _cmd_html(cmd), "out": out})
            return (cb.KIND_TOOL, {"name": name, "meta": seg.get("meta", ""),
                                   "params": {}, "ico": seg.get("ico"), "out": out,
                                   "tip": seg.get("tip", "")})
        if t == "result":
            out = self._render_seg_html(seg, i, t, f_main, f_sm, f_op, img_w) or ""
            return (cb.KIND_CMD,
                    {"label": "执行结果", "cmd": _cmd_html(seg.get("cmd")), "out": out})
        html = self._render_seg_html(seg, i, t, f_main, f_sm, f_op, img_w)
        if html is None:
            return None
        kind = cb.KIND_STREAM if t == "text" else cb.KIND_RICH
        return (kind, {"html": html})

    @staticmethod
    def _op_display(seg: dict) -> str:
        """op 段展示名：优先 name 字段，回退 html（去前缀竖线）"""
        name = str(seg.get("name") or "").strip()
        if name:
            return name
        return str(seg.get("html") or "").lstrip("▎").strip()

    def _seg_blocks(self, segs: list) -> list:
        """AI 段序列 → 事件流区块 [(kind, payload, sig)]（回合容器按签名增量渲染）"""
        s = self._font_scale()
        f_main, f_sm, f_op = int(14 * s), int(11 * s), int(13 * s)
        img_w = self._ai_img_width()   # 截图缩略图随回合内容宽度换算（约占 40%）
        out, think_seen = [], 0
        for i, seg in enumerate(segs):
            t = seg.get("type")
            if t == "split":
                continue
            sig = (i, img_w, _THEME_VERSION) + _seg_sig(seg)
            # 签名直接传下去：缓存键与区块签名是同一个元组，避免每段每 tick 算两遍
            block = self._seg_block_cached(seg, i, t, f_main, f_sm, f_op, img_w, sig)
            if block is None:
                continue
            kind, payload = block
            if kind == chat_bubbles.KIND_THINK:
                # 标签胶囊：首个思考段属规划阶段，其后的思考段属执行阶段（demo PLAN/EXEC）
                payload = dict(payload, tag="PLANNING" if think_seen == 0 else "EXEC")
                think_seen += 1
            out.append((kind, payload, sig))
        return self._mark_body_blocks(out)

    @staticmethod
    def _mark_body_blocks(blocks: list) -> list:
        """标注「正文」归属：只有**最后一段**正文在过程区收起后仍然可见。

        多轮任务里 AI 会在工具循环之间输出中间回复（同样是 text 段），它们属于执行
        过程的一部分；若一律按「正文」处理，回合结束后这些中间文字会残留在折叠后的
        视图里（用户反馈的「过程未完全折叠，有 AI 回复的文字出现」）。

        注意：标注一律**写新 dict**，绝不就地改写入参 payload —— 它们是段级渲染缓存里的
        同一个对象，就地加键会污染缓存（见 test_body_marking_keeps_only_last_stream）。
        """
        body = None
        for i, (kind, _payload, _sig) in enumerate(blocks):
            if kind == chat_bubbles.KIND_STREAM:
                body = i
        if body is None:
            return blocks
        return [(kind, dict(payload, proc=(i != body)), sig) if kind == chat_bubbles.KIND_STREAM
                else (kind, payload, sig)
                for i, (kind, payload, sig) in enumerate(blocks)]

    def _render_seg_html(self, seg: dict, i: int, t: str,
                         f_main: int, f_sm: int, f_op: int, img_w: int):
        """渲染单个段为富文本（_build_ai_html 的逐段实现，内容不变时被缓存跳过）"""
        if t == "think":
            # 全文入块：思考正文不在渲染层截断（截断会让「继续查看」展开后仍是残文）。
            # 体积由 ThinkBubble 的折叠承载 —— 折叠态只钉 5 行高，展开才铺全文。
            # 这里只给「已显示前缀」：落字节奏未推进到的尾字还不该出现（见 _shown_len）。
            body = (seg.get("html", "") or "")[:_shown_len(seg)]
            # 只返回思考正文：标题行、图标壳、tag 胶囊、超 5 行折叠与「继续查看」按钮
            # 全部由 demo 同构的 ThinkBubble 组件承载（字色/字号/行高由组件 QSS 统一给定，
            # 此处不再内联，避免覆盖组件样式）。
            return f'<div>{body}</div>'
        if t == "ask":
            # AI 提问（ask_user 工具）：问题陈列在上，用户弹窗回答陈列在下
            q_html = _esc(str(seg.get("q") or "")).replace("\n", "<br/>")
            answered = bool(seg.get("answered"))
            answer = str(seg.get("answer") or "").strip()
            if not answered:
                answer_html = (f'<span style="color:{TEXT_DIM};">等待你的回答…</span>')
            else:
                a_html = _esc(answer).replace("\n", "<br/>")
                if answer in ("（用户未作答）", "（用户取消回答）"):
                    answer_html = f'<span style="color:{TEXT_DIM};">{a_html}</span>'
                else:
                    answer_html = a_html
            return (
                f'<div style="margin-top:6px;">'
                f'<div style="color:{ACCENT};font-size:{f_sm}px;font-weight:600;">AI 提问</div>'
                f'<div style="color:{TEXT};font-size:{f_main}px;background:{PANEL};'
                f'border:1px solid {BORDER};border-radius:8px;padding:6px 10px;'
                f'margin:2px 0;">{q_html}</div>'
                f'<div style="color:{TEXT_DIM};font-size:{f_sm}px;font-weight:600;">你的回答</div>'
                f'<div style="color:{TEXT};font-size:{f_main}px;margin:2px 0 2px 10px;'
                f'border-left:2px solid {ACCENT};padding-left:8px;">{answer_html}</div>'
                f'</div>')
        if t == "result":
            # 命令执行结果：只返回输出正文（命令全文与标题栏由 demo 同构的 CmdBlock
            # 组件承载，输入在上、输出在下），超长截断保证单块体积有界。
            body = seg["html"]
            truncated = False
            if len(body) > _RESULT_TRUNCATE:
                # 超长命令输出截断展示：单块富文本体积随字符数线性增长，
                # 几万字符的单段会拖慢该块的布局与滚动（长对话卡顿源）
                body = body[:_RESULT_TRUNCATE] + "…"
                truncated = True
            html = _linkify(body)
            if truncated:
                html += (
                    f'<div style="color:{TEXT_DIM};font-size:{f_sm}px;">'
                    f'输出过长已截断显示（完整 {len(seg["html"])} 字符）</div>')
            return html
        if t == "progress":
            # 下载进度条：AI 气泡内实时渲染（面板轮询快照更新）
            pct = max(0, min(100, int(seg.get("pct") or 0)))
            bw = 220
            fill = int(bw * pct / 100)
            return (
                f'<div style="margin:12px 0 6px;">'
                f'<div style="background:{BG};border:1px solid {BORDER};border-radius:6px;'
                f'height:10px;width:{bw}px;">'
                f'<div style="background:{ACCENT};height:10px;width:{fill}px;'
                'border-radius:6px;"></div></div>'
                f'<div style="color:{TEXT_DIM};font-size:11px;margin-top:3px;">'
                f'{_esc(seg.get("text") or "下载中…")}</div></div>')
        if t == "image":
            # 截图融入主对话气泡：圆角缩略图 + 细边框，不显示“已截屏”等提示小字
            url = seg.get("url", "")
            return (
                f'<div style="padding-left:30px;">'
                f'<img src="{url}" width="{img_w}" style="border-radius:10px;'
                f'border:1px solid {BORDER};display:block;margin:12px 0 12px 0;"></div>')
        if t == "text":
            # 流式期间也走增量 markdown 渲染（块级缓存）：已完成块（表格/标题/列表/代码）
            # 即时解析为富文本，仅尾部增长块每次刷新重算，长回复不卡顿。
            # 旧实现流式期间纯文本显示，表格等 markdown 要等流式结束才解析，体验滞后。
            # 渲染的输入是「已显示前缀」（落字节奏，见 _shown_len），因此这里逐字增长。
            seg.setdefault("_rd_cache", {})
            body = _render_text_incr(seg["raw"][:_shown_len(seg)], seg["_rd_cache"])
            return (f'<div style="color:{TEXT};font-size:{f_main}px;">{body}</div>')
        if t == "mark":
            return (f'<div style="color:{TEXT_DIM};font-size:{f_sm}px;">'
                    f'{_linkify(seg["html"])}</div>')
        if t == "sub":
            # 子 Agent 输出块：与主 Agent 共用同一气泡，深蓝标签 + 缩进内容区分来源。
            # 折叠态（默认，含老数据）只渲染一行摘要 + 点击展开：展开态由多步工具输出
            # 拼成，实测单块可达 ~90KB 富文本，长对话里十几个子块会让 QLabel 富文本
            # 每次布局耗数百毫秒（滚动/缩放/输入全卡）—— 这是长上下文卡顿的最大来源。
            stitle = _esc(seg.get("title", "子Agent"))
            steps = seg.get("steps") or []
            _link = (f'<a href="sub:toggle:{i}" style="color:{ACCENT};'
                     f'text-decoration:none;">')
            # 暂停态必须可见：点了「暂停」却毫无反馈时，用户会以为点击没生效
            _pause = (f'&nbsp;<span style="color:{ACCENT};font-size:{f_sm}px;">'
                      f'已暂停</span>') if seg.get("paused") else ""
            if seg.get("collapsed", True):
                hint = f"{len(steps)} 步 · " if steps else ""
                return (
                    f'<div style="margin:4px 0 2px;">'
                    f'<span style="color:{ACCENT};font-size:{f_op}px;">'
                    f'子Agent · {stitle}</span>{_pause}&nbsp;'
                    f'<span style="color:{TEXT_DIM};font-size:{f_sm}px;">'
                    f'{_link}（{hint}已折叠 · 点击展开）</a></span></div>')
            # 展开态：标题（收起入口）+ 最近 N 步 + 总结正文，各自截断保证体积有界
            raw = _collapse_blank(seg.get("raw", ""))
            raw_trunc = len(raw) > _SUB_RAW_TRUNCATE
            if raw_trunc:
                raw = raw[:_SUB_RAW_TRUNCATE] + "…"
            body = _esc(raw).replace("\n", "<br/>")
            shown = steps[-_SUB_OPEN_MAX_STEPS:]
            steps_html = ""
            for stp in shown:
                stp_txt = str(stp.get("text", ""))
                if len(stp_txt) > _SUB_STEP_TRUNCATE:
                    stp_txt = stp_txt[:_SUB_STEP_TRUNCATE] + "…"
                if stp.get("kind") == "tool":
                    steps_html += (
                        f'<div style="color:{ACCENT};font-size:{f_sm}px;'
                        f'font-family:Consolas;margin:2px 0 1px;">'
                        f'▸ {_esc(stp_txt)}</div>')
                else:   # output
                    out = _collapse_blank(stp_txt)
                    if len(out) > _SUB_STEP_TRUNCATE:
                        out = out[:_SUB_STEP_TRUNCATE] + "…"
                    out = _esc(out).replace("\n", "<br/>")
                    steps_html += (
                        f'<div style="color:{TEXT_DIM};font-size:{f_sm}px;'
                        f'font-family:Consolas;background:{PANEL};border-radius:4px;'
                        f'padding:3px 8px;margin:0 0 4px 10px;">{out}</div>')
            if len(steps) > len(shown):
                steps_html = (
                    f'<div style="color:{TEXT_DIM};font-size:{f_sm}px;margin:0 0 4px 10px;">'
                    f'（共 {len(steps)} 步，仅展示最近 {len(shown)} 步）</div>') + steps_html
            body_html = (f'<div style="color:{TEXT_DIM};font-size:{f_sm}px;'
                         f'font-family:Consolas;border-left:2px solid {ACCENT};'
                         f'padding:2px 10px;margin:2px 0 4px 6px;">{body}</div>'
                         + (f'<div style="color:{TEXT_DIM};font-size:{f_sm}px;'
                            f'margin:0 0 4px 6px;">'
                            f'子Agent 输出过长已截断显示（完整 {len(seg.get("raw") or "")} 字符）</div>'
                            if raw_trunc else "")
                         if body else "")
            return (
                f'<div style="margin:6px 0 2px;">'
                f'<div style="color:{ACCENT};font-size:{f_op}px;">'
                f'子Agent · {stitle}&nbsp;'
                f'<span style="color:{TEXT_DIM};font-size:{f_sm}px;">'
                f'{_link}收起 ▲</a></span>{_pause}'
                f'<span style="font-size:{f_sm}px;">&nbsp;'
                f'<a href="sub:pause:{i}" style="color:{TEXT_DIM};'
                f'text-decoration:none;">暂停</a> '
                f'<a href="sub:resume:{i}" style="color:{TEXT_DIM};'
                f'text-decoration:none;">恢复</a></span></div>'
                f'{steps_html}'
                f'{body_html}'
                f'</div>')
        return None

    def _segments_full(self) -> list:
        """完整对话流 = 历史段（含 split 边界）+ 当前回复段"""
        return self._history_segments + self._segments

    def _split_groups(self) -> list:
        """按 split 边界把完整段流切分为「每条 AI 回复一组」"""
        groups, cur = [], []
        for seg in self._segments_full():
            if seg["type"] == "split":
                if cur:
                    groups.append(cur)
                    cur = []
            else:
                cur.append(seg)
        if cur:
            groups.append(cur)
        return groups

    def _reconstruct_rows(self) -> list:
        """旧版会话（无 rows 字段）回退重建显示顺序。

        按段流分组得到 AI 回复组；若会话末尾不是 split（最后一条消息的回复未归档），
        说明最后一条消息必有回复 → 最后一条消息配最后一组，其余前向配对，
        避免 user_msgs 与 AI 组数量错位导致用户消息被排到 AI 回复下方；否则直接前向配对。
        """
        groups = self._split_groups()
        n, q = len(self._user_msgs), len(groups)
        full = self._segments_full()
        tail_has_reply = bool(full) and full[-1].get("type") != "split"
        rows = []
        if q and n and tail_has_reply:
            for i, u in enumerate(self._user_msgs[:-1]):
                rows.append({"type": "user", "text": u})
                if i < q - 1:
                    rows.append({"type": "ai", "segs": groups[i]})
            rows.append({"type": "user", "text": self._user_msgs[-1]})
            rows.append({"type": "ai", "segs": groups[-1]})
        else:
            for i, u in enumerate(self._user_msgs):
                rows.append({"type": "user", "text": u})
                if i < q:
                    rows.append({"type": "ai", "segs": groups[i]})
            for g in groups[n:]:
                rows.append({"type": "ai", "segs": g})
        return rows

    def _render_history_all(self):
        """全量重建消息流：按持久化交错行渲染（加载会话/全屏缩放时调用），
        用户消息与 AI 回复天然成对，杜绝数量错位导致的顺序错乱。
        批量插入期间暂停整个滚动区重绘，一次性重建后再统一刷新，
        避免每条气泡插入都触发整窗重排版导致长对话加载卡顿。"""
        viewport = self.msg_area.viewport()
        viewport.setUpdatesEnabled(False)
        try:
            while self.msg_lay.count() > 1:   # 清空消息流（保留末尾 stretch）
                item = self.msg_lay.takeAt(0)
                self._free_layout_item(item)
            self._bubble_widgets = []
            self._bubble_segs = {}
            self._ai_bubble = None
            self._msg_nav_clear()   # 重建前清空定位圆点（随后按用户消息重新填充）
            for r in (self._rows or self._reconstruct_rows()):
                if r.get("type") == "user":
                    self._add_bubble(r.get("text", ""), "user", animate=False)
                else:
                    self._add_ai_group_bubble(r.get("segs") or [], animate=False,
                                              cost=r.get("cost"), meta=r.get("meta") or "")
            # 未归档的当前回复段（渲染时恒为空，防御保留）
            if self._segments:
                self._add_ai_group_bubble(self._segments)
        finally:
            viewport.setUpdatesEnabled(True)
            self._relayout_messages()   # 同步完成布局：批次插入期间几何可能停留在陈旧值
            viewport.update()
        # 历史/会话恢复后（无运行中任务时）标记最后一条 AI 气泡可重试，
        # 覆盖「重启恢复会话 / 切换回旧对话」等非实时任务结束场景。
        if not self._task_active and self._user_msgs:
            self._arm_retry_button()
        # 延迟自愈：面板隐藏/宽度未就绪时首轮布局用的是瞬时几何，
        # 事件循环推进（0ms）与布局稳定（200ms）后以真实宽度复核一次高度，
        # 修复"所有聊天被挤压、需点击气泡才恢复"的陈旧几何问题。
        QTimer.singleShot(0, self._relayout_messages)
        QTimer.singleShot(200, self._relayout_messages)

    def _relayout_messages(self):
        """强制消息流完成一次布局激活（自愈）：批次重建/隐藏期间容器高度或
        各气泡换行高度可能停留在陈旧值（表现为整屏被挤压），激活布局并让
        滚动区按最新 sizeHint 重设容器几何即可恢复。
        注意：这里只激活/重算几何，不再调用 _sync_bubble_heights —— sync 自身
        在高度变化时会调度本方法，若在此再做全量 sync 会形成无限单Shot 乒乓
        （每次 sync 判定"变化"→ 又调度 relayout → 又 sync → ……），拖死 UI。"""
        try:
            if getattr(self, "msg_lay", None) is None:
                return
            self.msg_lay.activate()
            container = self.msg_area.widget()
            if container is not None:
                container.updateGeometry()
        except Exception:
            pass

    def _sync_bubble_heights(self, bubbles=None):
        """气泡最小高度＝真实换行高度。QLabel 的 sizeHint 对换行长文本只按
        单行计高，滚动区据此不随流式内容增长，生成中的长回复会被压扁/裁切
        （"AI 生成时气泡被挤压"的根因）。文本变化/窗口宽度变化后按
        heightForWidth 显式钉住最小高度，驱动容器与滚动区正确扩展。
        bubbles 指定时只同步这些气泡（流式高频路径只重算当前气泡）；默认全量。
        注意：必须先 setMinimumHeight(0) 再取 heightForWidth —— QLabel 的
        heightForWidth 会被既有最小高度钳制，不先置零会导致内容收缩（结果段
        折叠/思考收起）后高度永远回不到真实值，气泡底部留大片空白。"""
        changed = False
        for b in (bubbles if bubbles is not None else self._bubble_widgets):
            try:
                if not self._bubble_alive(b):
                    continue
                if isinstance(b, chat_bubbles.ChatTurn):
                    # 事件流回合的高度完全自管（relayout_heights 按固定宽度一次性算准
                    # 每个块与整条回合的高度），面板既不再钉最小值、也不去清它的固定高度，
                    # 否则会把已算准的几何破坏掉。
                    continue
                w_b = b.width()
                if w_b <= 0:
                    continue
                prev = b.minimumHeight()       # 先记录旧值，再解除钳制测真实高度
                b.setMinimumHeight(0)          # 解除钳制（QLabel.heightForWidth 受最小高度钳制）
                h = b.heightForWidth(w_b)
                if isinstance(h, (int, float)) and h > 0:
                    # 容差 2px：流式刷新高度 ±1px 抖动（HTML 排版取整）不得触发重排，
                    # 否则每 tick 都判定变化 → 反复全流量布局（卡顿）
                    if abs(int(h) - prev) >= 2:
                        b.setMinimumHeight(int(h))
                        # 布局对已拉高的标签不会主动收缩其几何（内容折叠/收起后仍停在
                        # 旧高度）→ 显式把标签高度改为当前内容高，驱散底部空白
                        try:
                            if b.height() > int(h):
                                b.resize(b.width(), int(h))
                        except Exception:
                            pass
                        changed = True
                    elif prev:
                        b.setMinimumHeight(prev)   # 未变化：恢复原钉值，保持几何稳定
            except Exception:
                pass
        # 高度确实变化后安排一次布局激活：仅改最小高度不触发重排（缩小场景
        # 尤其如此，否则内容收缩后气泡几何仍停留在旧高度，底部留空白）
        if changed:
            QTimer.singleShot(0, self._relayout_messages)

    def _add_ai_group_bubble(self, segs: list, animate: bool = True,
                             cost=None, meta: str = ""):
        """把一组 AI 段渲染为一条独立事件流回合，并设为当前回合（新回复流式续接）。

        cost/meta 为会话恢复时从磁盘带回的耗时与系统时间行（新回合走实时计时）。
        """
        self._deactivate_retry(self._ai_bubble)   # 新回合成为最后一条：回收旧回合的重试热区
        b = self._add_bubble("", "ai", animate=animate)
        b.t_start = time.time() if self._task_active else 0.0   # 计时基准（历史回合为 0：不重复计）
        b.turn_meta = meta or ""     # 系统时间行（新回合结束时填「开始 → 结束」）
        b.set_cost(cost)
        self._bubble_segs[id(b)] = segs
        self._ai_bubble = b          # 先登记为当前回合：_render_ai_frame 据此判定「进行中」
        self._render_ai_frame(b, segs)
        return b

    def _render_ai_frame(self, frame, segs: list):
        """把一组 AI 段渲染进事件流回合容器（demo 结构：过程区 + 正文 + 系统时间）。

        - 进行中的回合：过程区展开、耗时徽章实时刷新；
        - 已结束且已产出正文的回合：过程区整体收起，只留正文与「查看执行过程」开关
          （demo `.ai-turn.done .ai-proc`）；没有正文的回合不收起，避免整条回合空掉。
        """
        if not self._bubble_alive(frame):
            return
        try:
            blocks = self._seg_blocks(segs)
            live = (frame is self._ai_bubble) and self._task_active
            if live and isinstance(frame, chat_bubbles.ChatTurn) and not frame.timing_started:
                # 耗时徽章按**任务起点**起钟，不等第一个内容事件：否则「派发子 Agent 后
                # 长时间没有输出」那一段完全不计入时长（用户反馈的「不计入时长」）。
                frame.start_live(getattr(frame, "t_start", 0.0) or None)
            has_body = any(k == chat_bubbles.KIND_STREAM for k, _p, _s in blocks)
            frame.render(blocks, cost=frame.cost, live=live,
                         sys_meta=frame.turn_meta, done=(not live) and has_body)
        except RuntimeError:
            pass

    def _finalize_turn(self, frame=None):
        """任务收尾：冻结当前回合的耗时徽章并填写系统时间行，随后按已结束态重渲染
        （过程区整体收起）。耗时与时间行随会话落盘，重启/切回后仍可见。"""
        frame = self._ai_bubble if frame is None else frame
        if frame is None or not self._bubble_alive(frame):
            return
        try:
            if not getattr(frame, "t_start", 0.0):
                return
            frame.finish()                    # 冻结耗时徽章（cost 属性同步返回该值）
            frame.turn_meta = f"{_hhmmss(frame.t_start)} → {_hhmmss(time.time())}"
            frame.t_start = 0.0
            segs = self._bubble_segs.get(id(frame))
            if segs is not None:
                # 收尾前先补齐落字节奏：否则最后几个字会被留在节拍里，用户看到被截断的正文
                self._reveal_flush(segs)
                self._render_ai_frame(frame, segs)
                self._sync_after_toggle(frame)
        except Exception:
            # 不静默：收尾失败会让回合停在「进行中」外观（耗时不停、过程区不收起），
            # 必须留下日志而不是无声吞掉（此前此处静默曾掩盖真实缺陷）。
            import logging
            logging.getLogger(app_identity.APP_SLUG).exception("_finalize_turn 收尾失败")

    # ---------- AI 回复重试（重新生成） ----------
    def _set_retry_icon(self, btn, on: bool):
        """鼠标移入/移出重试热区时切换图标：平时为透明热区，附着时才浮现矢量图标"""
        try:
            btn.setIcon(_line_icon("retry", 18, TEXT_DIM) if on else QIcon())
        except Exception:
            pass

    def _deactivate_retry(self, bubble):
        """停用某气泡的重试按钮（新气泡出现 / 会话重建时调用，回收占位热区）"""
        if bubble is None:
            return
        try:
            bubble._retry_armed = False
            btn = getattr(bubble, "_retry_btn", None)
            if btn is not None:
                btn.hide()
        except Exception:
            pass

    def _arm_retry_button(self):
        """标记最后一条 AI 气泡可重试：占位热区常驻（气泡正下方预留槽位），
        鼠标移入该位置即浮现重试图标；点击重新生成最后一条回复。"""
        b = self._ai_bubble
        if b is None or not self._bubble_alive(b):
            return
        if not self._user_msgs:
            return
        try:
            b._retry_armed = True
            btn = getattr(b, "_retry_btn", None)
            if btn is not None:
                btn.show()      # 占位热区（透明，无图标）
                if btn.underMouse():
                    self._set_retry_icon(btn, True)
        except Exception:
            pass

    def _drop_last_turn_ui(self):
        """移除最后一条用户消息与其 AI 回复（UI 数据层，随后 _render_history_all 重建）"""
        self._segments.clear()
        if self._history_segments and self._history_segments[-1].get("type") == "split":
            self._history_segments.pop()
        if self._rows and self._rows[-1].get("type") == "ai":
            self._rows.pop()
        if self._user_msgs:
            self._user_msgs.pop()
        if self._rows and self._rows[-1].get("type") == "user":
            self._rows.pop()
        self._ai_bubble = None
        self._seg_cache.clear()

    def _regenerate_last(self):
        """重新生成最后一条回复：移除最后一条 AI 回复与用户消息，重发原请求"""
        self._deactivate_retry(self._ai_bubble)   # 先回收重试热区，避免引用待删除控件
        st = self._sess.get(self._session_id) or {}
        eng = st.get("engine") or self._engine
        if self._task_active or (self._eval_pending is not None
                                 and self._eval_pending[0] == self._session_id):
            self._notify_blocked("任务进行中，暂无法重试")
            return
        payload = st.get("last_payload") or {}
        if not payload:
            # 重启恢复的会话可能没有随盘落盘的重试信息（旧格式 ui.json 无
            # last_payload / 落盘时任务中断被置空）：以最后一条用户消息文本
            # 重建 payload 兜底重发，保证存量会话重启后重试按钮仍可用。
            # 原消息若带图片/附件/技能，兜底只重发文本（尽力而为）。
            _ums = (st.get("user_msgs") or []) or list(self._user_msgs or [])
            if not _ums:
                self._notify_blocked("没有可重试的消息")
                return
            payload = {"text": _ums[-1]}
            st["last_payload"] = payload   # 回写，本次重发后按正常路径落盘
        # 子 Agent 直接调用：移除最后一条后重新调用子 Agent（不经主引擎）
        if payload.get("subagent"):
            name = payload["subagent"]
            self._drop_last_turn_ui()
            self._render_history_all()
            self._update_welcome()
            self._scroll_bottom()
            self._launch_subagent(name, payload.get("task") or payload.get("text") or "",
                                  list(payload.get("images") or []))
            return
        # 普通对话：回退引擎上下文到最后一条用户消息之前，再重发
        idx = st.get("regenerate_index")
        msgs = getattr(eng, "_messages", None)
        if msgs is not None:
            if not (isinstance(idx, int) and 0 <= idx <= len(msgs)):
                # 重启后引擎上下文为恢复的上下文，注册的回到点索引可能失效：
                # 按最后一条用户消息重新定位（找不到则从引擎消息起点以后重试）
                idx = None
                for _i in range(len(msgs) - 1, -1, -1):
                    if (msgs[_i] or {}).get("role") == "user":
                        idx = _i
                        break
                if idx is None:
                    idx = 0
            del msgs[idx:]
        self._drop_last_turn_ui()
        self._render_history_all()
        self._update_welcome()
        self._scroll_bottom()
        self._end_badge_shown = False
        self._do_send(payload)

    # ---------- AI 气泡右键：朗读回复 ----------
    @staticmethod
    def _bubble_read_text(segs: list) -> str:
        """从 AI 气泡段中提取可朗读的正文（text 段 raw 拼接，剔除思考/操作/结果）"""
        parts = []
        for seg in segs or []:
            if seg.get("type") == "text":
                raw = str(seg.get("raw", "") or "").strip()
                if raw:
                    parts.append(raw)
        return "\n".join(parts).strip()

    def _on_ai_bubble_menu(self, bubble, pos):
        """AI 回合右键菜单：朗读这条回复（pos 为全局坐标）

        事件流回合内的内容标签各自 Catch 右键，统一换算为全局坐标后回调到这里，
        因此弹窗位置与「在哪个标签上右键」无关，始终贴着光标。"""
        segs = self._bubble_segs.get(id(bubble))
        text = self._bubble_read_text(segs)
        menu = QMenu(self)
        read_act = menu.addAction("朗读这条回复")
        if text:
            menu.addSeparator()
            copy_act = menu.addAction("复制全文")
        act = menu.exec(pos)
        if act is read_act:
            if not text:
                self._notify_blocked("该回复没有可朗读的正文")
                return
            self._read_aloud(text)
        elif text and act is copy_act:
            QApplication.clipboard().setText(text)

    def _read_aloud(self, text: str):
        """后台线程合成并朗读文本（复用 TTS 播放器，避免阻塞 UI）"""
        def work():
            if not agent_tools._tts_play_start():
                self._notify_blocked("朗读不可用：pygame 未初始化")
                return
            try:
                agent_tts.synthesize_stream(
                    text, voice_id="", on_chunk=agent_tools._tts_play_chunk)
                agent_tools._tts_play_flush()
                agent_tools._tts_play_finish()
            except Exception as e:
                agent_tools._tts_play_stop()
                self._notify_blocked(f"朗读失败：{e}")
        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def _seg_index(url: str, segs: list, want_type: str):
        """链接形如 `xxx:{段索引}` → 校验段类型后返回索引（越界/类型不符返回 None）"""
        try:
            idx = int(url.split(":", 2)[2])
        except (IndexError, ValueError):
            return None
        if not (0 <= idx < len(segs)) or segs[idx].get("type") != want_type:
            return None
        return idx

    def _live_segs_ok(self, bubble, segs) -> bool:
        """过期注册防护：历史回合绝不能渲染活动段流（防内容被最新输出覆盖）"""
        return not (segs is self._segments and bubble is not self._ai_bubble)

    def _on_ai_turn_link(self, frame, url: str):
        """事件流回合内的链接点击（回合容器按所属回合回调，不依赖 sender 判定）"""
        self._on_bubble_link(url, frame=frame)

    def _on_bubble_link(self, url: str, frame=None):
        """回合内链接点击：子 Agent 块折叠/暂停恢复、文件路径打开等。

        frame 缺省时回退信号发送者（兼容既有调用）；事件流回合内的内容标签由
        ChatTurn 以闭包携带所属回合，故显式传入更可靠。
        """
        bubble = frame if frame is not None else self.sender()
        segs = self._bubble_segs.get(id(bubble)) if bubble is not None else None
        if url.startswith("sub:toggle"):
            # 子 Agent 块折叠/展开（链接形如 sub:toggle:{段索引}）。
            # 折叠缺省为真：老数据无 collapsed 字段时默认折叠（历史长对话才不会被
            # 十几个近百 KB 的子块撑爆，导致任何交互都卡）。
            if not segs or not self._live_segs_ok(bubble, segs):
                return
            idx = self._seg_index(url, segs, "sub")
            if idx is None:
                return
            segs[idx]["collapsed"] = not segs[idx].get("collapsed", True)
            self._render_ai_frame(bubble, segs)
            self._sync_after_toggle(bubble)
            return
        if url.startswith("sub:pause:") or url.startswith("sub:resume:"):
            # 子 Agent 块暂停/恢复（工具+UI 双通道的 UI 侧）
            if not segs or not self._live_segs_ok(bubble, segs):
                return
            idx = self._seg_index(url, segs, "sub")
            if idx is None:
                return
            seg = segs[idx]
            title = str(seg.get("title") or "子Agent")
            resuming = url.startswith("sub:resume:")
            try:
                from zhuzhu_Copilot.core import agent_control, agent_tools
                # 优先用子块自带的**控制注册 id**（sub:<名> / wf:<工作流>，由 dispatch 层
                # 随事件送达）：这才是 AgentControl 的键。拿标题反查只在老会话（段里没有
                # agent_id）时才作兜底 —— 标题是自然语言任务描述，反查几乎必然失败。
                aid = str(seg.get("agent_id") or "").strip()
                if not aid or agent_control.agent_control(aid) is None:
                    aid = agent_tools._resolve_agent_id(title)
                if not aid:
                    self._notify_blocked(
                        f"无法{'恢复' if resuming else '暂停'}「{title}」："
                        "该子任务当前未在运行（或已结束）")
                    return
                ok, msg = (agent_control.resume_agent(aid) if resuming
                           else agent_control.pause_agent(aid))
                if not ok:
                    self._notify_blocked(msg, "子 Agent 控制未执行")
                    return
                # 状态回写并就地重渲染该回合：没有可见反馈时用户会以为点击没生效
                seg["paused"] = not resuming
                self._render_ai_frame(bubble, segs)
                self._sync_after_toggle(bubble)
            except Exception as e:
                self._notify_blocked(f"控制子 Agent 失败: {e}")
            return
        url = _html.unescape(url)
        try:
            if url.startswith(("http://", "https://")):
                webbrowser.open(url)
            elif url.startswith("file://"):
                s = url[len("file://"):]
                k = len(s) - len(s.lstrip("/"))
                s = s.lstrip("/")
                # UNC（开头 ≥2 个 /）→ \\server\share；盘符 /C:/x → C:\x
                path = ("\\\\" + s.replace("/", os.sep)) if k >= 2 else s.replace("/", os.sep)
                os.startfile(path)
            else:
                os.startfile(url)
        except Exception as e:
            print(f"[agent] 打开链接失败: {url} → {e}")

    def _sync_after_toggle(self, bubble):
        """折叠/展开切换后：重排布局，消除折叠后气泡残留大片空白/内容被压扁的问题
        （QLabel.heightForWidth 受既有最小高度钳制，不解除就永远停在新旧内容中更高的高度）。

        **事件流回合必须走另一条路**：它的高度完全自管，且外层包裹层（`_TurnWrap`）按它的
        内容高度自钉高度。这里若照旧 `setMinimumHeight(0)`，面板不会（也不该）再钉回来，
        回合就失去基准；包裹层随后一旦按偏小的值钉住就再也回不来 —— 展开过程区后正文
        互相盖住（用户反馈的「严重折叠遮挡」）。所以对回合只做「作废高度缓存 + 按当前
        宽度重算 + 同步包裹层」。
        """
        if bubble is None:
            return
        try:
            if isinstance(bubble, chat_bubbles.ChatTurn):
                bubble._on_block_resize()      # 作废缓存 → 按当前宽度重钉 → _wrap_sync
            else:
                bubble.setMinimumHeight(0)     # 先解除钳制，再按真实内容重钉
                self._sync_bubble_heights([bubble])
        except Exception:
            pass
        try:
            QTimer.singleShot(0, self._relayout_messages)
        except Exception:
            pass

    # ---------- 流式落字节奏（reveal pacing） ----------
    # 模型成批吐字（实测一次 30~200 字）：把最新文本直接塞进气泡的观感是「一跳一大段」，
    # 与浮现动效叠加后依旧生硬（用户反馈「文字输出速度依旧太快」）。这里给每个流式段维护
    # 「已显示长度 _shown」，按固定速度逐帧推进前缀 —— 落字因此是连续的，配合浮现层就是
    # 「一个字一个字丝滑浮现」。速度自适应：模型慢时按基础速度落字，吐得快时按积压量加速
    # 追平，滞后不超过 _REVEAL_MAX_LAG_S（所以不会长期落后于模型）。
    def _reveal_note(self, seg: dict):
        """流式段又有新内容到达：登记显示起点并启动落字节拍。"""
        full = _seg_full_len(seg)
        if full is None:
            return
        if "_shown" not in seg:
            # 从空开始按节奏落下（含**首批**）：模型常常一次就吐几百字，首批直接整段显示
            # 恰恰是最刺眼的一跳；从 0 推进则连开场都是逐字浮现（每字约 11ms）。
            seg["_shown"] = 0
            seg["_shown_f"] = 0.0
        # 注意用 __dict__ 直接取：轻代理面板（测试替身）没跑过 __init__，属性缺失时
        # getattr 会走 sip 包装层并抛 RuntimeError（项目内 _token_pop 等处同此写法）
        if self.__dict__.get("_reveal_no_timer"):
            return                       # 轻代理面板：已判定建不了定时器（见下）
        t = self.__dict__.get("_reveal_timer")
        if t is None:
            try:
                t = QTimer(self)
                t.setTimerType(Qt.TimerType.PreciseTimer)
                t.setInterval(_STREAM_TICK_MS)
                t.timeout.connect(self._reveal_tick)
            except RuntimeError:
                # 轻代理面板没有 QObject 基类，建不了定时器：落字状态（_shown）照常更新，
                # 由调用方直接驱动 _reveal_tick。
                self._reveal_no_timer = True
                return
            self._reveal_timer = t
            self._reveal_t = time.perf_counter()
        if not t.isActive():
            self._reveal_t = time.perf_counter()
            t.start()

    def _reveal_tick(self):
        """按节拍推进显示长度：最靠后的待落字段按速度推进，更早的段直接补齐。"""
        now = time.perf_counter()
        # dt 限幅：卡顿/睡眠后一次性狂刷会失去落字节奏（也避免时间跳变导致整段闪现）
        dt = min(0.12, max(0.001, now - self.__dict__.get("_reveal_t", now)))
        self._reveal_t = now
        moved = paced = False
        for seg in reversed(self._segments[-6:]):
            full = _seg_full_len(seg)
            if full is None:
                continue
            shown = _shown_len(seg)
            if shown >= full:
                continue
            if not paced:
                rate = min(_REVEAL_CAP_CPS,
                           max(_REVEAL_CPS, (full - shown) / _REVEAL_MAX_LAG_S))
                # 累加**小数**字符：节拍是 4ms 而速度是 90 字/秒（每拍 0.36 字），
                # 每拍至少推进 1 字的话实际速度会变成 250 字/秒（把节奏直接抵消掉）。
                # 起点取 max(小数累计, 已显示)：flush 把 _shown 一次性推到全文后，
                # 小数累计可能落后，直接用会让已显示的字数回退（画面上的字闪掉）。
                acc = max(float(seg.get("_shown_f", shown)), float(shown)) + rate * dt
                seg["_shown_f"] = acc
                seg["_shown"] = min(full, int(acc))
                paced = True
            else:
                seg["_shown"] = full   # 已不是「正在落字」的那一段：不必再慢慢推
            moved = True
        if moved:
            self._refresh_ai_html()
        if not paced:
            t = self.__dict__.get("_reveal_timer")
            if t is not None:
                t.stop()

    def _reveal_flush(self, segs=None):
        """立即显示全部已到达内容：任务收尾 / 切会话 / 历史重建前必须调用，
        否则最后几个字会被留在节奏里（用户看到的是被截断的正文）。"""
        t = self.__dict__.get("_reveal_timer")
        if t is not None and t.isActive():
            t.stop()
        for seg in (segs if segs is not None else self._segments):
            if isinstance(seg, dict) and "_shown" in seg:
                full = _seg_full_len(seg)
                if full is not None:
                    seg["_shown"] = full
                    seg["_shown_f"] = float(full)   # 小数累计同步跟进（否则后续推进会让字数回退）

    def _refresh_ai_html(self):
        """节流刷新 AI 气泡：流式 token 高频调用时合并为批量 setText 一次，
        避免每个 token 全量重建 HTML + 触发整条消息区重排版导致输出卡顿。
        节流：33ms≈30fps 固定小间隔，流式文字连续落字（观感丝滑）；期间的所有增量
        由 dirty 防抖合并为一次渲染，不会因高频调用而重复重建富文本。"""
        # 流式正文被打断（追加了 result/op/status 等段）：把之前残留的
        # streaming text 段关闭，让其在下一次刷新时走完整 markdown 渲染补全格式
        if self._segments and self._segments[-1].get("type") != "text":
            for _seg in reversed(self._segments):
                if _seg.get("streaming"):
                    _seg["streaming"] = False
                    break
        if self._ai_bubble is None or self._html_dirty:
            return
        self._html_dirty = True
        raw = (self._segments[-1].get("raw", "")
               if self._segments and self._segments[-1].get("type") == "text" else "")
        n = len(raw)
        # 固定小间隔：改为「段级增量渲染 + 高度测量缓存」后，单帧渲染成本降到毫秒级，
        # 不必再按正文长度放宽间隔（旧实现最长 600ms，观感是一跳一跳的）。按
        # _STREAM_TICK_MS 定拍，文字连续落字、观感丝滑；dirty 防抖仍会把期间的所有增量
        # 合并为一次渲染。超大正文保留一档兜底间隔，避免极端长文下单帧占用过高。
        delay = _STREAM_TICK_MS if n < _STREAM_BIG_CHARS else 60
        if delay < _STREAM_TICK_LONG_MS and self._turn_block_count() >= _STREAM_TICK_BLOCKS:
            # 长任务（回合内区块多）把内容节拍放宽到 60fps：每 tick 的成本随区块数增长，
            # 「块一多就卡」本质是高频×大 N。落字连贯改由浮现层保证（EMERGE_TICK_MS 合成
            # ≈125fps，与内容节拍解耦），因此放宽内容节拍不会让输出变生硬。
            delay = _STREAM_TICK_LONG_MS
        QTimer.singleShot(delay, self._apply_refresh_ai_html)

    def _turn_block_count(self) -> int:
        """当前回合的区块数（0 = 未知/尚无回合）：长任务据它放宽内容节拍。"""
        try:
            return len(getattr(self._ai_bubble, "_items", ()) or ())
        except RuntimeError:
            return 0

    def _apply_refresh_ai_html(self):
        self._html_dirty = False
        if self._ai_bubble is None:
            return
        try:
            self._render_ai_frame(self._ai_bubble, self._segments)
            self._sync_bubble_heights([self._ai_bubble])  # 流式高频路径只重算当前回合
        except RuntimeError:
            self._ai_bubble = None

    # ---------- 滑动动画 + 思考过程（思考内容在 AI 气泡开头，完成后折叠） ----------
    def _ensure_spinner(self):
        """创建/显示任务行：••• 来回滑动动画 + 状态文字（位于 AI 气泡底部外侧，随消息流滚动）"""
        if self._spinner_row is not None:
            # 会话切换/主题重建可能已把该行从布局移除或销毁，引用失效时重建
            # （悬空 QVBoxLayout 引用若不清理，后续 insertLayout 会抛
            #  RuntimeError 并中断整条消息流渲染——生成中切主题气泡被清空的根因）
            _found = False
            try:
                for i in range(self.msg_lay.count()):
                    if self.msg_lay.itemAt(i).layout() is self._spinner_row:
                        _found = True
                        break
            except RuntimeError:
                _found = False
            if _found:
                return
            self._spinner = None
            self._spinner_lbl = None
            self._spinner_row = None
        self._spinner = _TypingDots()
        self._spinner_lbl = QLabel("AI 思考中…")
        self._spinner_lbl.setStyleSheet(f"color: {TEXT_DIM}; font-size: 12px;")
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)
        top.addWidget(self._spinner)
        top.addWidget(self._spinner_lbl)
        top.addStretch(1)

        self._spinner_row = QVBoxLayout()
        self._spinner_row.setContentsMargins(0, 0, 0, 0)
        self._spinner_row.setSpacing(2)
        self._spinner_row.addLayout(top)
        self.msg_lay.insertLayout(self.msg_lay.count() - 1, self._spinner_row)
        self._scroll_bottom()

    def _hide_spinner(self):
        """任务结束/清空时移除转圈动画行"""
        if self._spinner_row is None:
            return
        for i in range(self.msg_lay.count()):
            if self.msg_lay.itemAt(i).layout() is self._spinner_row:
                self._free_layout_item(self.msg_lay.takeAt(i))
                break
        self._spinner = None
        self._spinner_lbl = None
        self._spinner_row = None

    # ---------- /compact 压缩打字指示器（独立行，与任务转圈互不干扰） ----------
    def _ensure_compact_row(self):
        """压缩上下文期间在消息流中显示打字指示器行"""
        row = self._compact_row
        if row is not None:
            # 会话切换/清空对话可能已把该行从布局移除，引用失效时重建
            if any(self.msg_lay.itemAt(i).layout() is row
                   for i in range(self.msg_lay.count())):
                return
            self._compact_row = None
        dots = _TypingDots()
        lbl = QLabel("正在压缩上下文…")
        lbl.setStyleSheet(f"color: {TEXT_DIM}; font-size: 12px;")
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)
        top.addWidget(dots)
        top.addWidget(lbl)
        top.addStretch(1)
        self._compact_row = QVBoxLayout()
        self._compact_row.setContentsMargins(0, 0, 0, 0)
        self._compact_row.setSpacing(2)
        self._compact_row.addLayout(top)
        self.msg_lay.insertLayout(self.msg_lay.count() - 1, self._compact_row)
        self._scroll_bottom()

    def _hide_compact_row(self):
        """压缩结束/清空时移除打字指示器行"""
        row = self._compact_row
        self._compact_row = None
        if row is None:
            return
        for i in range(self.msg_lay.count()):
            if self.msg_lay.itemAt(i).layout() is row:
                self._free_layout_item(self.msg_lay.takeAt(i))
                break

    def _start_think(self):
        """任务进行中：显示转圈；每轮「正在思考…」都复位本轮计时与完成态。
        多轮工具循环每轮都会产生一轮思考，不能只在首轮展示（否则后续轮次被丢弃）。"""
        self._think_done = False
        self._think_start = time.time()
        if self._spinner_lbl is not None:
            self._spinner_lbl.setText("AI 思考中…")
        self._ensure_spinner()

    def _finish_thinking(self):
        """思考完成（开始输出正文/工具调用）：转圈行显示'已思考 x 秒'。

        思考内容的折叠由事件流回合的思考气泡自行承担（demo .think-bubble：正文超
        5 行折叠、可点「继续查看」），多轮任务的各轮思考段按时间顺序独立保留，
        因此这里不再回写 seg["collapsed"]（该字段对思考段已无渲染意义）。
        """
        if self._think_done:
            return
        self._think_done = True
        if self._spinner_lbl is not None and self._think_start:
            el = int(time.time() - self._think_start)
            self._spinner_lbl.setText(f"已思考 {el} 秒")

    def _on_reasoning(self, s: str):
        """流式思考过程：追加到当前一轮的思考段（转义为富文本）。
        多轮工具循环每轮都会有思考：末段不是思考段就新建一段，与操作/正文按时间顺序
        交织渲染，不会覆盖或丢弃后续轮次的思考。"""
        self._last_activity = time.time()
        self._ensure_ai_bubble()
        if not self._segments or self._segments[-1].get("type") != "think":
            self._segments.append({"type": "think", "html": ""})
        self._segments[-1]["html"] += _esc(s)
        # 思考过程同样走落字节拍：推理常成批涌出，逐字浮现才有「正在想」的质感
        self._reveal_note(self._segments[-1])
        self._scroll_bottom()

    # ---------- MCP 初始化 ----------
    def _init_mcp(self):
        try:
            self._mcp.close_all()   # 重连前关闭旧连接
            servers = agent_skills.load_mcp_servers()
            if not servers:
                self.mcp_signal.emit("MCP: 未配置服务器")
                return
            try:
                tools = self._mcp.connect_all(servers)
                n = len(tools)
                errs = "；".join(self._mcp.errors)
                msg = f"MCP: 已连接 {n} 个工具" + (f"（失败: {errs}）" if errs else "")
            except Exception as e:
                msg = f"MCP: 初始化失败 {e}"
        except Exception as e:
            msg = f"MCP: 初始化异常 {e}"
        self.mcp_signal.emit(msg)

    def _ret_theme(self):
        """主题切换：就地重建本面板 UI，立即生效（会话与运行中任务原样保留）。"""
        self._retheme()

    def _open_settings(self, *_args, highlight_sid: str = None):
        """打开 AI 设置；保存后应用（刷新纯文本/记忆状态，空闲时重建引擎）。
        highlight_sid：打开后定位到「对话流」页该会话行并闪烁边框（工作目录提示条跳转用）"""
        dlg = _AgentSettingsDialog(parent=self)
        agent_ui_ux.glassify_dialog(dlg)   # 浅色磨砂（增强磨砂感而非暗色感）
        # 首次打开挤压修复：exec 前布局未激活，窗口会以最小尺寸显示；显示后补一次
        # adjustSize 让对话框按真实内容恢复常规大小（内容更大则随内容放大，
        # 不低于最小 991x687），再居中于屏幕可用区（避免偏下）
        def _normalize_dialog():
            try:
                dlg.adjustSize()
                if dlg.width() < 991 or dlg.height() < 687:
                    dlg.resize(max(dlg.width(), 991), max(dlg.height(), 687))
            except Exception:
                pass
            _center_dialog_on_screen(dlg)
        QTimer.singleShot(0, _normalize_dialog)
        if highlight_sid:
            # exec() 阻塞：对话框显示且构建完成后跳到对应行（延迟一帧+窗口就绪）
            def _focus():
                try:
                    dlg.focus_workdir_row(highlight_sid)
                except Exception:
                    pass
            QTimer.singleShot(80, _focus)
        if dlg.exec():
            try:
                self._apply_agent_settings()
            except Exception:
                pass
            self._sync_todos_win()   # 任务清单窗口开关即时生效（立即隐藏/恢复）
            # 工作目录切换后：无论上述设置应用是否被个别异常中断，都强制刷新并
            # 抬升 Git/工作树面板（否则个别配置异常会在此提前 return，面板长期停留旧目录）。
            # 用防抖异步刷新，避免保存路径再起 git 子进程+扫目录造成卡顿。
            try:
                self._request_git_refresh()
                self._sync_wt_win()
                self._sync_git_win()
            except Exception:
                pass
            if getattr(dlg, "_theme_changed", False):
                # 主题变更：就地重建面板 UI，点击保存即立即生效。不关闭面板、
                # 不停止引擎，会话内存态（对话历史/排队消息/运行中任务）原样保留，
                # 切换主题不影响进行中的任务。
                self._retheme()
                return
            if getattr(dlg, "_wf_changed", False):
                # Cordis：工作流有变更（创建/切换/禁用/文件增删）→ 重注册工具技能并重建全部会话引擎（热插拔）
                try:
                    agent_workflow.apply_tools()
                    agent_skills.invalidate_skills_cache()
                except Exception:
                    pass
                for sid in list(self._sess.keys()):
                    self._rebuild_engine(sid)
                self._update_wf_label()   # 全局工作流变更后刷新顶部标签
            if getattr(dlg, "_ui_ux_changed", False):
                # UI/UX 包变更：就地重建面板 UI，与主题切换相同机制
                self._retheme()

    def _apply_context_budget(self, eng, cfg: dict = None, model: str = None,
                              provider: dict = None) -> None:
        """把当前生效的上下文上限（1M 开关 / 上游声明 / 手填 / 已知表 / 推断）
        热更新到既有引擎上。

        引擎为保留对话上下文而长期复用，窗口却由设置决定：不在保存设置与每次路由后
        对齐，就会出现「设置里勾了 1M，统计面板仍显示旧上限、压缩阈值也照旧」的错误。
        解析仍走 agent_llm.resolve_context 这一条决策链，不另起一套判断。"""
        if eng is None:
            return
        fn = getattr(eng, "set_context_budget", None)
        if not callable(fn):
            return
        cfg = cfg if isinstance(cfg, dict) else self._llm_config()
        context = cfg.get("context") or {}
        long_1m = bool(context.get("long_1m"))
        if model:
            # 按本轮实际路由的模型重新解析（多服务商/多模型窗口可以不同）
            if provider is None:
                provider = agent_llm.provider_for_model(cfg, model) or {}
            ctx = agent_llm.resolve_context(provider.get("base_url"), model,
                                            provider=provider, long_1m=long_1m)
        else:
            ctx = context
        try:
            fn(window=ctx.get("window"), max_output=ctx.get("max_output"),
               long_1m=ctx.get("long_1m"), source=ctx.get("source"))
        except Exception:
            pass

    def _apply_agent_settings(self):
        """设置变更后：刷新模式/工作目录/多模型/力度/纯文本状态；
        引擎单例复用，仅更新 LLM 连接参数与运行时开关（保留对话上下文）。"""
        s = agent_skills.load_settings()
        self._model_cfg = agent_llm.load_model_config()
        self._sync_model_combo()   # 模型/服务商变更后即时重建输入框右侧下拉
        self._effort = self._model_cfg.get("effort", "medium")
        self._auto_effort = bool(self._model_cfg.get("auto_effort", True))
        self._mode = str(self._settings.value("agent_mode", "ask"))   # 执行模式在设置页调整后同步
        self._restore_workdir()   # 工作目录在设置页调整后同步
        self._apply_panel_mode()  # 面板偏好（贴附/融入主面板）保存后即时生效
        self._cleanup_legacy_panel_keys()
        self._refresh_text_only()
        self._memory_enabled = bool(s.get("memory_enabled", True))
        if self._sess:
            busy = any(st.get("engine") and st["engine"]._thread
                       and st["engine"]._thread.is_alive() for st in self._sess.values())
            if busy:
                return
            # 复用引擎：仅更新连接参数与开关，不重建 → 对话上下文（_messages）
            # 与 token 统计完整保留；每次发送前 _launch_task 还会按模型路由重设连接参数
            cfg = self._llm_config()
            for st in self._sess.values():
                eng = st.get("engine")
                if not eng:
                    continue
                eng.llm.base_url = (cfg.get("base_url") or agent_llm.DEFAULT_BASE_URL).rstrip("/")
                eng.llm.api_key = cfg.get("api_key") or agent_llm.DEFAULT_API_KEY
                eng.llm.protocol = cfg.get("protocol", "chat")
                eng.llm.model = cfg.get("model") or agent_llm.DEFAULT_MODEL
                eng.text_only = self._text_only
                eng.memory_enabled = self._memory_enabled
                eng.direct = self._mode == "yolo"
                # 上下文上限随设置即时对齐（勾选/取消「开启 1M 上下文」当场生效，
                # 否则统计面板与压缩阈值会一直停在旧窗口）
                self._apply_context_budget(eng, cfg)
            return
        self._ensure_engine()

    # ---------- 工作力度 / 模型路由 ----------
    def _is_text_only(self, model: str) -> bool:
        """单个模型是否为纯文本：用户显式配置的多模态白名单优先，其次按模型名关键字判定。
        与发送阶段（_launch_task 中的 is_vision_model 检查）保持同一判据，避免闸门前后矛盾。"""
        if agent_llm.is_vision_model(self._model_cfg, model):
            return False
        return agent_llm.is_text_only_model(model)

    def _refresh_text_only(self):
        """纯文本模型识别：手动指定模型时以该模型为准；自动模式全部已配置模型
        均为纯文本时才全局禁用视觉（混配模型时按本次实际使用的模型逐次判断）"""
        m = self._model_override
        if m:
            self._text_only = self._is_text_only(m)
            return
        cfg = self._model_cfg
        models = cfg.get("models") or [cfg.get("model") or agent_llm.DEFAULT_MODEL]
        self._text_only = all(self._is_text_only(x) for x in models)


    def _resolve_effort(self, text: str) -> str:
        """本次任务使用的工作力度：自动开关开启时按任务难度估算，否则用手动力度"""
        if self._auto_effort:
            return agent_llm.estimate_effort(text)
        return self._effort

    # ---------- 子 Agent 准入（任务复杂度分档） ----------
    def _subagent_policy(self) -> str:
        """面板策略：settings.json 的 subagent_policy（auto/always/never），缺省 auto。
        非法值一律回落 auto，避免写错一个词就把整个派发能力锁死。"""
        try:
            p = str(agent_skills.load_settings().get("subagent_policy") or "auto").strip().lower()
        except Exception:
            p = "auto"
        return p if p in agent_llm.SUBAGENT_POLICIES else "auto"

    def _resolve_allow_subagents(self, text: str, effort: str, wf: str = "") -> bool:
        """本任务是否允许派发子 Agent / 其他工作流主 Agent。

        判定本身收敛在 agent_llm.subagent_allowed（唯一事实来源），此处只负责收集
        "用户显式要求"这一硬信号，命中任一即放行（否则用户的编队指令无法执行）：
        ①自然语言显式要求（并行/并发/编队/派发/@点名，见 agent_llm.wants_subagents）；
        ②当前工作流是工作团领导者或成员（团队协作本就靠派发运转）；
        ③消息中点名了本工作流已注册的子 Agent。
        其余情况交给复杂度分档：只有「非常复杂」任务才放行。"""
        explicit = agent_llm.wants_subagents(text)
        w = wf or self._effective_workflow()
        try:
            from zhuzhu_Copilot.core import agent_subagent, agent_team
            if not explicit:
                explicit = bool(agent_team.is_leader_workflow(w)
                                or agent_team.is_member_workflow(w))
            if not explicit:
                subs = agent_subagent.registered_subagents(w) or []
                explicit = any(str(s.get("name") or "") and str(s.get("name")) in text
                               for s in subs)
        except Exception:
            pass
        return agent_llm.subagent_allowed(effort, self._subagent_policy(), explicit)

    def _think_mode_override(self) -> str:
        """本次发送的思考模式：强制思考（force_think）优先级最高 → on；
        否则用模型接入页显式选择的 think_mode（auto 跟随力度）"""
        m = self._model_cfg
        if m.get("force_think"):
            return "on"
        mode = str(m.get("think_mode") or "auto")
        return mode if mode in ("on", "off") else "auto"


    def _sync_model_combo(self):
        """重建输入框右侧模型下拉：首项「自动选择」+ 所有服务商的全部模型（跨服务商可切换）。
        显示名去掉供应商前缀（deepseek-ai/DeepSeek-V4-Pro → DeepSeek-V4-Pro），
        完整模型 ID 与服务商名放入 tooltip，选中逻辑仍按原始 model 判定。"""
        cfg = self._model_cfg
        providers = cfg.get("providers") or []
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self.model_combo.addItem("自动选择", None)
        for p in providers:
            pname = p.get("name", "服务商")
            for x in p.get("models") or []:
                label = x.rsplit("/", 1)[-1] if x else x   # 去供应商/组织前缀
                self.model_combo.addItem(label, (pname, x))
                self.model_combo.setItemData(
                    self.model_combo.count() - 1, f"{pname} · {x}",
                    Qt.ItemDataRole.ToolTipRole)
        # 恢复当前选中（手动指定模型）
        if self._model_override:
            # 注意：QComboBox.findData 对 Python tuple 的 QVariant 比较不可靠
            # （tuple 会转换后比较失败返回 -1），这里改用 Python 层逐项比较
            target = (self._model_override_provider, self._model_override)
            idx = next((i for i in range(self.model_combo.count())
                        if self.model_combo.itemData(i) == target), -1)
            if idx < 0:      # 手动指定模型已不在列表：清除覆盖回到自动路由
                self._model_override = None
                self._model_override_provider = ""
                self._settings.setValue("agent_last_model", "")
                self._settings.setValue("agent_last_provider", "")
                idx = 0
            self.model_combo.setCurrentIndex(idx)
        else:
            self.model_combo.setCurrentIndex(0)
        self.model_combo.blockSignals(False)
        # 弹出列表「当前选中项」右侧对勾（幂等：主题切换/重建后自动重挂委托）
        try:
            attach_combo_checkmark(self.model_combo, check_color=ACCENT)
        except Exception:
            pass

    def _provider_for_model(self, model: str) -> str:
        """返回包含指定模型的服务商名（找不到返回空串）"""
        for p in (self._model_cfg.get("providers") or []):
            if model in (p.get("models") or []):
                return p.get("name", "")
        return ""

    def _on_model_combo(self, idx):
        """手动切换模型：选中具体模型则本次发送使用其所属服务商；选「自动」回到力度路由"""
        if idx < 0:
            return
        val = self.model_combo.itemData(idx)
        if val:
            self._model_override = val[1]
            self._model_override_provider = val[0]
        else:
            self._model_override = None
            self._model_override_provider = ""
        self._settings.setValue("agent_last_model", self._model_override or "")   # 记住选择，重启恢复
        self._settings.setValue("agent_last_provider",          # 同时记住服务商，防同名模型串服务商
                                self._model_override_provider or "")
        self._refresh_text_only()   # 切换模型立即更新纯文本判断（粘贴图片/附件过滤实时生效）
        # 切换模型不清空上下文：当前对话历史继续沿用，仅后续轮次使用新模型

    def _reconnect_mcp(self):
        threading.Thread(target=self._init_mcp, daemon=True).start()

    # ---------- 命令补全（/ 展示全部命令 + 内联预测） ----------
    def _invalidate_cmd_cache(self):
        """技能/插件集合、工作流、自定义 Agent 变化后清空候选缓存，下次键入自动重建"""
        self._wf_cache = None
        self._agent_cache = None
        self._cmd_cache = None
        self._skill_map = None
        self._plugin_map = None

    def _wf_list(self) -> list:
        """工作流列表（缓存，避免 "@" 每键重读各工作流 meta 造成卡顿）"""
        if self._wf_cache is None:
            self._wf_cache = agent_workflow.list_workflows()
        return self._wf_cache

    def _agent_list(self) -> list:
        """自定义 Agent 列表（缓存，避免 "@" 每键重读 agents 目录造成卡顿）"""
        if self._agent_cache is None:
            self._agent_cache = agent_agents.list_agents()
        return self._agent_cache

    def _cmd_skills(self) -> dict:
        """当前工作流技能 {name.lower(): skill}（缓存，技能集合/工作流变化后失效）"""
        if self._skill_map is None:
            self._skill_map = {s.get("name", "").strip().lower(): s
                               for s in agent_skills.load_skills(
                                   workflow=self._effective_workflow())}
        return self._skill_map

    def request_plugin_call(self, name: str):
        """外部入口（设置页插件列表「调用」）：回到对话页并把 `/插件名 ` 预填进输入框。

        只预填不自动发送：插件调用往往会带一句具体任务（如「/天气插件 明天上海」），
        由用户补完再回车；调用时引擎会把插件说明与调用规范直接注入模型上下文。
        """
        name = str(name or "").strip()
        if not name:
            return
        try:
            self._invalidate_cmd_cache()   # 插件集合可能刚变化，命令候选重建一次
            self.input.setPlainText(f"/{name} ")
            cur = self.input.textCursor()
            cur.movePosition(QTextCursor.MoveOperation.End)
            self.input.setTextCursor(cur)
            self.input.setFocus()
            self.raise_()
            self.activateWindow()
        except Exception:
            pass

    def _cmd_plugins(self) -> dict:
        """可手动调用的插件 {name.lower(): plugin}（缓存，插件集合变化后失效）。

        插件对模型是黑盒，手动调用走 `/插件名 [提示]`：引擎会把插件说明、SKILL.md 规范与
        调用规范直接注入上下文，用户无需知道它内部有哪几个工具。
        """
        if self._plugin_map is None:
            self._plugin_map = {str(p.get("name", "")).strip().lower(): p
                                for p in agent_plugins.list_plugin_calls()
                                if str(p.get("name") or "").strip()}
        return self._plugin_map

    def _all_commands(self) -> list:
        """所有可斜杠调用项：系统命令（/clear、/compact）+ 当前工作流启用的技能 + 插件"""
        if self._cmd_cache is None:
            cmds = ["/compact", "/clear"]
            cmds += [f"/{name}" for name in self._cmd_skills() if name]
            cmds += [f"/{name}" for name in self._cmd_plugins() if name]
            self._cmd_cache = cmds
        return self._cmd_cache

    def _cmd_desc(self, cmd: str) -> str:
        """命令描述（用于命令条 tooltip）"""
        if cmd.startswith("@"):
            name = cmd[1:]
            if name == "_default":
                return "切回内置默认 Agent（清空自定义人格）"
            ag = next((a for a in self._agent_list() if a["name"] == name), None)
            if ag:
                prompt = (ag.get("system_prompt") or "").replace("\n", " ")
                desc = prompt[:48] + ("…" if len(prompt) > 48 else "")
                bound = (ag.get("bound_workflow") or "").strip()
                return "自定义 Agent: " + desc + \
                    (f"（工具/技能来自工作流 {bound}）" if bound else "（独立人格，沿用当前工具/技能）")
            sub = next((s for s in
                        agent_subagent.registered_subagents(self._effective_workflow())
                        if s["name"] == name), None)
            if sub:
                goal = (sub.get("goal") or "").replace("\n", " ")
                return "子 Agent（@直接调用，不经主 Agent）: " + \
                    goal[:48] + ("…" if len(goal) > 48 else "")
            wf = next((w for w in self._wf_list() if w["name"] == name), None)
            if wf:
                return (wf.get("description") or "") + ("（默认/内置工作流）" if wf["is_default"] else "")
            return ""
        name = cmd.lstrip("/").lower()
        s = self._cmd_skills().get(name)
        if s:
            return s.get("description", "")
        plugin = self._cmd_plugins().get(name)
        if plugin:
            return "插件：" + (str(plugin.get("description") or "").strip() or "可手动调用该插件")
        for t in agent_tools.TOOLS:
            if t["function"]["name"].lower() == name:
                return t["function"].get("description", "")
        return ""

    def _match_skill(self, text: str):
        """解析 /技能名 [提示]：按技能名匹配（仅当前工作流启用的技能）；
        返回 (技能dict, 提示文本)；未匹配返回 (None, "")。已禁用技能不触发，避免绕过工作流隔离。"""
        if not text.startswith("/"):
            return None, ""
        parts = text[1:].split(None, 1)
        if not parts:
            return None, ""
        q = parts[0].strip().lower()
        s = self._cmd_skills().get(q)
        if s:
            return s, (parts[1].strip() if len(parts) > 1 else "")
        return None, ""

    def _match_plugin(self, text: str):
        """解析 /插件名 [提示]：按插件名匹配（仅已启用的插件）；返回 (插件dict, 提示文本)"""
        if not text.startswith("/"):
            return None, ""
        parts = text[1:].split(None, 1)
        if not parts:
            return None, ""
        p = self._cmd_plugins().get(parts[0].strip().lower())
        if p:
            return p, (parts[1].strip() if len(parts) > 1 else "")
        return None, ""

    def _match_tool(self, text: str):
        """/工具名 [参数] → (name, args_text)；支持中文别名（截屏/截图→screenshot）；未匹配返回 None"""
        body = text.lstrip("/").strip()
        parts = body.split(None, 1)
        if not parts:
            return None
        name = parts[0].strip().lower()
        aliases = {"截屏": "screenshot", "截图": "screenshot",
                   "看屏幕": "screenshot", "查看屏幕": "screenshot",
                   "查看桌面": "screenshot", "刷新": "screenshot"}
        name = aliases.get(name, name)
        names = {t["function"]["name"] for t in agent_tools.TOOLS}
        if name not in names:
            return None
        return name, (parts[1].strip() if len(parts) > 1 else "")

    @staticmethod
    def _tool_params_hint(name: str) -> str:
        """工具参数说明（供 AI 解析斜杠参数并转为 JSON）"""
        for t in agent_tools.TOOLS:
            if t["function"]["name"] == name:
                p = t["function"].get("parameters") or {}
                props = p.get("properties") or {}
                req = set(p.get("required") or [])
                if not props:
                    return "(无参数)"
                return ", ".join(
                    f"{k}({v.get('type', 'any')}{'必填' if k in req else '可选'})"
                    for k, v in props.items())
        return "(无参数)"

    def _update_cmd_suggestions(self, *_):
        # QPlainTextEdit 的 textChanged 无参数，需自行读取当前文本
        text = self.input.toPlainText()
        # 输入 "@" 展示自定义 Agent 优先（@agent 会话级切换），其次自定义子 Agent
        #（@子Agent 直接调用，不经主 Agent 转发），再其次工作流（@工作流 向后兼容）
        if text.startswith("@"):
            agents = [a["name"] for a in self._agent_list()]
            subs = [s["name"] for s in
                    agent_subagent.registered_subagents(self._effective_workflow())]
            matches = [f"@{n}" for n in agents]
            matches += [f"@{n}" for n in subs if f"@{n}" not in matches]
            wfs = [w for w in self._wf_list() if w.get("enabled", True)]
            matches += [f"@{w['name']}" for w in wfs if f"@{w['name']}" not in matches]
            if not any(c == "@_default" for c in matches):
                matches.append("@_default")   # 内置默认，允许 @_default 切回默认 Agent
            if len(text) > 1 and not text.startswith("@ "):
                matches = [c for c in matches if c.startswith(text)]
            if matches:
                self.cmd_list.clear()
                for c in matches:
                    item = QListWidgetItem(c)
                    name = c[1:]
                    ag = next((a for a in self._agent_list() if a["name"] == name), None)
                    if ag:
                        prompt = (ag.get("system_prompt") or "").replace("\n", " ")
                        desc = prompt[:48] + ("…" if len(prompt) > 48 else "")
                        bound = (ag.get("bound_workflow") or "").strip()
                        item.setToolTip("自定义 Agent: " + desc +
                                        (f"（工具/技能来自工作流 {bound}）" if bound
                                         else "（独立人格，沿用当前工具/技能）"))
                    elif name == "_default":
                        item.setToolTip("切回内置默认 Agent（清空自定义人格）")
                    else:
                        sub = next((s for s in
                                    agent_subagent.registered_subagents(
                                        self._effective_workflow())
                                    if s["name"] == name), None)
                        if sub:
                            goal = (sub.get("goal") or "").replace("\n", " ")
                            item.setToolTip("子 Agent（@直接调用，不经主 Agent）: " +
                                            goal[:48] + ("…" if len(goal) > 48 else ""))
                            self.cmd_list.addItem(item)
                            continue
                        wf = next((w for w in wfs if w["name"] == name), None)
                        if wf:
                            item.setToolTip((wf.get("description") or "") or
                                            ("默认（内置）工作流" if wf["is_default"] else ""))
                    self.cmd_list.addItem(item)
                self._resize_cmd_list()
                self.cmd_list.show()
                return
        # 输入 "/" 时展示全部可调用项（系统命令 + 全部技能 + 全部内置工具）；否则按前缀过滤
        if text.startswith("/"):
            matches = [c for c in self._all_commands() if c.startswith(text)]
            if matches:
                self.cmd_list.clear()
                for c in matches:
                    item = QListWidgetItem(c)
                    desc = self._cmd_desc(c)
                    if desc:
                        item.setToolTip(desc)
                    self.cmd_list.addItem(item)
                self._resize_cmd_list()
                self.cmd_list.show()
                return
        self.cmd_list.hide()

    def _cmd_list_qss(self, pad: str = "4px") -> str:
        """命令候选框样式；单行时容器上内边距清零使文字上移"""
        return (
            f"QListWidget {{ background: {PANEL}; color: {ACCENT};"
            f"border: 1px solid {BORDER}; border-radius: 8px;"
            f"font-size: 13px; padding: {pad}; }}"
            f"QListWidget::item {{ padding: 6px 12px 6px 12px; border-radius: 6px; }}"
            f"QListWidget::item:hover {{ background: {HOVER}; }}"
            f"QListWidget::item:selected {{ background: {ACCENT}; color: #FFFFFF; }}")

    def _resize_cmd_list(self):
        """命令列表高度随显示条数自适应：最多 5 行，最少 1 行；单行时去掉容器上内边距，文字上移"""
        count = self.cmd_list.count()
        row_h = self.cmd_list.sizeHintForRow(0)
        if row_h <= 0:
            row_h = 30   # 兜底：13px 字体 + item 上下内边距 12px
        self.cmd_list.setFixedHeight(min(count, 5) * row_h)
        # 单行时容器上内边距 4px→0px，文字整体上移 4px
        self.cmd_list.setStyleSheet(
            self._cmd_list_qss("0px 4px 4px 4px" if count == 1 else "4px"))

    def _on_cmd_selected(self, item):
        """点击候选框选中命令：填入输入框，光标停在命令名末尾（便于继续输入参数）"""
        self._fill_command(item.text())

    def _complete_cmd(self) -> bool:
        """Tab 补全命令：按当前输入前缀补全为首个候选，光标停在命令名末尾"""
        item = self.cmd_list.item(0)
        if item is None:
            return False
        self._fill_command(item.text())
        return True

    def _fill_command(self, cmd: str):
        """把命令写入输入框并把光标移到命令末尾（textChanged 会重建候选列表，随后隐藏）"""
        self.input.setPlainText(cmd)
        cur = self.input.textCursor()
        cur.setPosition(len(cmd))
        self.input.setTextCursor(cur)
        self.input.setFocus()
        self.cmd_list.hide()

    # ---------- 发送 / 停止 ----------
    def _llm_config(self) -> dict:
        # 多模型/接口/API Key 从 settings.json 读取（含同服务商多模型与力度路由）
        return self._model_cfg

    def _ensure_engine(self):
        """复用当前会话的引擎（多对话并发：每会话独立引擎，保留各自上下文）"""
        return self._engine_for(self._session_id)

    # ---------- @工作流 / @子Agent 路由（会话级切换 / 直接调用） ----------
    def _resolve_at_route(self, text: str):
        """解析消息开头的 @（如「@coder 帮我写个脚本」）：依次匹配
        自定义 Agent（@agent，会话级切换人格）→ 自定义子 Agent（@子Agent，直接调用，
        不经主 Agent 转发，独立上下文执行）→ 工作流（@工作流，向后兼容既有用法）。
        未知名称 → 列出可用 Agent/子Agent/工作流并原样返回。 @_default 显式切回内置默认。"""
        m = re.match(r"^@([A-Za-z0-9_-]+)(?:\s+|$)", text)
        if not m:
            return text, None
        name = m.group(1)
        rest = text[m.end():].strip()
        if name in ("_default", "default"):
            # @_default 显式切回默认工作流（同时清空自定义 Agent 人格），
            # 而非仅清 persona：否则从 @工作流 切到某工作流后无法再切回默认。
            return rest, ("workflow", agent_workflow.DEFAULT_WORKFLOW)
        if agent_agents.get_agent(name):
            return rest, ("agent", name)
        # 自定义子 Agent（subagents.json）：@子Agent 名 直接调用，无需主 Agent 转发
        if agent_subagent.subagent_tool(name, self._effective_workflow()) is not None:
            return rest, ("subagent", name)
        wfs = agent_workflow.list_workflows()
        wnames = [w["name"] for w in wfs if w.get("enabled", True)]
        if name in wnames:
            return rest, ("workflow", name)
        if any(w["name"] == name and not w.get("enabled", True) for w in wfs):
            self._notify_blocked(f"工作流「{name}」已被禁用，请先在设置-工作流中启用")
        else:
            agents = sorted(a["name"] for a in agent_agents.list_agents())
            subs = sorted(s["name"] for s in
                          agent_subagent.registered_subagents(self._effective_workflow()))
            self._notify_blocked(f"未知「{name}」，可用 Agent: {', '.join(agents) or '（暂无）'}；"
                                 f"子 Agent: {', '.join(subs) or '（暂无）'}；"
                                 f"工作流: {', '.join(sorted(wnames)) or '（暂无）'}（@_default 切回内置默认）")
        return text, None

    def _agent_persona(self, name: str):
        """返回自定义 Agent 的人格（用户 system_prompt）；保留名/缺失返回 None（用工作流人格）"""
        if not name or name == "_default":
            return None
        ag = agent_agents.get_agent(name)
        return (ag or {}).get("system_prompt") or None

    def _switch_session_agent(self, name: str) -> bool:
        """会话级切换自定义 Agent：人格覆盖（system_prompt）+ 可选绑定工作流（tools/skill/llm）。
        _default 清空人格回退内置默认。任务运行中拒绝（避免在途上下文丢失/任务重叠）。"""
        st = self._sess.setdefault(self._session_id, self._new_sess_state(self._session_id))
        eng = st.get("engine")
        if eng is not None and eng._thread and eng._thread.is_alive():
            self._notify_blocked("任务运行中，暂不可切换 Agent")
            return False
        if name == "_default":
            st["agent"] = None
            if eng is not None:
                eng.persona = None
            self._persist_current()
            return True
        ag = agent_agents.get_agent(name)
        if not ag:
            self._notify_blocked(f"未知 Agent「{name}」，可用: "
                                 f"{', '.join(sorted(a['name'] for a in agent_agents.list_agents())) or '（暂无）'}")
            return False
        # 绑定工作流：与当前不同则先切工作流（获得其 tools/skill；无绑定则沿用当前工作流）
        bound = (ag.get("bound_workflow") or "").strip()
        cur_wf = st.get("workflow") or agent_workflow.active_workflow()
        if bound and bound != cur_wf \
                and (bound == agent_workflow.DEFAULT_WORKFLOW or agent_workflow.is_workflow(bound)):
            if not self._switch_session_workflow(bound):
                return False
            st = self._sess.get(self._session_id)
        st["agent"] = name
        eng = st.get("engine")
        if eng is None:
            eng = self._engine_for(self._session_id)
        eng.persona = ag.get("system_prompt") or None
        self._persist_current()
        self._invalidate_cmd_cache()   # agent 变化：@ 候选/技能候选随之下次重建
        return True

    def _switch_session_workflow(self, name: str) -> bool:
        """会话级切换工作流：更新会话状态并（若引擎已存在且工作流不同）热插拔重建。
        提示一律展示用户实际切换到的目标工作流名（含 @_default → _default）。
        返回是否切换成功：任务运行中拒绝切换（避免丢失在途上下文与任务重叠）。"""
        st = self._sess.setdefault(self._session_id, self._new_sess_state(self._session_id))
        eng = st.get("engine")
        # 引擎线程仍在运行（本会话有任务在跑）时禁止热插拔：重建会摘除运行中引擎并
        # 让 _send 视为空闲直接开新任务，造成在途上下文丢失 + 两任务并发重叠。
        if eng is not None and eng._thread and eng._thread.is_alive():
            self._notify_blocked("任务运行中，暂不可切换工作流")
            return False
        st["workflow"] = name
        # @工作流 是纯工作流切换：清空会话自定义 Agent 人格，避免上一轮 @agent 的
        # persona 残留在新工作流（人格与工具/技能来源错位，看似切换未生效）。
        st["agent"] = None
        # 工作团：切换工作流时继承原工作流的活跃共享上下文空间——若原工作流开启了
        # 共享空间，新工作流主 Agent 也能基于既有协作上下文继续回答（空间跨切换延续）。
        try:
            from zhuzhu_Copilot.core import agent_context
            _sid = agent_context.active_space()
            if _sid:
                st["space"] = _sid
        except Exception:
            pass
        if eng is not None:
            eng.persona = None
        if eng is not None and getattr(eng, "workflow", None) != name:
            self._rebuild_engine(self._session_id, display_wf=name)   # 重建内部显示切换状态
            # 重建后恢复会话共享空间为活跃（若仍存在），使新引擎主循环注入其快照
            if st.get("space"):
                try:
                    from zhuzhu_Copilot.core import agent_context
                    if agent_context.has_space(st["space"]):
                        agent_context.set_active(st["space"])
                except Exception:
                    pass
        else:
            if eng is None:
                self._engine_for(self._session_id)   # 首次：按新工作流创建引擎
        self._persist_current()
        self._update_wf_label()   # @工作流切换后刷新顶部标签
        self._invalidate_cmd_cache()   # 工作流已切换：命令/技能候选缓存随之下次重建
        return True

    def _send(self):
        """发送消息：任务运行中（含评估阶段）则进入排队，本轮完成后自动发送；
        空闲则直接发送。排队消息可在提示条编辑或删除。"""
        text = self.input.toPlainText().strip()
        images = list(self._pending_images)
        files = list(self._pending_files)
        if not text and not images:
            return
        if text.lower().startswith("/compact"):
            self._do_compact()
            return
        if text.lower().startswith("/clear"):
            self._clear_chat()
            return
        # @agent/@工作流：会话级切换（@coder 帮我写脚本 → 切到该 agent/工作流）；
        # @子Agent：直接调用自定义子 Agent（不经主 Agent 转发，独立上下文执行）
        text, at_route = self._resolve_at_route(text)
        if at_route:
            kind, rname = at_route
            if kind == "subagent":
                self._launch_subagent(rname, text, images)
                return
            # @工作流：会话级持久切换（无论是否带正文），切换后保持在该工作流。
            if kind == "workflow":
                switched = self._switch_session_workflow(rname)
            else:
                switched = self._switch_session_agent(rname)
            if not switched:
                return   # 任务运行中切换被拒绝：不发送，等待本轮完成后重试
            if not text and not images:
                return   # 仅 @切换 无正文：只切换不发送
        payload = self._build_payload(text, images, files)
        if payload is None:
            return
        # 本会话有任务运行中/评估中 → 消息排队
        busy = self._task_active
        ep = self._eval_pending
        if ep is not None and ep[0] == self._session_id:
            busy = True
        eng = self._engine_for(self._session_id)
        if eng._thread and eng._thread.is_alive():
            busy = True
        if busy:
            qs = self._sess[self._session_id]["queued"]
            ei = self._queue_edit_idx
            if ei is not None and 0 <= ei < len(qs):
                qs[ei] = payload          # 编辑后按原队列位置重新排队
            else:
                qs.append(payload)        # 新增排队消息（支持多条按序排队）
            self._cancel_queue_edit()
            self.input.clear()
            self._clear_attachments()
            self._update_queue_bar()
            return
        self._cancel_queue_edit()
        self._do_send(payload)

    def _launch_task(self, ai_text: str, send_images: list, skill_names: list,
                     effort: str, sid: str = None, plugin_names: list = None):
        """按力度/评估结果路由模型并启动任务（评估完成或手动模式时调用）。
        sid：目标会话（默认当前会话）；后台会话（排队消息）也可启动任务；
        plugin_names：用户手动调用的插件（/插件名），其说明与规范一并注入上下文"""
        sid = sid or self._session_id
        engine = self._engine_for(sid)
        cfg = self._llm_config()
        providers = cfg.get("providers") or []
        # 手动选中的模型 → 使用其所属服务商的连接参数；否则当前服务商 + 力度路由
        if self._model_override:
            sel = next((p for p in providers
                        if p.get("name") == self._model_override_provider), None)
            if sel is None:   # 服务商名异常时按模型名回退匹配
                sel = next((p for p in providers
                            if self._model_override in (p.get("models") or [])), None)
            base_url = (sel or {}).get("base_url") or agent_llm.DEFAULT_BASE_URL
            api_key = (sel or {}).get("api_key") or agent_llm.DEFAULT_API_KEY
            protocol = (sel or {}).get("protocol") or "chat"
            model = self._model_override
        else:
            # 视觉任务（带图或需看页面截图）→ 自动切视觉模型
            vision = agent_llm.requires_vision(ai_text, send_images)
            model = agent_llm.resolve_model(cfg, effort, vision_needed=vision)
            # 同名模型多服务商（如 DeepSeek 与火山都有 deepseek-v4-flash）：自动路由时
            # 优先落到最近使用/手动指定过的服务商，避免新建对话等场景串回靠前的同名服务商
            last_provider = (self._model_override_provider
                             or str(self._settings.value("agent_last_provider", "")))
            sel = agent_llm.provider_for_model(cfg, model, provider_name=last_provider)
            base_url = (sel or {}).get("base_url") or agent_llm.DEFAULT_BASE_URL
            api_key = (sel or {}).get("api_key") or agent_llm.DEFAULT_API_KEY
            protocol = (sel or {}).get("protocol") or "chat"
        # agnes 默认模型只在内置默认服务可用：无论手动/自动选中，只要当前连接
        # 不是默认 agnes 服务就同步切过去（避免把该模型名/图片发给不支持的服务器）
        if model == agent_llm.DEFAULT_MODEL and base_url != agent_llm.DEFAULT_BASE_URL:
            base_url = agent_llm.DEFAULT_BASE_URL
            api_key = agent_llm.DEFAULT_API_KEY
        # 自动模式（下拉选「自动选择」）+ 图片：路由模型不在用户设置的多模态模型列表
        # → 改用 agnes 视觉模型（内置默认服务）处理图片而非丢弃
        # （视觉能力以用户设置的多模态模型列表为准，禁止私自按模型名推断）
        if (not self._model_override and send_images
                and not agent_llm.is_vision_model(cfg, model)):
            model = agent_llm.DEFAULT_MODEL
            base_url = agent_llm.DEFAULT_BASE_URL
            api_key = agent_llm.DEFAULT_API_KEY
        # 同步客户端连接参数（模型可能来自不同服务商）
        engine.llm.base_url = base_url.rstrip("/")
        engine.llm.api_key = api_key
        engine.llm.protocol = protocol
        engine.llm.model = model
        # 上下文上限按「本轮实际路由到的模型」对齐（同一会话内切换模型/服务商时，
        # 声明的窗口可能不同；1M 开关同样在此生效），统计面板与阈值随之下一次轮询更新
        self._apply_context_budget(engine, cfg, model=model, provider=sel)
        # 模型失败回退策略按模型来源区分：
        # - 下拉「自动选择」（无手动指定）：失败静默回退内置默认模型（不打扰）；
        # - 手动指定模型：失败不回退（直接报错），让用户感知所选模型故障。
        auto_mode = not self._model_override
        engine.llm.auto_fallback = auto_mode
        engine.llm.silent_fallback = auto_mode
        # 工作强度自动调节（Claude Code/Codex 式）：按模型正确映射 thinking/reasoning_effort，
        # 支持的服务商（DeepSeek V4 / GLM-4.5+ / OpenAI o 系列）自动附加参数，其余不发送；
        # think_mode 由模型接入页手动控制（跟随自动/始终开启/始终关闭，含每次强制思考）
        engine.llm.effort_params = agent_llm.build_effort_params(
            model, effort, self._think_mode_override())
        # 视觉能力以用户设置的多模态模型列表为准；agnes 内置视觉模型恒非纯文本
        engine.text_only = (model != agent_llm.DEFAULT_MODEL
                            and not agent_llm.is_vision_model(cfg, model))
        # 手动指定纯文本模型时剥离图片（混配模型场景逐次判断）
        if engine.text_only and send_images:
            send_images = []
        # 主 Agent 任务启动前：把本会话 @子Agent 未同步对话回合同步进引擎上下文
        # （主 Agent 对子 Agent 讨论内容有延续感知，普通提问不再"失忆"）
        try:
            self._inject_subagent_context(sid)
        except Exception:
            pass
        # 子 Agent 准入：按本轮力度分档 + 面板策略 + 用户显式要求重算（简单/中等任务
        # 禁止组队，非常复杂才放行），并写回会话以便引擎重建后沿用同一门槛
        engine.allow_subagents = self._resolve_allow_subagents(ai_text, effort, engine.workflow)
        try:
            self._sess.setdefault(sid, self._new_sess_state(sid))["allow_subagents"] = \
                engine.allow_subagents
        except Exception:
            pass
        engine.start(ai_text, "zhuzhu Copilot", send_images, skills=skill_names,
                     plugins=plugin_names or [])

    # ---------- @子Agent 直接调用（不经主 Agent 转发，独立 LLM 循环） ----------
    def _launch_subagent(self, name: str, task: str, images: list = None):
        """@子Agent 直接调用自定义子 Agent：以独立 LLM 循环执行该子任务。
        子 Agent 拥有独立上下文 + 受限工具白名单（无命令执行/无确认通道，见 agent_subagent）。
        任务运行中（主引擎/评估/其他子 Agent）拒绝启动，避免并发争用 LLM 与 UI 渲染。"""
        conf = agent_subagent.subagent_tool(name, self._effective_workflow())
        if conf is None:
            self._notify_blocked(f"子 Agent「{name}」未注册（register_sub_agent 注册后可用）")
            return
        st = self._sess.setdefault(self._session_id, self._new_sess_state(self._session_id))
        eng = st.get("engine")
        ep = self._eval_pending
        evaluating = ep is not None and ep[0] == self._session_id
        if (self._task_active or evaluating
                or (eng is not None and eng._thread and eng._thread.is_alive())
                or self._subagent_running()):
            self._notify_blocked("任务运行中，暂不可调用子 Agent（请等待本轮完成后重试）")
            return
        task = (task or "").strip()
        if not task:
            self._notify_blocked(f"请输入要交给子 Agent「{name}」的任务内容")
            return
        text = f"@{name} {task}"
        # 记录重试信息：子 Agent 调用可经「重试」重新发起
        st["last_payload"] = {"subagent": name, "text": text, "task": task,
                              "images": list(images or [])}
        self._auto_name_session(task)
        # 归档上一轮 AI 回复 + 记录用户消息（与 _do_send 一致的交错行顺序）
        if self._segments:
            archived = list(self._segments)
            self._history_segments.extend(self._segments)
            self._history_segments.append({"type": "split"})
            _prev = self._ai_bubble if self._bubble_alive(self._ai_bubble) else None
            self._rows.append({
                "type": "ai", "segs": archived,
                "cost": getattr(_prev, "cost", None),
                "meta": getattr(_prev, "turn_meta", "") or "",
            })
            # 关键：把仍指向活动段列表的气泡注册重定向到归档副本（同 _do_send）
            self._repoint_live_bubble_segs(archived)
        self._ai_bubble = None
        self._segments.clear()
        self._user_msgs.append(text)
        self._rows.append({"type": "user", "text": text})
        self._update_welcome()
        self._add_bubble(text, "user")
        self._user_stopped = False
        self._end_badge_shown = False
        self._think_done = False
        self._think_start = 0.0
        self.input.clear()
        self.input.setFocus()
        self.token_label.setText(f"~{agent_llm.estimate_tokens(text)} tk")
        self._set_action_busy()
        self._task_active = True
        self._turn_started_at = time.time()   # 回合计时起点（含子 Agent 静默期）
        self._subagent_mode = True
        self._subagent_ok = True
        self._subagent_result = ""
        self._sub_stop = threading.Event()
        # 模型路由与主引擎一致：按力度 + 当前服务商解析（子 Agent 为文本任务，无视觉需求）
        cfg = self._llm_config()
        effort = self._resolve_effort(task)
        model = agent_llm.resolve_model(cfg, effort, vision_needed=False)
        last_provider = (self._model_override_provider
                         or str(self._settings.value("agent_last_provider", "")))
        sel = agent_llm.provider_for_model(cfg, model, provider_name=last_provider)
        llm = agent_llm.LLMClient(
            base_url=(sel or {}).get("base_url") or agent_llm.DEFAULT_BASE_URL,
            api_key=(sel or {}).get("api_key") or agent_llm.DEFAULT_API_KEY,
            model=model,
            protocol=(sel or {}).get("protocol") or "chat")
        llm.effort_params = agent_llm.build_effort_params(
            model, effort, self._think_mode_override())
        try:
            self._commit_sess()
            self._write_ui_json(self._session_id, self._sess.get(self._session_id))
        except Exception:
            pass
        threading.Thread(target=self._subagent_worker,
                         args=(self._session_id, name, conf, task, llm), daemon=True).start()

    def _subagent_worker(self, sid: str, name: str, conf: dict, task: str, llm):
        """后台线程：run_sub_agent 独立循环；事件按主 Agent 标准对话流事件映射，
        经 evt_signal 回主线程按普通任务对话流渲染（与跨工作流调用主 Agent 的
        显示格式一致），不渲染「子Agent·xxx」子块。
        多轮追问：注入该子 Agent 此前会话历史（@同一子 Agent 可延续上轮上下文），
        结束后把完整对话写回会话状态，供下次 @继续追问；历史随会话持久化。"""
        st = self._sess.get(sid) or {}
        wf = st.get("workflow") or agent_workflow.active_workflow()
        stop = getattr(self, "_sub_stop", None)
        hist = list((st.get("sub_history") or {}).get(name, []) or [])

        def _save(messages, _sid=sid, _name=name):
            # 子 Agent 线程内写会话状态（dict 单 key 赋值，GIL 原子，主线程读取安全）
            _s = self._sess.get(_sid) or {}
            _s.setdefault("sub_history", {})[_name] = list(messages or [])

        def _ev(k, t, _sid=sid):
            # 子 Agent 事件 → 标准主对话流事件：
            # delta=正文流式输出；tool=工具步骤（▎正在执行）；output=执行结果块
            if k == "delta":
                self.evt_signal.emit(_sid, "delta", t)
            elif k == "tool":
                self.evt_signal.emit(_sid, "status", f"正在执行: {t}")
            elif k == "output":
                self.evt_signal.emit(_sid, "result", (name, t, []))

        try:
            # 先置思考态（转圈动画），与主引擎任务体验一致
            self.evt_signal.emit(sid, "status", "正在思考…")
            # 已注册自定义子 Agent：人格 = 显式 persona，否则用注册 goal 的自述
            # （goal 通常内含「你是一位…」式人格，直接作为系统提示；
            #  不被通用「你是子 Agent…」身份文本覆盖 —— 如「你是谁」按该人格作答）
            persona = conf.get("persona") or conf.get("goal") or ""
            out = agent_subagent.run_sub_agent(
                llm, task,
                allowed=tuple(conf.get("allowed") or []) or None,
                stop=stop.is_set if stop is not None else None,
                on_sub_event=_ev,
                workflow=wf,
                persona=persona,
                custom=True,
                history=hist,
                on_history=_save,
                # 是否加入共同上下文空间：@调用时没有主 Agent 在场逐次分配，用注册默认值
                # （共享全开：缺省开启=True；显式 false 保持关闭）；主 Agent 调用
                # sub_<name> 时仍可逐次覆盖。
                shared_context=True
                if conf.get("shared_context") is None
                else bool(conf.get("shared_context")),
                source=name,
                # 工作团控制：@子Agent 也注册控制句柄（sub:<名>），
                # 领导者可 pause/resume/warn、UI 子块可暂停/恢复
                agent_id="sub:" + str(name),
                # 对话作用域：本线程无继承的线程局部，显式落地所属会话
                # （该子 Agent 的空间读写落在本对话，跨对话隔离）
                conversation=sid)
        except Exception as e:
            out = f"子 Agent 异常: {e}"
        try:
            self.evt_signal.emit(sid, "subagent_done", out)
        except Exception:
            pass

    def _on_subagent_done(self, out: str):
        """子 Agent 独立任务结束（主线程）：记录结束态并交由 _refresh_meta 统一收尾"""
        self._sub_stop = None      # 清除运行标志 → _refresh_meta 判定空闲后统一收尾
        self._subagent_result = out or ""
        if self._user_stopped:
            self._subagent_ok = False
        # 无任何流式输出（异常/被停/空结果）时把总结补成正文段，保证结果可见
        if not self._segments:
            self._ensure_ai_bubble()
            self._ensure_text_segment()
            self._segments[-1]["raw"] = out or "（子 Agent 无输出）"
            self._refresh_ai_html()
            self._scroll_bottom()
        # 流式已结束：关闭最后一段 text 的 streaming 标记，触发完整 markdown 渲染补全
        if self._segments and self._segments[-1].get("streaming"):
            self._segments[-1]["streaming"] = False

    def _subagent_running(self) -> bool:
        """是否有子 Agent 独立任务在运行（_sub_stop 已创建且未被 set）"""
        e = getattr(self, "_sub_stop", None)
        return e is not None and not e.is_set()

    def _inject_subagent_context(self, sid: str = None):
        """把本会话中 @子Agent 的对话回合同步进主 Agent 引擎上下文（主 Agent 后续可延续）。

        子 Agent 是独立 LLM 循环，其对话从不自动进入主 Agent 的 _messages——
        @子Agent 讨论完再普通提问主 Agent 时，主 Agent 对刚才的子 Agent 内容完全无感知
        （表现为「上下文没保留在主对话中」）。此方法把 sub_history 中未同步部分的
        「用户提问 + 子 Agent 最终回复」整理为一条 user 上下文消息注入引擎 _messages，
        游标 sub_synced 幂等（只同步新增段），引擎上下文随 save_context 落盘，
        重启/切换会话后主 Agent 依然可延续。仅保留提问与最终文本，跳过工具过程防膨胀。"""
        sid = sid or self._session_id
        st = self._sess.get(sid)
        if not st:
            return
        sh = st.get("sub_history") or {}
        if not sh:
            return
        eng = st.get("engine") or self._engine_for(sid)
        if eng is None or not hasattr(eng, "_messages"):
            return
        synced = st.setdefault("sub_synced", {})

        def _text(m) -> str:
            c = m.get("content") if isinstance(m, dict) else None
            if isinstance(c, str):
                return c
            if isinstance(c, list):
                return "\n".join(str(x.get("text")) for x in c
                                  if isinstance(x, dict)
                                  and x.get("type") == "text" and x.get("text"))
            return ""

        for name, msgs in sh.items():
            msgs = [m for m in (msgs or []) if isinstance(m, dict)]
            n = len(msgs)
            k = int(synced.get(name) or 0)
            if n <= k:
                continue
            lines = []
            for m in msgs[k:]:
                role = m.get("role")
                if role == "user":
                    t = _text(m).strip()
                    if t:
                        lines.append(f"[用户 → @{name}] {t}")
                elif role == "assistant" and not m.get("tool_calls"):
                    t = _text(m).strip()
                    if t:
                        lines.append(f"[子Agent {name} 回应] {t}")
            synced[name] = n   # 无文本（纯工具段）也推进游标，避免每轮重复扫描
            body = "\n".join(lines).strip()
            if not body:
                continue
            if len(body) > 6000:
                body = body[:6000] + "\n…（记录过长已截断）"
            note = (f"（主对话中此前通过 @{name} 与子 Agent 的对话记录，属历史上下文，"
                    "供你延续理解；这不是用户发来的新指令，无需回应）\n" + body)
            try:
                content = agent_llm.build_content(note)
            except Exception:
                continue
            # 重试 @子Agent 等场景防止注入完全相同内容
            if eng._messages and isinstance(eng._messages[-1].get("content"), list):
                if [x for x in eng._messages[-1]["content"]
                        if isinstance(x, dict) and x.get("type") == "text" and x.get("text")] \
                        == [x for x in content
                            if isinstance(x, dict) and x.get("type") == "text" and x.get("text")]:
                    continue
            eng._messages.append({"role": "user", "content": content})

    def _assess_worker(self):
        """后台线程：用默认 agnes-2.5-flash 评估任务难度（失败回退本地估算）。
        任何异常都保证 emit，避免 _eval_pending 悬挂导致发送按钮无限转圈。"""
        ai_text = (self._eval_pending or (None, "", [], [], []))[1]
        effort = "medium"
        try:
            try:
                effort = agent_llm.assess_effort(ai_text)
            except Exception:
                effort = agent_llm.estimate_effort(ai_text)
        except Exception:
            effort = "medium"
        try:
            self.eval_signal.emit(effort)
        except Exception:
            pass

    def _on_assess_done(self, effort: str):
        """评估完成：按难度路由模型并启动任务（不显示评估文字，保持滑动动画）。
        启动失败兜底复位任务状态与按钮，绝不让发送按钮卡在转圈。"""
        if not self._eval_pending:
            return
        sid, ai_text, send_images, skill_names, plugin_names = self._eval_pending
        self._eval_pending = None
        if sid == self._session_id and self._user_stopped:
            # 用户已在评估期间点击停止：放弃启动并复位按钮
            self._task_active = False
            self._hide_spinner()
            self._set_action_idle()
            return
        try:
            self._launch_task(ai_text, send_images, skill_names, effort, sid=sid,
                              plugin_names=plugin_names)
        except Exception as e:
            import logging
            logging.getLogger(app_identity.APP_SLUG).exception("评估后任务启动失败: %s", e)
            self._task_active = False
            self._hide_spinner()
            self._set_action_idle()
            try:
                self._toast("任务启动失败", str(e), warn=True)
            except Exception:
                pass

    def _stop(self):
        # 捕获当次要停止的目标引擎：兜底回调 5s 后触发，期间用户可能已切换到其它
        # 会话启动新任务，self._engine 已指向新引擎。若兜底误用当前引擎会把仍在
        # 运行的其它会话强制隔离，故这里锁定当次要停的引擎，仅在从未切换时生效。
        self._stop_engine = self._engine
        # 评估阶段（引擎线程未启动）视为可停止：仍在评估的任务置停止态，评估完成自动复位。
        ep = self._eval_pending
        evaluating = ep is not None and ep[0] == self._session_id
        running_engine = bool(self._engine and self._engine._thread
                              and self._engine._thread.is_alive())
        sub_running = self._subagent_running()
        if not evaluating and not running_engine and not sub_running:
            # 实际并无任务在跑（任务早已结束/引擎已退出）：直接复位按钮，
            # 避免“无任务却停在暂停/停止中”，也不再调度强制隔离兜底。
            self._user_stopped = True
            self._task_active = False
            self._hide_spinner()
            self._set_action_idle()
            return
        if sub_running and getattr(self, "_sub_stop", None) is not None:
            self._sub_stop.set()   # 中断 @子Agent 独立任务（run_sub_agent 循环检测退出）
        if self._stop_engine:
            self._stop_engine.stop()
        self._user_stopped = True
        self._set_action_stopping()   # 红底转圈（禁用）
        # 兜底：5 秒后线程仍未退出（卡死）→ 强制隔离
        QTimer.singleShot(5000, self._force_stop_if_stuck)

    def _force_stop_if_stuck(self):
        """强制停止兜底：普通 stop 后线程仍卡死（LLM/MCP 阻塞）时隔离引擎。
        仅对 _stop 当时锁定的目标引擎生效：若期间已切换到其它会话/其它引擎，
        不误伤当前正在运行的新任务引擎。"""
        eng = getattr(self, "_stop_engine", None)
        if not (eng and eng._thread and eng._thread.is_alive()):
            return   # 线程已正常退出，无需兜底
        # 仅当目标引擎仍是全局当前引擎时才做状态复位；否则只断开其回调防污染，
        # 不动当前会话的全局引用与状态（避免把新任务的引擎一并清掉）
        is_current = self._engine is eng
        eng.on_delta = None
        eng.on_status = None
        eng.on_result = None
        eng.on_reasoning = None
        eng._stop.set()
        if is_current:
            st = self._sess.get(self._session_id)
            if st and st.get("engine") is eng:
                st["engine"] = None   # 下次发送时重建全新引擎
            self._engine = None
            self._notify_blocked("AI 线程无法中断，已强制隔离（后台线程已断开，新任务将自动重建）")
            self._task_active = False
            self._hide_spinner()
            self._set_action_idle()
            if not self._end_badge_shown:
                self._end_badge_shown = True
                self._show_end_badge()
            self._scroll_bottom()

    def _do_compact(self):
        """/compact：由当前模型自主生成摘要压缩上下文（后台线程执行，失败回退启发式）。
        仅允许在本会话空闲时进行：任务线程仍在运行时执行会与读写 _messages 竞争，
        导致上下文损坏，故直接拒绝并提示。"""
        self.input.clear()
        eng = self._engine
        if not eng or not eng._messages:
            return
        if eng._thread and eng._thread.is_alive():
            self._notify_blocked("任务运行中，暂不支持压缩上下文")
            return
        self._ensure_compact_row()
        threading.Thread(target=self._compact_worker, daemon=True).start()

    def _compact_worker(self):
        """后台线程：执行模型自主摘要压缩，完成后发信号回主线程更新状态"""
        try:
            n = self._engine._auto_compress(keep_recent=2)
        except Exception:
            n = 0
        self.compact_signal.emit(int(n or 0))

    def _on_compact_done(self, n: int):
        self._hide_compact_row()

    def _delete_session(self, sid: str):
        """永久删除会话：移除元数据记录并删除磁盘上的全部会话文件"""
        # 共同上下文空间按对话隔离 → 该对话的空间随对话一并回收，不残留占用（也不影响其他对话）
        try:
            from zhuzhu_Copilot.core import agent_context
            agent_context.close_conversation(sid)
        except Exception:
            pass
        d = self._sessions_dir()
        for fn in (f"{sid}.json", f"{sid}.ui.json"):
            try:
                (d / fn).unlink(missing_ok=True)
            except Exception:
                pass
        lst = [x for x in self._load_session_list() if x.get("id") != sid]
        self._save_session_list(lst)

    def _session_right_click_filter(self, obj, ev):
        """会话下拉列表右键守卫（eventFilter）：吞掉 view/viewport 上的右键 press/release，
        阻止 QComboBox popup 把被点项同步为 currentIndex（否则右键会先切进该对话再弹菜单）。
        右键的 context 菜单事件由 Qt 独立分发，不受此拦截影响。"""
        t = ev.type()
        if (t in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease,
                  QEvent.Type.MouseButtonDblClick)
                and ev.button() == Qt.MouseButton.RightButton):
            return True   # 已处理：不改变 selection/currentIndex
        return False

    def _on_session_context_menu(self, pos):
        """对话下拉列表右键菜单：提供删除对话入口"""
        view = self.session_combo.view()
        idx = view.indexAt(pos)
        if not idx.isValid():
            return
        sid = self.session_combo.itemData(idx.row())
        if not sid:
            return
        name = self.session_combo.itemText(idx.row())
        orig = self.session_combo.currentIndex()   # 记录原索引，防止 popup 关闭误切换对话
        menu = QMenu(self)
        del_act = menu.addAction(f"删除对话「{name}」")
        act = menu.exec(view.viewport().mapToGlobal(pos))
        # 菜单/下拉关闭时 QComboBox 会把高亮项同步为当前项，误触发 _on_session_selected：
        # 屏蔽信号恢复原索引，避免右键一下却切进了被点的对话
        if self.session_combo.currentIndex() != orig:
            self.session_combo.blockSignals(True)
            self.session_combo.setCurrentIndex(orig)
            self.session_combo.blockSignals(False)
        if act == del_act:
            self._remove_session(sid, name)

    def _remove_session(self, sid: str, name: str):
        """右键删除对话：确认后删除文件；若删除的是当前对话则清理并切到最近对话/新建"""
        if not self._confirm_box("永久删除对话",
                                 f"确定永久删除对话「{name}」吗？\n所有记录将无法恢复！"):
            return
        self._delete_session(sid)
        # 停止并清理该会话的独立引擎与内存态
        st = self._sess.pop(sid, None)
        if st:
            # 释放挂起的确认/提问等待，让后台引擎线程及时退出
            if st.get("confirm_evt"):
                st["confirm_evt"].set()
            if st.get("ask_evt"):
                st["ask_evt"].set()
            eng = st.get("engine")
            if eng:
                eng.clear_history()
                eng.clear_context()
                if eng._thread and eng._thread.is_alive():
                    eng.stop()
        if sid == self._session_id:
            # 删除当前对话：清理内存（_session_id 先置空，防止切会话时复活文件）
            self._session_id = None
            self._segments = []
            self._user_msgs = []
            self._ai_bubble = None
            self._bubble_widgets = []
            self._bubble_segs = {}
            self._msg_nav_clear()   # 删除会话清空定位圆点
            self._hide_spinner()
            self._stop_button_anim()
            self._task_active = False
            self._user_stopped = False
            self._end_badge_shown = False
            self._clear_attachments()
            self.cmd_list.hide()
            while self.msg_lay.count() > 1:
                item = self.msg_lay.takeAt(0)
                self._free_layout_item(item)
            lst = self._load_session_list()
            if lst:
                lst.sort(key=lambda s: s.get("updated", 0))
                self._switch_to(lst[-1]["id"])
            else:
                self._new_session()
        else:
            self._refresh_session_combo()

    def _confirm_box(self, title: str, text: str) -> bool:
        """暗色主题确认框（白色字体），点"是"返回 True，点"否"返回 False。
        液态玻璃下改用浅色液态底（不透明，黑字可读），与 AskUser/命令确认弹窗风格统一。"""
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(text)
        box.setIcon(QMessageBox.Icon.Question)
        yes = box.addButton("是", QMessageBox.ButtonRole.YesRole)
        box.addButton("否", QMessageBox.ButtonRole.NoRole)
        # 默认焦点给"否"，防误触删除
        no_btn = box.buttons()[1]
        box.setDefaultButton(no_btn)
        if agent_ui_ux.is_custom_package_active():
            _box_bg = "#F8FAFD"
            _btn_bg = "rgba(255,255,255,150)"
            _btn_bd = "rgba(255,255,255,220)"
        else:
            _box_bg = PANEL
            _btn_bg = AI_BG
            _btn_bd = BORDER
        box.setStyleSheet(
            f"QMessageBox {{ background: {_box_bg}; }}"
            f"QMessageBox QLabel {{ color: {TEXT}; font-size: 13px; }}"
            f"QMessageBox QPushButton {{ color: {TEXT}; background: {_btn_bg};"
            f"border: 1px solid {_btn_bd}; border-radius: 8px; padding: 6px 20px;"
            "font-size: 13px; font-weight: 600; }}"
            f"QMessageBox QPushButton:hover {{ background: {HOVER}; border-color: {ACCENT}; }}")
        box.exec()
        return box.clickedButton() is yes

    def _clear_chat(self, *_):
        """清空上下文并永久删除当前对话（二次弹窗确认，删除不可恢复）"""
        if not self._session_id:
            return
        # 第一次确认：清空上下文与聊天记录
        if not self._confirm_box("清空对话",
                                 "确定要清空当前对话吗？\n将清空全部上下文与聊天记录。"):
            return
        # 第二次确认：永久删除该对话（不可恢复）
        if not self._confirm_box("永久删除对话",
                                 "该对话将连同所有记录被永久删除，无法恢复！\n确定继续吗？"):
            return
        # ---- 执行：停止该会话引擎 + 清空上下文与气泡 + tokens 归零 ----
        old_id = self._session_id
        old_st = self._sess.pop(old_id, None)
        # 沿用刚被清空对话的工作流：清空上下文不应顺手改掉用户选择的工作流
        prev_wf = agent_workflow.resolve_workflow(
            (old_st or {}).get("workflow") or agent_workflow.active_workflow())
        if old_st:
            if old_st.get("confirm_evt"):
                old_st["confirm_evt"].set()
            if old_st.get("ask_evt"):
                old_st["ask_evt"].set()
            eng = old_st.get("engine")
            if eng:
                eng.clear_history()
                eng.clear_context()   # 同时删除磁盘上的持久化上下文
                if eng._thread and eng._thread.is_alive():
                    eng.stop()
        self._ai_bubble = None
        self._segments = []
        self._history_segments = []
        self._seg_cache.clear()
        self._sub_segs.clear()
        self._user_msgs = []
        self._rows = []
        self._hide_spinner()
        self.cmd_list.hide()
        self._stop_button_anim()   # 融合按钮恢复空闲发送状态
        self._task_active = False
        self._user_stopped = False
        self._end_badge_shown = False
        self._think_done = False
        self._think_start = 0.0
        self._last_activity = 0.0
        self._clear_attachments()
        while self.msg_lay.count() > 1:  # 保留末尾 stretch
            item = self.msg_lay.takeAt(0)
            self._free_layout_item(item)
        self._bubble_widgets = []   # 清空气泡引用，避免 resizeEvent 处理已删除对象
        self._bubble_segs = {}
        self._msg_nav_clear()       # 清空对话定位圆点
        self.token_label.setText("0 tk")
        # ---- 永久删除该对话，并新开空会话（界面回到欢迎页） ----
        self._delete_session(old_id)
        s = self._create_session()
        self._session_id = s["id"]
        self._session_name = "新对话"
        _st = self._new_sess_state(s["id"])
        _st["loaded"] = True   # 新会话无历史竞态，允许正常落盘（同 _new_session 修复）
        _st["workflow"] = prev_wf   # 沿用原对话的工作流（与「新建对话」一致）
        self._sess[s["id"]] = _st
        self._bind_sess(s["id"])
        self._refresh_session_combo()
        self._update_welcome()
        self._update_queue_bar()

    # ---------- 拖拽/粘贴/上传附件（图片/文件） ----------
    _IMG_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp")

    # 文件缩略图自定义样式：扩展名 → (徽章文字, 底色)。未收录的扩展名显示其大写形式
    _FILE_THUMB_STYLE = {
        ".py": ("PY", "#3572A5"), ".cs": ("CS", "#178600"),
        ".cpp": ("C++", "#5C8DBC"), ".cc": ("C++", "#5C8DBC"), ".cxx": ("C++", "#5C8DBC"),
        ".c": ("C", "#555555"), ".h": ("H", "#555555"),
        ".java": ("JAVA", "#E76F00"), ".kt": ("KT", "#7F52FF"), ".kts": ("KT", "#7F52FF"),
        ".html": ("HTML", "#E44D26"), ".htm": ("HTML", "#E44D26"), ".css": ("CSS", "#264DE4"),
        ".js": ("JS", "#F0C000"), ".mjs": ("JS", "#F0C000"), ".ts": ("TS", "#3178C6"),
        ".jsx": ("JSX", "#61DAFB"), ".tsx": ("TSX", "#3178C6"), ".vue": ("VUE", "#41B883"),
        ".json": ("JSON", "#7A5BC0"), ".xml": ("XML", "#8A93A6"), ".yaml": ("YAML", "#8A93A6"),
        ".yml": ("YML", "#8A93A6"), ".toml": ("TOML", "#8A93A6"),
        ".md": ("MD", "#4B6BD6"), ".txt": ("TXT", "#8A93A6"), ".rst": ("RST", "#8A93A6"),
        ".sql": ("SQL", "#E38C00"), ".sh": ("SH", "#4EAA25"), ".bash": ("BASH", "#4EAA25"),
        ".bat": ("BAT", "#4EAA25"), ".ps1": ("PS1", "#012456"), ".cmd": ("CMD", "#4EAA25"),
        ".go": ("GO", "#00ADD8"), ".rs": ("RS", "#DEA584"), ".rb": ("RB", "#CC342D"),
        ".php": ("PHP", "#777BB4"), ".swift": ("SWIFT", "#F05138"), ".dart": ("DART", "#0175C2"),
        ".csv": ("CSV", "#27AE60"), ".xlsx": ("XLSX", "#217346"), ".xls": ("XLS", "#217346"),
        ".docx": ("DOCX", "#2B579A"), ".doc": ("DOC", "#2B579A"), ".pptx": ("PPT", "#D24726"),
        ".pdf": ("PDF", "#E74C3C"), ".ipynb": ("IPY", "#F37726"),
        ".zip": ("ZIP", "#B8860B"), ".7z": ("7Z", "#B8860B"), ".rar": ("RAR", "#B8860B"),
        ".tar": ("TAR", "#B8860B"), ".gz": ("GZ", "#B8860B"),
        ".exe": ("EXE", "#3B3B3B"), ".dll": ("DLL", "#3B3B3B"), ".msi": ("MSI", "#3B3B3B"),
        ".iso": ("ISO", "#3B3B3B"), ".apk": ("APK", "#3DDC84"), ".svg": ("SVG", "#FFB13B"),
        ".ico": ("ICO", "#8A93A6"), ".ini": ("INI", "#8A93A6"), ".cfg": ("CFG", "#8A93A6"),
        ".log": ("LOG", "#8A93A6"), ".db": ("DB", "#8A93A6"), ".sqlite": ("DB", "#8A93A6"),
    }

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dropEvent(self, e):
        for url in e.mimeData().urls():
            p = url.toLocalFile()
            if p:
                self._add_attachment(p)
        e.acceptProposedAction()

    def _setup_admin_dnd(self):
        """管理员权限下启用 WM_DROPFILES 原生拖放通道（UIPI 拦截 OLE 拖放的绕行方案）"""
        if self._admin_dnd:
            return
        print(f"[dnd] 管理员拖放通道 setup（hwnd={int(self.winId())}）", flush=True)
        try:
            hwnd = int(self.winId())
            user32 = ctypes.windll.user32
            shell32 = ctypes.windll.shell32
            ole32 = ctypes.windll.ole32
            # 进程级放行（作用于进程内所有窗口）
            for m in (_WM_DROPFILES, _WM_COPYDATA, _WM_COPYGLOBALDATA):
                user32.ChangeWindowMessageFilter(m, _MSGFLT_ADD)
            # Qt 会在每个 setAcceptDrops 控件（含输入框等子控件）上注册 OLE IDropTarget。
            # 只撤销顶层注册时，鼠标悬停在子控件上 Explorer 仍走 OLE 路径、被 UIPI 拦截
            # （表现为"禁用圆圈"拖不进来）。因此对本进程所有窗口：
            # 1) 窗口级放行 WM_DROPFILES/COPYDATA/COPYGLOBALDATA（UIPI 消息过滤）
            # 2) RevokeDragDrop 移除全部 OLE 拖放注册，强制 Explorer 回退 WM_DROPFILES
            _ex = user32.ChangeWindowMessageFilterEx
            _ex.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint,
                            ctypes.c_void_p]
            hwnds = _all_process_hwnds()
            for w in hwnds:
                for m in (_WM_DROPFILES, _WM_COPYDATA, _WM_COPYGLOBALDATA):
                    _ex(w, m, _MSGFLT_ADD, None)
                ole32.RevokeDragDrop(w)
            print(f"[dnd] 已对 {len(hwnds)} 个窗口放行消息并撤销 OLE 拖放注册", flush=True)
            # WM_DROPFILES 只注册在顶层窗口：落点坐标为顶层客户区基准（_deliver 的 childAt 依赖）
            shell32.DragAcceptFiles.restype = None   # 该函数返回 VOID，显式声明避免误读
            shell32.DragAcceptFiles(hwnd, True)
            print("[dnd] DragAcceptFiles(顶层) 完成", flush=True)
            self._admin_drop_filter = _AdminDropFilter(self)
            QApplication.instance().installNativeEventFilter(self._admin_drop_filter)
            self._admin_dnd = True
            print("[dnd] 管理员拖放通道启用成功", flush=True)
        except Exception as e:
            self._admin_dnd = False
            self._notify_blocked(f"管理员拖放通道启用失败：{e}")
            print("[dnd] 启用失败:", repr(e), flush=True)

    def _on_input_files_dropped(self, paths: list):
        """输入框文件拖入：逐个加入附件（图片/文件，纯文本模型自动过滤图片）"""
        for p in paths or []:
            self._add_attachment(p)

    def _add_attachment(self, path: str):
        path = os.path.abspath(path)
        if not os.path.exists(path):
            self._notify_blocked(f"文件不存在: {path}")
            return
        if self._text_only and os.path.splitext(path)[1].lower() in self._IMG_EXTS:
            return
        if os.path.splitext(path)[1].lower() in self._IMG_EXTS:
            img = QImage(path)
            if img.isNull():
                self._notify_blocked(f"无法读取图片: {path}")
                return
            # 压缩为 ≤320px JPEG data URL 发送给模型（缩略图预览用原图）
            if img.width() > 320:
                img = img.scaledToWidth(320, Qt.TransformationMode.SmoothTransformation)
            ba = QByteArray()
            buf = QBuffer(ba)
            buf.open(QIODevice.OpenModeFlag.WriteOnly)
            img.save(buf, "JPEG", 80)
            data_url = ("data:image/jpeg;base64,"
                        + base64.b64encode(bytes(ba)).decode())
            self._pending_images.append(data_url)
            self._attach_thumb(QPixmap(path), path, remove=("img", data_url))
        else:
            self._pending_files.append(path)
            self._attach_thumb(self._file_thumb(path), path,
                               name=os.path.basename(path),
                               remove=("file", path))

    @staticmethod
    def _file_thumb(path: str, size: int = 56) -> QPixmap:
        """自定义文件缩略图：按扩展名生成彩色圆角徽章（PY/C++/JAVA/HTML…），
        未收录的扩展名显示其大写形式，替代单调的系统图标"""
        ext = os.path.splitext(path or "")[1].lower()
        label, color = AgentPanel._FILE_THUMB_STYLE.get(
            ext, (ext[1:].upper() or "FILE", "#6B7280"))
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color))
        p.drawRoundedRect(2, 2, size - 4, size - 4, 10, 10)
        f = QFont("Segoe UI", max(7, int(size * 0.20)))
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("#FFFFFF"))
        p.drawText(QRect(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, label[:5])
        p.end()
        return pm

    def _file_thumb_data_url(self, path: str) -> str:
        """文件徽章缩略图 → PNG data URL（嵌入发送后用户气泡富文本）"""
        pm = self._file_thumb(path)
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        pm.save(buf, "PNG")
        return "data:image/png;base64," + base64.b64encode(bytes(ba)).decode()

    def _paste_clipboard_image(self) -> bool:
        """Ctrl+V：剪贴板含图片时作为附件加入（多模态模型）；无图返回 False 走默认文本粘贴"""
        clip = QApplication.clipboard()
        if not clip.mimeData().hasImage():
            return False
        img = clip.image()
        if img.isNull():
            return False
        if self._text_only:
            return True   # 吞掉事件，避免图片被当文本粘贴进输入框
        if img.width() > 320:
            img = img.scaledToWidth(320, Qt.TransformationMode.SmoothTransformation)
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        img.save(buf, "JPEG", 80)
        data_url = ("data:image/jpeg;base64,"
                    + base64.b64encode(bytes(ba)).decode())
        self._pending_images.append(data_url)
        self._attach_thumb(QPixmap.fromImage(img), "剪贴板截图（Ctrl+V 粘贴）",
                           remove=("img", data_url))
        return True

    def eventFilter(self, obj, event):
        """拦截输入框按键：Tab 补全命令；Ctrl+V 剪贴板图片转附件。
        （文件拖放由面板 dragEnterEvent/dropEvent 统一处理，无需在此接管）
        重试按钮热区 hover：鼠标移入显示图标，移出恢复透明热区。"""
        # 骨架期守卫：本过滤器可能被早于输入框创建的控件（历史案例：顶栏状态按钮，
        # build_default_ui 内即 installEventFilter(self)，而 self.input 更晚才 new）
        # 装上，此期间其 StyleChange/ToolTip 等事件会走到下面的属性访问 → AttributeError
        # （PyQt6 未捕获异常直接弹崩溃框）。输入框未就绪时一律放行，不介入事件，
        # 从而不依赖任何初始化顺序（后续新增控件亦安全）。
        _inp = getattr(self, "input", None)
        if _inp is None:
            return super().eventFilter(obj, event)
        if obj is _inp and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Tab and self.cmd_list.isVisible():
                return self._complete_cmd()
            if event.matches(QKeySequence.StandardKey.Paste) and \
                    self._paste_clipboard_image():
                return True
        if getattr(obj, "_is_retry_btn", False):
            # 重试热区（按钮位置）：移入浮现图标，移出（且不在气泡上）恢复透明
            if event.type() == QEvent.Type.Enter:
                self._set_retry_icon(obj, True)
            elif event.type() == QEvent.Type.Leave:
                host = getattr(obj, "_host_bubble", None)
                if not self._retry_pointer_in(obj, host):
                    self._set_retry_icon(obj, False)
            return super().eventFilter(obj, event)
        # 附着到聊天气泡本体：同样触发重试图标显示（仅最后一条已完成的回复）
        if event.type() in (QEvent.Type.Enter, QEvent.Type.Leave):
            rbtn = getattr(obj, "_retry_btn", None)
            if rbtn is not None and obj is not rbtn:
                armed = (obj is self._ai_bubble
                         and getattr(obj, "_retry_armed", False)
                         and not rbtn.isHidden())
                if armed:
                    if event.type() == QEvent.Type.Enter:
                        self._set_retry_icon(rbtn, True)
                    elif not self._retry_pointer_in(rbtn, obj):
                        self._set_retry_icon(rbtn, False)
                return super().eventFilter(obj, event)
        return super().eventFilter(obj, event)

    def _retry_pointer_in(self, btn, bubble) -> bool:
        """光标是否仍在重试热区或其宿主气泡上（leave 时防相邻区域切换误隐藏图标）"""
        pos = QCursor.pos()
        for w in (btn, bubble):
            if w is None or not w.isVisible():
                continue
            try:
                if w.rect().contains(w.mapFromGlobal(pos)):
                    return True
            except Exception:
                pass
        return False

    def _pick_attachments(self, *_):
        """「+」上传按钮：文件选择器多选，图片/文件均可（纯文本模型自动过滤图片）"""
        paths, _ = QFileDialog.getOpenFileNames(self, "选择文件/图片发送给 AI")
        for p in paths or []:
            self._add_attachment(p)

    @staticmethod
    def _round_pixmap(src: QPixmap, size: int, radius: int = 8) -> QPixmap:
        """把任意图片按方形圆角裁剪（居中裁切），用于附件卡片/气泡缩略图"""
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(0, 0, size, size, radius, radius)
        p.setClipPath(path)
        p.drawPixmap(0, 0, src.scaled(size, size,
                                      Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                      Qt.TransformationMode.SmoothTransformation))
        p.end()
        return pm

    @staticmethod
    def _file_size_text(path: str) -> str:
        """文件大小人性化显示（B/KB/MB）"""
        try:
            n = os.path.getsize(path)
        except OSError:
            return ""
        if n < 1024:
            return f"{n} B"
        if n < 1024 * 1024:
            return f"{n / 1024:.1f} KB"
        return f"{n / 1024 / 1024:.1f} MB"

    def _attach_thumb(self, pixmap: QPixmap, tooltip: str, name: str = "",
                      remove: tuple = None):
        """附件条缩略图卡片：圆角方形缩略图 + 文件名，右上角 × 可单独取消上传。
        remove: (kind, value) —— kind ∈ img/file，value 为 data_url 或文件路径，用于取消时移除"""
        box = QWidget()
        box.setObjectName("attCard")
        box.setToolTip(tooltip)
        box.setFixedSize(78, 86)
        box.setStyleSheet(
            f"#attCard {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 10px; }}"
            f"#attCard:hover {{ background: {HOVER}; border: 1px solid {ACCENT}; }}")
        v = QVBoxLayout(box)
        v.setContentsMargins(6, 8, 6, 6)
        v.setSpacing(4)
        v.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thumb = QLabel()
        thumb.setFixedSize(44, 44)
        thumb.setPixmap(self._round_pixmap(pixmap, 44))
        thumb.setToolTip(tooltip)
        v.addWidget(thumb, 0, Qt.AlignmentFlag.AlignCenter)
        if name:
            nl = QLabel(name if len(name) <= 9 else name[:8] + "…")
            nl.setStyleSheet(f"color: {TEXT_DIM}; font-size: 10px;")
            nl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            nl.setToolTip(tooltip)
            v.addWidget(nl, 0, Qt.AlignmentFlag.AlignCenter)
        # 右上角取消按钮：手动定位（不加入布局），点击移除该附件
        if remove:
            rm = QPushButton("×")
            rm.setFixedSize(18, 18)
            rm.setCursor(Qt.CursorShape.PointingHandCursor)
            rm.setToolTip("取消上传")
            rm.setStyleSheet(
                f"QPushButton {{ background: {PANEL}; color: {TEXT_DIM};"
                f" border: 1px solid {BORDER};"
                " border-radius: 9px; font-size: 12px; font-weight: 700; }"
                "QPushButton:hover { background: #E74C3C; color: white; }")
            rm.clicked.connect(lambda: self._remove_attachment(box, remove[0], remove[1]))
            rm.setParent(box)
            rm.move(57, 3)
        # 插入到 stretch 之前
        self._attach_lay.addWidget(box)
        self._attach_bar.setVisible(True)

    def _remove_attachment(self, box: QWidget, kind: str, value):
        """取消单个附件：从附件条移除缩略图并从待发送列表剔除"""
        for i in range(self._attach_lay.count()):
            it = self._attach_lay.itemAt(i)
            if it and it.widget() is box:
                self._attach_lay.takeAt(i)
                break
        box.deleteLater()
        if kind == "img":
            self._pending_images = [x for x in self._pending_images if x != value]
        else:
            self._pending_files = [x for x in self._pending_files if x != value]
        if not self._pending_images and not self._pending_files:
            self._attach_bar.setVisible(False)

    def _clear_attachments(self):
        while self._attach_lay.count() > 0:
            item = self._attach_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._pending_images = []
        self._pending_files = []
        self._attach_bar.setVisible(False)

    def _free_layout_item(self, item):
        if item.widget():
            item.widget().deleteLater()
        elif item.layout():
            while item.layout().count():
                self._free_layout_item(item.layout().takeAt(0))
            item.layout().deleteLater()

    @staticmethod
    def _clear_layout(layout):
        """递归清空一个 QLayout 的全部子项（widget 与嵌套布局），供就地重建 UI 使用。
        widget 先 hide + 脱离父级（立即不再显示/参与布局）再 deleteLater 彻底释放，
        避免重建瞬间旧控件（如液态玻璃标题栏）仍残留在面板上。"""
        if layout is None:
            return
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                try:
                    w.hide()
                    w.setParent(None)
                except Exception:
                    pass
                w.deleteLater()
            else:
                sub = item.layout()
                if sub is not None:
                    AgentPanel._clear_layout(sub)
                    sub.deleteLater()

    @staticmethod
    @staticmethod
    def _panel_root_qss() -> str:
        """面板根样式表（背景渐变 + 下拉 + 主题自适应滚动条），__init__ 与 _retheme 共用，
        保证就地重建时根背景/边框随新主题立即刷新。
        自定义主题（如液态玻璃）用透明背景透出 Acrylic；默认主题用主题渐变底。
        注意：无边框模式下用 border-radius 实现圆角（比 setMask 更可靠）。"""
        _radius = "18px" if agent_ui_ux.is_custom_package_active() else "0px"
        if agent_ui_ux.is_custom_package_active():
            return (f"QDialog {{ background: transparent; border-radius: {_radius}; }}"
                    + _QCOMBO
                    + _scrollbar_css(8, 4, both=True))
        return (f"QDialog {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1,"
                f"stop:0 {BG}, stop:1 {BG_BOTTOM}); border-radius: {_radius}; }}"
                + _QCOMBO
                + _scrollbar_css(8, 4, both=True))

    def _retheme(self):
        """切换主题立即生效：就地重建本面板的 UI（同一面板实例，不关闭窗口、
        不停止任何运行中的引擎/任务）。会话内存态、对话历史、排队消息、后台任务
        全部原样保留；仅重建控件样式与聊天气泡，使用新主题色。
        重建前后同步面板几何/窗口状态/输入框/滚动位置，避免切换时面板偏移与内容跳动。
        """
        from PyQt6.QtWidgets import QApplication
        # 0) 备份面板几何/窗口状态与输入框/滚动状态（重建后恢复，避免偏移与丢失）
        geo = self.geometry()
        win_state = self.windowState()
        try:
            input_text = self.input.toPlainText()
            input_cursor = self.input.textCursor().position()
        except Exception:
            input_text, input_cursor = "", 0
        try:
            bar = self.msg_area.verticalScrollBar()
            scroll_val = bar.value()
            old_max = bar.maximum()
        except Exception:
            scroll_val, old_max = 0, 0
        # 切换前是否已滚到最底部（重建后若在底部则保持贴底，否则恢复原滚动位置）
        was_at_bottom = old_max <= 0 or scroll_val >= old_max - 2

        # 0.5) 窗口标志随 UIUX 包同步：自定义包（液态玻璃等）必须无边框 + 自定义
        # 标题栏，默认包保留系统标题栏。窗口标志只在 __init__ 按启动时的包判断一次，
        # 若此处不同步，切到玻璃包后系统标题栏仍保留、与包内自定义标题栏并存，表现
        # 为"切换后残留原有 UIUX 且无法操作"；且透明背景 + 非无边框组合下滚动重绘
        # 异常（白条累积）。注意：setWindowFlags 会触发窗口原生重建（隐藏→重建），
        # 必须在本阶段（控件树完整可见、尚未开始清理/重建）执行，并在设置后立即
        # 恢复显示，避免重建中途调用导致崩溃。
        try:
            _is_custom = (agent_ui_ux.get_active_package()
                          != agent_ui_ux.DEFAULT_PACKAGE)
            _f = self.windowFlags()
            if _is_custom:
                if not (_f & Qt.WindowType.FramelessWindowHint):
                    self.setWindowFlags(
                        Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
                    if win_state & Qt.WindowState.WindowMaximized:
                        self.showMaximized()
                    else:
                        self.show()
                    self.raise_()
                    self.activateWindow()
            else:
                if _f & Qt.WindowType.FramelessWindowHint:
                    self.setWindowFlags(
                        _f & ~Qt.WindowType.FramelessWindowHint
                        | Qt.WindowType.Window
                        | Qt.WindowType.WindowMinMaxButtonsHint
                        | Qt.WindowType.WindowMaximizeButtonHint
                        | Qt.WindowType.WindowMinimizeButtonHint)
                    if win_state & Qt.WindowState.WindowMaximized:
                        self.showMaximized()
                    else:
                        self.show()
                    self.raise_()
                    self.activateWindow()
        except Exception:
            pass

        # 立即补套主面板圆角蒙版：setWindowFlags 重建窗口会清除 Qt 蒙版，必须重设
        # 注意：setWindowFlags 重建窗口后需要延迟一下，确保窗口完全重建后再应用 SetWindowRgn
        try:
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(0, self._apply_window_round)
            QTimer.singleShot(50, self._apply_window_round)
            QTimer.singleShot(100, self._apply_window_round)
        except Exception:
            pass

        # 0.5) 防止 setWindowFlags 重建窗口期间短暂显示系统默认黑色背景：
        # 立即按当前包类型设置背景（透明=液态玻璃 / 渐变=默认），确保重建过程中
        # 面板始终有正确的底色，不出现黑屏闪烁。
        try:
            _cur_is_custom = (agent_ui_ux.get_active_package()
                              != agent_ui_ux.DEFAULT_PACKAGE)
            if _cur_is_custom:
                self.setStyleSheet("QDialog { background: transparent; border-radius: 18px; }")
            else:
                self.setStyleSheet(
                    f"QDialog {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1,"
                    f"stop:0 {BG}, stop:1 {BG_BOTTOM}); border-radius: 18px; }}")
        except Exception:
            pass

        # 更新模块级颜色常量与派生样式常量（_QCOMBO/_BTN_*/_scrollbar_css 等）
        # 重建期间挂起应用级全局 QSS 的全量重算：旧控件树即将销毁，对其逐个 polish 是
        # 纯浪费（反复 app.setStyleSheet 会长时间阻塞主线程，导致"切换后主面板无响应"）。
        # 重建完成后再统一应用一次（见步骤 11）。
        global _APP_QSS_DEFER
        _APP_QSS_DEFER = True
        try:
            new_theme = apply_theme()
        finally:
            _APP_QSS_DEFER = False

        # 1) 记录旧停靠子窗口位置（重建后原位恢复，避免侧栏偏移/重叠），随后关闭销毁。
        #    注意：_build_ui（默认/玻璃包）每次都会重建 4 个侧栏窗口并重绑属性，旧窗口
        #    必须 deleteLater 销毁，否则会随每次切换累积泄漏（控件树越滚越大，应用级
        #    样式重算随之越来越慢）；并立即 flush，避免旧窗口在重建期间被全树重算波及。
        side_pos = {}
        for name in ("todos_win", "git_win", "wt_win", "code_win"):
            w = getattr(self, name, None)
            if w is not None:
                try:
                    side_pos[name] = (w.pos().x(), w.pos().y(),
                                      w.width(), w.height())
                    w.close()
                    w.deleteLater()
                except Exception:
                    pass
        # 扩展面板随主题重建一并销毁（工厂可能依赖主题色常量），
        # _build_ui → _ensure_ext_panels 随后按注册表重建
        for w in (getattr(self, "_ext_panels", None) or {}).values():
            if w is None:
                continue
            try:
                w.close()
                w.deleteLater()
            except Exception:
                pass
        self._ext_panels = {}
        try:
            from PyQt6.QtCore import QCoreApplication, QEvent
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        except Exception:
            pass

        # 2) 备份待恢复的会话视图状态，重建后再重新绑定渲染
        sid = self._session_id
        pending_files = list(getattr(self, "_pending_files", []) or [])
        pending_images = list(getattr(self, "_pending_images", []) or [])

        # 3) 移除旧布局：自定义 UI/UX 包可能在 self 上额外套了一层外层布局（如液态玻璃的
        # titlebar+root 结构），仅删 _root_lay 会导致重建时 QVBoxLayout(self) 抛
        # "widget already has a layout" → 面板回退默认/按钮全部消失。因此同时删除窗口上
        # 实际安装的布局（self.layout()），确保重建可干净安装新布局。
        if self._root_lay is not None:
            try:
                self._clear_layout(self._root_lay)
            except Exception:
                pass
        try:
            from PyQt6 import sip
            top = self.layout()
            if top is not None:
                try:
                    if not sip.isdeleted(top):
                        # 关键：sip.delete 只删布局对象，不释放其管理的 widget
                        # （widget 的 parent 是 self）。液态玻璃包把 _TitleBar 等放在
                        # outer 顶层布局里，直接 sip.delete 会让这些控件残留（主面板
                        # 切默认后仍挂着液态玻璃标题栏）。先 _clear_layout 递归释放
                        # 全部 widget/嵌套布局，再删布局，杜绝旧 UI 控件残留。
                        self._clear_layout(top)
                        sip.delete(top)
                except Exception:
                    top.setParent(None)
                    top.deleteLater()
        except Exception:
            try:
                if self._root_lay is not None:
                    self._root_lay.setParent(None)
                    self._root_lay.deleteLater()
            except Exception:
                pass
        self._root_lay = None

        # 3.1) 立即处理步骤 3 产生的 DeferredDelete：旧面板控件（大量 HTML 聊天气泡等）
        #     若仍存活，后续应用级 setStyleSheet 全树重算时会对它们逐个 relayout
        #     （HTML sizeHint 解析极慢），是"切换后主面板无响应"的另一诱因。
        try:
            from PyQt6.QtCore import QCoreApplication, QEvent
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        except Exception:
            pass

        # 3.5) 若切到非自定义主题，先清除自定义 UI/UX 残留（玻璃层/透明背景/Acrylic/
        # 圆角蒙版/无边框标志等），避免默认面板仍显示液态玻璃风格
        try:
            _is_custom = (agent_ui_ux.get_active_package()
                          != agent_ui_ux.DEFAULT_PACKAGE)
            agent_ui_ux.cleanup_custom_ui(self, restore_window_flags=not _is_custom)
        except Exception:
            pass

        # 4) 重建全部控件，并重设根样式表（背景渐变/下拉/滚动条随新主题立即生效）
        # 4.0) 先应用包主题色板（幂等，_build_ui 内部会再次调用）并在此刻把应用级全局
        #     QSS 落地：当前面板控件树已清空（仅剩隐藏侧栏），全树重算代价最小；
        #     随后重建产生的新控件会在创建时自动继承该应用级样式 —— 从而避免重建完成后
        #     对整套新控件再做一次 2~3s 级全量重算（"切换后主面板无响应"的根因）。
        try:
            agent_ui_ux.apply_package_theme()
        except Exception:
            pass
        _apply_global_dialog_qss()
        # _build_ui → apply_package_theme 内部会再触发 apply_theme/apply_theme_custom
        # （应用级全局 QSS）：重建中途对半成品控件树重算样式同样昂贵 → 一并挂起，
        # 其过渡色板变化与 4.0 已应用内容一致时将被自动去重。_APP_QSS_DEFER 已声明 global。
        _APP_QSS_DEFER = True
        try:
            # 3.8) 就地重建前释放旧子面板资源：媒体播放器需先 stop/解绑视频输出再销毁，
            # 避免 ffmpeg 后台线程析构时序产生 QFFmpeg::* disconnect 告警；
            # 旧 code/todos/git/worktree 顶层窗口同时关闭，防止重建后残留挂起。
            try:
                _old_code = getattr(self, "code_win", None)
                if _old_code is not None:
                    _shutdown = getattr(_old_code, "shutdown_media", None)
                    if callable(_shutdown):
                        _shutdown()
                    try:
                        _old_code.close()
                    except Exception:
                        pass
                for _old_name in ("todos_win", "git_win", "wt_win"):
                    _old_w = getattr(self, _old_name, None)
                    if _old_w is not None:
                        try:
                            _old_w.close()
                        except Exception:
                            pass
            except Exception:
                pass
            self._build_ui()
        finally:
            _APP_QSS_DEFER = False
        # 重建后清理过渡型临时控件引用：旧转圈动画行/压缩提示行布局已随旧消息流
        # 销毁，悬空引用会让 _place_spinner_bottom/insertLayout 抛 RuntimeError
        # （生成中切换主题报错、气泡渲染中断被清空的根因）。运行中的任务行由
        # 下方 _sync_task_ui 按当前状态重建。
        self._spinner = None
        self._spinner_lbl = None
        self._spinner_row = None
        self._compact_row = None
        self.setStyleSheet(self._panel_root_qss())
        try:
            agent_ui_ux.apply_panel_qss(self)   # 自定义包 panel.qss 覆盖（无则跳过）
        except Exception:
            pass
        # 补套 Acrylic 毛玻璃：setWindowFlags 重建窗口会清除 Acrylic 状态
        # （build_ui 内部已不调用 apply_acrylic，故需在 _retheme 完成后手动补套）
        try:
            if agent_ui_ux.is_custom_package_active():
                agent_ui_ux.apply_acrylic(self)
        except Exception:
            pass
        # 4.1) 应用级 QSS 已在 4.0 落地最终内容（重建期挂起的只是 apply_package_theme 的
        #     过渡色板，其终态与 4.0 一致）→ 清除挂起标记，避免后续调用误判重复重算。
        _APP_QSS_CACHE["pending"] = False
        self._sync_model_combo()
        # 重建期跳过工作树/git 重复刷新（步骤 9 重新显示时统一刷新一次，避免双倍扫描）
        self._retheming = True
        try:
            self._restore_workdir()
        finally:
            self._retheming = False
        self._update_wf_label()
        self._apply_reserve()   # 全屏/最大化时恢复根布局左右预留边距

        # 5) 重新绑定当前会话并全量重渲染（对话内容保留）
        # 清除所有渲染缓存（段 HTML 缓存 + 段内 markdown 块缓存），
        # 确保聊天气泡使用新主题色而非旧颜色缓存；需覆盖 segments / history_segments
        # / rows[].segs / sub_segs 全部段来源。
        self._seg_cache.clear()
        for st in self._sess.values():
            _segs = list(st.get("segments") or []) + list(st.get("history_segments") or [])
            for _r in (st.get("rows") or []):
                _segs += list(_r.get("segs") or [])
            for _ss in (st.get("sub_segs") or {}).values():
                if isinstance(_ss, dict):
                    _segs.append(_ss)
            for seg in _segs:
                if isinstance(seg, dict):
                    seg.pop("_rd_cache", None)
        self._bind_sess(sid)
        self._render_history_all()
        self._refresh_session_combo()
        self._update_queue_bar()
        self._update_welcome()
        # 任务进行中切主题：恢复打字指示器与发送/停止按钮（重建把转圈行等
        # 临时控件清掉了，不恢复会表现为"任务在跑却无指示器、按钮为灰色"）
        try:
            self._sync_task_ui()
        except Exception:
            pass

        # 6) 恢复输入框内容与光标位置
        if input_text:
            try:
                self.input.setPlainText(input_text)
                c = self.input.textCursor()
                c.setPosition(min(input_cursor, len(input_text)))
                self.input.setTextCursor(c)
            except Exception:
                pass

        # 7) 恢复待发送附件预览（图片 data URL 无法还原缩略图，仅文件重建）
        for p in pending_files:
            try:
                self._attach_thumb(self._file_thumb(p), p,
                                   name=os.path.basename(p), remove=("file", p))
            except Exception:
                pass
        if pending_images:
            self._attach_bar.setVisible(True)

        # 8) 恢复面板几何/窗口状态（避免切换主题时面板偏移）
        try:
            maximized = bool(win_state & Qt.WindowState.WindowMaximized)
            fullscreen = bool(win_state & Qt.WindowState.WindowFullScreen)
            if fullscreen:
                self.showFullScreen()
            elif maximized:
                self.showMaximized()
            else:
                self.setGeometry(geo)
        except Exception:
            try:
                self.setGeometry(geo)
            except Exception:
                pass

        # 8.1) 兜底显示：cleanup_custom_ui 还原窗口标志时 setWindowFlags 会把窗口
        #     隐藏（Qt 行为），重建后若不重新 show 主面板会"消失"（常见于切换到
        #     非自定义主题/UI/UX 包）。此处统一补显，保证切换后主面板始终可见。
        try:
            if not self.isVisible():
                self.show()
                self.raise_()
                self.activateWindow()
        except Exception:
            pass

        # 9) 重新停靠子窗口（按 工作树→git→todos 自上而下堆叠，再右侧重置代码预览）
        #    顺序不能乱：后一个面板要读取前一个的 frameGeometry 决定垂直位置
        self._sync_wt_win()
        self._sync_git_win()
        self._sync_todos_win()
        self._sync_code_win()
        # 9.1) 恢复切换前的子窗口原位（保持左侧停靠位置，避免偏移到右侧与预览重叠）
        if side_pos:
            for name, g in side_pos.items():
                w = getattr(self, name, None)
                if w is not None:
                    try:
                        w.move(g[0], g[1])
                    except Exception:
                        pass
        # 子面板重建后显式补圆角蒙版：showEvent/QTimer 的延迟可能在 _retheme 期间失效，
        # 显式调用确保每个子面板四角剪成圆角。
        for _name in ("todos_win", "git_win", "wt_win", "code_win"):
            _w = getattr(self, _name, None)
            if _w is not None and hasattr(_w, "_apply_window_round"):
                try:
                    _w._apply_window_round()
                except Exception:
                    pass

        # 10) 恢复滚动位置（延迟到布局稳定后，避免跳到底部/顶部）
        try:
            bar = self.msg_area.verticalScrollBar()
            if was_at_bottom:
                QTimer.singleShot(0, self._do_scroll_bottom)
                QTimer.singleShot(150, self._do_scroll_bottom)
            elif scroll_val > 0:
                QTimer.singleShot(0, lambda: bar.setValue(min(scroll_val, bar.maximum())))
                QTimer.singleShot(150, lambda: bar.setValue(min(scroll_val, bar.maximum())))
        except Exception:
            pass

        # 11) 应用级 QToolTip 样式随主题刷新（液态玻璃：实心浅底，绝非纯黑）。
        #     全局 QToolTip 规则已由 _global_dialog_qss 承载（主题自适应），并随上面的
        #     apply_theme() 缓存了最新内容；此处不再重复 app.setStyleSheet（避免对重建后
        #     的整棵控件树再做一次全量重算），仅补 Windows 原生 Tooltip 的调色板双保险。
        app = QApplication.instance()
        if app is not None:
            try:
                from PyQt6.QtGui import QColor, QPalette
                from PyQt6.QtWidgets import QToolTip
                if agent_ui_ux.is_custom_package_active():
                    _tip_rgb, _txt_rgb = (247, 249, 252), (11, 15, 20)
                else:
                    _c1, _c2 = QColor(PANEL), QColor(TEXT)
                    _tip_rgb, _txt_rgb = (_c1.red(), _c1.green(), _c1.blue()), \
                                         (_c2.red(), _c2.green(), _c2.blue())
                _tp = QToolTip.palette()
                _tp.setColor(QPalette.ColorRole.ToolTipBase, QColor(*_tip_rgb))
                _tp.setColor(QPalette.ColorRole.ToolTipText, QColor(*_txt_rgb))
                _tp.setColor(QPalette.ColorRole.Window, QColor(*_tip_rgb))
                _tp.setColor(QPalette.ColorRole.Text, QColor(*_txt_rgb))
                QToolTip.setPalette(_tp)
            except Exception:
                pass
            # 全局 QSS 已在步骤 4.0 提前落地（内容未变则跳过重设），此处无需再调用

        # 12) 标题栏随主题刷新（showEvent 只在首次显示触发，需就地重设）
        try:
            if _resolve_theme() == "dark":
                _dark_titlebar(self)
            else:
                _light_titlebar(self)
        except Exception:
            pass

        # 清除挂起标记（旧逻辑兼容）
        self._agent_theme_pending = False
        # 13) 同步 zhuzhu Copilot 浮层主题（若浮层已创建，按当前主题就地重刷）
        try:
            _cp = self.__dict__.get("_copilot_panel")
            if _cp is not None:
                QTimer.singleShot(0, _cp._ret_theme)
        except Exception:
            pass

    def _apply_pending_theme(self):
        """任务结束后应用挂起的主题切换：任务运行期间选择浅色/深色时,若直接重建面板
        会关闭旧面板（closeEvent 中 eng.stop()）导致任务中断,故挂起待任务结束再重建。"""
        if not getattr(self, "_agent_theme_pending", False):
            return
        self._agent_theme_pending = False
        try:
            self._retheme()
        except Exception:
            pass

    def _refresh_meta(self):
        """轮询刷新 tokens / 缓存命中率 / 按钮反馈状态 / 卡死兜底"""
        # auto 主题跨时间点（8:00/20:00）自动换色：面板主体样式只随 _retheme 更新
        # （系统标题栏/对话框边框随 showEvent 即时变化，二者会脱节，表现为"边框已变色、
        # 主体要重启才生效"）。此处轮询检测实际主题漂移：空闲就地重建；任务/评估运行中
        # 挂起（复用 _apply_pending_theme，任务结束后重建），避免中断在途任务。
        if _theme_setting() == "auto" \
                and not agent_ui_ux.is_custom_package_active() \
                and _APPLIED_THEME:
            _want = _resolve_theme()
            if _want != _APPLIED_THEME:
                _busy = bool(self._task_active or self._eval_pending)
                _st = self._sess.get(self._session_id) or {}
                _eng = _st.get("engine")
                if _eng is not None and getattr(_eng, "_thread", None) \
                        and _eng._thread.is_alive():
                    _busy = True
                if _busy:
                    self._agent_theme_pending = True
                else:
                    try:
                        self._retheme()
                    except Exception:
                        pass
        # 读取当前会话真实引擎（口径见 _active_engine：优先 st["engine"]，
        # 回退 self._engine，避免会话切换/后台任务时引用漂移导致计数不更新）
        eng = self._active_engine()
        if eng:
            t = eng.tokens
            used = t['prompt'] + t['completion']
            # 缓存命中率 = cache_hit / (cache_hit + cache_miss)，显示在 token 统计左侧
            _hit, _miss = int(t.get('cache_hit', 0)), int(t.get('cache_miss', 0))
            _tot = _hit + _miss
            _rate_txt = f"缓存命中率 {_hit * 100.0 / _tot:.0f}% · " if _tot > 0 else ""
            _meta_txt = (f"{_rate_txt}已用 {used} tokens"
                         f"（输入 {t['prompt']} / 输出 {t['completion']}）")
            # 400ms 轮询常驻执行：文本未变化时跳过 setText，
            # 避免每次都触发 QLabel 重排版/重绘（token 不变时高频空转的主要来源）
            if self.token_label.text() != _meta_txt:
                self.token_label.setText(_meta_txt)
        # 统计浮层可见时同步刷新（不可见时本方法立即返回，无额外开销）
        self._refresh_token_pop()
        # 评估阶段（引擎线程未启动）同样视为任务进行中，避免误清理禁用停止按钮；
        # @子Agent 直接调用（无引擎线程）也计入运行态，防止被误收尾
        running = bool(self._eval_pending is not None
                       or (eng and eng._thread
                           and eng._thread.is_alive())
                       or self._subagent_running())
        # 评估卡死看门狗：难度评估线程异常未 emit 导致 _eval_pending 悬挂，
        # 会令按钮无限转圈 → 超时后强制清理并复位为空闲发送态。
        if (self._eval_pending is not None and self._eval_pending[0] == self._session_id
                and self._eval_pending_at and time.time() - self._eval_pending_at > 60):
            self._eval_pending = None
            self._eval_pending_at = 0.0
            self._task_active = False
            self._hide_spinner()
            self._set_action_idle()
            self._notify_blocked("任务难度评估超时，已取消本次任务（可重新发送）")
            running = False
        if not running and eng is None and self.token_label.text() != "0 tk":
            # 新对话/无引擎会话：轮询兜底复位计数（_new_session 已即时复位，
            # 覆盖其他进入无引擎会话的路径，避免残留旧对话的 token 统计）
            self.token_label.setText("0 tk")
        # 下载任务实时进度：仅当前会话有任务时渲染进其气泡（避免后台会话下载进度串台）
        if self._task_active:
            self._sync_download_progress()
        # 任务结束即清理：只要任务标志开启且线程已退出，就执行收尾
        # （不依赖 spinner/按钮状态判断，避免切换模式等路径下漏清理）
        if not running and self._task_active:
            self._task_active = False
            self._task_auto_ids.discard(self._session_id)   # 本次任务内批量授权任务结束即失效
            self._hide_spinner()
            self._set_action_idle()   # 融合按钮恢复空闲发送状态
            if not self._end_badge_shown:
                self._end_badge_shown = True
                self._show_end_badge()
                self._notify_task_end()   # 右下角通知：任务完成/已停止/出错
            # 流式正文结束：把最后一段 text 的 streaming 关闭，触发一次完整
            # markdown 渲染补全格式（流式期间仅轻量纯文本显示）
            if self._segments and self._segments[-1].get("streaming"):
                self._segments[-1]["streaming"] = False
                self._refresh_ai_html()
            # AI 生成完成后标记最后一条回合，悬停时在右下角显示重试按钮；
            # 同时冻结耗时徽章并填回合时间行，过程区按 demo 收起为「查看执行过程」
            self._finalize_turn()
            self._arm_retry_button()
            self._persist_current()   # 任务结束即持久化当前会话（重启可恢复）
            self._flush_queue(self._session_id)   # 本轮完成 → 自动发送排队消息
            self._scroll_bottom()   # 结束执行时自动滚动到最下方
            self._apply_pending_theme()   # 任务结束：若期间切换了主题则重建面板应用
            # UI/UX 包切换/更新：任务结束后重建面板（热插拔）
            if getattr(self, "_uiux_rebuild_pending", False):
                self._uiux_rebuild_pending = False
                try:
                    self._retheme()
                except Exception:
                    pass
            # HIGH-A 修复：任务真正结束后再应用"跨工作流切换/use_workflow_agent"的延迟重建
            # （避免中途替换造成同会话双引擎并发）。先切回目标工作流再在空闲态重建。
            if self._session_id in self._pending_rebuild:
                _pwf = self._pending_rebuild.pop(self._session_id)
                # 重建前先校验引擎是否已是最新工作流（可能已被 _rebuild_engine 更新过）
                _st = self._sess.get(self._session_id)
                _cur_wf = (_st.get("workflow") if _st else None) or agent_workflow.active_workflow()
                _target_wf = _pwf if _pwf else agent_workflow.DEFAULT_WORKFLOW
                if _cur_wf != _target_wf:
                    self._rebuild_engine(self._session_id, display_wf=_pwf)
                else:
                    # 工作流已经正确，只需确保引擎引用正确（边缘情况：_pending_rebuild 残留）
                    _eng = _st.get("engine") if _st else None
                    if _eng is not None:
                        self._engine = _eng
        # 残留停止态兜底复位：无任何活动任务（_task_active 已 False）但按钮仍处于
        # 「停止中」或 _user_stopped 残留 → 强制恢复空闲发送态，杜绝“无任务却显示暂停”。
        elif (not running and not self._task_active
              and (self._user_stopped
                   or getattr(self.action_btn, "toolTip", lambda: "")() == "停止中…")):
            self._user_stopped = False
            self._hide_spinner()
            self._set_action_idle()

    def _sync_download_progress(self):
        """轮询活跃下载任务，把进度条实时渲染进 AI 气泡（完成后保留最终状态）"""
        task = agent_tools.get_active_download()
        if task is None:
            return
        try:
            snap = task.snapshot()
        except Exception:
            agent_tools.clear_active_download(task)   # 仅清理刚读取的该任务，避免误清并发新下载
            return
        status = snap.get("status") or ""
        total = snap.get("total") or 0
        done = snap.get("done") or 0
        pct = int(done * 100 / total) if total else 0
        name = snap.get("filename") or "下载中"
        self._ensure_ai_bubble()
        seg = next((s for s in reversed(self._segments)
                    if s.get("type") == "progress"), None)
        if seg is None:
            seg = {"type": "progress", "pct": 0, "text": ""}
            self._segments.append(seg)
        seg["pct"] = pct
        seg["text"] = (f"{name} · {self._fmt_bytes(done)}/{self._fmt_bytes(total)}"
                       f" · {status}")
        self._refresh_ai_html()
        if status in ("done", "error", "canceled"):
            agent_tools.clear_active_download(task)   # 结束：保留最终进度条，仅清理该任务不再轮询
        self._scroll_bottom()

    @staticmethod
    def _fmt_bytes(n) -> str:
        """字节数人性化显示（B/KB/MB/GB）"""
        try:
            n = max(0, int(n or 0))
        except (TypeError, ValueError):
            n = 0
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if n < 1024:
                return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
            n /= 1024
        return f"{n:.1f} PB"

    def _collapse_process_blocks(self, segs=None):
        """任务结束：把本轮回复里的子 Agent 过程块折叠为一行（正文/提问等不受影响）。

        子块由多步工具输出拼成，展开态单块可达近百 KB 富文本；长对话若一直展开，
        QLabel 富文本每次布局需数百毫秒 → 滚动/缩放/输入全卡（长上下文卡顿主因）。
        与「命令结果先输出再折叠」「思考完成后折叠」保持一致的交互习惯，可点击展开。"""
        changed = False
        for seg in (segs if segs is not None else self._segments):
            if seg.get("type") == "sub" and not seg.get("collapsed"):
                seg["collapsed"] = True
                changed = True
        if changed:
            self._refresh_ai_html()

    def _show_end_badge(self):
        """任务结束后在 AI 气泡外显示结果徽章（+ 本轮文件变更徽章）。
        变更徽章仅主引擎任务轮展示：@子Agent 直呼不经引擎 run()，其 _last_edit_delta
        为上一轮残留，子 Agent 的文件改动会在父引擎任务轮结束时一并统计展示。"""
        # 任务结束：先把落字节奏补齐（停止/出错时正文可能还差几个字没落下来）
        self._reveal_flush()
        # 任务结束：过程块折叠（长上下文体积护栏，见 _collapse_process_blocks）
        self._collapse_process_blocks()
        # 任务结束：清除本次任务内「确认去重缓存」，下次任务重新弹窗
        try:
            _st = self._sess.get(self._session_id) or {}
            _st.pop("confirm_cache", None)
            _st.pop("pending_confirm", None)
        except Exception:
            pass
        if getattr(self, "_subagent_mode", False):
            # @子Agent 直接调用：无引擎 end_state，按停止/完成/异常显示徽章
            self._subagent_mode = False
            if self._user_stopped or not self._subagent_ok:
                self._add_badge("Stop by user", WARN)
            elif str(self._subagent_result or "").startswith("子 Agent"):
                self._add_badge("Error", ERR)
            else:
                self._add_badge("Successfully", OK)
            return
        state = getattr(self._engine, "end_state", "") if self._engine else ""
        if self._user_stopped or state == "stopped":
            self._add_badge("Stop by user", WARN)
        elif state == "done":
            self._add_badge("Successfully", OK)
        else:   # error 视为异常
            self._add_badge("Error", ERR)
        delta = getattr(self._engine, "_last_edit_delta", None) if self._engine else None
        self._add_change_badge(delta)   # 气泡末尾外侧：-N 删除行(红) / +M 新增行(绿)

    # ---------- 引擎回调（信号槽，主线程） ----------
    def _ensure_text_segment(self):
        """正文段：末尾不是 text 段则新建，否则复用（操作与正文交织）"""
        if not self._segments or self._segments[-1]["type"] != "text":
            self._segments.append({"type": "text", "raw": ""})

    def _stop_send_spin(self):
        """AI 开始响应/执行后停止转圈（按钮保持可点击停止状态，不再一直转圈误导）"""
        if self._action_anim.isActive():
            self._action_anim.stop()
            self.action_btn.setIcon(_line_icon("stop", 16, "#FFFFFF"))
            self.action_btn.setToolTip("停止当前任务")

    def _on_delta(self, s: str):
        self._finish_thinking()   # 开始输出正文即视为思考完成
        # 正文回复状态：转圈行同步切换为「正在回复正文」（覆盖刚设置的「已思考 N 秒」）。
        # _set_spinner_text 内部有「文案未变则不 setText」保护，高频 token 下开销可忽略。
        self._set_spinner_text(_OP_STATUS_REPLY)
        self._stop_send_spin()
        self._last_activity = time.time()
        self._ensure_ai_bubble()
        self._ensure_text_segment()
        self._segments[-1]["raw"] += s
        self._segments[-1]["streaming"] = True   # 流式期间轻量渲染
        # 不直接渲染：交给落字节拍按速度推进显示长度（模型一次可能吐几十到上百字）
        self._reveal_note(self._segments[-1])
        self._scroll_bottom()

    def _on_result(self, name: str, text: str, images: list = None):
        """工具执行完成：输出文本与截图一并渲染进 AI 气泡（截图以缩略图独立成块，不挤压）。
        命令执行结果先输出（默认展开可见），短暂展示后自动折叠为一行，可点击展开/收起。"""
        self._stop_send_spin()
        self._last_activity = time.time()
        self._ensure_ai_bubble()
        # AI 使用任务清单工具时，同步独立 todos 窗口（常显，实时刷新进度）
        if name in ("update_todo", "list_todo"):
            self._call_panel("todos_win", "update_todos", agent_tools.load_todos())
        if name == "ask_user":
            # ask_user 工具：把 AI 提问直接拼接到该次工具执行的「执行结果」内容前面
            # （沿用灰色小字样式），不作为正文主输出；随后落入公共 result 渲染
            body = (str(text) or "").strip()
            if body.startswith("[该问题此前已询问过"):
                # 引擎去重回包（同问题已答，未再弹窗）：原文直出，不套「提问+回答」模板
                text = body
            else:
                st = self._sess.get(self._session_id) or {}
                q = str(st.get("last_ask_q") or "").strip()
                st["last_ask_q"] = ""          # 用后即清，防后续工具误带
                text = (f"AI 提问：{q}\n\n你的回答：{body}" if q
                        else (f"你的回答：{body}" if body else ""))
            if not (text or "").strip():
                return
        # 命令工具：AI 输入的命令全文直接拼接到输出结果前面一并展示（不单独成卡）
        cmd = ""
        if name == "run_command":
            st_c = self._sess.get(self._session_id) or {}
            cmd = str(st_c.get("last_cmd") or "").strip()
            st_c["last_cmd"] = ""          # 用后即清，防后续工具误带
        shown = (text or "").strip()
        if shown:
            if len(shown) > 20000:
                shown = shown[:20000] + " …（输出过长已截断显示，完整内容已返回模型）"
            shown = _esc(shown).replace("\n", "<br/>")
            # 输出挂载：结果就地续写到对应工具行的 op 段（同一区块内「工具在上、输出在下」），
            # 不再另起「执行结果」区块；找不到对应工具行时才退回独立 result 段。
            # 无输出的工具不落任何区块（调用行本身已在视图中）。
            if not self._attach_out_seg(self._segments, name, shown, cmd):
                seg = {"type": "result", "html": shown, "collapsed": False, "cmd": cmd}
                self._segments.append(seg)
        # 截图段（AI 主动截图：browser_snapshot 等工具返回的页面截图）渲染进主对话气泡。
        # 限制单气泡截图数量：data URL 原图解码后常驻内存，保留最近 5 张避免长会话膨胀
        for u in images or []:
            if len(self._segments) >= 5 and \
               sum(1 for s in self._segments if s.get("type") == "image") >= 5:
                break
            self._segments.append({"type": "image", "url": u, "caption": "已截屏"})
        self._refresh_ai_html()
        self._scroll_bottom()

    # 输出挂载的搜索窗口：工具/命令的输出必然紧跟其 op 段，只在尾部有限范围内回看，
    # 避免长会话里每次工具结束都做一次全表扫描。
    _OUT_SCAN = 16

    @staticmethod
    def _attach_out_seg(segs: list, name: str, html: str, cmd: str = "") -> bool:
        """把工具/命令的输出**就地续写**到对应工具行的 op 段（不新增段/区块）。

        「AI 每调用一个工具/执行命令，输出结果排列到对应气泡下方」的段层实现：op 段
        同时承载调用信息与输出，`_seg_block` 据此把它渲染成一个整体（工具行在上、
        输出紧贴其下，由块内间距隔开）。定位规则是按工具名匹配的**最早一个尚未带
        输出**的 op 段 —— 同一轮里同名工具被多次调用时，输出按执行顺序各归其位。

        后台会话与前台共用（segs 由调用方给），保证切回前台看到的结构完全一致。
        返回 False 表示没找到对应工具行（历史数据 / 无状态事件）→ 调用方落回独立段。
        """
        if not name or not html or name == "ask_user":
            # ask_user 的输出是「提问 + 回答」富文本卡片、自带排版，不并入工具输出区
            return False
        for seg in segs[-AgentPanel._OUT_SCAN:]:
            if seg.get("type") != "op" or seg.get("out"):
                continue
            if str(seg.get("name") or "") != name:
                continue
            seg["out"] = html
            if cmd and not seg.get("cmd"):
                seg["cmd"] = cmd
            return True
        return False

    def _remember_cmd_seg(self, segs: list, args_json: str) -> bool:
        """工具执行确认时把命令全文写进对应 op 段：该行立刻呈现为命令块（`$ 命令`），
        随后的输出就地续写为同一块的输出区 —— 区块类型全程不变，不会中途重建闪烁。
        """
        cmd = _cmd_text_from_args(args_json)
        if not cmd:
            return False
        for seg in reversed(segs[-self._OUT_SCAN:]):
            if seg.get("type") == "op" and not seg.get("cmd") and not seg.get("out"):
                seg["cmd"] = cmd
                return True
        return False

    def _on_sub_event(self, kind: str, idx: int, title: str, text: str,
                      agent_id: str = ""):
        """子 Agent 事件（与主 Agent 共用同一聊天气泡）：
        start=创建子块；delta=流式输出追加到对应子块；
        tool=工具/命令调用步骤；output=工具执行结果（命令输出）。
        多个子任务并发时按 task_idx 定位各自子块，互不覆盖。

        agent_id 为该子 Agent 的控制注册 id（`sub:<名>` / `wf:<工作流>`）：记进子块，
        子块上的「暂停/恢复」链接直接拿它命中 AgentControl —— 不能再靠标题反查，
        标题是自然语言任务描述，反查必然失败（用户反馈「点击无法暂停/恢复」）。
        """
        self._stop_send_spin()
        self._last_activity = time.time()
        if kind == "start":
            self._ensure_ai_bubble()
            # collapsed=False：任务进行中子块展开可见（与"命令结果先输出再折叠"一致）；
            # 任务结束时由 _collapse_process_blocks 统一折叠为一行，避免历史气泡过大。
            seg = {"type": "sub", "title": title, "raw": "", "steps": [],
                   "collapsed": False, "agent_id": agent_id}
            self._sub_segs[idx] = seg
            self._segments.append(seg)
            self._refresh_ai_html()
            self._scroll_bottom()
            return
        seg = self._sub_segs.get(idx)
        if seg is None:
            # 兜底：段列表被重建（历史加载/会话重置）导致索引失效时，
            # 定位最后一个同标题 sub 段；仍找不到则新建
            for s in reversed(self._segments):
                if s.get("type") == "sub" and s.get("title") == title:
                    seg = s
                    break
            if seg is None:
                self._ensure_ai_bubble()
                seg = {"type": "sub", "title": title, "raw": "", "steps": [],
                       "collapsed": False, "agent_id": agent_id}
                self._segments.append(seg)
            self._sub_segs[idx] = seg
        if agent_id and not seg.get("agent_id"):
            seg["agent_id"] = agent_id   # 兜底建的段补上控制 id（老路径可能没带）
        if kind == "delta":
            seg["raw"] += text
        elif kind == "tool":
            seg.setdefault("steps", []).append({"kind": "tool", "text": text})
        elif kind == "output":
            seg.setdefault("steps", []).append({"kind": "output", "text": text})
        self._refresh_ai_html()
        self._scroll_bottom()

    def _plugin_of_tool(self, name: str, sid: str = None) -> str:
        """该工具是否由插件提供 → 插件名（内置工具/普通 MCP 工具返回空串）。

        插件提供的 MCP 工具名由插件自己决定，无法静态映射，只能走引擎运行期的
        「工具 → MCP 服务器名（{插件名}-mcp）→ 插件」链路（引擎持有 MCP 管理器）。
        sid：目标会话（后台会话的事件也要按它自己的引擎判定归属）。
        """
        try:
            st = self._sess.get(sid or self._session_id) or {}
            getter = getattr(st.get("engine"), "plugin_of_tool", None)
            if callable(getter):
                return getter(name) or ""
        except Exception:
            pass
        return ""

    @staticmethod
    def _plugin_tip(plugin: str, subject: str) -> str:
        """插件来源行的图标气泡提示：插件名 + 用途说明 + 来源对象"""
        try:
            desc = str(agent_plugins.get_plugin(plugin).get("description") or "").strip()
        except Exception:
            desc = ""
        return f"插件「{plugin}」提供的{subject}" + (f"：{desc}" if desc else "")

    def _tool_op_tip(self, name: str, sid: str = None) -> tuple:
        """工具调用行 → (图标气泡提示, 图标 kind)。插件提供的工具换插件专属矢量图。

        提示只是锦上添花：任何解析异常都退化为「无提示」，绝不能因此中断状态渲染。
        """
        try:
            plugin = self._plugin_of_tool(name, sid)
            if plugin:
                return self._plugin_tip(plugin, "工具"), "plugin"
            return self._cmd_desc("/" + str(name).lower()), None
        except Exception:
            return "", None

    def _skill_op_seg(self, title: str, name: str, html: str, sid: str = None) -> dict:
        """技能调用行 seg：技能由插件登记（插件目录名即技能名）时换插件矢量图与插件提示。"""
        seg = {"type": "op", "name": title, "meta": name, "ico": "skill", "html": html}
        try:
            plugin = agent_plugins.plugin_of_skill(name)
            if plugin:
                seg["ico"] = "plugin"
                seg["tip"] = self._plugin_tip(plugin, f"技能 {name}")
            else:
                seg["tip"] = self._cmd_desc("/" + str(name).lower())
        except Exception:
            pass
        return seg

    def _on_status(self, s: str):
        self._stop_send_spin()
        self._last_activity = time.time()
        if s == "正在思考…":
            self._start_think()
        elif s.startswith("待执行工具:"):
            self._finish_thinking()   # 本轮思考结束（转入工具执行），折叠本轮思考
            name = s.split(":", 1)[1].strip()
            # 立即同步转圈行文案：预处理阶段（技能拦截/参数校验/用户确认弹窗）
            # 可能耗时，若不在此处更新，转圈行会停在「已思考 N 秒」造成状态不同步。
            # 同一轮多个工具时文案会依次经过各工具，随后被「正在并行执行」覆盖。
            self._set_spinner_text(_op_status_text(name))
            self._ensure_ai_bubble()
            tip, ico = self._tool_op_tip(name)
            seg = {"type": "op", "html": f"▎{_esc(name)}", "name": name, "tip": tip}
            if ico:
                seg["ico"] = ico
            self._segments.append(seg)
            self._refresh_ai_html()
            self._scroll_bottom()
        elif s.startswith("正在执行:"):
            name = s.split(":", 1)[1].strip()
            # 打字指示器文案 = 工具名映射的进行时状态（如「正在执行命令」），
            # 无映射回退「正在调用工具 <名>」；同步更新转圈行小字
            self._set_spinner_text(_op_status_text(name))
            self._ensure_ai_bubble()
            if self._segments and self._segments[-1]["type"] == "op":
                self._segments[-1]["html"] = f"▎{_esc(name)} …"
            else:
                tip, ico = self._tool_op_tip(name)
                seg = {"type": "op", "html": f"▎{_esc(name)} …", "name": name, "tip": tip}
                if ico:
                    seg["ico"] = ico
                self._segments.append(seg)
            self._refresh_ai_html()
            self._scroll_bottom()
        elif s.startswith("正在并行执行"):   # 并发编辑批量状态（引擎侧已带工具名清单）
            self._set_spinner_text(s)
            self._ensure_ai_bubble()
            self._segments.append({"type": "op", "html": f"▎{_esc(s)}",
                                   "name": s, "ico": "parallel"})
            self._refresh_ai_html()
            self._scroll_bottom()
        elif s.startswith("正在调用技能:"):
            self._finish_thinking()   # 本轮思考结束（转入技能调用），折叠本轮思考
            name = s.split(":", 1)[1].strip()
            self._set_spinner_text(f"正在调用技能 {name}")
            self._ensure_ai_bubble()
            # 标题固定「调用技能」，技能名放入 meta（不再拼进标题）；ico 稳定为 skill/plugin，
            # 使该行获得专属图标，并作为同一技能行原地更新的识别标记。
            seg = self._skill_op_seg("调用技能", name, f"▎调用技能 {_esc(name)}")
            if self._segments and self._segments[-1].get("ico") in ("skill", "plugin"):
                self._segments[-1].update(seg)
            else:
                self._segments.append(seg)
            self._refresh_ai_html()
            self._scroll_bottom()
        elif s.startswith("技能已调用:"):
            name = s.split(":", 1)[1].strip()
            self._ensure_ai_bubble()
            seg = self._skill_op_seg("已调用技能", name, f"✓ 已调用技能 {_esc(name)}")
            if self._segments and self._segments[-1].get("ico") in ("skill", "plugin"):
                self._segments[-1].update(seg)
            else:
                self._segments.append(seg)
            self._refresh_ai_html()
            self._scroll_bottom()
        elif s.startswith(("正在调用插件:", "插件已调用:")):
            done = s.startswith("插件已调用:")
            name = s.split(":", 1)[1].strip()
            self._finish_thinking()
            self._set_spinner_text(
                ("已调用插件 " if done else "正在调用插件 ") + name)
            self._ensure_ai_bubble()
            # 插件调用独占一行并带插件专属矢量图：与技能行（拼图图标）区分开，
            # 图标气泡提示给出插件名与用途，便于用户确认「是哪个插件在干活」。
            seg = {"type": "op", "name": "已调用插件" if done else "调用插件",
                   "meta": name, "ico": "plugin", "tip": self._plugin_tip(name, "能力"),
                   "html": ("✓ 已调用插件 " if done else "▎调用插件 ") + _esc(name)}
            if self._segments and self._segments[-1].get("ico") == "plugin":
                self._segments[-1].update(seg)
            else:
                self._segments.append(seg)
            self._refresh_ai_html()
            self._scroll_bottom()
        elif s == "完成":
            self._hide_spinner()   # 任务结束，停掉转圈
        elif s.startswith("错误"):
            self._hide_spinner()
            self._ensure_ai_bubble()
            # 完整报错（含多行诊断/上游响应体）原样输出至对话页，换行正确显示
            self._segments.append({"type": "mark",
                                   "html": _esc(s).replace("\n", "<br/>")})
            self._refresh_ai_html()
            self._scroll_bottom()
        elif s == "已停止" or "已停止" in s:
            self._hide_spinner()   # 用户手动停止/包含“已停止”字样的状态均不输出小字

    def _set_spinner_text(self, text: str):
        """更新转圈行的状态文字（打字指示器文案）；转圈行未创建则先创建"""
        if self._spinner_lbl is not None:
            if self._spinner_lbl.text() != text:
                self._spinner_lbl.setText(text)
        else:
            self._ensure_spinner()
            if self._spinner_lbl is not None:
                self._spinner_lbl.setText(text)

    # ---------- 每步确认 / ask_user 提问（engine 线程调用 → 信号 → 主线程） ----------
    # 前台会话：直接弹窗；后台会话：不弹窗打扰，挂起等待并置「待确认/待回答」标记，
    # 在其他对话优雅通知提醒，用户切到该会话后处理。
    def _confirm_tool(self, sid: str, name: str, args: dict) -> bool:
        st = self._sess.get(sid) or {}
        # 工具执行前即把 AI 输入的命令全文陈列进对话流（仅 run_command；其余工具仍以
        # status 的工具名步骤展示）。engine 的 confirm 钩子在每个工具执行前同步调用
        # （ask_user 除外），此处从 worker 线程 emit 到主线程处理，不直接触碰 UI 对象；
        # 免确认直行（白名单/yolo/edit）同样先走到这里，确保命令输入始终可见。
        if name == "run_command":
            try:
                self.evt_signal.emit(
                    sid, "tool_input", (name, json.dumps(args, ensure_ascii=False)))
            except Exception:
                pass
        # 先做工具级安全评估，再判断白名单：白名单只对「整体评估为 safe」的命令免确认
        # 放行。含危险词/管道/重定向/多命令的命令即使命中白名单首词（如填 `python`），
        # 也会先被 assess_command 判为 risky/dangerous，从而落回确认流程而非直接执行。
        level, reason = agent_sandbox.assess_tool(name, args)
        # 自定义白名单命令：仅当无危险操作时直接放行，不再弹确认框
        # （空格+回车加入白名单后，AI 再次执行同一条安全命令即可免确认直行）
        if (name == "run_command" and level == "safe"
                and agent_sandbox.cmd_in_custom_safe(str(args.get("command") or ""))):
            return True
        if self._mode == "yolo":
            # YOLO（真正意义上的直行模式）：完全放行所有操作——任意目录、删除系统关键
            # 目录、执行任意命令一律不再拦截（dangerous 同样授权），由 execute_tool 以
            # allow_dangerous=True 执行。文档/弹窗文案同步声明此为"去约束"模式。
            return True
        if self._mode == "edit":
            if name != "run_command" or level == "safe":
                return True
        # 本次任务内已由用户选择"全部允许"：非危险操作直接放行（危险仍硬拒，需显式授权）
        if level != "dangerous" and sid in getattr(self, "_task_auto_ids", set()):
            return True
        # 防重复弹窗：本次任务内已确认过的「工具 + 参数」直接复用上次结果，
        # 不再重复弹出确认窗（任务结束清理，见 _show_end_badge）
        key = (name, json.dumps(args, sort_keys=True, ensure_ascii=False))
        try:
            cache = st.setdefault("confirm_cache", {})
            if key in cache:
                return cache[key]
        except Exception:
            pass
        if sid != self._session_id:
            # 后台会话：挂起该确认，置待处理标记，优雅通知，不弹窗打扰当前对话
            evt = threading.Event()
            st["pending_confirm"] = {"name": name, "args": args, "risk": level,
                                     "answered": False}
            st["confirm_evt"] = evt
            st["confirm_result"] = False
            self.evt_signal.emit(sid, "pending",
                                 (f"对话「{self._session_label(sid)}」有命令待确认：{name}",
                                  "切换到该对话即可继续"))
            # 无限等待：不设 600s 超时误判拒绝。用户切到该会话经 _flush_pending
            # 应答后 set 唤醒；停止任务/切换会话同样唤醒（见 _stop/_select_session）
            evt.wait()
            result = st.get("confirm_result", False)
            try:
                st.setdefault("confirm_cache", {})[key] = result
            except Exception:
                pass
            return result
        # 前台会话：直接弹窗确认（弹窗无限等待用户作答，不自动超时）
        self._confirm_evt.clear()
        self.confirm_signal.emit(sid, name, json.dumps(args, ensure_ascii=False), level)
        self._confirm_evt.wait()
        try:
            st.setdefault("confirm_cache", {})[key] = self._confirm_result
        except Exception:
            pass
        return self._confirm_result

    def _on_confirm(self, sid: str, name: str, args_json: str, risk: str):
        try:
            args = json.loads(args_json)
        except json.JSONDecodeError:
            args = {}
        # 供确认弹窗"加入白名单"使用：记住本次 run_command 的命令
        _ConfirmDialog._current_cmd = (str(args.get("command") or "").strip()
                                       if name == "run_command" else "")
        # 需要确认：右下角通知（适用于所有对话，含前台）
        self._toast("AI 请求确认", f"对话「{self._session_label(sid)}」需确认：{name}", True)
        dlg = _ConfirmDialog(name, args, risk, self)
        agent_ui_ux.glassify_dialog(dlg, tint=0x30FFFFFF, frost_alpha=40)
        dlg.exec()
        self._confirm_result = dlg.result_ok
        # 本次任务内"全部允许"：记录会话，后续非危险操作直接放行（任务结束自动失效）
        if getattr(dlg, "bulk_allow", False):
            self._task_auto_ids.add(sid)
        # "拒绝并停止任务"：置停止标记并停引擎，终止在途任务
        if getattr(dlg, "stop_requested", False):
            self._user_stopped = True
            eng = (self._sess.get(sid) or {}).get("engine") or getattr(self, "_engine", None)
            if eng is not None:
                try:
                    eng.stop()
                except Exception:
                    pass
        self._confirm_evt.set()

    def _ask_user_tool(self, sid: str, args: dict) -> str:
        st = self._sess.get(sid) or {}
        # 记录本次提问（供 ask_user 工具输出渲染时把提问拼到用户回答前面，用后即清）
        st["last_ask_q"] = str(args.get("question") or "") if isinstance(args, dict) else ""
        if sid != self._session_id:
            # 后台会话：挂起提问，置待处理标记，优雅通知，不弹窗打扰当前对话
            evt = threading.Event()
            st["pending_ask"] = {"args": args, "answered": False}
            st["ask_evt"] = evt
            st["ask_result"] = ""
            self.evt_signal.emit(sid, "pending",
                                 (f"对话「{self._session_label(sid)}」向你提问："
                                  f"{str(args.get('question', ''))[:40]}",
                                  "切换到该对话作答"))
            # 无限等待：不设 600s 超时返回空内容。用户切到该会话作答后由
            # _flush_pending set 唤醒；停止任务/切换会话同样唤醒
            evt.wait()
            return st.get("ask_result", "")
        self._ask_evt.clear()
        self.ask_signal.emit(sid, json.dumps(args, ensure_ascii=False))
        # 无限等待：弹窗 4 分钟无操作自动关闭并 set 唤醒（视为未作答），
        # 不再叠加 600s 兜底，避免长上下文/长思考被误判超时
        self._ask_evt.wait()
        return self._ask_result

    def _on_ask(self, sid: str, args_json: str):
        try:
            args = json.loads(args_json)
        except json.JSONDecodeError:
            args = {}
        # 需要用户作答：右下角通知（适用于所有对话，含前台）
        self._toast("AI 向你提问", f"对话「{self._session_label(sid)}」需作答："
                    f"{str(args.get('question', ''))[:40]}", False)
        # 提问不单独成卡：由 ask_user 工具结果(_on_result)把提问拼到用户回答前
        # 一并渲染成连续问答文本（弹窗内仍完整呈现问题与选项）
        dlg = _AskUserDialog(str(args.get("question", "")),
                             list(args.get("options") or []),
                             bool(args.get("multi_select", False)), self)
        agent_ui_ux.glassify_dialog(dlg, tint=0x30FFFFFF, frost_alpha=40)
        dlg.exec()
        self._ask_result = dlg.answer()
        self._ask_evt.set()

    def _session_label(self, sid: str) -> str:
        """取会话显示名（用于后台通知）"""
        lst = self._load_session_list()
        s = next((x for x in lst if x.get("id") == sid), None)
        return s.get("name", "新对话") if s else "新对话"

    def _notify_background(self, text: str, hint: str = ""):
        """优雅通知：右下角通知（不弹阻塞对话框、不在消息流留小字）"""
        msg = f"{text}\n{hint}" if hint else text
        self._popup_toast("后台对话提醒", msg, True, 5000)

    def _popup_toast(self, title: str, message: str, warn: bool = False, duration: int = 6000):
        """右下角任务栏上方滑出通知：自持 ToastNotification（主窗口已移除，不再依赖它）"""
        try:
            box = self.__dict__.get("_toast_box")
            if box is None:
                from zhuzhu_Copilot.ui.widgets import ToastNotification
                box = ToastNotification(None)
                self._toast_box = box
            box.show_toast(title, message, warn, duration)
        except Exception:
            pass

    def _toast(self, title: str, message: str, warn: bool = False, duration: int = 6000):
        """右下角任务栏上方滑出通知。用于任务完成 / 出错 / 停止、需要确认等事件提醒。"""
        self._popup_toast(title, message, warn, duration)

    def _panel_foreground(self) -> bool:
        """面板是否在前台可见（非最小化/隐藏）——用于决定是否显示 AI 工作状态通知"""
        return self.isVisible() and not self.isMinimized()

    def _notify_task_end(self):
        """当前会话任务结束通知：面板在最顶层时抑制，最小化/隐藏后再弹右下角通知"""
        if self._panel_foreground():
            return   # 面板在前台：用户可直接看到结果，不弹工作状态通知
        state = getattr(self._engine, "end_state", "") if self._engine else ""
        label = self._session_label(self._session_id)
        if self._user_stopped or state == "stopped":
            self._toast("AI 任务已停止", f"对话「{label}」的任务已被停止", True)
        elif state == "done":
            self._toast("AI 任务完成", f"对话「{label}」的任务已成功完成", False)
        else:
            self._toast("AI 任务出错", f"对话「{label}」的任务执行出错", True)

    def _flush_pending(self, sid: str):
        """前台处理该会话的挂起确认/提问（切到该会话时调用）"""
        st = self._sess.get(sid)
        if st is None:
            return
        if st.get("pending_confirm"):
            p = st["pending_confirm"]
            dlg = _ConfirmDialog(p["name"], p["args"], p["risk"], self)
            agent_ui_ux.glassify_dialog(dlg, tint=0x30FFFFFF, frost_alpha=40)
            dlg.exec()
            st["confirm_result"] = dlg.result_ok
            st["pending_confirm"] = None
            if st["confirm_evt"]:
                st["confirm_evt"].set()
        if st.get("pending_ask"):
            pa = st["pending_ask"]
            args = pa.get("args") or {}
            dlg = _AskUserDialog(str(args.get("question", "")),
                                 list(args.get("options") or []),
                                 bool(args.get("multi_select", False)), self)
            agent_ui_ux.glassify_dialog(dlg, tint=0x30FFFFFF, frost_alpha=40)
            dlg.exec()
            answer = dlg.answer()
            # 提问由 ask_user 工具结果(bg result)统一拼接到回答前渲染，
            # 此处仅把作答结果交给引擎（引擎随后经 result 事件触发问答文本段）
            st["ask_result"] = answer
            st["pending_ask"] = None
            if st["ask_evt"]:
                st["ask_evt"].set()
        self._refresh_session_combo()

    # ---------- 设置 ----------
    # 模型/接口/API Key 已写死，无需设置对话框


    def _persist_all_on_close(self):
        """关闭前同步持久化所有会话（多对话并发各自独立）：UI 气泡（rows）与模型上下文
        （save_context）均同步落盘，避免仅持久化当前会话 + daemon 线程随进程退出导致
        其它会话（尤其工作流会话）重启后历史丢失。"""
        # 关闭前释放媒体播放器（stop + 解绑视频输出），避免退出时 ffmpeg 后台线程
        # 析构时序产生 QFFmpeg::* disconnect 告警
        try:
            _code = getattr(self, "code_win", None)
            if _code is not None:
                _shutdown = getattr(_code, "shutdown_media", None)
                if callable(_shutdown):
                    _shutdown()
        except Exception:
            pass
        d = self._sessions_dir()
        d.mkdir(parents=True, exist_ok=True)
        for sid, st in list(self._sess.items()):
            if not sid or not st:
                continue
            eng = st.get("engine")
            if eng is not None:
                try:
                    eng.save_context(d / f"{sid}.json")
                except Exception:
                    pass
            self._write_ui_json(sid, st)
        lst = self._load_session_list()
        changed = False
        for x in lst:
            if x.get("id") in self._sess:
                x["updated"] = time.time()
                changed = True
        if changed:
            self._save_session_list(lst)

    def closeEvent(self, event):
        # 关闭面板：先收起统计浮层并解绑其全局事件守卫（避免残留事件过滤器）
        try:
            self._reset_token_pop()
        except Exception:
            pass
        # 关闭面板时同步收起并清理 zhuzhu Copilot 浮层（含防护线程 / 桌宠 / 托盘）
        _cp = self.__dict__.get("_copilot_panel")
        if _cp is not None:
            try:
                _cp.hide()
                _cp.shutdown()
            except Exception:
                pass
        # 关闭面板：停止所有会话的引擎（多对话并发各自独立）
        for st in self._sess.values():
            eng = st.get("engine")
            if eng:
                eng.stop()
                eng.join(1)   # 缩短等待：持久化改后台线程收尾，避免关闭窗口长时间阻塞
        # 关闭前先把当前会话的「实时内存态」回写进会话状态：任务生成中/刚被手动打断时，
        # AI 回复段只累积在 self._segments、尚未并入 _rows，若不先 commit_sess，
        # _persist_all_on_close 会写入陈旧（缺本次 AI 回复）的状态，重启后气泡丢失。
        try:
            self._commit_sess()
        except Exception:
            pass
        self._record_last_session()   # 记录最后停靠的会话页面（重启优先恢复到它）
        self._persist_all_on_close()   # 关闭前同步持久化全部会话（工作流会话重启后历史不丢）
        # 当前会话已停止的引擎不再持有：避免 st["engine"] 引用已 join 的线程
        _cs = self._sess.get(self._session_id)
        if _cs is not None and _cs.get("engine") is self._engine:
            _cs["engine"] = None
            self._engine = None
        try:
            self._mcp.close_all()
        except Exception:
            pass
        if self._admin_drop_filter is not None:
            try:
                QApplication.instance().removeNativeEventFilter(self._admin_drop_filter)
            except Exception:
                pass
            self._admin_drop_filter = None
        # 关闭面板：同时关闭所有独立无边框子窗口（避免反复启动时窗口残留堆积，
        # 上一次只关 todos_win 导致 git/wt/code/桌宠全部泄漏，
        # 用户看到桌面上"无限多个"Git/Bing/任务清单/小猫咪窗口）
        for _name in ("todos_win", "git_win", "wt_win", "code_win"):
            _w = getattr(self, _name, None)
            if _w is not None:
                try:
                    _w.close()
                except Exception:
                    pass
        event.accept()
