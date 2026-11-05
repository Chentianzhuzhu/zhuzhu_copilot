import os
import re
import time
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QDialog, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
    QCheckBox, QSizePolicy, QGraphicsDropShadowEffect,
    QStyledItemDelegate, QStyle,
    QFileIconProvider, QApplication, QComboBox, QMenu, QFrame,
    QListWidget, QListWidgetItem, QStyleOptionViewItem,
)
from PyQt6.QtCore import (
    Qt, QSize, QRect, QRectF, QFileInfo, QPoint, QTimer, QObject, QPropertyAnimation,
    QParallelAnimationGroup, QEasingCurve, QEvent, QVariantAnimation,
    pyqtSignal, pyqtProperty
)
from PyQt6.QtGui import (QColor, QPainter, QIcon, QFont, QFontMetrics, QPixmap,
                         QPainterPath, QPen, QCursor)

from zhuzhu_Copilot.ui.styles import PALETTE, svg_icon
from zhuzhu_Copilot.ui.tokens import (
    COMBO_ARROW_W, FONT_BASE, RADIUS_MD, RADIUS_SM, RADIUS_XS,
)
from zhuzhu_Copilot.ui import glass
from zhuzhu_Copilot.ui import motion
from zhuzhu_Copilot.core.i18n import ui as _ui, uim as _uim, uif as _uif

# 卸载/安装类程序名，图标无意义，查找主程序时排除
_BANNED_EXE = {
    "unins", "unins000", "unins001", "uninst", "uninstall", "uninstaller",
    "setup", "install", "installer", "update", "updater", "redist",
}
# UWP 包内常见 logo 命名，优先选用
_UWP_LOGO_HINTS = ("storelogo", "square150x150logo", "square44x44logo", "applogo")

# Toast 关闭按钮矢量图标（淡灰简约，替代文本符号）
_TOAST_CLOSE_SVG = f"""<svg width="16" height="16" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
<path d="M7 7l10 10M17 7L7 17" stroke="{PALETTE['text_secondary']}" stroke-width="2"
      stroke-linecap="round"/></svg>"""


def _danger_hover() -> str:
    """危险色按钮的悬停提亮色"""
    return QColor(PALETTE["danger"]).lighter(112).name()


