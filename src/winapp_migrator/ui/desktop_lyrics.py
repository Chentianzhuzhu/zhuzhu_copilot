"""桌面歌词窗口：无边框、透明、始终置顶，最多显示 2 行（当前句 + 下一句预唱）。

- 默认透明无背景；鼠标悬停（附着）时显示半透明深色圆角背景版
- 当前句（上行）纯白按节奏从左往右填充；下一句预唱（下行）淡灰
- 滚轮调整字号（10~40），窗口宽高随字号与文本自适应；字号持久化到 QSettings
- 可拖动，位置持久化；字体保持系统默认粗细（禁止加粗）
- 悬停时底部显示控制条：暂停/播放、上一首、下一首、关闭（关闭仅本次生效，设置页可再次打开）
"""
from PyQt6.QtCore import QPoint, QRectF, QSettings, QTimer, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QCursor, QIcon
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QApplication, QWidget, QPushButton, QHBoxLayout

from winapp_migrator.core.lyrics_engine import get_lyrics_engine

DEFAULT_FONT_PX = 17   # 默认字号（px）
FONT_MIN = 10          # 最小字号
FONT_MAX = 40          # 最大字号
FONT_STEP = 1          # 滚轮单档字号步进
SYNC_STEP_MS = 60      # 歌词高精度跟随步进（毫秒，与音乐页歌词视图一致）
PAD_X = 16             # 横向内边距
PAD_Y = 10             # 上下内边距
CORNER = 12            # 背景圆角半径
MIN_W = 220            # 最小窗口宽度
CTRL_H = 34            # 悬停控制条高度
FILLED = "#FFFFFF"     # 已填充部分：纯白
PENDING = "#A6A6A6"    # 未填充部分：淡灰
CTRL_COLOR = "#B9B9B9"  # 控制按钮图标：淡灰
_BG_RGBA = (26, 26, 26, 215)   # 半透明近黑底（hover 背景版）
_POS_KEY = "desktop_lyrics/pos"
_FONT_KEY = "desktop_lyrics/font_size"
_ENABLED_KEY = "desktop_lyrics/enabled"

# ---------- 控制按钮线条矢量图标（24x24，淡灰简约，无 emoji） ----------
_ICON_PLAY = ('<svg width="24" height="24" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">'
              '<path d="M8 5l11 7-11 7z" fill="{C}"/></svg>')
_ICON_PAUSE = ('<svg width="24" height="24" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">'
               '<rect x="7" y="5" width="3.4" height="14" rx="1" fill="{C}"/>'
               '<rect x="13.6" y="5" width="3.4" height="14" rx="1" fill="{C}"/></svg>')
_ICON_PREV = ('<svg width="24" height="24" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">'
              '<path d="M7 5v14" stroke="{C}" stroke-width="2" stroke-linecap="round"/>'
              '<path d="M18 6l-9 6 9 6z" fill="{C}"/></svg>')
_ICON_NEXT = ('<svg width="24" height="24" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">'
              '<path d="M17 5v14" stroke="{C}" stroke-width="2" stroke-linecap="round"/>'
              '<path d="M6 6l9 6-9 6z" fill="{C}"/></svg>')
_ICON_CLOSE = ('<svg width="24" height="24" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">'
               '<path d="M6 6l12 12M18 6L6 18" stroke="{C}" stroke-width="2" stroke-linecap="round"/></svg>')


def _svg_icon(svg_tpl: str, color: str, size: int = 16) -> QIcon:
    """渲染内联 SVG 线条矢量图标为 QIcon（淡灰简约，统一线条风格）"""
    pm = None
    try:
        from PyQt6.QtGui import QPixmap
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        r = QSvgRenderer(svg_tpl.replace("{C}", color).encode("utf-8"))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r.render(p)
        p.end()
    except Exception:
        if pm is None:
            from PyQt6.QtGui import QPixmap
            pm = QPixmap(size, size)
            pm.fill(Qt.GlobalColor.transparent)
    return QIcon(pm)



