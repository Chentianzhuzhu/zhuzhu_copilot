"""AI 操控期间的屏幕提示层：彩色呼吸光带 + 顶部胶囊提示窗，零第三方依赖。

两件东西
--------
1. **屏幕四边光带**（`Aura`）：一条贴着屏幕边缘、包围整屏的彩色光带，
   实时带呼吸感 + 轻微闪烁 + 向内扩散的泛光，永远置顶、鼠标穿透。
2. **胶囊提示窗**（`HintCapsule`）：屏幕顶部中央略靠下的无框小窗，
   文字「Agent正在操控您的电脑，移动鼠标/按任意键打断」，
   文字后面跟 8 个同大小同样式的小圆点，绕成一个转圈圆环当等待动画。

三条硬性约束
------------
* **绝不进截图**：两个窗口都调 `SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)`，
  让 `BitBlt` / `PrintWindow` / 图形捕获 API 一律看不到它们 —— AI 截到的画面
  永远是"用户会看到的桌面"，不含任何提示层。系统不支持时退化为
  `capture_guard()` 里临时 `ShowWindow(SW_HIDE)`（见 `_EXCLUDE_ALL`）。
* **绝不吃用户的点击**：分层窗口 + `WS_EX_TRANSPARENT | WS_EX_NOACTIVATE`，
  鼠标键盘全部穿透到下面的真实窗口。
* **性能优先**：见下面的渲染策略。

渲染策略（这是本文件最要紧的设计）
----------------------------------
屏幕 1920×1080 下整屏 DIB 是 8.3MB，逐像素 Python 循环是唯一的热点。实测
（`scripts/_aura_bench.py`）单像素写入约 0.7µs，所以**每帧要写的像素数必须
被设计住**：

* 贴边 5px 的高饱和**核心带**每帧重写（负责色相流动 + 沿边亮度波纹）；
* 向内 9px 的**泛光层**只在建窗时写一次，此后一动不动 —— 泛光本来是柔和的
  静态扩散，不需要流动；
* **呼吸 / 闪烁 / 淡入淡出**全部交给 `BLENDFUNCTION.SourceConstantAlpha`
  做整层 alpha 缩放，**零像素开销**；
* 像素直接写进 `CreateDIBSection` 的内存（`memoryview.from_address`），
  省掉"bytearray 再 memmove 8MB"的整轮拷贝。

代价：每帧约 3 万像素 ≈ 21ms + 一次 `UpdateLayeredWindow`，10~12fps 下
约占单核 30%，且**自带限速**（单帧超时就自动拉长间隔，绝不把 CPU 打满）。
"""

import ctypes
import math
import threading
import time
from ctypes import wintypes, POINTER, byref, c_void_p

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# ---- 光带参数（集中在此，便于调优） ----
AURA_FPS = 12                 # 帧率上限
AURA_CORE = 4                 # 贴边核心亮带宽度（每帧重写）
AURA_GLOW = 8                 # 向内泛光宽度（只写一次，静态；加宽不增加每帧开销）
                              # 合计 12px：17px 嫌粗、9px 又太细（用户反馈"光带消失"），
                              # 12px 是这两者之间；每帧像素 24k，12fps 下约占单核 30%。
AURA_THICKNESS = AURA_CORE + AURA_GLOW
AURA_SATURATION = 0.92        # 核心带的饱和度
AURA_GLOW_SATURATION = 0.88   # 泛光起始饱和度（向内外渐变到 0.36 → 偏白）
AURA_MIN_LIGHT = 0.56         # 最低亮度：保证在浅色壁纸上也能看见
AURA_HUE_CYCLES = 1           # 一圈彩虹绕几遍（整数，否则接缝会断色）
AURA_FLOW_SPEED = 0.30        # 色相沿边流动速度（每秒转过的比例）
AURA_RIPPLE = 6               # 沿边的亮度波纹个数（整数，接缝才连续）
AURA_RIPPLE_AMP = 0.16        # 波纹的 alpha 起伏幅度（±16%）
AURA_BREATH_PERIOD = 3.4      # 呼吸周期（秒）
AURA_BREATH_DEPTH = 0.24      # 呼吸深度：整体亮度在 1.00 ↔ 0.76 之间起伏
                              # （原 0.34 → 最暗 0.66，浅色壁纸上几乎看不见）
AURA_FLICKER_DEPTH = 0.055    # 轻微闪烁幅度（约 Hz 级的微弱抖动）
AURA_FADE_S = 0.35            # 启动/停止淡入淡出时长

# ---- 胶囊提示窗参数 ----
HINT_TEXT = "Agent正在操控您的电脑，移动鼠标/按任意键打断"
AURA_HINT_TOP = 60            # 距屏幕顶部的距离（"略靠下"）
AURA_HINT_FPS = 24            # 转圈动画帧率
AURA_HINT_FONT_PX = 13
AURA_HINT_PAD_X = 18
AURA_HINT_PAD_Y = 8
AURA_HINT_GAP = 10            # 文字与转圈之间的间距
AURA_HINT_DOTS = 8
AURA_HINT_SPIN_S = 1.15       # 转一圈的时间
AURA_HINT_DOT_R = 1.75        # 圆点半径
AURA_HINT_ORBIT_R = 8.4       # 圆点绕行半径（留够间距，相邻两点不会糊成一团）
HINT_BG = (30, 24, 20)        # 胶囊底色 BGRA（近黑，微冷）
HINT_BG_ALPHA = 208
HINT_RIM = 0.55               # 贴边亮边的强度（让胶囊在浅色壁纸上也有轮廓）

# 分层窗口常量
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020     # 鼠标穿透
WS_EX_TOOLWINDOW = 0x00000080      # 不进任务栏/Alt+Tab
WS_EX_NOACTIVATE = 0x08000000      # 永不抢焦点
WS_POPUP = 0x80000000
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
SWP_HIDEWINDOW = 0x0080
# 异步定位：SetWindowPos 默认是同步的 —— 它会向目标窗口所属线程发消息并等它处理。
# 我们的窗口线程只跑渲染循环、**不泵 Windows 消息**，一旦别的线程对别人的窗口调用
# 同步 SetWindowPos，就会永久阻塞（实测光带线程卡死在 hint.raise_top() 上，只剩胶囊）。
# 带这个标志后调用立即返回、由系统异步投递，任何线程调用都不会卡。
SWP_ASYNCWINDOWPOS = 0x4000
ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
BI_RGB = 0
HWND_TOPMOST = -1
SW_HIDE = 0
SW_SHOWNOACTIVATE = 4
# 截图排除（Windows 10 2004+）：窗口照常显示在屏幕上，但捕获 API 里完全不存在
WDA_EXCLUDEFROMCAPTURE = 0x00000011

