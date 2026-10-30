"""窗口级「任务栏折叠」动画（观感对标 macOS 坞站收起 / Windows 11 最小化）。

## 为什么不用「直接改窗口几何」

主面板是无边框自绘窗口（WA_TranslucentBackground + paintEvent 裁圆角），内部有上百个
聊天气泡、四块停靠面板、滚动区与自适应缩放。逐帧改几何会让整棵控件树每帧重排
（layout + 气泡宽度同步 + 停靠面板归位 + 缩放热区重钉），实测必掉帧；而且内容会被
「压扁重排」而不是「折叠」，观感很脏。

所以这里走 **离屏快照 + 点击穿透浮层** 的路子：
  1. `QWidget.grab()` 把窗口渲染成一张位图（离屏渲染，窗口处于最小化态同样有效）；
  2. 真实窗口按系统原生时序最小化 / 恢复，屏幕上由一张顶层浮层接管残影；
  3. 浮层只做「矩形插值 + 透明度 + 圆角收缩」，不碰任何真实控件 → 零重排；
  4. 动画结束一帧切换回真实窗口，因为浮层与真窗口像素一致，切换不可见。

## 折叠语义

两端都锚定在**可用桌面区的下边缘**（底部任务栏时即任务栏上边缘）：
  - 收起（conceal）：窗口从当前几何压扁、沉入任务栏，同时淡出；
  - 展开（reveal）：从任务栏边缘「抽」出来，纵向由扁到满、横向由窄到满，同时淡入。
因此观感就是「从任务栏下方折叠弹出」。

## 实测约束（改这里之前先看）

- `QWidget.grab()` 首次调用有一次性懒初始化（本机实测 300~500ms），故提供 `prewarm()`
  用小区域抓一次把该路径提前消化；预热后整窗抓取 20~40ms。
- 最小化态抓取比可见态慢（100ms 量级），所以收起时抓到的快照会被缓存，恢复时直接复用
  （见 `_CACHE_TTL`），让「点任务栏图标」到动画起手是零延迟。
- `showNormal()` 内部会同步跑面板的 showEvent（归位子面板，100~200ms），这段时间必须由
  浮层继续顶着，否则会露出桌面。收尾顺序因此是「钉末帧 → 切窗口 → 延后一拍撤浮层」。

## 与系统原生行为的关系

调用方拦截 WM_SYSCOMMAND 的 SC_MINIMIZE / SC_RESTORE，先尝试本模块；`start_*()` 返回
False 时（取不到快照、尺寸异常、已有动画在跑）调用方必须退回系统默认处理 —— 任何异常
都只是「没有动画」，绝不影响最小化/恢复本身。

## 连点 / 打断

动画播到一半又来一次点击（用户手快、或连点任务栏图标）时，**从当前帧直接折返**
（`_reverse`），而不是「等这一遍播完再播回去」：前者 ~200ms 到位，后者 ~570ms，且后者
看起来像「先冲到底再弹回」。折返点由 `e0 = 1 - 当前进度` 换算，矩形走归一化参数，
接缝处位移实测 0~3px。折返段单独用 InOutCubic，否则首帧就会吃掉一半剩余行程。
"""

import ctypes
import os
import time

from PyQt6.QtCore import (
    QAbstractNativeEventFilter,
    QEasingCurve,
    QObject,
    QRect,
    QRectF,
    Qt,
    QTimer,
    QVariantAnimation,
)
from PyQt6.QtGui import QColor, QGuiApplication, QLinearGradient, QPainter, QPainterPath, QPixmap
from PyQt6.QtWidgets import QWidget

# ---------- 时长与形态参数（单一数据源，调手感只改这里） ----------
REVEAL_MS = 280          # 展开时长（略长于收起：入场给足「看清」的时间）
CONCEAL_MS = 230         # 收起时长（Windows 最小化约 200~250ms）
_FOLD_H = 0.055          # 折叠态高度 = 窗口高度 * 该比例
_FOLD_W = 0.86           # 折叠态宽度 = 窗口宽度 * 该比例
_FOLD_MIN_H = 12         # 折叠态最小高度（px），保证「一条边」始终可见
_FADE_PORTION = 0.55     # 前 55% 进度内完成淡入/淡出（后半程纯位移，收尾更干脆）
_GUARD_MS = 600          # 安全网：动画时长之外再多等这么久就强制收尾
_RETIRE_MS = 40          # 切回真窗口后延迟撤浮层（等真窗口画上去，避免闪一下桌面）
_CACHE_TTL = 6.0         # 快照缓存有效期（秒）：超时视为内容可能已变，重新抓取
_CREASE_ALPHA = 58       # 折痕阴影最大不透明度

# Win32 消息常量（调用方拦截系统命令时复用）
WM_SYSCOMMAND = 0x0112
SC_MINIMIZE = 0xF020
SC_RESTORE = 0xF120

_DBG_PATH = os.environ.get("ZHZHU_FX_DEBUG") or ""


def _dbg(msg: str):
    """排查用埋点：设置环境变量 ZHZHU_FX_DEBUG=<文件路径> 后把每步耗时写进该文件。

    动画问题（卡顿、少一帧、状态没落地）光看代码很难定位，现场抓时间线最快；
    不设环境变量时只有一次全局判断，无开销。
    """
    if not _DBG_PATH:
        return
    try:
        with open(_DBG_PATH, "a", encoding="utf-8") as f:
            f.write("%9.1f  %s\n" % (time.monotonic() * 1000.0, msg))
    except Exception:
        pass