class DesktopLyrics(QWidget):
    """始终置顶的桌面歌词小窗（全局单例，自治驱动：自绑定播放器与歌词引擎）。

    显示与否由 QSettings(desktop_lyrics/enabled) 决定：主面板自动播放或音乐页
    开关均可触发显示，无需先打开音乐设置页；歌词跟随由内部定时器独立驱动。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pending = ""     # 下一句（预唱，下行）
        self._current = ""     # 当前句（填充，上行）
        self._fill = 0.0       # 当前句填充进度 0..1
        self._hover = False    # 鼠标悬停时显示背景版
        self._font_px = self._load_font()
        self._drag_off = None
        self._player = None    # 绑定后的播放器
        self._lyrics = None    # 歌词引擎（bind 后）
        self._timer = None     # 60ms 高精度跟随定时器
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._auto_size()
        self._restore_pos()
        self._build_controls()
        # 程序退出前统一保存位置与字号（拖拽/滚轮之外的收尾保险）
        app = QApplication.instance()
        if app is not None:
            try:
                app.aboutToQuit.connect(self.save_state)
            except Exception:
                pass

    # ---------------- 自治驱动：绑定播放器 / 显示 / 跟随 ----------------

    def bind(self, player) -> "DesktopLyrics":
        """绑定播放器与歌词引擎（幂等）：切歌重载歌词、播放状态驱动定时器"""
        if self._player is not None or player is None:
            return self
        self._player = player
        self._lyrics = get_lyrics_engine().bind(player)
        player.state_changed.connect(self._sync_mode)
        player.song_changed.connect(self._on_song_changed)
        player.tick.connect(self._sync_mode)          # 播放心跳兜底：定时器持续跟随
        self._lyrics.lyrics_changed.connect(self._on_lyrics_changed)
        self._timer = QTimer(self)
        self._timer.setInterval(SYNC_STEP_MS)
        self._timer.timeout.connect(self._step)
        self._sync_mode()
        return self

    def ensure_enabled(self) -> bool:
        """按上次开关状态决定是否显示：开启则置顶显示并开始歌词跟随"""
        if str(QSettings("WinAppMigrator", "WinAppMigrator").value(_ENABLED_KEY, "0")) != "1":
            return False
        if not self.isVisible():
            self.show()
        self.raise_()
        self.sync_now()
        return True

    def sync_now(self):
        """立即同步一次歌词内容并确保跟随定时器运行（开关/启动时调用）"""
        if self._timer is not None:
            self._sync_mode()
            self._step()

    def _sync_mode(self, *_):
        """播放中且可见时运行定时器，否则停止（暂停/停止/隐藏时冻结）"""
        if getattr(self, "_btn_toggle", None) is not None:
            self._update_toggle_icon()
        if self._timer is None:
            return
        want = (self.isVisible() and self._player is not None
                and self._player.is_playing() and not self._player.is_paused())
        if want and not self._timer.isActive():
            self._timer.start()
        elif not want and self._timer.isActive():
            self._timer.stop()

    def _step(self):
        """高精度歌词跟随：当前句 + 下一句预唱 + 行内从左往右填充"""
        if not self.isVisible():
            self._timer.stop()
            return
        lines = self._lyrics.lines() if self._lyrics is not None else []
        if not lines:
            self.set_state("", "", 0.0)
            return
        idx, fill = self._lyrics.locate(self._player.current_position_f())
        cur = lines[idx] if 0 <= idx < len(lines) else ""
        pending = lines[idx + 1] if 0 <= idx + 1 < len(lines) else ""
        self.set_state(pending, cur, fill)

    def _on_song_changed(self, *_):
        """切歌/重播：启动跟随定时器并立即同步。

        play_name 不发 state_changed（首次播放/重播时定时器需在此显式启动）；
        重播瞬间播放器 get_pos 可能残留旧曲位置，先以歌曲开头（首句、填充 0）
        显示，再由定时器按实时位置微调。
        """
        if not (self.isVisible() and self._player is not None):
            return
        self._sync_mode()
        lines = self._lyrics.lines() if self._lyrics is not None else []
        if lines:
            self.set_state(
                lines[1] if len(lines) > 1 else "",
                lines[0], 0.0)
        self._step()

    def _on_lyrics_changed(self):
        if self.isVisible():
            self._step()

    # ---------------- 数据（脏比较合并重绘 + 自适应尺寸） ----------------

    def set_state(self, pending: str, current: str, fill: float):
        pending = pending or ""
        current = current or ""
        fill = min(1.0, max(0.0, fill))
        changed = (pending, current) != (self._pending, self._current)
        if not changed and abs(fill - self._fill) < 0.004:
            return
        self._pending, self._current, self._fill = pending, current, fill
        if changed:
            self._auto_size()
        self.update()

    def font_size(self) -> int:
        return self._font_px

    def adjust_font(self, delta: int):
        """调整字号（滚轮步进），越界钳制并持久化，窗口自适应"""
        new = max(FONT_MIN, min(FONT_MAX, self._font_px + delta))
        if new == self._font_px:
            return
        self._font_px = new
        self._save_font()
        self._auto_size()
        self.update()

    def _row_h(self) -> int:
        return self._font_px + 10

    def _auto_size(self):
        """窗口尺寸随字号与最长文本自适应：宽=最长行+边距，高=两行+边距（悬停时加控制条）"""
        font = QFont()
        font.setPixelSize(self._font_px)
        fm = QFontMetrics(font)
        texts = [self._current, self._pending]
        w = PAD_X * 2 + max([fm.horizontalAdvance(t) for t in texts] + [MIN_W - PAD_X * 2])
        h = PAD_Y * 2 + self._row_h() * 2 + (CTRL_H if self._hover else 0)
        if (self.width(), self.height()) != (w, h):
            self.resize(w, h)
        self._layout_controls()

    def is_active(self) -> bool:
        return bool(self._current)

    # ---------------- 绘制 ----------------

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        if self._hover:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(*_BG_RGBA))
            p.drawRoundedRect(r, CORNER, CORNER)
        font = QFont()          # 系统默认粗细，禁止 Bold
        font.setPixelSize(self._font_px)
        p.setFont(font)
        fm = p.fontMetrics()
        vw = self.width()
        # 当前句在预唱句之上：上行=当前句（纯白填充），下行=下一句（淡灰预唱）
        top_y = PAD_Y
        self._draw_line(p, fm, self._current, top_y, vw, self._fill)
        self._draw_line(p, fm, self._pending, top_y + self._row_h(), vw, -1.0)

    def _draw_line(self, p: QPainter, fm, text: str, y: int, vw: int, fill: float):
        """绘制一行歌词：base 淡灰全文本，fill(0..1) 时用纯白按文本宽度从左往右覆盖"""
        if not text:
            return
        text_w = min(fm.horizontalAdvance(text), vw - PAD_X * 2)
        x = max(0, (vw - text_w) / 2)
        rect = QRectF(x, y, text_w + 2, self._row_h())
        p.setPen(QColor(PENDING))
        p.drawText(rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
        if fill > 0.01:
            p.save()
            p.setClipRect(QRectF(x, y, min(text_w, vw) * fill, self._row_h()))
            p.setPen(QColor(FILLED))
            p.drawText(rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
            p.restore()

    # ---------------- 悬停控制条（暂停/切歌/关闭） ----------------

    def _build_controls(self):
        """构建底部悬停控制条：上一首 / 播放暂停 / 下一首 / 关闭（默认隐藏）"""
        self._controls = QWidget(self)
        self._controls.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        lay = QHBoxLayout(self._controls)
        lay.setContentsMargins(0, 4, 0, 2)
        lay.setSpacing(6)
        lay.addStretch(1)
        self._btn_prev = self._make_btn(_ICON_PREV, "上一首", self._on_prev)
        self._btn_toggle = self._make_btn(_ICON_PLAY, "播放/暂停", self._on_toggle)
        self._btn_next = self._make_btn(_ICON_NEXT, "下一首", self._on_next)
        self._btn_close = self._make_btn(_ICON_CLOSE, "关闭桌面歌词（仅本次关闭）", self._on_close)
        lay.addWidget(self._btn_prev)
        lay.addWidget(self._btn_toggle)
        lay.addWidget(self._btn_next)
        lay.addWidget(self._btn_close)
        lay.addStretch(1)
        self._controls.hide()

    def _make_btn(self, svg_tpl: str, tip: str, slot) -> QPushButton:
        """创建控制条按钮：淡灰矢量图标 + 透明底 + 悬停提亮"""
        btn = QPushButton()
        btn.setIcon(_svg_icon(svg_tpl, CTRL_COLOR, 15))
        btn.setFixedSize(28, 28)
        btn.setIconSize(btn.size())
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolTip(tip)
        btn.setAutoDefault(False)
        btn.setStyleSheet(
            "QPushButton { background: transparent; border: none; border-radius: 6px; }"
            "QPushButton:hover { background: rgba(255,255,255,28); }")
        btn.clicked.connect(slot)
        return btn

    def _layout_controls(self):
        """按悬停状态显示/隐藏并定位底部控制条"""
        ctrl = getattr(self, "_controls", None)
        if ctrl is None:
            return
        if self._hover and self._player is not None:
            ctrl.setGeometry(0, self.height() - CTRL_H, self.width(), CTRL_H)
            ctrl.show()
            ctrl.raise_()
            self._update_toggle_icon()
        else:
            ctrl.hide()

    def _update_toggle_icon(self):
        if self._player is not None and self._player.is_playing() and not self._player.is_paused():
            self._btn_toggle.setIcon(_svg_icon(_ICON_PAUSE, CTRL_COLOR, 15))
            self._btn_toggle.setToolTip("暂停")
        else:
            self._btn_toggle.setIcon(_svg_icon(_ICON_PLAY, CTRL_COLOR, 15))
            self._btn_toggle.setToolTip("播放")

    def _on_prev(self):
        if self._player is not None:
            self._player.play_prev()

    def _on_next(self):
        if self._player is not None:
            self._player.play_next()

    def _on_toggle(self):
        if self._player is not None:
            self._player.toggle()
            self._update_toggle_icon()

    def _on_close(self):
        """关闭桌面歌词（仅本次关闭一次；设置页再次打开可恢复显示）"""
        QSettings("WinAppMigrator", "WinAppMigrator").setValue(_ENABLED_KEY, "0")
        self._hover = False
        self._auto_size()
        self._layout_controls()
        self.hide()

    # ---------------- 悬停 / 滚轮 / 拖拽 ----------------

    def enterEvent(self, e):
        self._hover = True
        self._auto_size()   # 悬停展开控制条（高度增加）
        self._layout_controls()
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        # 鼠标移到子按钮上也可能触发父窗口 leave，用全局坐标判真伪，避免控制条闪烁
        gp = QCursor.pos()
        if self.rect().contains(self.mapFromGlobal(gp)):
            super().leaveEvent(e)
            return
        self._hover = False
        self._auto_size()   # 离开收起控制条
        self._layout_controls()
        self.update()
        super().leaveEvent(e)

    def wheelEvent(self, e):
        """滚轮调整字号：向上放大、向下缩小（驱动消息含方向）"""
        delta = 1 if e.angleDelta().y() > 0 else -1
        self.adjust_font(delta * FONT_STEP)
        e.accept()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_off = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            e.accept()

    def mouseMoveEvent(self, e):
        if self._drag_off is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_off)
            e.accept()

    def mouseReleaseEvent(self, e):
        if self._drag_off is not None:
            self._drag_off = None
            self.save_state()
            e.accept()

    # ---------------- 持久化 ----------------

    def save_state(self):
        """统一保存当前位置与字号（拖拽释放 / 应用退出时调用）"""
        self._save_pos()
        self._save_font()

    def _restore_pos(self):
        try:
            st = QSettings("WinAppMigrator", "WinAppMigrator")
            pos = st.value(_POS_KEY)
            if isinstance(pos, QPoint):
                self.move(pos)
                return
        except Exception:
            pass
        # 默认位置：主屏右侧中部（避免盖住任务栏）
        try:
            scr = self.screen() or QApplication.primaryScreen()
            avail = scr.availableGeometry() if scr else None
            if avail:
                self.move(avail.right() - self.width() - 40,
                          avail.top() + avail.height() // 2)
        except Exception:
            pass

    def _save_pos(self):
        try:
            QSettings("WinAppMigrator", "WinAppMigrator").setValue(
                _POS_KEY, self.pos())
        except Exception:
            pass

    def _load_font(self) -> int:
        try:
            v = int(QSettings("WinAppMigrator", "WinAppMigrator").value(_FONT_KEY, DEFAULT_FONT_PX))
            return max(FONT_MIN, min(FONT_MAX, v))
        except Exception:
            return DEFAULT_FONT_PX

    def _save_font(self):
        try:
            QSettings("WinAppMigrator", "WinAppMigrator").setValue(
                _FONT_KEY, self._font_px)
        except Exception:
            pass


# 全局单例：设置面板重建/频繁开关时避免产生多个置顶窗口
_desktop = None


def get_desktop_lyrics() -> DesktopLyrics:
    global _desktop
    if _desktop is None:
        _desktop = DesktopLyrics()
    return _desktop