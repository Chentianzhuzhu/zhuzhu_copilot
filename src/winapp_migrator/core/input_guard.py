"""用户输入监控：全局低级钩子（WH_MOUSE_LL / WH_KEYBOARD_LL）检测用户手动鼠标/键盘操作。

用途：AI 通过鼠标/键盘工具操控电脑时，若用户手动移动鼠标/按键/点击，立即回调通知
（供上层停止 AI 操控）。AI 自身的模拟输入（SendInput / SetCursorPos / mouse_event）
也会触发低级钩子，因此模拟输入期间须通过 ai_suppress() 抑制，避免把 AI 自己的输入
误判为用户操作。

低级钩子必须运行在带消息循环的线程上，故本模块持有独立守护线程（daemon）。
"""

import ctypes
import threading
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14

# 视为"用户手动操作"的鼠标/键盘事件
WM_MOUSEMOVE = 0x0200
WM_MOUSEWHEEL = 0x020A
WM_LBUTTONDOWN = 0x0201
WM_RBUTTONDOWN = 0x0204
WM_MBUTTONDOWN = 0x0207
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
WM_KEYUP = 0x0101
WM_SYSKEYUP = 0x0105

_AI_EVENTS = {WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_RBUTTONDOWN, WM_MBUTTONDOWN,
              WM_MOUSEWHEEL, WM_KEYDOWN, WM_SYSKEYDOWN, WM_KEYUP, WM_SYSKEYUP}

# 低级钩子相关的句柄/消息参数在 64 位下均为指针宽度。未声明 argtypes/restype 时
# ctypes 缺省按 32 位 c_int 转换：lParam（指向 MSLLHOOKSTRUCT 的指针）等大整数会被
# 截断/溢出，抛出 "OverflowError: int too long to convert" 导致设置工作目录等
# 弹窗操作期间钩子回调频繁崩溃。这里显式声明全部函数原型（指针按原生宽度）。
HHOOK = wintypes.HHOOK
WPARAM = wintypes.WPARAM
LPARAM = wintypes.LPARAM
LRESULT = ctypes.c_ssize_t

HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, WPARAM, LPARAM)

user32.SetWindowsHookExW.restype = HHOOK
user32.SetWindowsHookExW.argtypes = (ctypes.c_int, HOOKPROC,
                                     wintypes.HMODULE, wintypes.DWORD)
user32.CallNextHookEx.restype = LRESULT
user32.CallNextHookEx.argtypes = (HHOOK, ctypes.c_int, WPARAM, LPARAM)
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.UnhookWindowsHookEx.argtypes = (HHOOK,)
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
user32.GetMessageW.restype = ctypes.c_int
user32.GetMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                               wintypes.UINT, wintypes.UINT)
user32.TranslateMessage.restype = wintypes.BOOL
user32.TranslateMessage.argtypes = (ctypes.POINTER(wintypes.MSG),)
user32.DispatchMessageW.restype = ctypes.c_ssize_t   # LRESULT
user32.DispatchMessageW.argtypes = (ctypes.POINTER(wintypes.MSG),)
user32.PostThreadMessageW.restype = wintypes.BOOL
user32.PostThreadMessageW.argtypes = (wintypes.DWORD, wintypes.UINT, WPARAM, LPARAM)


class UserInputGuard:
    """全局低级钩子：检测用户手动操作并触发一次性回调"""

    def __init__(self):
        self._mouse_hook = None
        self._key_hook = None
        self._thread = None
        self._callback = None
        self._ai_depth = 0          # AI 模拟输入深度（引用计数，支持嵌套抑制）
        self._lock = threading.Lock()
        self._mouse_cb = HOOKPROC(self._mouse_proc)
        self._key_cb = HOOKPROC(self._key_proc)

    # ---- AI 模拟输入抑制 ----
    def is_ai_inputting(self) -> bool:
        with self._lock:
            return self._ai_depth > 0

    def suppress(self):
        with self._lock:
            self._ai_depth += 1

    def unsuppress(self):
        with self._lock:
            if self._ai_depth > 0:
                self._ai_depth -= 1

    # ---- 钩子回调（钩子线程执行，需轻量） ----
    def _trigger_stop(self):
        # 每次检测到用户操作都通知（上层自行判断是否处于 AI 操控中），不永久清除
        cb = self._callback
        if cb:
            try:
                cb()
            except Exception:
                pass

    def _mouse_proc(self, nCode, wParam, lParam):
        try:
            if nCode >= 0 and wParam in _AI_EVENTS and not self.is_ai_inputting():
                self._trigger_stop()
        except Exception:
            pass
        # 钩子回调绝不能抛异常（Windows 在系统上下文调用）：
        # 出错时返回 0 放行事件，保证桌面输入不被吞掉、线程不被异常击穿。
        try:
            return user32.CallNextHookEx(self._mouse_hook, nCode, wParam, lParam)
        except Exception:
            return 0

    def _key_proc(self, nCode, wParam, lParam):
        try:
            if nCode >= 0 and wParam in _AI_EVENTS and not self.is_ai_inputting():
                self._trigger_stop()
        except Exception:
            pass
        try:
            return user32.CallNextHookEx(self._key_hook, nCode, wParam, lParam)
        except Exception:
            return 0

    # ---- 生命周期 ----
    def start(self, callback):
        """启动监控：callback 在检测到用户手动操作时被调用（在钩子线程）。"""
        self._callback = callback
        if self._thread and self._thread.is_alive():
            return False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return True

    def _run(self):
        hmod = kernel32.GetModuleHandleW(None)
        self._mouse_hook = user32.SetWindowsHookExW(
            WH_MOUSE_LL, self._mouse_cb, hmod, 0)
        self._key_hook = user32.SetWindowsHookExW(
            WH_KEYBOARD_LL, self._key_cb, hmod, 0)
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        self._unhook()

    def _unhook(self):
        if self._mouse_hook:
            user32.UnhookWindowsHookEx(self._mouse_hook)
            self._mouse_hook = None
        if self._key_hook:
            user32.UnhookWindowsHookEx(self._key_hook)
            self._key_hook = None

    def stop(self):
        """停止监控：向消息循环投递 WM_QUIT 使其退出并卸载钩子。"""
        tid = self._thread.ident if self._thread else None
        if tid:
            user32.PostThreadMessageW(tid, 0x0012, 0, 0)   # WM_QUIT
        self._callback = None


# 模块级单例
guard = UserInputGuard()


class _SuppressCtx:
    """AI 模拟输入期间的抑制上下文：with ai_suppress(): <模拟输入>"""
    def __enter__(self):
        guard.suppress()
        return self

    def __exit__(self, *exc):
        guard.unsuppress()
        return False


def ai_suppress():
    """返回抑制上下文管理器，包裹 AI 的模拟鼠标/键盘输入，避免自触发停止。"""
    return _SuppressCtx()