"""歌词滚动视图：自绘多行歌词，播放到哪行滚动到哪行并高亮，
当前行文本按节奏从左往右被主色平滑填充（KTV 效果）。

- 非当前行：淡灰；当前行：浅色底条 + 加粗 + 主色按 fill 比例从左往右覆盖
- 自动滚动跟随（行切换触发滚动动画）；用户滚轮/拖条浏览后 2 秒内暂停跟随
"""
import time

from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QAbstractScrollArea

from zhuzhu_Copilot.core.i18n import ui as _ui

ROW_H = 30          # 每行高度（px）
FONT_PX = 15        # 歌词字号（px）
PAD_X = 10          # 横向留白
PAD_Y = 6           # 纵向留白（首尾）
LEAD_PX = 2         # 当前行滚到视口顶部的留白
SCROLL_MS = 260     # 行切换滚动动画时长（毫秒，较长过渡更顺滑）
FILL_EPS = 0.004    # 填充进度变化阈值：小于该值不重绘（防抖，减少无效绘制）
BROWSE_PAUSE_S = 2  # 用户手动浏览后暂停自动跟随的秒数


class LyricsView(QAbstractScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._lines = []
        self._current = -1
        self._fill = 0.0
        self._text = "#333333"
        self._dim = "#8A8A8A"
        self._accent = "#2E5A87"
        self._hl = "#EAF2FA"
        self._bg = "#FFFFFF"
        self._browsed_at = 0.0
        self.setFrameShape(QAbstractScrollArea.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._anim = QPropertyAnimation(self.verticalScrollBar(), b"value", self)
        self._anim.setDuration(SCROLL_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    # ---------------- 数据 / 样式 ----------------

    def set_colors(self, text: str, dim: str, accent: str, hl: str, bg: str):
        """主题刷新时更新配色并重绘。"""
        self._text, self._dim, self._accent = text, dim, accent
        self._hl, self._bg = hl, bg
        self.viewport().update()

    def set_lines(self, lines: list):
        """设置歌词行列表；内容未变化时跳过重置（避免高频刷新打断跟唱与重排）"""
        lines = [str(x) for x in (lines or [])]
        if lines == self._lines:
            self.viewport().update()
            return
        self._lines = lines
        self._current = -1
        self._fill = 0.0
        self._anim.stop()
        self.update_geometry()
        self.viewport().update()

    def set_current(self, index: int, fill: float):
        """更新当前行与行内填充进度（0..1），行切换时自动滚动。

        填充变化小于 FILL_EPS 且未换行时跳过重绘（60ms 高精度驱动下的防抖裁剪）。
        """
        if not self._lines:
            return
        index = max(0, min(index, len(self._lines) - 1))
        fill = min(1.0, max(0.0, fill))
        row_changed = index != self._current
        if not row_changed and abs(fill - self._fill) < FILL_EPS:
            return
        self._current = index
        self._fill = fill
        if row_changed:
            self._auto_scroll(index)
        self.viewport().update()

    def update_geometry(self):
        total = max(0, len(self._lines) * ROW_H + PAD_Y * 2)
        sb = self.verticalScrollBar()
        sb.setRange(0, max(0, total - self.viewport().height()))
        sb.setSingleStep(ROW_H)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.update_geometry()

    # ---------------- 滚动跟随 ----------------

    def _auto_scroll(self, index: int):
        """行切换时平滑滚动使当前行贴近视口顶部（用户正在浏览时暂停跟随）"""
        if self.verticalScrollBar().isSliderDown():
            return
        if time.monotonic() - self._browsed_at < BROWSE_PAUSE_S:
            return
        target = max(0, PAD_Y + index * ROW_H - LEAD_PX)
        # 快速连续换行时中断旧动画，从当前实际值继续
        self._anim.stop()
        self._anim.setStartValue(self.verticalScrollBar().value())
        self._anim.setEndValue(target)
        self._anim.start()

    def wheelEvent(self, e):
        self._browsed_at = time.monotonic()
        super().wheelEvent(e)

    # ---------------- 绘制 ----------------

    def paintEvent(self, e):
        p = QPainter(self.viewport())
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.viewport().rect(), QColor(self._bg))
        font = QFont()
        font.setPixelSize(FONT_PX)
        p.setFont(font)
        vw = self.viewport().width()
        vh = self.viewport().height()
        if not self._lines:
            p.setPen(QColor(self._dim))
            p.drawText(self.viewport().rect(), Qt.AlignmentFlag.AlignCenter,
                       _ui("未找到歌词，可点击「导入歌词」"))
            return
        value = self.verticalScrollBar().value()
        first = max(0, (value - PAD_Y) // ROW_H)
        fm = p.fontMetrics()
        for i in range(first, len(self._lines)):
            y = PAD_Y + i * ROW_H - value
            if y >= vh:
                break
            rect = QRectF(PAD_X, y + 2, vw - PAD_X * 2, ROW_H - 4)
            if i == self._current:
                # 当前行：浅色底条（字体保持与普通行一致，不加粗）
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(self._hl))
                p.drawRoundedRect(QRectF(1, y + 1, vw - 2, ROW_H - 2), 6, 6)
                # 底稿：高亮行全文本用文字色绘制
                p.setPen(QColor(self._text))
                p.drawText(rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                           self._lines[i])
                # 填充层：按 fill 比例截取文本宽度，用主色从左往右覆盖实现节奏随动
                if self._fill > 0.01:
                    text_w = min(fm.horizontalAdvance(self._lines[i]) + 2, rect.width())
                    p.save()
                    p.setClipRect(QRectF(rect.x(), rect.y(),
                                         text_w * self._fill, rect.height()))
                    p.setPen(QColor(self._accent))
                    p.drawText(rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                               self._lines[i])
                    p.restore()
            else:
                p.setPen(QColor(self._dim))
                p.drawText(rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                           self._lines[i])