def fold_line(screen) -> int:
    """折叠基准线（全局逻辑坐标的 y）。

    取 `availableGeometry().bottom() + 1`：Windows 已把任务栏与其它 AppBar 从可用区
    排除，且 Qt 的 availableGeometry 就是逻辑像素（无需像 SHAppBarMessage 那样自己做
    DPI 换算），底部任务栏时它正好是任务栏上边缘。任务栏在左/右/顶部（或自动隐藏）
    时同样退回「屏幕可用区下边缘」—— 折叠方向始终朝屏幕下边缘，观感依旧成立。
    """
    try:
        return int(screen.availableGeometry().bottom()) + 1
    except Exception:
        return 0


def dock_rect(target: QRect, screen) -> QRect:
    """折叠终点：贴在折叠基准线上、水平对齐窗口中心的一条窄带。"""
    line = fold_line(screen)
    h = max(_FOLD_MIN_H, int(round(target.height() * _FOLD_H)))
    w = max(140, int(round(target.width() * _FOLD_W)))
    x = target.center().x() - w // 2
    try:
        g = screen.geometry()
        x = min(max(x, g.left()), g.right() - w + 1)
    except Exception:
        pass
    y = line - h if line > 0 else target.bottom() - h
    return QRect(int(x), int(y), int(w), int(h))


def _screen_for(win):
    try:
        scr = win.screen()
        if scr is not None:
            return scr
    except Exception:
        pass
    return QGuiApplication.primaryScreen()


#: ShowWindow 的「还原」命令（SW_RESTORE）：清掉最大化/最小化状态并回到还原矩形
_SW_RESTORE = 9


def _win32_restore(win) -> bool:
    """Win32 还原最大化/最小化窗口（非阻塞，实测 ~106ms）。

    为什么不用 Qt 的 `setWindowState(WindowNoState)`：实测同一窗口要 ~430ms
    （Qt 会重建 backing store 并同步跑一次完整 state change），首次 `showNormal()`
    更可达 1.4s —— 这段时间主线程全被占住，用户看到的就是「点了还原，窗口卡死」。
    ShowWindow(SW_RESTORE) 只让系统改窗口状态，Qt 随后收到 WM_SIZE 正常同步。
    """
    try:
        hwnd = int(win.winId())
    except Exception:
        return False
    if not hwnd:
        return False
    try:
        ctypes.windll.user32.ShowWindow(ctypes.c_void_p(hwnd), _SW_RESTORE)
        return True
    except Exception:
        return False


def _pin_geometry(win, target: QRect):
    """窗口几何与 target 明显不一致时才补一次 setGeometry（延后执行）。

    `setGeometry` 会触发整窗重排（实测可达 260ms），不能放在还原的同步路径上；
    而 ShowWindow 之后 Qt 的几何要等处理完 WM_SIZE 才更新，立刻比较必然读到旧值。
    """
    try:
        g = win.geometry()
        if (abs(g.x() - target.x()) > 8 or abs(g.y() - target.y()) > 8
                or abs(g.width() - target.width()) > 8
                or abs(g.height() - target.height()) > 8):
            win.setGeometry(target)
    except RuntimeError:
        pass


def unmaximize(win, target: QRect = None):
    """把最大化/最小化窗口还原成普通态（瞬时），并保证几何真正回到普通尺寸。

    关键点：
    1. **优先 Win32 ShowWindow(SW_RESTORE)**：非阻塞路径，实测 ~106ms；Qt 的
       `setWindowState` 实测 ~430ms、首次 `showNormal` 可达 1400ms（都是「还原卡死」
       的直接来源），只在 Win32 失败时兜底。
    2. **状态清了不代表几何跟随**：若窗口的「还原矩形」本身就是满屏尺寸（被拖到
       满屏、或热区拉满过），还原后窗口仍是 1920x1032 但状态是普通态 —— 之后点最大化
       会因「没有可放大的空间」直接空操作，按钮从此按了没反应。所以 target 与实测
       几何明显不符时要补一次 setGeometry —— 但**延后一拍**，避开点击响应路径。
    """
    try:
        ok = _win32_restore(win)
        if not ok:
            # Win32 不可用（句柄异常/被系统策略拦下）：退回 Qt 原生路径
            win.setWindowState(Qt.WindowState.WindowNoState)
            if win.isMaximized():
                win.showNormal()
        if target is not None and not target.isEmpty():
            QTimer.singleShot(0, lambda w=win, t=QRect(target): _pin_geometry(w, t))
    except Exception:
        pass


# DWMWA_TRANSITIONS_FORCEDISABLED（Win8+）：关掉本窗口的 DWM 过渡动画
_DWMWA_TRANSITIONS_FORCEDISABLED = 3


