"""屏幕边缘彩色光环（AI 操控提示），零第三方依赖。

实现要点
--------
1. **分层窗口 + 鼠标穿透**：WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW |
   WS_EX_NOACTIVATE。穿透保证光环绝不吞掉用户的点击，也不会抢焦点。
2. **UpdateLayeredWindow**：逐像素 alpha 合成，真正做到边缘渐隐、中心透明，
   不需要在屏幕上盖一层不透明底。
3. **性能优先（开发原则：程序性能 > UI/UX）**：
   - 帧率上限 AURA_FPS（默认 20），远低于动画常见 60 —— 光环只是提示，无需高帧；
   - 变化节流：颜色/相位没到该更新的帧就跳过整帧重绘；
   - 位图与 DC 全局复用，不在动画循环里分配内存；
   - 状态变化（开始/停止）才重建位图，动画帧只改像素。
4. **可降级**：非 Windows / 分层窗口不可用时静默关闭，操控功能不受影响。

关于配色：本文件是**屏幕叠加提示层**，按需求使用彩色流光；与应用内 UI 的
纯黑+淡灰+白+深蓝风格是两套东西，不互相约束。
"""

import ctypes
import threading
import time
from ctypes import wintypes, POINTER, byref, c_void_p

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# ---- 可调参数（集中在此，便于扩展/调优） ----
AURA_FPS = 12                 # 帧率上限：提示用途，12fps 足够且留足 CPU 余量
                                # （实测 1920×1080 单帧约 50ms，20fps 会顶满预算；
                                #  12fps → 83ms 预算，留 40% 余量）
AURA_THICKNESS = 5            # 光环粗细（屏幕像素）
AURA_MARGIN = 0               # 贴边偏移
AURA_SATURATION = 0.85        # 色彩饱和度（HSL 的 S）
AURA_MIN_LIGHT = 0.55         # 最低亮度：保证在浅色壁纸上也能看见
AURA_FADE_S = 0.35            # 启动/停止淡入淡出时长（秒）

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
ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
BI_RGB = 0

HWND_TOPMOST = -1


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
    user32.DestroyWindow.argtypes = (wintypes.HWND,)
    user32.IsWindow.argtypes = (wintypes.HWND,)
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleDC.argtypes = (wintypes.HDC,)
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    gdi32.CreateDIBSection.argtypes = [wintypes.HDC, POINTER(BITMAPINFO), wintypes.UINT,
                                       POINTER(c_void_p), wintypes.HANDLE, wintypes.DWORD]
    gdi32.SelectObject.restype = ctypes.c_void_p
    gdi32.SelectObject.argtypes = (wintypes.HDC, ctypes.c_void_p)
    gdi32.DeleteObject.argtypes = (ctypes.c_void_p,)
    gdi32.DeleteDC.argtypes = (wintypes.HDC,)


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


