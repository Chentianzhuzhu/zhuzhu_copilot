# -*- coding: utf-8 -*-
"""AI 事件流气泡组件（1:1 复刻 ui_style_demo/index.html）。

设计来源：`ui_style_demo/index.html` 的「AI 事件流」结构。与旧实现（单个填充色
气泡 + 内部富文本）的关键差异：

- AI 回合**没有填充气泡**：改为上下 1px 虚线分区 + 左侧竖向虚线（demo `.ai-turn`），
  左上角骑在虚线上的「耗时徽章」（demo `.cost-ribbon`）。
- 思考过程是**独立气泡**（demo `.think-bubble`：卡片底 + 1px 边框 + 非对称圆角
  6/16/16/16 + 图标壳 + tag 胶囊），正文超 THINK_FOLD_LINES 行自动折叠渐隐。
- 工具调用是**无气泡行内条目**（demo `.tool-call`：图标壳 + 工具名 + meta + 参数 chip）。
- 执行命令是**独立命令块**（demo `.cmd`：圆角 10 边框 + 标题栏三点 + `$ 命令` + 输出行）。
- 正文回复是**纯文本**（demo `.stream`），永不参与折叠。
- AI 完成汇报后**过程区整体收起**，只留正文与「查看执行过程」按钮（demo `.ai-proc`）。
- 用户消息气泡为非对称圆角（demo `.msg`：18/18/6/18）。

本模块只负责「外壳与排版」，不负责内容语义：各段内层 HTML 由调用方
（agent_panel）逐段渲染后经 `render()` 传入，因此既有的 markdown 增量渲染、段级
内容签名缓存、链接识别、长文本截断能力全部复用。颜色/字体由 `ChatStyle` 注入，
几何常量取自 `ui.tokens`，本模块内不出现主题色字面量。

性能约定：`render()` 按段内容签名增量更新 —— 签名未变的段不重建控件、不触发重排，
因此流式输出（每 60ms 一次刷新）实际只更新正在增长的那一个标签。
"""
from __future__ import annotations

import dataclasses
import time
from typing import Callable, Optional

from PyQt6.QtCore import QPoint, QRect, QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPen, QTransform
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QVBoxLayout,
    QWidget,
)

from winapp_migrator.ui.tokens import (
    BUBBLE_RADIUS_THINK_TAIL,
    BUBBLE_RADIUS_USER,
    BUBBLE_RADIUS_USER_TAIL,
    DASH_PATTERN,
    FONT_BASE,
    FONT_BODY,
    FONT_CAPTION,
    FONT_SMALL,
    RADIUS_CHIP,
    RADIUS_CMD,
    RADIUS_LG,
    RADIUS_PILL,
    RADIUS_SM,
    RADIUS_TILE,
    SPACING_XS,
    THINK_FOLD_LINES,
)

# ---------------------------------------------------------------------------
# demo 几何：数值 1:1 取自 ui_style_demo/index.html（颜色不在此处，由 ChatStyle 注入）
# ---------------------------------------------------------------------------
BUBBLE_PAD_V = 13              # .msg padding: 13px 18px
BUBBLE_PAD_H = 18
AI_TURN_PAD_TOP = 18           # .ai-turn padding-top: 18px
AI_TURN_PAD_BOTTOM = 6         # .ai-turn padding-bottom: 6px
AI_TURN_GAP = 30               # .ai-turn margin-bottom: 30px
RIBBON_H = 21                  # .cost-ribbon 实测高度（上下 padding 4 + 行高 12 + 边框）
LEFT_GUTTER = 14               # .vline left:-14px 的等效留白（竖虚线到内容左缘）
THINK_PAD_V = 13               # .think-bubble padding: 13px 15px
THINK_PAD_H = 15
THINK_HEAD_GAP = 8             # .tb-head margin-bottom
TOOL_ICON = 30                 # .tc-icon 30x30
THINK_ICON = 28                # .tb-head .icon 28x28
CMD_PAD_V = 7                  # .cmd .bar padding: 7px 12px
CMD_PAD_H = 13                 # .cmd .in / .out 左右内边距
DOT_D = 8                      # .cmd .bar .dotbtn 直径
DOT_GAP = 5
DOTS_TICK_MS = 260             # 思考胶囊三点动画节拍
RIBBON_TICK_MS = 200           # 耗时徽章实时刷新节拍
FADE_H = 22                    # 折叠遮罩渐变高度（约 1.5 行）

# 段类型（调用方通过 render() 的 kind 字段指定）
KIND_THINK = "think"
KIND_TOOL = "tool"
KIND_CMD = "cmd"
KIND_RICH = "rich"
KIND_STREAM = "stream"

# 各段与下一段之间的间距（demo 中 .think-bubble/.cmd 的 margin-bottom、.tool-call 的 padding）
BLOCK_GAP = {KIND_THINK: 14, KIND_TOOL: 0, KIND_CMD: 14, KIND_RICH: SPACING_XS,
             KIND_STREAM: 16}

# 归入「过程区」的段类型（AI 完成汇报后整体收起，只留正文）
PROC_KINDS = frozenset({KIND_THINK, KIND_TOOL, KIND_CMD, KIND_RICH})

TOGGLE_OPEN_TEXT = "收起执行过程"
TOGGLE_CLOSED_TEXT = "查看执行过程"