def set_transitions(win, disabled: bool):
    """开关「窗口自己的」系统过渡动画（最小化到任务栏 / 从任务栏还原那一下）。

    系统默认会为最小化/还原播一段 DWM 过渡。它和本模块的折叠是两段独立运动：
    一旦系统那套也开着，屏幕上就是「窗口先缩进任务栏，快照再折叠一遍」——
    也就是用户说的「动画重复播放」。动画期间关掉它，屏幕上只留我们这一套。

    只影响本窗口，不动系统全局设置（`SPI_SETANIMATION` 那种改法会影响其它程序）。
    某些系统设置下这段过渡本来就不播，此时本调用是空操作，没有副作用。
    """
    try:
        hwnd = int(win.winId())
    except Exception:
        return
    if not hwnd:
        return
    try:
        dwmapi = ctypes.windll.dwmapi
        dwmapi.DwmSetWindowAttribute.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                                 ctypes.c_void_p, ctypes.c_uint]
        val = ctypes.c_int(1 if disabled else 0)
        dwmapi.DwmSetWindowAttribute(ctypes.c_void_p(hwnd),
                                     _DWMWA_TRANSITIONS_FORCEDISABLED,
                                     ctypes.byref(val), ctypes.sizeof(val))
    except Exception:
        pass


def grab_window(win, rect=None):
    """窗口快照。最小化/隐藏状态下同样有效（QWidget.render 是离屏渲染，不看窗口可见性）。"""
    try:
        pix = win.grab(rect) if rect is not None else win.grab()
    except Exception:
        return None
    if pix is None or pix.isNull() or pix.width() <= 0 or pix.height() <= 0:
        return None
    # 统一按 1:1 设备像素处理：绘制时源矩形与目标矩形同量纲，避免高 DPI 下缩放错位
    pix.setDevicePixelRatio(1.0)
    return pix


class _MSG(ctypes.Structure):
    """Win32 MSG（64 位布局，与 agent_panel._MSG 同构；此处独立定义以免反向依赖）。"""
    _fields_ = [("hwnd", ctypes.c_void_p),
                ("message", ctypes.c_uint),
                ("wParam", ctypes.c_void_p),
                ("lParam", ctypes.c_void_p),
                ("time", ctypes.c_uint),
                ("pt_x", ctypes.c_long), ("pt_y", ctypes.c_long)]


class TaskbarCommandFilter(QAbstractNativeEventFilter):
    """拦截本窗口的系统命令：任务栏图标点击 / 系统菜单 / Win+↓ 发出的
    SC_MINIMIZE、SC_RESTORE。

    **不能用重写 QWidget.nativeEvent 实现**：PyQt 6.11 下给 QWidget 子类加
    nativeEvent 覆写，会在窗口创建阶段（WM_NCCREATE 派发）直接访问违例崩溃，
    最小复现代码亦如此（与业务逻辑无关）。QAbstractNativeEventFilter 是 Qt 官方
    推荐的通道，也是本工程既有的做法（见 agent_panel._AdminDropFilter）。

    只有在回调确认「动画能接管」时才吞掉消息；否则原样放行，系统行为不变。
    """

    def __init__(self, window, can_minimize, can_restore, do_minimize, do_restore):
        super().__init__()
        self._win = window
        self._can_min = can_minimize
        self._can_res = can_restore
        self._do_min = do_minimize
        self._do_res = do_restore
        self._hwnd = 0
        # 去重：同一条 WM_SYSCOMMAND 可能经 windows_dispatcher_MSG 与 windows_generic_MSG
        # 各来一次（Qt 的两条投递通道）。不去重就会触发两次 → 第二次被当成「连点」排队，
        # 动画收尾后又重播一遍（用户看到的就是「重复播放」）。
        self._last_key = None
        self._last_at = 0.0

    def bind(self):
        """绑定窗口句柄（窗口创建后调用；未绑定时按「消息目标为空」处理，不误吞）。"""
        try:
            self._hwnd = int(self._win.winId())
        except Exception:
            self._hwnd = 0

    def nativeEventFilter(self, eventType, message):
        try:
            if bytes(eventType) not in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
                return False, 0
            msg = ctypes.cast(int(message), ctypes.POINTER(_MSG)).contents
            if msg.message != WM_SYSCOMMAND:
                return False, 0
            _dbg("syscmd %-22s hwnd=%s (bound=%s) wparam=%#x" % (
                bytes(eventType).decode("ascii", "replace"), int(msg.hwnd or 0),
                self._hwnd, int(msg.wParam or 0)))
            # 全局过滤器会收到进程内所有窗口的消息，只认本窗口
            if self._hwnd and int(msg.hwnd or 0) != self._hwnd:
                return False, 0
            cmd = int(msg.wParam or 0) & 0xFFF0
            key = (int(msg.message), cmd, int(msg.time or 0))
            now = time.monotonic()
            if key == self._last_key and (now - self._last_at) < 0.3:
                _dbg("filter DUP ignored cmd=%#06x" % cmd)
                return True, 0   # 同一条消息的第二次投递：吞掉但不重复触发
            self._last_key, self._last_at = key, now
            _dbg("filter %-22s cmd=%#06x time=%d hwnd=%s min=%s res=%s" % (
                bytes(eventType).decode("ascii", "replace"), cmd, int(msg.time or 0),
                int(msg.hwnd or 0), self._can_min(), self._can_res()))
            if cmd == SC_MINIMIZE and self._can_min():
                QTimer.singleShot(0, self._do_min)   # 原生消息里不能直接操作窗口
                return True, 0
            if cmd == SC_RESTORE and self._can_res():
                QTimer.singleShot(0, self._do_res)
                return True, 0
        except Exception:
            pass
        return False, 0