# GDI 文本常量
TRANSPARENT_BK = 1
ANTIALIASED_QUALITY = 4
FW_MEDIUM = 500
DEFAULT_CHARSET = 1
OUT_TT_PRECIS = 10
CLIP_DEFAULT_PRECIS = 0
DEFAULT_PITCH = 0
FF_DONTCARE = 0


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", wintypes.BYTE), ("BlendFlags", wintypes.BYTE),
                ("SourceConstantAlpha", wintypes.BYTE), ("AlphaFormat", wintypes.BYTE)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


def _bind():
    # GetModuleHandleW 的返回值是 64 位 HMODULE，**必须声明 restype**：
    # 默认 restype 是 c_int，会把模块基址截断成 32 位，再当 hInstance 传给
    # CreateWindowExW 就成了野指针 —— 实测在 pytest 里拉起时直接 access violation。
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                       wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                       wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p]
    user32.UpdateLayeredWindow.restype = wintypes.BOOL
    user32.UpdateLayeredWindow.argtypes = [wintypes.HWND, wintypes.HDC, POINTER(wintypes.POINT),
                                           POINTER(wintypes.SIZE), wintypes.HDC,
                                           POINTER(wintypes.POINT), wintypes.DWORD,
                                           POINTER(BLENDFUNCTION), wintypes.DWORD]
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.GetDC.restype = wintypes.HDC
    user32.GetDC.argtypes = (wintypes.HWND,)
    user32.ReleaseDC.argtypes = (wintypes.HWND, wintypes.HDC)
    user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, POINTER(MONITORINFO)]
    # GetMonitorInfoW 的第一个参数必须是**有效 HMONITOR**：早先一直传 0，
    # 函数直接失败，于是 _work_area/_screen_size 双双退到 GetSystemMetrics（整屏），
    # 光带就被任务栏盖住一截。这里显式从桌面窗口取主屏监视器。
    user32.GetDesktopWindow.restype = wintypes.HWND
    user32.MonitorFromWindow.restype = wintypes.HANDLE
    user32.MonitorFromWindow.argtypes = (wintypes.HWND, wintypes.DWORD)
    user32.DestroyWindow.argtypes = (wintypes.HWND,)
    user32.IsWindow.argtypes = (wintypes.HWND,)
    user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
    user32.SetWindowDisplayAffinity.argtypes = (wintypes.HWND, wintypes.DWORD)
    user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
    gdi32.TextOutW.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int,
                               wintypes.LPCWSTR, ctypes.c_int]
    gdi32.TextOutW.restype = wintypes.BOOL
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleDC.argtypes = (wintypes.HDC,)
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    gdi32.CreateDIBSection.argtypes = [wintypes.HDC, POINTER(BITMAPINFO), wintypes.UINT,
                                       POINTER(c_void_p), wintypes.HANDLE, wintypes.DWORD]
    gdi32.SelectObject.restype = ctypes.c_void_p
    gdi32.SelectObject.argtypes = (wintypes.HDC, ctypes.c_void_p)
    gdi32.DeleteObject.argtypes = (ctypes.c_void_p,)
    gdi32.DeleteDC.argtypes = (wintypes.HDC,)
    gdi32.CreateFontW.restype = ctypes.c_void_p
    gdi32.CreateFontW.argtypes = [ctypes.c_int] * 5 + [wintypes.DWORD] * 8 + [wintypes.LPCWSTR]
    gdi32.SetBkMode.argtypes = (wintypes.HDC, ctypes.c_int)
    gdi32.SetTextColor.argtypes = (wintypes.HDC, wintypes.DWORD)
    gdi32.GetTextExtentPoint32W.argtypes = [wintypes.HDC, wintypes.LPCWSTR,
                                            ctypes.c_int, POINTER(wintypes.SIZE)]


_bind()


def _hsl_to_rgb(h, s, l):
    """HSL(0..1) → (r,g,b) 0..255。h 为色相（0..1，循环）。"""
    c = (1.0 - abs(2.0 * l - 1.0)) * s
    hp = (h % 1.0) * 6.0
    x = c * (1.0 - abs(hp % 2.0 - 1.0))
    m = l - c / 2.0
    if hp < 1:
        r, g, b = c, x, 0.0
    elif hp < 2:
        r, g, b = x, c, 0.0
    elif hp < 3:
        r, g, b = 0.0, c, x
    elif hp < 4:
        r, g, b = 0.0, x, c
    elif hp < 5:
        r, g, b = x, 0.0, c
    else:
        r, g, b = c, 0.0, x
    return (int(max(0.0, min(1.0, r + m)) * 255),
            int(max(0.0, min(1.0, g + m)) * 255),
            int(max(0.0, min(1.0, b + m)) * 255))


MONITOR_DEFAULTTOPRIMARY = 1


def _primary_monitor() -> int:
    """主屏的 HMONITOR。

    必须经 MonitorFromWindow 拿，不能给 GetMonitorInfoW 传 0（那不是合法句柄，
    调用会直接失败 —— 这正是「光带按整屏画、底部被任务栏吞掉」的根因）。
    """
    try:
        h = user32.MonitorFromWindow(user32.GetDesktopWindow(),
                                     MONITOR_DEFAULTTOPRIMARY)
        return int(h or 0)
    except Exception:
        return 0


def _work_area() -> tuple:
    """主屏**可用桌面区域** (x, y, w, h)：rcWork，已排除任务栏。

    为什么光带围 rcWork 而不是 rcMonitor（**别改回去，这是实测结论**）：
    任务栏（Shell_TrayWnd）属于 Windows 的置顶系统 band，任何应用窗口都盖不住它 ——
    实测把自己设成 HWND_TOPMOST、并每 1.2s 重申一次，EnumWindows 里任务栏依然排在
    我们前面（z 索引 10 vs 我们的 32）。按整屏画的结果就是：屏幕底部那一条被任务栏
    整条吞掉，用户看到「光带没有包围全屏、底部缺一块」。

    围 rcWork 之后：任务栏上沿正好是光带的底边，四边完整闭合；任务栏自动隐藏时
    rcWork 等于整屏，光带自然贴到物理边 —— 两种设置下都不会缺角。
    """
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    if user32.GetMonitorInfoW(_primary_monitor(), ctypes.byref(mi)):
        r = mi.rcWork
        w, h = int(r.right - r.left), int(r.bottom - r.top)
        if w > 0 and h > 0:
            return (int(r.left), int(r.top), w, h)
    w, h = _screen_size()
    return (0, 0, w, h)