@dataclasses.dataclass(frozen=True)
class ChatStyle:
    """一次渲染所需的样式快照：颜色取当前主题色板，字体族取平台字体栈。

    字段与 demo 的 CSS 变量一一对应（映射写在行尾注释），由面板 `_chat_style()`
    生成；主题切换后重新生成即整体换肤 —— 组件内不出现任何颜色字面量。
    """

    card: str          # --card        气泡/卡片底
    border: str        # --card-border 卡片描边
    border_soft: str   # --line        分隔线
    dash: str          # --dash        虚线
    text: str          # --title-fg    标题字色
    text_dim: str      # --body-fg     正文/次级字色
    accent: str        # --accent      强调色（图标/按钮/命令提示符）
    muted: str         # --muted       更弱的辅助字色
    icon_shell: str    # --icon-shell  图标壳底
    icon_color: str    # --icon-color  图标线条色
    tag_bg: str        # --tag-bg      胶囊底
    tag_fg: str        # --tag-fg      胶囊字色
    user_bg: str       # --user-bg     用户气泡底
    user_fg: str       # --user-fg     用户气泡字色
    cmd_fg: str        # --cmd-fg      命令字色
    ok_fg: str         # --ok-fg       命令输出字色（demo 用蓝，不用绿）
    hover: str         # 悬停底色（面板 HOVER）
    panel: str         # 代码块内底（面板 PANEL）
    font_ui: str = '"Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif'
    font_mono: str = 'Consolas, "Cascadia Code", "Courier New", monospace'


IconProvider = Callable[[str, int, str], QIcon]


# ---------------------------------------------------------------------------
# 通用小工具
# ---------------------------------------------------------------------------
def _dashed_pen(color: str) -> QPen:
    """虚线画笔：实 4 / 空 4，与 demo 的 dashed / linear-gradient 节奏一致。"""
    pen = QPen(QColor(color), 1, Qt.PenStyle.CustomDashLine)
    pen.setDashPattern(list(DASH_PATTERN))
    return pen


def rotate_icon(icon: QIcon, degrees: int, size: int) -> QIcon:
    """按角度旋转矢量图标（demo 用 CSS transform 旋转展开/收起指示符）。"""
    pm = icon.pixmap(size, size)
    if pm.isNull():
        return icon
    return QIcon(pm.transformed(QTransform().rotate(degrees),
                                Qt.TransformationMode.SmoothTransformation))


def fmt_seconds(seconds: float) -> str:
    """耗时文案：不足 1 分钟用 `12.4s`（与 demo 一致），超过用 `2m03s` 防长数字。"""
    seconds = max(0.0, float(seconds or 0.0))
    if seconds < 60:
        return f"{seconds:.1f}s"
    return f"{int(seconds // 60)}m{int(seconds % 60):02d}s"


def _widget_hfw(wid: QWidget, width: int) -> int:
    """控件在给定宽度下的真实高度：优先 heightForWidth（换行标签），否则用 sizeHint。"""
    try:
        h = wid.heightForWidth(max(1, int(width)))
        if isinstance(h, int) and h > 0:
            return h
    except Exception:
        pass
    return int(wid.sizeHint().height())


class PillButton(QPushButton):
    """胶囊按钮（demo .fold-btn 虚线描边 / .proc-btn 实线描边）。"""

    def __init__(self, style: ChatStyle, text: str, icon: QIcon = None,
                 dashed: bool = False, parent: QWidget = None):
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAutoDefault(False)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        border_style = "dashed" if dashed else "solid"
        border_color = style.dash if dashed else style.border
        self.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {style.accent};"
            f" font-family: {style.font_ui}; font-size: {FONT_SMALL}px; font-weight: 600;"
            f" border: 1px {border_style} {border_color};"
            f" border-radius: {RADIUS_PILL}px; padding: 5px 16px; }}"
            f"QPushButton:hover {{ background: {style.hover}; border-style: solid; }}")
        if icon is not None:
            self.setIcon(icon)
            self.setIconSize(QSize(FONT_SMALL, FONT_SMALL))


class DotsLabel(QWidget):
    """思考胶囊里的三点（demo .dots 依次弹起）。仅在回合进行中滚动，完成后静止，
    避免历史长对话里几十个定时器空转。"""

    def __init__(self, color: str, parent: QWidget = None):
        super().__init__(parent)
        self._color = color
        self._phase = 0
        self._live = False
        self._d = max(3.0, FONT_CAPTION / 2.6)
        self.setFixedSize(int(self._d * 3 + 6), int(self._d))
        self._timer = QTimer(self)
        self._timer.setInterval(DOTS_TICK_MS)
        self._timer.timeout.connect(self._tick)

    def set_live(self, live: bool):
        live = bool(live)
        if live == self._live:
            return
        self._live = live
        self._phase = 0
        if live and self.isVisible():
            self._timer.start()
        else:
            self._timer.stop()
        self.update()

    def _tick(self):
        self._phase = (self._phase + 1) % 3
        self.update()

    def hideEvent(self, e):
        self._timer.stop()
        super().hideEvent(e)

    def showEvent(self, e):
        super().showEvent(e)
        if self._live:
            self._timer.start()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        base = QColor(self._color)
        slot = self.width() / 3.0
        for i in range(3):
            c = QColor(base)
            active = self._live and i == self._phase
            c.setAlpha(255 if active else 110)
            p.setBrush(c)
            x = slot * i + (slot - self._d) / 2
            y = (self.height() - self._d) / 2 - (self._d * 0.45 if active else 0.0)
            p.drawEllipse(QRect(int(x), int(y), int(self._d), int(self._d)))
        p.end()