class SwitchButton(QWidget):
    """滑块开关：点击切换，带动画。信号 toggled(bool) 在用户点击时发射

    招 5：拨动用「咬合」性格（motion.snap，OutBack 5% 微簧 + SNAP_MS）——
    滑块到位时轻微回弹一下再咬死，比纯 OutCubic 的「滑过去就停」有质感。
    """

    toggled = pyqtSignal(bool)

    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self._checked = checked
        self._knob = 1.0 if checked else 0.0   # 滑块位置 0~1
        self.setFixedSize(48, 26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(motion.SNAP_MS)
        self._anim.setEasingCurve(motion.snap())

    def knob(self) -> float:
        return self._knob

    def set_knob(self, v: float):
        self._knob = v
        self.update()

    knob = pyqtProperty(float, knob, set_knob)

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, checked: bool):
        if checked == self._checked:
            return
        self._checked = checked
        self._anim.stop()
        self._anim.setStartValue(self._knob)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setChecked(not self._checked)
            self.toggled.emit(self._checked)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._checked:
            # 开：主色浮起；关：凹陷字段色（招 1：不描边，用底色分层表达状态）
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(PALETTE["primary"]))
            p.drawRoundedRect(self.rect(), 13, 13)
        else:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(PALETTE.get("field") or PALETTE["bg_bottom"]))
            p.drawRoundedRect(self.rect(), 13, 13)
        pad = 3
        d = self.height() - pad * 2
        x = pad + self._knob * (self.width() - d - pad * 2)
        # 滑块：白芯 + 浅色主题下加一圈淡描边（白 knob 压在浅灰轨道上需要轮廓）
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#FFFFFF"))
        p.drawEllipse(int(round(x)), pad, d, d)
        if not self._checked and QColor(PALETTE["bg_top"]).lightness() >= 128:
            p.setPen(QPen(QColor(PALETTE["border"]), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(int(round(x)) + 1, pad + 1, d - 2, d - 2)


class FadeMenu(QMenu):
    """整体平滑淡入的菜单（右键 / 「更多」/ 托盘菜单共用）。

    弹层是独立原生窗口，只能整体调 ``windowOpacity``；透明度动画**延后一帧**再启动：
    showEvent 里菜单刚建好，首帧仍在逐行绘制，此时立刻推进透明度会露出「从上到下
    刷出来」的割裂感。先让菜单在不透明态同步绘制一帧，再回到 0 整块淡入。

    兜底：动画被中断/窗口提前销毁时，400ms 后强制恢复不透明，避免菜单「透明不可见」。
    """

    FADE_DELAY_MS = 16           # 首帧绘制完成后再启动（约一帧）
    FADE_FALLBACK_MS = 400       # 兜底恢复不透明

    def __init__(self, parent=None):
        super().__init__(parent)
        # 菜单也是玻璃表面：窗口改半透明 + QSS 底色让位，底由 paintEvent 自绘。
        # padding 必须显式保留（widget 级样式表会盖掉 app 级 QMenu 规则里的同名项，
        # 丢了它菜单项会贴到窗口边缘）。
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet(
            f"QMenu {{ background: transparent; border: none; padding: 5px; }}")
        glass.subscribe(self.update)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # 菜单项文字密集：同下拉弹层，填充更实
        glass.paint_surface(self, p, radius=RADIUS_MD, tint=PALETTE["elevated"],
                            min_alpha=0.85)
        p.end()
        super().paintEvent(e)        # 菜单项/分隔线/箭头（QSS 底已透明）

    def showEvent(self, e):
        super().showEvent(e)
        try:
            self._pop_seq = int(getattr(self, "_pop_seq", 0)) + 1
            seq = self._pop_seq
            self.setWindowOpacity(0.0)
            QTimer.singleShot(self.FADE_DELAY_MS, lambda: self._start_fade(seq))
            QTimer.singleShot(self.FADE_FALLBACK_MS, lambda: self._fade_guard(seq))
        except Exception:
            try:
                self.setWindowOpacity(1.0)
            except Exception:
                pass

    def _start_fade(self, seq: int):
        try:
            if seq != getattr(self, "_pop_seq", 0) or not self.isVisible():
                return
            self.setWindowOpacity(1.0)
            self.repaint()          # 不透明态同步整块绘制，动画期间不再逐行刷出
            self.setWindowOpacity(0.0)
            anim = QPropertyAnimation(self, b"windowOpacity", self)
            anim.setDuration(motion.POP_MS)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(motion.settle())
            self._pop_anim = anim   # 持有引用，避免被回收
            anim.start()
        except Exception:
            try:
                self.setWindowOpacity(1.0)
            except Exception:
                pass

    def _fade_guard(self, seq: int):
        """兜底：动画未跑完也必须可见（否则菜单会「透明」得像没弹出）。"""
        try:
            if seq == getattr(self, "_pop_seq", 0) and self.isVisible() \
                    and self.windowOpacity() < 1.0:
                self.setWindowOpacity(1.0)
        except Exception:
            pass


def _combo_scroll_qss() -> str:
    """下拉弹层内的极简滚动条（透明轨道 + 半透明淡灰滑块）。"""
    handle = QColor(PALETTE["text_secondary"])
    handle.setAlphaF(0.30)
    hover = QColor(PALETTE["text_secondary"])
    hover.setAlphaF(0.55)
    hb = f"rgba({handle.red()},{handle.green()},{handle.blue()},{handle.alpha()})"
    hv = f"rgba({hover.red()},{hover.green()},{hover.blue()},{hover.alpha()})"
    return (
        "QScrollBar:vertical { background: transparent; width: 10px;"
        " margin: 2px 2px 2px 0; }"
        f"QScrollBar::handle:vertical {{ background: {hb}; border-radius: 3px;"
        " min-height: 28px; margin: 0 2px; }"
        f"QScrollBar::handle:vertical:hover {{ background: {hv}; }}"
        "QScrollBar::sub-page:vertical, QScrollBar::add-page:vertical {"
        " background: transparent; }"
        "QScrollBar::sub-line:vertical, QScrollBar::add-line:vertical {"
        " background: transparent; height: 0; width: 0; }"
    )


#: 下拉弹层整体缩放系数：字体 / 行高 / 内边距 / 图标 / 对勾全部按它等比推导。
#: 动因（用户反馈「下拉菜单字体有点大」）：列表字号原取 FONT_BASE(14)，比下拉框
#: 本体（FONT_SMALL=12）大两档；缩到 12 后与本体对齐，行高与留白同比缩，
#: 避免「字小了但行还很高」的割裂。改这一个系数即可整体缩放。
POPUP_SCALE = 0.86


class _ComboRowDelegate(QStyledItemDelegate):
    """下拉项绘制：统一行高，并在当前生效项右侧画对勾（路径折线，不依赖字体）。

    尺寸全部由 `POPUP_SCALE` 等比推导，只改一个系数就能整体缩放。
    """

    ROW_H = max(20, round(30 * POPUP_SCALE))     # 30 → 26

    def sizeHint(self, opt, idx) -> QSize:
        s = super().sizeHint(opt, idx)
        return QSize(s.width(), max(self.ROW_H, int(s.height())))

    def paint(self, p: QPainter, opt, idx):
        k = POPUP_SCALE
        o = QStyleOptionViewItem(opt)
        self.initStyleOption(o, idx)
        o.text = ""                      # 文本交给父类按主题绘制
        style = o.widget.style() if o.widget is not None else None
        if style is not None:
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, o, p, o.widget)
        # 文本（含图标）
        pad_l, pad_r = round(10 * k), round(22 * k)
        icon_px, icon_adv = max(12, round(16 * k)), round(22 * k)
        text_rect = o.rect.adjusted(pad_l, 0, -pad_r, 0)
        icon = idx.data(Qt.ItemDataRole.DecorationRole)
        x = text_rect.left()
        if isinstance(icon, QIcon) and not icon.isNull():
            icon.paint(p, QRect(x, text_rect.center().y() - icon_px // 2, icon_px, icon_px))
            x += icon_adv
        fm = QFontMetrics(o.font)
        txt = fm.elidedText(str(idx.data(Qt.ItemDataRole.DisplayRole) or ""),
                            Qt.TextElideMode.ElideRight,
                            max(10, text_rect.right() - x))
        p.save()
        p.setPen(QColor(PALETTE["text"]))
        p.drawText(QRect(x, text_rect.top(), text_rect.right() - x, text_rect.height()),
                   int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), txt)
        p.restore()
        # 当前项对勾
        if idx.data(Qt.ItemDataRole.UserRole + 1):
            pen = QPen(QColor(PALETTE["primary"]))
            pen.setWidthF(1.8 * k)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.save()
            p.setPen(pen)
            cx = o.rect.right() - round(14 * k)
            cy = o.rect.center().y()
            a, b = 4 * k, 3 * k
            path = QPainterPath()
            path.moveTo(cx - a, cy)
            path.lineTo(cx - a / 4, cy + b)
            path.lineTo(cx + a, cy - b)
            p.drawPath(path)
            p.restore()


class ComboPopup(QFrame):
    """自绘下拉弹层：**整块**平滑弹出（淡入 + 轻微上浮），不依赖原生弹层窗口。

    为什么不用 QComboBox 原生弹层：原生弹层是独立原生窗口，只能动画 ``windowOpacity``，
    且首帧会在动画启动前先上屏 —— 肉眼就是「先闪一下再淡入」。

    为什么不用 QGraphicsOpacityEffect 淡入：它要求 Qt 每帧重绘整棵子树再合成，
    列表越长越容易掉帧，掉帧时用户看到的就是**一截一截从上往下刷出**的割裂感。

    本实现的做法（性能优先，且与帧率无关）：开场前先把整块内容 ``grab()`` 成一张位图，
    动画期间隐藏真实子控件、只按当前不透明度绘制这一张整块位图。整块内容的像素在动画
    开始前就已经定死，物理上不存在「逐行渲染」的中间态；每帧开销也从「重绘整棵列表」
    降为「一次位图 blit」。动画结束后再把真实子控件交还（内容与位图逐像素一致，切换不可见）。
    """

    GAP = 4                  # 与下拉框的间距
    RISE_PX = 6              # 展开位移（从下方 6px 浮起）
    MARGIN = 6               # 距窗口边缘的最小留白
    MAX_H = 440              # 弹层最大高度（约 16 行，超出在列表内滚动）
    MIN_ROWS = 3             # 最少按 3 行给高度：短列表也像个菜单，不挤成一条
    FADE_MS = motion.POP_MS  # 淡入时长（与动效注册表同源）
    # 收起比淡入更快：菜单/下拉收得利索，避免"拖泥带水"；也缩短"该关未关"的窗口
    FADE_OUT_MS = 120
    CLOSE_RISE_PX = 4        # 收起时下移的位移

    picked = pyqtSignal(int)

    def __init__(self, combo):
        super().__init__(combo.window())
        self.setObjectName("comboPopup")
        # 不用 QSS 背景：底必须由 paintEvent 自己画。QSS 背景由 Qt 在 paintEvent **之前**
        # 绘制，位图叠在它上面永远是 100% 不透明 —— 淡入会彻底失效（只剩"瞬间弹出"）。
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._combo = combo
        # 整块位图遮罩：动画期间真实子控件不绘制，只画这一张图
        self._snap: QPixmap | None = None
        self._veiled = False        # True = 正在以位图代替子控件绘制
        self._op = 1.0              # 位图不透明度 0~1
        self._from = QPoint()       # 本次动画的起始 / 目标位置
        self._to = QPoint()
        self._o0 = 1.0              # 本次动画的起止不透明度
        self._o1 = 1.0
        self._hide_on_end = False   # 收起动画：结束即隐藏
        self._anim = QVariantAnimation(self)
        self._anim.valueChanged.connect(self._on_step)
        self._anim.finished.connect(self._on_end)
        glass.subscribe(self.update)
        self._list = QListWidget(self)
        self._list.setObjectName("comboPopupList")
        self._list.setFrameShape(QFrame.Shape.NoFrame)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._list.setMouseTracking(True)      # ::item:hover 需要鼠标跟踪才会刷新
        self._list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self._list.setItemDelegate(_ComboRowDelegate(self._list))
        self._list.itemClicked.connect(lambda it: self._pick(it))
        lay = QVBoxLayout(self)
        _m = max(2, round(4 * POPUP_SCALE))      # 弹层内边距同比缩
        lay.setContentsMargins(_m, _m, _m, _m)
        lay.setSpacing(0)
        lay.addWidget(self._list)
        self._apply_theme()
        self.hide()

    # ---------- 主题 ----------
    def _apply_theme(self):
        self.setStyleSheet(
            f"#comboPopup {{ background: transparent; border: none; }}"   # 底自绘，见 paintEvent
            f"#comboPopupList {{ background: transparent; border: none; outline: none;"
            f" color: {PALETTE['text']};"
            f" font-size: {max(10, round(FONT_BASE * POPUP_SCALE))}px; }}"
            f"#comboPopupList::item {{"
            f" padding: {max(2, round(4 * POPUP_SCALE))}px {max(3, round(6 * POPUP_SCALE))}px;"
            f" border-radius: {RADIUS_XS}px; }}"
            f"#comboPopupList::item:hover {{ background: {PALETTE['hover']}; }}"
            + _combo_scroll_qss())

    # ---------- 内容 ----------
    def _reload(self):
        combo = self._combo
        cur = combo.currentIndex()
        self._list.clear()
        for i in range(combo.count()):
            it = QListWidgetItem(combo.itemText(i))
            ic = combo.itemIcon(i)
            if ic is not None and not ic.isNull():
                it.setIcon(ic)
            it.setData(Qt.ItemDataRole.UserRole, i)
            it.setData(Qt.ItemDataRole.UserRole + 1, i == cur)
            tip = combo.itemData(i, Qt.ItemDataRole.ToolTipRole)
            if tip:
                it.setToolTip(str(tip))
            self._list.addItem(it)
        if 0 <= cur < self._list.count():
            self._list.setCurrentRow(cur)
            self._list.scrollToItem(self._list.item(cur))

    def _content_width(self) -> int:
        combo = self._combo
        fm = QFontMetrics(self._list.font())
        w = 0
        has_icon = False
        for i in range(combo.count()):
            w = max(w, fm.horizontalAdvance(combo.itemText(i)))
            ic = combo.itemIcon(i)
            if ic is not None and not ic.isNull():
                has_icon = True
        # 左右内边距 + 对勾位：与 POPUP_SCALE 同源，缩放后不会挤出文字
        return (w + (round(26 * POPUP_SCALE) if has_icon else 0)
                + round(40 * POPUP_SCALE))

    def _target_rect(self) -> QRect:
        combo = self._combo
        host = self.parentWidget()
        if host is None:
            return QRect()
        rows = max(self.MIN_ROWS, self._list.count())
        content_h = min(self.MAX_H, rows * _ComboRowDelegate.ROW_H + 10)
        width = max(int(combo.width()), min(self._content_width(), 460))
        width = min(width, max(120, host.width() - 2 * self.MARGIN))
        top_left = combo.mapTo(host, QPoint(0, 0))
        x = min(max(self.MARGIN, top_left.x()),
                max(self.MARGIN, host.width() - width - self.MARGIN))
        below = top_left.y() + combo.height() + self.GAP
        above = top_left.y() - self.GAP - content_h
        if below + content_h <= host.height() - self.MARGIN:
            y = below
        elif above >= self.MARGIN:
            y = above
        else:
            y = max(self.MARGIN, host.height() - content_h - self.MARGIN)
        return QRect(x, y, width, content_h)

    # ---------- 整块位图（动画期间的唯一绘制源） ----------
    def _capture(self) -> QPixmap | None:
        """把整块内容渲染成一张位图。

        已是位图态（例如入场未结束就收起）时复用旧图，避免抓到一张没有子控件的空图。
        """
        if self._veiled and self._snap is not None:
            return self._snap
        try:
            self.ensurePolished()
            self._list.ensurePolished()
            lay = self.layout()
            if lay is not None:
                lay.activate()      # 先完成布局，位图尺寸与子控件位置才与最终画面一致
            return self.grab()
        except Exception:
            return None

    def _veil(self, on: bool):
        """进入/退出位图态：进入时隐藏真实子控件，退出时交还。"""
        self._veiled = on
        if on:
            self._list.hide()
        else:
            self._op = 1.0
            self._list.show()
        self.update()

    # ---------- 动画（单一进度驱动位置 + 不透明度，二者天然同步） ----------
    def _play(self, start: QPoint, end: QPoint, o0: float, o1: float, ms: int,
              hide_on_end: bool = False):
        self._anim.stop()
        self._from, self._to = QPoint(start), QPoint(end)
        self._o0, self._o1 = float(o0), float(o1)
        self._hide_on_end = hide_on_end
        self._anim.setDuration(ms)
        self._anim.setEasingCurve(motion.fade_out() if hide_on_end else motion.settle())
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._on_step(0.0)          # 同步首帧：交给动画引擎前就已是起始状态
        self._anim.start()

    def _on_step(self, t):
        t = float(t)
        self._op = self._o0 + (self._o1 - self._o0) * t
        y = self._from.y() + int(round((self._to.y() - self._from.y()) * t))
        if self.pos() != QPoint(self._to.x(), y):
            self.move(self._to.x(), y)
        self.update()

    def _on_end(self):
        if self._hide_on_end:
            self.hide()
            self._veil(False)
            self._snap = None
            try:
                if self._combo.isVisible():    # 焦点还给下拉框，键盘操作可继续
                    self._combo.setFocus(Qt.FocusReason.PopupFocusReason)
            except RuntimeError:
                pass
            return
        self._veil(False)           # 入场结束：位图与真实内容逐像素一致，切换不可见

    def paintEvent(self, e):
        if self._veiled:
            if self._snap is None:
                return              # 还没抓到整块位图：这一帧什么都不画，绝不上半成品
            # 动画期间只做一次整块位图合成：每帧开销恒定，与行数无关
            p = QPainter(self)
            p.setOpacity(max(0.0, min(1.0, self._op)))
            p.drawPixmap(0, 0, self._snap)
            return
        # 常驻态：玻璃底（磨砂 + 半透明 + 高光，与位图里的底是同一套像素，
        # 撤掉遮罩时不会「跳」；壁纸变化由 glass 的重绘通知兜住）
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # 列表文字密集：填充更实（0.85 兜底），背景文字不再透上来干扰阅读
        glass.paint_surface(self, p, radius=RADIUS_SM, tint=PALETTE["elevated"],
                            reflect=True, min_alpha=0.85)
        p.end()

    # ---------- 开合 ----------
    def open(self):
        self._reload()
        target = self._target_rect()
        self._snap = None
        self._op = 0.0
        self._veiled = True             # show 期间即便被要求绘制也什么都不画
        self.setGeometry(target)        # 先按最终尺寸布局，位图才与最终画面同尺寸
        # 显示前抓「弹层将要压住的那块真实画面」（此时弹层不可见，不会拍到自己）：
        # 自定义背景模式下玻璃底因此能反出下方的卡片与文字，而不只是壁纸
        glass.capture_scene(self)
        start = QPoint(target.x(), target.y() + self.RISE_PX)
        self.move(start)
        self.show()
        # 抓图必须在「常驻态」下做（底由 paintEvent 自绘，遮罩态不画底）。
        # 这三行是纯 Python 调用，不给事件循环任何绘制机会，因此不会有半成品上屏。
        self._veiled = False
        self._snap = self._capture()
        self._veil(True)
        self.raise_()
        self.setFocus(Qt.FocusReason.PopupFocusReason)
        self._guard(True)
        self._play(start, target.topLeft(), 0.0, 1.0, self.FADE_MS)
        # 兜底：动画时间轴被主线程重活卡住时，不能让弹层停在半透明/不可见状态
        QTimer.singleShot(self.FADE_MS + 150, self._finish_open_guard)
        # 兜底：动画时间轴被主线程重活卡住时，不能让弹层停在半透明/不可见状态
        QTimer.singleShot(self.FADE_MS + 150, self._finish_open_guard)

    def close_animated(self):
        if not self.isVisible():
            return
        self._guard(False)
        cur = self.geometry()
        self._snap = self._capture()    # 重新抓一张：淡出的是用户最后看到的画面
        self._veil(True)
        end = QPoint(cur.x(), cur.y() + self.CLOSE_RISE_PX)
        self._play(cur.topLeft(), end, float(self._op), 0.0,
                   self.FADE_OUT_MS, hide_on_end=True)
        # 兜底：动画时间轴可能被主线程的重活卡住（大面板上实测偶发，表现为弹层
        # "该关却不关"）。到点无条件收尾，不依赖 finished 一定送达。
        QTimer.singleShot(self.FADE_OUT_MS + 120, self._force_close)

    def _finish_open_guard(self):
        """淡入兜底：到点仍未淡完就直接置为常驻态（否则弹层看起来像"没弹出来"）。"""
        if not self.isVisible() or self._hide_on_end:
            return
        if self._op < 0.99:
            self._anim.stop()
            self._veil(False)

    def _finish_open_guard(self):
        """淡入兜底：到点仍未淡完就直接置为常驻态（否则弹层看起来像"没弹出来"）。"""
        if not self.isVisible() or self._hide_on_end:
            return
        if self._op < 0.99:
            self._anim.stop()
            self._veil(False)

    def _force_close(self):
        """兜底收尾：只在本该收起的场景生效，绝不打断正常的重新展开。"""
        if not self._hide_on_end:
            return
        if self.isVisible():
            self._anim.stop()
            self.hide()
            self._veil(False)
            self._snap = None

    def close_now(self):
        self._guard(False)
        self._anim.stop()
        self._veil(False)
        self._snap = None
        self.hide()

    # ---------- 关闭守卫（点外面 / Esc / 窗口变化） ----------
    def _guard(self, on: bool):
        app = QApplication.instance()
        if app is None:
            return
        try:
            if on:
                app.installEventFilter(self)
            else:
                app.removeEventFilter(self)
        except Exception:
            pass

    def _inside(self, gp: QPoint) -> bool:
        try:
            if self.rect().contains(self.mapFromGlobal(gp)):
                return True
        except Exception:
            return False
        try:
            combo = self._combo
            return combo.isVisible() and combo.rect().contains(combo.mapFromGlobal(gp))
        except Exception:
            return False

    def eventFilter(self, obj, ev):
        if not self.isVisible():
            return False
        t = ev.type()
        if t == QEvent.Type.MouseButtonPress:
            try:
                gp = ev.globalPosition().toPoint()
            except Exception:
                gp = QCursor.pos()
            if not self._inside(gp):
                self.close_animated()
        elif t == QEvent.Type.Wheel:
            try:
                gp = ev.globalPosition().toPoint()
            except Exception:
                gp = QCursor.pos()
            if not self._inside(gp):
                self.close_animated()
        elif t == QEvent.Type.KeyPress and ev.key() == Qt.Key.Key_Escape:
            self.close_animated()
            return True
        elif t in (QEvent.Type.Resize, QEvent.Type.Move, QEvent.Type.Hide) \
                and obj is self.parentWidget():
            self.close_now()
        return False

    def keyPressEvent(self, e):
        k = e.key()
        if k == Qt.Key.Key_Escape:
            self.close_animated()
            e.accept()
            return
        if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._pick(self._list.currentItem())
            e.accept()
            return
        if k in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            row = self._list.currentRow()
            row += -1 if k == Qt.Key.Key_Up else 1
            row = max(0, min(row, self._list.count() - 1))
            self._list.setCurrentRow(row)
            self._list.scrollToItem(self._list.item(row))
            e.accept()
            return
        super().keyPressEvent(e)

    def _pick(self, item):
        if item is None:
            self.close_animated()
            return
        idx = item.data(Qt.ItemDataRole.UserRole)
        self.picked.emit(int(idx) if idx is not None else -1)
        self.close_animated()


class ArrowComboBox(QComboBox):
    """带旋转动画下拉箭头的 QComboBox：收起时 chevron 朝下，展开后旋转 180° 朝上。

    为什么必须自绘：一旦样式表接管了 `QComboBox::drop-down`，Qt 就**不再绘制原生箭头**；
    而 QSS 又画不出三角 —— 用「透明左右边框 + 实心上边框」拼三角会被 Qt 渲染成一个
    实心小方块（用户反馈「下拉箭头是个方块」）。自绘是同时满足
    「箭头存在 / 形状正确 / 随主题与 hover 换色」的唯一做法。

    箭头预留宽度与 QSS `::drop-down` 取同一 Design Token（COMBO_ARROW_W），两处不会漂移。
    颜色在绘制时实时读 PALETTE，因此主题切换无需重建控件。
    """

    ARROW_W = COMBO_ARROW_W      # 与 QSS ::drop-down 宽度同源
    ARROW_HALF = 4.0             # chevron 半宽（px）

    def __init__(self, parent=None):
        super().__init__(parent)
        # 本体玻璃底：默认开启；底色由 QSS 让位（否则会盖住玻璃）。
        # 需要实色本体的下拉（对话框里的输入型下拉）可 set_glass_body(False) 关掉。
        self._glass_body = True
        self._glass_tint = None
        self._pressed = False           # 按下态：只用于内侧叠色（描边保持不动）
        self._arrow_angle = 0.0        # 箭头旋转角度：0=收起，180=展开
        self._arrow_open = False
        self._arrow_hover = False
        self._arrow_anim = QPropertyAnimation(self, b"arrowAngle", self)
        # 招 5：箭头旋转是「微反馈」性格（motion.hover，110ms 快反馈）
        self._arrow_anim.setDuration(motion.HOVER_MS)
        self._arrow_anim.setEasingCurve(motion.hover())

    # ---------- 动画属性 ----------
    def get_arrowAngle(self) -> float:
        return self._arrow_angle

    def set_arrowAngle(self, a: float):
        self._arrow_angle = a
        self.update()

    arrowAngle = pyqtProperty(float, get_arrowAngle, set_arrowAngle)

    # ---------- 展开/收起 ----------
    def showPopup(self):
        """弹出下拉：不用 Qt 原生弹层，改用同一窗口内的子控件浮层（整体淡入）。

        原生弹层是独立原生窗口，只能动画 windowOpacity：首个全不透明帧会先上屏，
        视觉上仍是「瞬间弹出 / 逐行刷出」。子控件浮层由 Qt 直接合成，
        透明度渐变一定作用在**整块内容**上，见 ``ComboPopup``。
        """
        if self.count() <= 0 or not self.isEnabled():
            return
        self._ensure_popup()
        self._run_arrow(True)
        self._popup.open()

    def hidePopup(self):
        self._run_arrow(False)
        pop = getattr(self, "_popup", None)
        if pop is not None:
            try:
                pop.close_animated()
            except RuntimeError:
                self._popup = None

    def isPopupOpen(self) -> bool:
        """下拉是否展开（原生 view 不再使用，宿主判断应走本方法）。"""
        pop = getattr(self, "_popup", None)
        if pop is None:
            return False
        try:
            return pop.isVisible()
        except RuntimeError:
            self._popup = None
            return False

    def _ensure_popup(self):
        pop = getattr(self, "_popup", None)
        if pop is not None:
            try:
                pop.isVisible()
                return
            except RuntimeError:
                self._popup = None
        pop = ComboPopup(self)
        pop.picked.connect(self._on_popup_pick)
        try:
            self.destroyed.connect(pop.deleteLater)
        except Exception:
            pass
        self._popup = pop

    def _on_popup_pick(self, idx: int):
        if 0 <= int(idx) < self.count():
            self.setCurrentIndex(int(idx))
            self.activated.emit(int(idx))
        self.hidePopup()

    def mousePressEvent(self, e):
        # 点击下拉框本体：已展开则收起（与原生下拉一致的开关手感）
        if e.button() == Qt.MouseButton.LeftButton and self.isPopupOpen():
            self.hidePopup()
            e.accept()
            return
        self._pressed = True
        self.update()
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        self._pressed = False
        self.update()
        super().mouseReleaseEvent(e)

    def focusInEvent(self, e):
        super().focusInEvent(e)
        self.update()          # 聚焦也只在内部叠色，需重绘一次

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self.update()

    def hideEvent(self, e):
        # 控件被隐藏（切页 / 弹窗关闭）时下拉不能停留在屏幕上
        try:
            pop = getattr(self, "_popup", None)
            if pop is not None:
                pop.close_now()
        except RuntimeError:
            self._popup = None
        super().hideEvent(e)

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

    def apply_theme(self):
        """主题切换后重绘箭头（颜色在 paintEvent 实时取 PALETTE，只需重绘）。"""
        self.update()

    def set_glass_body(self, on: bool = True, tint: str | None = None) -> None:
        """控制本体是否绘制玻璃底（tint 为空时取主题 FIELD 色）。"""
        self._glass_body = bool(on)
        if tint is not None:
            self._glass_tint = tint
        self.update()

    def paintEvent(self, e):
        if self._glass_body and glass.enabled():
            g = QPainter(self)
            g.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            glass.paint_surface(self, g, radius=RADIUS_SM,
                                tint=self._glass_tint or PALETTE["field"])
            # 悬停 / 按下 / 聚焦只叠内侧对比色：描边高光恒定（QSS 里也禁止再铺底）
            if self._arrow_hover or self._pressed or self.hasFocus():
                state = ("press" if self._pressed
                         else "focus" if self.hasFocus() else "hover")
                glass.paint_state(self, g, RADIUS_SM, state)
            g.end()
        super().paintEvent(e)
        # 在右侧下拉区自绘旋转箭头（∨ 形折线，展开后旋转 180° 变为 ∧）
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx = self.rect().right() - self.ARROW_W / 2
        cy = self.rect().center().y()
        p.save()
        p.translate(cx, cy)
        p.rotate(self._arrow_angle)
        color = PALETTE["primary"] if (self._arrow_open or self._arrow_hover) \
            else PALETTE["text_secondary"]
        pen = QPen(QColor(color))
        pen.setWidthF(1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        s = self.ARROW_HALF
        path = QPainterPath()
        path.moveTo(-s, -s * 0.5)
        path.lineTo(0.0, s * 0.5)
        path.lineTo(s, -s * 0.5)
        p.drawPath(path)
        p.restore()
        p.end()


class Card(QWidget):
    """浮层内的内容卡片：圆角底 + 细描边（纯黑+淡灰+白+深蓝四色系）。

    QSS 里内联了主题色，**必须**提供 `apply_theme()`：卡片是在页面构建期建好的，
    主题切换时控件不会被重建，只有重新按当前 PALETTE 生成 QSS 才能跟着换色
    （用户反馈的「深色模式下卡片仍是浅色」就是漏了这一步）。
    """

    def __init__(self, title="", parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self._title_label = None
        # 底改由 paintEvent 自绘（磨砂 + 半透明填充 + 边缘高光，强度见 glass.py）
        glass.subscribe(self.update)

        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(18, 16, 18, 16)
        self.layout.setSpacing(12)

        if title:
            title_label = QLabel(title)
            title_label.setObjectName("cardTitle")
            self._title_label = title_label
            self.layout.addWidget(title_label)
        self.apply_theme()

    def apply_theme(self):
        """按当前 PALETTE 重建标题样式并重绘底（幂等，主题切换时由外层统一调用）。"""
        if self._title_label is not None:
            self._title_label.setStyleSheet(
                f"font-size: 16px; font-weight: 700; color: {PALETTE['text']};")
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        glass.paint_surface(self, p, radius=RADIUS_MD, tint=PALETTE["card"])
        p.end()

    def mousePressEvent(self, e):
        glass.invalidate(self)
        super().mousePressEvent(e)

class GlassPanel(QWidget):
    """通用玻璃面板：磨砂底 + 半透明填充 + 边缘高光（子控件样式各自独立）。

    只负责「底」；圆角与描边不进 QSS —— 与 Card 同一套绘制，观感一致。
    """

    def __init__(self, parent=None, *, radius: float | None = None,
                 tint: str | None = None, tint_key: str | None = None,
                 min_alpha: float = 0.0):
        super().__init__(parent)
        self._g_radius = RADIUS_MD if radius is None else float(radius)
        self._g_tint = tint
        # 色板键而不是颜色快照：控件活过主题切换时必须取实时色板，否则切深色
        # 后仍留着浅色底（历史 bug，见 tests/test_copilot_panel.py 的回归用例）
        self._g_key = tint_key
        self._g_min = max(0.0, float(min_alpha))
        glass.subscribe(self.update)

    def resolved_tint(self) -> str | None:
        return PALETTE[self._g_key] if self._g_key else self._g_tint

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        glass.paint_surface(self, p, radius=self._g_radius, tint=self.resolved_tint(),
                            min_alpha=self._g_min)
        p.end()

    def mousePressEvent(self, e):
        # 点击后重建一次玻璃底：极端重绘竞态下可能缓存到没有磨砂的帧
        glass.invalidate(self)
        super().mousePressEvent(e)


class GlassPill(QWidget):
    """玻璃胶囊基类：自绘磨砂底 + 描边高光，圆角取半高（永远是胶囊）。

    子控件（图标 / 文案 / 落尖角）由子类摆放；底色来源可注入（`tint_fn`），因为
    面板侧的 `_base_color` 才是当前皮肤的真实底色。悬停/按下只在**内部**叠色，
    边缘高光不动（与 GlassIconButton 同一套规则）。
    """

    def __init__(self, parent=None, *, tint_fn=None):
        super().__init__(parent)
        self._g_tint_fn = tint_fn
        self._g_hover = False
        # QWidget 没有 isDown()（那是 QAbstractButton 的）：按下态自己记，
        # 直接在 paintEvent 里调 isDown() 会抛 AttributeError → 进程 abort
        self._g_pressed = False
        # 纯 QWidget 默认会先用调色板背景填一遍，会把圆角外的部分涂成实色
        # （玻璃底就变成了"方块里套圆角"）。显式关掉系统背景填充。
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        glass.subscribe(self.update)

    # ---------- 底色 ----------
    def glass_tint(self) -> str:
        if self._g_tint_fn is not None:
            try:
                return self._g_tint_fn()
            except Exception:
                pass
        return PALETTE["card"]

    def paint_glass(self, p: QPainter) -> None:
        """画玻璃底；子类要叠状态色时在自己 paintEvent 里接着调用 fill_inner。"""
        r = self.height() / 2.0
        glass.paint_surface(self, p, radius=r, tint=self.glass_tint())
        if self._g_hover or self._g_pressed:
            glass.paint_state(self, p, r, "press" if self._g_pressed else "hover")

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.paint_glass(p)
        p.end()

    # ---------- 交互 ----------
    def enterEvent(self, e):
        self._g_hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._g_hover = False
        self.update()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        self._g_pressed = True
        glass.invalidate(self)
        self.update()
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        self._g_pressed = False
        self.update()
        super().mouseReleaseEvent(e)


class GlassIconButton(QPushButton):
    """圆形玻璃图标按钮（输入行 / 侧栏入口 / 工具条共用）。

    底由玻璃层绘制：磨砂 + 半透明填充 + 描边高光；QSS 只留图标与 hover 反馈
    （底色一律透明，否则会把玻璃盖掉）。尺寸与圆角绑定，永远是正圆。
    """

    def __init__(self, icon: QIcon, diameter: int = 34, parent=None, *,
                 tint_key: str = "elevated", hover_tint_key: str = "primary_light",
                 reflect: bool = False):
        super().__init__(icon, "", parent)
        self._g_tint_key = tint_key
        # hover_tint_key 仅为兼容保留：状态反馈走内侧叠色，底色与描边恒定
        self._g_hover = False
        self._g_reflect = bool(reflect)
        self.setFixedSize(int(diameter), int(diameter))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAutoDefault(False)
        self.setStyleSheet(
            "QPushButton, QPushButton:hover, QPushButton:pressed, QPushButton:focus"
            " { background: transparent; border: none; }")
        glass.subscribe(self.update)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # 底色恒定：悬停/按下只在内侧叠对比色。以前换 tint 键会连整块贴图
        # （含描边高光）一起重建，边缘随之变淡 —— 看起来像"悬停就丢了高光"。
        tint = PALETTE.get(self._g_tint_key) or PALETTE["elevated"]
        r = self.width() / 2.0
        glass.paint_surface(self, p, radius=r, tint=tint, reflect=self._g_reflect)
        if self._g_hover or self.isDown():
            glass.paint_state(self, p, r, "press" if self.isDown() else "hover")
        p.end()
        super().paintEvent(e)          # 图标（QSS 底已透明）

    def mousePressEvent(self, e):
        glass.invalidate(self)
        super().mousePressEvent(e)

    def enterEvent(self, e):
        self._g_hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._g_hover = False
        self.update()
        super().leaveEvent(e)


class GlassButton(QPushButton):
    """圆形玻璃按钮：透明底 + 左上/右下角高光 + 真实背景反射。

    与普通按钮的唯一区别是「底」：QSS 里底色全透明，底由 glass.paint_surface 绘制
    （磨砂 + 半透明填充 + 角部高光）。图标仍由基类画，鼠标/焦点/快捷键行为不变。
    """

    def __init__(self, icon: QIcon, diameter: int = 40, parent=None, *,
                 tint_key: str = "elevated", hover_tint_key: str = "primary_light",
                 reflect: bool = True):
        super().__init__(icon, "", parent)
        self._g_tint_key = tint_key
        # hover_tint_key 仅为兼容保留：状态反馈走内侧叠色，底色与描边恒定
        self._g_reflect = bool(reflect)
        self._g_hover = False
        self._g_last_capture = 0.0
        self.setFixedSize(int(diameter), int(diameter))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAutoDefault(False)
        # 底全透明：不让 QSS 抢走玻璃底，也不给 hover/pressed 铺色
        self.setStyleSheet(
            "QPushButton, QPushButton:hover, QPushButton:pressed, QPushButton:focus"
            " { background: transparent; border: none; }")
        glass.subscribe(self.update)

    def radius(self) -> float:
        return self.width() / 2.0

    def refresh_backdrop(self, min_interval_ms: int = 120):
        """背景变了（滚动/改尺寸/换肤）后重抓反射帧；按时间节流，滚动时不过度抓屏。"""
        if not self._g_reflect:
            return
        now = time.monotonic() * 1000.0
        if now - self._g_last_capture < max(0, int(min_interval_ms)):
            return
        self._g_last_capture = now
        glass.capture_scene(self)

    # ---------- 事件 ----------
    def showEvent(self, e):
        super().showEvent(e)
        # 不能在 showEvent 里同步抓：抓图需要临时隐藏自己，而此刻控件正在被显示，
        # 重入会直接把进程打崩。投递到下一回合（首帧先按壁纸层兜底，随后换上反射）。
        QTimer.singleShot(0, self._capture_later)

    def _capture_later(self):
        try:
            self.refresh_backdrop(0)
        except RuntimeError:
            pass            # 控件已被销毁（弹层式用法）：忽略迟到的抓图

    def mousePressEvent(self, e):
        glass.invalidate(self)
        super().mousePressEvent(e)

    def enterEvent(self, e):
        self._g_hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._g_hover = False
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # 底与描边恒定；悬停/按下只叠内部填充，边缘高光不变
        tint = PALETTE.get(self._g_tint_key) or PALETTE["elevated"]   # 防主题缺键
        glass.paint_surface(self, p, radius=self.radius(), tint=tint,
                            reflect=self._g_reflect)
        if self._g_hover or self.isDown():
            glass.paint_state(self, p, self.radius(),
                              "press" if self.isDown() else "hover")
        p.end()
        super().paintEvent(e)           # 图标/文字（QSS 底已透明）


class PrimaryButton(QPushButton):
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(40)

class SecondaryButton(QPushButton):
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setObjectName("secondary")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(40)


class _BrandFooterPlacer(QObject):
    """左下角品牌字样定位器：跟随窗口 resize 保持在左下角"""

    def __init__(self, foot: QLabel, dialog: QDialog):
        super().__init__(dialog)
        self._foot, self._dialog = foot, dialog
        dialog.installEventFilter(self)
        QTimer.singleShot(0, self._place)

    def _place(self):
        f, d = self._foot, self._dialog
        f.adjustSize()
        f.move(8, d.height() - f.height() - 8)

    def eventFilter(self, obj, ev):
        from PyQt6.QtCore import QEvent
        if obj is self._dialog and ev.type() == QEvent.Type.Resize:
            self._place()
        return False


def add_brand_footer(dialog: QDialog, overlay: bool = False) -> None:
    """在所有对话框底部左下角追加 'Create By Xiaozhu' 品牌字样。

    overlay=False：QVBoxLayout 根布局直接在末尾追加；
    其他布局（QFormLayout）或 overlay=True（主面板等复杂布局）
    用绝对定位叠加在左下角并跟随窗口缩放。
    """
    from PyQt6.QtWidgets import QFormLayout
    lay = dialog.layout()
    if lay is None:
        return
    foot = QLabel("Create By Xiaozhu")
    foot.setObjectName("brandFooter")
    foot.setStyleSheet(f"color: {PALETTE['text_secondary']}; font-size: 10px; background: transparent;")
    foot.setAlignment(Qt.AlignmentFlag.AlignLeft)
    if overlay or not isinstance(lay, QVBoxLayout):
        foot.setParent(dialog)
        _BrandFooterPlacer(foot, dialog)
        foot.show()
        return
    lay.addWidget(foot)


class DataDirDialog(QDialog):
    """迁移前勾选要一并移动的数据目录（AppData/文档等安装目录之外的目录）"""

    def __init__(self, candidates, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_ui("选择要一并迁移的数据目录"))
        self.setMinimumWidth(600)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        tip = QLabel(_ui("检测到以下与安装目录之外的关联数据目录，勾选后将一并移动到目标盘："))
        tip.setWordWrap(True)
        tip.setStyleSheet(f"color: {PALETTE['text']}; font-size: 13px; font-weight: 600;")
        layout.addWidget(tip)

        self._boxes = []
        self._items = []
        for p in candidates:
            cb = QCheckBox(str(p))
            cb.setChecked(True)
            cb.setStyleSheet("font-size: 13px; padding: 4px 0;")
            self._boxes.append(cb)
            self._items.append(p)
            layout.addWidget(cb)

        hint = QLabel(_ui("提示：如微信/QQ 的聊天记录目录。迁移后若应用找不到数据，请在应用设置内重新指定该目录。"))
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {PALETTE['text_secondary']}; font-size: 11px;")
        layout.addWidget(hint)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        skip_btn = QPushButton(_ui("仅迁移主目录"))
        skip_btn.clicked.connect(lambda: self.done(QDialog.DialogCode.Rejected))
        btn_layout.addWidget(skip_btn)
        ok_btn = PrimaryButton(_ui("迁移勾选目录"))
        ok_btn.clicked.connect(self.accept)
        btn_layout.addWidget(ok_btn)
        layout.addLayout(btn_layout)

    def selected(self):
        return [p for cb, p in zip(self._boxes, self._items) if cb.isChecked()]


class UninstallConfirmDialog(QDialog):
    """强力卸载前展示删除清单并要求确认"""

    def __init__(self, plan, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_ui("确认强力卸载"))
        self.setMinimumWidth(620)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        head = QLabel(_uif("将强力卸载 <b>{a0}</b>（{a1}）", a0=plan.app.name, a1=plan.app.app_type))
        head.setStyleSheet(f"font-size: 15px; color: {PALETTE['text']};")
        layout.addWidget(head)

        if plan.is_uwp:
            info = QLabel(
                _uim("UWP 应用包：<b>{pkg}</b>\n"
                     "将调用 Remove-AppxPackage 卸载该应用及其数据。")
                .format(pkg=plan.package_name)
            )
            info.setWordWrap(True)
            info.setStyleSheet(f"color: {PALETTE['text_secondary']}; font-size: 13px;")
            layout.addWidget(info)
        else:
            self._add_section(layout, _ui("安装根目录（将删除）"), [str(plan.root)])
            native = getattr(plan, "native_uninstaller", None)
            if native:
                native_exe = native[0] if isinstance(native, tuple) else native
                self._add_section(
                    layout, _ui("自带卸载器（将优先调用）"),
                    [native_exe, _ui("运行完成后再执行下方清理，以正确保留卸载钩子。")],
                )
            if plan.data_dirs:
                self._add_section(layout, _ui("数据/存档/聊天记录目录（将删除）"), [str(d) for d in plan.data_dirs])
            else:
                self._add_section(layout, _ui("数据目录"), [_ui("未检测到关联数据目录")])
            self._add_section(layout, _ui("其他清理"), [
                f"快捷方式：{len(plan.shortcuts)} 个",
                f"注册表关联项：{len(plan.registry_entries)} 处",
            ])

        warn = QLabel(_ui("此操作不可恢复！以上内容将被永久删除。"))
        warn.setWordWrap(True)
        warn.setStyleSheet(f"color: {PALETTE['danger']}; font-size: 13px; font-weight: 700;")
        layout.addWidget(warn)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        cancel_btn = QPushButton(_ui("取消"))
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)
        ok_btn = QPushButton(_ui("确认卸载"))
        ok_btn.setStyleSheet(
            f"QPushButton {{ background-color: {PALETTE['danger']}; color: white; font-weight: 700; "
            "border: none; border-radius: 8px; padding: 8px 22px; }"
            f"QPushButton:hover {{ background-color: {_danger_hover()}; }}"
        )
        ok_btn.clicked.connect(self.accept)
        btn_layout.addWidget(ok_btn)
        layout.addLayout(btn_layout)
        add_brand_footer(self)

    def _add_section(self, layout, title, lines):
        tl = QLabel(title)
        tl.setStyleSheet(f"color: {PALETTE['text']}; font-size: 12px; font-weight: 700; margin-top: 4px;")
        layout.addWidget(tl)
        for line in lines:
            l = QLabel(line)
            l.setWordWrap(True)
            l.setStyleSheet(f"color: {PALETTE['text_secondary']}; font-size: 12px;")
            layout.addWidget(l)


class AppItemDelegate(QStyledItemDelegate):
    """自绘应用列表项：真实图标/首字符、类型标签、名称、路径、大小，选中态完整渲染"""
    ROW_HEIGHT = 68

    def __init__(self, parent=None):
        super().__init__(parent)
        self._provider = QFileIconProvider()
        self._icon_cache = {}      # id(app) -> QIcon
        self._source_cache = {}    # id(app) -> 图标源文件路径(str | None)

    def sizeHint(self, option, index):
        return QSize(0, self.ROW_HEIGHT)

    def paint(self, painter, option, index):
        app = index.data(Qt.ItemDataRole.UserRole)
        if not app:
            super().paint(painter, option, index)
            return

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect

        # --- 背景（选中/悬停/默认）---
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        bg_rect = QRect(rect.left() + 3, rect.top() + 3, rect.width() - 6, rect.height() - 6)
        if selected:
            painter.setBrush(QColor(PALETTE["primary_light"]))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(bg_rect, 12, 12)
            # 左侧选中指示条
            painter.setBrush(QColor(PALETTE["primary_hover"]))
            painter.drawRoundedRect(QRect(rect.left() + 3, rect.top() + 14, 4, rect.height() - 28), 2, 2)
        elif hovered:
            painter.setBrush(QColor(PALETTE["hover"]))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(bg_rect, 12, 12)

        # --- 图标 ---
        icon_rect = QRect(rect.left() + 16, rect.top() + (rect.height() - 36) // 2, 36, 36)
        icon = self._icon_for(app)
        if icon is not None and not icon.isNull():
            painter.drawPixmap(icon_rect, icon.pixmap(36, 36))
        else:
            # 首字符圆形占位
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(PALETTE["primary"]))
            painter.drawEllipse(icon_rect)
            painter.setPen(QColor("white"))
            char_font = painter.font()
            char_font.setBold(True)
            char_font.setPixelSize(16)
            painter.setFont(char_font)
            first = (app.name[0] if app.name else "?").upper()
            painter.drawText(icon_rect, Qt.AlignmentFlag.AlignCenter, first)

        # --- 文本区 ---
        text_left = rect.left() + 64
        text_right = rect.right() - 14
        text_top = rect.top() + (rect.height() - 40) // 2

        # 第二行：类型标签 + 路径
        type_color = PALETTE["primary"] if app.app_type == "Win32" else PALETTE["warning"]
        if app.app_type == "自定义":
            type_color = PALETTE["text_secondary"]
        tag_width, tag_height = 38, 16
        tag_rect = QRect(text_left, text_top + 26, tag_width, tag_height)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(type_color))
        painter.drawRoundedRect(tag_rect, 3, 3)
        painter.setPen(QColor("white"))
        tag_font = painter.font()
        tag_font.setPixelSize(10)
        tag_font.setBold(True)
        painter.setFont(tag_font)
        painter.drawText(tag_rect, Qt.AlignmentFlag.AlignCenter, app.app_type)

        # 路径（elide）
        path_x = tag_rect.right() + 8
        path_font = QFont(painter.font())
        path_font.setPixelSize(11)
        path_metrics = QFontMetrics(path_font)
        path_text = str(app.install_location)
        path_width = text_right - path_x
        elided_path = path_metrics.elidedText(path_text, Qt.TextElideMode.ElideMiddle, max(0, path_width))
        painter.setPen(QColor(PALETTE["text_secondary"]))
        painter.setFont(path_font)
        painter.drawText(QRect(path_x, text_top + 23, max(0, path_width), 20),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, elided_path)

        # 第一行：名称（elide）
        size_text = self._format_size(app.size_bytes)
        size_font = QFont(painter.font())
        size_font.setPixelSize(11)
        size_metrics = QFontMetrics(size_font)
        size_width = size_metrics.horizontalAdvance(size_text) + 4
        name_font = QFont(painter.font())
        name_font.setPixelSize(13)
        name_font.setBold(True)
        name_metrics = QFontMetrics(name_font)
        name_max = text_right - text_left - size_width - 8
        elided_name = name_metrics.elidedText(app.name, Qt.TextElideMode.ElideRight, max(0, name_max))
        painter.setPen(QColor(PALETTE["text"]))
        painter.setFont(name_font)
        painter.drawText(QRect(text_left, text_top, max(0, name_max), 22),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, elided_name)

        # 大小（右对齐）
        painter.setPen(QColor(PALETTE["text_secondary"]))
        painter.setFont(size_font)
        painter.drawText(QRect(text_right - size_width, text_top, size_width, 22),
                         Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, size_text)

        painter.restore()

    def _icon_for(self, app) -> QIcon:
        # 仅读缓存；未命中返回空图标（paint 用首字符占位），图标由后台线程预取后填充
        return self._icon_cache.get(id(app), QIcon())

    def _icon_source(self, app) -> str | None:
        return self._source_cache.get(id(app))

    def preload_icon_source(self, app) -> str | None:
        """后台线程调用：只做目录搜索 IO，不创建 QIcon（Qt 界面对象需在主线程创建）"""
        key = id(app)
        if key not in self._source_cache:
            try:
                self._source_cache[key] = self._find_icon_file(app)
            except Exception:
                self._source_cache[key] = None
        return self._source_cache[key]

    def apply_icon(self, app) -> None:
        """主线程调用：按已缓存的图标源构建 QIcon，写入缓存"""
        key = id(app)
        if key not in self._icon_cache:
            self._icon_cache[key] = self._resolve_icon(app)

    def _resolve_icon(self, app) -> QIcon:
        source = self._icon_source(app)
        if not source:
            return QIcon()
        # 1. 系统文件图标（exe/ico/dll 直接有效）
        try:
            icon = self._provider.icon(QFileInfo(source))
            if not icon.isNull():
                return icon
        except Exception:
            pass
        # 2. ExtractIconEx 从 exe/dll/ico 直接提取图标资源
        pix = self._extract_icon(source)
        if pix is not None:
            return QIcon(pix)
        # 3. UWP logo png 直接加载
        if source.lower().endswith(".png"):
            pix = QPixmap(source)
            if not pix.isNull():
                return QIcon(pix)
        return QIcon()

    def _find_icon_file(self, app) -> str | None:
        """解析图标源文件：DisplayIcon 指定文件 > 安装目录内评分最佳 exe/ico/UWP logo"""
        # 1. 注册表 DisplayIcon 直接指定
        if app.app_type != "UWP" and app.executable:
            f = self._parse_display_icon(app.executable)
            if f:
                return f
        base = app.install_location
        if not base or not base.is_dir():
            return None
        # 2. 目录内搜索（深度<=2），按与 app 名称的匹配度评分
        name = (app.name or "").lower()
        best, best_score = None, -1
        try:
            for dirpath, dirnames, filenames in os.walk(base):
                depth = len(Path(dirpath).relative_to(base).parts)
                if depth > 2:
                    dirnames.clear()
                    continue
                # 跳过目录联接，防止循环跟随（低版本 Python 无 isjunction 时回退）
                for d in list(dirnames):
                    try:
                        if os.path.isjunction(os.path.join(dirpath, d)):
                            dirnames.remove(d)
                    except (AttributeError, OSError):
                        pass
                for fn in filenames:
                    low = fn.lower()
                    stem = fn[:-4]
                    is_png = low.endswith(".png") and app.app_type == "UWP"
                    if not (low.endswith(".exe") or low.endswith(".ico") or is_png):
                        continue
                    if low.endswith(".exe") and stem.lower() in _BANNED_EXE:
                        continue
                    if is_png:
                        # UWP logo 文件名与 app 名无关，按命名 hint 给分；splash 图排除
                        s = stem.lower()
                        if "splash" in s:
                            continue
                        score = 70 - depth * 5 if (s in _UWP_LOGO_HINTS or "logo" in s or "icon" in s) else -1
                    else:
                        score = self._score_name(stem, name) - depth * 5
                    if score > best_score:
                        best_score = score
                        best = str(Path(dirpath) / fn)
        except Exception:
            pass
        return best

    @staticmethod
    def _score_name(stem: str, app_name: str) -> int:
        """文件主名与 app 名称的匹配得分，越高越可能是主程序"""
        stem, name = stem.lower(), app_name.lower()
        if not stem:
            return -1
        if stem == name:
            return 100
        if name.startswith(stem) or stem.startswith(name):
            return 80
        if stem in name or name in stem:
            return 60
        return 10

    @staticmethod
    def _parse_display_icon(raw: str) -> str | None:
        """解析注册表 DisplayIcon：支持 "path",0、path,-101、裸路径，含环境变量"""
        if not raw:
            return None
        raw = raw.strip()
        m = re.search(r'"([^"]+)"', raw)
        if m:
            p = Path(os.path.expandvars(m.group(1).strip()))
            return str(p) if p.is_file() else None
        if "," in raw:
            p = Path(os.path.expandvars(raw.split(",")[0].strip()))
            if p.is_file():
                return str(p)
        p = Path(os.path.expandvars(raw))
        if p.is_file():
            return str(p)
        m = re.search(r"([A-Za-z]:[^,]+?\.(?:exe|ico|dll))", raw, re.IGNORECASE)
        if m:
            p = Path(os.path.expandvars(m.group(1)))
            return str(p) if p.is_file() else None
        return None

    @staticmethod
    def _extract_icon(path: str):
        """ExtractIconEx 提取文件图标资源，返回 QPixmap；失败返回 None"""
        try:
            import win32gui
            large, small = win32gui.ExtractIconEx(path, 0)
            handles = list(large) + list(small)
            if not handles:
                return None
            pix = QPixmap.fromWinHICON(handles[0])
            for h in handles:
                win32gui.DestroyIcon(h)
            return pix if not pix.isNull() else None
        except Exception:
            return None

    @staticmethod
    def _format_size(size):
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} PB"


class ToastNotification(QWidget):
    """屏幕右下角（任务栏上方）自定义滑出通知弹窗（非系统通知/原生对话框）

    - 无边框置顶、不抢焦点，圆角卡片 + 阴影
    - 从屏幕右侧向左滑入，停留后向右滑出并淡出
    - 可点击 ✕ 立即关闭
    """

    _MARGIN = 16
    _WIDTH = 404

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(self._WIDTH)

        # 滑入：从右侧外平移到目标位置（招 5：落定性格，240ms 内完成不等候）
        self._anim_in = QPropertyAnimation(self, b"pos", self)
        self._anim_in.setDuration(motion.SETTLE_MS)
        self._anim_in.setEasingCurve(motion.settle())
        # 滑出：向右平移 + 淡出（招 5：消退性格，快于滑入）
        self._anim_out = QPropertyAnimation(self, b"pos", self)
        self._anim_out.setDuration(motion.FADE_OUT_MS)
        self._anim_out.setEasingCurve(motion.fade_out())
        self._fade_out = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade_out.setDuration(motion.FADE_OUT_MS)
        self._fade_out.setStartValue(1.0)
        self._fade_out.setEndValue(0.0)
        self._out_group = QParallelAnimationGroup(self)
        self._out_group.addAnimation(self._anim_out)
        self._out_group.addAnimation(self._fade_out)
        self._out_group.finished.connect(self.hide)

        self._stay = QTimer(self)
        self._stay.setSingleShot(True)
        self._stay.timeout.connect(self._slide_out)
        self._build_ui()

    def _build_ui(self):
        # 通知是独立窗口（背后是桌面，没有可采样的磨砂底）：只叠半透明填充 +
        # 边缘高光，并给足兜底不透明度，保证文字在花哨桌面壁纸上也可读
        root = GlassPanel(self, radius=14, tint_key="card", min_alpha=0.85)
        root.setObjectName("toast")
        self._root = root
        shadow = QGraphicsDropShadowEffect(root)
        shadow.setBlurRadius(40)
        shadow.setColor(QColor(0, 0, 0, 96))
        shadow.setOffset(0, 8)
        root.setGraphicsEffect(shadow)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 12, 18, 26)   # 给投影留出绘制空间（原 2px 会裁掉阴影）
        outer.addWidget(root)

        lay = QHBoxLayout(root)
        lay.setContentsMargins(16, 14, 12, 14)
        lay.setSpacing(12)

        self._accent = QLabel()
        self._accent.setFixedSize(4, 40)
        lay.addWidget(self._accent, 0, Qt.AlignmentFlag.AlignVCenter)

        texts = QVBoxLayout()
        texts.setSpacing(4)
        self._title = QLabel()          # 文案配色统一由 apply_theme() 落地
        texts.addWidget(self._title)
        self._body = QLabel()
        self._body.setWordWrap(True)
        texts.addWidget(self._body)
        lay.addLayout(texts, 1)

        close = QPushButton()
        close.setIcon(svg_icon(_TOAST_CLOSE_SVG))
        close.setIconSize(QSize(14, 14))
        close.setFixedSize(24, 24)
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.clicked.connect(self._close_now)
        lay.addWidget(close, 0, Qt.AlignmentFlag.AlignTop)
        self.apply_theme()

    def apply_theme(self):
        """按当前 PALETTE 重建弹窗配色（壁纸/主题切换时由外部调用）。

        弹窗是无父窗口的独立顶层窗口，不随主面板控件树遍历到 —— 构造期烘焙的主题色
        若不主动重刷，切到深色后弹窗会仍是浅色卡片（与浮层卡片同一类缺陷）。
        强调条颜色在 show_toast 时按需取色，不在这里固定。
        """
        self.setStyleSheet(f"""
            QLabel {{ background: transparent; }}
            QPushButton {{
                background: transparent; border: none; border-radius: 12px;
                color: {PALETTE['text_secondary']}; font-size: 14px; font-weight: 700;
            }}
            QPushButton:hover {{ background: {PALETTE['hover']}; color: {PALETTE['danger']}; }}
        """)
        root = getattr(self, "_root", None)
        if root is not None:
            root.setStyleSheet("QWidget#toast { background: transparent; border: none; }")
        if getattr(self, "_title", None) is not None:
            self._title.setStyleSheet(
                f"font-size: 13px; font-weight: 700; color: {PALETTE['text']};")
        if getattr(self, "_body", None) is not None:
            self._body.setStyleSheet(
                f"font-size: 12px; color: {PALETTE['text_secondary']};")

    def show_toast(self, title: str, message: str, warn: bool = False, duration: int = 6000):
        """显示/刷新右下角弹窗并重新滑入"""
        self._title.setText(title)
        self._body.setText(message)
        accent = PALETTE["danger"] if warn else PALETTE["primary"]
        self._accent.setStyleSheet(f"background-color: {accent}; border-radius: 2px;")
        self.adjustSize()

        screen = QApplication.primaryScreen().availableGeometry()
        target = QPoint(screen.right() - self.width() - self._MARGIN,
                        screen.bottom() - self.height() - self._MARGIN)
        self.setWindowOpacity(1.0)
        self.move(screen.right() + 8, target.y())  # 从屏幕右侧外起始
        self.show()
        self.raise_()

        self._anim_in.stop()
        self._anim_in.setStartValue(self.pos())
        self._anim_in.setEndValue(target)
        self._anim_in.start()

        self._stay.stop()
        self._stay.start(duration)

    def _slide_out(self):
        screen = QApplication.primaryScreen().availableGeometry()
        self._anim_out.stop()
        self._fade_out.stop()
        self._anim_out.setStartValue(self.pos())
        self._anim_out.setEndValue(QPoint(screen.right() + 8, self.pos().y()))
        self._out_group.start()

    def _close_now(self, *_):
        self._stay.stop()
        self._anim_in.stop()
        self.hide()
