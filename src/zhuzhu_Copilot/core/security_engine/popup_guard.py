"""广告弹窗拦截：WinEventHook 监控窗口创建，按标题/进程特征匹配自动关闭。

- 后台线程设置 WinEventHook(EVENT_OBJECT_CREATE..SHOW) + 消息循环
- 回调内轻量判定：可见窗口 + 标题含广告关键词 或 进程名含广告进程特征 → 关闭(WM_CLOSE)
- 特征库（关键词/进程名/动作）全部来自配置，可扩展
- 拦截记录进线程安全队列，编排器周期读取上报

默认动作「close」保守关闭广告窗口，避免误关正常程序（仅命中明确广告特征才处理）。
"""
import ctypes
import ctypes.wintypes as wintypes
import os
import threading
from typing import Callable, List, Optional

logger = __import__("zhuzhu_Copilot.utils.helpers", fromlist=["setup_logging"]).setup_logging()

from zhuzhu_Copilot.core.security_engine.config import config

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

EVENT_OBJECT_CREATE = 0x8000
EVENT_OBJECT_SHOW = 0x8002
WINEVENT_OUTOFCONTEXT = 0x0000
WINEVENT_SKIPOWNPROCESS = 0x0002
OBJID_WINDOW = 0
WM_CLOSE = 0x0010
WM_QUIT = 0x0012
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

WINEVENTPROC = ctypes.WINFUNCTYPE(
    None, ctypes.c_void_p, wintypes.DWORD, wintypes.HWND,
    ctypes.c_long, ctypes.c_long, wintypes.DWORD, wintypes.DWORD)


class PopupGuard:
    """广告弹窗拦截器（独立消息循环线程）"""

    def __init__(self):
        self._hook = None
        self._proc = None
        self._thread = None
        self._running = False
        self._lock = threading.Lock()
        self._records: List[dict] = []

    # ---------- 生命周期 ----------
    def start(self) -> bool:
        if self._running:
            return False
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._running = False
        tid = self._thread.ident if self._thread else None
        if tid:
            user32.PostThreadMessageW(tid, WM_QUIT, 0, 0)

    def _run(self) -> None:
        self._proc = WINEVENTPROC(self._on_event)  # 保持引用防 GC
        hmod = kernel32.GetModuleHandleW(None)
        self._hook = user32.SetWinEventHook(
            EVENT_OBJECT_CREATE, EVENT_OBJECT_SHOW, hmod, self._proc, 0, 0,
            WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS)
        if not self._hook:
            self._running = False
            return
        msg = wintypes.MSG()
        while self._running and user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        user32.UnhookWinEvent(self._hook)
        self._hook = None

    # ---------- 事件判定 ----------
    def _on_event(self, hook, event, hwnd, idObject, idChild, tid, time_ms):
        try:
            if idObject != OBJID_WINDOW or not user32.IsWindowVisible(hwnd):
                return
            title = self._window_text(hwnd)
            pname = self._process_name(hwnd)
            if self._is_ad(title, pname):
                if str(config.get("popup.action", "close")) == "close":
                    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
                self._record(title, pname)
        except Exception:
            pass

    def _is_ad(self, title: str, pname: str) -> bool:
        keywords = [str(k) for k in config.list_of("popup.title_keywords", [])]
        procs = [str(p).lower() for p in config.list_of("popup.ad_processes", [])]
        if title and any(k in title for k in keywords):
            return True
        base = os.path.basename(pname).lower().replace(".exe", "")
        return bool(base and any(p in base for p in procs))

    def _window_text(self, hwnd) -> str:
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowTextW.restype = ctypes.c_int
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        return buf.value

    def _process_name(self, hwnd) -> str:
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return ""
        h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not h:
            return ""
        try:
            kernel32.QueryFullProcessImageNameW.argtypes = [
                wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
            kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(1024)
            if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return buf.value
            return ""
        finally:
            kernel32.CloseHandle(h)

    def _record(self, title: str, pname: str) -> None:
        with self._lock:
            self._records.append({"title": title, "process": pname})
            self._records = self._records[-50:]  # 仅保留最近 50 条

    # ---------- 结果读取 ----------
    def drain(self) -> List[dict]:
        """取出并清空最近拦截记录（供编排器周期上报）"""
        with self._lock:
            out, self._records = self._records, []
        return out


popup_guard = PopupGuard()