class FlowLayout(QLayout):
    """按行流式排列并自动换行（demo `.kv { display:flex; flex-wrap:wrap }`）。

    Qt 无内置流式布局；工具参数 chip 个数不确定，不换行会撑破面板宽度。
    """

    def __init__(self, parent: QWidget = None, h_gap: int = SPACING_XS,
                 v_gap: int = SPACING_XS):
        super().__init__(parent)
        self._items: list = []
        self._h_gap = h_gap
        self._v_gap = v_gap
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._measure(max(1, int(width)))

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size

    def _visible_items(self) -> list:
        out = []
        for item in self._items:
            wid = item.widget()
            if wid is not None and not wid.isHidden():
                out.append(item)
        return out

    def _measure(self, width: int) -> int:
        x, y, line_h = 0, 0, 0
        for item in self._visible_items():
            nxt = item.sizeHint()
            if x + nxt.width() > width and line_h > 0:
                x = 0
                y += line_h + self._v_gap
                line_h = 0
            x += nxt.width() + self._h_gap
            line_h = max(line_h, nxt.height())
        return y + line_h

    def _do_layout(self, rect: QRect):
        x, y, line_h = rect.x(), rect.y(), 0
        for item in self._visible_items():
            nxt = item.sizeHint()
            if x + nxt.width() > rect.right() + 1 and line_h > 0:
                x = rect.x()
                y += line_h + self._v_gap
                line_h = 0
            item.setGeometry(QRect(QPoint(x, y), nxt))
            x += nxt.width() + self._h_gap
            line_h = max(line_h, nxt.height())


def _mk_label(style: ChatStyle, wrap: bool = True) -> QLabel:
    """气泡内文本标签的公共配置（保持一致的选择行为与字体族）。"""
    lbl = QLabel()
    lbl.setTextFormat(Qt.TextFormat.RichText)
    lbl.setWordWrap(wrap)
    lbl.setTextInteractionFlags(
        Qt.TextInteractionFlag.TextSelectableByMouse |
        Qt.TextInteractionFlag.LinksAccessibleByMouse)
    lbl.setOpenExternalLinks(False)
    lbl.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
    lbl.setStyleSheet(f"background: transparent; font-family: {style.font_ui};")
    return lbl


def _tile(style: ChatStyle, icon: QIcon, size: int, radius: int) -> QLabel:
    """图标壳（demo .tc-icon / .tb-head .icon）：壳底 + 内描边 + 居中线条矢量图标。"""
    lbl = QLabel()
    lbl.setFixedSize(size, size)
    glyph = max(12, int(size * 0.55))
    lbl.setPixmap(icon.pixmap(glyph, glyph))
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lbl.setStyleSheet(
        f"background: {style.icon_shell}; border: 1px solid {style.border};"
        f" border-radius: {radius}px;")
    return lbl


class _FadeMask(QWidget):
    """折叠遮罩：由透明渐变到气泡底色，模拟 demo 的 mask-image 渐隐。"""

    def __init__(self, color: str, parent: QWidget = None):
        super().__init__(parent)
        self._color = QColor(color)
        self.setFixedHeight(FADE_H)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def paintEvent(self, _e):
        p = QPainter(self)
        g = QLinearGradient(0, 0, 0, self.height())
        top = QColor(self._color)
        top.setAlpha(0)
        g.setColorAt(0.0, top)
        g.setColorAt(1.0, self._color)
        p.fillRect(self.rect(), g)
        p.end()


class _PinMixin:
    """换行标签高度钉住：QLabel.sizeHint 对换行长文只按单行计高，
    必须按真实宽度显式钉住最小高度，否则气泡底部的文字会被裁掉。"""

    def _pin_wrapping(self):
        lbl = getattr(self, "_body", None)
        if lbl is None:
            return
        m = self.layout().contentsMargins()
        w = self.width() - m.left() - m.right()
        if w <= 0:
            return
        try:
            h = lbl.heightForWidth(w)
        except Exception:
            return
        if isinstance(h, int) and h > 0 and abs(lbl.minimumHeight() - h) >= 2:
            lbl.setMinimumHeight(h)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._pin_wrapping()


