"""AI 操作反馈动画覆盖层：在真实操作位置直观展示点击/滑动/输入。

- 全屏置顶透明悬浮窗（不拦截鼠标），覆盖虚拟屏幕（多显示器），坐标与真实屏幕 1:1。
- 由 agent_screen 在每次点击/滑动/输入后调用 notify_* 追加动画事件；
  动画在 GUI 线程的 QTimer 中推进渲染，线程安全（跨线程仅追加到带锁队列）。
- 风格：深蓝 ACCENT 呼吸光圈/波纹，符合产品极简四色（纯黑/淡灰/白/深蓝）视觉。应客户端
  在 GUI 线程调用 init() 创建覆盖层。
"""

import ctypes
import threading
import time

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QApplication, QWidget

_DUR_CLICK = 0.55    # 点击波纹持续秒数
_DUR_SCROLL = 0.5
_DUR_TYPE = 1.2
_DUR_MOVE = 0.7      # 移动轨迹线持续秒数
_MAX_ANIMS = 16      # 同屏最大动画数，防止刷屏拖慢

_ACCENT = QColor("#1E40AF")        # 深蓝主色
_ACCENT_BRIGHT = QColor("#2563EB") # 深蓝悬亮


class _FeedbackOverlay(QWidget):
    """全局操作反馈覆盖层（单例）。"""

    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._anims = []          # 动画事件列表
        self._lock = threading.Lock()
        self._vx = self._vy = 0   # 虚拟屏幕原点
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)

    # ---- 生命周期 ----
    def init_overlay(self):
        """在 GUI 线程创建后调用：铺满虚拟屏幕并启动常驻动画定时器。

        定时器常驻但仅在队列非空时显示窗口；队列由任意线程追加，显示/隐藏/推进
        全部在 GUI 线程的 _tick 中完成，避免跨线程调用 QWidget 方法导致不显示。
        """
        user32 = ctypes.windll.user32
        self._vx = user32.GetSystemMetrics(76)   # SM_XVIRTUALSCREEN
        self._vy = user32.GetSystemMetrics(77)   # SM_YVIRTUALSCREEN
        ww = max(user32.GetSystemMetrics(78), 1)  # SM_CXVIRTUALSCREEN
        hh = max(user32.GetSystemMetrics(79), 1)  # SM_CYVIRTUALSCREEN
        self.setGeometry(self._vx, self._vy, ww, hh)
        self._timer.start()   # 常驻轮询队列（GUI 线程）

    def add(self, anim: dict):
        # 仅追加到带锁队列（可能来自工作线程），不做任何 GUI 操作
        with self._lock:
            self._anims.append(anim)
            if len(self._anims) > _MAX_ANIMS:
                self._anims.pop(0)

    def _tick(self):
        now = time.time()
        with self._lock:
            self._anims = [a for a in self._anims if now - a["t0"] < a["dur"]]
            alive = bool(self._anims)
        # 显示/隐藏与推进都在 GUI 线程完成
        if alive:
            if not self.isVisible():
                self.show()
                self.raise_()
            self.update()
        elif self.isVisible():
            self.hide()

    # ---- 渲染 ----
    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        with self._lock:
            anims = list(self._anims)
        now = time.time()
        for a in anims:
            prog = min((now - a["t0"]) / max(a["dur"], 1e-3), 1.0)   # 0→1
            kind = a["kind"]
            if kind == "move":
                self._paint_move(p, a["points"], prog, self._vx, self._vy)
                continue
            lx = a["x"] - self._vx
            ly = a["y"] - self._vy
            if kind == "click":
                self._paint_click(p, lx, ly, prog)
            elif kind == "scroll":
                self._paint_scroll(p, lx, ly, prog, a["delta"])
            elif kind == "type":
                self._paint_type(p, lx, ly, prog, a["text"])
        p.end()

    def _paint_click(self, p, x, y, prog):
        """点击波纹：外扩圆环 + 中心亮点，随进度淡出"""
        fade = 1.0 - prog
        radius = 8 + 34 * prog
        p.setPen(QPen(QColor(_ACCENT.red(), _ACCENT.green(), _ACCENT.blue(),
                             int(180 * fade)), 3))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(int(x - radius), int(y - radius), int(radius * 2), int(radius * 2))
        # 中心亮点
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(_ACCENT_BRIGHT.red(), _ACCENT_BRIGHT.green(),
                          _ACCENT_BRIGHT.blue(), int(230 * fade)))
        p.drawEllipse(int(x - 4), int(y - 4), 8, 8)

    def _paint_scroll(self, p, x, y, prog, delta):
        """滑动指示：滚动方向箭头（向上/向下），随进度淡出"""
        fade = 1.0 - prog
        down = float(delta) < 0
        col = QColor(_ACCENT_BRIGHT.red(), _ACCENT_BRIGHT.green(),
                     _ACCENT_BRIGHT.blue(), int(230 * fade))
        p.setBrush(col)
        p.setPen(Qt.PenStyle.NoPen)
        s = 6 + 4 * (1 - prog)
        cy = y + (10 if down else -10)
        # 箭头三角形
        path = QPainterPath()
        if down:
            path.moveTo(x - s, cy - s)
            path.lineTo(x + s, cy - s)
            path.lineTo(x, cy + s)
        else:
            path.moveTo(x - s, cy + s)
            path.lineTo(x + s, cy + s)
            path.lineTo(x, cy - s)
        path.closeSubpath()
        p.drawPath(path)

    def _paint_move(self, p, points, prog, vx, vy):
        """移动轨迹可视化：淡色折线连接路径点，随进度渐隐，末端有行进亮点"""
        if not points:
            return
        fade = 1.0 - prog
        # 末段渐隐：越靠近起点越淡，模拟"轨迹逐渐消散"
        n = len(points)
        for i in range(1, n):
            x0, y0 = points[i - 1][0] - vx, points[i - 1][1] - vy
            x1, y1 = points[i][0] - vx, points[i][1] - vy
            seg = i / max(n - 1, 1)
            alpha = int(200 * fade * seg)
            p.setPen(QPen(QColor(_ACCENT_BRIGHT.red(), _ACCENT_BRIGHT.green(),
                                 _ACCENT_BRIGHT.blue(), min(alpha, 255)), 2))
            p.drawLine(int(x0), int(y0), int(x1), int(y1))
        # 末端行进亮点
        ex, ey = points[-1][0] - vx, points[-1][1] - vy
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(_ACCENT_BRIGHT.red(), _ACCENT_BRIGHT.green(),
                          _ACCENT_BRIGHT.blue(), int(230 * fade)))
        p.drawEllipse(int(ex - 3), int(ey - 3), 6, 6)

    def _paint_type(self, p, x, y, prog, text):
        """输入反馈：在光标附近显示所输入的文本小气泡"""
        if not text:
            return
        fade = 1.0 - min(prog * 1.4, 1.0)   # 保留更久后淡出
        shown = text if len(text) <= 24 else text[:23] + "…"
        font = QFont("Microsoft YaHei UI", 11)
        font.setBold(True)
        p.setFont(font)
        fm = p.fontMetrics()
        tw = fm.horizontalAdvance(shown) + 24
        th = fm.height() + 12
        bx = x + 14
        by = y - th - 8
        # 气泡底板
        p.setPen(QPen(QColor(_ACCENT.red(), _ACCENT.green(), _ACCENT.blue(),
                             int(150 * fade)), 2))
        p.setBrush(QColor(0, 0, 0, int(200 * fade)))
        p.drawRoundedRect(int(bx), int(by), int(tw), int(th), 8, 8)
        # 文本
        p.setPen(QColor(255, 255, 255, int(255 * fade)))
        p.drawText(int(bx) + 12, int(by) + th // 2 + fm.ascent() // 2 - 1, shown)


# ---- 模块级单例（GUI 线程创建） ----
_overlay: _FeedbackOverlay = None
_lock = threading.Lock()


def init():
    """在 GUI 线程调用：创建并铺满覆盖层"""
    global _overlay
    with _lock:
        if _overlay is None:
            _overlay = _FeedbackOverlay()
            _overlay.init_overlay()
    return _overlay


def _cursor() -> tuple:
    """当前鼠标全局屏幕坐标（无则居中）"""
    try:
        pt = ctypes.wintypes.POINT()
        if ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
            return int(pt.x), int(pt.y)
    except Exception:
        pass
    scr = QApplication.primaryScreen()
    g = scr.availableGeometry() if scr else None
    if g:
        return g.center().x(), g.center().y()
    return 0, 0


def notify_click(x: int, y: int):
    """点击反馈：在点击位置显示波纹"""
    if _overlay is None:
        return
    _overlay.add({"kind": "click", "x": x, "y": y, "t0": time.time(),
                  "dur": _DUR_CLICK})


def notify_move_path(points):
    """移动轨迹可视化：传入路径点列表 [(x,y),...]，绘制淡色轨迹线"""
    if _overlay is None or not points:
        return
    _overlay.add({"kind": "move", "points": list(points),
                  "t0": time.time(), "dur": _DUR_MOVE})


def notify_scroll(delta: int):
    """滑动反馈：在光标处显示滚动方向箭头"""
    if _overlay is None:
        return
    x, y = _cursor()
    _overlay.add({"kind": "scroll", "x": x, "y": y, "delta": delta,
                  "t0": time.time(), "dur": _DUR_SCROLL})


def notify_type(text: str):
    """输入反馈：在光标处显示所输入的文本气泡"""
    if _overlay is None or not text:
        return
    x, y = _cursor()
    _overlay.add({"kind": "type", "x": x, "y": y, "text": str(text),
                  "t0": time.time(), "dur": _DUR_TYPE})