class Aura:
    """屏幕边缘彩色光环：AI 操控期间常亮流动，停止后淡出。

    用法：Aura().start() / .stop() / .set_active(bool)；
    单例由 module 级 active()/deactivate() 暴露给 server.py。
    """

    def __init__(self, thickness: int = AURA_THICKNESS, fps: int = AURA_FPS):
        self.thickness = max(2, int(thickness))
        self.interval = 1.0 / max(1, int(fps))
        self._lock = threading.Lock()
        self._thread = None
        self._stop = threading.Event()
        self._active = False
        self._hwnd = None
        self._screen_dc = None
        self._mem_dc = None
        self._bmp = None
        self._bits = c_void_p()      # CreateDIBSection 的输出：指向像素缓冲的指针
        self._width = 0
        self._height = 0
        self._pixels = 0          # 预先算好的 (x, y, depth_ratio) 列表
        self._failed = False
        self._last_frame = 0.0
        self._error = ""
        self._start_ts = 0.0          # 淡入起点
        self._stop_ts = 0.0           # 淡出起点
        self._lut = []                # 色相×深度 → BGRA
        self._fade_lut = []           # 叠加淡入淡出档位后的表
        self._fade_i = -1
        self._frame_buf = bytearray() # 复用帧缓冲
        self._blank_row = b""
        self._blank_size = 0
        self._addr = 0
        self._buf = None

    # ---- 对外接口 ----
    def start(self):
        """启动光环线程（幂等）。"""
        with self._lock:
            if self._failed or (self._thread and self._thread.is_alive()):
                return False
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
            return True

    def stop(self, timeout: float = 1.5):
        """请求停止并等线程收尾（幂等）。"""
        self._stop.set()
        th = self._thread
        if th and th.is_alive():
            th.join(timeout)
        with self._lock:
            self._thread = None

    def set_active(self, flag: bool):
        """设置可见性：True=操控中显示彩色光环，False=淡出并隐藏。"""
        flag = bool(flag)
        with self._lock:
            if self._active == flag and not (flag and not self._thread):
                return
            self._active = flag
        if flag:
            self.start()

    @property
    def active(self) -> bool:
        return self._active

    def capability(self) -> str:
        if self._failed:
            return f"屏幕光环不可用（{self._error}）"
        return f"屏幕四边彩色光环（{self.thickness}px / {int(1 / self.interval)}fps / 鼠标穿透）"

    # ---- 内部实现 ----
    def _run(self):
        try:
            if not self._create_window():
                return
            self._last_frame = time.time()
            while not self._stop.is_set():
                now = time.time()
                if now - self._last_frame >= self.interval:
                    self._last_frame = now
                    self._draw_frame(now)
                time.sleep(0.008)     # 轮询间隔短于帧间隔，保证不掉帧又不空转
            self._teardown()
        except Exception as e:
            self._failed = True
            self._error = f"{type(e).__name__}: {e}"

    def _create_window(self) -> bool:
        width, height = self._screen_size()
        if width <= 0 or height <= 0:
            self._failed = True
            self._error = "无法获取屏幕尺寸"
            return False
        self._width, self._height = width, height
        self._screen_dc = user32.GetDC(0)
        if not self._screen_dc:
            self._failed = True
            self._error = "GetDC 失败"
            return False
        self._hwnd = user32.CreateWindowExW(
            WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
            "STATIC", "ai-control-aura", WS_POPUP,
            -AURA_MARGIN, -AURA_MARGIN, width + AURA_MARGIN * 2, height + AURA_MARGIN * 2,
            0, 0, kernel32.GetModuleHandleW(None), None)
        if not self._hwnd:
            self._failed = True
            self._error = f"CreateWindowExW 失败 err={ctypes.get_last_error()}"
            return False
        user32.SetWindowPos(self._hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW)
        # 逐像素图层：每像素 alpha，边缘不生硬
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = width
        bmi.bmiHeader.biHeight = -height          # 自顶向下
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = BI_RGB
        self._mem_dc = gdi32.CreateCompatibleDC(self._screen_dc)
        if not self._mem_dc:
            self._failed = True
            self._error = "CreateCompatibleDC 失败"
            return False
        self._bmp = gdi32.CreateDIBSection(self._screen_dc, ctypes.byref(bmi), 0,
                                           ctypes.byref(self._bits), None, 0)
        if not self._bmp:
            self._failed = True
            self._error = "CreateDIBSection 失败"
            return False
        gdi32.SelectObject(self._mem_dc, self._bmp)
        self._blank_size = width * height * 4
        self._blank_row = bytes(4096)         # 清屏用零块
        self._frame_buf = bytearray(self._blank_size)   # 复用帧缓冲，避免每帧分配
        # 像素缓冲的地址 → 可整体 memmove 的地址
        self._addr = ctypes.cast(self._bits, ctypes.c_void_p).value or 0
        self._buf = ctypes.cast(ctypes.c_void_p(self._addr),
                                ctypes.POINTER(ctypes.c_ubyte)) if self._addr else None
        self._build_lut()
        self._pixels = self._build_pixels()
        # 建窗时清一次：屏幕内部区域此后恒为全透明，动画帧无需再清屏
        ctypes.memmove(self._addr, bytes(self._blank_size), self._blank_size)
        return True

    def _build_pixels(self) -> list:
        """预先算好属于光环的像素 (offset, hue_pos, depth)。

        覆盖完整四边：上下横边 + 左右竖边，转角去重（早先只画横边、漏掉左右竖边，
        会导致屏幕两侧没有光环）。只遍历贴边区域而非整屏，动画帧仅写这些点。
        """
        t = self.thickness
        w, h = self._width, self._height
        seen = set()
        out = []

        def _add(x, y, depth, along):
            off = y * w + x
            if off in seen:
                return
            seen.add(off)
            out.append((off, along, depth))

        # 上下横边（含转角）
        for y in list(range(t)) + list(range(h - t, h)):
            depth = 1.0 - min(y, h - 1 - y) / float(t)
            for x in range(w):
                _add(x, y, depth, x / float(w))
        # 左右竖边（跳过已被横边覆盖的转角行）
        for x in list(range(t)) + list(range(w - t, w)):
            depth = 1.0 - min(x, w - 1 - x) / float(t)
            for y in range(t, h - t):
                _add(x, y, depth, y / float(h))
        return out

    def _build_lut(self):
        """把色相/饱和度/亮度离散成查找表：动画帧不再逐像素做 HSL→RGB 三角函数。"""
        steps = 256
        self._lut = []
        for i in range(steps):
            hue = i / float(steps)
            row = []
            for j in range(steps):
                depth = j / float(steps - 1)
                light = min(0.92, AURA_MIN_LIGHT + depth * (1.0 - AURA_MIN_LIGHT))
                r, g, b = _hsl_to_rgb(hue, AURA_SATURATION, light)
                row.append((b, g, r, int(255 * (0.35 + 0.65 * depth))))
            self._lut.append(row)
        return self._lut

    def _draw_frame(self, now: float):
        if not self._active and not self._stop_ts:
            self._stop_ts = now
        # 淡入/淡出进度：active 时从 _start_ts 渐入，否则从 _stop_ts 渐出
        if self._active:
            if not self._start_ts:
                self._start_ts = now
            self._stop_ts = 0.0
            alpha_scale = min(1.0, (now - self._start_ts) / AURA_FADE_S)
        else:
            if not self._stop_ts:
                self._stop_ts = now
            self._start_ts = 0.0
            alpha_scale = max(0.0, 1.0 - (now - self._stop_ts) / AURA_FADE_S)

        w, h = self._width, self._height
        if not self._pixels or not self._bits:
            return
        if not self._active and alpha_scale <= 0.01:
            self._push_frame(clear_only=True)
            self._stop.set()      # 淡出完成 → 线程自然退出并清理
            return

        if self._buf is None or not self._frame_buf:
            return
        # 淡入淡出只量化成 16 档 → 相位/alpha 组合数固定，可用预建表
        fade_i = int(max(0.0, min(1.0, alpha_scale)) * 15)
        if fade_i != self._fade_i:
            self._fade_i = fade_i
            self._fade_lut = [[(b, g, r, min(255, a * (fade_i + 1) // 16))
                               for (b, g, r, a) in row] for row in self._lut]
        lut = self._fade_lut
        n = len(self._lut)
        shift = (now * 0.35) % 1.0        # 色彩沿边流动
        # 热路径：复用一块 bytearray，用切片赋值写 4 字节像素（比 ctypes 逐索引快一个
        # 数量级）。无需每帧清屏 —— 四边像素每帧都会被全量覆写，屏幕内部区域从创建后
        # 就一直是全透明（alpha=0），因此只在建窗时清一次即可。
        buf = self._frame_buf
        idx_n = n - 1
        for offset, pos, depth in self._pixels:
            b, g, r, a = lut[int((pos + shift) * n) % n][int(depth * idx_n)]
            o = offset * 4
            buf[o] = b
            buf[o + 1] = g
            buf[o + 2] = r
            buf[o + 3] = a
        ctypes.memmove(self._addr, bytes(buf), self._blank_size)
        self._push_frame()

    def _push_frame(self, clear_only: bool = False):
        """把内存位图推到屏幕（alpha 叠加，不盖底）。"""
        if not self._hwnd or not self._mem_dc:
            return
        blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, 1)   # AlphaFormat=1(AC_SRC_ALPHA)
        sz = wintypes.SIZE(self._width, self._height)
        dst = wintypes.POINT(0, 0)      # 窗口已在 (0,0)，不移动
        src = wintypes.POINT(0, 0)
        try:
            user32.UpdateLayeredWindow(self._hwnd, self._screen_dc, ctypes.byref(dst),
                                       ctypes.byref(sz), self._mem_dc, ctypes.byref(src),
                                       0, ctypes.byref(blend), ULW_ALPHA)
        except Exception:
            pass

    def _teardown(self):
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
        self._hwnd = self._bmp = self._mem_dc = self._screen_dc = self._bits = None

    @staticmethod
    def _screen_size() -> tuple:
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if user32.GetMonitorInfoW(0, ctypes.byref(mi)):
            r = mi.rcMonitor
            return (int(r.right - r.left), int(r.bottom - r.top))
        return (int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1)))


# 光环状态：操控开始/结束由 server.py 驱动
_aura = Aura()
_aura._error = ""


def start_aura():
    return _aura.start()


def stop_aura():
    _aura.stop()


def set_aura_active(flag: bool):
    _aura.set_active(flag)


def aura_state() -> dict:
    return {"active": _aura.active, "failed": _aura._failed,
            "capability": _aura.capability()}


def aura_capability() -> str:
    return _aura.capability()