class ThinkBubble(_PinMixin, QFrame):
    """思考过程气泡（demo .think-bubble）：卡片底 + 1px 边框 + 非对称圆角
    （左上 6 为「小尾巴」角），头行 = 图标壳 + 「思考过程」+ tag 胶囊（含三点），
    正文超 THINK_FOLD_LINES 行自动折叠并以渐隐遮罩收尾。"""

    def __init__(self, style: ChatStyle, icon_provider: IconProvider,
                 parent: QWidget = None):
        super().__init__(parent)
        self._style = style
        self._icon_provider = icon_provider
        self._user_open: Optional[bool] = None   # 用户手动展开/收起后不再自动判定
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            f"ThinkBubble {{ background: {style.card};"
            f" border: 1px solid {style.border};"
            f" border-top-left-radius: {BUBBLE_RADIUS_THINK_TAIL}px;"
            f" border-top-right-radius: {RADIUS_LG}px;"
            f" border-bottom-right-radius: {RADIUS_LG}px;"
            f" border-bottom-left-radius: {RADIUS_LG}px; }}")

        root = QVBoxLayout(self)
        root.setContentsMargins(THINK_PAD_H, THINK_PAD_V, THINK_PAD_H, THINK_PAD_V)
        root.setSpacing(THINK_HEAD_GAP)

        head = QWidget(self)
        hl = QHBoxLayout(head)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(9)
        self._icon = _tile(style, icon_provider("think", FONT_BODY + 3, style.icon_color),
                           THINK_ICON, RADIUS_SM)
        hl.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignVCenter)
        title = QLabel("思考过程")
        title.setStyleSheet(
            f"color: {style.text}; font-size: {FONT_BODY}px; font-weight: 600;"
            f" font-family: {style.font_ui}; background: transparent;")
        hl.addWidget(title, 0, Qt.AlignmentFlag.AlignVCenter)
        hl.addStretch(1)
        self._tag = QWidget(head)
        self._tag.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._tag.setStyleSheet(
            f"QWidget {{ background: {style.tag_bg}; border-radius: {RADIUS_PILL}px; }}")
        tl = QHBoxLayout(self._tag)
        tl.setContentsMargins(9, 4, 9, 4)
        tl.setSpacing(6)
        self._tag_text = QLabel("")
        self._tag_text.setStyleSheet(
            f"color: {style.tag_fg}; font-size: {FONT_CAPTION}px; background: transparent;")
        tl.addWidget(self._tag_text, 0, Qt.AlignmentFlag.AlignVCenter)
        self._dots = DotsLabel(style.tag_fg, self._tag)
        tl.addWidget(self._dots, 0, Qt.AlignmentFlag.AlignVCenter)
        hl.addWidget(self._tag, 0, Qt.AlignmentFlag.AlignVCenter)
        root.addWidget(head)

        self._body = _mk_label(style)
        self._body.setStyleSheet(
            f"background: transparent; color: {style.text_dim};"
            f" font-size: {FONT_BODY}px; line-height: 1.75;"
            f" font-family: {style.font_ui};")
        root.addWidget(self._body)

        self._fold_btn = PillButton(style, "继续查看",
                                    icon_provider("chev", FONT_SMALL, style.accent),
                                    dashed=True, parent=self)
        self._fold_btn.clicked.connect(self._toggle)
        self._fold_btn.hide()
        wrap = QHBoxLayout()
        wrap.setContentsMargins(0, 0, 0, 0)
        wrap.addStretch(1)
        wrap.addWidget(self._fold_btn)
        wrap.addStretch(1)
        root.addLayout(wrap)

        self._mask = _FadeMask(style.card, self)
        self._mask.hide()
        self._folded = None      # 折叠态缓存：仅在状态真正变化时改动几何

    # ---------- 内容 ----------
    def set_content(self, tag: str, body_html: str):
        self._tag_text.setText(tag or "")
        self._tag.setVisible(bool(tag))
        self._body.setText(body_html or "")
        self._body.setMinimumHeight(0)
        self._user_open = None
        self._fold_btn.setText("继续查看")
        self._fold_btn.setIcon(self._icon_provider("chev", FONT_SMALL, self._style.accent))
        self._apply_fold()

    def set_live(self, live: bool):
        self._dots.set_live(live)

    # ---------- 折叠 ----------
    def _line_height(self) -> int:
        return max(1, self._body.fontMetrics().lineSpacing())

    def _limit_h(self) -> int:
        return self._line_height() * THINK_FOLD_LINES

    def _inner_w(self) -> int:
        m = self.layout().contentsMargins()
        return max(1, self.width() - m.left() - m.right())

    def _full_h(self) -> int:
        return _widget_hfw(self._body, self._inner_w())

    def _is_foldable(self) -> bool:
        return self._full_h() > self._limit_h()

    def _apply_fold(self):
        foldable = self._is_foldable()
        folded = foldable and self._user_open is not True
        if self._folded == folded:
            self._place_mask()
            return
        self._folded = folded
        self._fold_btn.setVisible(foldable)
        self._body.setMaximumHeight(self._limit_h() if folded else 16777215)
        self._mask.setVisible(folded)
        self._place_mask()
        self.updateGeometry()

    def _place_mask(self):
        """折叠遮罩定位：贴在折叠边界。用 _folded 判定而非 isVisible ——
        面板未显示时 isVisible 恒为 False，据此判定会让遮罩在显示后落在错误位置。"""
        if not self._folded:
            return
        m = self.layout().contentsMargins()
        y = self._body.geometry().bottom() - FADE_H + 1
        self._mask.setGeometry(m.left(), max(0, y),
                               max(1, self.width() - m.left() - m.right()), FADE_H)

    def _toggle(self):
        opened = self._user_open is not True
        self._user_open = opened
        self._fold_btn.setText("收起" if opened else "继续查看")
        self._fold_btn.setIcon(rotate_icon(
            self._icon_provider("chev", FONT_SMALL, self._style.accent),
            180 if opened else 0, FONT_SMALL))
        self._apply_fold()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._apply_fold()

    def heightForWidth(self, width: int) -> int:
        m = self.layout().contentsMargins()
        inner = max(1, int(width) - m.left() - m.right())
        h = m.top() + m.bottom() + THINK_ICON + THINK_HEAD_GAP
        full = _widget_hfw(self._body, inner)
        foldable = full > self._limit_h()
        folded = foldable and self._user_open is not True
        h += self._limit_h() if folded else full
        if foldable:
            h += self._fold_btn.sizeHint().height() + THINK_HEAD_GAP
        return h