class FoldSprite(QWidget):
    """点击穿透的顶层快照浮层。

    - 不抢焦点（WA_ShowWithoutActivating / Tool）、不进任务栏（Tool）、不接收鼠标
      （WindowTransparentForInput + WA_TransparentForMouseEvents）；
    - 几何只在创建时定一次（起点与终点的并集），之后每帧只改「内容矩形」并做**局部**
      update：逐帧 resize 一个分层窗口（WS_EX_LAYERED）会重新分配表面，是掉帧的主因。
    """

    def __init__(self, pix: QPixmap):
        super().__init__(
            None,
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._pix = pix
        self._content = QRect()   # 当前折叠矩形（全局坐标）
        self._alpha = 1.0
        self._fold = 1.0          # 0=完全折叠 1=完全展开（决定折痕阴影强度）
        self._radius = 0

    # ---------- 帧参数 ----------
    def set_pix(self, pix: QPixmap):
        """换快照（浮层复用：原生分层窗口只创建一次，省掉每次 30ms 的建窗开销）。"""
        self._pix = pix
        self._content = QRect()
        self._alpha = 1.0
        self._fold = 1.0
        self.update()

    def set_radius(self, r: int):
        self._radius = max(0, int(r))

    def content(self) -> QRect:
        """当前折叠矩形（反向播放时以它为起点，避免从终点重头播）。"""
        return QRect(self._content)

    def set_content(self, rect: QRect):
        old = self._content
        self._content = QRect(rect)
        if old.isValid() and not old.isEmpty():
            # 局部重绘：折叠中段内容矩形只占窗口一小条，整窗 update 会白刷 100 万像素
            self.update(old.united(self._content).adjusted(-2, -2, 2, 2))
        else:
            self.update()

    def set_alpha(self, a: float):
        self._alpha = max(0.0, min(1.0, float(a)))

    def set_fold(self, f: float):
        self._fold = max(0.0, min(1.0, float(f)))

    # ---------- 绘制 ----------
    def paintEvent(self, e):
        try:
            p = QPainter(self)
        except Exception:
            return
        try:
            # 先清掉本次脏区再画。**这步不能省**：分层窗口（WA_TranslucentBackground）
            # 的背面缓冲不会自动擦除，而折叠是「内容矩形逐帧收缩」，腾空的区域如果不
            # 主动清掉，上一帧的大矩形会一直残留在屏幕上 —— 用户看到的就是窗口拖影。
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            p.fillRect(e.rect(), QColor(0, 0, 0, 0))
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            if self._pix is None or self._content.isEmpty():
                return
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            p.setOpacity(self._alpha)
            r = QRectF(self._content.translated(-self.pos()))
            # 折叠时窗口被纵向压扁，圆角同步收缩：否则会出现「薄片配大圆角」的怪形
            rad = min(float(self._radius), r.height() / 2.0, r.width() / 2.0)
            path = QPainterPath()
            path.addRoundedRect(r, rad, rad)
            p.setClipPath(path)
            p.drawPixmap(r, self._pix, QRectF(self._pix.rect()))
            # 折痕：折叠越深，顶缘越像「纸被折起而背光」，一道极淡的暗边收住轮廓
            depth = 1.0 - self._fold
            if depth > 0.02:
                g = QLinearGradient(r.left(), r.top(),
                                    r.left(), r.top() + max(1.0, r.height() * 0.45))
                g.setColorAt(0.0, QColor(0, 0, 0, int(_CREASE_ALPHA * depth)))
                g.setColorAt(1.0, QColor(0, 0, 0, 0))
                p.fillRect(r, g)
        finally:
            p.end()


class WindowFoldAnimator(QObject):
    """把「最小化 / 从最小化恢复」换成任务栏方向的折叠动画。

    调用方约定：
      - `can_conceal()` / `can_reveal()` 只做廉价判定，用于决定是否拦截系统命令；
      - `start_conceal()` / `start_reveal()` 返回 False 时必须退回系统默认行为。
    """

    def __init__(self, window, radius: int = 18, parent=None):
        super().__init__(parent or window)
        self._win = window
        self._radius = int(radius)
        self._sprite = None
        self._anim = None
        self._guard = None
        self._busy = False
        self._mode = None       # 正在播的方向：conceal / reveal
        self._pending = None    # 动画期间收到的反向请求（收尾后接着播）
        self._defer = None      # 排队动作的延时器（独立 QTimer，见 _finish）
        self._progress = 0.0    # 当前帧进度（反向接播时据此换算起点）
        self._target_rect = QRect()   # 窗口在「展开态」的几何（反向接播的锚点）
        self._on_done = None
        self._end_state = None
        self._restore_max = False
        self._restore_full = False
        # 快照缓存：收起时抓的图留给恢复用（最小化态抓图慢 3~5 倍）
        self._cache_pix = None
        self._cache_rect = QRect()
        self._cache_at = 0.0
        self._prewarmed = False
        self._spare = None      # 复用的隐藏浮层（避免每次都重建原生分层窗口）
        self._trans_seq = 0     # DWM 过渡开关的序号（防止延迟恢复撞上新动画）
        self._restored_at = 0.0  # 最近一次「动画还原窗口」的时刻

    # ---------- 预热 ----------
    def prewarm(self):
        """预热离屏渲染路径。

        Qt 首次 `QWidget.grab()` 有一次懒初始化（本机实测 300~500ms），不预热的话这
        笔开销正好压在用户第一次点「最小化」上 —— 表现为点了没反应半秒。用 64x64 的
        小区域抓一次即可把该路径消化掉（实测首次 35ms，之后整窗抓取 20~40ms）。
        """
        if self._prewarmed:
            return
        self._prewarmed = True
        try:
            self._win.grab(QRect(0, 0, 64, 64))
        except Exception:
            pass
        # 顺手把浮层的原生窗口也建出来（首次 show() 要 30~35ms）：隐藏状态下 winId()
        # 即可完成创建，真正动画时直接复用，点击到起手的延迟能再压掉一截。
        try:
            if self._spare is None:
                sp = FoldSprite(QPixmap(8, 8))
                sp.set_radius(self._radius)
                sp.winId()
                sp.hide()
                self._spare = sp
        except Exception:
            self._spare = None

    # ---------- 状态查询 ----------
    def busy(self) -> bool:
        return bool(self._busy)

    def recently_restored(self, within: float = 0.7) -> bool:
        """刚刚由本模块把窗口还原过（调用方据此避免再叠一层自己的入场动画）。"""
        return (time.monotonic() - self._restored_at) < within

    def can_conceal(self) -> bool:
        w = self._win
        if self._busy:
            # 动画在跑：一律返回 True 把系统命令吞掉，交给 start_conceal 决定
            # （反向 → 折返；同向 → 排队，收尾时自然变成空操作）。
            # **不能因为「当前正在收起」就返回 False**：那会把重复投递的最小化命令
            # 放行给系统，系统随即原生最小化，正在播的折叠被当场打断 —— 表现就是
            # 「动画播到一半窗口突然消失」。
            return True
        try:
            return (w.isVisible() and not w.isMinimized()
                    and w.width() >= 200 and w.height() >= 150)
        except RuntimeError:
            return False

    def can_reveal(self) -> bool:
        w = self._win
        if self._busy:
            # 同上：折叠还没播完就点任务栏图标（用户手快）也必须能接住。
            return True
        try:
            return (w.isMinimized() and w.width() >= 200 and w.height() >= 150)
        except RuntimeError:
            return False

    # ---------- 对外动作 ----------
    def start_conceal(self) -> bool:
        """折叠收起：快照 → 最小化 → 浮层沉入任务栏。"""
        w = self._win
        if self._busy:
            # 反方向打断：直接从当前帧反向，不排队等重播（见 _reverse）
            if self._mode == "reveal" and self._reverse("conceal"):
                return True
            return self._queue("conceal")
        if not self.can_conceal():
            _dbg("conceal REJECT can_conceal=False busy=%s mode=%s" % (self._busy, self._mode))
            return False
        target = QRect(w.geometry())
        t0 = time.monotonic()
        pix = grab_window(w)
        t_grab = (time.monotonic() - t0) * 1000.0
        if pix is None:
            _dbg("conceal REJECT grab=None (%.0fms)" % t_grab)
            return False
        self._remember(pix, target)
        self._target_rect = QRect(target)
        # 记住最小化前的窗口形态，恢复时按原样还原（最大化/全屏不能一律 showNormal）
        try:
            self._restore_max = bool(w.isMaximized())
            self._restore_full = bool(w.isFullScreen())
        except Exception:
            self._restore_max = self._restore_full = False
        dock = dock_rect(target, _screen_for(w))
        self._busy = True   # 先置忙：showMinimized 会同步触发 changeEvent，必须挡住重入
        self._disable_transitions()
        done = {"min": False}

        def _minimize():
            # 关键顺序：浮层先顶上去（像素与窗口完全一致，看不出变化），再最小化。
            # showMinimized 会同步跑面板的 changeEvent（收起子面板、关浮层），实测
            # 20~300ms 不等；这段重活必须在浮层后面完成，否则用户看到的是「点了没反应，
            # 然后窗口突然消失」。同时这里 DWM 过渡已被关掉，系统不会再自己播一遍
            # 最小化动画（否则就是「同一个动作播两次」）。
            try:
                w.showMinimized()
                done["min"] = True
            except Exception:
                pass

        self._play(pix, target, dock, "conceal", None, before=_minimize)
        if not done["min"]:
            # 最小化没成功：撤浮层、放行给系统原生处理（动画只是锦上添花）
            self.cancel()
            return False
        _dbg("conceal grab=%.0fms rect=%s" % (t_grab, target))
        return True

    def start_reveal(self) -> bool:
        """折叠展开：快照 → 浮层从任务栏抽出 → 还原窗口。"""
        w = self._win
        if self._busy:
            if self._mode == "conceal" and self._reverse("reveal"):
                return True
            return self._queue("reveal")
        if not self.can_reveal():
            _dbg("reveal REJECT can_reveal=False busy=%s mode=%s min=%s" % (
                self._busy, self._mode, w.isMinimized()))
            return False
        target = QRect(w.geometry())
        if target.width() < 200 or target.height() < 150:
            # 个别驱动在最小化态会把 geometry 压成标题栏尺寸，退回 normalGeometry
            try:
                ng = QRect(w.normalGeometry())
                if ng.width() >= 200 and ng.height() >= 150:
                    target = ng
            except Exception:
                pass
        pix = self._cached(target) or grab_window(w)
        if pix is None:
            return False
        self._target_rect = QRect(target)
        dock = dock_rect(target, _screen_for(w))
        self._busy = True
        self._disable_transitions()   # 收尾的 showNormal 不能再播系统还原动画
        self._play(pix, dock, target, "reveal", self._restore_window)
        return True

    def cancel(self):
        """立即收尾（关闭窗口/退出时调用）：只收浮层，不再触发还原回调 ——
        窗口正在关闭，此时把最小化的窗口还原出来只会多闪一下。"""
        self._on_done = None
        self._pending = None
        try:
            if self._defer is not None:
                self._defer.stop()
        except Exception:
            pass
        self._defer = None
        if self._busy:
            self._finish()
        # 窗口正在关闭：复用池里的浮层也要销毁，否则退出时会留一个顶层窗口
        sp, self._spare = self._spare, None
        if sp is not None:
            try:
                sp.hide()
                sp.setParent(None)
                sp.deleteLater()
            except RuntimeError:
                pass

    # ---------- 动画期间的连点排队 ----------
    def _queue(self, action: str) -> bool:
        """记下一个「等当前动画收尾后再执行」的动作。

        折叠/展开之间的互相打断走 `_reverse()`（从当前帧折返，更跟手），这里只兜住
        真正没法折返的情况：最大化/还原的尺寸过渡途中被要求最小化/恢复，或反向接播
        缺少必要信息（取不到浮层或锚点矩形）。

        必须返回 True 并吞掉系统命令：若返回 False，调用方会退回系统原生处理，而原生
        最小化/还原会在浮层之下直接改窗口状态，屏幕上就是「动画放到一半窗口突然跳变」。

        后到的请求覆盖先到的（最后意图优先）：折叠→展开→折叠，最终应停在折叠态。
        """
        self._pending = action
        return True

    def _run_pending(self):
        """执行排队动作。到这时窗口状态可能已经变了（例如展开已把窗口还原），
        所以先重试动画；动画不适用时退回「直接达成该状态」，绝不留下中间态。"""
        action, self._pending = self._pending, None
        if action is None:
            return
        try:
            if self._busy:
                # 排队等待期间用户又触发了新的动画：方向一致就算已满足，方向相反则再排一次
                if self._mode != action:
                    self._pending = action
                return
            if action == "conceal":
                if not self.start_conceal() and not self._win.isMinimized():
                    self._win.showMinimized()
            else:
                if not self.start_reveal() and self._win.isMinimized():
                    self._restore_window()
        except Exception:
            pass

    def _reverse(self, action: str) -> bool:
        """反方向打断：沿用当前浮层与快照，从当前帧直接往回播。

        与「排队等这一遍播完再播回去」相比，响应从 ~570ms 降到 ~200ms，且运动是
        连续折返（和 macOS / Windows 的窗口动画一致），不会出现「先冲到底再弹回」。

        进度换算：设收起进度 p，收起态的 alpha=(1-p)/0.55、折痕=1-p；展开在进度 e
        时 alpha=e/0.55、折痕=e。令两者相等即得 e = 1-p —— 所以反向接播的起点
        恒为 `1 - 当前进度`，两个方向对称。
        """
        sprite = self._sprite
        if sprite is None or self._anim is None:
            return False
        cur = sprite.content()
        if cur.width() < 2 or cur.height() < 2:
            return False
        # 反向的锚点必须是「窗口在展开态的几何」。优先用本次动画开始时记下的目标矩形；
        # 窗口若是被其它途径最小化的（Win+D、外部调用），_cache_rect 可能为空，再退回快照。
        base = QRect(self._target_rect)
        if base.width() < 200 or base.height() < 150:
            base = QRect(self._cache_rect)
        if base.width() < 200 or base.height() < 150:
            return False
        if action == "reveal":
            end, on_done = QRect(base), self._restore_window
        elif action == "conceal":
            # 展开中被打断：真实窗口此时仍是「最小化」状态（showNormal 在动画末尾才调），
            # 所以反向播到折叠带收尾即可，on_done 必须为空，窗口保持最小化。
            end, on_done = dock_rect(base, _screen_for(self._win)), None
        else:
            return False
        for name in ("_guard", "_anim"):
            obj = getattr(self, name, None)
            setattr(self, name, None)
            try:
                if obj is not None:
                    obj.stop()
            except Exception:
                pass
        e0 = max(0.0, min(0.95, 1.0 - float(self._progress)))
        self._busy = True
        self._pending = None
        self._set_end_state(end, action)
        self._run(sprite, cur, end, action, on_done, e0)
        return True

    # ---------- 快照缓存 ----------
    def _disable_transitions(self):
        """动画期间关掉窗口自身的 DWM 过渡（避免系统动画与本模块叠加）。"""
        self._trans_seq += 1
        set_transitions(self._win, True)

    def _enable_transitions_later(self, delay: int):
        """延迟恢复 DWM 过渡：必须等窗口状态真正切换完，否则系统动画会在收尾时冒出来。"""
        seq = self._trans_seq

        def _f():
            try:
                if self._busy or seq != self._trans_seq:
                    return
                set_transitions(self._win, False)
            except RuntimeError:
                pass

        QTimer.singleShot(delay, _f)

    def _remember(self, pix, rect: QRect):
        self._cache_pix = pix
        self._cache_rect = QRect(rect)
        self._cache_at = time.monotonic()

    def _cached(self, rect: QRect):
        if self._cache_pix is None or self._cache_rect != rect:
            return None
        if (time.monotonic() - self._cache_at) > _CACHE_TTL:
            return None
        return self._cache_pix

    # ---------- 内部 ----------
    def _play(self, pix, start: QRect, end: QRect, mode: str, on_done, before=None):
        """mode：reveal（折叠展开）/ conceal（折叠收起）。

        `before` 在浮层已经显示、动画开始之前执行：用来做「先切窗口状态、再播动画」的
        动作（如 showNormal）。它内部的重排耗时被浮层遮住，不会露出桌面。
        """
        union = start.united(end)
        t0 = time.monotonic()
        try:
            sprite = self._spare
            self._spare = None
            if sprite is not None:
                # 复用上一次的浮层：原生分层窗口已存在，省掉 30ms 建窗 + 首次 show 开销
                sprite.set_pix(pix)
            else:
                sprite = FoldSprite(pix)
            sprite.set_radius(self._radius)
            sprite.setGeometry(union)
            sprite.set_content(start)
            sprite.set_alpha(0.0 if mode == "reveal" else 1.0)
            sprite.set_fold(0.0 if mode == "reveal" else 1.0)
            sprite.show()
            sprite.raise_()
        except Exception:
            self._busy = False
            self._mode = None
            if on_done is not None:
                try:
                    on_done()
                except Exception:
                    pass
            return
        _dbg("sprite %s show=%.0fms union=%s" % (mode, (time.monotonic() - t0) * 1000.0, union))
        self._sprite = sprite
        self._set_end_state(end, mode)

        if before is not None:
            # 顺序讲究：`show()` 只是把绘制排进队列，此刻屏幕上一帧都还没有。
            # 若紧接着做真实窗口的状态切换（还原/最大化，含整棵控件树重排，实测
            # 200~300ms），用户看到的是「点一下、整个窗口僵住」——卡顿感全来自这里。
            # 先 repaint() 把浮层真正刷上屏，再把 before 推到下一轮事件循环执行：
            # 那时的重排完全被浮层遮住，屏幕上是静态快照而不是半张没画完的窗口。
            try:
                sprite.repaint()
            except Exception:
                pass

            def _kick():
                # 期间可能被取消 / 被新动画接管：那时不能再动真实窗口
                if not self._busy or self._sprite is not sprite:
                    return
                t1 = time.monotonic()
                try:
                    before()
                except Exception:
                    pass
                _dbg("before(%s)=%.0fms" % (mode, (time.monotonic() - t1) * 1000.0))
                self._run(sprite, start, end, mode, on_done, 0.0)

            QTimer.singleShot(0, _kick)
            return

        self._run(sprite, start, end, mode, on_done, 0.0)

    def _set_end_state(self, end: QRect, mode: str):
        """末帧形态：收起到折叠带（全透明、完全折叠），其余到目标矩形（不透明、完全展开）。"""
        if mode == "conceal":
            self._end_state = (QRect(end), 0.0, 0.0)
        else:
            self._end_state = (QRect(end), 1.0, 1.0)

    def _run(self, sprite, start: QRect, end: QRect, mode: str, on_done, e0: float):
        """在既有浮层上跑一段时间轴。`e0` 为起始进度（0=正常播，>0=反向接播）。"""
        base = {"reveal": REVEAL_MS, "conceal": CONCEAL_MS}[mode]
        # 反向时按剩余行程折算时长；但给一个下限，保证至少能出 6~7 帧，
        # 否则「折叠到一半就点回来」会变成一次瞬移。
        floor = 110 if e0 > 1e-6 else 70
        dur = max(floor, int(round(base * (1.0 - e0))))
        anim = QVariantAnimation(self)
        anim.setDuration(dur)
        anim.setStartValue(float(e0))
        anim.setEndValue(1.0)
        if e0 > 1e-6:
            # 反向接播必须缓起：若沿用 OutCubic（首帧就走掉 54%），接缝处会「嗖」地
            # 窜回原位，看上去像一次跳帧。InOutCubic 让折返点先减速、再加速返回。
            curve = QEasingCurve.Type.InOutCubic
        elif mode == "conceal":
            curve = QEasingCurve.Type.InCubic
        else:
            curve = QEasingCurve.Type.OutCubic
        anim.setEasingCurve(curve)
        _dbg("run %s e0=%.2f dur=%dms" % (mode, e0, dur))
        anim.valueChanged.connect(
            lambda v: self._tick(float(v), start, end, mode, e0))
        anim.finished.connect(self._finish)
        self._anim = anim
        self._mode = mode
        self._on_done = on_done
        anim.start()

        # 安全网：时间轴异常（掉帧/驱动吞掉浮层/主线程长时间阻塞）时强制收尾，
        # 绝不让窗口卡在「浮层残留」或「已最小化但没恢复」的中间态。
        guard = QTimer(self)
        guard.setSingleShot(True)
        guard.timeout.connect(self._finish)
        guard.start(dur + _GUARD_MS)
        self._guard = guard

    def _tick(self, e: float, start: QRect, end: QRect, mode: str, e0: float = 0.0):
        sprite = self._sprite
        if sprite is None:
            return
        self._progress = e
        # 矩形用「归一化进度」重走：反向接播时 e 从 e0 起，t 仍从 0 到 1，
        # 保证第一帧恰好落在当前矩形上（不跳变）。alpha / 折痕直接用 e ——
        # 正反两个方向上 e 表示的是同一个视觉量（见 _reverse 的推导）。
        span = 1.0 - e0
        t = 0.0 if span <= 1e-6 else (e - e0) / span
        x = start.x() + (end.x() - start.x()) * t
        y = start.y() + (end.y() - start.y()) * t
        w = start.width() + (end.width() - start.width()) * t
        h = start.height() + (end.height() - start.height()) * t
        sprite.set_content(QRect(int(round(x)), int(round(y)),
                                 max(1, int(round(w))), max(1, int(round(h)))))
        if mode == "reveal":
            sprite.set_alpha(min(1.0, e / _FADE_PORTION))
            sprite.set_fold(e)
        else:
            sprite.set_alpha(min(1.0, (1.0 - e) / _FADE_PORTION))
            sprite.set_fold(1.0 - e)

    def _finish(self):
        if not self._busy:
            return
        _dbg("finish mode=%s progress=%.2f pending=%s" % (self._mode, self._progress, self._pending))
        self._busy = False
        self._mode = None
        self._progress = 0.0
        on_done, self._on_done = self._on_done, None
        end_state, self._end_state = self._end_state, None
        for name in ("_guard", "_anim", "_defer"):
            obj = getattr(self, name, None)
            setattr(self, name, None)
            try:
                if obj is not None:
                    obj.stop()
            except Exception:
                pass
        sprite, self._sprite = self._sprite, None

        # 1) 先把浮层钉到末帧并同步重绘（末帧与真窗口像素一致）
        if sprite is not None and end_state is not None:
            try:
                rect, alpha, fold = end_state
                sprite.set_content(rect)
                sprite.set_alpha(alpha)
                sprite.set_fold(fold)
                sprite.repaint()
            except RuntimeError:
                sprite = None

        # 2) 切换真实窗口。showNormal/showMaximized 内部会同步跑面板的 resizeEvent /
        #    showEvent（归位子面板、重算气泡，100~200ms），这段时间由浮层继续顶着，
        #    用户看不到桌面。必须延后一拍：在动画 finished 回调里直接切窗口状态会被
        #    平台层吞掉（实测 isMaximized() 变 True 但窗口尺寸不动）。
        if on_done is not None:
            QTimer.singleShot(0, on_done)

        # 3) 延后一拍撤浮层：等真窗口画上去，两帧像素一致所以切换不可见
        if sprite is not None:
            self._retire(sprite)

        # 4) 收尾后接播排队动作（连点任务栏）。等浮层撤完再起手，避免两个浮层同屏；
        #    必须晚于 on_done 的 singleShot(0)，展开的排队最小化才能看到已还原的窗口。
        #    用独立 QTimer 而不是 QTimer.singleShot：实测 singleShot 绑定本对象方法在
        #    动画 finished 回调里偶发不派发，排队动作会被静默丢弃。
        if self._pending is not None:
            defer = QTimer(self)
            defer.setSingleShot(True)
            defer.timeout.connect(self._run_pending)
            defer.start(_RETIRE_MS + 16)
            self._defer = defer

        # 5) 等窗口状态切换彻底落地再放开 DWM 过渡：放早了系统动画会在收尾处冒出来
        self._enable_transitions_later(_RETIRE_MS + 140)

    def _retire(self, sprite):
        """撤浮层。不销毁而是留着复用：原生分层窗口只建一次，下次动画省 30ms。"""
        def _kill():
            try:
                sprite.hide()
                if self._spare is None:
                    self._spare = sprite
                else:
                    sprite.setParent(None)
                    sprite.deleteLater()
            except RuntimeError:
                pass
        QTimer.singleShot(_RETIRE_MS, _kill)

    def _restore_window(self):
        """展开动画收尾：按最小化前的形态把真实窗口放回原位（与浮层末帧像素重合）。"""
        w = self._win
        self._restored_at = time.monotonic()   # 先打标：showNormal 会同步触发 changeEvent
        try:
            if self._restore_full:
                w.showFullScreen()
            elif self._restore_max:
                w.showMaximized()
            else:
                w.showNormal()
            if w.isMinimized():
                # Qt 偶尔认为状态已是 NoState 而跳过 ShowWindow（实测偶发）：
                # 显式再推一次，避免「浮层撤了、窗口还最小化」的假死
                w.setWindowState(Qt.WindowState.WindowNoState)
                w.show()
                if self._restore_full:
                    w.showFullScreen()
                elif self._restore_max:
                    w.showMaximized()
            w.raise_()
            w.activateWindow()
            # 强制同步画一帧：浮层 _RETIRE_MS 之后就撤，必须先保证窗口自己已经有像素，
            # 否则撤浮层到窗口上屏之间会露出桌面（看着像「闪一下又重播一次」）。
            w.repaint()
            self._restored_at = time.monotonic()   # 以「真正还原完成」为准
        except Exception:
            pass