def _screen_size() -> tuple:
    """主屏物理分辨率（整屏，含任务栏区域）。"""
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    if user32.GetMonitorInfoW(_primary_monitor(), ctypes.byref(mi)):
        r = mi.rcMonitor
        return (int(r.right - r.left), int(r.bottom - r.top))
    return (int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1)))


# ============================================================================
# 分层窗口底座：建窗 / DIB / 直写内存 / 推帧 / 截图排除
# ============================================================================

# 当前所有活跃的分层窗口：截图排除与显隐兜底都靠这张表
_LAYERS = []
_CAPTURE_LOCK = threading.Lock()
_EXCLUDE_ALL = True        # 全部窗口都成功设了 WDA_EXCLUDEFROMCAPTURE 才为 True


class _LayeredWindow:
    """逐像素 alpha 的无边框分层窗口。

    像素内存通过 `memoryview.from_address` 直接映射到 `CreateDIBSection` 给出的
    地址，写像素 = 直接写显存位图，不需要"先写 bytearray 再 memmove"的整轮拷贝。
    """

    def __init__(self, name: str = "zl-layer"):
        self.name = name
        self._hwnd = 0
        self._screen_dc = 0
        self._mem_dc = 0
        self._bmp = 0
        self._bits = c_void_p()
        self._addr = 0
        self._mv = None
        self._owner = None
        self._w = 0
        self._h = 0
        self._x = 0
        self._y = 0
        self._failed = False
        self._error = ""
        self._visible = False
        self._excluded = False
        self._blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, 1)
        self._size = None
        self._dst = None
        self._src = wintypes.POINT(0, 0)

    # ---- 建/拆 ----
    def create(self, x: int, y: int, w: int, h: int) -> bool:
        global _EXCLUDE_ALL
        w, h = int(w), int(h)
        if w <= 0 or h <= 0:
            self._failed, self._error = True, "窗口尺寸无效"
            return False
        self._w, self._h, self._x, self._y = w, h, int(x), int(y)
        self._screen_dc = user32.GetDC(0)
        if not self._screen_dc:
            self._failed, self._error = True, "GetDC 失败"
            return False
        self._hwnd = user32.CreateWindowExW(
            WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
            "STATIC", self.name, WS_POPUP, self._x, self._y, w, h,
            0, 0, kernel32.GetModuleHandleW(None), None)
        if not self._hwnd:
            self._failed = True
            self._error = f"CreateWindowExW 失败 err={ctypes.get_last_error()}"
            return False
        user32.SetWindowPos(self._hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE |
                            SWP_ASYNCWINDOWPOS | SWP_SHOWWINDOW)
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = w
        bmi.bmiHeader.biHeight = -h              # 自顶向下
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = BI_RGB
        self._mem_dc = gdi32.CreateCompatibleDC(self._screen_dc)
        if not self._mem_dc:
            self._failed, self._error = True, "CreateCompatibleDC 失败"
            return False
        self._bits = c_void_p()
        self._bmp = gdi32.CreateDIBSection(self._screen_dc, ctypes.byref(bmi), 0,
                                           ctypes.byref(self._bits), None, 0)
        if not self._bmp or not self._bits:
            self._failed, self._error = True, "CreateDIBSection 失败"
            return False
        gdi32.SelectObject(self._mem_dc, self._bmp)
        self._addr = ctypes.cast(self._bits, ctypes.c_void_p).value or 0
        if not self._addr:
            self._failed, self._error = True, "像素缓冲地址为空"
            return False
        # 直接映射位图内存：写像素不需要任何中间拷贝
        self._owner = (ctypes.c_ubyte * (w * h * 4)).from_address(self._addr)
        self._mv = memoryview(self._owner).cast("B")
        ctypes.memset(self._addr, 0, w * h * 4)
        self._size = wintypes.SIZE(w, h)
        self._dst = wintypes.POINT(self._x, self._y)
        # 截图排除：让所有捕获 API 都看不见这扇窗（做不到就记下来，改用临时隐藏）
        try:
            self._excluded = bool(user32.SetWindowDisplayAffinity(
                self._hwnd, WDA_EXCLUDEFROMCAPTURE))
        except Exception:
            self._excluded = False
        if not self._excluded:
            with _CAPTURE_LOCK:
                _EXCLUDE_ALL = False
        with _CAPTURE_LOCK:
            _LAYERS.append(self)
        self._visible = True
        return True

    def destroy(self):
        with _CAPTURE_LOCK:
            if self in _LAYERS:
                _LAYERS.remove(self)
        try:
            if self._hwnd and user32.IsWindow(self._hwnd):
                user32.DestroyWindow(self._hwnd)
        except Exception:
            pass
        try:
            if self._bmp:
                gdi32.DeleteObject(self._bmp)
        except Exception:
            pass
        try:
            if self._mem_dc:
                gdi32.DeleteDC(self._mem_dc)
        except Exception:
            pass
        try:
            if self._screen_dc:
                user32.ReleaseDC(0, self._screen_dc)
        except Exception:
            pass
        self._mv = None
        self._owner = None
        self._hwnd = 0
        self._bmp = 0
        self._mem_dc = 0
        self._screen_dc = 0
        self._bits = c_void_p()
        self._visible = False

    # ---- 显隐（截图兜底用） ----
    def hide(self):
        """隐藏窗口（截图兜底：系统不支持截图排除时才走这里）。

        用带 `SWP_ASYNCWINDOWPOS` 的 SetWindowPos 而不是 `ShowWindow`：后者同样是
        跨线程窗口操作，目标线程不泵消息时会阻塞调用方（截图线程卡住 = 工具超时）。
        """
        if self._hwnd and user32.IsWindow(self._hwnd):
            user32.SetWindowPos(self._hwnd, 0, 0, 0, 0, 0,
                                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE |
                                SWP_ASYNCWINDOWPOS | SWP_HIDEWINDOW)
            self._visible = False

    def show(self):
        if self._hwnd and user32.IsWindow(self._hwnd):
            user32.SetWindowPos(self._hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE |
                                SWP_ASYNCWINDOWPOS | SWP_SHOWWINDOW)
            self._visible = True

    def raise_top(self):
        """重新提到 topmost 组最前。

        为什么必须周期性重申：任务栏（Shell_TrayWnd）本身就是 topmost 窗口，它在
        我们建窗之后只要再调一次 SetWindowPos，就会排到我们前面 —— 于是屏幕底部
        那条光带被任务栏整条盖住，看起来就是「光带没有包围全屏、底部缺一截」。
        置顶组内只能靠「再设一次 HWND_TOPMOST」把自己挪回最前。

        **必须带 SWP_ASYNCWINDOWPOS**：本方法可能被光带线程用来重申**胶囊窗口**
        的置顶，而胶囊窗口属于胶囊线程。两个线程都不泵消息，同步 SetWindowPos 会
        互相发消息并永久等待 —— 这正是「首帧之后光带线程卡死、只剩胶囊在转」的
        根因（faulthandler 实测栈停在 SetWindowPos）。异步标志让调用立即返回。
        """
        if not self._hwnd or not user32.IsWindow(self._hwnd):
            return
        try:
            user32.SetWindowPos(self._hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE |
                                SWP_ASYNCWINDOWPOS)
        except Exception:
            pass

    # ---- 推帧 ----
    def push(self, sca: int = 255):
        """把位图推到屏幕（alpha 叠加）。sca=整层 alpha 缩放，呼吸/闪烁全靠它。"""
        if not self._hwnd or not self._mem_dc:
            return
        self._blend.SourceConstantAlpha = max(0, min(255, int(sca)))
        try:
            user32.UpdateLayeredWindow(self._hwnd, self._screen_dc, byref(self._dst),
                                       byref(self._size), self._mem_dc, byref(self._src),
                                       0, byref(self._blend), ULW_ALPHA)
        except Exception:
            pass