class ToolCallRow(QWidget):
    """工具调用行（demo .tool-call）：无气泡，图标壳 + 工具名 + meta + 参数 chip。"""

    def __init__(self, style: ChatStyle, icon_provider: IconProvider,
                 parent: QWidget = None):
        super().__init__(parent)
        self._style = style
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)
        self._icon = _tile(style, icon_provider("tool", FONT_BASE + 2, style.icon_color),
                           TOOL_ICON, RADIUS_TILE)
        root.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignTop)
        self._body = QWidget(self)
        bl = QVBoxLayout(self._body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(3)
        self._title = QLabel("")
        self._title.setStyleSheet(
            f"color: {style.text}; font-size: {FONT_BODY}px; font-weight: 600;"
            f" font-family: {style.font_mono}; background: transparent;")
        bl.addWidget(self._title)
        self._meta = QLabel("")
        self._meta.setWordWrap(True)
        self._meta.setStyleSheet(
            f"color: {style.muted}; font-size: {FONT_CAPTION}px;"
            f" font-family: {style.font_mono}; background: transparent;")
        bl.addWidget(self._meta)
        self._chips = QWidget(self._body)
        self._chip_lay = FlowLayout(self._chips, h_gap=6, v_gap=6)
        bl.addWidget(self._chips)
        root.addWidget(self._body, 1)
        self._body_lay = bl

    def set_content(self, name: str, meta: str, params: dict):
        self._title.setText(name or "")
        self._meta.setText(meta or "")
        self._meta.setVisible(bool(meta))
        while self._chip_lay.count():
            item = self._chip_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        for key, val in (params or {}).items():
            self._chip_lay.addWidget(self._chip(key, val))
        self._chips.setVisible(bool(params))

    def _chip(self, key: str, value) -> QLabel:
        lbl = QLabel(f"<b>{key}</b>&nbsp;{value}")
        lbl.setStyleSheet(
            f"background: {self._style.tag_bg}; color: {self._style.tag_fg};"
            f" border-radius: {RADIUS_CHIP}px; padding: 4px 10px;"
            f" font-size: {FONT_CAPTION}px; font-family: {self._style.font_mono};")
        return lbl

    def _inner_w(self) -> int:
        return max(1, self.width() - self.layout().contentsMargins().right()
                   - TOOL_ICON - self.layout().spacing())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        w = self._inner_w()
        for lbl in (self._meta,):
            if not lbl.isHidden():
                h = lbl.heightForWidth(w)
                if isinstance(h, int) and h > 0 and abs(lbl.minimumHeight() - h) >= 2:
                    lbl.setMinimumHeight(h)

    def heightForWidth(self, width: int) -> int:
        inner = max(1, int(width) - TOOL_ICON - self.layout().spacing()
                    - self.layout().contentsMargins().right())
        h = self._title.sizeHint().height()
        if not self._meta.isHidden():
            h += self._body_lay.spacing() + _widget_hfw(self._meta, inner)
        if not self._chips.isHidden():
            h += self._body_lay.spacing() + self._chip_lay.heightForWidth(inner)
        return max(TOOL_ICON, h)


class CmdBlock(_PinMixin, QFrame):
    """执行命令块（demo .cmd）：圆角边框 + 标题栏三点 + `$ 命令` + 输出行。"""

    def __init__(self, style: ChatStyle, parent: QWidget = None):
        super().__init__(parent)
        self._style = style
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            f"CmdBlock {{ background: {style.panel};"
            f" border: 1px solid {style.border}; border-radius: {RADIUS_CMD}px; }}")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        bar = QWidget(self)
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        bar.setStyleSheet(
            f"QWidget {{ background: transparent;"
            f" border-bottom: 1px solid {style.border}; }}")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(CMD_PAD_H, CMD_PAD_V, CMD_PAD_H, CMD_PAD_V)
        bl.setSpacing(DOT_GAP)
        for _ in range(3):
            dot = QLabel(bar)
            dot.setFixedSize(DOT_D, DOT_D)
            dot.setStyleSheet(f"background: {style.border}; border-radius: {DOT_D // 2}px;")
            bl.addWidget(dot, 0, Qt.AlignmentFlag.AlignVCenter)
        bl.addSpacing(CMD_PAD_H)
        self._bar_label = QLabel("")
        self._bar_label.setStyleSheet(
            f"color: {style.text_dim}; font-size: {FONT_CAPTION}px;"
            f" font-family: {style.font_mono}; background: transparent;")
        bl.addWidget(self._bar_label, 0, Qt.AlignmentFlag.AlignVCenter)
        bl.addStretch(1)
        root.addWidget(bar)

        self._cmd = _mk_label(style)
        self._cmd.setStyleSheet(
            f"background: transparent; padding: {CMD_PAD_V + 4}px {CMD_PAD_H}px;"
            f" color: {style.cmd_fg}; font-size: {FONT_BODY}px;"
            f" font-family: {style.font_mono};")
        root.addWidget(self._cmd)

        self._body = _mk_label(style)
        self._body.setStyleSheet(
            f"background: transparent; padding: 0 {CMD_PAD_H}px {CMD_PAD_V + 4}px;"
            f" color: {style.ok_fg}; font-size: {FONT_SMALL}px;"
            f" font-family: {style.font_mono};")
        root.addWidget(self._body)

    def set_content(self, label: str, cmd_html: str, out_html: str):
        self._bar_label.setText(label or "")
        self._cmd.setText(f'<span style="color:{self._style.accent};">$&nbsp;</span>'
                          f'{cmd_html}' if cmd_html else "")
        self._body.setText(f'<span style="color:{self._style.muted};">ok</span>&nbsp;&nbsp;'
                           f'{out_html}' if out_html else "")
        self._cmd.setVisible(bool(cmd_html))
        self._body.setVisible(bool(out_html))
        for lbl in (self._cmd, self._body):
            lbl.setMinimumHeight(0)

    def _inner_w(self) -> int:
        return max(1, self.width() - 2)

    def _pin_wrapping(self):
        for lbl in (self._cmd, self._body):
            if not lbl.isVisible():
                continue
            h = _widget_hfw(lbl, self._inner_w())
            if h > 0 and abs(lbl.minimumHeight() - h) >= 2:
                lbl.setMinimumHeight(h)

    def heightForWidth(self, width: int) -> int:
        inner = max(1, int(width) - 2)
        h = self.layout().itemAt(0).widget().sizeHint().height()
        if not self._cmd.isHidden():
            h += _widget_hfw(self._cmd, inner)
        if not self._body.isHidden():
            h += _widget_hfw(self._body, inner)
        return h + 2


class RichBlock(QWidget):
    """通用富文本段容器（透明无气泡）：承载提问 / 子 Agent / 下载进度 / 截图等既有段，
    内容 HTML 由调用方渲染，链接点击回到面板既有处理链路。"""

    def __init__(self, style: ChatStyle, font_size: int = FONT_BODY,
                 color: str = None, parent: QWidget = None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, SPACING_XS, 0, 0)
        root.setSpacing(0)
        self._body = _mk_label(style)
        self._body.setStyleSheet(
            f"background: transparent; color: {color or style.text};"
            f" font-size: {font_size}px; line-height: 1.8;"
            f" font-family: {style.font_ui};")
        root.addWidget(self._body)

    def set_html(self, html: str):
        self._body.setText(html or "")
        self._body.setMinimumHeight(0)

    def heightForWidth(self, width: int) -> int:
        m = self.layout().contentsMargins()
        inner = max(1, int(width) - m.left() - m.right())
        return _widget_hfw(self._body, inner) + m.top() + m.bottom()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        m = self.layout().contentsMargins()
        w = self.width() - m.left() - m.right()
        if w <= 0:
            return
        h = _widget_hfw(self._body, w)
        if h > 0 and abs(self._body.minimumHeight() - h) >= 2:
            self._body.setMinimumHeight(h)


class StreamBlock(RichBlock):
    """正文回复（demo .stream .txt）：纯文本、永不参与过程区折叠。"""

    def __init__(self, style: ChatStyle, parent: QWidget = None):
        super().__init__(style, font_size=FONT_BASE, color=style.text, parent=parent)


class UserBubble(QLabel):
    """用户消息气泡（demo .msg）：深蓝底 + 非对称圆角 18/18/6/18。"""

    def __init__(self, style: ChatStyle, parent: QWidget = None):
        super().__init__(parent)
        self.setWordWrap(True)
        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse |
            Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.setOpenExternalLinks(False)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self.setStyleSheet(
            f"background: {style.user_bg}; color: {style.user_fg}; border: none;"
            f" border-top-left-radius: {BUBBLE_RADIUS_USER}px;"
            f" border-top-right-radius: {BUBBLE_RADIUS_USER}px;"
            f" border-bottom-right-radius: {BUBBLE_RADIUS_USER_TAIL}px;"
            f" border-bottom-left-radius: {BUBBLE_RADIUS_USER}px;"
            f" padding: {BUBBLE_PAD_V}px {BUBBLE_PAD_H}px;"
            f" font-family: {style.font_ui}; font-size: {FONT_BASE}px;")


class CostRibbon(QFrame):
    """耗时徽章（demo .cost-ribbon）：骑在回合顶部虚线上，时钟图标 + 耗时文本。

    回合进行中按 RIBBON_TICK_MS 实时刷新，完成后冻结为总耗时（由面板持久化）。
    """

    def __init__(self, style: ChatStyle, icon: QIcon, parent: QWidget = None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            f"CostRibbon {{ background: {style.card};"
            f" border: 1px solid {style.border}; border-radius: {RADIUS_PILL}px; }}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 12, 4)
        lay.setSpacing(7)
        self._icon = QLabel(self)
        self._icon.setPixmap(icon.pixmap(FONT_SMALL + 1, FONT_SMALL + 1))
        self._icon.setStyleSheet("background: transparent; border: none;")
        lay.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignVCenter)
        self._text = QLabel("")
        self._text.setStyleSheet(
            f"background: transparent; border: none; color: {style.accent};"
            f" font-size: {FONT_SMALL}px; font-weight: 600;"
            f" font-family: {style.font_ui};")
        lay.addWidget(self._text, 0, Qt.AlignmentFlag.AlignVCenter)
        self.setFixedHeight(RIBBON_H)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._t0 = 0.0
        self._live = False
        self._timer = QTimer(self)
        self._timer.setInterval(RIBBON_TICK_MS)
        self._timer.timeout.connect(self._tick)

    def start(self, t0: float = None):
        self._t0 = float(t0 or time.time())
        self._live = True
        self._text.setText(fmt_seconds(time.time() - self._t0))
        self.show()
        self._timer.start()

    def freeze(self, seconds: float):
        self._live = False
        self._timer.stop()
        self._text.setText(fmt_seconds(seconds))
        self.show()

    def _tick(self):
        self._text.setText(fmt_seconds(time.time() - self._t0))

    @property
    def is_live(self) -> bool:
        return self._live

    def hideEvent(self, e):
        self._timer.stop()
        super().hideEvent(e)

    def showEvent(self, e):
        super().showEvent(e)
        if self._live:
            self._timer.start()