def capture_guard():
    """截图期间的上下文管理器：确保捕获画面里没有提示层。

    正常情况（`WDA_EXCLUDEFROMCAPTURE` 生效）它什么都不做 —— 窗口照常显示，
    只是不出现在 `BitBlt`/`PrintWindow` 的结果里，用户看不到任何闪烁。
    系统不支持时才退化成"截前隐藏、截后恢复"，此时必须紧跟截图调用，别嵌套太久。
    """
    return _CaptureGuard()


class _CaptureGuard:
    def __enter__(self):
        self._hidden = []
        if not _EXCLUDE_ALL:
            with _CAPTURE_LOCK:
                targets = [lw for lw in _LAYERS if lw._visible]
            for lw in targets:
                try:
                    lw.hide()
                    self._hidden.append(lw)
                except Exception:
                    pass
            if self._hidden:
                time.sleep(0.012)      # 给 DWM 一帧把窗口真正撤下画面
        return self

    def __exit__(self, *exc):
        for lw in self._hidden:
            try:
                lw.show()
            except Exception:
                pass
        self._hidden = []
        return False


def capture_exclude_supported() -> bool:
    """截图排除是否走的是原生路径（False 表示靠临时隐藏兜底）。"""
    return bool(_EXCLUDE_ALL)


# ============================================================================
# 屏幕四边光带
# ============================================================================

class Aura:
    """屏幕四边彩色光带：AI 操控期间呼吸流动，停止后淡出。"""

    def __init__(self, thickness: int = 0, fps: int = AURA_FPS):
        if thickness and int(thickness) != AURA_CORE + AURA_GLOW:
            # 兼容旧签名：显式给了总粗细就按 36% / 64% 拆成核心带与泛光
            self.thickness = max(3, int(thickness))
            self.core = max(1, int(round(self.thickness * 0.36)))
            self.glow = max(1, self.thickness - self.core)
        else:
            self.core, self.glow = AURA_CORE, AURA_GLOW
            self.thickness = self.core + self.glow
        self.interval = 1.0 / max(1, int(fps))
        self._lock = threading.Lock()
        self._thread = None
        self._hint_thread = None
        self._stop = threading.Event()
        self._active = False
        self._failed = False
        self._error = ""
        self._ring = None
        self._hint = None
        self._core_px = []          # (offset*4, pos_idx, depth_idx)
        self._glow_px = []
        self._core_lut = []
        self._glow_lut = []
        self._start_ts = 0.0
        self._stop_ts = 0.0
        self._frame_cost = 0.0      # 最近一帧的实际耗时（秒），用于自动限速
        self._hint_error = ""       # 胶囊窗建窗/渲染异常（不静默吞掉，供排障）
        self._bands = []            # 四条边条窗口（上/下/左/右）
        self._band_core = []        # 每条边的核心像素（局部偏移）
        self._band_glow = []        # 每条边的泛光像素（局部偏移）
        self._last_top_ts = 0.0      # 上次重申 topmost 的时间（防任务栏盖住底边）

    # ---- 对外接口 ----
    def start(self) -> bool:
        """启动光带/胶囊两个渲染线程（幂等，且**分别**自愈）。

        为什么必须分开判断：两个线程的生命周期并不一致 —— 光带线程负责驱动淡入
        淡出的时间轴，胶囊线程在"淡出到底"时会先退出。早先 `start()` 只检查光带
        线程，于是出现过这种故障：胶囊线程先退了，之后再怎么点亮都**只亮光带、
        永远不出胶囊**（调用方看起来像功能坏了，其实是线程没被重启）。
        所以这里对两个线程各自判活、各自拉起；任一线程死了就补一个。

        **失败不永久锁死**：早期一次建窗失败（CreateWindowExW 偶发失败、DIB 分配
        失败、桌面尺寸取不到）会把 `_failed` 置真，而 `start()` 见到 `_failed`
        就直接返回 False —— 于是**光带从那以后再也不会出现**，而且没有任何提示
        （调用方把异常吞了）。用户看到的现象就是「提示层莫名其妙就没了」。
        现在失败只影响当次尝试，下一次点亮会重新尝试。
        """
        with self._lock:
            ring_dead = not (self._thread and self._thread.is_alive())
            hint_dead = not (self._hint_thread and self._hint_thread.is_alive())
            if not (ring_dead or hint_dead):
                return False
            # 任一线程要重启，就必须先撤掉停止信号（否则新线程一起跑就被叫停）
            self._stop.clear()
            if self._failed:
                # 上次失败过：清掉标记重来一次（连续失败会一直重来，但每次都只有
                # 一次窗口创建的开销，不会变成忙等）
                self._failed = False
                self._error = ""
            if ring_dead:
                self._thread = threading.Thread(target=self._run_ring, daemon=True,
                                                name="zhuzhu-aura")
                self._thread.start()
            if hint_dead:
                self._hint_thread = threading.Thread(target=self._run_hint, daemon=True,
                                                     name="zhuzhu-aura-hint")
                self._hint_thread.start()
            return True

    def stop(self, timeout: float = 1.5):
        """请求停止并等线程收尾（幂等）。"""
        self._stop.set()
        for th in (self._thread, self._hint_thread):
            if th and th.is_alive():
                th.join(timeout)
        with self._lock:
            self._thread = None
            self._hint_thread = None

    def set_active(self, flag: bool):
        """True=操控中显示提示层，False=淡出并隐藏。"""
        flag = bool(flag)
        with self._lock:
            self._active = flag
            need = flag and not (self._thread and self._thread.is_alive()
                                 and self._hint_thread and self._hint_thread.is_alive())
        if need:
            self.start()

    @property
    def active(self) -> bool:
        return self._active

    def capability(self) -> str:
        if self._failed:
            return f"屏幕提示层不可用（{self._error}）"
        cap = (f"屏幕四边彩色光带（{self.core}px 核心 + {self.glow}px 泛光 / "
               f"{int(round(1 / self.interval))}fps / 呼吸+闪烁 / 鼠标穿透）")
        hint = f"顶部胶囊提示窗（{AURA_HINT_DOTS} 点转圈）"
        if self._hint_error:
            hint = f"顶部胶囊提示窗异常（{self._hint_error}）"
        tail = "" if _EXCLUDE_ALL else "；截图排除走隐藏兜底"
        return f"{cap} + {hint}{tail}"

    # ---- 时间轴（纯函数，两个线程都可安全调用） ----
    def _sync_timers(self, now: float):
        if self._active:
            if not self._start_ts:
                self._start_ts = now
            self._stop_ts = 0.0
        else:
            if not self._stop_ts:
                self._stop_ts = now
            self._start_ts = 0.0

    def _fade_at(self, now: float) -> float:
        if self._active:
            if not self._start_ts:
                return 0.0
            return max(0.0, min(1.0, (now - self._start_ts) / AURA_FADE_S))
        if not self._stop_ts:
            return 1.0
        return max(0.0, min(1.0, 1.0 - (now - self._stop_ts) / AURA_FADE_S))

    @staticmethod
    def _pulse(now: float) -> int:
        """呼吸 + 轻微闪烁 → 整层 alpha 缩放系数（0..255），零像素开销。"""
        ph = (now % AURA_BREATH_PERIOD) / AURA_BREATH_PERIOD
        breath = 1.0 - AURA_BREATH_DEPTH * (0.5 - 0.5 * math.cos(2.0 * math.pi * ph))
        flick = 1.0 + AURA_FLICKER_DEPTH * (0.62 * math.sin(now * 57.0)
                                            + 0.38 * math.sin(now * 93.1 + 1.7))
        return int(max(0.0, min(1.0, breath * flick)) * 255)

    # ---- 线程 ----
    def _run_ring(self):
        try:
            if not self._create_ring():
                return
            last = 0.0
            while not self._stop.is_set():
                now = time.time()
                if now - last >= self.interval:
                    last = now
                    t0 = time.perf_counter()
                    self._draw_ring(now)
                    cost = time.perf_counter() - t0
                    self._frame_cost = cost
                    # 自动限速：单帧已经吃掉间隔的大半就拉长节拍，绝不把 CPU 打满
                    if cost > self.interval * 0.7:
                        last = now + cost * 0.35
                time.sleep(0.006)
        except Exception as e:
            self._failed = True
            self._error = f"{type(e).__name__}: {e}"
            self._stop.set()
        finally:
            for lw in getattr(self, "_bands", None) or []:
                lw.destroy()
            self._bands = []
            self._band_core = []
            self._band_glow = []
            self._ring = None

    def _run_hint(self):
        interval = 1.0 / max(1, AURA_HINT_FPS)
        try:
            # 等光环线程把 _active 的时间轴推进起来再建窗，避免闪一下空胶囊
            for _ in range(200):
                if self._stop.is_set() or self._active:
                    break
                time.sleep(0.01)
            if self._stop.is_set() or self._failed:
                return          # 光带建窗失败时淡入时间轴永远不会推进，胶囊别空转
            if not self._create_hint():
                return
            last_top = 0.0
            while not self._stop.is_set():
                now = time.time()
                fade = self._fade_at(now)
                drawn = self._hint_draw(now)
                if not drawn:
                    break
                # 胶囊只跟淡入淡出走，**不参与**呼吸/闪烁：上面有要读的文字，
                # 跟着一起忽明忽暗会看不清（呼吸感只属于边缘光带）。
                self._hint.push(int(255 * fade))
                # 置顶重申由**本线程**完成：胶囊窗口属于本线程，跨线程同步
                # SetWindowPos 会永久阻塞（见 raise_top 的注释）。
                if now - last_top >= 2.0:
                    last_top = now
                    self._hint.raise_top()
                if fade <= 0.004 and not self._active:
                    break
                time.sleep(interval)
            # 淡出到底：补一帧全透明再收窗
            try:
                self._hint.push(0)
            except Exception:
                pass
        except Exception as e:
            self._hint_error = f"{type(e).__name__}: {e}"
        finally:
            if self._hint:
                self._hint.destroy()
                self._hint = None

    # ---- 光带：建窗（四条独立边条，不是一个全屏大窗）----
    def _create_ring(self) -> bool:
        """建上/下/左/右四条边条窗口。

        **为什么从「一个全屏分层窗口」改成四条边条**（实测结论，别改回去）：
        全屏方案要为 1920×1080 开一块 **8MB 的 DIB**，每帧还要遍历 198 万个像素
        （哪怕只有 12px 宽的那一圈有用）。在内存紧张、或叠了一堆大型应用的机器上，
        这块 DIB 的分配/提交会失败或慢到看不出效果 —— 用户侧现象是「胶囊在、光带
        没有」，而异常被 `_run_ring` 的 finally 吞掉，**一点线索都不留**。
        四条边条的总内存是 368KB（1/20），每帧像素 7 万（1/28），且任一条边建窗失败
        都能精确定位是哪条。
        """
        wx, wy, width, height = _work_area()
        if width <= 0 or height <= 0:
            self._failed, self._error = True, "无法获取可用桌面区域"
            return False
        thick = max(2, self.core + self.glow)
        specs = [
            ("top", wx, wy, width, thick),
            ("bottom", wx, wy + height - thick, width, thick),
            ("left", wx, wy, thick, height),
            ("right", wx + width - thick, wy, thick, height),
        ]
        bands = []
        for name, bx, by, bw, bh in specs:
            lw = _LayeredWindow(f"zhuzhu-aura-{name}")
            if not lw.create(bx, by, bw, bh):
                for old in bands:
                    old.destroy()
                self._failed = True
                self._error = f"{name} 边建窗失败：{lw._error}"
                return False
            bands.append(lw)
        self._bands = bands
        # 记住工作区尺寸：_build_pixels 的「全屏坐标」要用它（不能读某条边的小尺寸）
        self._width, self._height = width, height
        self._ring = bands[0]        # 兼容旧字段（自检/日志用）
        self._build_luts()
        self._build_pixels()
        self._paint_static()
        return True

    # ---- 光带：查找表 ----
    def _build_luts(self):
        steps = 256
        core_lut = []
        glow_lut = []
        for i in range(steps):
            pos = i / float(steps)
            hue = (pos * AURA_HUE_CYCLES) % 1.0
            ripple = 1.0 + AURA_RIPPLE_AMP * math.sin(2.0 * math.pi * pos * AURA_RIPPLE)
            crow = []
            grow = []
            for j in range(steps):
                d = j / float(steps - 1)
                # 核心带：贴边最亮最浓，向内侧略微收暗（L 0.56→0.72 才有"彩色"的浓度，
                # 早先用 0.72→0.92 太浅，一过白底就糊成粉白）
                light = AURA_MIN_LIGHT + 0.16 * (1.0 - d)
                r, g, b = _hsl_to_rgb(hue, AURA_SATURATION, light)
                a = int((250 - 26 * d) * ripple)
                crow.append((b, g, r, max(0, min(255, a))))
                # 泛光：从核心带内沿接上去，越往外越淡越白，最后归零
                gl = 0.62 + 0.14 * d
                gs = AURA_GLOW_SATURATION - 0.52 * d
                r, g, b = _hsl_to_rgb(hue, max(0.05, gs), gl)
                ga = int(228 * (1.0 - d) ** 1.9)
                grow.append((b, g, r, max(0, ga)))
            core_lut.append(crow)
            glow_lut.append(grow)
        self._core_lut = core_lut
        self._glow_lut = glow_lut

    # ---- 光带：像素清单 ----
    def _build_pixels(self):
        """算出属于光带的所有像素 → (字节偏移, 色相索引, 深度索引)。

        `self._core_px` 每帧重写（色相流动 + 亮度波纹），`self._glow_px` 只在建窗
        时写一次（泛光是静态扩散，不需要动）。返回值是两者之和，供测试/自检核对
        "四边都被覆盖、屏幕中心不被点亮"。
        """
        core, glow = self.core, self.glow
        total = core + glow
        # 全屏（工作区）尺寸：优先用建窗时记下的，其次才是测试里手填的
        w = int(getattr(self, "_width", 0) or 0)
        h = int(getattr(self, "_height", 0) or 0)
        if not w or not h:
            w, h = 1920, 1080
        steps = 256
        core_px, glow_px = [], []
        seen = set()
        # 沿**整圈周长**参数化：色相顺着边界一路绕过去，四个转角才不会有色相断层。
        # 早先横边用 x/w、竖边用 y/h 各自归一化，两条边在转角处色相对不上，
        # 会看到一条生硬的颜色切缝（实测在左上角非常明显）。
        perim = float(2 * (w + h) - 4)

        def _pos(x, y, d):
            if d == y:                              # 上边：从左到右
                s = x
            elif d == h - 1 - y:                    # 下边：从右到左（顺着绕过去）
                s = 2 * w + h - 3 - x
            elif d == x:                            # 左边：从下到上
                s = 2 * (w + h) - 4 - y
            else:                                   # 右边：从上到下
                s = w - 1 + y
            return s / perim

        def _emit(x, y):
            d = min(y, h - 1 - y, x, w - 1 - x)
            if d >= total:
                return
            off = y * w + x
            if off in seen:
                return
            seen.add(off)
            pos_i = int(_pos(x, y, d) * steps) & (steps - 1)
            if d < core:
                dep = 0 if core <= 1 else d / float(core - 1)
                core_px.append((off << 2, pos_i, int(dep * (steps - 1))))
            else:
                q = (d - core) / float(max(1, glow))
                glow_px.append((off << 2, pos_i, int(min(1.0, q) * (steps - 1))))

        # 上下横边（含四个角）
        for y in list(range(total)) + list(range(h - total, h)):
            for x in range(w):
                _emit(x, y)
        # 左右竖边（跳过已被横边覆盖的转角行）
        for x in list(range(total)) + list(range(w - total, w)):
            for y in range(total, h - total):
                _emit(x, y)
        self._core_px = core_px
        self._glow_px = glow_px
        self._split_into_bands()
        return core_px + glow_px

    def _split_into_bands(self):
        """把全屏坐标的像素清单按所属边条重新编组（局部字节偏移）。

        全屏坐标只在自检/测试里用；真正绘制时每条边只写自己那一小块 DIB，
        所以这里把 `off4`（全屏偏移）换算成各边条内的局部偏移。
        """
        bands = getattr(self, "_bands", None) or []
        self._band_core = [[] for _ in bands]
        self._band_glow = [[] for _ in bands]
        if not bands:
            return
        w = self._width
        for lst, px in ((self._band_core, self._core_px),
                        (self._band_glow, self._glow_px)):
            for off4, pi, di in px:
                off = off4 >> 2
                x, y = off % w, off // w
                for i, lw in enumerate(bands):
                    lx, ly = x - lw._x, y - lw._y
                    if 0 <= lx < lw._w and 0 <= ly < lw._h:
                        lst[i].append(((ly * lw._w + lx) << 2, pi, di))
                        break

    # ---- 光带：静态层（泛光 + 核心带的初始帧），逐边条写 ----
    def _paint_static(self):
        glow_lut = self._glow_lut
        core_lut = self._core_lut
        for i, lw in enumerate(getattr(self, "_bands", None) or []):
            mv = lw._mv
            if mv is None:
                continue
            for off4, pi, di in self._band_glow[i]:
                b, g, r, a = glow_lut[pi][di]
                mv[off4] = b
                mv[off4 + 1] = g
                mv[off4 + 2] = r
                mv[off4 + 3] = a
            for off4, pi, di in self._band_core[i]:
                b, g, r, a = core_lut[pi][di]
                mv[off4] = b
                mv[off4 + 1] = g
                mv[off4 + 2] = r
                mv[off4 + 3] = a
            lw.push(0)

    # ---- 光带：动画帧 ----
    def _draw_ring(self, now: float):
        self._sync_timers(now)
        fade = self._fade_at(now)
        if not self._active and fade <= 0.004:
            for lw in getattr(self, "_bands", None) or []:
                lw.push(0)
            self._stop.set()      # 淡出完成 → 线程收尾并清理
            return
        # 周期性重申置顶（2s 一次）：任务栏是 topmost，会在我们之后抢到更前的位置，
        # 把底部光带整条盖掉。四条边都要重申。
        # **只重申本线程自己的窗口**：胶囊窗属于胶囊线程，跨线程 SetWindowPos 即使
        # 加了异步标志也没必要，交给胶囊线程自己重申（它在 _run_hint 里做）。
        if now - self._last_top_ts >= 2.0:
            self._last_top_ts = now
            for lw in getattr(self, "_bands", None) or []:
                lw.raise_top()
        lut = self._core_lut
        shift = int(now * AURA_FLOW_SPEED * 256) & 255
        sca = int(self._pulse(now) * fade)
        for i, lw in enumerate(getattr(self, "_bands", None) or []):
            mv = lw._mv
            if mv is None:
                continue
            for off4, pi, di in self._band_core[i]:
                b, g, r, a = lut[(pi + shift) & 255][di]
                mv[off4] = b
                mv[off4 + 1] = g
                mv[off4 + 2] = r
                mv[off4 + 3] = a
            lw.push(sca)

    # ---- 胶囊提示窗 ----
    def _create_hint(self) -> bool:
        hint = HintCapsule()
        if not hint.build(HINT_TEXT):
            return False
        self._hint = hint
        return True

    def _hint_draw(self, now: float) -> bool:
        hint = self._hint
        if hint is None:
            return False
        return hint.draw_dots(now)

    @staticmethod
    def _screen_size() -> tuple:
        return _screen_size()