class ChatTurn(QWidget):
    """AI 回合容器（demo .ai-turn）。

    结构：上下 1px 虚线分区 + 左侧竖向虚线 + 耗时徽章（骑线）+ 过程区 + 正文 + 系统时间。
    过程区（思考/工具/命令/富文本段）在回合完成后整体收起，只留正文与
    「查看执行过程」开关（demo `.ai-turn.done .ai-proc`）。
    """

    def __init__(self, style: ChatStyle, icon_provider: IconProvider,
                 parent: QWidget = None):
        super().__init__(parent)
        self._style = style
        self._icon_provider = icon_provider
        self.style_obj = style
        self._link_handler: Optional[Callable[[str], None]] = None
        self._menu_handler: Optional[Callable[[QPoint], None]] = None
        self._items: list = []          # [(kind, sig, widget, spacer)]
        self._done = False
        self._settled = False           # 回合是否已结束过（决定过程区开关是否常驻）
        self._user_open: Optional[bool] = None   # 用户对过程区的手动选择（优先于 done）
        self._live = False
        self._cost: Optional[float] = None
        self._t0 = 0.0
        self._inset = RIBBON_H // 2     # 顶部虚线相对容器顶部的偏移（徽章骑线）
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(
            lambda pos: self._emit_menu(self.mapToGlobal(pos)))

        self._lay = QVBoxLayout(self)
        self._lay.setSpacing(0)
        self._box = QWidget(self)
        self._box_lay = QVBoxLayout(self._box)
        self._box_lay.setContentsMargins(0, self._inset + AI_TURN_PAD_TOP, 0,
                                         AI_TURN_PAD_BOTTOM + AI_TURN_GAP)
        self._box_lay.setSpacing(0)
        self._lay.addWidget(self._box)

        self._ribbon = CostRibbon(style, icon_provider("clock", FONT_SMALL, style.accent), self)
        self._ribbon.hide()

        self._toggle = PillButton(style, TOGGLE_CLOSED_TEXT,
                                  icon_provider("chev", FONT_SMALL, style.accent), parent=self)
        self._toggle.clicked.connect(self._on_toggle)
        self._toggle.hide()
        self._toggle_idx = -1

        self._sys = QLabel("")          # demo .sys-meta：回合的「开始 → 结束」时间行
        self._sys.setStyleSheet(
            f"background: transparent; color: {style.muted};"
            f" font-size: {FONT_CAPTION}px; font-family: {style.font_mono};")
        self._sys.hide()
        self._box_lay.addWidget(self._sys)

    # ---------- 外部接口 ----------
    def set_link_handler(self, fn: Callable[[str], None]):
        """链接点击回调（折叠/文件路径等）"""
        self._link_handler = fn

    def set_menu_handler(self, fn: Callable[[QPoint], None]):
        """右键菜单回调（入参为全局坐标）"""
        self._menu_handler = fn

    @property
    def cost(self) -> Optional[float]:
        """本回合的总耗时（秒）；进行中/数据缺失为 None（供会话持久化）"""
        return self._cost

    def set_cost(self, seconds: Optional[float]):
        """注入已持久化的耗时（会话恢复）或重置为未知；徽章在下一次 render 生效"""
        self._cost = None if seconds is None else float(seconds)

    def content_labels(self) -> list:
        return [lbl for lbl in self.findChildren(QLabel)
                if lbl.textInteractionFlags() != Qt.TextInteractionFlag.NoTextInteraction]

    def render(self, blocks, *, cost: Optional[float] = None, live: bool = False,
               sys_meta: str = "", done: bool = False):
        """增量渲染：blocks 为 (kind, payload, sig) 序列。

        sig 未变且 kind 相同的段直接复用既有控件（不重建、不重排），因此流式刷新
        只影响内容变化的那一段。
        """
        blocks = list(blocks)
        self._live = bool(live)
        self._finalize_ribbon(cost, live)
        self._rebuild_blocks(blocks)
        self._sys.setText(sys_meta or "")
        self._sys.setVisible(bool(sys_meta))
        self._set_done(done)
        self._apply_live()
        self.updateGeometry()

    # ---------- 过程区收起 / 展开 ----------
    def _set_done(self, done: bool):
        """按「回合是否已结束」收放过程区。

        一旦结束过（settled），开关按钮就常驻：用户手动展开后仍可再收起。
        用户的手动选择（_user_open）优先于传入的 done，避免窗口缩放等重渲染
        把用户刚展开的过程区又自动收起。
        """
        done = bool(done)
        if done:
            self._settled = True
        if self._user_open is not None:
            done = not self._user_open
        self._done = done
        has_proc = self._has_proc()
        for kind, _sig, wid, spacer in self._items:
            # 用 isHidden()（显式隐藏标记）判定，而非 isVisible()：面板最小化/尚未
            # 显示时 isVisible() 恒为 False，会误判成「已按期望隐藏」而漏收起过程区。
            want_visible = not (done and kind in PROC_KINDS)
            if wid.isHidden() == want_visible:
                wid.setVisible(want_visible)
            spacer.changeSize(
                0, BLOCK_GAP.get(kind, SPACING_XS) if want_visible else 0)
        show_toggle = self._settled and has_proc
        self._toggle.setVisible(show_toggle)
        self._toggle.setText(TOGGLE_CLOSED_TEXT if done else TOGGLE_OPEN_TEXT)
        self._toggle.setIcon(rotate_icon(
            self._icon_provider("chev", FONT_SMALL, self._style.accent),
            0 if done else 180, FONT_SMALL))
        if show_toggle:
            self._place_toggle()

    def _on_toggle(self):
        self._user_open = self._done      # 展开：记住用户选择（后续重渲染不再自动收起）
        self._set_done(not self._done)
        self.updateGeometry()

    def _has_proc(self) -> bool:
        return any(kind in PROC_KINDS for kind, _s, _w, _sp in self._items)

    def _place_toggle(self):
        """把开关放到最后一个过程块之后（demo 中 .proc-ctrl 紧随 .ai-proc）。
        位置未变时不重复插拔，避免流式刷新期间反复触发布局失效。"""
        idx = 0
        for i, (kind, _s, _w, _sp) in enumerate(self._items):
            if kind in PROC_KINDS:
                idx = i * 2 + 2
        idx = min(idx, max(0, self._box_lay.count() - 1))
        if idx == self._toggle_idx and self._toggle.parent() is self._box:
            return
        self._toggle_idx = idx
        self._box_lay.removeWidget(self._toggle)
        self._box_lay.insertWidget(idx, self._toggle)

    # ---------- 增量重建 ----------
    def _rebuild_blocks(self, blocks: list):
        reuse = 0
        while (reuse < len(blocks) and reuse < len(self._items)
               and self._items[reuse][0] == blocks[reuse][0]
               and self._items[reuse][1] == blocks[reuse][2]):
            reuse += 1      # 签名命中：内容未变，不重建也不重排（流式只处理末段）
        if reuse < len(self._items):
            self._drop_from(reuse)
        for kind, payload, sig in blocks[reuse:]:
            self._append_block(kind, payload, sig)

    def _drop_from(self, index: int):
        for _kind, _sig, wid, spacer in self._items[index:]:
            self._box_lay.removeWidget(wid)
            self._box_lay.removeItem(spacer)
            wid.setParent(None)
            wid.deleteLater()
        del self._items[index:]

    def _append_block(self, kind: str, payload: dict, sig):
        wid = self._make_block(kind)
        spacer = QSpacerItem(0, BLOCK_GAP.get(kind, SPACING_XS),
                             QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self._box_lay.insertWidget(self._box_lay.count() - 1, wid)
        self._box_lay.insertItem(self._box_lay.count() - 1, spacer)
        self._items.append((kind, sig, wid, spacer))
        self._update_block(wid, kind, payload)
        self._wire(wid)

    def _make_block(self, kind: str) -> QWidget:
        if kind == KIND_THINK:
            return ThinkBubble(self._style, self._icon_provider, self._box)
        if kind == KIND_TOOL:
            return ToolCallRow(self._style, self._icon_provider, self._box)
        if kind == KIND_CMD:
            return CmdBlock(self._style, self._box)
        if kind == KIND_STREAM:
            return StreamBlock(self._style, self._box)
        return RichBlock(self._style, parent=self._box)

    def _update_block(self, wid: QWidget, kind: str, payload: dict):
        payload = payload or {}
        if kind == KIND_THINK:
            wid.set_content(payload.get("tag", ""), payload.get("body", ""))
        elif kind == KIND_TOOL:
            wid.set_content(payload.get("name", ""), payload.get("meta", ""),
                            payload.get("params") or {})
        elif kind == KIND_CMD:
            wid.set_content(payload.get("label", ""), payload.get("cmd", ""),
                            payload.get("out", ""))
        else:
            wid.set_html(payload.get("html", ""))

    def _wire(self, wid: QWidget):
        """新块接入链接点击与右键菜单（右键菜单事件按需从子控件冒泡到外层）。"""
        for lbl in wid.findChildren(QLabel):
            flags = lbl.textInteractionFlags()
            if flags != Qt.TextInteractionFlag.NoTextInteraction:
                lbl.setOpenExternalLinks(False)
                lbl.linkActivated.connect(self._emit_link)
                lbl.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                lbl.customContextMenuRequested.connect(
                    lambda _pos, l=lbl: self._emit_menu(l.mapToGlobal(_pos)))

    # ---------- 事件出口 ----------
    def _emit_link(self, url: str):
        if self._link_handler is not None:
            self._link_handler(url)

    def _emit_menu(self, pos: QPoint):
        if self._menu_handler is not None:
            self._menu_handler(pos)

    # ---------- 耗时徽章 ----------
    def _finalize_ribbon(self, cost: Optional[float], live: bool):
        if live:
            self._t0 = self._t0 or time.time()
            if not self._ribbon.is_live:
                self._ribbon.start(self._t0)   # 进行中：实时刷新（重复渲染不重启计时）
            self._ensure_inset(True)
            return
        if cost is None:
            cost = self._cost        # 已冻结的耗时保持稳定（重复渲染不闪烁）
        if cost is not None:
            self._ribbon.freeze(float(cost))
            self._cost = float(cost)
            self._ensure_inset(True)
            return
        # 无耗时数据（老会话）：隐藏徽章并收回骑线留白，上虚线贴顶
        self._ribbon.hide()
        self._cost = None
        self._ensure_inset(False)

    def _ensure_inset(self, has_ribbon: bool):
        inset = RIBBON_H // 2 if has_ribbon else 0
        if inset == self._inset:
            return
        self._inset = inset
        self._box_lay.setContentsMargins(0, inset + AI_TURN_PAD_TOP, 0,
                                         AI_TURN_PAD_BOTTOM + AI_TURN_GAP)

    def _apply_live(self):
        for _kind, _sig, wid, _sp in self._items:
            if isinstance(wid, ThinkBubble):
                wid.set_live(self._live)

    def start_live(self):
        """任务开始：启动耗时计时（徽章实时刷新）"""
        self._t0 = time.time()

    def finish(self) -> float:
        """任务结束：停止计时并冻结耗时徽章，返回总耗时（秒）。

        重复调用返回已冻结的值（幂等），避免任务收尾路径被多次触发时徽章反复重置。
        """
        if not self._t0:
            return float(self._cost or 0.0)
        seconds = max(0.0, time.time() - self._t0)
        self._t0 = 0.0
        self._cost = seconds
        self._ribbon.freeze(seconds)
        return seconds

    # ---------- 绘制：虚线分区 ----------
    def _dash_bottom_y(self) -> int:
        """下虚线位置：容器底边向上让出 AI_TURN_GAP（demo 的 margin-bottom 30px）"""
        return max(0, self.height() - AI_TURN_GAP - 1)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setPen(_dashed_pen(self._style.dash))
        top = self._inset
        right = max(LEFT_GUTTER, self.width() - 1)
        p.drawLine(LEFT_GUTTER, top, right, top)
        bottom = self._dash_bottom_y()
        p.drawLine(LEFT_GUTTER, bottom, right, bottom)
        # 左侧竖向虚线（demo .vline：top 18px / bottom 8px）
        p.drawLine(0, top + AI_TURN_PAD_TOP, 0, max(top + AI_TURN_PAD_TOP, self.height() - 8))
        p.end()

    def _place_ribbon(self):
        self._ribbon.adjustSize()
        try:
            self._ribbon.move(0, 0)
            self._ribbon.raise_()
        except Exception:
            pass

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place_ribbon()

    def showEvent(self, e):
        super().showEvent(e)
        self._place_ribbon()

    def heightForWidth(self, width: int) -> int:
        m = self._box_lay.contentsMargins()
        inner = max(1, int(width) - m.left() - m.right())
        h = m.top() + m.bottom()
        for _kind, _sig, wid, spacer in self._items:
            if wid.isHidden():
                continue
            h += _widget_hfw(wid, inner) + spacer.sizeHint().height()
        if self._toggle.isVisible():
            h += self._toggle.sizeHint().height()
        if self._sys.isVisible():
            h += _widget_hfw(self._sys, inner)
        return h