# ============================================================================
# 顶部胶囊提示窗
# ============================================================================

class HintCapsule:
    """屏幕顶部中央略靠下的胶囊无框窗口：一行文字 + 8 点转圈等待动画。

    文字与胶囊底是**一次性**画好写进位图的（GDI 画字 → 亮度当 alpha → 与底色
    合成），此后每帧只重绘转圈那一小块区域（约 20×20 像素），开销可以忽略。
    """

    def __init__(self):
        self._win = None
        self._text = ""
        self._ring_cx = 0.0
        self._ring_cy = 0.0
        self._box = (0, 0, 0, 0)      # 转圈区域的像素包围盒 (x0, y0, x1, y1)
        self._bg_row = b""            # 清场用的整行底色
        self._dot_lut = []            # 圆点 alpha → 合成后的 BGRA
        self._angle0 = 0.0
        self._w = 0
        self._h = 0

    # ---- 建窗 ----
    def build(self, text: str) -> bool:
        self._text = text
        wx, wy, sw, sh = _work_area()      # 与光带同一坐标系：可用桌面区域
        if sw <= 0 or sh <= 0:
            return False
        # 先量文字：建一个临时 DC 量完就扔，正式位图按量出来的宽度开
        sdc = user32.GetDC(0)
        tmp_dc = gdi32.CreateCompatibleDC(sdc)
        font = _create_font(AURA_HINT_FONT_PX)
        old = gdi32.SelectObject(tmp_dc, font)
        sz = wintypes.SIZE(0, 0)
        try:
            gdi32.GetTextExtentPoint32W(tmp_dc, text, len(text), ctypes.byref(sz))
        finally:
            gdi32.SelectObject(tmp_dc, old)
            gdi32.DeleteObject(font)
            gdi32.DeleteDC(tmp_dc)
            user32.ReleaseDC(0, sdc)
        text_w = int(sz.cx) if sz.cx > 0 else 260
        text_h = int(sz.cy) if sz.cy > 0 else 17
        box = int(AURA_HINT_ORBIT_R * 2 + AURA_HINT_DOT_R * 2 + 4)     # 转圈占位（留 2px 余量给抗锯齿）
        w = AURA_HINT_PAD_X * 2 + text_w + AURA_HINT_GAP + box
        h = max(text_h + AURA_HINT_PAD_Y * 2, box + AURA_HINT_PAD_Y * 2)
        x = wx + max(0, (sw - w) // 2)
        y = wy + int(AURA_HINT_TOP)
        self._w, self._h = w, h

        win = _LayeredWindow("zhuzhu-aura-hint")
        if not win.create(x, y, w, h):
            return False
        self._win = win
        mv = win._mv

        # 1) 先把文字用 GDI 画进位图（黑底白字）—— 掩膜
        gdi32.SetBkMode(win._mem_dc, TRANSPARENT_BK)
        gdi32.SetTextColor(win._mem_dc, 0x00FFFFFF)      # COLORREF 是 BGR
        font = _create_font(AURA_HINT_FONT_PX)
        old = gdi32.SelectObject(win._mem_dc, font)
        try:
            gdi32.TextOutW(win._mem_dc, AURA_HINT_PAD_X, (h - text_h) // 2,
                           text, len(text))
        finally:
            gdi32.SelectObject(win._mem_dc, old)
            gdi32.DeleteObject(font)

        # 2) 亮度当 alpha：GDI 只写 RGB，不写 alpha，直接推上去是"完全透明"的，
        #    所以逐像素取最亮通道作为文字覆盖率，再把颜色统一成白色。
        text_alpha = bytearray(w * h)
        for p in range(w * h):
            q = p * 4
            lum = mv[q]
            if mv[q + 1] > lum:
                lum = mv[q + 1]
            if mv[q + 2] > lum:
                lum = mv[q + 2]
            text_alpha[p] = lum

        # 3) 合成：胶囊底（圆角 + 1px 亮边）在下，文字在上
        #    全部按 0..1 归一化算「over」，最后再乘回 255 —— 早先这里把归一化分子
        #    除以了字节尺度的分母，白字被压成黑色（t=1 时算出 1 而不是 255）。
        br, bg_, bb = HINT_BG[0], HINT_BG[1], HINT_BG[2]
        c0, c1, c2 = bb / 255.0, bg_ / 255.0, br / 255.0     # 归一化底色（BGR）
        ba01 = HINT_BG_ALPHA / 255.0
        radius = h / 2.0
        cx, cy = w / 2.0, h / 2.0
        half_w = cx - radius
        half_h = cy - radius
        rim_w = 1.2
        for py in range(h):
            dy = abs(py + 0.5 - cy) - half_h
            row_base = py * w
            for px in range(w):
                dx = abs(px + 0.5 - cx) - half_w
                mx, my = (dx if dx > 0 else 0.0), (dy if dy > 0 else 0.0)
                dist = math.sqrt(mx * mx + my * my) + min(max(dx, dy), 0.0) - radius
                cov = 0.5 - dist
                if cov <= 0.0:
                    continue                        # 胶囊外：保持全透明
                if cov > 1.0:
                    cov = 1.0
                a_bg = ba01 * cov
                # 贴边 1.2px 的亮边：把底色朝浅灰提亮，让胶囊在浅色壁纸上也有轮廓
                rim = 0.0
                if dist > -rim_w:
                    rim = (1.0 + dist / rim_w) * HINT_RIM
                cb, cg, cr = c0, c1, c2
                if rim > 0.0:
                    cb += (0.72 - cb) * rim
                    cg += (0.74 - cg) * rim
                    cr += (0.80 - cr) * rim
                t = text_alpha[row_base + px] / 255.0
                o = (row_base + px) * 4
                if t <= 0.0:
                    mv[o] = int(cb * 255.0)
                    mv[o + 1] = int(cg * 255.0)
                    mv[o + 2] = int(cr * 255.0)
                    mv[o + 3] = int(a_bg * 255.0)
                    continue
                a_out = t + a_bg * (1.0 - t)
                if a_out <= 0.0:
                    mv[o] = mv[o + 1] = mv[o + 2] = mv[o + 3] = 0
                    continue
                k = a_bg * (1.0 - t) / a_out
                s = t / a_out
                mv[o] = int(min(255.0, (cb * k + s) * 255.0))
                mv[o + 1] = int(min(255.0, (cg * k + s) * 255.0))
                mv[o + 2] = int(min(255.0, (cr * k + s) * 255.0))
                mv[o + 3] = int(a_out * 255.0)

        # 4) 转圈区域：位置、清场行、圆点合成表
        self._ring_cx = w - AURA_HINT_PAD_X - box / 2.0
        self._ring_cy = h / 2.0
        x0 = int(self._ring_cx - box / 2.0) - 1
        y0 = int(self._ring_cy - box / 2.0) - 1
        x1 = x0 + box + 2
        y1 = y0 + box + 2
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(w, x1), min(h, y1)
        self._box = (x0, y0, x1, y1)
        self._bg_row = bytes((bb, bg_, br, HINT_BG_ALPHA)) * (x1 - x0)
        self._dot_lut = _compose_lut(bb, bg_, br, HINT_BG_ALPHA)
        self._angle0 = time.time()
        win.push(0)
        return True

    # ---- 每帧：重绘转圈 ----
    def draw_dots(self, now: float) -> bool:
        win = self._win
        if win is None or win._mv is None:
            return False
        mv = win._mv
        w = self._w
        x0, y0, x1, y1 = self._box
        row_bytes = (x1 - x0) * 4
        row = self._bg_row[:row_bytes]
        for py in range(y0, y1):                  # 清场：整块抹回胶囊底色
            start = (py * w + x0) * 4
            mv[start:start + row_bytes] = row
        ang = (now - self._angle0) / AURA_HINT_SPIN_S * 2.0 * math.pi
        lut = self._dot_lut
        R = AURA_HINT_ORBIT_R
        r = AURA_HINT_DOT_R
        span = int(r + 1.5)
        n = AURA_HINT_DOTS
        for k in range(n):
            th = ang + 2.0 * math.pi * k / n
            # 头部最亮，尾巴渐隐：8 个点大小样式完全一致，只差透明度（透明度下限
            # 留到 0.6，保证任何时刻八个点都看得清，只是亮度层次不同）
            ratio = 1.0 - 0.40 * (k / float(n - 1))
            cx = self._ring_cx + R * math.cos(th)
            cy = self._ring_cy + R * math.sin(th)
            ix, iy = int(cx), int(cy)
            for py in range(iy - span, iy + span + 1):
                if py < y0 or py >= y1:
                    continue
                rowbase = py * w
                ddy = py + 0.5 - cy
                for px in range(ix - span, ix + span + 1):
                    if px < x0 or px >= x1:
                        continue
                    ddx = px + 0.5 - cx
                    cov = r + 0.5 - math.sqrt(ddx * ddx + ddy * ddy)
                    if cov <= 0.0:
                        continue
                    if cov > 1.0:
                        cov = 1.0
                    o = (rowbase + px) * 4
                    b, g, rr, a = lut[int(cov * ratio * 255.0)]
                    mv[o] = b
                    mv[o + 1] = g
                    mv[o + 2] = rr
                    mv[o + 3] = a
        return True

    def push(self, sca: int):
        if self._win:
            self._win.push(sca)

    def raise_top(self):
        if self._win:
            self._win.raise_top()

    def destroy(self):
        if self._win:
            self._win.destroy()
            self._win = None


def _create_font(px: int) -> int:
    return gdi32.CreateFontW(-int(px), 0, 0, 0, FW_MEDIUM, 0, 0, 0,
                             DEFAULT_CHARSET, OUT_TT_PRECIS, CLIP_DEFAULT_PRECIS,
                             ANTIALIASED_QUALITY, DEFAULT_PITCH | FF_DONTCARE,
                             "Microsoft YaHei UI")


def _compose_lut(b, g, r, a_bg: int) -> list:
    """白点以 alpha=i 盖在固定底色 (b,g,r,a_bg) 上 → 256 个预合成 BGRA。

    转圈区域每帧要写几百个像素，把合成算术挪到建窗时算一次，动画帧只查表。
    合成按 0..1 归一化算「over」（归一化分子除以归一化分母），最后乘回 255。
    """
    out = []
    ab = a_bg / 255.0
    c0, c1, c2 = b / 255.0, g / 255.0, r / 255.0
    for i in range(256):
        t = i / 255.0
        a_out = t + ab * (1.0 - t)
        if a_out <= 0.0:
            out.append((0, 0, 0, 0))
            continue
        k = ab * (1.0 - t) / a_out
        s = t / a_out
        out.append((int(min(255.0, (c0 * k + s) * 255.0)),
                    int(min(255.0, (c1 * k + s) * 255.0)),
                    int(min(255.0, (c2 * k + s) * 255.0)),
                    int(a_out * 255.0)))
    return out


# ============================================================================
# 模块级单例：状态由 server.py 驱动（操控动作开始 → 点亮，接管/结束 → 淡出）
# ============================================================================

_aura = Aura()


def start_aura():
    return _aura.start()


def stop_aura():
    _aura.stop()


def set_aura_active(flag: bool):
    _aura.set_active(flag)


def aura_state() -> dict:
    return {"active": _aura.active, "failed": _aura._failed,
            "error": _aura._error,
            "capability": _aura.capability()}


def aura_status_line() -> str:
    """一行话说明提示层当前状态（已点亮 / 未点亮 / 不可用+原因）。

    为什么要单独给一行：提示层不出现时，用户和模型都**看不到任何线索**
    （异常都被吞了），只能靠猜。这行字会出现在 screen_info 与 control(status)
    的输出里，一次调用就能定位。
    """
    if _aura._failed:
        return f"提示层不可用（{_aura._error or '未知原因'}）"
    if _aura.active:
        return "提示层已点亮（屏幕光带 + 顶部胶囊窗）"
    return "提示层当前未点亮（AI 未在操控桌面）"


def aura_capability() -> str:
    return _aura.capability()
