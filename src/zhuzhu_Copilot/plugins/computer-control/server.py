"""电脑操控插件（MCP stdio 工具 + 本地 HTTP 可视化）: computer-control

三件套（技能 + MCP 工具 + 本地 HTTP 浏览器界面）
------------------------------------------------
- MCP（stdio）：向 AI 暴露点击/长按/拖拽/滑动/选择/输入/按键/复制/粘贴/截屏/窗口/
  虚拟桌面等工具，全部真实 Win32 调用，零第三方依赖（ctypes + 标准库）。
- HTTP（127.0.0.1 随机端口）：给用户看的可视化界面 —— 实时截图（默认原始画面，可勾选网格）、
  操作日志、接管/暂停/恢复按钮；与 MCP 工具共用同一份模块级 _STATE。
- 感知：GDI 截屏（StretchBlt 缩放 + 手写 PNG），**默认输出纯净原图**（不叠加坐标网格/
  刻度/准星），以 MCP image 内容项直接发给视觉模型看图；坐标基准与 AI 所见图完全一致
  （图像像素 → 屏幕物理像素由插件换算），杜绝缩放换算误差。网格/刻度/准星仅在
  显式传 grid=true / cursor=true 时才叠加，供人肉读数或调试用。
- 输入：触控优先（InjectTouchInput，真实 Win10/11 触控语义：tap/长按/惯性滑动），
  无触控设备自动回退 SendInput 鼠标键盘；所有注入事件带签名，供打断检测区分
  「AI 注入」与「真人输入」。
- 打断：全局低级钩子（WH_MOUSE_LL / WH_KEYBOARD_LL）监听真人鼠标/键盘输入，命中即置
  interrupted，后续操控工具一律拒绝执行并把控制权交还用户（Web UI 可一键接管/暂停/恢复）。

进程约束：本进程以 MCP stdio 方式被拉起，**stdout 只能输出 JSON-RPC** —— 日志一律不写 stdout。
"""

import base64
import ctypes
import difflib
import json
import math
import os
import re
import sys
import threading
import time
import urllib.parse
import zlib
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# 同目录模块（aura.py）：server.py 可能被以任意 cwd 拉起，先把自身目录加进 path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import aura
except Exception:      # 缺模块不应让整个插件不可用：光环只是提示，不影响操控
    aura = None

APP_NAME = "computer-control"
MCP_VERSION = "2024-11-05"

# 会真正改变桌面状态、需要在屏幕上点亮彩色光环的工具（查询类不亮）
_CONTROL_TOOLS = frozenset({
    "click", "long_press", "drag", "swipe", "select", "scroll", "type_text",
    "key_press", "copy", "paste", "open_virtual_desktop",
    "manage_window", "click_window", "verify_point", "open_app", "taskbar_open",
})

# ---- 可调参数（集中在此，便于扩展/调优） ----
MODEL_MAX_W = 1280          # 返回给视觉模型的截图最大宽度（与主程序视觉基准一致）
PREVIEW_MAX_W = 960         # Web UI 预览截图最大宽度（性能优先：预览不需要全分辨率）
PREVIEW_MIN_INTERVAL = 0.25  # Web 预览两次抓图的最小间隔（秒）：页面高频轮询时复用缓存
GRID_CELLS = 16             # 按需叠加的网格数量（N×N），刻度数字即图像像素值；默认不叠加（AI 看图不需要坐标轴）
SESSION_TTL_S = 300.0       # 操控会话滑动有效期：期内真人输入才算「接管」
INJECT_SIGNATURE = 0x5A5A5A5A       # SendInput dwExtraInfo 签名：标记 AI 自身注入
INJECT_GRACE_S = 0.35       # 注入后抑制窗口：钩子在此窗口内不把事件判为真人输入
LOG_MAX = 200               # 操作日志上限（环形）
DEFAULT_LONG_PRESS_S = 0.8
MOVE_STEP_S = 0.012         # 真人式移动的单步间隔
ZOOM_MAX_PX = 2400          # 放大图最大边长（防超大图撑爆视觉输入）
DRAG_SETTLE_S = 0.12        # 拖拽落点后等待时间：让目标控件完成响应再截图验证
DRAG_MIN_STEPS = 12         # 拖拽最少分步数：步数太少会被系统判定为瞬移（非拖拽）
LONG_PRESS_MAX_S = 5.0      # 长按时长上限（秒），防止模型传入过大值卡住流程
SETTLE_AFTER_CLICK_S = 0.06  # 点击后最小稳定时间：先等界面响应再返回，减少无效重试
FUZZY_MIN_SCORE = 0.62      # 控件名近似匹配的最低相似度：低于此值宁可报「找不到」，也别点错控件
CLICK_INSET_RATIO = 0.22    # 元素落点内缩比例（仅在中心被遮挡时用于采样，避开边框/圆角）
INVOKE_SETTLE_S = 0.35      # Invoke 后等待界面反应的时长（判断 Invoke 是否真生效）
INVOKE_MIN_CHANGE_RATIO = 0.02  # 窗口指纹变化超过此比例才算 Invoke 生效（滤掉光标闪烁/动画）

# ---- Win32 绑定 ----
user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

ULONG_PTR = ctypes.c_size_t
HGDIOBJ = ctypes.c_void_p
LRESULT = ctypes.c_ssize_t

SM_CXSCREEN, SM_CYSCREEN = 0, 1
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79
SM_DIGITIZER = 94
NID_INTEGRATED_TOUCH, NID_EXTERNAL_TOUCH, NID_READY = 0x01, 0x02, 0x80

WH_MOUSE_LL, WH_KEYBOARD_LL = 14, 13
WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_RBUTTONDOWN, WM_MBUTTONDOWN = 0x0200, 0x0201, 0x0204, 0x0207
WM_MOUSEWHEEL, WM_XBUTTONDOWN = 0x020A, 0x020B
WM_KEYDOWN, WM_SYSKEYDOWN, WM_KEYUP, WM_SYSKEYUP = 0x0100, 0x0104, 0x0101, 0x0105

MOUSEEVENTF_MOVE, MOUSEEVENTF_ABSOLUTE = 0x0001, 0x8000
# MOUSEEVENTF_VIRTUALDESK：绝对坐标按**虚拟桌面**（多显示器并集）映射。
# 缺了它，0..65535 只会映射到**主显示器**，多屏环境下副屏上的点击会严重偏移
# （副屏坐标被按主屏尺寸重新换算）。本机是单屏所以看不出问题，但必须带上。
MOUSEEVENTF_VIRTUALDESK = 0x4000
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x0008, 0x0010
MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP = 0x0020, 0x0040
MOUSEEVENTF_WHEEL = 0x0800
KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x0002, 0x0004
INPUT_MOUSE, INPUT_KEYBOARD = 0, 1

PT_TOUCH = 2
TOUCH_FEEDBACK_DEFAULT = 1
TOUCH_MASK_CONTACTAREA = 0x00000001
POINTER_FLAG_INRANGE, POINTER_FLAG_INCONTACT = 0x00080000, 0x00000001
POINTER_FLAG_DOWN, POINTER_FLAG_UPDATE, POINTER_FLAG_UP = 0x00010000, 0x00020000, 0x00040000

SRCCOPY, HALFTONE = 0x00CC0020, 4
# COLORONCOLOR 在**放大**时是逐像素复制（最近邻）：1px 的边框放大 3 倍仍是 3px 实边。
# HALFTONE 会做插值，把 1px 边框糊成 3~4px 的渐变带 —— 模型照着糊图读数会差 1~2 个
# 源像素，在 Blender 这类 20px 高的按钮上足以点到隔壁。故放大默认走 COLORONCOLOR。
COLORONCOLOR = 3
PS_SOLID, TRANSPARENT, NULL_BRUSH = 0, 1, 5

GA_ROOT = 2                       # GetAncestor：取顶层窗口（非 popup 的最外层）
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
WS_EX_LAYERED = 0x00080000        # 分层窗口（桌宠/悬浮球常靠它实现圆角与阴影）


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class POINTER_INFO(ctypes.Structure):
    _fields_ = [("pointerType", wintypes.DWORD), ("pointerId", wintypes.DWORD),
                ("frameId", wintypes.DWORD), ("pointerFlags", wintypes.DWORD),
                ("sourceDevice", wintypes.HANDLE), ("hwndTarget", wintypes.HWND),
                ("ptPixelLocation", wintypes.POINT), ("ptHimetricLocation", wintypes.POINT),
                ("ptPixelLocationRaw", wintypes.POINT), ("ptHimetricLocationRaw", wintypes.POINT),
                ("dwTime", wintypes.DWORD), ("historyCount", wintypes.DWORD),
                ("InputData", ctypes.c_int32), ("dwKeyStates", wintypes.DWORD),
                ("PerformanceCount", ctypes.c_uint64), ("ButtonChangeType", ctypes.c_int32)]


class POINTER_TOUCH_INFO(ctypes.Structure):
    _fields_ = [("pointerInfo", POINTER_INFO), ("touchFlags", wintypes.DWORD),
                ("touchMask", wintypes.DWORD), ("rcContact", wintypes.RECT),
                ("rcContactRaw", wintypes.RECT), ("orientation", wintypes.DWORD),
                ("pressure", wintypes.DWORD)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


def _bind_win32():
    """声明函数原型：指针/句柄参数在 64 位下必须显式声明，否则 ctypes 按 c_int 截断。"""
    u, g, k = user32, gdi32, kernel32
    u.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
    u.SetProcessDpiAwarenessContext.argtypes = (wintypes.HANDLE,)
    u.GetDC.restype = wintypes.HDC
    u.GetDC.argtypes = (wintypes.HWND,)
    u.ReleaseDC.argtypes = (wintypes.HWND, wintypes.HDC)
    u.GetCursorPos.argtypes = (ctypes.POINTER(wintypes.POINT),)
    u.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
    u.PrintWindow.argtypes = (wintypes.HWND, wintypes.HDC, wintypes.UINT)
    u.GetForegroundWindow.restype = wintypes.HWND
    u.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
    u.WindowFromPoint.restype = wintypes.HWND
    u.WindowFromPoint.argtypes = (wintypes.POINT,)
    u.GetParent.restype = wintypes.HWND
    u.GetParent.argtypes = (wintypes.HWND,)
    u.GetAncestor.restype = wintypes.HWND
    u.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
    u.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    u.EnumChildWindows.argtypes = (wintypes.HWND, ctypes.c_void_p, wintypes.LPARAM)
    u.IsChild.restype = wintypes.BOOL
    u.IsChild.argtypes = (wintypes.HWND, wintypes.HWND)
    u.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
    u.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    u.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    u.SetWindowsHookExW.restype = wintypes.HHOOK
    u.SetWindowsHookExW.argtypes = (ctypes.c_int, ctypes.c_void_p, wintypes.HMODULE, wintypes.DWORD)
    u.CallNextHookEx.restype = LRESULT
    u.CallNextHookEx.argtypes = (wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
    u.UnhookWindowsHookEx.argtypes = (wintypes.HHOOK,)
    u.GetMessageW.restype = ctypes.c_int
    u.GetMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)
    u.TranslateMessage.argtypes = (ctypes.POINTER(wintypes.MSG),)
    u.DispatchMessageW.restype = LRESULT
    u.DispatchMessageW.argtypes = (ctypes.POINTER(wintypes.MSG),)
    u.PostThreadMessageW.argtypes = (wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    u.IsWindowVisible.argtypes = (wintypes.HWND,)
    u.IsWindow.argtypes = (wintypes.HWND,)
    u.IsIconic.argtypes = (wintypes.HWND,)
    u.IsZoomed.argtypes = (wintypes.HWND,)
    u.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
    u.BringWindowToTop.argtypes = (wintypes.HWND,)
    u.SetForegroundWindow.argtypes = (wintypes.HWND,)
    u.AttachThreadInput.argtypes = (wintypes.DWORD, wintypes.DWORD, wintypes.BOOL)
    u.SetWindowPos.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                               ctypes.c_int, ctypes.c_int, wintypes.UINT)
    # GetWindowThreadProcessId 的 argtypes 已在上方声明，这里只补 restype
    u.GetWindowThreadProcessId.restype = wintypes.DWORD
    k.GetCurrentThreadId.restype = wintypes.DWORD
    u.EnumWindows.argtypes = (ctypes.c_void_p, wintypes.LPARAM)
    u.OpenClipboard.restype = wintypes.BOOL
    u.OpenClipboard.argtypes = (wintypes.HWND,)
    u.CloseClipboard.restype = wintypes.BOOL
    u.EmptyClipboard.restype = wintypes.BOOL
    u.GetClipboardData.restype = wintypes.HANDLE
    u.GetClipboardData.argtypes = (wintypes.UINT,)
    u.SetClipboardData.restype = wintypes.HANDLE
    u.SetClipboardData.argtypes = (wintypes.UINT, wintypes.HANDLE)
    u.InitializeTouchInjection.restype = wintypes.BOOL
    u.InitializeTouchInjection.argtypes = (wintypes.UINT, wintypes.DWORD)
    u.InjectTouchInput.restype = wintypes.BOOL
    u.InjectTouchInput.argtypes = (wintypes.UINT, ctypes.POINTER(POINTER_TOUCH_INFO))
    k.GetModuleHandleW.restype = wintypes.HMODULE
    k.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
    k.OpenProcess.restype = wintypes.HANDLE
    k.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    k.QueryFullProcessImageNameW.argtypes = (
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
    k.CloseHandle.argtypes = (wintypes.HANDLE,)
    g.CreateCompatibleDC.restype = wintypes.HDC
    g.CreateCompatibleDC.argtypes = (wintypes.HDC,)
    g.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    g.CreateCompatibleBitmap.argtypes = (wintypes.HDC, ctypes.c_int, ctypes.c_int)
    g.SelectObject.restype = HGDIOBJ
    g.SelectObject.argtypes = (wintypes.HDC, HGDIOBJ)
    g.GetStockObject.restype = HGDIOBJ
    g.GetStockObject.argtypes = (ctypes.c_int,)
    g.DeleteObject.argtypes = (HGDIOBJ,)
    g.DeleteDC.argtypes = (wintypes.HDC,)
    g.SetStretchBltMode.argtypes = (wintypes.HDC, ctypes.c_int)
    g.SetBrushOrgEx.argtypes = (wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_void_p)
    g.StretchBlt.argtypes = (wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                             wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                             wintypes.DWORD)
    # BitBlt：屏幕区域原样拷贝（窗口内容抓不到时的兜底 + 点击生效校验的局部取样）。
    # 必须声明签名 —— HDC 是 64 位句柄，不声明会被按 32 位截断（实测报 OverflowError）。
    g.BitBlt.argtypes = (wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD)
    g.BitBlt.restype = wintypes.BOOL
    g.GetDIBits.argtypes = (wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                            ctypes.c_void_p, ctypes.POINTER(BITMAPINFOHEADER), wintypes.UINT)
    g.CreatePen.restype = HGDIOBJ
    g.CreatePen.argtypes = (ctypes.c_int, ctypes.c_int, wintypes.DWORD)
    g.CreateFontW.restype = HGDIOBJ
    g.CreateFontW.argtypes = (ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                              ctypes.c_int, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                              wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                              wintypes.DWORD, wintypes.LPCWSTR)
    g.SetTextColor.argtypes = (wintypes.HDC, wintypes.DWORD)
    g.SetBkMode.argtypes = (wintypes.HDC, ctypes.c_int)
    g.TextOutW.argtypes = (wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.LPCWSTR, ctypes.c_int)
    g.MoveToEx.argtypes = (wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_void_p)
    g.LineTo.argtypes = (wintypes.HDC, ctypes.c_int, ctypes.c_int)
    g.Ellipse.argtypes = (wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int)
    g.SetBkColor.argtypes = (wintypes.HDC, wintypes.DWORD)
    k.GlobalAlloc.restype = wintypes.HANDLE
    k.GlobalAlloc.argtypes = (wintypes.UINT, ctypes.c_size_t)
    k.GlobalLock.restype = ctypes.c_void_p
    k.GlobalLock.argtypes = (wintypes.HANDLE,)
    k.GlobalUnlock.argtypes = (wintypes.HANDLE,)


_bind_win32()


def _init_dpi_awareness():
    """每显示器 DPI 感知：保证屏幕尺寸/窗口矩形/注入坐标统一为物理像素，杜绝缩放偏移。"""
    for ctx in (-4, -3):        # PER_MONITOR_AWARE_V2 / PER_MONITOR_AWARE
        try:
            if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(ctx)):
                return
        except Exception:
            pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass


# ============================================================================
# 截屏：GDI 抓图 + 网格/刻度/准星叠加 + 手写 PNG
# ============================================================================

def screen_size() -> tuple:
    return int(user32.GetSystemMetrics(SM_CXSCREEN)), int(user32.GetSystemMetrics(SM_CYSCREEN))


def _virtual_screen() -> tuple:
    """虚拟屏幕（多显示器并集）原点与尺寸：注入绝对坐标必须用它归一化。"""
    return (int(user32.GetSystemMetrics(SM_XVIRTUALSCREEN)),
            int(user32.GetSystemMetrics(SM_YVIRTUALSCREEN)),
            int(user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)) or screen_size()[0],
            int(user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)) or screen_size()[1])


def _cursor_pos() -> tuple:
    pt = wintypes.POINT()
    if user32.GetCursorPos(ctypes.byref(pt)):
        return int(pt.x), int(pt.y)
    return 0, 0


def _dib_bits(dc, bmp, w: int, h: int) -> bytes:
    """位图 → BGRA 像素缓冲（自顶向下 32bpp）。"""
    bmi = BITMAPINFOHEADER()
    bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.biWidth, bmi.biHeight = w, -h
    bmi.biPlanes, bmi.biBitCount, bmi.biCompression = 1, 32, 0
    buf = ctypes.create_string_buffer(w * h * 4)
    if not gdi32.GetDIBits(dc, bmp, 0, h, buf, ctypes.byref(bmi), 0):
        raise RuntimeError("GetDIBits 失败（屏幕抓取）")
    return bytes(buf)


def _gdi_text(dc, x: int, y: int, text: str, color: int, size: int = 16):
    """GDI 叠字：先描黑边再填色，保证缩放到视觉输入后仍可读。"""
    font = gdi32.CreateFontW(-size, 0, 0, 0, 700, 0, 0, 0, 1, 0, 0, 4, 0, "Consolas")
    old = gdi32.SelectObject(dc, font)
    gdi32.SetBkMode(dc, TRANSPARENT)
    try:
        gdi32.SetTextColor(dc, 0x000000)
        gdi32.TextOutW(dc, x + 1, y + 1, text, len(text))
        gdi32.SetTextColor(dc, color)
        gdi32.TextOutW(dc, x, y, text, len(text))
    finally:
        gdi32.SelectObject(dc, old)
        gdi32.DeleteObject(font)


def _gdi_line(dc, x1, y1, x2, y2, color: int, width: int = 1):
    pen = gdi32.CreatePen(PS_SOLID, width, color)
    old = gdi32.SelectObject(dc, pen)
    try:
        gdi32.MoveToEx(dc, x1, y1, None)
        gdi32.LineTo(dc, x2, y2)
    finally:
        gdi32.SelectObject(dc, old)
        gdi32.DeleteObject(pen)


def _draw_grid(dc, w: int, h: int, cells: int):
    """坐标网格 + 像素刻度（**默认不启用**：AI 截图走纯净原图，只有显式 grid=true 或
    用户在 Web UI 勾选时才叠加）。刻度数字与图像同基准，读数可直接作为工具坐标。"""
    for i in range(1, cells):
        x, y = w * i // cells, h * i // cells
        _gdi_line(dc, x, 0, x, h, 0x505050)
        _gdi_line(dc, 0, y, w, y, 0x505050)
    _gdi_line(dc, w // 2, 0, w // 2, h, 0x808080)
    _gdi_line(dc, 0, h // 2, w, h // 2, 0x808080)
    step = max(1, cells // 2)   # 每 2 格标一个数字，避免拥挤
    for i in range(0, cells + 1, step):
        x, y = w * i // cells, h * i // cells
        _gdi_text(dc, min(x + 3, w - 60), 16, str(x), 0x0000E6FF)    # X 轴：黄（COLORREF BGR）
        _gdi_text(dc, 3, min(y + 18, h - 20), str(y), 0x00AAFF00)    # Y 轴：青


def _draw_mark(dc, sx, sy, sw, sh, tw, th, pt, color=0x005533FF, label: str = ""):
    """在源坐标点 pt 处画一个准星+圆环（源坐标系 → 图像坐标）；点不在区域内则不画。

    color 用 BGR 顺序（GDI 的 COLORREF 是 0x00BBGGRR）：默认 0x005533FF = 红。
    """
    if not pt:
        return
    px, py = pt
    if not (sx <= px <= sx + sw and sy <= py <= sy + sh):
        return
    x = int((px - sx) * tw / max(1, sw))
    y = int((py - sy) * th / max(1, sh))
    _gdi_line(dc, x - 14, y, x + 14, y, color, 2)
    _gdi_line(dc, x, y - 14, x, y + 14, color, 2)
    pen = gdi32.CreatePen(PS_SOLID, 2, color)
    old_pen = gdi32.SelectObject(dc, pen)
    old_brush = gdi32.SelectObject(dc, gdi32.GetStockObject(NULL_BRUSH))   # 空心圆环
    try:
        gdi32.Ellipse(dc, x - 15, y - 15, x + 15, y + 15)
    finally:
        gdi32.SelectObject(dc, old_brush)
        gdi32.SelectObject(dc, old_pen)
        gdi32.DeleteObject(pen)
    if label:
        _gdi_text(dc, x + 16, y - 26, label, color, 16)


def _draw_cursor(dc, sx, sy, sw, sh, tw, th, pt):
    """红色准星标记当前鼠标位置（源坐标系 → 图像坐标），鼠标不在区域内不画。"""
    _draw_mark(dc, sx, sy, sw, sh, tw, th, pt, 0x005533FF)


def _draw_box(dc, sx, sy, sw, sh, tw, th, rect, color=0x00E6D23C, label: str = ""):
    """在源坐标矩形 rect=(x0,y0,x1,y1) 处画方框 + 编号标签（源坐标 → 图像坐标）。

    用途：把「候选目标」一个个框出来并**编号**，让模型看着编号挑，而不是靠估坐标 ——
    这是 refine_point 的候选菜单（见其说明）。用 4 条 _gdi_line 而不是 Rectangle()：
    不必再绑定一个 GDI 函数，线宽也能直接控（缩放后的图上 2px 才看得清）。
    """
    if not rect:
        return
    rx0, ry0, rx1, ry1 = rect

    def ix(v):
        return int((v - sx) * tw / max(1, sw))

    def iy(v):
        return int((v - sy) * th / max(1, sh))

    x0, y0, x1, y1 = ix(rx0), iy(ry0), ix(rx1), iy(ry1)
    if x1 < 0 or y1 < 0 or x0 > tw or y0 > th:      # 完全在区域外
        return
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(tw - 1, x1), min(th - 1, y1)
    _gdi_line(dc, x0, y0, x1, y0, color, 2)
    _gdi_line(dc, x1, y0, x1, y1, color, 2)
    _gdi_line(dc, x1, y1, x0, y1, color, 2)
    _gdi_line(dc, x0, y1, x0, y0, color, 2)
    if label:
        # 字号跟放大倍数走：放大 3 倍时用 16px 的固定字号，编号在密集工具栏里会糊成一团；
        # 按比例放大后编号才看得清（上限 32，太大反而盖住小目标本身）。
        fs = max(16, min(32, int(round(tw / max(1, sw) * 12))))
        # 框够高就把编号放框外上方，否则放框内左上 —— 都不至于盖住框里的内容
        ty = y0 - fs - 4 if y0 >= fs + 8 else y0 + 2
        _gdi_text(dc, x0 + 3, ty, label, color, fs)


def _stretch_mode(enlarging: bool, smooth: bool) -> int:
    """选择 StretchBlt 重采样模式。

    * 缩小（enlarging=False）→ HALFTONE：抗锯齿最好，读数最清楚；
    * 放大且 smooth=False → COLORONCOLOR（逐像素复制/最近邻）：1px 边框放大后
      仍是硬实方块，图上像素与屏幕像素一一对应，坐标读数不会被插值糊边带偏；
    * 放大且 smooth=True → HALFTONE：画面柔和、文字更好认，但边缘会糊 1~2px。
    """
    return HALFTONE if (smooth or not enlarging) else COLORONCOLOR


def _capture_scaled(src_dc, sx, sy, sw, sh, tw, cells, cursor_pt=None,
                    smooth: bool = True, marks=None, level: int = 6,
                    boxes=None) -> tuple:
    """抓取源 DC 区域 → 缩放到 tw 宽 → （可选）叠加网格/刻度/准星/候选框 → PNG 字节。

    返回 (png, img_w, img_h)。坐标基准：图像像素 = (源坐标 - (sx,sy)) * tw/sw。
    叠加是**可选项**：cells=0 且 cursor_pt=None 时输出纯净原图（给 AI 的默认形态）。

    smooth 只影响**放大**（tw > sw）时的重采样方式：
      * True  —— HALFTONE 插值，画面柔和、文字好读（缩小时也用它，抗锯齿最好）；
      * False —— COLORONCOLOR 逐像素复制，边缘是硬实的方块（读精确坐标用这个）。
    缩小（tw < sw）一律用 HALFTONE：最近邻降采样会丢像素、产生锯齿，反而更难读数。

    marks 是额外的标注点列表 [(源x, 源y, 颜色, 文字)]，用于把「模型给的点」和
    「算法修正后的点」同时画出来对照（见 refine_point）。
    boxes 是候选框列表 [(源x0, 源y0, 源x1, 源y1, 颜色, 编号)]，用于把候选目标
    逐个框出并编号（见 refine_point 的候选菜单）。先画框、后画准星，准星不会被盖。
    """
    tw = max(1, int(tw))
    th = max(1, round(sh * tw / max(1, sw)))
    mem = gdi32.CreateCompatibleDC(src_dc)
    bmp = gdi32.CreateCompatibleBitmap(src_dc, tw, th)
    old = gdi32.SelectObject(mem, bmp)
    try:
        enlarging = tw > max(1, sw)
        gdi32.SetStretchBltMode(mem, _stretch_mode(enlarging, smooth))
        gdi32.SetBrushOrgEx(mem, 0, 0, None)   # HALFTONE 需重置刷原点，否则缩放发糊
        gdi32.StretchBlt(mem, 0, 0, tw, th, src_dc, sx, sy, sw, sh, SRCCOPY)
        if cells > 0:
            _draw_grid(mem, tw, th, cells)
        for bx in (boxes or ()):
            _draw_box(mem, sx, sy, sw, sh, tw, th, (bx[0], bx[1], bx[2], bx[3]),
                      bx[4] if len(bx) > 4 else 0x00E6D23C,
                      bx[5] if len(bx) > 5 else "")
        for m in (marks or ()):
            _draw_mark(mem, sx, sy, sw, sh, tw, th, (m[0], m[1]),
                       m[2] if len(m) > 2 else 0x005533FF,
                       m[3] if len(m) > 3 else "")
        if cursor_pt is not None:
            _draw_cursor(mem, sx, sy, sw, sh, tw, th, cursor_pt)
        return _png_encode(_dib_bits(mem, bmp, tw, th), tw, th, level), tw, th
    finally:
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)


def _png_encode(bgra: bytes, w: int, h: int, level: int = 6) -> bytes:
    """BGRA 像素 → PNG（zlib + 手写 PNG 容器，零第三方依赖）。"""
    raw = bytearray()
    stride = w * 4
    for y in range(h):
        row = bgra[y * stride:(y + 1) * stride]
        raw.append(0)                      # 过滤器类型 0（None）
        rgb = bytearray(w * 3)
        rgb[0::3] = row[2::4]              # BGRA → RGB（切片赋值走 C 层，避免逐像素 Python 循环）
        rgb[1::3] = row[1::4]
        rgb[2::3] = row[0::4]
        raw += rgb

    def _chunk(tag: bytes, data: bytes) -> bytes:
        return (len(data).to_bytes(4, "big") + tag + data
                + zlib.crc32(tag + data).to_bytes(4, "big"))

    ihdr = w.to_bytes(4, "big") + h.to_bytes(4, "big") + bytes((8, 2, 0, 0, 0))
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", zlib.compress(bytes(raw), level))
            + _chunk(b"IEND", b""))


def grab_screen(max_w: int = MODEL_MAX_W, grid: bool = False, cursor: bool = False,
                level: int = 6) -> tuple:
    """整屏抓图（**整个虚拟桌面**，多显示器并集）。返回 (png, img_w, img_h, ox, oy, w, h)。

    为什么抓虚拟桌面而不是主屏：注入坐标是按虚拟桌面归一化的（见 _move_absolute），
    而 SM_CXSCREEN 只给主屏尺寸。副屏在主屏左侧/上方时虚拟原点是负数，若截图只覆盖
    主屏、却把原点当成 (0,0)，副屏上的目标会整体算错；即使只有一块屏，返回显式原点
    也让「图像坐标 ↔ 物理坐标」的换算无歧义。

    默认 grid=False / cursor=False：给 AI 的截图是纯净原图，不带坐标轴与准星。
    """
    ox, oy, w, h = _virtual_screen()
    dc = user32.GetDC(0)
    try:
        png, iw, ih = _capture_scaled(dc, ox, oy, w, h, min(max_w, w),
                                      GRID_CELLS if grid else 0,
                                      _cursor_pos() if cursor else None,
                                      level=level)
        return png, iw, ih, ox, oy, w, h
    finally:
        user32.ReleaseDC(0, dc)


def _ensure_window_capturable(hwnd: int) -> str:
    """窗口截图前确保窗口处于「可抓取」状态：最小化就先恢复，仍不可用则**拒绝**。

    为什么必须做（实测踩过的坑）：窗口最小化后 `GetWindowRect` 会返回
    `(-32000,-32000,160,28)` 这种哨兵值，PrintWindow 也只给出一张 160×28 的废图 ——
    但调用方看起来「截图成功了」，于是照着这张废图算出来的坐标全都在屏幕外，
    点击永远落不到目标上。宁可明确拒绝，也不要交出一张会让模型算出错误坐标的图。
    """
    if not hwnd or not user32.IsWindow(hwnd):
        raise RuntimeError("目标窗口不存在（句柄可能已失效），请重新 list_windows 取最新句柄")
    note = ""
    if user32.IsIconic(hwnd):
        user32.ShowWindow(int(hwnd), SW_RESTORE)
        time.sleep(0.3)
        note = (f"注意：目标窗口「{window_title(hwnd) or hwnd}」原本是最小化的，"
                f"已先恢复它再截图（否则截图是废图、算出来的坐标全在屏幕外）。")
    r = window_rect(hwnd)
    w, h = r.right - r.left, r.bottom - r.top
    if r.left <= -30000 or r.top <= -30000 or w <= 8 or h <= 8:
        raise RuntimeError(
            f"无法截取窗口「{window_title(hwnd) or hwnd}」：它仍是最小化/不可见状态"
            f"（窗口矩形 {r.left},{r.top} {w}×{h}）。请先 "
            f"manage_window(action=\"restore\", hwnd={hwnd}) 或 action=\"activate\" "
            f"把它显示出来再操作 —— 否则截图是废图，坐标会全错。")
    return note


def grab_window(hwnd: int, max_w: int = MODEL_MAX_W, grid: bool = False,
                cursor: bool = False, capture: str = "auto") -> tuple:
    """指定窗口抓图。返回 (png, img_w, img_h, x, y, w, h)。

    两条路径，`capture` 决定用哪条：
      * printwindow —— PrintWindow(PW_RENDERFULLCONTENT)，**不受遮挡影响**，首选；
      * screen      —— 把窗口提到最前，再从屏幕 DC 拷贝窗口所在区域（兜底）。
      * auto        —— 先用 printwindow；**抓到空图（全黑/全同色）就自动改走 screen**。

    为什么需要兜底：GPU 渲染的窗口（OpenGL/DirectX/Vulkan，如 Blender、各类 3D/剪辑
    软件）对 PrintWindow 的支持并不一致 —— 有的应用（自绘且不处理 WM_PRINTCLIENT、
    或走独占/硬件叠加层）会返回全黑或空白。此时若直接把黑图喂给模型，模型会「看不见
    界面」，所有点击都成了盲点。这里检测到空图就换成屏幕抓取，并在返回值里如实说明。

    注意：screen 路径要求窗口真的在最上层（会被其他窗口遮挡），所以会先激活并置顶它。
    """
    note = _ensure_window_capturable(hwnd)
    _GRAB_INFO["note"] = note
    rect = window_rect(hwnd)
    w, h = rect.right - rect.left, rect.bottom - rect.top
    if w <= 0 or h <= 0:
        raise RuntimeError("窗口尺寸无效")
    strategy = str(capture or "auto").strip().lower()
    if strategy not in ("auto", "printwindow", "screen"):
        strategy = "auto"
    used = ""
    png = None
    if strategy in ("auto", "printwindow"):
        png, used = _grab_window_printwindow(hwnd, w, h, max_w, grid, cursor, rect)
    if png is None and strategy in ("auto", "screen"):
        png, used = _grab_window_screen(hwnd, rect, w, h, max_w, grid, cursor)
    if png is None:
        raise RuntimeError("窗口抓图失败：PrintWindow 与屏幕抓取都没能取到画面")
    _GRAB_INFO["strategy"] = used
    return png, _GRAB_INFO["img_w"], _GRAB_INFO["img_h"], rect.left, rect.top, w, h


# 记录最近一次窗口抓图的元信息（供工具文案如实说明用了哪条路径、有没有踩到空图）
_GRAB_INFO = {"strategy": "", "blank": False, "note": ""}


def _grab_window_printwindow(hwnd, w, h, max_w, grid, cursor, rect):
    """PrintWindow 路径：抓到空图（全黑/全同色）时返回 (None, 原因) 让调用方兜底。"""
    scr = user32.GetDC(0)
    mem = gdi32.CreateCompatibleDC(scr)
    bmp = gdi32.CreateCompatibleBitmap(scr, w, h)
    old = gdi32.SelectObject(mem, bmp)
    try:
        if not user32.PrintWindow(hwnd, mem, 2):    # PW_RENDERFULLCONTENT
            return None, "PrintWindow 返回失败"
        if _bits_are_blank(_dib_bits(mem, bmp, w, h), w, h):
            return None, "PrintWindow 抓到的是空白画面"
        cx, cy = _cursor_pos()
        png, iw, ih = _capture_scaled(mem, 0, 0, w, h, min(max_w, w),
                                      GRID_CELLS if grid else 0,
                                      (cx - rect.left, cy - rect.top) if cursor else None)
        _GRAB_INFO.update({"img_w": iw, "img_h": ih, "blank": False})
        return png, "PrintWindow"
    finally:
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(0, scr)


def _grab_window_screen(hwnd, rect, w, h, max_w, grid, cursor):
    """屏幕区域路径：先把窗口提到最前，再从屏幕 DC 拷贝它覆盖的区域。

    裁剪要点：窗口可能部分在屏外（最大化窗口的隐形边框会让 rect 超出屏幕），
    这里把抓取区域裁到虚拟屏内，并把**裁剪后的实际区域**写进 _GRAB_INFO，
    保证图像像素 ↔ 物理坐标的映射依然精确（否则落点会整体偏移）。
    """
    activate_window(hwnd)
    time.sleep(0.25)
    vx, vy, vw, vh = _virtual_screen()
    sx, sy = max(vx, rect.left), max(vy, rect.top)
    ex, ey = min(vx + vw, rect.right), min(vy + vh, rect.bottom)
    cw, ch = ex - sx, ey - sy
    if cw <= 0 or ch <= 0:
        return None, ""
    scr = user32.GetDC(0)
    mem = gdi32.CreateCompatibleDC(scr)
    bmp = gdi32.CreateCompatibleBitmap(scr, cw, ch)
    old = gdi32.SelectObject(mem, bmp)
    try:
        if not gdi32.BitBlt(int(mem), 0, 0, cw, ch, int(scr), sx, sy, SRCCOPY):
            return None, ""
        cx, cy = _cursor_pos()
        png, iw, ih = _capture_scaled(mem, 0, 0, cw, ch, min(max_w, cw),
                                      GRID_CELLS if grid else 0,
                                      (cx - sx, cy - sy) if cursor else None)
        _GRAB_INFO.update({"img_w": iw, "img_h": ih, "blank": False,
                           "offset": (sx - rect.left, sy - rect.top)})
        return png, "屏幕抓取"
    finally:
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(0, scr)


def _bits_are_blank(bits: bytes, w: int, h: int) -> bool:
    """判断位图是不是「空白」—— GPU 窗口抓取失败的典型特征。

    两条判据（都要保守，宁可多跑一次屏幕抓取，也不能把正常界面误判成空白）：
      ① 98% 以上是近黑（各通道 < 8）→ 抓取失败；
      ② 99.5% 以上同色，且该颜色是「近黑（< 24）或近白（> 240）」→ 整块空表面。
    刻意**不**把中等深色当成空白：Blender 这类软件本身就是深色主题
    （主背景约 #303030 = 48），若把「均匀深色」判成空白，每次截图都会白跑一次
    屏幕抓取，还会平白把窗口抢到最前打扰用户。

    抽样步长按面积自适应（最多看约 2 万个像素），大图也不会拖慢。
    """
    total = w * h
    if total <= 0 or len(bits) < total * 4:
        return True
    step = max(1, total // 20000)
    dark = 0
    n = 0
    first = None
    first_raw = None
    same = 0
    for p in range(0, total, step):
        i = p * 4
        b, g, r = bits[i], bits[i + 1], bits[i + 2]
        n += 1
        if r < 8 and g < 8 and b < 8:
            dark += 1
        cur = (r >> 4, g >> 4, b >> 4)
        if first is None:
            first = cur
            first_raw = (r, g, b)
        elif cur == first:
            same += 1
    if n == 0:
        return True
    if dark >= n * 0.98:
        return True
    if same >= n * 0.995 and first_raw is not None:
        return max(first_raw) < 24 or min(first_raw) > 240
    return False


def _region_snapshot_at(x: int, y: int, radius: int = 48) -> tuple:
    """取以 (x,y) 为中心的小方块原始像素（BGRA），并返回它在屏幕上的真实原点。

    返回 (bits, x0, y0, w, h)。为什么要返回原点：区域会按虚拟桌面边界夹取，
    夹取后的原点未必是 (x-r, y-r)；元素吸附要按「局部像素 → 屏幕坐标」反推，
    拿错原点会让吸附点整体偏移。

    为什么存原始像素而不是稀疏采样：稀疏网格会**漏掉细线**（实测：靶场画了
    2px 宽的十字标记，12×12 采样一次都没采到，导致误报「点击没生效」）。
    96×96 区域约 36KB，比一次全屏截图（数 MB）便宜得多，代价可以忽略。

    夹取用**虚拟桌面**边界：副屏在主屏左侧/上方时，按主屏 (0,0) 夹取会把区域
    整体挪走，比对与元素吸附都会读到错误位置的像素。
    """
    vx, vy, vw, vh = _virtual_screen()
    r = max(8, int(radius))
    x0 = max(vx, min(vx + vw - 1, int(x) - r))
    y0 = max(vy, min(vy + vh - 1, int(y) - r))
    w = max(1, min(2 * r, vx + vw - x0))
    h = max(1, min(2 * r, vy + vh - y0))
    dc = user32.GetDC(0)
    mem = gdi32.CreateCompatibleDC(dc)
    bmp = gdi32.CreateCompatibleBitmap(dc, w, h)
    old = gdi32.SelectObject(mem, bmp)
    try:
        if not gdi32.BitBlt(int(mem), 0, 0, w, h, int(dc), x0, y0, SRCCOPY):
            return b"", x0, y0, w, h
        return _dib_bits(mem, bmp, w, h), x0, y0, w, h
    except Exception:
        return b"", x0, y0, w, h
    finally:
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(0, dc)


def _region_snapshot(x: int, y: int, radius: int = 48) -> bytes:
    """同 _region_snapshot_at，只要像素（用于点击前后逐像素比对）。"""
    return _region_snapshot_at(x, y, radius)[0]


def _grab_rect_bits(x0: int, y0: int, w: int, h: int) -> tuple:
    """抓取**显式矩形**区域的原始 BGRA 像素，返回 (bits, x0, y0, w, h)。

    与 _region_snapshot_at 的分工：那个以「估计点 + 半径」为中心，服务元素吸附；
    这个按矩形抓取，服务任务栏这类**整条通栏**（只有矩形能表达「屏幕底部那一带」）。
    同样按**虚拟桌面**边界夹取，并如实返回夹取后的真实原点 —— 调用方靠它把局部像素
    换算回屏幕坐标，拿错原点会让所有候选点的坐标整体偏移。

    1:1 抓取、绝不缩放：局部像素与屏幕像素一一对应。
    """
    vx, vy, vw, vh = _virtual_screen()
    w, h = max(1, int(w)), max(1, int(h))
    x0 = max(vx, min(vx + vw - 1, int(x0)))
    y0 = max(vy, min(vy + vh - 1, int(y0)))
    w = max(1, min(w, vx + vw - x0))
    h = max(1, min(h, vy + vh - y0))
    dc = user32.GetDC(0)
    mem = gdi32.CreateCompatibleDC(dc)
    bmp = gdi32.CreateCompatibleBitmap(dc, w, h)
    old = gdi32.SelectObject(mem, bmp)
    try:
        if not gdi32.BitBlt(int(mem), 0, 0, w, h, int(dc), x0, y0, SRCCOPY):
            return b"", x0, y0, w, h
        return _dib_bits(mem, bmp, w, h), x0, y0, w, h
    except Exception:
        return b"", x0, y0, w, h
    finally:
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(0, dc)


def _region_diff(a: bytes, b: bytes, tol: int = 32) -> tuple:
    """逐像素比对两份快照：返回 (变化像素数, 总像素数)。

    tol 是「三通道绝对差之和」的阈值，用来滤掉压缩/抖动噪声；细线标记
    （2px 宽、颜色差 200+）能轻松越过阈值。
    """
    if not a or not b or len(a) != len(b):
        return 0, 0
    n = len(a) // 4
    changed = 0
    for i in range(0, n * 4, 4):
        if (abs(a[i] - b[i]) + abs(a[i + 1] - b[i + 1])
                + abs(a[i + 2] - b[i + 2])) > tol:
            changed += 1
    return changed, n


# ============================================================================
# 像素级元素吸附：把「模型估的坐标」修正到「那个元素的正中心」
# ============================================================================
# 为什么需要它：模型看图给坐标，本质是**估**出来的。屏幕被缩放成 1280 宽的图后，
# 图上 1px 的估计误差在 4K 屏上就是 3px；而 Blender / 剪辑软件里 20px 高的按钮、
# 菜单项密集排布，差 3px 就点到隔壁了。可访问性树（UIA）能给出精确矩形，但这类
# GPU 自绘界面根本没有控件树。于是改用**纯像素**的办法：元素与背景的色差天然把
# 界面切成一块块连通域，找到离估计点最近的那一块，点它的几何中心 —— 不依赖任何
# 控件信息，对自绘界面同样有效，而且结果是确定性的（同样输入必得同样输出）。
SNAP_REACH_DEFAULT = 88     # 默认搜索半径（像素）：越大越能救回偏得多的估计，也越慢。
                            # 取 88 而不是更小，是被**列表**逼出来的：联系人/消息列表的
                            # 一行有几百像素宽，但内容（头像、名字）只占左边一小块，
                            # 模型估的「这一行的中间」往往落在一大片空白里 —— 半径小到
                            # 48 时区域根本够不着内容，吸附只能放弃（实测过，见下方说明）。
SNAP_REACH_MAX = 140        # 搜索半径上限：区域面积是 (2r)^2，再大就影响响应速度
SNAP_MIN_AREA = 16          # 候选元素最小像素数：再小多半是文字笔画/噪点
SNAP_MAX_AREA_RATIO = 0.55  # 候选元素最大面积占比：超过就是背景/整块面板，不是元素
SNAP_BG_DIST = 18           # 与背景色的色差（|dR|+|dG|+|dB|）超过它才算「前景像素」。
                            # 取 18（≈每通道 6 级）而不是更大的值：Blender 这类专业软件的
                            # 按钮填充与面板背景往往只差十几级，阈值太大会**完全找不到元素**；
                            # 调低带来的「把渐变/阴影当元素」风险由「触边界即淘汰」+
                            # 面积上限 + 距离闸门共同兜住（渐变块必然触边界，会被淘汰）。
SNAP_MIN_SPAN = 4           # 候选外接矩形的最小边长
SNAP_MERGE_GAP = 2          # 相邻碎片间距 ≤ 它就并成同一个目标：图标会被高光/抗锯齿
                            # 切成两三块，按碎片中心吸附会偏到碎片上；2px 能合并笔画缝隙，
                            # 又远小于并排按钮之间的间距（一般 ≥6px），不会误并隔壁按钮
SNAP_MERGE_OVERLAP = 2      # 合并时另一轴至少要重叠这么多像素：只合并“同一块”的碎片，
                            # 避免斜对角的两个噪点被并成一个假大块

# ---- 横向内容带（列表行）检测 ----------------------------------------------
# 为什么需要它：连通域算法只懂「离估计点最近的那一块」，**没有「行」的概念**。
# 于是碰到列表就出两种错（都实测复现过）：
#   ① 估计点落在行内的空白区（微信一行几百像素宽，内容只在左边）⇒ 区域里什么都没有
#      ⇒ 吸附直接放弃 ⇒ 用不精确的原坐标点下去 ⇒ 点到隔壁行；
#   ② 有内容时吸到**单个汉字**（15×15）而不是**整行**，垂直容差只有半个字高。
# 补上「带」这一层：把区域按 y 做前景投影，切出一条条横向内容带，行的中心就出来了。
# 关键是**只动 y、不动 x** —— 列表整行可点，x 保持模型给的位置最安全；
# 而工具条上并排的按钮如果按整条带的 x 中心去吸附，会点到**隔壁按钮**（回归）。
SNAP_BAND_MIN_PIXELS = 3    # 某一行 y 上至少要有几个前景像素才算「有内容」
SNAP_BAND_MERGE_GAP = 5     # 相邻内容行间距小于它就算同一条带：汉字横笔画之间有空隙
                            # （「三」「目」这类会断成好几条），5px 能把一个字并回一条，
                            # 又远小于行间距（几十像素），不会把相邻两行并到一起
SNAP_BAND_MAX_RATIO = 0.62  # 带高超过区域高的这个比例 ⇒ 是**连续内容**（Blender 视口、
                            # 画布、大片面板），不是「行」。此时退回连通域逻辑，绝不按
                            # 带中心吸附 —— 否则会把点击拖到区域正中间去
SNAP_BAND_MAX_REPORT = 8    # 最多报告几条带（给模型当「行标尺」用）
SNAP_BAND_MIN_SPAN = 6      # 带的最小高度：低于它的不是「行」，多半是行分隔线（1px）——
                            # 分隔线横贯整行、前景像素很多，不滤掉会被误当成一条带，
                            # 吸上去等于点在两行交界处，比不吸还糟（实测踩过）

# ---- 候选菜单 --------------------------------------------------------------
# 为什么要有它：吸附只会挑「离估计点最近的那一块」。这在按钮**成排密排**的界面上
# 是错的 —— 估计点只要稍微偏一点，最近的那一块就是**隔壁按钮**，于是「点偏」变成了
# 「稳稳地点错」，而且模型完全无从察觉。人遇到这种情况会看一眼说「不，是右边那个」，
# 模型却没有任何办法表达「不，是第 3 个」。
# 候选菜单就是把这件事补上：把区域里所有像控件的目标**编号框出来**，连同它们的
# 屏幕坐标一起给出。于是「估一个位置」变成「挑一个编号」—— 模型只需在图上**认出**
# 目标（它擅长），不需要**算出**目标在哪（它不擅长）。这是纯几何/像素信息，
# 不含任何控件名或可点语义，与「删掉罗列可点击元素」的决定不冲突。
SNAP_CAND_MAX = 9           # 候选菜单最多列几个（太多反而让模型挑花眼）


def _bg_color(bits: bytes, w: int, h: int):
    """估计区域背景色 = 四条边上出现最多的颜色（取 8 级量化后的众数，再求均值）。

    为什么用边框而不是全图众数：UI 里元素很少铺满最外圈，边框几乎总是背景；
    而全图众数在「大片面板 + 少量控件」时会被面板内部颜色带偏。
    """
    from collections import Counter
    cnt = Counter()
    ring = []
    for i in range(w):
        for yy in (0, h - 1):
            o = (yy * w + i) * 4
            ring.append(o)
    for j in range(h):
        for xx in (0, w - 1):
            o = (j * w + xx) * 4
            ring.append(o)
    for o in ring:
        cnt[(bits[o] >> 3, bits[o + 1] >> 3, bits[o + 2] >> 3)] += 1
    if not cnt:
        return None
    key = cnt.most_common(1)[0][0]
    n = sr = sg = sb = 0
    for o in ring:
        if (bits[o] >> 3, bits[o + 1] >> 3, bits[o + 2] >> 3) == key:
            sr += bits[o]
            sg += bits[o + 1]
            sb += bits[o + 2]
            n += 1
    if not n:
        return None
    return (sr // n, sg // n, sb // n)


def _fg_mask(bits: bytes, w: int, h: int, bg, tol: int) -> bytearray:
    """前景掩码：与背景色的三通道绝对差之和超过 tol 的像素置 1。"""
    n = w * h
    mask = bytearray(n)
    if len(bits) < n * 4:
        return mask
    br, bgc, bb = bg
    for i in range(n):
        o = i * 4
        if (abs(bits[o] - br) + abs(bits[o + 1] - bgc)
                + abs(bits[o + 2] - bb)) > tol:
            mask[i] = 1
    return mask


def _fg_components(bits: bytes, w: int, h: int, bg, tol: int) -> list:
    """把「明显不同于背景」的像素做 4 邻域连通域标记，返回 [{area,bbox,dist}]。

    刻意**不**要求相邻像素彼此同色：按钮的填充色与它上面的文字笔画颜色不同，但两者
    合起来才是这个按钮的可见区域 —— 合并后外接矩形正好是按钮本身，中心也就准了。
    相邻元素之间的背景缝隙天然把它们分开，所以不会把一排按钮并成一块。
    """
    return _components_from_mask(_fg_mask(bits, w, h, bg, tol), w, h)


def _components_from_mask(is_fg: bytearray, w: int, h: int) -> list:
    """连通域标记本体。掩码单独传进来，是为了让「连通域」与「行带」共用同一份掩码 ——
    区域放大到 176×176 后，重复构造掩码是纯浪费（实测能省掉近三成耗时）。"""
    n = w * h
    if len(is_fg) < n:
        return []
    seen = bytearray(n)
    out = []
    for start in range(n):
        if not is_fg[start] or seen[start]:
            continue
        seen[start] = 1
        stack = [start]
        area = 0
        minx = maxx = start % w
        miny = maxy = start // w
        while stack:
            cur = stack.pop()
            area += 1
            cx, cy = cur % w, cur // w
            if cx < minx:
                minx = cx
            elif cx > maxx:
                maxx = cx
            if cy < miny:
                miny = cy
            elif cy > maxy:
                maxy = cy
            if cx > 0:
                k = cur - 1
                if is_fg[k] and not seen[k]:
                    seen[k] = 1
                    stack.append(k)
            if cx < w - 1:
                k = cur + 1
                if is_fg[k] and not seen[k]:
                    seen[k] = 1
                    stack.append(k)
            if cy > 0:
                k = cur - w
                if is_fg[k] and not seen[k]:
                    seen[k] = 1
                    stack.append(k)
            if cy < h - 1:
                k = cur + w
                if is_fg[k] and not seen[k]:
                    seen[k] = 1
                    stack.append(k)
        out.append({"area": area, "minx": minx, "miny": miny,
                    "maxx": maxx, "maxy": maxy})
    return out


def _merge_components(comps: list, gap: int = SNAP_MERGE_GAP,
                      overlap: int = SNAP_MERGE_OVERLAP) -> list:
    """把同一目标被高光/抗锯齿切开的碎片并回一整块（按外接矩形相邻关系）。

    为什么必须合并：任务栏图标、扁平按钮上的图标常被高光切成 2~4 块，连通域会得到
    好几个小矩形；取其中任一块的中心都可能落偏。合并后的外接矩形才是这个目标真正的
    可见范围，中心也才稳。

    只合并「一轴间距 ≤ gap 且另一轴至少重叠 overlap 像素」的碎片：并排按钮之间的缝隙
    通常 ≥6px，不会被并到一起；斜对角的两点也不满足重叠条件，不会拼出假目标。
    """
    if len(comps) < 2:
        return comps
    out = [dict(c) for c in comps]
    merged = True
    while merged:
        merged = False
        for i in range(len(out)):
            a = out[i]
            for j in range(i + 1, len(out)):
                b = out[j]
                h_gap = max(b["minx"] - a["maxx"], a["minx"] - b["maxx"]) - 1
                v_gap = max(b["miny"] - a["maxy"], a["miny"] - b["maxy"]) - 1
                h_ov = min(a["maxx"], b["maxx"]) - max(a["minx"], b["minx"]) + 1
                v_ov = min(a["maxy"], b["maxy"]) - max(a["miny"], b["miny"]) + 1
                near_x = h_gap <= gap and v_ov >= overlap
                near_y = v_gap <= gap and h_ov >= overlap
                if not (near_x or near_y):
                    continue
                out[i] = {"area": a["area"] + b["area"],
                          "minx": min(a["minx"], b["minx"]),
                          "miny": min(a["miny"], b["miny"]),
                          "maxx": max(a["maxx"], b["maxx"]),
                          "maxy": max(a["maxy"], b["maxy"])}
                del out[j]
                merged = True
                break
            if merged:
                break
    return out


def _fg_bands(bits: bytes, w: int, h: int, bg, tol: int,
              merge_gap: int = SNAP_BAND_MERGE_GAP,
              min_pixels: int = SNAP_BAND_MIN_PIXELS,
              min_span: int = SNAP_BAND_MIN_SPAN) -> list:
    """把前景按 y 做投影，切出「横向内容带」，返回 [{"ymin","ymax"}]（局部坐标）。

    这是列表行的几何：每一行内容在 y 上占一段，行与行之间是背景缝隙。投影到 y 之后
    行的位置一目了然，完全不需要知道那一行「是什么」—— 拿到的是一把**行标尺**。

    合并间距小于 merge_gap 的相邻内容行：汉字的横笔画之间本来就有空隙（「三」会断成
    三条），不合并会把一个字拆成好几段，行的中心就偏了。

    丢弃高度小于 min_span 的带：微信/大多数列表的行之间有一条 1px 分隔线，它横贯整行
    因而前景像素很多，会被误当成一条「带」—— 但分隔线不是行，吸到它上面等于点在两行
    的交界处，比不吸还糟（实测过）。6px 能把分隔线滤掉，又留得住菜单项那种十几像素
    高的文字带。
    """
    n = w * h
    if len(bits) < n * 4:
        return []
    return _bands_from_mask(_fg_mask(bits, w, h, bg, tol), w, h,
                            merge_gap, min_pixels, min_span)


def _bands_from_mask(mask: bytearray, w: int, h: int,
                     merge_gap: int = SNAP_BAND_MERGE_GAP,
                     min_pixels: int = SNAP_BAND_MIN_PIXELS,
                     min_span: int = SNAP_BAND_MIN_SPAN) -> list:
    """行带检测本体（掩码由调用方给，与连通域共用同一份）。"""
    if len(mask) < w * h:
        return []
    rows = bytearray(h)
    for y in range(h):
        base = y * w
        cnt = 0
        for x in range(w):
            if mask[base + x]:
                cnt += 1
                if cnt >= min_pixels:
                    rows[y] = 1
                    break
    bands = []
    y = 0
    while y < h:
        if not rows[y]:
            y += 1
            continue
        ymin = last = y
        gap = 0
        while y < h and gap <= merge_gap:
            if rows[y]:
                last = y
                gap = 0
            else:
                gap += 1
            y += 1
        if last - ymin + 1 >= min_span:
            # 顺便算出这条带的水平范围：吸附本身只需要 y，但**候选菜单**要把每条带
            # 框出来给模型看（见 refine_point），没有 x 范围就只能画一条通栏的横线。
            bx0, bx1 = w, -1
            for yy in range(ymin, last + 1):
                base = yy * w
                for xx in range(w):
                    if mask[base + xx]:
                        if xx < bx0:
                            bx0 = xx
                        if xx > bx1:
                            bx1 = xx
            if bx1 >= bx0:
                bands.append({"ymin": ymin, "ymax": last,
                              "xmin": bx0, "xmax": bx1})
    return bands


def _band_ok(band: dict, h: int) -> bool:
    """这条带「像一行」吗？吸附与候选菜单共用同一套判据，避免两处标准不一致。

    拒绝条件（宁可退回连通域逻辑，也不按一个可疑的带中心去动坐标）：
      * 带高超过区域高的 SNAP_BAND_MAX_RATIO —— 这是连续内容（视口/画布/整块面板），
        不是行；按它的中心吸附会把点击拖到区域正中间，是纯粹的伤害；
      * 带贴到区域上/下边界 —— 行的真实高度未知（被截断了），中心不可信。
    """
    if (band["ymax"] - band["ymin"] + 1) > SNAP_BAND_MAX_RATIO * h:
        return False
    if band["ymin"] <= 0 or band["ymax"] >= h - 1:
        return False
    return True


def _pick_band(bands: list, ly: int, h: int):
    """选出估计点所在（或最近）的那条带；不满足「像一行」的条件时返回 None。"""
    if not bands:
        return None
    inside = [b for b in bands if b["ymin"] <= ly <= b["ymax"]]
    if inside:
        band = inside[0]
    else:
        band = min(bands, key=lambda b: min(abs(ly - b["ymin"]), abs(ly - b["ymax"])))
    return band if _band_ok(band, h) else None


def _fill_candidates(out: list, comps: list, bands: list, usable, x, y, lx, ly,
                     x0: int, y0: int, w: int, h: int) -> None:
    """把区域里的候选目标编号填进 out（按离估计点由近到远），供 refine_point 列菜单。

    两类候选：
      * 「行」= 横向内容带（列表行 / 菜单项 / 工具条的一排）。列表整行可点，所以候选点
        取 (估计点的 x, 带中心 y) —— **只对齐 y、不把 x 拽到带中心**，否则工具条上会
        点到隔壁按钮（这条回归专门有测试）。
      * 「块」= 连通域（按钮、图标、单个文字块）。候选点取其外接矩形中心。
    两类都过同一套「像不像一个可点目标」的判据（_band_ok / usable），所以菜单里不会
    出现分隔线、视口背景这类假目标。
    """
    pool = []
    for b in bands:
        if not _band_ok(b, h):
            continue
        cy_b = y0 + (b["ymin"] + b["ymax"] + 1) // 2
        pool.append({"kind": "行", "x": int(x), "y": cy_b,
                     "w": b["xmax"] - b["xmin"] + 1,
                     "h": b["ymax"] - b["ymin"] + 1,
                     "box": (x0 + b["xmin"], y0 + b["ymin"],
                             x0 + b["xmax"], y0 + b["ymax"]),
                     "d": float(abs(cy_b - int(y)))})
    for c in comps:
        if not usable(c):
            continue
        pool.append({"kind": "块",
                     "x": x0 + (c["minx"] + c["maxx"] + 1) // 2,
                     "y": y0 + (c["miny"] + c["maxy"] + 1) // 2,
                     "w": c["maxx"] - c["minx"] + 1,
                     "h": c["maxy"] - c["miny"] + 1,
                     "box": (x0 + c["minx"], y0 + c["miny"],
                             x0 + c["maxx"], y0 + c["maxy"]),
                     "d": ((max(c["minx"] - lx, 0, lx - c["maxx"]) ** 2
                            + max(c["miny"] - ly, 0, ly - c["maxy"]) ** 2) ** 0.5)})
    pool.sort(key=lambda t: t["d"])
    for i, c in enumerate(pool[:SNAP_CAND_MAX], 1):
        c["i"] = i
        out.append(c)


def _snap_region(x: int, y: int, reach: int = SNAP_REACH_DEFAULT,
                 max_shift=None, cands=None) -> tuple:
    """单次吸附（固定搜索半径）：把 (x,y) 吸附到附近最可信的元素中心。

    返回 (sx, sy, note, ruler)。`_snap_core` 会在本函数找不到目标时自动扩大半径重试。

    传了 cands（一个 list）时，额外把区域里所有**像控件**的目标按「离估计点由近到远」
    填进去，每项含 kind（"行"/"块"）、屏幕坐标中心 (x,y)、尺寸 (w,h)、外接矩形 box
    与距离 d —— 这就是**候选菜单**（见 SNAP_CAND_MAX 的说明）。为什么用「传列表进来
    填充」而不是多返回一个值：保持返回元组长度不变，既有调用方与测试都不用改。

    两条路径，先试**行/带模式**，不成立再退**连通域模式**。

    ── 行/带模式（列表、菜单、工具条）──
    区域按 y 做前景投影切出「横向内容带」，估计点所在的那条带就是它的「行」，
    y 对齐到该带中心。为什么必须单独有这一层：连通域只懂「最近的那一块」，
    碰到列表必错 —— 估计点落在行内空白区时区域里什么都没有，吸附直接放弃
    （实测：微信联系人一行几百像素宽，内容只在左边，半径 48 时区域够不着）；
    有内容时又只吸到单个汉字（15×15），垂直容差只有半个字高。
    只对齐 y、不按带中心改 x：列表整行可点，保持原 x 最安全；而工具条上并排的
    按钮若按整条带的 x 中心吸附，会点到**隔壁按钮**。

    ── 连通域模式（按钮、图标、视口内元素）──
      1. 以 1:1 抓取 (x,y) 周围 ±reach 的**全分辨率**像素（绝不缩放，1 图 px = 1 屏 px）；
      2. 用区域四条边的众数色估计背景色；
      3. 把色差超过阈值的像素做 4 邻域连通域标记（按钮填充 + 其上的文字会连成一块，
         元素之间的背景缝隙自然把它们分开）；
      4. 淘汰不像控件的连通域：面积过小（噪点/笔画）、面积过大（背景/整块面板）、
         **外接矩形触到区域边界**（真实尺寸未知 ⇒ 中心不可信）、边长过小；
      5. 在幸存候选里按可信度挑一个：**估计点落在其中的候选优先**，其次按到候选
         外接矩形的距离、再到中心距离、最后偏向更大的块（同距离时按钮块胜过零碎笔画），
         吸附到它的外接矩形中心。

    保守原则：任何一步不成立就**放弃吸附、原样返回**。宁可不动，也不能把点击带到
    别的地方 —— 这正是它敢默认开启的原因。

    两道位移闸门（都算「可信度」）：
      * 估计点到候选外接矩形的距离 ≤ reach/2 —— 候选必须**确实就在估计点附近**；
      * 最终位移 ≤ reach —— 吸附到元素中心可能移动较多（元素越大中心越远），
        但绝不允许跳到 reach 之外。分开设这两道闸，是因为「离元素很近但元素很大」
        （估计点刚好压在按钮边缘、中心在 40px 外）恰恰是最该吸附的情形，
        若只卡总位移就会把它拒掉。

    ruler 是本次区域里检测到的所有内容带的中心 y（屏幕物理坐标），用来当**行标尺**：
    模型据此判断「我要点的是第几行」—— 这是纯几何数字，不含任何元素名或可点信息。
    """
    try:
        reach = max(16, min(int(reach), SNAP_REACH_MAX))
    except (TypeError, ValueError):
        reach = SNAP_REACH_DEFAULT
    near_cap = int(max_shift) if max_shift else max(10, reach // 2)
    shift_cap = reach
    bits, x0, y0, w, h = _region_snapshot_at(x, y, reach)
    if not bits:
        return x, y, "", []
    bg = _bg_color(bits, w, h)
    if bg is None:
        return x, y, "", []
    lx, ly = int(x) - x0, int(y) - y0          # 估计点在区域内的局部坐标

    def _usable(c):
        """连通域是否「像控件」：面积、边长合适，且没有被区域边界截断。"""
        bw = c["maxx"] - c["minx"] + 1
        bh = c["maxy"] - c["miny"] + 1
        if c["area"] < SNAP_MIN_AREA or bw < SNAP_MIN_SPAN or bh < SNAP_MIN_SPAN:
            return False
        if c["area"] > SNAP_MAX_AREA_RATIO * w * h:
            return False
        # 触到区域边界 ⇒ 元素被截断，真实中心在区域之外，不能作为吸附目标
        if c["minx"] <= 0 or c["miny"] <= 0 or c["maxx"] >= w - 1 or c["maxy"] >= h - 1:
            return False
        return True

    # 掩码只构造一次，连通域与行带共用（区域 176×176 时这是最大的一笔可省开销）
    mask = _fg_mask(bits, w, h, bg, SNAP_BG_DIST)
    # 先并回被高光/抗锯齿切开的同一目标碎片，再判断可用性：按碎片中心吸附会偏
    comps = _merge_components(_components_from_mask(mask, w, h))
    bands = _bands_from_mask(mask, w, h)
    band = _pick_band(bands, ly, h)
    ruler = [{"y": y0 + (b["ymin"] + b["ymax"] + 1) // 2,
              "h": b["ymax"] - b["ymin"] + 1}
             for b in bands[:SNAP_BAND_MAX_REPORT]]

    if cands is not None:
        _fill_candidates(cands, comps, bands, _usable, x, y, lx, ly, x0, y0, w, h)

    if band is not None:
        # ---- 行/带模式：y 对齐到这一条带的中心 ----
        sy = y0 + (band["ymin"] + band["ymax"] + 1) // 2
        # x 取「同一条带内」离估计点最近的候选。距离按外接矩形算，估计点落在框内即为 0，
        # 所以大元素（整行、大按钮）不受影响；只有估计点**在元素之外**时才受这道闸门约束。
        # 列表整行可点，模型估的 x 常常落在行内空白处，此时保持原 x 最安全 —— 不硬拽。
        inband = [c for c in comps
                  if _usable(c) and c["maxy"] >= band["ymin"] and c["miny"] <= band["ymax"]]
        pick = None
        for c in inband:
            dx = max(c["minx"] - lx, 0, lx - c["maxx"])
            if dx <= near_cap and (pick is None or dx < pick[0]):
                pick = (dx, c)
        if pick is not None:
            c = pick[1]
            sx = x0 + (c["minx"] + c["maxx"] + 1) // 2
        else:
            sx = int(x)
        shift = ((sx - int(x)) ** 2 + (sy - int(y)) ** 2) ** 0.5
        if shift > shift_cap:
            return x, y, "", ruler
        if (sx, sy) == (int(x), int(y)):
            return sx, sy, "", ruler
        how = (f"已吸附到列表行（横向内容带）的中心：该带在屏幕上 y "
               f"{band['ymin'] + y0}~{band['ymax'] + y0}，高 "
               f"{band['ymax'] - band['ymin'] + 1}px")
        if pick is not None:
            how += (f"；x 取同行最近的 {c['maxx'] - c['minx'] + 1}×"
                    f"{c['maxy'] - c['miny'] + 1}px 连通域中心")
        else:
            how += "；该行内容离估计点较远，x 保持你给的位置（整行都可点，这是安全的）"
        return sx, sy, f"{how}。由 ({x},{y}) 修正为 ({sx},{sy})，位移 {shift:.1f}px。", ruler

    # ---- 连通域模式：没有像样的「行」时（按钮、图标、视口内元素）按可信度吸附 ----
    if not comps:
        return x, y, "", ruler
    best = None
    for c in comps:
        if not _usable(c):
            continue
        # 估计点到该候选外接矩形的距离（点在框内则为 0）
        dx = max(c["minx"] - lx, 0, lx - c["maxx"])
        dy = max(c["miny"] - ly, 0, ly - c["maxy"])
        dist = (dx * dx + dy * dy) ** 0.5
        if dist > near_cap:
            continue
        inside = (c["minx"] <= lx <= c["maxx"] and c["miny"] <= ly <= c["maxy"])
        cx = (c["minx"] + c["maxx"]) / 2.0
        cy = (c["miny"] + c["maxy"]) / 2.0
        cdist = ((cx - lx) ** 2 + (cy - ly) ** 2) ** 0.5
        area = (c["maxx"] - c["minx"] + 1) * (c["maxy"] - c["miny"] + 1)
        # 排序键：点在候选内优先；其次离外接矩形近；再其次中心近；同距离时大块优先
        # （按钮整体胜过按钮上零碎的文字笔画）；最后用坐标兜底，保证确定性。
        key = (0 if inside else 1, round(dist, 3), round(cdist, 3), -area,
               c["miny"], c["minx"])
        if best is None or key < best[0]:
            best = (key, c, inside)
    if best is None:
        return x, y, "", ruler

    c = best[1]
    sx = x0 + (c["minx"] + c["maxx"] + 1) // 2
    sy = y0 + (c["miny"] + c["maxy"] + 1) // 2
    shift = ((sx - int(x)) ** 2 + (sy - int(y)) ** 2) ** 0.5
    if shift > shift_cap:
        return x, y, (f"附近找到疑似元素（{c['maxx'] - c['minx'] + 1}×"
                      f"{c['maxy'] - c['miny'] + 1}px）但它的中心离估计点 {shift:.0f}px，"
                      f"超出可信范围，未吸附。"), ruler
    if (sx, sy) == (int(x), int(y)):
        return sx, sy, "", ruler
    return sx, sy, (f"已吸附到元素中心：{c['maxx'] - c['minx'] + 1}×"
                    f"{c['maxy'] - c['miny'] + 1}px 的连通域，"
                    f"由 ({x},{y}) 修正为 ({sx},{sy})，位移 {shift:.1f}px。"), ruler


def _snap_core(x: int, y: int, reach: int = SNAP_REACH_DEFAULT,
               max_shift=None, cands=None) -> tuple:
    """吸附总入口：先在给定半径内找，找不到再自动扩大半径重试一次。

    为什么要自动扩大：模型给出的坐标偶尔会偏出默认 88px（尤其是整屏缩略图上估算
    的小图标）。过去这种情况直接放弃吸附、按原坐标点 —— 结果就是「点了没反应」。
    现在用同一套算法在更大的区域里再找一次，能把这类偏差救回来；仍找不到则原样返回，
    保守原则不变。refine_point 传 cands 时不做扩张（编号菜单需要稳定的同一片区域）。
    """
    try:
        base = max(16, min(int(reach), SNAP_REACH_MAX))
    except (TypeError, ValueError):
        base = SNAP_REACH_DEFAULT
    sx, sy, note, ruler = _snap_region(x, y, base, max_shift, cands)
    if cands is not None or (sx, sy) != (int(x), int(y)):
        return sx, sy, note, ruler
    wider = min(SNAP_REACH_MAX, max(base * 2, base + 24))
    if wider <= base:
        return sx, sy, note, ruler
    sx2, sy2, note2, ruler2 = _snap_region(x, y, wider, max_shift)
    if (sx2, sy2) == (int(x), int(y)):
        return sx, sy, note, ruler
    return (sx2, sy2,
            f"默认搜索半径 {base}px 内没有可信元素，已扩大到 {wider}px 找到并吸附。"
            + (note2 or ""), ruler2 or ruler)


def _snap_to_element(x: int, y: int, reach: int = SNAP_REACH_DEFAULT,
                     max_shift=None) -> tuple:
    """_snap_core 的薄包装：只返回 (sx, sy, note)，供 click/drag 等直接使用。"""
    sx, sy, note, _ruler = _snap_core(x, y, reach, max_shift)
    return sx, sy, note


# ============================================================================
# 任务栏图标定位：把「屏幕底部那条通栏」里的一个个应用图标量出来
# ============================================================================
# 为什么要有这一层（这是「打开/切换应用」最常见的入口）：
# 用户说「打开微信」时，微信**往往已经在跑**，只是窗口在后台 —— 此时最快的路径不是去
# 开始菜单重新启动一个实例，而是**点任务栏上那个图标**（一次点击 = 切到它的窗口，
# 已在运行的应用不会再启动第二个）。可任务栏是 explorer.exe 自绘的，同样没有任何
# 可读的控件信息，只能靠像素定位。于是这里做三件事：
#   ① 认出任务栏那一条通栏在哪；② 把通栏里的图标切成一个个槽位并**编号**；
#   ③ 按编号点击，并回读落点 + 校验前台窗口是否真的换了。
#
# 实测（本机 1920×1080 / 125% DPI / Win11，全部为真机测量）：
#   * 任务栏是**竖向渐变**背景：底行众数色 (40,32,24)，往上逐渐变亮（(29,34,44) 一类）；
#     整条带里**没有一个像素**与底行众数色的色差小于 18 —— 所以「单一背景色 + 色差
#     阈值」的模型在任务栏上必然失效（实测只能抓到零星碎片）。必须**按行取背景色**。
#   * 通栏的判据因此是「逐行众数色 + 该行众数色的覆盖率」：任务栏行的覆盖率实测
#     ≈0.52~0.58，而紧邻的壁纸行 ≈0.35 且众数色跳变 —— 两者一起判，边界干净利落。
#   * 任务栏图标会被高光/抗锯齿**切成好几块**（实测「英/拼」切成 8 块、时钟数字逐字
#     分开），所以要先按 x 邻近合并成槽位，不能拿连通域直接当图标。
TASKBAR_STRIP_H = 200       # 探任务栏时抓屏幕底部多高（任务栏一般 40~64px，留足余量）
TASKBAR_MIN_H = 24          # 通栏最小高度：低于它多半不是任务栏（壁纸色带/窗口标题栏）
TASKBAR_MAX_H = 140         # 通栏最大高度：再高就不是任务栏了（防止把整片壁纸认成通栏）
TASKBAR_MIN_COVER = 0.45    # 某行的众数色覆盖率下限（任务栏是通栏，背景占满整行）
TASKBAR_COLOR_TOL = 90      # 该行众数色与「底行众数色」的最大色差（竖向渐变会拉开几十级）
TASKBAR_BG_TOL = 24         # 图标前景判定：与**本行背景色**的色差阈值
TASKBAR_PART_MIN_AREA = 10  # 连通域碎片的最小面积（比元素吸附宽松：图标会被高光切碎）
TASKBAR_PART_MIN_SPAN = 3   # 碎片的最小边长（1~2px 的细线是分隔线/阴影，不是图标）
TASKBAR_MERGE_GAP = 12      # 水平间距 ≤ 它就把碎片并成**同一个图标**（笔画/数字之间的缝）
TASKBAR_SLOT_MAX_W = 280    # 合并后的槽位最大宽度（搜索框/小组件按钮天生很宽，要放行）
TASKBAR_SLOT_MIN_W = 6      # 槽位最小宽度
TASKBAR_SLOT_MIN_H = 6      # 槽位最小高度（「↑」这种小字形只有 7px 高，别再抬高）
TASKBAR_RIGHT_ZONE = 360    # 中心 x ≥ 宽-360 视为**右侧托盘/状态区**（输入法/音量/时钟/↑）
TASKBAR_LEFT_ZONE = 150     # 中心 x ≤ 150 视为**最左区**（开始菜单/小组件/搜索）
TASKBAR_IND_MAX_H = 8       # 「已启动指示条」的最大高度（图标下方那条小横条）
TASKBAR_IND_MIN_W = 8
TASKBAR_IND_MAX_W = 60
TASKBAR_IND_BOTTOM = 12     # 指示条距通栏底边的最大距离
TASKBAR_CTX_TOP = 28        # 出图时在通栏上方多带一点上下文（能看到窗口底边，便于对号）
TASKBAR_IMG_MAX_W = 1440    # 通栏出图的目标宽度（1920 宽屏缩一点，图标仍可辨认）
TASKBAR_PANEL_W = 380       # 「↑ 隐藏图标/后台应用面板」的搜索矩形宽
TASKBAR_PANEL_H = 480       # 同上，高
TASKBAR_PANEL_CHANGE_MIN = 0.004  # 面板区域像素变化比例下限：低于它说明面板没弹出来
TASKBAR_PANEL_MAX_SLOTS = 16      # 面板里最多报告几个槽位（多了模型会挑花眼）
TASKBAR_CHEVRON_MAX_W = 44  # 「↑」按钮的宽度上限（用它把 ↑ 与输入法/时钟/音量区分开）
TASKBAR_BASE_ROWS = 2       # 逐列背景色取条带**顶部**几行的中位数（图标垂直居中，顶行是纯背景）
TASKBAR_BASE_FILT = 5       # 逐列背景再做 5 宽中值滤波（顶行万一被图标/窗口边缘污染能盖回来）
TASKBAR_SPLIT_GAP = 3       # 连续「零活动列」宽度 ≥ 它就切开：图标之间的缝 ~8px，字形内的缝 1~2px
TASKBAR_COL_MIN_PX = 1      # 一列至少这么多前景像素才算「有活动」
TASKBAR_IND_ROWS = 4        # 「已启动指示条」只在条带**最底 4 行**里找（实测指示条厚 2~3px）
TASKBAR_IND_MIN_PX = 6      # 这 4 行里至少这么多前景像素才算有指示条
TASKBAR_IND_MIN_W = 6       # 指示条的最小宽度（窄于它多半是图标底边的抗锯齿）


def _row_modal(bits: bytes, w: int, y: int, tol: int = 30):
    """某一行的背景色与它的覆盖率：返回 ((r,g,b), 覆盖率)。

    背景色取该行**众数色**（8 级量化后取桶中心）。任务栏是竖向渐变，只有「按行取背景」
    才能把图标从背景里分出来（见上方实测说明）。
    """
    from collections import Counter
    base = y * w
    cnt = Counter()
    for x in range(w):
        o = (base + x) * 4
        cnt[(bits[o] >> 3, bits[o + 1] >> 3, bits[o + 2] >> 3)] += 1
    if not cnt:
        return None, 0.0
    key = cnt.most_common(1)[0][0]
    col = (key[0] << 3, key[1] << 3, key[2] << 3)
    near = 0
    for x in range(w):
        o = (base + x) * 4
        if (abs(bits[o] - col[0]) + abs(bits[o + 1] - col[1])
                + abs(bits[o + 2] - col[2])) <= tol:
            near += 1
    return col, near / max(1, w)


def _area_modal(bits: bytes, w: int, h: int):
    """整块区域的主色（众数色）——用于「面板背景」这种整块同色的情形。"""
    from collections import Counter
    cnt = Counter()
    for i in range(w * h):
        o = i * 4
        cnt[(bits[o] >> 3, bits[o + 1] >> 3, bits[o + 2] >> 3)] += 1
    if not cnt:
        return None
    key = cnt.most_common(1)[0][0]
    return (key[0] << 3, key[1] << 3, key[2] << 3)


def _detect_taskbar_band(bits: bytes, w: int, h: int):
    """从底部往上找出任务栏那条通栏，返回 (y_top, y_bottom, 背景色, 每行背景色列表)。

    判据两条**必须同时成立**（缺一不可）：
      * 该行众数色的覆盖率 ≥ TASKBAR_MIN_COVER —— 任务栏是**通栏**，背景铺满整行；
      * 该行众数色与「底行众数色」的色差 ≤ TASKBAR_COLOR_TOL —— 挡住「壁纸恰好也有
        一条纯色横带」这种巧合。
    从底行往上连续走，遇到第一个不满足的行就停：它上面那一行就是通栏的上边界。
    找不到（或高度不合常理）返回 None —— 此时要如实告诉模型「没找到任务栏」，
    而不是硬按一个假想的任务栏去点。
    """
    if w <= 0 or h <= 0 or len(bits) < w * h * 4:
        return None
    base_col, base_cov = _row_modal(bits, w, h - 1)
    if base_col is None or base_cov < TASKBAR_MIN_COVER:
        return None
    row_bgs = [None] * h
    row_bgs[h - 1] = base_col
    top = 0
    for y in range(h - 2, -1, -1):
        col, cov = _row_modal(bits, w, y)
        if col is None:
            top = y + 1
            break
        d = abs(col[0] - base_col[0]) + abs(col[1] - base_col[1]) + abs(col[2] - base_col[2])
        if cov < TASKBAR_MIN_COVER or d > TASKBAR_COLOR_TOL:
            top = y + 1
            break
        row_bgs[y] = col
    band_h = h - top
    if band_h < TASKBAR_MIN_H or band_h > TASKBAR_MAX_H:
        return None
    return top, h - 1, base_col, row_bgs


def _taskbar_parts(bits: bytes, w: int, h: int, row_bgs: list) -> list:
    """按**每行自己的背景色**取前景掩码并做连通域标记，返回碎片列表。

    这就是任务栏版本与通用元素吸附的关键差别：通用吸附用「区域四条边的众数色」当背景，
    而任务栏背景是竖向渐变，单一背景色会把整条带都判成前景（实测 100% 像素「像前景」）。
    """
    mask = bytearray(w * h)
    for y in range(h):
        bg = row_bgs[y] if y < len(row_bgs) else None
        if bg is None:
            continue
        base = y * w
        br, bgc, bb = bg
        for x in range(w):
            o = (base + x) * 4
            if (abs(bits[o] - br) + abs(bits[o + 1] - bgc)
                    + abs(bits[o + 2] - bb)) > TASKBAR_BG_TOL:
                mask[base + x] = 1
    return _components_from_mask(mask, w, h)


def _band_base_cols(bits: bytes, w: int, h: int,
                    rows: int = TASKBAR_BASE_ROWS,
                    filt: int = TASKBAR_BASE_FILT) -> list:
    """逐列背景色 base(x)：取条带**顶部**几行的中位数，再做一次水平中值滤波。

    为什么不能只按行取众数色：真实任务栏背景是**双向渐变** —— 竖向由深到浅，横向还会
    因为壁纸/亚克力透出来而整体偏色（实测本机左侧 (80,38,22)、右侧 (43,35,31)，差 40+
    级）。单色模型会把左半条整片判成前景（实测 100% 像素「像前景」，图标全丢）。

    可分离模型 bg(x,y) = base(x) + vdelta(y) 把两个方向拆开：
      * base(x) 从**顶行**取 —— 任务栏图标垂直居中，顶行一定是纯背景；
      * vdelta(y) 从每行众数色取 —— 同一行内，背景的竖向偏移处处相同。
    中值滤波是为兜底：万一某个图标顶到了条带上沿（或窗口边缘压在顶行），
    那一列的 base 会被图标色污染、整列被判成背景，中值滤波会用左右邻居把它盖回来。
    """
    if w <= 0 or h <= 0 or len(bits) < w * h * 4:
        return []
    n = max(1, min(int(rows), h))
    raw = []
    for x in range(w):
        ch = ([], [], [])
        for y in range(n):
            o = (y * w + x) * 4
            ch[0].append(bits[o])
            ch[1].append(bits[o + 1])
            ch[2].append(bits[o + 2])
        raw.append([sorted(c)[len(c) // 2] for c in ch])
    half = max(0, int(filt) // 2)
    if half == 0:
        return [tuple(c) for c in raw]
    out = []
    for x in range(w):
        win = raw[max(0, x - half):min(w, x + half + 1)]
        out.append(tuple(sorted(c[k] for c in win)[len(win) // 2] for k in range(3)))
    return out


def _band_vdelta(row_bgs: list) -> list:
    """每行的竖向偏移 vdelta(y) = 该行众数色 − 顶行众数色（三通道各自算）。"""
    if not row_bgs:
        return []
    top = row_bgs[0]
    return [(c[0] - top[0], c[1] - top[1], c[2] - top[2]) for c in row_bgs]


def _band_fg(bits: bytes, w: int, h: int, base: list, vdelta: list,
             tol: int = TASKBAR_BG_TOL):
    """按可分离渐变模型取前景掩码，同时返回**逐列活动度** act[x]。

    列活动度是任务栏分段的依据：任务栏是**一行**图标，相邻按钮之间一定有整列纯背景；
    而一个图标内部（哪怕是被抗锯齿切碎的字形）几乎不可能出现整列纯背景。
    """
    mask = bytearray(w * h)
    act = [0] * w
    if not base:
        return mask, act
    for y in range(h):
        dy = vdelta[y] if y < len(vdelta) else (0, 0, 0)
        row = y * w
        for x in range(w):
            b = base[x]
            o = (row + x) * 4
            if (abs(bits[o] - (b[0] + dy[0])) + abs(bits[o + 1] - (b[1] + dy[1]))
                    + abs(bits[o + 2] - (b[2] + dy[2]))) > tol:
                mask[row + x] = 1
                act[x] += 1
    return mask, act


def _band_runs(act: list, w: int) -> list:
    """列活动度 → 连续的「有内容」列区间 [(x0, x1), ...]。

    切开条件：连续 TASKBAR_SPLIT_GAP 列**零活动**。这个阈值是实测出来的分界线 ——
    相邻任务栏按钮之间是 6~10px 的纯背景缝，而一个字形（如「拼」）内部的缝只有 1~2px。
    用「连通域 + 间隙合并」反而会把两个相邻按钮并成一个（那样落点落在两图标之间，
    就是最典型的「点了没反应」）。
    """
    runs = []
    x = 0
    while x < w:
        if act[x] < TASKBAR_COL_MIN_PX:
            x += 1
            continue
        s = x
        zero = 0
        while x < w:
            if act[x] < TASKBAR_COL_MIN_PX:
                zero += 1
                if zero >= TASKBAR_SPLIT_GAP:
                    break
            else:
                zero = 0
            x += 1
        e = min(w - 1, x - zero)
        if e >= s:
            runs.append((s, e))
        x = e + 1
    return runs


def _band_slots(bits: bytes, w: int, h: int, x0: int, y0: int,
                base: list, vdelta: list) -> list:
    """任务栏通栏内的图标槽位（单行）：列分段 → 每段的 y 范围 → 编号（1 起）。

    每个槽位给出屏幕物理坐标中心 (x,y)、尺寸 (w,h)、外接矩形 box、running
    （图标下方带「已启动指示条」= 该应用已在运行，点它等于切到它的窗口）。

    中心 y 用该段前景像素的**中位 y**，不用外框中心：外框会把下方那条指示条算进去，
    外框中心因此比图标本体低 3~5px（在大图标上足以把落点压到图标边缘）。
    """
    mask, act = _band_fg(bits, w, h, base, vdelta)
    slots = []
    for s, e in _band_runs(act, w):
        sw = e - s + 1
        if sw < TASKBAR_SLOT_MIN_W:
            continue
        ys = []
        for y in range(h):
            row = y * w
            for x in range(s, e + 1):
                if mask[row + x]:
                    ys.append(y)
                    break
        if not ys:
            continue
        sh = ys[-1] - ys[0] + 1
        if sh < TASKBAR_SLOT_MIN_H:
            continue
        # 「已启动」指示条：只在最底几行找，且它必须明显比图标本体窄（否则那是图标本身）
        bar = []
        for y in range(max(0, h - TASKBAR_IND_ROWS), h):
            row = y * w
            for x in range(s, e + 1):
                if mask[row + x]:
                    bar.append(x)
        running = False
        if len(bar) >= TASKBAR_IND_MIN_PX:
            bspan = max(bar) - min(bar) + 1
            running = (TASKBAR_IND_MIN_W <= bspan <= min(TASKBAR_IND_MAX_W, max(6, sw - 2)))
        slots.append({
            "x": x0 + (s + e + 1) // 2,
            "y": y0 + ys[len(ys) // 2],
            "w": sw,
            "h": sh,
            "box": (x0 + s, y0 + ys[0], x0 + e, y0 + ys[-1]),
            "running": running,
        })
    slots = _sort_slots(slots)
    for i, s in enumerate(slots, 1):
        s["i"] = i
    return slots


def _merge_parts(parts: list, w: int, h: int) -> list:
    """把碎片按 x 邻近合并成**图标槽位**（图标被高光/抗锯齿切成几块是常态）。

    合并规则：按左边缘排序后贪心扫描，与当前槽位的水平间距 ≤ TASKBAR_MERGE_GAP 就并
    进来；但并进来会超过 TASKBAR_SLOT_MAX_W 时**不并** —— 宁可把它当成两个候选（让模型
    在图上认），也绝不把相邻的两个任务栏按钮并成一个（那样落点会落在两个图标之间的缝上，
    是最典型的「点了没反应」）。
    触到区域边界的碎片一律淘汰：真实尺寸未知（被截断），中心不可信。
    """
    keep = []
    for c in parts:
        bw = c["maxx"] - c["minx"] + 1
        bh = c["maxy"] - c["miny"] + 1
        if c["area"] < TASKBAR_PART_MIN_AREA or bw < TASKBAR_PART_MIN_SPAN \
                or bh < TASKBAR_PART_MIN_SPAN:
            continue
        if c["minx"] <= 0 or c["miny"] <= 0 or c["maxx"] >= w - 1 or c["maxy"] >= h - 1:
            continue
        keep.append(c)
    keep.sort(key=lambda c: c["minx"])
    merged = []
    for c in keep:
        if merged:
            s = merged[-1]
            gap = c["minx"] - s["maxx"] - 1
            if gap <= TASKBAR_MERGE_GAP and (c["maxx"] - s["minx"] + 1) <= TASKBAR_SLOT_MAX_W:
                s["minx"] = min(s["minx"], c["minx"])
                s["maxx"] = max(s["maxx"], c["maxx"])
                s["miny"] = min(s["miny"], c["miny"])
                s["maxy"] = max(s["maxy"], c["maxy"])
                s["area"] += c["area"]
                continue
        merged.append({"minx": c["minx"], "maxx": c["maxx"],
                       "miny": c["miny"], "maxy": c["maxy"], "area": c["area"]})
    out = []
    for s in merged:
        if (s["maxx"] - s["minx"] + 1) < TASKBAR_SLOT_MIN_W \
                or (s["maxy"] - s["miny"] + 1) < TASKBAR_SLOT_MIN_H:
            continue
        out.append(s)
    return out


def _sort_slots(slots: list, grid: bool = False) -> list:
    """槽位排序：任务栏是**一行**，按 x 从左到右即可；面板是**网格**，先按行再按列。

    面板（隐藏图标/后台应用面板）里图标是几行几列排的，只按 x 排会把不同行的图标
    交错编号，模型看着编号图就找不到对应关系了。
    """
    if not grid:
        return sorted(slots, key=lambda s: s["x"])
    rows = []                       # [(行中心 y, [槽位...])]
    for s in sorted(slots, key=lambda s: s["y"]):
        placed = False
        for row in rows:
            if abs(s["y"] - row[0]) <= max(10, s["h"] // 2):
                row[1].append(s)
                placed = True
                break
        if not placed:
            rows.append([s["y"], [s]])
    rows.sort(key=lambda r: r[0])
    out = []
    for _cy, group in rows:
        out.extend(sorted(group, key=lambda s: s["x"]))
    return out


def _taskbar_slots(bits: bytes, w: int, h: int, x0: int, y0: int,
                   row_bgs: list, parts=None, grid: bool = False) -> list:
    """通栏/面板内的**图标槽位**：碎片 → 合并 → 过滤 → 编号（1 起）。

    每个槽位给出屏幕物理坐标中心 (x,y)、尺寸 (w,h)、外接矩形 box，以及 running
    （图标下方带「已启动指示条」= 该应用已在运行，点它等于切到它的窗口）。
    """
    parts = _taskbar_parts(bits, w, h, row_bgs) if parts is None else parts
    merged = _merge_parts(parts, w, h)
    indicators = [c for c in parts
                  if (c["maxy"] - c["miny"] + 1) <= TASKBAR_IND_MAX_H
                  and TASKBAR_IND_MIN_W <= (c["maxx"] - c["minx"] + 1) <= TASKBAR_IND_MAX_W
                  and c["maxy"] >= h - TASKBAR_IND_BOTTOM]
    slots = []
    for s in merged:
        scx = (s["minx"] + s["maxx"]) // 2
        slots.append({
            "x": x0 + (s["minx"] + s["maxx"] + 1) // 2,
            "y": y0 + (s["miny"] + s["maxy"] + 1) // 2,
            "w": s["maxx"] - s["minx"] + 1,
            "h": s["maxy"] - s["miny"] + 1,
            "box": (x0 + s["minx"], y0 + s["miny"], x0 + s["maxx"], y0 + s["maxy"]),
            "running": any(abs((i["minx"] + i["maxx"]) // 2 - scx) <= 14 for i in indicators),
        })
    slots = _sort_slots(slots, grid=grid)
    for i, s in enumerate(slots, 1):
        s["i"] = i
    return slots


def _taskbar_zone(cx: int, gx: int, gw: int) -> str:
    """槽位落在任务栏的哪个区域（纯几何分区，便于模型对号入座）。"""
    rel = cx - gx
    if rel <= TASKBAR_LEFT_ZONE:
        return "最左区（开始菜单/小组件/搜索）"
    if rel >= gw - TASKBAR_RIGHT_ZONE:
        return "右侧托盘区（输入法/音量/网络/时钟/↑隐藏图标）"
    return "应用区"


def _taskbar_scan(strip_h: int = 0) -> dict:
    """抓屏幕底部条带 → 找任务栏通栏 → 切图标槽位。返回 dict（失败时带 reason）。"""
    vx, vy, vw, vh = _virtual_screen()
    sh = max(TASKBAR_MIN_H + 8, min(int(strip_h or TASKBAR_STRIP_H), vh))
    bits, gx, gy, gw, gh = _grab_rect_bits(vx, vy + vh - sh, vw, sh)
    if not bits:
        return {"ok": False, "reason": "capture",
                "msg": "抓取屏幕底部失败（截图不可用）"}
    band = _detect_taskbar_band(bits, gw, gh)
    if band is None:
        return {"ok": False, "reason": "no_band", "gx": gx, "gy": gy, "gw": gw, "gh": gh}
    top, bottom, bg, row_bgs = band
    bh = bottom - top + 1
    band_bits = bits[top * gw * 4:(bottom + 1) * gw * 4]
    band_row_bgs = row_bgs[top:bottom + 1]
    base = _band_base_cols(band_bits, gw, bh)
    vdelta = _band_vdelta(band_row_bgs)
    slots = _band_slots(band_bits, gw, bh, gx, gy + top, base, vdelta)
    return {"ok": True, "gx": gx, "gy": gy, "gw": gw, "gh": gh,
            "top": top, "bottom": bottom, "bg": bg, "row_bgs": band_row_bgs,
            "band_bits": band_bits, "base": base, "vdelta": vdelta, "slots": slots}


def _taskbar_click(x: int, y: int, what: str, wait_ms: int = 250) -> str:
    """点任务栏槽位中心并自证：回读落点 + 比对点击前后的前台窗口。

    为什么要点完立刻比前台窗口：任务栏图标点击的**唯一目的**就是让某个应用到前台。
    不比对的话，「点了没反应」（图标是未启动的固定项、或落点偏了）会被当成成功，
    模型就会带着错误前提继续往下走 —— 这正是「疯狂找位置」的开始。
    """
    before = foreground_window()
    if not do_click(int(x), int(y), "left", 1, mode="mouse"):
        return (f"⚠ **没有完成点击**{what}：光标无法稳定停在 ({x},{y})（当前在 {_cursor_pos()}）。"
                f"通常是**用户正在用真实鼠标**，注入被实时覆盖 —— 硬点会落到别处。"
                f"请先请用户停手，再重试；**不要对着同一坐标反复点**。")
    ax, ay = _cursor_pos()
    land = (f"落点回读：({ax},{ay})，与请求完全一致（注入链路 0 偏差）"
            if (ax, ay) == (int(x), int(y)) else
            f"⚠ 落点回读：光标实际在 ({ax},{ay})，与请求的 ({x},{y}) 相差 "
            f"({ax - int(x):+d},{ay - int(y):+d})px")
    time.sleep(max(0.05, min(2.0, wait_ms / 1000.0)))
    after = foreground_window()
    if after and after != before:
        return (f"已点击{what}（{x},{y}）。{land}。前台窗口已切换："
                f"「{window_title(before) or '（无）'}」→「{window_title(after) or after}」"
                f"—— 应用已到前台，可以继续操作（先 screen_shot 建立坐标基准）。")
    who = window_title(after) or after or "（无）"
    return (f"已点击{what}（{x},{y}）。{land}。⚠ 前台窗口**没有变化**（仍是「{who}」）："
            f"可能 ① 这是个**未启动**的固定图标（点击会启动它，等一两秒再 screen_shot 看）；"
            f"② 它本来就是前台、或只是把同一应用的另一个窗口提到前台；"
            f"③ 落点没落在图标上（对照编号图核对一下）。**不要盲目重复点击同一坐标**。")


def _taskbar_no_band_msg(scan: dict) -> str:
    """没找到任务栏通栏时的说明（如实告知 + 给出可行的下一步）。"""
    gw = scan.get("gw") or 0
    return (
        "没在屏幕底部找到任务栏那条通栏（底部这一带不是「通栏纯色带」）。常见原因：\n"
        "  · 任务栏设成了**自动隐藏** —— 把鼠标移到屏幕最下沿停一下它就会浮出来，"
        "之后重新调用本工具；\n"
        "  · 当前有**全屏窗口**盖住了任务栏（游戏/播放器/全屏演示）；\n"
        "  · 任务栏被挪到了屏幕的上边或侧边（本工具只认底部通栏）。\n"
        "下一步：先 screen_shot 看一眼屏幕底部到底有什么；若图标确实不在任务栏上，"
        "直接用 open_app(name=\"应用名\") 启动/切换（它会自己找窗口并置顶）。"
        + (f"\n（已抓取的底部区域为 {gw}px 宽，里面没有找到连续通栏。）" if gw else ""))


def _taskbar_slot_lines(slots: list, gx: int, gw: int, pick: int = 0,
                        show_zone: bool = True, running_note: bool = True) -> list:
    """把槽位表渲染成给模型看的文字（含可直接照抄的 click 调用）。"""
    if not slots:
        return ["（没识别到任何图标槽位 —— 这一带可能只有背景，或者任务栏是空的。）"]
    lines = []
    for s in slots:
        sel = "   ← 本次点击" if s["i"] == pick else ""
        zone = f"  {_taskbar_zone(s['x'], gx, gw)}" if show_zone else ""
        run = "  [已启动：图标下方有指示条]" if (running_note and s.get("running")) else ""
        lines.append(f"  {s['i']}. 中心({s['x']},{s['y']})  尺寸 {s['w']}×{s['h']}"
                     f"{zone}{run}{sel}")
    return lines


def _taskbar_boxes(slots: list, pick: int = 0) -> list:
    """槽位 → 编号方框（青色=候选、洋红=本次点击的那个）。"""
    boxes = []
    for s in slots:
        bx0, by0, bx1, by1 = s["box"]
        boxes.append((bx0, by0, bx1, by1,
                      0x00FF00FF if s["i"] == pick else 0x00FFFF00, str(s["i"])))
    return boxes


def _taskbar_strip_png(gx: int, sy: int, gw: int, sh: int, tw: int, boxes: list,
                       marks=None) -> tuple:
    """把条带区域出成一张（可缩放的）图，并把编号方框画上去。"""
    dc = user32.GetDC(0)
    try:
        return _capture_scaled(dc, gx, sy, gw, sh, tw, 0, None, smooth=True,
                               marks=marks, boxes=boxes)
    finally:
        user32.ReleaseDC(0, dc)


def _app_state_hint(name: str) -> str:
    """按应用名判断它当前处于什么状态，给出一句话行动建议。

    目的很直接：**别让模型为了「切到某个应用」反复调 open_app / manage_window 置顶**。
    已经在前台就直说「不用再切」，在后台就说「点任务栏图标一步到位」。
    """
    if not name:
        return ""
    hwnd, title, how = _find_app_window(_app_keys(name))
    if not hwnd:
        return (f"「{name}」当前**没有运行中的窗口** —— 任务栏上它的图标是未启动的固定图标，"
                f"点下去会启动（要等 1~2 秒再截图）；也可以直接用 open_app(name=\"{name}\")。")
    label = f"窗口 [{hwnd}] {title or '（无标题）'}"
    if foreground_window() == hwnd:
        return (f"「{name}」**已经在最前**（{label}）—— 不要再调 open_app / manage_window "
                f"去置顶，直接 screen_shot 开始操作即可。")
    if window_state(hwnd).get("minimized"):
        return (f"「{name}」**正在运行但已最小化**（{label}）—— 点它的任务栏图标就能一步恢复，"
                f"不要用 open_app / manage_window 反复置顶。")
    return (f"「{name}」**正在运行但在后台**（{label}）—— 直接点它的任务栏图标切过去即可，"
            f"不要反复调 open_app / manage_window 置顶。")


def _taskbar_band_flow(args: dict) -> list:
    """任务栏本体：扫描 → 编号 → （可选）按编号点击。"""
    index = _num(args.get("index"), 0)
    name = str(args.get("name") or "").strip()
    scan = _taskbar_scan()
    if not scan.get("ok"):
        if scan.get("reason") == "no_band":
            return [{"type": "text", "text": _taskbar_no_band_msg(scan)}]
        return [{"type": "text", "text": scan.get("msg") or "抓取屏幕底部失败。"}]
    gx, gy, gw, gh = scan["gx"], scan["gy"], scan["gw"], scan["gh"]
    top, bottom = scan["top"], scan["bottom"]
    slots = scan["slots"]
    band_y0 = gy + top
    band_h = bottom - top + 1
    target = next((s for s in slots if s["i"] == index), None) if index else None
    if index and target is None:
        return [{"type": "text",
                 "text": f"任务栏里只识别到 {len(slots)} 个图标槽位，没有编号 {index}。"
                         f"请用下面列出的编号重试（不填 index 就是只扫描）。\n"
                         + "\n".join(_taskbar_slot_lines(slots, gx, gw))}]

    head = (f"任务栏（屏幕物理坐标）：检测到通栏 y {band_y0}~{band_y0 + band_h - 1}"
            f"（高 {band_h}px），屏幕底部 {gw}px 宽。"
            + (f"你要找的是「{name}」——" if name else "")
            + f"共识别出 {len(slots)} 个图标槽位（下图已用青色方框编号）：")
    lines = [head] + _taskbar_slot_lines(slots, gx, gw, pick=index)
    hint = _app_state_hint(name)
    if hint:
        lines.append("")
        lines.append("状态：" + hint)
    lines.append("")
    lines.append("怎么用：")
    lines.append("  ① 在图上认出你要的那个图标 → "
                 "click(x=它的中心x, y=它的中心y, space=\"screen\", snap=false)")
    lines.append("  ② 或一步到位：taskbar_open(index=N) —— 直接点第 N 个，"
                 "并回读落点、校验前台窗口是否真的切过去了")
    lines.append("说明：带「已启动」指示条的图标 = 该应用**正在运行**，点它等于"
                 "切到它的窗口（不会重开一个）；没有指示条的多是未启动的固定图标，"
                 "点它会启动该应用（要等一两秒再截图确认）。")
    lines.append("注意：右侧托盘区（输入法/音量/网络/时钟）和最左区（开始菜单/小组件）"
                 "也在编号里 —— 别点错区域。")
    lines.append("下一步：确认图标不在任务栏上时，用 taskbar_open(where=\"panel\") 展开"
                 "右侧「↑」面板找（后台运行的图标常藏在那里）；"
                 "只有确认应用**根本没在运行**才用 open_app(name=\"…\")。")

    click_note = ""
    if target is not None:
        reason = _guard("click")
        if reason:
            return [{"type": "text", "text": reason}]
        click_note = _taskbar_click(target["x"], target["y"],
                                    f"任务栏第 {target['i']} 个槽位", 
                                    _num(args.get("timeout"), 250))
        _log("ai", f"任务栏点击 #{target['i']} ({target['x']},{target['y']})")
    _refresh_env()
    ctx_top = TASKBAR_CTX_TOP if top >= TASKBAR_CTX_TOP else 0
    sy = gy + top - ctx_top
    sh = band_h + ctx_top
    tw = max(640, min(TASKBAR_IMG_MAX_W, gw))
    png, iw, ih = _taskbar_strip_png(gx, sy, gw, sh, tw, _taskbar_boxes(slots, index))
    _set_view(gx, sy, gw, sh, iw, ih, "full")
    text = "\n".join(lines)
    if click_note:
        text = text + "\n\n" + click_note
    text += (f"\n（本图 {iw}×{ih}，覆盖屏幕区域 ({gx},{sy}) {gw}×{sh}；"
             f"{_transform_line()}）")
    return [{"type": "text", "text": text},
            {"type": "image", "data": base64.b64encode(png).decode(), "mimeType": "image/png"}]


def _change_bbox(a: bytes, b: bytes, w: int, h: int, tol: int = 24):
    """两份同尺寸快照的**变化外接矩形**（用于把「刚弹出来的面板」框出来）。"""
    if not a or not b or len(a) != len(b):
        return None, 0
    minx, miny, maxx, maxy = w, h, -1, -1
    changed = 0
    for y in range(h):
        base = y * w
        for x in range(w):
            o = (base + x) * 4
            if (abs(a[o] - b[o]) + abs(a[o + 1] - b[o + 1])
                    + abs(a[o + 2] - b[o + 2])) > tol:
                changed += 1
                if x < minx:
                    minx = x
                if x > maxx:
                    maxx = x
                if y < miny:
                    miny = y
                if y > maxy:
                    maxy = y
    if maxx < 0:
        return None, 0
    return (minx, miny, maxx, maxy), changed


def _panel_row_bgs(bits: bytes, w: int, h: int, fallback) -> list:
    """面板裁剪图的逐行背景色：行内主色覆盖率够就用它，否则退回整块主色。

    面板边缘会带上桌面内容/阴影，那些行的「众数色」不是面板背景；用覆盖率兜住。
    """
    out = []
    for y in range(h):
        col, cov = _row_modal(bits, w, y)
        out.append(col if (col is not None and cov >= 0.40) else fallback)
    return out


def _taskbar_panel_flow(args: dict) -> list:
    """右侧「↑」面板（隐藏图标 / 后台运行的应用）：先展开，再把面板里的图标编号。"""
    index = _num(args.get("index"), 0)
    chev = _num(args.get("chevron"), 0)
    name = str(args.get("name") or "").strip()
    scan = _taskbar_scan()
    if not scan.get("ok"):
        if scan.get("reason") == "no_band":
            return [{"type": "text", "text": _taskbar_no_band_msg(scan)}]
        return [{"type": "text", "text": scan.get("msg") or "抓取屏幕底部失败。"}]
    gx, gy, gw = scan["gx"], scan["gy"], scan["gw"]
    top, bottom = scan["top"], scan["bottom"]
    band_y0 = gy + top
    band_h = bottom - top + 1
    slots = scan["slots"]

    # 「↑ 显示隐藏图标」的定位：托盘区里**最左的那个小图标**（实测它就在输入法/时钟左边）
    if chev:
        target = next((s for s in slots if s["i"] == chev), None)
        how = f"按你指定的 #{chev}"
    else:
        right = [s for s in slots
                 if (s["x"] - gx) >= gw - TASKBAR_RIGHT_ZONE and s["w"] <= TASKBAR_CHEVRON_MAX_W]
        target = right[0] if right else None
        how = "自动认作「↑ 显示隐藏图标」（右侧托盘区里最左的那个小图标）"
    if target is not None:
        cx, cy = target["x"], target["y"]
    else:
        cx = gx + gw - 120
        cy = band_y0 + band_h // 2
        how = ("没能在托盘区认出「↑」（任务栏布局特殊），按常见位置 x=屏宽-120 试一下")

    reason = _guard("click")
    if reason:
        return [{"type": "text", "text": reason}]
    pw = max(160, min(TASKBAR_PANEL_W, gw))
    # 面板高度要按**虚拟桌面顶部**算，不能按抓取条带的高度算：
    # 条带只有 200px（TASKBAR_STRIP_H），而「↑」面板有 300~500px 高，
    # 按条带高度截会只看到面板最下面一小条，变化量判定直接失败。
    _vx, vy_top, _vw2, _vh2 = _virtual_screen()
    ph = max(160, min(TASKBAR_PANEL_H, band_y0 - vy_top))
    px0 = max(gx, min(gx + gw - pw, cx - pw // 2))
    py0 = max(vy_top, band_y0 - ph - 6)
    before, bx, by, bw2, bh2 = _grab_rect_bits(px0, py0, pw, ph)
    if not before:
        return [{"type": "text", "text": "抓取任务栏上方区域失败，无法展开面板。"}]
    click_note = _taskbar_click(cx, cy, f"「↑ 隐藏图标」按钮（{how}）",
                                _num(args.get("timeout"), 450))
    after, _x, _y, _w, _h = _grab_rect_bits(px0, py0, pw, ph)
    bb, changed = _change_bbox(before, after, bw2, bh2)
    total = bw2 * bh2
    if bb is None or changed < TASKBAR_PANEL_CHANGE_MIN * total:
        _refresh_env()
        return [{"type": "text",
                 "text": (f"点完「↑」后，任务栏上方这一片**没有弹出任何面板**"
                          f"（{bw2}×{bh2} 区域里只变了 {changed} 个像素）。\n{click_note}\n"
                          f"说明刚才那一下没点在「↑」上（或本机没有隐藏图标）。请：\n"
                          f"  · 先 screen_shot 看任务栏右侧到底有没有「↑」，"
                          f"再用 taskbar_open(where=\"taskbar\") 拿到编号，"
                          f"用 taskbar_open(where=\"panel\", chevron=N) 指定它；\n"
                          f"  · 图标若本来就在任务栏上，直接 taskbar_open(index=N) 点它最快；\n"
                          f"  · 都没有就用 open_app(name=\"…\")。")}]

    # 只对「变化出来的那一块」做图标分割：面板的圆角/阴影不会混进来
    cx0, cy0, cx1, cy1 = bb
    cw, ch = cx1 - cx0 + 1, cy1 - cy0 + 1
    crop = bytearray()
    for y in range(cy0, cy1 + 1):
        o = (y * bw2 + cx0) * 4
        crop += after[o:o + cw * 4]
    fallback = _area_modal(crop, cw, ch)
    if fallback is None:
        return [{"type": "text", "text": "面板区域取不到主色，无法识别图标。"}]
    row_bgs = _panel_row_bgs(crop, cw, ch, fallback)
    ox, oy = px0 + cx0, py0 + cy0
    parts = _taskbar_parts(crop, cw, ch, row_bgs)
    pslots = _taskbar_slots(crop, cw, ch, ox, oy, row_bgs, parts, grid=True)
    target2 = next((s for s in pslots if s["i"] == index), None) if index else None
    if index and target2 is None:
        return [{"type": "text",
                 "text": f"面板里只识别到 {len(pslots)} 个图标，没有编号 {index}。"
                         f"请用下列编号重试。\n" + "\n".join(_taskbar_slot_lines(pslots, ox, cw))}]

    shown = pslots[:TASKBAR_PANEL_MAX_SLOTS]
    lines = [f"「↑」面板已展开（{how}）：面板区域在屏幕上 ({ox},{oy}) {cw}×{ch}，"
             f"共识别出 {len(pslots)} 个图标（下图已编号；青色=候选、洋红=本次点击）"
             + (f"。你要找的是「{name}」——在图上认出它的编号。" if name else "。")]
    lines += _taskbar_slot_lines(shown, ox, cw, pick=index, show_zone=False,
                                 running_note=False)
    hint = _app_state_hint(name)
    if hint:
        lines.append("")
        lines.append("状态：" + hint)
    if len(pslots) > len(shown):
        lines.append(f"  （另有 {len(pslots) - len(shown)} 个未列出，"
                     f"可用 zoom_in 放大面板区域后按坐标点。）")
    lines.append("")
    lines.append("怎么用：")
    lines.append("  ① 认出编号后一步点掉：taskbar_open(where=\"panel\", index=N) "
                 "（会重新展开面板并点第 N 个）")
    lines.append("  ② 或直接点：click(x=中心x, y=中心y, space=\"screen\", snap=false) "
                 "—— 点完面板会自动收起、应用会到前台")
    lines.append("说明：面板里既有「隐藏的托盘图标」，也有**后台运行的应用**"
                 "（Windows 会把它们列在这里）—— 想切回某个后台程序，这里往往比任务栏更全。")

    click_note2 = ""
    if target2 is not None:
        click_note2 = _taskbar_click(target2["x"], target2["y"],
                                     f"面板第 {target2['i']} 个图标",
                                     _num(args.get("timeout"), 250))
        _log("ai", f"面板点击 #{target2['i']} ({target2['x']},{target2['y']})")
    _refresh_env()
    tw = max(320, min(720, cw))
    png, iw, ih = _taskbar_strip_png(ox, oy, cw, ch, tw, _taskbar_boxes(shown, index))
    _set_view(ox, oy, cw, ch, iw, ih, "full")
    text = "\n".join(lines)
    if click_note2:
        text = text + "\n\n" + click_note2
    text += (f"\n（本图 {iw}×{ih}，覆盖屏幕区域 ({ox},{oy}) {cw}×{ch}；"
             f"{_transform_line()}）")
    return [{"type": "text", "text": text},
            {"type": "image", "data": base64.b64encode(png).decode(), "mimeType": "image/png"}]


def tool_taskbar_open(args: dict) -> list:
    """从任务栏打开/切换应用：扫描 → 编号 → （可选）点击。

    用户说「打开微信 / 切到微信 / 微信在后台」时的**第一选择**：先 screen_shot 看一眼
    任务栏上有没有那个图标，有就 taskbar_open(index=N) 一步切过去 —— 应用已经在跑时，
    点任务栏图标就是「切到它的窗口」，比重新启动一个实例快得多，也不会开出第二个窗口。

    图标不在任务栏可见区时用 where="panel"：工具会先点右侧的「↑」把隐藏图标/后台应用
    面板展开，再把面板里的图标编号列出，然后同样可以按编号点击。

    where="taskbar"（默认）时的返回值：任务栏通栏位置 + 每个槽位的编号/中心/尺寸/区域
    + 编号图；带 index 时额外给出落点回读与**前台窗口是否真的切换了**。
    全程不读控件树、不返回任何控件名 —— 编号图让模型「认图标」，坐标由几何算法量出来。
    """
    where = str(args.get("where") or "taskbar").strip().lower()
    if where in ("panel", "overflow", "hidden", "background", "后台", "隐藏", "面板"):
        return _taskbar_panel_flow(args)
    return _taskbar_band_flow(args)


def grab_zoom(cx: int, cy: int, size: int, factor: int, grid: bool = False,
              cursor: bool = False, smooth: bool = False, marks=None,
              boxes=None) -> tuple:
    """以物理坐标 (cx,cy) 为中心抓取 size×size 并放大 factor 倍。

    默认纯净放大（不画网格/刻度/准星）：放大查看是为了看清像素级细节，任何叠加物
    都可能盖住目标。需要人工对照时再显式开 grid/cursor/marks/boxes。

    默认 smooth=False：放大走最近邻，1px 边框放大后仍是硬实方块 —— 模型据此读出的
    坐标与真实像素一一对应，不会因为插值糊边而差 1~2 个像素。

    边界用**虚拟桌面**而非主屏：副屏在主屏左侧/上方时主屏矩形会让夹取算错，
    放大区域会整体偏移到错误的屏幕上。
    """
    vx, vy, vw, vh = _virtual_screen()
    size = max(60, min(int(size), min(vw, vh)))
    factor = max(1, min(int(factor), 8))
    x0 = max(vx, min(vx + vw - size, int(round(cx - size / 2))))
    y0 = max(vy, min(vy + vh - size, int(round(cy - size / 2))))
    tw = min(size * factor, ZOOM_MAX_PX)
    dc = user32.GetDC(0)
    try:
        png, iw, ih = _capture_scaled(dc, x0, y0, size, size, tw,
                                      GRID_CELLS if grid else 0,
                                      (cx, cy) if cursor else None,
                                      smooth=smooth, marks=marks, boxes=boxes)
        return png, iw, ih, x0, y0, size
    finally:
        user32.ReleaseDC(0, dc)


# ============================================================================
# 输入注入：鼠标 / 键盘（SendInput，带签名）+ 触控（InjectTouchInput，优先）
# ============================================================================
_inject_lock = threading.Lock()
_inject_depth = 0
_inject_ts = 0.0
# 触控能力状态：unknown（未验证）→ ok（注入成功过）/ broken（注入失败，已回退鼠标）
# 为什么不能只看初始化：InitializeTouchInjection 成功并不代表 InjectTouchInput 可用 ——
# 无触控数字化仪 / TabletInputService 未运行的环境下初始化返回成功，但真实注入返回
# 「参数无效(87)」。只凭初始化判定「可用」会让点击走触控分支后静默失效（实测踩过：
# 点击没落到目标窗口，后续输入也跟着落空）。故以**首次真实注入的返回值**为最终判据。
_touch = {"state": "unknown"}
_last_touch_err = 0


def _injecting() -> bool:
    """是否处于 AI 注入窗口（含短暂余波）：钩子据此忽略自身事件，不误判为真人输入。"""
    with _inject_lock:
        return _inject_depth > 0 or (time.time() - _inject_ts) < INJECT_GRACE_S


class _Injecting:
    """with 包裹一次注入动作：期间（及之后 GRACE 秒）抑制打断检测。"""

    def __enter__(self):
        global _inject_depth
        with _inject_lock:
            _inject_depth += 1
        return self

    def __exit__(self, *exc):
        global _inject_depth, _inject_ts
        with _inject_lock:
            _inject_depth = max(0, _inject_depth - 1)
            _inject_ts = time.time()
        return False


def _send_mouse(flags: int, dx: int = 0, dy: int = 0, data: int = 0):
    inp = INPUT()
    inp.type = INPUT_MOUSE
    inp.u.mi = MOUSEINPUT(dx, dy, data, flags, 0, INJECT_SIGNATURE)
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def _send_key(vk: int, up: bool = False, scan: int = 0, unicode: bool = False):
    flags = (KEYEVENTF_KEYUP if up else 0) | (KEYEVENTF_UNICODE if unicode else 0)
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.u.ki = KEYBDINPUT(0 if unicode else vk, scan, flags, 0, INJECT_SIGNATURE)
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def _norm_abs(v: int, origin: int, span: int) -> int:
    """像素坐标 → SendInput 绝对坐标的 0..65535 归一化值（**精确逆运算**）。

    Windows 把归一化值换算回像素用的是 `px = floor(nx * span / 65536)`（不是
    `nx * (span-1) / 65535`）。本机 1920 宽屏实测 80 个点验证过这个模型：
    用 `round(v * 65535 / (span-1))` 求逆时取整方向与它不一致，会在**个别像素**
    上系统性差 1px（1920 宽屏上有 7 个 x：1,2,3,8,9,16,23 —— 实测全部偏 1px）。
    改用 `ceil(v * 65536 / span)` 即它的精确逆：可证明对 0..span-1 全部像素命中，
    实测 17 个点（含上述 7 个）误差全部为 0。

    用整数 ceil 除法而不是 math.ceil：`-(-a // b)` 对正负号都正确（副屏在主屏
    左侧时差值为负），且不受浮点误差影响。
    """
    span = max(1, int(span))
    n = -(-(int(v) - int(origin)) * 65536 // span)
    return max(0, min(65535, n))


def _move_absolute(x: int, y: int):
    """鼠标绝对移动（落点为**精确像素**，见 _norm_abs 的验证说明）。

    精度要点（两处都踩过坑）：
    1. 归一化基准必须是**虚拟桌面**（多显示器并集），且必须同时带
       MOUSEEVENTF_VIRTUALDESK —— 否则 0..65535 只映射主屏，副屏落点全错；
    2. 归一化公式必须与 Windows 的换算方向互为逆运算，否则个别像素差 1px。
    """
    vx, vy, vw, vh = _virtual_screen()
    _send_mouse(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
                _norm_abs(x, vx, vw), _norm_abs(y, vy, vh))


def _move_human(x: int, y: int, duration: float = 0.0):
    """真人式平滑移动：分步绝对移动（带缓出与轻微弧度），全部走 SendInput 以携带签名。"""
    x1, y1 = _cursor_pos()
    dx, dy = int(x) - x1, int(y) - y1
    dist = (dx * dx + dy * dy) ** 0.5
    if dist < 3:
        with _Injecting():
            _move_absolute(x, y)
        return
    dur = duration or min(0.18 + dist / 2200.0, 0.42)
    steps = max(int(dur / MOVE_STEP_S), 10)
    arc = min(dist * 0.035, 12.0) if dist >= 90 else 0.0
    px, py = (-dy / dist if dist else 0.0), (dx / dist if dist else 0.0)
    with _Injecting():
        for i in range(1, steps + 1):
            t = i / steps
            ease = 1 - (1 - t) ** 2
            bend = math.sin(math.pi * t) * arc   # 轻微弧度：像真人手移，不做瞬移
            _move_absolute(int(x1 + dx * ease + px * bend), int(y1 + dy * ease + py * bend))
            time.sleep(dur / steps)
        _move_absolute(x, y)   # 精确落点


def touch_ready() -> bool:
    """触控注入是否可用（预期值）。

    首次调用按 SM_DIGITIZER + InitializeTouchInjection 判定；一旦真实注入失败即转为
    broken（本进程内不再尝试，全部走鼠标回退）。最终判据是首次实际注入的返回值，
    见 _touch_tap / _touch_long_press / _touch_drag / _touch_swipe。
    """
    if _touch["state"] != "unknown":
        return _touch["state"] == "ok"
    _touch["state"] = "broken"
    try:
        digitizer = int(user32.GetSystemMetrics(SM_DIGITIZER))
        has_touch = bool(digitizer & (NID_INTEGRATED_TOUCH | NID_EXTERNAL_TOUCH))
        if has_touch and (digitizer & NID_READY) and \
                user32.InitializeTouchInjection(4, TOUCH_FEEDBACK_DEFAULT):
            _touch["state"] = "ok"
    except Exception:
        _touch["state"] = "broken"
    if _touch["state"] == "broken":
        _log("sys", "本机不支持触控注入（无触控数字化仪或相关服务未运行），操控使用鼠标模拟")
    return _touch["state"] == "ok"


def touch_capability() -> str:
    """触控能力描述（供工具文案如实汇报：触控 / 触控已回退鼠标 / 无触控设备）"""
    if _touch["state"] == "ok":
        return "触控"
    if _touch["state"] == "broken" and _last_touch_err:
        return f"鼠标（触控注入不可用：错误码 {_last_touch_err}）"
    return "鼠标"


def _touch_contact(x: int, y: int, flags: int) -> POINTER_TOUCH_INFO:
    info = POINTER_TOUCH_INFO()
    info.pointerInfo.pointerType = PT_TOUCH
    info.pointerInfo.pointerId = 1
    info.pointerInfo.pointerFlags = flags
    info.pointerInfo.ptPixelLocation = wintypes.POINT(int(x), int(y))
    info.pointerInfo.ptPixelLocationRaw = wintypes.POINT(int(x), int(y))
    info.touchMask = TOUCH_MASK_CONTACTAREA
    info.rcContact = wintypes.RECT(int(x) - 3, int(y) - 3, int(x) + 3, int(y) + 3)
    return info


def _inject_touch(x: int, y: int, flags: int) -> bool:
    """注入一次触控接触，返回是否成功（失败记错误码，供回退判定与如实提示）。"""
    global _last_touch_err
    info = _touch_contact(x, y, flags)
    if user32.InjectTouchInput(1, ctypes.byref(info)):
        return True
    _last_touch_err = int(ctypes.get_last_error())
    return False


def _touch_failed() -> None:
    """真实触控注入失败 → 判定本机不可用，后续一律走鼠标回退（并如实记录）。"""
    _touch["state"] = "broken"
    _STATE["input"] = "mouse"
    _log("sys", f"触控注入失败（错误码 {_last_touch_err}），已自动回退鼠标模拟")


def _touch_release(x: int, y: int) -> None:
    """抬起触点；失败重试一次，避免留下「粘住」的悬停触点影响后续操作。"""
    if not _inject_touch(x, y, POINTER_FLAG_UP):
        time.sleep(0.02)
        _inject_touch(x, y, POINTER_FLAG_UP)


def _touch_tap(x: int, y: int, clicks: int = 1) -> bool:
    """触控 tap。返回 True = 触控路径已生效；False = **首次**注入即失败（未产生任何输入，
    调用方回退鼠标是安全的）。后续帧失败不再回退，避免已经点过又点一次。"""
    if not touch_ready():
        return False
    with _Injecting():
        for i in range(max(1, clicks)):
            if not _inject_touch(x, y, POINTER_FLAG_DOWN | POINTER_FLAG_INRANGE
                                 | POINTER_FLAG_INCONTACT):
                _touch_failed()
                return i > 0
            time.sleep(0.05)
            _touch_release(x, y)
            if i + 1 < max(1, clicks):
                time.sleep(0.08)
        _move_absolute(x, y)   # 触控不移动鼠标指针；同步指针便于用户看清准星
    return True


def _touch_long_press(x: int, y: int, duration: float) -> bool:
    """触控长按（可呼出上下文菜单）。返回值语义同 _touch_tap。"""
    if not touch_ready():
        return False
    with _Injecting():
        if not _inject_touch(x, y, POINTER_FLAG_DOWN | POINTER_FLAG_INRANGE
                             | POINTER_FLAG_INCONTACT):
            _touch_failed()
            return False
        time.sleep(max(0.2, duration))
        _touch_release(x, y)
        _move_absolute(x, y)
    return True


def _touch_drag(x1: int, y1: int, x2: int, y2: int, duration: float,
                hold: float = 0.15) -> bool:
    """触控拖拽。返回值语义同 _touch_tap。"""
    if not touch_ready():
        return False
    steps = max(int(duration / 0.02), 8)
    with _Injecting():
        if not _inject_touch(x1, y1, POINTER_FLAG_DOWN | POINTER_FLAG_INRANGE
                             | POINTER_FLAG_INCONTACT):
            _touch_failed()
            return False
        time.sleep(hold)
        for i in range(1, steps + 1):
            _inject_touch(int(x1 + (x2 - x1) * i / steps), int(y1 + (y2 - y1) * i / steps),
                          POINTER_FLAG_UPDATE | POINTER_FLAG_INRANGE | POINTER_FLAG_INCONTACT)
            time.sleep(duration / steps)
        _touch_release(x2, y2)
        _move_absolute(x2, y2)
    return True


def _touch_swipe(x1: int, y1: int, x2: int, y2: int, duration: float) -> bool:
    """触控快速滑动（速度→系统惯性滚动）。返回值语义同 _touch_tap。"""
    if not touch_ready():
        return False
    steps = max(int(duration / 0.012), 10)
    with _Injecting():
        if not _inject_touch(x1, y1, POINTER_FLAG_DOWN | POINTER_FLAG_INRANGE
                             | POINTER_FLAG_INCONTACT):
            _touch_failed()
            return False
        for i in range(1, steps + 1):
            t = i / steps
            _inject_touch(int(x1 + (x2 - x1) * t), int(y1 + (y2 - y1) * t),
                          POINTER_FLAG_UPDATE | POINTER_FLAG_INRANGE | POINTER_FLAG_INCONTACT)
            time.sleep(duration / steps)
        _touch_release(x2, y2)
        _move_absolute(x2, y2)
    return True


def _mouse_button(button: str) -> tuple:
    return {"left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
            "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
            "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP)}.get(
        button, (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP))


INPUT_MODES = ("auto", "mouse", "touch")


def _touch_allowed(mode: str) -> bool:
    """本次注入是否走触控。**只有显式要求才走触控。**

    为什么默认不用触控（实测结论，勿凭直觉改回「触控优先」）：
    在原生 OpenGL 窗口上实测（本机 125% DPI、带触控数字化仪），
      * 鼠标注入：落点偏差 **(+0,+0)** —— 像素级精准；
      * 触控注入：落点偏差在 **0 ~ 428 像素之间且不可预测**（同一进程内多次
        tap 的偏差方向与大小都不一致），因为触控要经过数字化仪标定与
        「触控→鼠标」提升两段坐标变换。
    生产级软件（Blender / CAD / 3D / 视频剪辑）的按钮常常只有十几像素宽，
    这种偏差意味着「点了但点错地方」——比点不到更危险。故默认走确定性的鼠标注入；
    确实需要触控语义（触控优先的 UI）时，调用方显式传 input="touch"。
    """
    return str(mode or "auto").strip().lower() == "touch"


def _input_mode(args: dict) -> str:
    """从工具参数里取注入方式（auto/mouse/touch），非法值回落 auto。"""
    m = str((args or {}).get("input") or "auto").strip().lower()
    return m if m in INPUT_MODES else "auto"


def _settle_at(x: int, y: int, tries: int = 3) -> bool:
    """把光标精确落到 (x,y) 并**回读确认**；确认不到就返回 False。

    为什么按键前非要回读一次：SendInput 的绝对定位不是原子的 —— 如果此刻真实鼠标
    （用户的手/触控板/另一个注入源）也在发事件，我们刚定位好的光标会被**立刻顶走**，
    于是「按下」发生在完全不同的位置。实测踩过：请求 (1683,1055)，回读却是 (1231,510)，
    点击打到了别的窗口上 —— 这是坐标算得再准也救不回来的一类「点偏」。

    重试 3 次仍对不上就**不按键**：按在错误位置（可能是删除/发送/关闭）比不按危险得多。
    """
    for _ in range(max(1, int(tries))):
        _move_absolute(x, y)
        if _cursor_pos() == (int(x), int(y)):
            return True
        time.sleep(0.012)
    return False


def do_click(x: int, y: int, button: str = "left", clicks: int = 1, mode: str = "auto"):
    """点击。

    默认走**鼠标注入**（像素级精准，见 _touch_allowed 的实测说明）；只有显式
    mode="touch" 时才尝试真实触控 tap，触控注入失败会自动回退鼠标。

    精度要点：按下与抬起必须在**同一像素**，且移动到目标后才按下 —— 移动途中
    误按会把点击落到沿途的窗口上。落点用整数像素，避免子像素取整漂移。
    每次按下前都用 _settle_at 回读确认落点（被真实鼠标抢走时不按）。

    返回 True=确实点了；False=落点确认不到、**没有按键**（调用方应如实告知模型）。
    """
    if button == "left" and _touch_allowed(mode) and _touch_tap(x, y, clicks):
        time.sleep(SETTLE_AFTER_CLICK_S)
        return True
    down, up = _mouse_button(button)
    _move_human(x, y)
    with _Injecting():
        for i in range(max(1, clicks)):
            # 每一次点击前都精确归位（多次点击时防止首击位移带来的漂移）
            if not _settle_at(x, y):
                _log("sys", f"点击取消：落点 ({x},{y}) 回读不一致（光标被真实鼠标抢走），"
                            f"为避免点错位置已放弃按键")
                return False
            _send_mouse(down)
            time.sleep(0.04)
            _move_absolute(x, y)      # 按住期间不移动，保证 down/up 同点
            _send_mouse(up)
            time.sleep(0.05 if i + 1 < max(1, clicks) else SETTLE_AFTER_CLICK_S)
    return True


def do_long_press(x: int, y: int, duration: float, mode: str = "auto"):
    """长按：默认鼠标按住不动（精准）；显式 mode="touch" 时走触控长按。

    精度要点：整个按住期间**完全不动**指针（只发一次 down），避免被目标控件
    识别为「拖动」而丢失长按语义；时长做上下限保护。
    """
    duration = max(0.2, min(float(duration), LONG_PRESS_MAX_S))
    if _touch_allowed(mode) and _touch_long_press(x, y, duration):
        return True
    _move_human(x, y)
    with _Injecting():
        if not _settle_at(x, y):
            _log("sys", f"长按取消：落点 ({x},{y}) 回读不一致（光标被真实鼠标抢走），已放弃按键")
            return False
        _send_mouse(MOUSEEVENTF_LEFTDOWN)
        # 分段等待而不是一次 sleep：长按时长可变，且能保证指针零漂移
        left = duration
        while left > 0:
            step = min(0.1, left)
            time.sleep(step)
            left -= step
        _move_absolute(x, y)
        _send_mouse(MOUSEEVENTF_LEFTUP)
    return True


def do_drag(x1: int, y1: int, x2: int, y2: int, duration: float, hold: float = 0.15,
            mode: str = "auto"):
    """拖拽：触控可用时触控拖拽，否则鼠标拖拽。

    精度要点（三处最容易导致「拖不动」）：
    1. **按下后先停顿**（hold）：很多控件要等按下被识别后才进入拖拽语义；
       停顿太短会被当成普通点击，文件/滑块根本不跟手。
    2. **分步数下限**：步数太少 = 瞬移，系统会判定为点击而非拖拽；用缓动曲线
       起步（慢→快→慢）更像真人，也让控件有足够采样点。
    3. **落点精确归位**：最后一步显式移动到 (x2,y2)，并停顿让目标完成响应。
    """
    if _touch_allowed(mode) and _touch_drag(x1, y1, x2, y2, duration, hold):
        time.sleep(DRAG_SETTLE_S)
        return True
    steps = max(int(duration / 0.02), DRAG_MIN_STEPS)
    _move_human(x1, y1)
    with _Injecting():
        if not _settle_at(x1, y1):
            _log("sys", f"拖拽取消：起点 ({x1},{y1}) 回读不一致（光标被真实鼠标抢走），已放弃按键")
            return False
        _send_mouse(MOUSEEVENTF_LEFTDOWN)
        time.sleep(hold)                      # 关键：给目标识别「开始拖拽」的时间
        for i in range(1, steps + 1):
            t = i / steps
            ease = t * t * (3 - 2 * t)       # smoothstep：两端慢、中间快
            _move_absolute(int(x1 + (x2 - x1) * ease), int(y1 + (y2 - y1) * ease))
            time.sleep(duration / steps)
        _move_absolute(x2, y2)                # 关键：精确落在目标点
        time.sleep(DRAG_SETTLE_S)
        _send_mouse(MOUSEEVENTF_LEFTUP)
    return True


def do_swipe(x1: int, y1: int, x2: int, y2: int, duration: float, mode: str = "auto"):
    """滑动：显式 mode="touch" 时走触控快速滑动（带速度→系统惯性滚动），
    否则退化为鼠标拖动（默认路径，落点确定）。

    精度要点：滑动末段**逐渐减速**才不会被系统当成「拖到某处再停住」；
    同时给一段收尾停顿，让惯性滚动/翻页真正发生。
    """
    if _touch_allowed(mode) and _touch_swipe(x1, y1, x2, y2, duration):
        time.sleep(DRAG_SETTLE_S)
        return True
    steps = max(int(duration / 0.012), 10)
    _move_human(x1, y1)
    with _Injecting():
        if not _settle_at(x1, y1):
            _log("sys", f"滑动取消：起点 ({x1},{y1}) 回读不一致（光标被真实鼠标抢走），已放弃按键")
            return False
        _send_mouse(MOUSEEVENTF_LEFTDOWN)
        time.sleep(0.02)
        for i in range(1, steps + 1):
            t = i / steps
            decay = 1.0 - (1.0 - t) ** 2      # 起步快、末段减速
            _move_absolute(int(x1 + (x2 - x1) * decay), int(y1 + (y2 - y1) * decay))
            time.sleep(duration / steps)
        _move_absolute(x2, y2)
        time.sleep(DRAG_SETTLE_S)
        _send_mouse(MOUSEEVENTF_LEFTUP)
    return True


def do_scroll(delta: int):
    with _Injecting():
        _send_mouse(MOUSEEVENTF_WHEEL, 0, 0, int(delta) & 0xFFFFFFFF)


def do_scroll_at(x: int, y: int, delta: int):
    """先移动到指定位置再滚动：滚轮事件投递给指针下的容器，
    比停在默认位置滚动命中率高得多（长页面/多滚动区界面尤其明显）。"""
    _move_human(x, y)
    time.sleep(0.05)
    do_scroll(delta)


def do_type_text(text: str, interval: float = 0.012):
    """Unicode 注入文本（支持中文/符号，不受键盘布局影响）；换行转回车。"""
    with _Injecting():
        for ch in text:
            if ch in ("\n", "\r"):
                _send_key(0x0D)
                time.sleep(interval)
                _send_key(0x0D, up=True)
            else:
                for unit in _unicode_units(ch):
                    _send_key(0, scan=unit, unicode=True)
                    _send_key(0, up=True, scan=unit, unicode=True)
            time.sleep(interval)


def _unicode_units(ch: str) -> list:
    """字符 → UTF-16 码元（代理对字符拆成两个码元，SendInput 才能正确输入）"""
    cp = ord(ch)
    if cp <= 0xFFFF:
        return [cp]
    cp -= 0x10000
    return [0xD800 + (cp >> 10), 0xDC00 + (cp & 0x3FF)]


_MODIFIERS = {"ctrl": 0x11, "control": 0x11, "shift": 0x10, "alt": 0x12,
              "win": 0x5B, "windows": 0x5B, "meta": 0x5B}
_KEYS = {"enter": 0x0D, "return": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B,
         "space": 0x20, "backspace": 0x08, "delete": 0x2E, "del": 0x2E, "ins": 0x2D,
         "insert": 0x2D, "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
         "pgup": 0x21, "pgdn": 0x22, "up": 0x26, "down": 0x28, "left": 0x25,
         "right": 0x27, "arrowup": 0x26, "arrowdown": 0x28, "arrowleft": 0x25,
         "arrowright": 0x27, "capslock": 0x14, "printscreen": 0x2C,
         "apps": 0x5D, "menu": 0x5D,
         "+": 0xBB, "=": 0xBB, "-": 0xBD, "minus": 0xBD, ".": 0xBE, ",": 0xBC,
         "/": 0xBF, ";": 0xBA, "'": 0xDE, "[": 0xDB, "]": 0xDD, "\\": 0xDC, "`": 0xC0}


def key_vk(name: str) -> int:
    """键名 → 虚拟键码（字母/数字/修饰键/F1-F24/常用键名）。未知键名抛 ValueError。

    - 名字里的空格/下划线会被忽略：`page up`、`page_up`、`PAGEUP` 等价；
    - `-` 单独作为键名时仍表示减号键（先判特殊键，再做归一化）。
    """
    raw = str(name or "").strip()
    if raw in ("-", "+"):
        return _KEYS[raw]
    n = re.sub(r"[\s_]+", "", raw.lower())
    if len(n) == 1 and (n.isalpha() or n.isdigit()):
        return ord(n.upper())
    if n in _MODIFIERS:
        return _MODIFIERS[n]
    if n in _KEYS:
        return _KEYS[n]
    if n.startswith("f") and n[1:].isdigit() and 1 <= int(n[1:]) <= 24:
        return 0x70 + int(n[1:]) - 1
    raise ValueError(f"未知按键: {name}")


def do_key(key: str):
    """按键/组合键：'enter'、'f5'、'ctrl+c'、'win+ctrl+d'、'ctrl++'（放大）。

    `+` 本身也是键名，所以不能简单 split('+')：`ctrl++` / `ctrl+shift++` 的末尾
    两个加号表示「按住修饰键再按加号键」，需要单独还原，否则会被解析成只按修饰键。
    """
    raw = str(key or "").strip()
    if not raw:
        raise ValueError("按键为空")
    if raw in ("+", "-"):
        parts = [raw]
    elif raw.endswith("++"):
        head = raw[:-2].strip()
        parts = ([p for p in re.split(r"\s*\+\s*", head) if p] if head else []) + ["+"]
    else:
        parts = [p.strip() for p in raw.split("+") if p.strip()]
    if not parts:
        raise ValueError("按键为空")
    mods = [key_vk(p) for p in parts[:-1]]
    main = key_vk(parts[-1])
    with _Injecting():
        for vk in mods:
            _send_key(vk)
            time.sleep(0.02)
        _send_key(main)
        time.sleep(0.03)
        _send_key(main, up=True)
        for vk in reversed(mods):
            time.sleep(0.02)
            _send_key(vk, up=True)


# ---- 剪贴板 ----
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002


def clipboard_get() -> str:
    """读取剪贴板文本（无内容/非文本返回空串）。"""
    if not user32.OpenClipboard(0):
        return ""
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return ""
        try:
            return ctypes.c_wchar_p(ptr).value or ""
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def clipboard_set(text: str) -> bool:
    """写入剪贴板文本。"""
    data = str(text or "")
    if not user32.OpenClipboard(0):
        return False
    try:
        user32.EmptyClipboard()
        size = (len(data) + 1) * 2
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
        if not handle:
            return False
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return False
        ctypes.memmove(ptr, ctypes.create_unicode_buffer(data), size)
        kernel32.GlobalUnlock(handle)
        return bool(user32.SetClipboardData(CF_UNICODETEXT, handle))
    finally:
        user32.CloseClipboard()


# ---- 窗口 ----
def window_title(hwnd: int) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value.strip()


def window_rect(hwnd: int) -> wintypes.RECT:
    rect = wintypes.RECT()
    user32.GetWindowRect(int(hwnd or 0), ctypes.byref(rect))
    return rect


def foreground_window() -> int:
    hwnd = user32.GetForegroundWindow()
    return int(hwnd) if hwnd and user32.IsWindowVisible(hwnd) else 0


MIN_WINDOW_SIZE = 16        # list_windows 最小尺寸：小于此值视为系统辅助窗口，不列出
                            # （桌宠/悬浮球约 240×240、右键菜单约 107×52 都远大于它，
                            #  而 0×0 / 1×1 / 1905×4 的框架辅助窗口会被挡掉）


# ---------------------------------------------------------------------------
# 窗口枚举原语（纯 Win32，不依赖 COM / UI Automation）
#
# 这一层只回答「桌面上有哪些窗口、它们是什么类别、某个坐标点上是谁」——
# 与「窗口里有哪些控件」无关。元素级枚举能力的移除不影响这里。
# ---------------------------------------------------------------------------
_MIN_CLASS_BUF = 64


def win32_class_name(hwnd) -> str:
    """窗口类名。"""
    try:
        buf = ctypes.create_unicode_buffer(_MIN_CLASS_BUF + 1)
        user32.GetClassNameW(int(hwnd), buf, _MIN_CLASS_BUF + 1)
        return buf.value
    except Exception:
        return ""


# 弹出菜单/浮动面板的窗口类特征。Win32 原生菜单是 "#32768"，Qt 是
# "Qt<x>QWindowPopup*"，其他框架各有命名 —— 统一用「类名含这些词」判定。
POPUP_CLASS_HINTS = ("#32768", "popup", "menu", "tooltip", "dropdown", "flyout",
                     "combobox", "contextmenu")


def is_popup_window(hwnd) -> bool:
    """是否弹出型窗口（菜单/下拉/提示）：无父窗口、类名命中特征。"""
    hwnd = int(hwnd or 0)
    if not hwnd:
        return False
    cls = win32_class_name(hwnd).lower()
    if not any(hint in cls for hint in POPUP_CLASS_HINTS):
        return False
    return not int(user32.GetParent(hwnd) or 0)


def window_kind(hwnd) -> str:
    """窗口类别（供工具输出标注，帮助模型判断「这是不是一个菜单/桌宠」）。

    main    —— 常规应用窗口（有标题、不是工具窗口）
    menu    —— 弹出菜单（右键菜单、下拉菜单）
    overlay —— 工具型/分层窗口（桌宠、悬浮球、贴边工具条）
    other   —— 其他

    为什么用扩展样式而不是「有没有标题」判定浮层：**PyQt 会把应用名自动设为窗口
    标题**（桌宠窗口的标题其实是 "python" / "zhuzhu Copilot"，并不为空），
    靠标题判会把它误判成常规窗口 —— 实测踩过这个坑。WS_EX_TOOLWINDOW
    （且非 WS_EX_APPWINDOW）才是「这窗口不进任务栏、是辅助浮层」的可靠标志。
    """
    hwnd = int(hwnd or 0)
    if not hwnd:
        return "other"
    if is_popup_window(hwnd):
        return "menu"
    ex = _ex_style(hwnd)
    if (ex & WS_EX_TOOLWINDOW) and not (ex & WS_EX_APPWINDOW):
        return "overlay"
    if not window_title(hwnd):
        return "overlay" if (ex & WS_EX_LAYERED) else "other"
    return "main"


def process_name(hwnd) -> str:
    """取窗口所属进程的可执行文件名（识别「这是哪个程序」最直接的依据）。

    ⚠️ 这里必须写 `ctypes.byref`：本文件只 `import ctypes`，没做
    `from ctypes import byref`。写成裸 `byref(pid)` 会在运行时抛 NameError，而外层
    try 又把它吞掉、返回空串 —— 于是 `list_windows` 每一行都静默丢掉进程名，
    表面看不出任何异常（实测：本该返回 "WorkBuddyAI.exe"，实际返回 ""）。
    """
    try:
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(int(hwnd), ctypes.byref(pid))
        if not pid.value:
            return ""
        h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not h:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(1024)
            if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return buf.value.rsplit("\\", 1)[-1]
        finally:
            kernel32.CloseHandle(h)
    except Exception:
        pass
    return ""


def window_at(x, y) -> int:
    """屏幕物理坐标 (x,y) 处最顶层的**顶层**窗口句柄（0 = 取不到）。"""
    try:
        h = int(user32.WindowFromPoint(wintypes.POINT(int(x), int(y))) or 0)
        if not h:
            return 0
        root = int(user32.GetAncestor(h, GA_ROOT) or 0)
        return root or h
    except Exception:
        return 0


def _enum_top_hwnds() -> list:
    """枚举**全部**顶层窗口句柄（不做任何过滤，过滤交给调用方）。"""
    out = []

    def _cb(hwnd, _lparam):
        out.append(int(hwnd))
        return True

    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(_cb)
    try:
        user32.EnumWindows(ctypes.cast(proc, c_void_p), 0)
    except Exception:
        pass
    return out


def list_top_windows(visible_only: bool = True, min_size: int = 1,
                     kinds: tuple = ()) -> list:
    """列出顶层窗口（**含无标题 / 工具窗口 / 分层窗口 / 弹出菜单**）。

    与「只看有标题的大窗口」的实现相比，这里把桌宠、悬浮球、右键菜单这类
    「无标题小窗口」也一并列出 —— 否则模型根本看不到它们，谈不上精确点击。

    visible_only —— 只列可见窗口（默认）
    min_size     —— 宽高小于该值的窗口丢弃（默认 1，即只要不是 0×0）
    kinds        —— 只保留这些类别（main/menu/overlay/other），空表示全要
    """
    out = []
    for hwnd in _enum_top_hwnds():
        try:
            if not user32.IsWindow(hwnd):
                continue
            if visible_only and not user32.IsWindowVisible(hwnd):
                continue
            r = window_rect(hwnd)
            l, t, b_r, b_b = r.left, r.top, r.right, r.bottom
            w, h = max(0, b_r - l), max(0, b_b - t)
            if w < min_size or h < min_size:
                continue
            kind = window_kind(hwnd)
            if kinds and kind not in kinds:
                continue
            ex = _ex_style(hwnd)
            out.append({"hwnd": hwnd, "title": window_title(hwnd),
                        "class": win32_class_name(hwnd),
                        "rect": (l, t, b_r, b_b), "w": w, "h": h, "kind": kind,
                        "ex_style": ex, "process": process_name(hwnd),
                        "popup": is_popup_window(hwnd),
                        "topmost": bool(ex & WS_EX_TOPMOST)})
        except Exception:
            continue
    return out


def list_windows(kinds: tuple = (), min_size: int = MIN_WINDOW_SIZE) -> list:
    """枚举当前可见的顶层窗口，**含无标题的桌宠/悬浮球与弹出菜单**。

    与旧实现（只列「有标题且 ≥60×40」的窗口）的关键差别：桌宠、悬浮球这类
    **无标题的工具窗口**过去被直接过滤掉，模型在 list_windows 里根本看不到它们，
    自然谈不上精确点击。现在它们会带 kind=overlay 出现；右键菜单带 kind=menu。

    kinds —— 只保留这些类别：main（常规窗口）/ menu（弹出菜单）/ overlay（无标题浮层）
    """
    out = []
    for w in list_top_windows(visible_only=True, min_size=max(1, int(min_size)),
                              kinds=kinds):
        l, t, r, b = w["rect"]
        out.append({"hwnd": w["hwnd"], "title": w["title"], "x": l, "y": t,
                    "w": w["w"], "h": w["h"], "class": w["class"],
                    "process": w["process"], "kind": w["kind"],
                    "topmost": w["topmost"], "popup": w["popup"]})
    # 常规窗口排前面，浮层/菜单排后面（模型更常对常规窗口操作）
    order = {"main": 0, "other": 1, "overlay": 2, "menu": 3}
    out.sort(key=lambda w: (order.get(w["kind"], 9), -w["w"] * w["h"]))
    return out


def find_window(title: str = "", hwnd: int = 0) -> int:
    """按句柄或标题子串定位窗口（标题匹配忽略大小写，取第一个命中）。"""
    if hwnd:
        return int(hwnd) if user32.IsWindow(int(hwnd)) else 0
    key = str(title or "").strip().lower()
    if not key:
        return 0
    for w in list_windows():
        if key in w["title"].lower():
            return int(w["hwnd"])
    return 0


def popup_menus() -> list:
    """当前打开的弹出菜单窗口（右键/下拉菜单）——菜单是独立顶层窗口，需单独找。"""
    return list_windows(kinds=("menu",))


# ---- 窗口/应用管理（应对复杂桌面：弹窗抢焦点、窗口遮挡、位置乱飞） ----
SW_RESTORE = 9
SW_MAXIMIZE = 3
SW_MINIMIZE = 6
SW_SHOW = 5
HWND_TOP = 0
HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_SHOWWINDOW = 0x0040
SWP_NOACTIVATE = 0x0010
# 扩展样式常量：必须在本模块内自行定义 —— 曾经这里引用了定义在别的模块里的同名
# 常量，函数一执行就 NameError，被 `except Exception` 吞掉，于是「置顶成功、
# 取消置顶那一步抛异常」，窗口被永久留在 always-on-top 状态，而调用方还收到
# 「置顶失败」。见 _raise_topmost。教训：跨模块借常量 = 定时炸弹。
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
GWL_EXSTYLE = -20
# GetWindowLongPtrW 返回 LONG_PTR：64 位下默认 restype(c_int) 会截断，必须显式声明
try:
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
except Exception:
    pass


def window_state(hwnd: int) -> dict:
    """窗口当前状态（是否最小化/最大化/前台）。"""
    hwnd = int(hwnd or 0)
    if not hwnd or not user32.IsWindow(hwnd):
        return {"valid": False}
    return {"valid": True, "hwnd": hwnd, "title": window_title(hwnd),
            "minimized": bool(user32.IsIconic(hwnd)),
            "maximized": bool(user32.IsZoomed(hwnd)),
            "visible": bool(user32.IsWindowVisible(hwnd)),
            "foreground": foreground_window() == hwnd}


def activate_window(hwnd: int) -> bool:
    """把窗口提到前台（先恢复最小化，再置顶）。

    复杂桌面的关键：子窗口/对话框常在启动时抢焦点，直接点击会落到错误的窗口上。
    先显式激活目标窗口，再做坐标操作，可避免绝大多数「点错窗口」。

    **前台锁（foreground lock）**：`SetForegroundWindow` 只在「调用线程拥有前台窗口」
    等少数条件下才生效，否则系统只让任务栏图标闪一下。绕过它的标准做法是
    `AttachThreadInput(当前线程, 前台窗口线程)` —— 必须接**前台窗口的线程**，
    因为前台锁判定的就是「调用线程的输入队列是否与前台线程相连」。
    旧实现接的是**目标窗口的线程**，目标通常不是前台窗口，于是这一步形同虚设，
    置顶被系统静默拒绝 —— 这正是「窗口在后台时怎么都切不到前台」的根因之一。
    """
    hwnd = int(hwnd or 0)
    if not hwnd or not user32.IsWindow(hwnd):
        return False
    try:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
            time.sleep(0.15)
        fg = foreground_window()
        cur = kernel32.GetCurrentThreadId()
        attach_to = set()
        for th in (user32.GetWindowThreadProcessId(hwnd, None),
                   user32.GetWindowThreadProcessId(fg, None) if fg else 0):
            if th and th != cur:
                attach_to.add(int(th))
        attached = [th for th in attach_to
                    if user32.AttachThreadInput(th, cur, True)]
        user32.SetForegroundWindow(hwnd)
        user32.BringWindowToTop(hwnd)
        user32.SetWindowPos(hwnd, HWND_TOP, 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
        for th in attached:
            user32.AttachThreadInput(th, cur, False)
        time.sleep(0.12)
        return True
    except Exception:
        return False


def _bring_to_front(hwnd: int) -> tuple:
    """把窗口弄成「可操作状态」并**实测校验**，返回 (是否真的在前台, 说明)。

    三步，每步都验证，绝不谎报：
      ① 最小化 → `ShowWindow(SW_RESTORE)`（不恢复的话截图是废图、坐标全在屏幕外）；
      ② `activate_window`（常规置顶）；
      ③ 仍不在前台 → `_raise_topmost`（topmost 不受前台锁限制）。
    """
    hwnd = int(hwnd or 0)
    if not hwnd or not user32.IsWindow(hwnd):
        return False, "目标窗口不存在（句柄可能已失效）"
    notes = []
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
        time.sleep(0.28)
        notes.append("原本最小化，已恢复显示")
    activate_window(hwnd)
    time.sleep(0.15)
    if foreground_window() != hwnd:
        _raise_topmost(hwnd)
        activate_window(hwnd)
        time.sleep(0.12)
    if foreground_window() == hwnd:
        notes.append("已切到前台")
        return True, "；".join(notes)
    fg = foreground_window()
    notes.append(
        f"未能取得前台（当前前台是「{window_title(fg) or fg}」）—— 系统拒绝了置顶，"
        f"常见原因是锁屏/安全桌面/UAC 弹窗或别的程序正在抢焦点")
    return False, "；".join(notes)


def set_window_state(hwnd: int, action: str) -> str:
    """窗口状态操作：maximize / minimize / restore / move / resize / close。"""
    hwnd = int(hwnd or 0)
    if not hwnd or not user32.IsWindow(hwnd):
        raise ValueError(f"窗口不存在（hwnd={hwnd}）")
    action = str(action or "").strip().lower()
    with _Injecting():
        if action == "maximize":
            user32.ShowWindow(hwnd, SW_MAXIMIZE)
        elif action == "minimize":
            user32.ShowWindow(hwnd, SW_MINIMIZE)
        elif action == "restore":
            user32.ShowWindow(hwnd, SW_RESTORE)
        elif action == "show":
            user32.ShowWindow(hwnd, SW_SHOW)
        elif action == "close":
            user32.PostMessageW(hwnd, 0x0010, 0, 0)      # WM_CLOSE
        else:
            raise ValueError(f"未知窗口操作: {action}")
    time.sleep(0.18)
    return f"已对窗口「{window_title(hwnd) or hwnd}」执行 {action}"


def move_window(hwnd: int, x: int, y: int, w: int = 0, h: int = 0) -> str:
    """移动/调整窗口大小（坐标为屏幕物理像素；w/h 为 0 表示只移动不改大小）。"""
    hwnd = int(hwnd or 0)
    if not hwnd or not user32.IsWindow(hwnd):
        raise ValueError(f"窗口不存在（hwnd={hwnd}）")
    r = window_rect(hwnd)
    width = int(w) if w else (r.right - r.left)
    height = int(h) if h else (r.bottom - r.top)
    flags = SWP_NOZORDER | SWP_NOACTIVATE
    if not w and not h:
        flags |= SWP_NOSIZE
    with _Injecting():
        user32.SetWindowPos(hwnd, 0, int(x), int(y), max(1, width), max(1, height), flags)
    time.sleep(0.12)
    return (f"窗口「{window_title(hwnd) or hwnd}」已移动到 ({x},{y})，"
            f"尺寸 {max(1, width)}×{max(1, height)}")


def wait_for_window(title: str = "", hwnd: int = 0, timeout: float = 10.0,
                     poll: float = 0.25) -> int:
    """等待窗口出现（复杂桌面里安装向导/弹窗常延迟弹出，固定 sleep 不可靠）。"""
    deadline = time.time() + max(0.5, float(timeout))
    while time.time() < deadline:
        h = find_window(title=title, hwnd=hwnd)
        if h:
            st = window_state(h)
            if st.get("visible") and not st.get("minimized"):
                return h
        time.sleep(max(0.05, float(poll)))
    return 0


# ---- 应用发现与启动（把「打开某软件」收成一次调用） ----
#
# 为什么必须有这一段：用户说「打开微信」时，如果微信**没在运行**，旧版插件里
# 没有任何工具能启动它 —— 模型只能反复 list_windows / manage_window / screen_shot
# 地找，这正是「微信在后台/最小化时疯狂调用工具查找位置」的主因。
# 现在 open_app 一个调用就把「找窗口 → 没找到就启动 → 等窗口出现 → 恢复最小化 →
# 置顶 → 实测校验」全做完，模型不需要再猜。

# 口语名 → 匹配关键字（进程名/窗口标题片段）。只覆盖容易对不上的常见应用；
# 没有条目时直接用用户给的名字做模糊匹配。
_APP_ALIASES = {
    "微信": ("wechat", "weixin", "微信"),
    "wechat": ("wechat", "weixin", "微信"),
    "企业微信": ("wxwork", "wecom", "企业微信"),
    "qq": ("qq", "腾讯qq"),
    "钉钉": ("dingtalk", "钉钉"),
    "飞书": ("feishu", "lark", "飞书"),
    "浏览器": ("edge", "chrome", "firefox", "browser"),
    "记事本": ("notepad", "记事本"),
    "计算器": ("calc", "calculator", "计算器"),
    "画图": ("mspaint", "paint", "画图"),
    "资源管理器": ("explorer", "资源管理器", "文件资源管理器"),
    "命令提示符": ("cmd", "命令提示符"),
    "终端": ("windowsterminal", "wt", "terminal", "终端"),
    "任务管理器": ("taskmgr", "task manager", "任务管理器"),
    "设置": ("systemsettings", "设置"),
    "控制面板": ("control", "控制面板"),
}

# UWP / 系统设置这类**没有可执行文件**的入口：用协议 URI 启动（ShellExecute 认得）。
_APP_URIS = {
    "设置": "ms-settings:",
    "windows设置": "ms-settings:",
    "windows 设置": "ms-settings:",
    "系统设置": "ms-settings:",
}

# 开始菜单里明显不是「应用入口」的名字，索引时跳过（避免模糊匹配到卸载器）
_APP_NOISE = ("卸载", "uninstall", "官网", "帮助", "help", "readme", "说明",
              "release notes", "更新日志", "许可证", "license")

_APP_INDEX = {"at": 0.0, "items": []}
APP_INDEX_TTL = 180.0       # 索引缓存有效期（秒）：避免每次调用都全盘扫描开始菜单


def _norm_app(s: str) -> str:
    """应用名归一化：去空白/连字符/点，统一小写（「Visual Studio Code」→「visualstudiocode」）。"""
    return re.sub(r"[\s\-_·.]+", "", str(s or "")).lower()


def _start_menu_dirs() -> list:
    dirs = []
    appdata = os.environ.get("APPDATA") or ""
    programdata = os.environ.get("ProgramData") or ""
    if appdata:
        dirs.append(os.path.join(appdata, "Microsoft", "Windows",
                                 "Start Menu", "Programs"))
    if programdata:
        dirs.append(os.path.join(programdata, "Microsoft", "Windows",
                                 "Start Menu", "Programs"))
    return [d for d in dirs if os.path.isdir(d)]


def _app_index(refresh: bool = False) -> list:
    """已安装应用索引：[{name, target, kind, dir}]。

    两个来源，都**不需要第三方库、不需要 COM**：
      1. 开始菜单的 `.lnk`（用户 + 全局）—— 覆盖绝大多数桌面应用；
         启动时直接 `ShellExecute` 这个 .lnk，由系统自己解析，无需读快捷方式内容；
      2. 注册表 `App Paths` —— 覆盖没有开始菜单入口、但注册了可执行文件的应用。
    """
    now = time.time()
    if not refresh and _APP_INDEX["items"] and (now - _APP_INDEX["at"]) < APP_INDEX_TTL:
        return _APP_INDEX["items"]
    items, seen = [], set()
    for base in _start_menu_dirs():
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for fn in files:
                if not fn.lower().endswith(".lnk"):
                    continue
                name = os.path.splitext(fn)[0].strip()
                if not name:
                    continue
                low = name.lower()
                if any(t in low for t in _APP_NOISE):
                    continue
                key = _norm_app(name)
                if not key or key in seen:
                    continue
                seen.add(key)
                items.append({"name": name, "target": os.path.join(root, fn),
                              "kind": "lnk",
                              "dir": "" if root == base else os.path.basename(root)})
    try:
        import winreg
        for hive, sub in (
            (winreg.HKEY_LOCAL_MACHINE,
             r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
            (winreg.HKEY_CURRENT_USER,
             r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
            (winreg.HKEY_LOCAL_MACHINE,
             r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths"),
        ):
            try:
                with winreg.OpenKey(hive, sub) as k:
                    for i in range(winreg.QueryInfoKey(k)[0]):
                        try:
                            exe = winreg.EnumKey(k, i)
                            with winreg.OpenKey(k, exe) as ek:
                                target = str(winreg.QueryValue(ek, None) or "").strip('"')
                        except OSError:
                            continue
                        name = os.path.splitext(exe)[0]
                        key = _norm_app(name)
                        if not target or not key or key in seen:
                            continue
                        seen.add(key)
                        items.append({"name": name, "target": target,
                                      "kind": "exe", "dir": ""})
            except OSError:
                continue
    except Exception:
        pass
    _APP_INDEX.update({"at": now, "items": items})
    return items


def _app_keys(name: str) -> tuple:
    """把用户给的应用名展开成一组匹配关键字（别名优先）。"""
    raw = str(name or "").strip()
    return _APP_ALIASES.get(raw) or _APP_ALIASES.get(raw.lower()) or (raw,)


def _app_score(keys: tuple, text: str) -> float:
    """关键字与某个名字的匹配分（0~1）：精确 > 前缀 > 包含 > 相似度。"""
    n = _norm_app(text)
    if not n:
        return 0.0
    best = 0.0
    for q in keys:
        q = _norm_app(q)
        if not q:
            continue
        if n == q:
            best = max(best, 1.0)
        elif n.startswith(q) or q.startswith(n):
            best = max(best, 0.92)
        elif q in n or n in q:
            best = max(best, 0.80)
        else:
            best = max(best, difflib.SequenceMatcher(None, q, n).ratio() * 0.75)
    return best


def _probe_system_exe(keys: tuple) -> str:
    """在系统目录里按名字找可执行文件。

    系统自带工具（计算器 `calc`、任务管理器 `taskmgr`、记事本 `notepad`…）**既没有
    开始菜单快捷方式、也不在 App Paths 注册表**里，只靠索引根本找不到 ——
    但它们就躺在 `System32` / `Windows` 下，按名字直接探一下最省事。
    """
    root = os.environ.get("SystemRoot") or r"C:\Windows"
    dirs = [os.path.join(root, "System32"), root]
    for q in keys:
        q = str(q or "").strip()
        if not q or re.search(r"[\\/]", q):
            continue
        for d in dirs:
            for ext in (".exe", ".cmd", ".bat"):
                p = os.path.join(d, q + ext)
                if os.path.isfile(p):
                    return p
    return ""


def _resolve_app(name: str) -> dict:
    """把应用名解析成启动目标。返回 {target, kind, display, score, candidates}。

    解析顺序：协议 URI（UWP 如「设置」）→ 开始菜单/App Paths 索引 → 系统目录同名 exe。
    """
    raw = str(name or "").strip()
    uri = _APP_URIS.get(raw) or _APP_URIS.get(raw.lower())
    if uri:
        return {"target": uri, "kind": "uri", "display": raw,
                "score": 1.0, "candidates": []}
    keys = _app_keys(name)
    scored = []
    for it in _app_index():
        sc = _app_score(keys, it["name"])
        if sc >= FUZZY_MIN_SCORE:
            scored.append((sc, it))
    scored.sort(key=lambda x: (-x[0], len(x[1]["name"])))
    # 索引里没有「几乎同名」的命中时，先探一下系统目录 —— calc/taskmgr 这类
    # 系统工具只有 exe、没有快捷方式，探到了比拿一个弱匹配的第三方应用更准。
    if not scored or scored[0][0] < 0.95:
        exe = _probe_system_exe(keys)
        if exe:
            return {"target": exe, "kind": "system",
                    "display": os.path.splitext(os.path.basename(exe))[0],
                    "score": 0.95, "candidates": []}
    if not scored:
        near = sorted(((_app_score(keys, i["name"]), i["name"]) for i in _app_index()),
                      key=lambda x: -x[0])[:8]
        return {"target": "", "kind": "", "display": "", "score": 0.0,
                "candidates": [n for _s, n in near]}
    sc, it = scored[0]
    return {"target": it["target"], "kind": it["kind"], "display": it["name"],
            "score": sc, "candidates": [i["name"] for _s, i in scored[1:6]]}


_URI_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:")


def _is_launch_uri(target: str) -> bool:
    """是不是协议 URI（`ms-settings:` / `shell:AppsFolder\\…` 这类）。

    注意**不能**把 `C:\\path` 当 URI —— 盘符同样匹配 `^[a-z]:`，必须先排除。
    """
    if re.match(r"^[a-zA-Z]:[\\/]", target):
        return False
    return bool(_URI_RE.match(target))


def launch_app(target: str) -> None:
    """启动一个应用（.lnk / .exe / 路径 / 协议 URI）。用 ShellExecute，快捷方式由系统解析。"""
    target = str(target or "").strip()
    if not target:
        raise ValueError("启动目标为空")
    if not (_is_launch_uri(target) or os.path.exists(target)):
        raise RuntimeError(f"启动目标不存在：{target}")
    try:
        shell32 = ctypes.windll.shell32
        shell32.ShellExecuteW.restype = ctypes.c_void_p
        shell32.ShellExecuteW.argtypes = [wintypes.HWND, wintypes.LPCWSTR,
                                          wintypes.LPCWSTR, wintypes.LPCWSTR,
                                          wintypes.LPCWSTR, ctypes.c_int]
        rc = shell32.ShellExecuteW(None, "open", target, None, None, 1)
    except Exception as e:                                  # noqa: BLE001
        raise RuntimeError(f"启动失败：{e}") from e
    code = int(rc or 0)
    if code <= 32:
        raise RuntimeError(f"启动失败（ShellExecute 返回 {code}）：{target}")


def _find_app_window(keys: tuple, exclude: "set | None" = None,
                     any_new: bool = False) -> tuple:
    """在可见窗口里找应用窗口，返回 (hwnd, 标题, 命中方式)。

    keys    —— 匹配关键字（对**窗口标题**与**进程名**分别打分）
    exclude —— 排除这些 hwnd（启动后用它排除「启动前就存在的窗口」）
    any_new —— 忽略名字，只要是 exclude 之外的窗口就认（名字对不上时的兜底）
    """
    best = (0.0, 0, "", "")
    for w in list_windows():
        if w["kind"] not in ("main", "other", "overlay"):
            continue
        if exclude and w["hwnd"] in exclude:
            continue
        if any_new:
            return int(w["hwnd"]), w["title"], "启动后新出现的窗口"
        sc_t = _app_score(keys, w["title"])
        stem = os.path.splitext(str(w.get("process") or ""))[0]
        sc_p = _app_score(keys, stem) * 0.98
        if max(sc_t, sc_p) > best[0]:
            best = (max(sc_t, sc_p), w["hwnd"], w["title"],
                    "窗口标题" if sc_t >= sc_p else f"进程名 {stem}")
    if best[0] >= FUZZY_MIN_SCORE:
        return int(best[1]), best[2], best[3]
    return 0, "", ""


def _wait_app_window(keys: tuple, timeout: float = 12.0,
                     exclude: "set | None" = None, any_new: bool = False) -> tuple:
    """等应用窗口出现（启动后调用）。返回 (hwnd, 标题, 命中方式)。"""
    deadline = time.time() + max(1.0, float(timeout))
    while time.time() < deadline:
        h, title, how = _find_app_window(keys, exclude=exclude, any_new=any_new)
        if h:
            return h, title, how
        time.sleep(0.3)
    return 0, "", ""


# ---- 虚拟桌面容器 ----
def switch_virtual_desktop(action: str) -> str:
    """Windows 虚拟桌面：new=新建并切入；back/prev=回到上一个；next=下一个。"""
    combos = {"new": "win+ctrl+d", "back": "win+ctrl+left", "prev": "win+ctrl+left",
              "next": "win+ctrl+right"}
    combo = combos.get(str(action or "new").strip().lower())
    if not combo:
        raise ValueError(f"未知虚拟桌面操作: {action}（可用 new/back/next/prev）")
    do_key(combo)
    time.sleep(0.8)          # 等待桌面切换动画完成
    return combo


# ============================================================================
# 真人输入打断检测（全局低级钩子）
# ============================================================================

class InputWatcher:
    """监听真人鼠标/键盘输入 → 置「用户已接管」。

    AI 自身注入的事件带 dwExtraInfo 签名且在注入窗口内，一律忽略；
    只有真实用户的鼠标移动/点击/按键（且处于操控会话有效期）才触发打断。
    """

    _EVENTS = frozenset({WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_RBUTTONDOWN, WM_MBUTTONDOWN,
                         WM_MOUSEWHEEL, WM_XBUTTONDOWN, WM_KEYDOWN, WM_SYSKEYDOWN,
                         WM_KEYUP, WM_SYSKEYUP})
    HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

    def __init__(self):
        self._thread = None
        self._mouse_hook = None
        self._key_hook = None
        self._mouse_cb = self.HOOKPROC(self._on_mouse)
        self._key_cb = self.HOOKPROC(self._on_key)

    # ---- 回调（钩子线程执行，必须轻量、绝不抛异常） ----
    def _hit(self, extra: int):
        try:
            if int(extra or 0) == INJECT_SIGNATURE or _injecting():
                return                      # AI 自身注入 → 不算真人输入
            if time.time() > _STATE["active_until"]:
                return                      # 非操控会话期 → 不打扰（用户正常用电脑）
            mark_interrupted("检测到真人鼠标/键盘输入")
        except Exception:
            pass

    def _on_mouse(self, ncode, wparam, lparam):
        try:
            if ncode >= 0 and wparam in self._EVENTS:
                self._hit(ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents.dwExtraInfo)
        except Exception:
            pass
        try:
            return user32.CallNextHookEx(self._mouse_hook, ncode, wparam, lparam)
        except Exception:
            return 0

    def _on_key(self, ncode, wparam, lparam):
        try:
            if ncode >= 0 and wparam in self._EVENTS:
                self._hit(ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents.dwExtraInfo)
        except Exception:
            pass
        try:
            return user32.CallNextHookEx(self._key_hook, ncode, wparam, lparam)
        except Exception:
            return 0

    # ---- 生命周期 ----
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        try:
            hmod = kernel32.GetModuleHandleW(None)
            self._mouse_hook = user32.SetWindowsHookExW(WH_MOUSE_LL, self._mouse_cb, hmod, 0)
            self._key_hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._key_cb, hmod, 0)
            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception:
            pass


_watcher = InputWatcher()


# ============================================================================
# 共享状态（MCP 工具与 HTTP 界面共用）
# ============================================================================
_STATE = {
    "interrupted": False,       # 真人接管标记
    "paused": False,            # 用户暂停标记
    "container": "foreground",  # 操控容器：foreground=用户前台桌面 / virtual_desktop=独立虚拟桌面
    "active_until": 0.0,        # 操控会话滑动有效期
    "session_started": 0.0,
    "view": "full",             # 当前视觉基准描述（full/window/zoom）
    "input": "mouse",           # 实际输入方式：touch/mouse
    "screen": [0, 0],
    "cursor": [0, 0],
    "foreground": {"hwnd": 0, "title": ""},
    "web_port": 0,
    "aura": False,             # 屏幕边缘彩色光环是否点亮
}
_LOG: list = []
_LOG_LOCK = threading.Lock()


def _log(who: str, text: str):
    """写操作日志（环形，供 Web UI 展示）。who ∈ ai/user/sys。"""
    entry = {"t": time.strftime("%H:%M:%S"), "who": who, "text": str(text)[:300]}
    with _LOG_LOCK:
        _LOG.append(entry)
        if len(_LOG) > LOG_MAX:
            del _LOG[:len(_LOG) - LOG_MAX]


def _touch_session():
    """刷新操控会话有效期：每个工具调用都延长，期内真人输入才算接管。"""
    now = time.time()
    if not _STATE["session_started"]:
        _STATE["session_started"] = now
    _STATE["active_until"] = now + SESSION_TTL_S


def mark_interrupted(reason: str):
    """置「用户已接管」：后续操控工具一律拒绝，把控制权交还用户。"""
    if not _STATE["interrupted"]:
        _STATE["interrupted"] = True
        _log("user", f"接管操控（{reason}）")
    else:
        _STATE["interrupted"] = True
    _aura_off("用户接管")


def _aura_on():
    """操控开始：点亮屏幕边缘彩色光环（用户可见的「AI 正在操作」提示）。"""
    if aura is None:
        return
    try:
        aura.set_aura_active(True)
        _STATE["aura"] = True
    except Exception:
        pass


def _aura_off(reason: str = ""):
    """操控结束/被接管：淡出光环。"""
    if aura is None:
        return
    try:
        aura.set_aura_active(False)
        _STATE["aura"] = False
    except Exception:
        pass


def _guard(action: str) -> str:
    """操控前置检查：返回拒绝原因（空串=放行）。"""
    if _STATE["paused"]:
        _log("sys", f"拒绝 {action}：用户已暂停操控")
        return ("[已被用户暂停] 用户在可视化界面上暂停了 AI 操控，本次操作未执行。"
                "请立即停止操控，等待用户在界面点「恢复」或明确对你说「继续」后再调用 "
                "control(action=\"resume\") 恢复。")
    if _STATE["interrupted"]:
        _log("sys", f"拒绝 {action}：用户已接管")
        return ("[用户已接管] 检测到真人鼠标/键盘输入（或用户点击了接管），控制权已交还用户，"
                "本次操作未执行。请立即停止一切操控并向用户汇报当前进度与界面状态；"
                "只有用户明确说「继续」后才可调用 control(action=\"resume\") 恢复。")
    return ""


def _interrupted() -> bool:
    return bool(_STATE["interrupted"] or _STATE["paused"])


# ---- 视觉基准：AI 所见图 ↔ 屏幕物理坐标 ----
_VIEW = {"x0": 0.0, "y0": 0.0, "w": 0.0, "h": 0.0, "img_w": 0.0, "img_h": 0.0}


def _set_view(x0, y0, w, h, img_w, img_h, kind: str, target_hwnd: int = 0):
    _VIEW.update({"x0": float(x0), "y0": float(y0), "w": float(w), "h": float(h),
                  "img_w": float(img_w), "img_h": float(img_h)})
    _STATE["view"] = kind
    # 记住「这张截图是哪个窗口的」：坐标点击前据此把目标窗口提到最前，
    # 避免「照着截图点」却点到盖在上面的别的窗口（见 _ensure_click_target）。
    _STATE["view_target"] = int(target_hwnd or 0)


def _ex_style(hwnd: int) -> int:
    """读窗口扩展样式（GetWindowLongPtrW；任何异常都退回 0，绝不因读样式失败而中断）。"""
    try:
        return int(user32.GetWindowLongPtrW(int(hwnd), GWL_EXSTYLE) or 0)
    except Exception:
        return 0


def _raise_topmost(hwnd: int) -> bool:
    """把窗口临时置顶再恢复：topmost **不受「前台锁」限制**，能强行压过遮挡窗口。

    `SetForegroundWindow` 常被系统拒绝（用户在别的窗口里打字时），此时普通窗口
    即使 `BringWindowToTop` 也未必真到最前。临时置顶是绕过它的标准手段：
    置顶 → 等一帧 → 取消置顶（原本就是置顶窗口则保持不动，不破坏它的属性）。

    **必须保证「取消置顶」一定会执行**：曾经这里的样式判断引用了未定义的名字，
    抛 NameError 被吞掉，结果窗口被留在 always-on-top 且再也回不来（用户窗口
    会一直压在别的窗口上面），调用方还收到「失败」。所以用 try/finally 兜底。
    """
    hwnd = int(hwnd or 0)
    if not hwnd or not user32.IsWindow(hwnd):
        return False
    was_topmost = bool(_ex_style(hwnd) & WS_EX_TOPMOST)
    try:
        user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        time.sleep(0.18)
        return True
    except Exception:
        return False
    finally:
        # 原本就置顶的窗口保持不动；否则无论上面成功与否都要撤掉置顶，
        # 绝不把用户的窗口永久留在最前面。
        if not was_topmost:
            try:
                user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0,
                                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
                time.sleep(0.12)
            except Exception:
                pass


def _input_target_hwnd(args: dict = None) -> int:
    """当前「键盘输入该去哪个窗口」：显式 title/hwnd 优先，其次最近一次窗口截图的那个。"""
    explicit = 0
    if args:
        explicit = find_window(str(args.get("title") or ""), _num(args.get("hwnd"), 0))
    target = explicit or int(_STATE.get("view_target") or 0)
    if target and not user32.IsWindow(target):
        return 0
    return int(target or 0)


def _ensure_input_target(args: dict, action: str) -> str:
    """键盘注入前确保目标窗口真的在前台 —— 否则文字会打进**别的应用**里。

    这是最危险的一类失败（实测用户反馈过）：目标窗口被遮挡时，AI 照着「只截该窗口」
    的截图操作；如果点击没能把焦点交给目标窗口，随后的 `type_text` / `enter` 就会
    打进**当前前台应用**（比如 AI 助手自己的输入框）—— 文字发错地方，比点不到严重得多。
    `SendInput` 的键击只认「前台窗口」，没有目标概念，所以必须在这里把关。

    抢不到前台时**直接拒绝注入**并说明原因，绝不「先打了再说」。
    """
    target = _input_target_hwnd(args)
    if not target:
        return ""
    if window_kind(target) == "menu":
        return ""                      # 菜单不接受键盘输入，无需把关
    fg = foreground_window()
    if fg == target:
        return ""
    name = window_title(target) or target
    activate_window(target)
    time.sleep(0.2)
    if foreground_window() != target:
        _raise_topmost(target)
    if foreground_window() == target:
        return (f"注意：注入前目标窗口「{name}」不在前台，已先把它切到前台再{action}。")
    raise RuntimeError(
        f"已拒绝{action}：目标窗口「{name}」未能获得焦点（当前前台是"
        f"「{window_title(foreground_window()) or foreground_window()}」）。"
        f"此时注入会打进前台应用（例如 AI 助手自己的输入框），所以**没有执行**。"
        f"请先手动切到「{name}」，或用 manage_window(action=\"activate\", hwnd={target}) 后重试。")


def _ensure_click_target(x: int, y: int, args: dict = None) -> str:
    """坐标点击前，确保「这一击真的会落到目标窗口上」；必要时把目标窗口提到最前。

    为什么必须有这一步（实测踩过的坑）：窗口截图走 PrintWindow，**窗口被遮挡也照样
    能截到内容**；但鼠标点击只会落到**该点最顶层的窗口**上。于是「照着截图点」时，
    若目标窗口被别的窗口盖着，点击就打到了遮挡窗口上 —— 目标界面毫无变化，任务却
    以为自己点过了（微信「点文件传输助手」就是这么失败的）。

    提到最前分两步，且**每步都实测验证**：
      ① `activate_window`（正常路径）；
      ② 若前台锁拒绝了置顶，改用「临时置顶」（topmost 不受前台锁限制）。
    两步都失败时**如实告警**，绝不说「已激活置顶」—— 谎报会让模型以为点成功了。
    """
    explicit = 0
    if args:
        explicit = find_window(str(args.get("title") or ""), _num(args.get("hwnd"), 0))
    target = explicit or int(_STATE.get("view_target") or 0)
    if not target or not user32.IsWindow(target):
        return ""
    r = window_rect(target)
    if not (r.left <= x <= r.right and r.top <= y <= r.bottom):
        return ""                      # 落点不在目标窗口内 → 不擅自换窗口
    under = window_at(x, y)
    if under == target:
        return ""
    cover = window_title(under) or under
    name = window_title(target) or target
    activate_window(target)
    time.sleep(0.2)
    if window_at(x, y) == target:
        return (f"注意：落点 ({x},{y}) 原本被「{cover}」盖住，"
                f"已先把目标窗口「{name}」激活置顶再点击。")
    _raise_topmost(target)
    if window_at(x, y) == target:
        return (f"注意：落点 ({x},{y}) 原本被「{cover}」盖住，系统拒绝了常规置顶，"
                f"已改用临时置顶把「{name}」提到最前再点击。")
    raise RuntimeError(
        f"已拒绝点击：落点 ({x},{y}) 被「{cover}」盖住，且系统拒绝把目标窗口「{name}」"
        f"提到最前。此时点击会落到遮挡窗口上（可能误触别的应用的按钮），所以**没有执行**。"
        f"请先手动切到「{name}」，或用 manage_window(action=\"activate\", hwnd={target}) 后重试；"
        f"也可以对遮挡窗口做 manage_window(action=\"minimize\") 让开。")


def to_physical(x, y) -> tuple:
    """当前截图基准图像坐标 → 屏幕物理坐标（无基准时按物理坐标原样透传）。

    用 round 而不是 int：截图缩放比常常是 1.5 / 2.0 / 2.5 这类非整数（1920 屏 →
    1280 图就是 1.5），int() 向下截断会产生**系统性负偏差** —— 图像里越靠右下的点
    偏得越多，最大可达一个缩放步长（4K 屏缩到 1280 时是 3px）。对 Blender 里只有
    20px 高的按钮，3px 就够点到隔壁控件了。round 把误差压到 ±0.5 像素。
    """
    x, y = _num(x), _num(y)
    iw, ih = _VIEW.get("img_w", 0.0), _VIEW.get("img_h", 0.0)
    w = _VIEW.get("w", 0.0)
    if iw <= 0 or ih <= 0 or w <= 0:
        return int(x), int(y)
    return (int(round(_VIEW["x0"] + x * w / iw)),
            int(round(_VIEW["y0"] + y * _VIEW.get("h", 0.0) / ih)))


def _resolve_point(args: dict, kx: str = "x", ky: str = "y") -> tuple:
    """解析工具入参里的坐标 → 屏幕物理坐标。

    默认按「当前截图的图像坐标」换算（模型绝大多数时候读的就是最近一张图）。
    显式传 space="screen" 时直通 —— 用于模型手里握着**物理坐标**（比如上一轮
    click 的返回值、或 list_windows 给的窗口位置）却不想被当前的截图基准二次换算。
    没有这个开关时，模型只能靠「再截一张图」来对齐基准，而那张新图又会让基准
    变成别的窗口/区域，来回打转。

    kx/ky 用于取不同键名（drag 用 x1/y1、x2/y2）。
    """
    if str(args.get("space") or "image").strip().lower() in (
            "screen", "physical", "abs", "absolute", "物理", "绝对"):
        return _num(args.get(kx)), _num(args.get(ky))
    return to_physical(args.get(kx), args.get(ky))


def _num(v, default: int = 0) -> int:
    """健壮数值转换：容忍 '1280, 720' / 'x=100' / '100px' 等模型常见写法。"""
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        return int(v)
    for tok in re.findall(r"-?\d+(?:\.\d+)?", str(v or "")):
        try:
            return int(float(tok))
        except ValueError:
            continue
    return int(default)


def _bool(v, default: bool = False) -> bool:
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("1", "true", "yes", "on", "是"):
        return True
    if s in ("0", "false", "no", "off", "否"):
        return False
    return default


def _refresh_env():
    """刷新状态里的环境快照（屏幕/鼠标/前台窗口/输入方式）。"""
    _STATE["screen"] = list(screen_size())
    _STATE["cursor"] = list(_cursor_pos())
    hwnd = foreground_window()
    _STATE["foreground"] = {"hwnd": hwnd, "title": window_title(hwnd) if hwnd else ""}
    # _STATE["input"] 表示**实际会用的注入方式**：默认是鼠标（像素级精准），
    # 触控仅在调用方显式传 input="touch" 时才用。触控设备是否可用另记一份。
    _STATE["input"] = "mouse"
    _STATE["touch"] = "available" if touch_ready() else "unavailable"


# ============================================================================
# MCP 工具实现
# ============================================================================

def tool_screen_info(args: dict) -> str:
    _refresh_env()
    sw, sh = _STATE["screen"]
    vx, vy, vw, vh = _virtual_screen()
    multi = (vw, vh) != (sw, sh) or (vx, vy) != (0, 0)
    remain = max(0, int(_STATE["active_until"] - time.time()))
    lines = [
        f"屏幕物理分辨率: {sw}×{sh}（主显示器；进程已做 DPI 感知，坐标即物理像素）",
        f"虚拟桌面: 原点 ({vx},{vy}) 尺寸 {vw}×{vh}"
        + ("　← 多显示器：截图覆盖整个虚拟桌面，原点不为 0 属正常" if multi else ""),
        f"输入方式: 鼠标注入（像素级精准，默认）"
        + ("；本机也有触控设备，如需触控语义可传 input=\"touch\""
           if touch_ready() else "；本机无可用触控设备"),
        f"坐标定位: screen_shot 看图取坐标 → refine_point 精修（像素级吸附到控件中心）→ "
        f"click 点击（本插件不读取控件树，一律坐标定位，故对 Blender 等自绘界面同样有效）",
        f"点击精度: click/drag 默认自动做元素吸附（把坐标修正到最近控件的几何中心，"
        f"依据是像素连通域，不依赖控件树）；想点精确像素传 snap=false",
        _transform_line(),
        f"操控提示: {aura.aura_capability() if aura else '不可用'}",
        f"操控容器: {'独立虚拟桌面' if _STATE['container'] == 'virtual_desktop' else '用户前台桌面'}",
        f"鼠标位置: {_STATE['cursor'][0]}, {_STATE['cursor'][1]}",
        f"前台窗口: {_STATE['foreground']['title'] or '(无)'}",
        f"接管状态: {'已被用户接管/暂停（操控被拒绝）' if _interrupted() else '正常'}"
        + f"；操控会话剩余 {remain}s（每次工具调用自动延长）",
        f"可视化界面: http://127.0.0.1:{_STATE['web_port']}/",
        "下一步建议: screen_shot 先看图 → 小目标先 zoom_in 放大到接近 1:1 → "
        "拿不准坐标就 refine_point 精修并看图确认 → click(verify=true) 点击 → 重新截图确认结果。",
    ]
    _log("ai", "查看环境信息")
    return "\n".join(lines)


def _transform_line() -> str:
    """当前截图基准的**精确换算公式**（原点 + 缩放比），供模型换算与人工排障。

    为什么要把数字写出来：模型常常需要「图上读到的点」与「已知的物理坐标」互相
    换算（比如拿 list_windows 给的窗口位置去推图上的大致区域），只给「自动换算」
    四个字它就只能靠猜；出问题时人也无法判断到底是基准错了还是缩放错了。
    """
    iw, ih = _VIEW.get("img_w", 0.0) or 0.0, _VIEW.get("img_h", 0.0) or 0.0
    w = _VIEW.get("w", 0.0) or 0.0
    if iw <= 0 or ih <= 0 or w <= 0:
        return "坐标换算：当前无截图基准，x/y 按屏幕物理坐标直接使用（space=\"screen\"）。"
    sx, sy = w / iw, (_VIEW.get("h", 0.0) or 0.0) / ih
    ox, oy = int(round(_VIEW.get("x0", 0.0))), int(round(_VIEW.get("y0", 0.0)))
    same = abs(sx - 1) < 1e-6 and abs(sy - 1) < 1e-6
    return (f"坐标换算：屏幕物理 = ({ox},{oy}) + 图像坐标 × ({sx:.4f},{sy:.4f})"
            + ("，即 1:1（图像像素就是屏幕像素，读数即坐标）" if same else
               f"（图上 1px ≈ 屏幕 {sx:.2f}px，读数误差会被放大 {sx:.2f} 倍，"
               f"小目标请先 zoom_in 放大再读）"))


def _shot_meta(desc: str, grid: bool = False) -> str:
    """截图返回文本：说明「是否纯净」+ 精确坐标换算，避免模型误以为图上有坐标轴。"""
    head = (f"{desc}。本图**按你的要求叠加了 {GRID_CELLS}×{GRID_CELLS} 坐标网格与像素刻度**，"
            "刻度数字即图像像素值；" if grid else
            f"{desc}。截图为**纯净原图**（不叠加坐标网格/刻度/准星），你直接看图判断目标位置即可；")
    return (head + "所有工具的 x/y 一律用**图像像素坐标**（图像左上角为 0,0），"
            "插件自动换算为屏幕物理坐标，无需再乘缩放比例。"
            + _transform_line()
            + "点击默认会自动做元素吸附（把坐标修正到最近控件的中心，见 refine_point）；"
              "需要点精确像素时传 snap=false。"
              "（需要网格辅助读数时，在 screen_shot 里显式传 grid=true。）")


def tool_screen_shot(args: dict) -> list:
    mode = str(args.get("mode") or "full").strip().lower()
    grid = _bool(args.get("grid"), False)      # 默认纯净截图：坐标轴不画，直接发图给模型
    hwnd = _num(args.get("hwnd"), 0)
    title = str(args.get("title") or "")
    capture = str(args.get("capture") or "auto").strip().lower()
    if mode == "window" or hwnd or title:
        target = find_window(title, hwnd)
        if not target:
            raise ValueError(f"未找到窗口（title={title!r} hwnd={hwnd}），可用 list_windows 查看")
        png, iw, ih, x, y, w, h = grab_window(target, grid=grid, capture=capture)
        _set_view(x, y, w, h, iw, ih, "window", target_hwnd=target)
        # 屏幕抓取路径可能因窗口部分在屏外而裁剪，落点基准要跟着调整，否则会整体偏移
        off = _GRAB_INFO.get("offset")
        if off and (off[0] or off[1]):
            x, y = x + off[0], y + off[1]
            _set_view(x, y, w, h, iw, ih, "window", target_hwnd=target)
        desc = (f"已截取窗口「{window_title(target)}」（区域 {w}×{h}），图像 {iw}×{ih}"
                f"（窗口左上角对应屏幕 {x},{y}；抓取方式：{_GRAB_INFO['strategy'] or 'PrintWindow'}）"
                + (_GRAB_INFO.get("note") or ""))
        if _GRAB_INFO["strategy"] == "屏幕抓取":
            desc += ("。注意：该窗口的 PrintWindow 抓到的是空白（常见于 GPU 渲染/独占表面的"
                     "界面，如 Blender 一类 3D 软件），已改用屏幕抓取 —— 它会先激活并置顶"
                     "该窗口，操作期间请勿让其他窗口盖住它")
        _log("ai", f"截屏：窗口「{window_title(target)}」")
    else:
        png, iw, ih, ox, oy, vw, vh = grab_screen(grid=grid)
        _set_view(ox, oy, vw, vh, iw, ih, "full")
        scale = vw / iw
        desc = (f"已截取整个桌面 {vw}×{vh}（虚拟桌面并集，含全部显示器；原点 {ox},{oy}），"
                f"图像 {iw}×{ih}（缩放比 {scale:.2f}）"
                + ("。注意：图像被缩小了，图上 1px 误差在屏幕上会被放大 "
                   f"{scale:.2f} 倍；点小目标（工具栏按钮/菜单项）请先 zoom_in 放大到"
                   "接近 1:1 再读数。" if scale > 1.05 else ""))
        _log("ai", "截屏：整个桌面")
    _refresh_env()
    return [{"type": "text", "text": _shot_meta(desc, grid=grid)},
            {"type": "image", "data": base64.b64encode(png).decode(), "mimeType": "image/png"}]


def tool_zoom_in(args: dict) -> list:
    x, y = _resolve_point(args)
    size = _num(args.get("size"), 400)
    factor = _num(args.get("factor"), 3)
    grid = _bool(args.get("grid"), False)
    cursor = _bool(args.get("cursor"), False)
    smooth = _bool(args.get("smooth"), False)
    png, iw, ih, x0, y0, region = grab_zoom(x, y, size, factor, grid=grid,
                                             cursor=cursor, smooth=smooth)
    _set_view(x0, y0, region, region, iw, ih, "zoom")
    _log("ai", f"放大查看 ({x},{y}) 区域 {region}px × {factor}")
    crisp = "" if smooth else "（最近邻放大：边缘是硬实的像素方块，可直接按像素读数）"
    return [{"type": "text",
             "text": _shot_meta(f"已放大屏幕 ({x},{y}) 附近 {region}×{region} 区域为 {iw}×{ih}"
                                f"（放大 {iw / region:.1f} 倍）{crisp}", grid=grid)},
            {"type": "image", "data": base64.b64encode(png).decode(), "mimeType": "image/png"}]


_KIND_LABEL = {"main": "常规窗口", "menu": "弹出菜单", "overlay": "工具型浮层（桌宠/悬浮球）",
               "other": "其他"}


def _window_line(w: dict, idx: int = 0) -> str:
    """渲染一行窗口信息（含类别 / 进程 / 类名 / **最小化标记**）。

    最小化标记必须直接写在这一行里：模型看到「（最小化）」就知道要先
    `open_app` 或 `manage_window(action="restore")`，而不是照着哨兵尺寸
    （160×28、位置 -32000）去算坐标 —— 那样算出来的点全在屏幕外。
    """
    head = f"{idx}. " if idx else ""
    kind = _KIND_LABEL.get(w.get("kind", ""), w.get("kind", ""))
    title = w.get("title") or "（无标题）"
    extra = []
    if w.get("process"):
        extra.append(w["process"])
    if w.get("class"):
        extra.append(w["class"])
    if w.get("topmost"):
        extra.append("置顶")
    try:
        minimized = bool(user32.IsIconic(int(w["hwnd"])))
    except (OSError, TypeError, ValueError):
        minimized = False
    if minimized:
        extra.append("最小化")
    tail = ("  [" + " · ".join(extra) + "]") if extra else ""
    return (f"{head}[{w['hwnd']}] <{kind}> {title} — 位置 {w['x']},{w['y']} "
            f"尺寸 {w['w']}×{w['h']}{tail}")


def tool_list_windows(args: dict) -> str:
    kinds = ()
    kind = str(args.get("kind") or "").strip().lower()
    if kind in _KIND_LABEL:
        kinds = (kind,)
    wins = list_windows(kinds=kinds)
    _log("ai", f"列出窗口（{len(wins)} 个）")
    if not wins:
        return "当前没有可见窗口"
    limit = max(1, min(200, _num(args.get("limit"), 40)))
    lines = [_window_line(w, i + 1) for i, w in enumerate(wins[:limit])]
    return ("可见窗口（hwnd / 类别 / 标题 / 位置 / 尺寸）:\n" + "\n".join(lines)
            + "\n说明：<弹出菜单> 是右键/下拉出来的菜单（菜单项用 screenshot + 坐标点）；"
              "<无标题浮层> 多为桌宠/悬浮球这类工具窗口（没有标题，只能靠 hwnd 定位）；"
              "标注「最小化」的窗口不能直接截图算坐标（尺寸是哨兵值，坐标会全错），"
              "要先恢复。"
              "\n**找某个应用时不要反复调本工具**：直接用 open_app(name=\"微信\")，"
              "它会把「没运行就启动 / 最小化就恢复 / 切到前台」一次做完并给出 hwnd。"
              "\n用 screen_shot(mode=\"window\", title=\"标题片段\") 只看某一个窗口；"
              "kind=main/menu/overlay 可只看某一类。")


def _window_rel_to_screen(hwnd: int, rel_x, rel_y) -> tuple:
    """把「窗口截图的图像坐标」换算成屏幕物理坐标，返回 (x, y, note)。

    为什么要有这一步：窗口截图（screen_shot(mode="window")）在窗口宽度超过
    1280 时会**等比缩小**，模型从图上读到的坐标是**图像坐标**而不是窗口像素 ——
    直接当窗口偏移用会整体偏掉。这里判断「当前视觉基准是否就是该窗口」：
      * 是 → 走 to_physical，按截图缩放比精确换算；
      * 否 → 按 1:1（相对窗口外框左上角）处理，并提示模型先截图刷新基准。

    基准说明：窗口截图的 (0,0) 对应 `GetWindowRect` 的左上角（含标题栏与隐形
    边框），所以 rel_x/rel_y 就是「在这张窗口截图上读到的像素位置」，无歧义。
    """
    r = window_rect(hwnd)
    v = _VIEW
    same_view = (v["w"] > 0 and v["img_w"] > 0
                 and abs(v["x0"] - r.left) <= 2 and abs(v["y0"] - r.top) <= 2)
    if same_view:
        x, y = to_physical(rel_x, rel_y)
        return x, y, ""
    x, y = r.left + int(rel_x), r.top + int(rel_y)
    return x, y, \
        ("注意：未检测到该窗口的最新截图基准，rel_x/rel_y 按「相对窗口外框左上角 1:1」"
         "处理；窗口被缩放过时会有偏差，建议先 screen_shot(mode=\"window\") 刷新基准再点。")


def tool_click_window(args: dict) -> str:
    """窗口相对坐标点击：rel_x/rel_y 用**窗口截图上的像素坐标**。

    适用场景：没有可访问性树的窗口（Blender 等 GPU 自绘界面、游戏、部分浮层）
    只能用坐标点击，而屏幕绝对坐标会随窗口移动全部失效 —— 用「相对窗口截图」
    的坐标，窗口挪了也不会点错。不传 rel_x/rel_y 时点窗口正中心。

    精度保证：若刚截过这个窗口，会按截图缩放比精确换算（窗口 >1280px 被缩小时
    也准）；否则按 1:1 处理并如实提示。
    """
    reason = _guard("click_window")
    if reason:
        return reason
    hwnd = _target_hwnd(args)
    r = window_rect(hwnd)
    ww, wh = max(1, r.right - r.left), max(1, r.bottom - r.top)
    rx = _num(args.get("rel_x"), -1)
    ry = _num(args.get("rel_y"), -1)
    if rx < 0 and ry < 0:
        rx, ry = ww // 2, wh // 2
    if not (0 <= rx < ww and 0 <= ry < wh):
        raise ValueError(f"窗口相对坐标 ({rx},{ry}) 超出窗口范围（窗口尺寸 {ww}×{wh}）；"
                         f"rel_x/rel_y 应取 0..{ww - 1} / 0..{wh - 1}，"
                         f"不传则点窗口正中心。")
    x, y, rnote = _window_rel_to_screen(hwnd, rx, ry)
    # 窗口相对点击同样做吸附：这是「窗口内精确点控件」的主路径
    x, y, snap_note = _apply_snap(args, x, y)
    button = str(args.get("button") or "left").strip().lower()
    clicks = max(1, min(_num(args.get("clicks"), 1), 3))
    verify = _bool(args.get("verify"), False)
    warn = _prepare_target(hwnd)
    before = _pre_click_probe(x, y, hwnd) if verify else ()
    if not do_click(x, y, button, clicks, mode=_input_mode(args)):
        return (f"⚠ **没有完成点击**：光标无法稳定停在 ({x},{y})（连续回读不一致）。"
                f"这通常说明**用户正在用真实鼠标**，注入被实时覆盖了 —— 此时硬点会落到"
                f"别的位置。请先请用户停手/确认，或调 control(action=\"status\") 看接管状态，"
                f"不要重复重试同一坐标。")
    vtext = _click_verify(x, y, before, hwnd)
    _log("ai", f"窗口相对点击 [{hwnd}] 偏移({rx},{ry}) → 屏幕({x},{y}) {button}")
    _refresh_env()
    return (f"已在窗口「{window_title(hwnd) or hwnd}」内偏移 ({rx},{ry}) 处点击"
            f"（屏幕物理坐标 ({x},{y})，{button}×{clicks}，鼠标注入）。"
            f"{_join_notes(rnote, snap_note, warn, _hit_check(hwnd, x, y))}{vtext}"
            f"请重新 screen_shot 验证结果。")


def _window_sig(hwnd: int, cells: int = 32) -> tuple:
    """窗口内容的粗粒度指纹（cells×cells 的 RGB 采样），用于判断操作是否真产生了效果。

    做法：PrintWindow 抓到原尺寸位图后 **StretchBlt 缩到 32 像素宽**再取样，
    这样跨进程读回来的只有 4KB（而不是几 MB），一次判断约 10~30ms。
    """
    r = window_rect(hwnd)
    w, h = r.right - r.left, r.bottom - r.top
    if w <= 0 or h <= 0:
        return ()
    tw = max(8, int(cells))
    th = max(1, round(h * tw / w))
    scr = user32.GetDC(0)
    src = gdi32.CreateCompatibleDC(scr)
    full = gdi32.CreateCompatibleBitmap(scr, w, h)
    old1 = gdi32.SelectObject(src, full)
    dst = gdi32.CreateCompatibleDC(scr)
    small = gdi32.CreateCompatibleBitmap(scr, tw, th)
    old2 = gdi32.SelectObject(dst, small)
    try:
        if not user32.PrintWindow(hwnd, src, 2):
            return ()
        gdi32.SetStretchBltMode(dst, HALFTONE)
        if not gdi32.StretchBlt(int(dst), 0, 0, tw, th, int(src), 0, 0, w, h, SRCCOPY):
            return ()
        bits = _dib_bits(dst, small, tw, th)
        return tuple((bits[i + 2], bits[i + 1], bits[i]) for i in range(0, len(bits), 4))
    except Exception:
        return ()
    finally:
        gdi32.SelectObject(src, old1)
        gdi32.SelectObject(dst, old2)
        gdi32.DeleteObject(full)
        gdi32.DeleteObject(small)
        gdi32.DeleteDC(src)
        gdi32.DeleteDC(dst)
        user32.ReleaseDC(0, scr)


def _sig_change_ratio(a, b, tol: int = 24) -> float:
    """两份窗口指纹的变化比例（0~1）。窗口动画/光标闪烁只会动极少数采样点。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    diff = sum(1 for (r1, g1, b1), (r2, g2, b2) in zip(a, b)
               if abs(r1 - r2) + abs(g1 - g2) + abs(b1 - b2) > tol)
    return diff / len(a)


def _pre_click_probe(x: int, y: int, hwnd: int) -> tuple:
    """点击前的双通道基线：落点局部像素 + 目标窗口整体指纹。

    为什么要两个：点击的**副作用常发生在别处**（点聊天列表某一行，变化的是右侧
    会话面板；点按钮弹出对话框，变化在窗口中部）。只看落点周围那一小块会漏判，
    进而误报「点了没反应」，把模型带偏。
    """
    return (_region_snapshot(x, y), _window_sig(hwnd) if hwnd else ())


def _click_verify(x: int, y: int, before, hwnd: int = 0) -> str:
    """点击后比对落点局部像素与目标窗口整体指纹，判断界面是否真的响应了。

    「点了没反应」是复杂软件里最常见的静默失败（窗口没激活、被遮挡、控件禁用、
    坐标漂移）。双通道比对（局部 96×96 逐像素 + 整窗 32 点指纹）能可靠地把它抓出来，
    比让模型反复截全屏肉眼看省得多。
    """
    if not before:
        return ""
    base_region, base_sig = before if isinstance(before, tuple) else (before, ())
    time.sleep(0.12)
    changed, total = _region_diff(base_region, _region_snapshot(x, y)) if base_region else (0, 0)
    ratio = _sig_change_ratio(base_sig, _window_sig(hwnd)) if (base_sig and hwnd) else 0.0
    if changed > 0 or ratio >= INVOKE_MIN_CHANGE_RATIO:
        parts = []
        if changed > 0:
            parts.append(f"落点附近 {changed}/{total} 个像素变化")
        if ratio > 0:
            parts.append(f"窗口整体变化 {ratio:.0%}")
        return "点击后" + "、".join(parts) + "，界面已响应。"
    return ("注意：点击后落点附近像素与整个窗口都**没有变化** —— 这一击很可能没生效"
            "（目标未响应/被遮挡/窗口未激活/控件禁用）。请先重新 screen_shot 确认，"
            "不要盲目重试同一坐标。")


def tool_verify_point(args: dict) -> str:
    """精度自检：把图像坐标换算成物理坐标，可选移动光标，再回读真实落点。

    为什么要有这个工具：精准点击出问题时，很难判断是「坐标基准不对」「DPI 缩放
    没对上」「多屏映射错了」还是「窗口被遮挡」。这里把整条链路拆开验一遍 ——
    图像坐标 → 物理坐标 → 鼠标实际落点 → 该点上的窗口/控件，偏差为 0 就说明
    坐标链路精确。GPU 自绘界面（Blender 一类）没有可访问性树，全靠坐标点击，
    动手前先跑一次自检最稳妥。
    """
    rx, ry = _num(args.get("x")), _num(args.get("y"))
    x, y = _resolve_point(args)
    move = _bool(args.get("move"), True)
    lines = [f"目标点：图像坐标 ({rx},{ry}) → 屏幕物理 ({x},{y})"]
    lines.append(_transform_line())
    if move:
        _move_absolute(x, y)
        time.sleep(0.15)
    ax, ay = _cursor_pos()
    dx, dy = ax - x, ay - y
    lines.append(f"移动后光标实际位置：({ax},{ay})  偏差 ({dx:+d},{dy:+d})"
                 + ("  ✓ 完全一致（坐标链路精确）" if (dx, dy) == (0, 0) else
                    "  ⚠ 有偏差：可能是坐标基准过期（请重新 screen_shot）或缩放/多屏映射异常"))
    under = _owner_window(ax, ay)
    if under:
        kind = window_kind(under)
        cls = win32_class_name(under)
        lines.append(f"该点上的窗口：[{under}] {window_title(under) or '(无标题)'}"
                     f"  <{kind}> {cls}")
    else:
        lines.append("该点上没有窗口（桌面空白处）")
    lines.append("用法：偏差为 0 时即可放心 click；不为 0 时先 screen_shot 刷新坐标基准。")
    _log("ai", f"精度自检 ({x},{y}) 偏差 ({dx:+d},{dy:+d})")
    return "\n".join(lines)


def _cand_menu_lines(cands: list, sx: int, sy: int) -> list:
    """把候选菜单渲染成给模型看的文字（含可直接照抄的 click 调用）。"""
    if not cands:
        return ["候选目标：本区域没有识别到像控件的目标"
                "（可能整片是空白、连续画面，或目标比搜索范围还小）。"]
    lines = [f"候选目标（本区域共 {len(cands)} 个，按离你的估计点由近到远；"
             "下图已用青色方框**编号**框出，当前选中的那个是洋红色）："]
    for c in cands:
        sel = "   ← 当前选中" if (int(c["x"]), int(c["y"])) == (sx, sy) else ""
        lines.append(f"  {c['i']}. {c['kind']}  中心({c['x']},{c['y']})  "
                     f"尺寸 {c['w']}×{c['h']}  距估计点 {c['d']:.0f}px{sel}")
    lines.append("怎么用：在图上按编号确认哪个才是你要点的目标，然后")
    lines.append("  ① 直接点：click(x=上表「中心」的 x, y=它的 y, space=\"screen\", snap=false)")
    lines.append("  ② 或改选：refine_point(x=原估计x, y=原估计y, pick=N, space=\"screen\")")
    lines.append("**务必带 snap=false**：否则 click 会再吸附一次，可能又挪到隔壁那个去。")
    return lines


def tool_refine_point(args: dict) -> list:
    """坐标精修：吸附到附近元素的中心，并把**候选目标编号列出来**让你挑。

    为什么要有候选菜单：吸附只会挑「离估计点最近的那一块」。按钮成排密排的界面
    （Blender 工具栏、剪辑软件属性面板、一排小图标）上，估计点稍微偏一点，最近的那块
    就是**隔壁按钮** —— 于是「点偏」变成「稳稳地点错」，而且你无从察觉。这里把区域里
    所有像控件的目标编号框出来、连同屏幕坐标一起给你：**「估一个位置」变成「挑一个
    编号」**。认出目标是你擅长的，算出目标在哪不是。候选只含几何数字（编号/中心/尺寸），
    不含控件名或可点语义。

    返回一张放大图：黄色准星=你的估计点，绿色准星=最终目标点，青色方框=候选（编号），
    洋红方框=当前选中。放大图是最近邻的，边缘是硬像素方块，可直接按像素读数。

    只读工具：不移动鼠标、不点击。确定后用
    `click(x=…, y=…, space="screen", snap=false)` 点击（**带 snap=false**，
    否则 click 会再吸附一次，可能又挪到隔壁那个去）。
    """
    x, y = _resolve_point(args)
    reach = _num(args.get("reach"), SNAP_REACH_DEFAULT)
    cands = []
    sx, sy, note, ruler = _snap_core(x, y, reach, cands=cands)
    pick = _num(args.get("pick"), 0)
    if pick:
        chosen = next((c for c in cands if c["i"] == pick), None)
        if chosen:
            sx, sy = int(chosen["x"]), int(chosen["y"])
            note = (f"按你指定的 #{pick}（{chosen['kind']}，{chosen['w']}×{chosen['h']}px）"
                    f"取其中心。")
        else:
            note = (f"没有编号 {pick} 的候选（本区域只有 {len(cands)} 个），"
                    f"已改用最近的元素吸附。" + (note or ""))
    moved = (sx, sy) != (x, y)
    lines = [f"原估计点：图像坐标 ({_num(args.get('x'))},{_num(args.get('y'))}) → "
             f"屏幕物理 ({x},{y})", _transform_line()]
    if moved:
        lines.append(f"最终目标点：({sx},{sy})（屏幕物理）")
        lines.append(note or "已吸附到最近元素的中心。")
    else:
        lines.append(f"最终目标点：({sx},{sy})（与你给的一致，未吸附）")
        lines.append(note or "未吸附（附近没有找到可信的界面元素）。")
    if len(ruler) >= 2:
        # 行标尺：这是解决「列表点偏」的关键 —— 模型看图是**估**不出第几行在哪里的，
        # 但这里直接给出本区域每一条内容带的中心 y。纯几何数字，不含元素名/可点信息。
        ys = " / ".join(str(b["y"]) for b in ruler)
        hit = next((i for i, b in enumerate(ruler)
                    if abs(b["y"] - y) <= max(2, b["h"] // 2 + 1)), None)
        lines.append(f"行标尺：本区域检测到 {len(ruler)} 条横向内容带，中心 y = {ys}"
                     f"（屏幕物理坐标）。")
        if hit is None:
            lines.append("⚠ 你给的 y 不在任何一条带上 —— 说明它落在两行之间的空隙里，"
                         "这正是「点偏」最常见的原因。请从上面挑正确的那一个 y 再点"
                         "（x 沿用你给的值即可，列表整行都可点）。")
        else:
            lines.append(f"你给的 y 落在第 {hit + 1} 条带上；要点别的行就换用对应的 y。")
    lines.append("")
    lines += _cand_menu_lines(cands, sx, sy)
    lines.append("下图：黄色准星=你的估计点，绿色准星=最终目标点；放大图为最近邻放大，"
                 "边缘是硬实的像素方块，可直接按像素读数。")
    _log("ai", f"坐标精修 ({x},{y}) → ({sx},{sy})，候选 {len(cands)} 个")

    boxes = []
    for c in cands:
        bx0, by0, bx1, by1 = c["box"]
        sel = (int(c["x"]), int(c["y"])) == (sx, sy)
        boxes.append((bx0, by0, bx1, by1,
                      0x00FF00FF if sel else 0x00FFFF00, str(c["i"])))
    marks = [(x, y, 0x0000FFFF, "估")]
    if moved:
        marks.append((sx, sy, 0x0000CC33, "点"))
    # 放大范围要**覆盖整个搜索区域**（±reach），否则有的候选框落在画面外，编号就对不上了；
    # 所以以「估计点」为中心（候选都在它附近），而不是以吸附点为中心。
    size = max(200, min(2 * reach + 40, 560))
    png, iw, ih, _x0, _y0, region = grab_zoom(
        x, y, size, 3, marks=marks, boxes=boxes)
    return [{"type": "text", "text": "\n".join(lines)},
            {"type": "image", "data": base64.b64encode(png).decode(), "mimeType": "image/png"}]


def _apply_snap(args: dict, x: int, y: int, default_on: bool = True) -> tuple:
    """按 snap 参数决定是否做元素吸附，返回 (x, y, note)。

    snap 取值：
      * 不传 / "auto" / true —— 做吸附；找不到可信元素就**原样返回**（保守，见 _snap_to_element）；
      * false               —— 完全不动坐标（想点一个精确像素、或吸附判断不合意时用）。
    默认开启的理由：模型看图给的是**估计**坐标，缩放后误差会被放大，而吸附只会在
    「附近确实有一个像控件的连通域」时才动手，最坏情况等于没吸附。
    """
    v = args.get("snap")
    on = default_on if v is None else _bool(v, default_on)
    if not on:
        return x, y, ""
    reach = _num(args.get("snap_reach"), SNAP_REACH_DEFAULT)
    return _snap_to_element(x, y, reach)


_CLICK_ACTIONS = {
    "": "click", "click": "click", "tap": "click", "left": "click",
    "double": "double", "double_click": "double", "dblclick": "double",
    "right": "right", "right_click": "right", "context": "right",
    "long": "long_press", "long_press": "long_press",
    "drag": "drag", "swipe": "swipe", "select": "select",
}


def _click_action(args: dict) -> str:
    """click 工具的高级动作：把 action 归一化到 click/double/right/long_press/drag/swipe/select。
    未知值回落 click（工具描述里已列出合法值，宁可当普通点击也不报错卡住流程）。"""
    a = str((args or {}).get("action") or "").strip().lower().replace("-", "_")
    return _CLICK_ACTIONS.get(a, "click")


def _as_drag_args(args: dict) -> dict:
    """把统一 click 的 x/y 起点写法映射成 drag/swipe/select 需要的 x1/y1。
    同时兼容旧调用方直接传 x1/y1。"""
    out = dict(args or {})
    if out.get("x1") is None and out.get("x") is not None:
        out["x1"] = out.get("x")
    if out.get("y1") is None and out.get("y") is not None:
        out["y1"] = out.get("y")
    return out


def tool_click(args: dict) -> str:
    """统一点击入口：普通点击 / 双击 / 右键 / 长按 / 拖拽 / 滑动 / 拖选。

    为什么把这些动作并进 click：模型在任务里最常犯的错不是「不会拖拽」，而是
    「忘记还有 drag 工具」—— 于是把拖拽拆成点击+移动+点击，行为完全不对。
    一个 click 工具带 action，模型只需记住一个入口；底层仍走各自经过精度验证的
    注入实现（do_click/do_long_press/do_drag/do_swipe），不是另写一套。
    """
    action = _click_action(args)
    if action == "long_press":
        return tool_long_press(args)
    if action in ("drag", "swipe", "select"):
        fn = {"drag": tool_drag, "swipe": tool_swipe, "select": tool_select}[action]
        return fn(_as_drag_args(args))
    reason = _guard("click")
    if reason:
        return reason
    x, y = _resolve_point(args)
    x, y, snap_note = _apply_snap(args, x, y)
    button = str(args.get("button") or "left").strip().lower()
    clicks = max(1, min(_num(args.get("clicks"), 1), 3))
    if action == "right":
        button = "right"
        clicks = 1
    elif action == "double":
        button = "left"
        clicks = 2
    verify = _bool(args.get("verify"), False)
    pre = _ensure_click_target(x, y, args)
    # 生效校验要拿目标窗口做「整窗指纹」：显式 hwnd 优先，其次最近一次窗口截图的那个
    hwnd = _num(args.get("hwnd"), 0) or int(_STATE.get("view_target") or 0)
    before = _pre_click_probe(x, y, hwnd) if verify else ()
    if not do_click(x, y, button, clicks, mode=_input_mode(args)):
        _refresh_env()
        return (f"⚠ **没有完成点击**：光标无法稳定停在 ({x},{y})（连续回读不一致，"
                f"当前在 {_cursor_pos()}）。这通常说明**用户正在用真实鼠标**，"
                f"注入被实时覆盖了 —— 此时硬点会落到别的位置（可能误触删除/发送）。\n"
                f"请先请用户停手，或 control(action=\"status\") 查看接管状态；"
                f"**不要重复重试同一坐标**。")
    # 落点回读：click 以前只报「请求的坐标」，一旦注入链路有偏差（多屏归一化/DPI 缩放）
    # 就是**静默点空**，模型还以为点对了。回读 GetCursorPos 让每一次点击都自证落点，
    # 偏差不再是猜出来的。
    ax, ay = _cursor_pos()
    land = ("落点回读：({},{})，与请求完全一致（注入链路 0 偏差）。".format(ax, ay)
            if (ax, ay) == (x, y) else
            f"⚠ 落点回读：光标实际在 ({ax},{ay})，与请求的 ({x},{y}) 相差 "
            f"({ax - x:+d},{ay - y:+d})px —— 注入链路有偏差（多屏归一化 / DPI 缩放），"
            f"请先用 verify_point 自检坐标链路，不要盲目重试。")
    vtext = _click_verify(x, y, before, hwnd)
    label = {"right": "右键", "double": "双击"}.get(action, "点击")
    _log("ai", f"{label} {x},{y}（{button}×{clicks}，鼠标注入），落点 ({ax},{ay})")
    _refresh_env()
    return (f"已{label} ({x},{y}) {button}×{clicks}（屏幕物理坐标，鼠标注入）。"
            + _join_notes(snap_note, pre, land, vtext)
            + "请重新 screen_shot 验证结果。")


def _inject_lost_note(x: int, y: int) -> str:
    """注入动作（拖拽/滑动/长按/拖选）被真实鼠标覆盖时的统一告警文案。"""
    return (f"⚠ **动作没有执行**：光标无法稳定停在 ({x},{y})（当前在 {_cursor_pos()}），"
            f"通常说明**用户正在用真实鼠标**，注入被实时覆盖了。"
            f"请先请用户停手，或 control(action=\"status\") 查看接管状态；"
            f"**不要重复重试同一坐标**。")


def tool_long_press(args: dict) -> str:
    reason = _guard("long_press")
    if reason:
        return reason
    x, y = _resolve_point(args)
    x, y, snap_note = _apply_snap(args, x, y)
    dur = max(0.2, float(_num(args.get("duration"), int(DEFAULT_LONG_PRESS_S * 1000)) / 1000.0))
    pre = _ensure_click_target(x, y, args)
    ok = do_long_press(x, y, dur, mode=_input_mode(args))
    if not ok:
        _refresh_env()
        return _inject_lost_note(x, y)
    _log("ai", f"长按 {x},{y}（{dur:.1f}s，鼠标注入）")
    _refresh_env()
    return (f"已长按 ({x},{y}) {dur:.1f}s。" + _join_notes(snap_note, pre)
            + "请重新 screen_shot 查看是否弹出菜单"
            "（如需右键菜单请用 click(button=\"right\")）。")


def tool_drag(args: dict) -> str:
    reason = _guard("drag")
    if reason:
        return reason
    x1, y1 = _resolve_point(args, "x1", "y1")
    x2, y2 = _resolve_point(args, "x2", "y2")
    # 拖拽两端都吸附：起点要抓住控件中心（抓偏了会拖空），终点要落在目标控件上
    x1, y1, n1 = _apply_snap(args, x1, y1)
    x2, y2, n2 = _apply_snap(args, x2, y2)
    dur = max(0.1, _num(args.get("duration"), 500) / 1000.0)
    pre = _ensure_click_target(x1, y1, args)
    if not do_drag(x1, y1, x2, y2, dur, mode=_input_mode(args)):
        _refresh_env()
        return _inject_lost_note(x1, y1)
    _log("ai", f"拖拽 ({x1},{y1}) → ({x2},{y2})")
    _refresh_env()
    return (f"已从 ({x1},{y1}) 拖拽到 ({x2},{y2})。"
            + _join_notes(n1, n2, pre) + "请重新 screen_shot 验证结果。")


def tool_swipe(args: dict) -> str:
    reason = _guard("swipe")
    if reason:
        return reason
    x1, y1 = _resolve_point(args, "x1", "y1")
    x2, y2 = _resolve_point(args, "x2", "y2")
    # 滑动默认**不**吸附：手势是划过一片内容，两端没有「元素中心」可言
    x1, y1, n1 = _apply_snap(args, x1, y1, default_on=False)
    x2, y2, n2 = _apply_snap(args, x2, y2, default_on=False)
    dur = max(0.08, _num(args.get("duration"), 260) / 1000.0)
    pre = _ensure_click_target(x1, y1, args)
    if not do_swipe(x1, y1, x2, y2, dur, mode=_input_mode(args)):
        _refresh_env()
        return _inject_lost_note(x1, y1)
    _log("ai", f"滑动 ({x1},{y1}) → ({x2},{y2})")
    _refresh_env()
    if _touch_allowed(_input_mode(args)):
        return (f"已从 ({x1},{y1}) 滑动到 ({x2},{y2})（触控滑动，系统会带惯性）。"
                + _join_notes(n1, n2, pre)
                + "请重新 screen_shot 看滚动结果；滚动窗口内容也可直接用 scroll。")
    return (f"已从 ({x1},{y1}) 滑动到 ({x2},{y2})（鼠标拖动）。"
            + _join_notes(n1, n2, pre) + "滚动窗口内容建议改用 scroll(delta)。")


def tool_select(args: dict) -> str:
    reason = _guard("select")
    if reason:
        return reason
    x1, y1 = _resolve_point(args, "x1", "y1")
    x2, y2 = _resolve_point(args, "x2", "y2")
    # 选择默认**不**吸附：选区两端通常落在文字/内容的任意位置，吸附到「元素中心」反而错
    x1, y1, n1 = _apply_snap(args, x1, y1, default_on=False)
    x2, y2, n2 = _apply_snap(args, x2, y2, default_on=False)
    pre = _ensure_click_target(x1, y1, args)
    if not do_drag(x1, y1, x2, y2, max(0.2, _num(args.get("duration"), 420) / 1000.0), hold=0.05):
        _refresh_env()
        return _inject_lost_note(x1, y1)
    _log("ai", f"选择区域 ({x1},{y1}) → ({x2},{y2})")
    _refresh_env()
    return (f"已拖选区域 ({x1},{y1}) → ({x2},{y2})，随后可用 copy 复制选中内容。"
            + _join_notes(pre))


def tool_scroll(args: dict) -> str:
    reason = _guard("scroll")
    if reason:
        return reason
    delta = _num(args.get("delta"), 0)
    if not delta:
        raise ValueError("delta 不能为 0（正值向上、负值向下，一格 120）")
    if args.get("x") is not None and args.get("y") is not None:
        x, y = to_physical(args.get("x"), args.get("y"))
        _move_human(x, y)
    delta = max(-1200, min(1200, delta))
    do_scroll(delta)
    _log("ai", f"滚轮 {delta:+d}")
    _refresh_env()
    return f"已滚动 {delta:+d}（{'向上' if delta > 0 else '向下'} {abs(delta) // 120 or 1} 格）。请 screen_shot 验证。"


def tool_type_text(args: dict) -> str:
    reason = _guard("type_text")
    if reason:
        return reason
    text = str(args.get("text") if args.get("text") is not None else "")
    if not text:
        raise ValueError("text 为空，无可输入内容")
    if len(text) > 2000:
        text = text[:2000]
    note = _ensure_input_target(args, "输入文本")
    tgt = _input_target_hwnd(args)
    fg = foreground_window()
    do_type_text(text)
    # 发送：输入完直接回车，省掉「移动鼠标去点发送按钮」这一步（也避免点偏）。
    # 先停一下让输入法/输入框把上屏处理完，否则回车可能被当成输入法候选上屏。
    submit = _bool(args.get("submit"), False)
    if submit:
        time.sleep(max(0.08, _num(args.get("submit_delay"), 120) / 1000.0))
        do_key("enter")
    _log("ai", f"输入文本（{len(text)} 字符）" + ("并回车发送" if submit else "") + f"：{text[:60]}")
    _refresh_env()
    # 关于「文字到底进了哪里」：**不做整窗像素比对** —— 实测输入一个字符只让窗口
    # 指纹变化 1~2 个采样点，与光标闪烁同量级，据此报警必然是误报，反而会害模型
    # 重复输入。这里只如实报告「注入时的前台窗口」，由模型自己截图核对。
    where = f"注入时前台窗口：「{window_title(fg) or fg}」"
    if tgt and fg != tgt:
        where = (f"⚠ 注入时前台窗口是「{window_title(fg) or fg}」而不是目标窗口"
                 f"「{window_title(tgt) or tgt}」—— 请立刻截图核对文字去了哪里，不要重复输入")
    return (f"已输入 {len(text)} 个字符（{where}）"
            + ("，并按 Enter 发送（无需再移动鼠标点发送按钮）" if submit else "")
            + "。" + _join_notes(note)
            + "请重新 screen_shot 确认输入是否正确。")


def tool_key_press(args: dict) -> str:
    reason = _guard("key_press")
    if reason:
        return reason
    key = str(args.get("key") or "").strip()
    if not key:
        raise ValueError("key 为空（如 enter / ctrl+c / win+ctrl+d）")
    # win 开头的组合键是系统级快捷键（如 win+ctrl+d 切虚拟桌面），不绑定窗口；
    # 其余按键只对前台窗口生效，必须先把目标窗口切到前台，否则会打进别的应用。
    note = "" if key.lower().startswith("win") else _ensure_input_target(args, f"发送按键 {key}")
    fg = foreground_window()
    do_key(key)
    _log("ai", f"按键 {key}")
    _refresh_env()
    return (f"已发送按键 {key}（发往窗口：{window_title(fg) or fg}）。" + _join_notes(note)
            + "请重新 screen_shot 验证结果。")


def tool_copy(args: dict) -> str:
    reason = _guard("copy")
    if reason:
        return reason
    note = _ensure_input_target(args, "复制")
    do_key("ctrl+c")
    time.sleep(0.18)
    text = clipboard_get()
    _log("ai", f"复制（剪贴板 {len(text)} 字符）")
    if not text:
        return ("已发送 Ctrl+C，但剪贴板为空（可能没有选中内容）。" + _join_notes(note))
    return (f"已复制，剪贴板内容（{len(text)} 字符）：\n{text[:1500]}" + _join_notes(note))


def tool_paste(args: dict) -> str:
    reason = _guard("paste")
    if reason:
        return reason
    text = args.get("text")
    if text not in (None, "") and not clipboard_set(str(text)):
        raise RuntimeError("写入剪贴板失败")
    note = _ensure_input_target(args, "粘贴")
    do_key("ctrl+v")
    time.sleep(0.15)
    cur = clipboard_get()
    _log("ai", f"粘贴（剪贴板 {len(cur)} 字符）")
    return (f"已发送 Ctrl+V 粘贴。剪贴板内容：{cur[:300] or '(空)'}。" + _join_notes(note)
            + "请重新 screen_shot 验证。")


def tool_open_virtual_desktop(args: dict) -> str:
    action = str(args.get("action") or "new").strip().lower()
    # 返回用户桌面（back/prev）不受接管/暂停拦截：它是**把控制权还给用户**的收尾动作，
    # 拦下会把用户留在空虚拟桌面上（AI 明明该退场却退不出去）。
    if action not in ("back", "prev"):
        reason = _guard("open_virtual_desktop")
        if reason:
            return reason
    combo = switch_virtual_desktop(action)
    if action == "new":
        _STATE["container"] = "virtual_desktop"
        msg = ("已新建并切入独立虚拟桌面（AI 操控容器）：当前桌面内没有用户原有窗口，"
               "请先在此桌面打开/操作目标应用。任务完成后必须调用 "
               "open_virtual_desktop(action=\"back\") 返回用户桌面。")
    elif action in ("back", "prev"):
        _STATE["container"] = "foreground"
        msg = "已返回用户桌面（操控容器切回前台）。"
    else:
        msg = f"已切换到相邻虚拟桌面（{combo}）。"
    _log("ai", f"虚拟桌面 {action}（{combo}）")
    _refresh_env()
    return msg + " 请重新 screen_shot 确认当前桌面内容。"


def tool_control(args: dict) -> str:
    action = str(args.get("action") or "status").strip().lower()
    if action == "status":
        _refresh_env()
        paused = _STATE["paused"]
        intr = _STATE["interrupted"]
        return (f"操控状态: {'已暂停' if paused else ''}{'已接管' if intr else ''}"
                f"{'正常' if not (paused or intr) else ''}；"
                f"容器: {'虚拟桌面' if _STATE['container'] == 'virtual_desktop' else '前台桌面'}；"
                f"输入: {'触控' if _STATE['input'] == 'touch' else '鼠标'}；"
                f"会话剩余 {max(0, int(_STATE['active_until'] - time.time()))}s")
    if action == "take_over":
        mark_interrupted("AI 主动交还控制权")
        _log("ai", "主动交还控制权给用户")
        return "已交还控制权：后续操控工具将被拒绝，直到用户明确要求继续。"
    if action == "resume":
        _STATE["interrupted"] = False
        _STATE["paused"] = False
        _touch_session()
        _log("ai", "恢复操控（已确认用户要求继续）")
        return "已清除接管/暂停标记，可继续操控。"
    raise ValueError(f"未知动作: {action}（可用 status/take_over/resume）")


def tool_web_url(args: dict) -> str:
    return (f"可视化界面: http://127.0.0.1:{_STATE['web_port']}/ —— "
            "请用 preview_open 在用户默认浏览器里打开给用户，用户可在该界面实时观看操控画面、查看操作日志，"
            "并可一键「接管 / 暂停 / 恢复」。")


# ============================================================================
# 窗口目标解析（供坐标类工具共用）
# ============================================================================

def _target_hwnd(args: dict, required: bool = True) -> int:
    """解析目标窗口：显式 hwnd > title > 当前前台窗口。"""
    h = find_window(hwnd=_num(args.get("hwnd"), 0))
    if h:
        return h
    title = str(args.get("title") or "").strip()
    if title:
        h = find_window(title=title)
        if h:
            return h
    if not required:
        return foreground_window()
    fg = foreground_window()
    if fg:
        return fg
    raise ValueError("未找到目标窗口：请用 list_windows 查看并传 title/hwnd")


def _is_popup(hwnd: int) -> bool:
    """目标窗口是不是弹出菜单（对菜单不能做激活操作，否则菜单会被关掉）。"""
    try:
        return window_kind(int(hwnd or 0)) == "menu"
    except Exception:
        return False


def _owner_window(x, y) -> int:
    """屏幕物理坐标 (x,y) 处最顶层的窗口句柄（0 = 取不到）。"""
    try:
        h = user32.WindowFromPoint(wintypes.POINT(int(x), int(y)))
        return int(h) if h else 0
    except Exception:
        return 0


def _join_notes(*notes) -> str:
    """拼接若干条提示（跳过空串，统一补句号），供工具返回值原样带给模型。"""
    parts = [str(n).strip() for n in notes if str(n or "").strip()]
    if not parts:
        return ""
    return "".join(p if p.endswith(("。", "！", "？")) else p + "。" for p in parts) + " "


def _focus_warning(hwnd: int) -> str:
    """目标窗口未真正成为前台时给出风险提示（避免点进错误窗口而不自知）。"""
    hwnd = int(hwnd or 0)
    if not hwnd:
        return ""
    fg = foreground_window()
    if fg and fg != hwnd:
        name = window_title(fg) or fg
        return (f"注意：目标窗口未成为前台（当前前台是「{name}」），"
                f"点击可能未落在目标窗口上，请重新 screen_shot 确认。")
    return ""


def _prepare_target(hwnd: int) -> str:
    """点击前的窗口准备：非菜单窗口才激活并返回焦点风险提示。"""
    if not hwnd or _is_popup(hwnd):
        return ""
    activate_window(hwnd)
    return _focus_warning(hwnd)


def _hit_check(hwnd: int, x: int, y: int) -> str:
    """点完之后校验命中：该屏幕坐标上最顶层的窗口是不是目标窗口。

    最常见的「点了没反应」其实是**点到别的窗口上去了**（目标没激活、被遮挡、
    或坐标算错落到了标题栏/任务栏）。这里按祖先链判断，命中别的窗口就明说，
    免得模型反复重试同一个错误坐标。
    """
    hwnd = int(hwnd or 0)
    if not hwnd:
        return ""
    try:
        hit = int(window_at(x, y) or 0)
    except Exception:
        return ""
    if not hit or hit == hwnd:
        return ""

    def _ancestors(h: int) -> set:
        seen = set()
        for _ in range(12):
            if not h or h in seen:
                break
            seen.add(h)
            try:
                h = int(user32.GetAncestor(h, GA_ROOT) or user32.GetParent(h) or 0)
            except Exception:
                break
        return seen

    if _ancestors(hit) & _ancestors(hwnd):
        return ""
    try:
        who = window_title(hit)
    except Exception:
        who = ""
    try:
        kind = window_kind(hit)
    except Exception:
        kind = ""
    name = f"「{who}」" if who else f"hwnd={hit}"
    tip = "（看起来是弹出菜单/浮层，请确认它就是要点的目标）" if kind == "menu" else ""
    return (f"注意：({x},{y}) 处最顶层窗口是 {name}{tip}，不是目标窗口 —— "
            f"本次点击可能落在了别的窗口上，建议先 open_app/activate 目标再重试。")

# ============================================================================
# 窗口 / 应用管理工具
# ============================================================================
def tool_open_app(args: dict) -> str:
    """打开 / 切到某个应用：**一次调用**完成「找到它 → 没运行就启动 → 等窗口 → 恢复最小化 → 置顶 → 校验」。

    用户说「打开微信」「切到 XX」「XX 在后台/最小化了」时**先调这个**，不要用
    list_windows 反复找窗口 —— 那正是把「打开微信发个消息」拖成几十次工具调用、
    甚至绕不出来的原因。返回值里直接给出 hwnd 与就绪状态，后续 screen_shot(mode="window",
    hwnd=…) / click_window 直接用这个 hwnd 即可。

    name 用用户的原话（中文口语名也行，如「微信」「记事本」）；插件自己按
    开始菜单 + 注册表 App Paths 建的索引做模糊匹配。
    """
    name = str(args.get("name") or args.get("app") or args.get("title") or "").strip()
    if not name:
        raise ValueError("请传 name（应用名，例如 name=\"微信\"）")
    reason = _guard("open_app")
    if reason:
        return reason
    keys = _app_keys(name)
    tmo = max(2.0, _num(args.get("timeout"), 15000) / 1000.0)
    steps = []

    hwnd, title, how = _find_app_window(keys)
    if hwnd:
        steps.append(f"「{name}」已经在运行（按{how}认到窗口 [{hwnd}] {title or '（无标题）'}）")
    else:
        res = _resolve_app(name)
        if not res.get("target"):
            cand = "、".join(res.get("candidates") or []) or "（应用索引为空）"
            raise RuntimeError(
                f"没找到名为「{name}」的应用，它当前也没有运行中的窗口。\n"
                f"已安装应用里名字最接近的是：{cand}\n"
                f"请从这些**真实名字**里挑一个重试；若列表里确实没有，说明它没装，"
                f"请先安装，或用文件工具找到它的可执行文件路径后让用户确认。")
        before = {w["hwnd"] for w in list_windows()}
        _log("ai", f"启动应用「{res['display']}」← {res['target']}")
        launch_app(res["target"])
        steps.append(f"原本没在运行，已启动「{res['display']}」"
                     f"（匹配度 {res['score']:.2f}，来源 {res['kind']}）")
        hwnd, title, how = _wait_app_window(keys, timeout=tmo)
        if not hwnd:
            # 兜底：启动的可能是个引导器/启动台，标题与进程名都对不上 ——
            # 那就认「启动后新出现的窗口」，但要如实说明是这么认的。
            hwnd, title, how = _wait_app_window((), timeout=4.0,
                                                exclude=before, any_new=True)
            if hwnd:
                steps.append(f"⚠ 没等到名字匹配的窗口，按「启动后新出现的窗口」"
                             f"认作目标：[{hwnd}] {title or '（无标题）'}（请核对是不是它）")
        if not hwnd:
            raise RuntimeError(
                f"已启动「{res['display']}」（{res['target']}），"
                f"但 {tmo:.0f}s 内没等到它的窗口出现。\n"
                f"可能原因：① 应用启动较慢；② 启动后直接最小化到了托盘；"
                f"③ 弹了需要人工处理的登录/更新框；"
                f"④ 该目标只是个**商店应用的转发壳**（如 System32 下的 calc.exe），"
                f"而对应的商店应用没安装 —— 此时启动会「成功」但什么都不会发生。\n"
                f"请用 list_windows 看看现在到底有哪些窗口；"
                f"确认应用已安装后可加大 timeout 重试。")

    ok, note = _bring_to_front(hwnd)
    if note:
        steps.append(note)
    _log("ai", f"open_app：{name} → hwnd={hwnd} 前台={ok}")
    r = window_rect(hwnd)
    w, h = r.right - r.left, r.bottom - r.top
    head = (f"✅ 「{title or name}」已就绪：hwnd={hwnd}，"
            f"位置 {r.left},{r.top}，尺寸 {w}×{h}，"
            f"{'已在前台' if ok else '⚠ 不在前台'}")
    body = "\n".join(f"  · {s}" for s in steps)
    if not ok:
        head += ("\n（系统拒绝了置顶，后续坐标点击有落到别的窗口上的风险 —— 先用 "
                 "taskbar_open(index=N) 点一下它的任务栏图标（通常一步就能切过去），"
                 "还不行再请用户手动切一下。）")
    return (f"{head}\n{body}\n"
            f"下一步：用 screen_shot(mode=\"window\", hwnd={hwnd}) 建立坐标基准，"
            f"再用 zoom_in 放大细看、click 按坐标点击 —— **不要**再重复 list_windows 找窗口，"
            f"也**不要**再调 open_app/manage_window 反复置顶。")


def tool_manage_window(args: dict) -> str:
    """窗口管理：activate/maximize/minimize/restore/move/close/state。"""
    action = str(args.get("action") or "state").strip().lower()
    if action in ("take_over", "resume"):
        return tool_control(args)          # 语义互通，避免模型记错工具
    if action in ("focus", "front"):
        reason = _guard("manage_window")
        if reason:
            return reason
        hwnd = _target_hwnd(args)
        ok, note = _bring_to_front(hwnd)
        _log("ai", f"manage_window focus [{hwnd}] 前台={ok}")
        return (f"窗口「{window_title(hwnd) or hwnd}」：{note or '已在前台'}"
                + ("" if ok else " —— 抢不到前台，后续点击可能落空，建议让用户手动切换。"))
    reason = _guard("manage_window")
    if reason:
        return reason
    if action in ("state", "list"):
        # 与 list_windows 共用同一套渲染（含最小化标记），避免两种格式让模型混淆
        return "当前窗口列表:\n" + tool_list_windows({"limit": 30})
    hwnd = _target_hwnd(args, required=(action != "wait"))
    if action == "wait":
        h = wait_for_window(title=str(args.get("title") or ""), timeout=_num(args.get("timeout"), 10000) / 1000.0)
        if not h:
            raise RuntimeError(f"等待超时：未出现标题含「{args.get('title')}」的窗口")
        return f"窗口已出现：[{h}] {window_title(h)}"
    if action == "activate":
        ok, note = _bring_to_front(hwnd)
        _log("ai", f"激活窗口 [{hwnd}] {window_title(hwnd)} 前台={ok}")
        name = window_title(hwnd) or hwnd
        if ok:
            return f"已把窗口「{name}」切到前台。" + (f"（{note}）" if note else "")
        # 诚实汇报：置顶被系统拒绝时通常有前置窗口（如锁屏/UAC/安全桌面）
        # 抢占焦点，此时后续坐标点击可能落到别的窗口上，必须让模型知道。
        return (f"已请求把窗口「{name}」切到前台，但{note}。"
                f"继续点击有落到错误窗口的风险，建议先处理前置窗口"
                f"（如用 manage_window 列出窗口确认），或请用户手动切到目标窗口。")
    if action == "move":
        return move_window(hwnd, to_physical(args.get("x"), args.get("y"))[0],
                           to_physical(args.get("x"), args.get("y"))[1],
                           _num(args.get("w"), 0), _num(args.get("h"), 0))
    if action in ("maximize", "minimize", "restore", "show", "close"):
        return set_window_state(hwnd, action)
    raise ValueError(f"未知动作: {action}（可用 state/list/activate/focus/maximize/"
                     f"minimize/restore/move/close/wait）")


def tool_aura(args: dict) -> str:
    """屏幕边缘彩色光环控制（默认由操控自动点亮，此工具用于手动开关）。"""
    if aura is None:
        raise RuntimeError("光环模块未加载")
    action = str(args.get("action") or "state").strip().lower()
    if action == "on":
        aura.set_aura_active(True)
        _STATE["aura"] = True
    elif action == "off":
        aura.set_aura_active(False)
        _STATE["aura"] = False
    st = aura.aura_state()
    return f"屏幕光环: {'显示中' if st['active'] else '已隐藏'}；{st['capability']}"


TOOLS = [
    {"name": "screen_info", "description":
        "查看电脑操控环境：屏幕物理分辨率、输入方式（触控/鼠标）、操控容器（前台/虚拟桌面）、"
        "前台窗口、鼠标位置、接管与暂停状态。开始操控前先调用。",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "screen_shot", "description":
        "截屏并返回**纯净原图**给你查看（不叠加坐标网格/刻度/准星），直接看图判断目标位置。"
        "坐标基准=图像像素（左上角 0,0），点击/拖拽等工具的 x/y 直接填你在图上读到的像素值即可"
        "（插件自动换算为屏幕物理坐标）。mode=full 整屏（默认）；mode=window 只截某个窗口"
        "（配合 list_windows 的 title/hwnd）。每次行动前后都应截屏：先看准目标，行动后再验证结果。",
     "inputSchema": {"type": "object", "properties": {
         "mode": {"type": "string", "description": "full=整屏（默认），window=指定窗口"},
         "title": {"type": "string", "description": "窗口标题片段（mode=window 时用）"},
         "hwnd": {"type": "integer", "description": "窗口句柄（mode=window 时用，来自 list_windows）"},
         "grid": {"type": "boolean", "description": "是否叠加坐标网格刻度（默认 false；仅人工读数/调试时开启）"},
         "capture": {"type": "string", "description": "抓图方式：auto（默认，PrintWindow 抓到空白时自动改用屏幕抓取）；printwindow=不受遮挡；screen=屏幕区域（GPU 自绘/独占表面界面如 Blender 用这个）"}}}},
    {"name": "zoom_in", "description":
        "放大屏幕局部以精确定位（两步定位第二步）：x/y 为当前截图基准坐标，"
        "size 为放大区域边长（默认 400），factor 为放大倍数（默认 3）。"
        "返回**纯净放大图**（不叠加网格/刻度/准星），直接看清像素级细节。"
        "放大走**最近邻**（边缘是硬实的像素方块），所以图上读到的像素与屏幕像素一一对应 —— "
        "点小目标（工具栏按钮/菜单项）先放大到接近 1:1 再读数，比在全屏缩略图上估准得多。",
     "inputSchema": {"type": "object", "properties": {
         "x": {"type": "integer", "description": "中心 x（当前截图坐标）"},
         "y": {"type": "integer", "description": "中心 y（当前截图坐标）"},
         "size": {"type": "integer", "description": "放大区域边长，默认 400"},
         "factor": {"type": "integer", "description": "放大倍数，默认 3"},
         "grid": {"type": "boolean", "description": "是否叠加坐标网格刻度（默认 false，纯净放大）"},
         "cursor": {"type": "boolean", "description": "是否叠加鼠标准星（默认 false）"},
         "smooth": {"type": "boolean", "description": "是否平滑插值放大（默认 false=最近邻，像素边缘硬实、便于精确读数）"},
         "space": {"type": "string", "description": "x/y 的坐标系：image（默认，当前截图基准）；screen=直接给屏幕物理坐标"}},
         "required": ["x", "y"]}},
    {"name": "list_windows", "description":
        "列出当前可见窗口（句柄/类别/标题/位置/尺寸/进程/类名），用于选择操控目标。"
        "**包含无标题的桌宠、悬浮球等工具窗口（类别=无标题浮层）与弹出菜单（类别=弹出菜单）**"
        "—— 这类窗口没有标题，过去列不出来，只能靠这里拿到的 hwnd 定位。"
        "kind 可只看某一类：main（常规窗口）/menu（弹出菜单）/overlay（无标题浮层）。",
     "inputSchema": {"type": "object", "properties": {
         "kind": {"type": "string", "description": "可选：main/menu/overlay，只看某一类"}}}},
    {"name": "click_window", "description":
        "在目标窗口内按**窗口相对坐标**点击：rel_x/rel_y 相对窗口左上角（物理像素），"
        "窗口移动/换位置也不会点错。适合没有可访问性信息、只能用坐标点的窗口"
        "（自绘浮层、游戏画面等）。不传 rel_x/rel_y 则点窗口正中心 —— "
        "要呼出右键菜单就 button=\"right\"。",
     "inputSchema": {"type": "object", "properties": {
         "title": {"type": "string", "description": "窗口标题片段"},
         "hwnd": {"type": "integer", "description": "窗口句柄（来自 list_windows）"},
         "rel_x": {"type": "integer", "description": "相对窗口左上角的 x（物理像素）"},
         "rel_y": {"type": "integer", "description": "相对窗口左上角的 y（物理像素）"},
         "button": {"type": "string", "description": "left（默认）/right/middle"},
         "clicks": {"type": "integer", "description": "点击次数，默认 1"},
         "input": {"type": "string", "description": "注入方式：默认 auto=鼠标（像素级精准）；touch=真实触控（仅触控优先的界面才用）"},
         "verify": {"type": "boolean", "description": "点击前后比对落点像素，报告是否真的响应（默认 false）"},
         "snap": {"type": "boolean", "description": "是否把 rel 坐标吸附到最近控件的中心（默认 true；想点精确像素传 false）"},
         "snap_reach": {"type": "integer", "description": "吸附搜索半径像素，默认 88（估计偏得很大时可加大到 140）"}}}},
    {"name": "verify_point", "description":
        "**点击精度自检**：把图像坐标换算成屏幕物理坐标，把光标移过去，再回读真实落点，"
        "报告偏差以及该点上的窗口/控件。偏差为 0 说明「图像坐标→物理坐标→鼠标落点」"
        "整条链路精确。在 GPU 自绘界面（Blender/CAD/剪辑软件等无元素信息的界面）上"
        "动手前先跑一次最稳妥；点击落点总是不对时也先用它定位问题。"
        "**只验坐标链路，不做元素吸附** —— 要修正到控件中心请用 refine_point。",
     "inputSchema": {"type": "object", "properties": {
         "x": {"type": "integer", "description": "x 坐标（截图基准）"},
         "y": {"type": "integer", "description": "y 坐标（截图基准）"},
         "move": {"type": "boolean", "description": "是否真的把光标移过去，默认 true"},
         "space": {"type": "string", "description": "x/y 的坐标系：image（默认，当前截图基准）；screen=直接给屏幕物理坐标"}},
         "required": ["x", "y"]}},
    {"name": "refine_point", "description":
        "**坐标精修（强烈建议在 click 前用）**：你从截图上估出的坐标是「估」的 —— 屏幕缩放到 "
        "1280 宽后，图上 1px 误差在 4K 屏上就是 3px，而 Blender/剪辑软件里按钮只有 20px 高，"
        "差 3px 就点到隔壁。这个工具用**纯像素算法**（不读控件树，故对 GPU 自绘界面同样有效）"
        "把区域里所有「像控件的目标」**编号框出来**并列出它们各自的屏幕坐标，同时给出一张"
        "放大图（青色方框=候选编号、黄色准星=你的估计点、洋红方框=当前选中）。"
        "**按钮成排密排的界面（工具栏/属性面板/一排小图标）一定要用它**：吸附只会挑离你最近"
        "的那一个，估计偏一点就会稳稳点到隔壁，而这里你可以直接挑编号 —— 把「估位置」变成「认目标」。"
        "拿到确认的坐标后，用 click(x=…, y=…, space=\"screen\", snap=false) 点击"
        "（**必须带 snap=false**，否则 click 会再吸附一次，可能又挪到隔壁那个去）。",
     "inputSchema": {"type": "object", "properties": {
         "x": {"type": "integer", "description": "你估计的 x（截图基准）"},
         "y": {"type": "integer", "description": "你估计的 y（截图基准）"},
         "reach": {"type": "integer", "description": "搜索半径像素，默认 88（估计得越不准越要加大，最大 140）"},
         "pick": {"type": "integer", "description": "指定候选菜单里的编号 N，直接取第 N 个候选的中心（不填=取最近的那个）"},
         "space": {"type": "string", "description": "x/y 的坐标系：image（默认，当前截图基准）；screen=直接给屏幕物理坐标"}},
         "required": ["x", "y"]}},
    {"name": "click", "description":
        "鼠标操作统一入口（x/y 为当前截图基准坐标）。用 action 选动作（只需记住这一个工具）："
        "click=单击（默认）/double=双击/right=右键/long_press=长按（可呼出上下文菜单）/"
        "drag=拖拽（起点 x,y → 终点 x2,y2，移动文件、拖滑块、拖窗口）/swipe=快速滑动（滚列表、翻页）/"
        "select=拖选文本或区域（随后可用 copy）。"
        "**默认会自动做元素吸附**：把坐标修正到附近那个控件的几何中心（纯像素连通域判定，"
        "不依赖控件树，对 Blender 等自绘界面同样有效），返回值里会说明有没有吸附、位移多少。"
        "想点一个精确像素（如三维视口里的某点）传 snap=false。"
        "拖拽与滑动动作顺带支持 duration（毫秒）与 input=touch。",
     "inputSchema": {"type": "object", "properties": {
         "x": {"type": "integer", "description": "x 坐标（截图基准）；action=drag/swipe/select 时为起点"},
         "y": {"type": "integer", "description": "y 坐标（截图基准）；action=drag/swipe/select 时为起点"},
         "action": {"type": "string", "description": "click（默认）/double/right/long_press/drag/swipe/select"},
         "x2": {"type": "integer", "description": "action=drag/swipe/select 时的终点 x（截图基准）"},
         "y2": {"type": "integer", "description": "action=drag/swipe/select 时的终点 y（截图基准）"},
         "duration": {"type": "integer", "description": "长按时长/拖拽时长/滑动时长毫秒（长按默认 800、拖拽 500、滑动 260）"},
         "button": {"type": "string", "description": "left（默认）/right/middle"},
         "clicks": {"type": "integer", "description": "点击次数，默认 1（action=double 时自动为 2）"},
         "input": {"type": "string", "description": "注入方式：默认 auto=鼠标（像素级精准）；touch=真实触控（触控优先的界面才用，实测落点偏差不可控）"},
         "verify": {"type": "boolean", "description": "点击前后比对落点像素，报告界面是否真的响应了（默认 false）"},
         "snap": {"type": "boolean", "description": "是否自动吸附到最近控件的中心（默认 true；点精确像素、滑动/拖选内容时传 false）"},
         "snap_reach": {"type": "integer", "description": "吸附搜索半径像素，默认 88（估计偏差较大时可加大到 140）"},
         "space": {"type": "string", "description": "x/y 的坐标系：image（默认，当前截图基准）；screen=直接给屏幕物理坐标"},
         "title": {"type": "string", "description": "目标窗口标题片段（可选；填了就在该窗口内点击，并自动把它激活置顶）"},
         "hwnd": {"type": "integer", "description": "目标窗口句柄（可选，来自 list_windows）"}},
         "required": ["x", "y"]}},
    {"name": "long_press", "description":
        "长按指定位置：触控设备为真实触控长按（可呼出上下文菜单），无触控时为按住鼠标不动"
        "（需要右键菜单请用 click(button=\"right\")）。默认同样做元素吸附。",
     "inputSchema": {"type": "object", "properties": {
         "x": {"type": "integer", "description": "x 坐标（截图基准）"},
         "y": {"type": "integer", "description": "y 坐标（截图基准）"},
         "duration": {"type": "integer", "description": "按住时长毫秒，默认 800"},
         "snap": {"type": "boolean", "description": "是否自动吸附到最近控件的中心（默认 true）"},
         "snap_reach": {"type": "integer", "description": "吸附搜索半径像素，默认 88"},
         "space": {"type": "string", "description": "x/y 的坐标系：image（默认）；screen=屏幕物理坐标"},
         "input": {"type": "string", "description": "注入方式：默认 auto=鼠标（像素级精准）；touch=真实触控（仅触控优先的界面才用）"}},
         "required": ["x", "y"]}},
    {"name": "drag", "description":
        "拖拽：从 (x1,y1) 按住拖到 (x2,y2)，用于移动文件/图标、拖滑块、拖动窗口等。"
        "**两端都默认做元素吸附**（起点抓控件中心才不会拖空，终点落在目标控件上）；"
        "拖动三维视口/画布这类没有明确控件的区域时传 snap=false。",
     "inputSchema": {"type": "object", "properties": {
         "x1": {"type": "integer", "description": "起点 x（截图基准）"},
         "y1": {"type": "integer", "description": "起点 y（截图基准）"},
         "x2": {"type": "integer", "description": "终点 x（截图基准）"},
         "y2": {"type": "integer", "description": "终点 y（截图基准）"},
         "duration": {"type": "integer", "description": "拖拽时长毫秒，默认 500"},
         "snap": {"type": "boolean", "description": "是否自动吸附到最近控件的中心（默认 true；拖画布/视口内容传 false）"},
         "snap_reach": {"type": "integer", "description": "吸附搜索半径像素，默认 88"},
         "space": {"type": "string", "description": "x1/y1/x2/y2 的坐标系：image（默认）；screen=屏幕物理坐标"},
         "input": {"type": "string", "description": "注入方式：默认 auto=鼠标（像素级精准）；touch=真实触控（仅触控优先的界面才用）"}},
         "required": ["x1", "y1", "x2", "y2"]}},
    {"name": "swipe", "description":
        "滑动：从 (x1,y1) 快速滑到 (x2,y2)。触控设备带系统惯性，适合滚动列表/翻页/下拉；"
        "无触控设备退化为鼠标拖动（滚动内容建议改用 scroll）。**默认不做元素吸附**"
        "（手势是划过一片内容，两端没有「元素中心」可言）。",
     "inputSchema": {"type": "object", "properties": {
         "x1": {"type": "integer", "description": "起点 x（截图基准）"},
         "y1": {"type": "integer", "description": "起点 y（截图基准）"},
         "x2": {"type": "integer", "description": "终点 x（截图基准）"},
         "y2": {"type": "integer", "description": "终点 y（截图基准）"},
         "duration": {"type": "integer", "description": "滑动时长毫秒，默认 260"},
         "snap": {"type": "boolean", "description": "是否吸附到最近控件中心（默认 false）"},
         "space": {"type": "string", "description": "坐标系：image（默认）；screen=屏幕物理坐标"},
         "input": {"type": "string", "description": "注入方式：默认 auto=鼠标（像素级精准）；touch=真实触控（仅触控优先的界面才用）"}},
         "required": ["x1", "y1", "x2", "y2"]}},
    {"name": "select", "description":
        "选择：从 (x1,y1) 拖选到 (x2,y2) 选中文本/区域（随后可用 copy 复制）。"
        "**默认不做元素吸附**（选区两端通常落在文字/内容的任意位置）。",
     "inputSchema": {"type": "object", "properties": {
         "x1": {"type": "integer", "description": "起点 x（截图基准）"},
         "y1": {"type": "integer", "description": "起点 y（截图基准）"},
         "x2": {"type": "integer", "description": "终点 x（截图基准）"},
         "y2": {"type": "integer", "description": "终点 y（截图基准）"},
         "snap": {"type": "boolean", "description": "是否吸附到最近控件中心（默认 false）"},
         "space": {"type": "string", "description": "坐标系：image（默认）；screen=屏幕物理坐标"},
         "input": {"type": "string", "description": "注入方式：默认 auto=鼠标（像素级精准）；touch=真实触控（仅触控优先的界面才用）"}},
         "required": ["x1", "y1", "x2", "y2"]}},
    {"name": "scroll", "description":
        "滚轮滚动：delta 正值向上、负值向下（120 为一格）；可传 x/y 先把指针移到该处再滚动。",
     "inputSchema": {"type": "object", "properties": {
         "delta": {"type": "integer", "description": "滚动量（正上负下，120=一格）"},
         "x": {"type": "integer", "description": "可选：滚动前把指针移到此处（截图基准 x）"},
         "y": {"type": "integer", "description": "可选：滚动前把指针移到此处（截图基准 y）"}},
         "required": ["delta"]}},
    {"name": "type_text", "description":
        "输入文本（Unicode 注入，支持中文/符号；先点击输入框使其获得焦点再调用）。"
        "**发消息别再去点发送按钮**：传 submit=true 就会在输入完成后自动按 Enter 发送。"
        "**注入前会确保目标窗口在最前**：抢不到焦点就拒绝执行，避免文字打进别的应用"
        "（例如 AI 助手自己的输入框）。",
     "inputSchema": {"type": "object", "properties": {
         "title": {"type": "string", "description": "目标窗口标题片段（可选；注入前会确保它在最前，避免文字打进别的应用）"},
         "hwnd": {"type": "integer", "description": "目标窗口句柄（可选，来自 list_windows）"},
         "text": {"type": "string", "description": "要输入的文本"},
         "submit": {"type": "boolean", "description": "输入完成后自动按 Enter 发送（默认 false；聊天/搜索框发消息时传 true，无需移动鼠标点发送按钮）"},
         "submit_delay": {"type": "integer", "description": "输入完成到按 Enter 之间的等待毫秒，默认 120（输入法上屏慢时可加大）"}},
         "required": ["text"]}},
    {"name": "key_press", "description":
        "按键/组合键：enter、tab、esc、backspace、f5、ctrl+c、ctrl+shift+s、win+ctrl+d 等。"
        "除 win 开头的系统级快捷键外，注入前会确保目标窗口在最前（抢不到就拒绝），"
        "避免按键（尤其 enter）打进别的应用。",
     "inputSchema": {"type": "object", "properties": {
         "title": {"type": "string", "description": "目标窗口标题片段（可选；注入前会确保它在最前，避免文字打进别的应用）"},
         "hwnd": {"type": "integer", "description": "目标窗口句柄（可选，来自 list_windows）"},
         "key": {"type": "string", "description": "键名或组合键，如 ctrl+c"}},
         "required": ["key"]}},
    {"name": "copy", "description":
        "复制当前选中内容（Ctrl+C）并返回剪贴板文本（先 select 或点击目标再调用）。"
        "注入前会确保目标窗口在最前（抢不到就拒绝）。",
     "inputSchema": {"type": "object", "properties": {
         "title": {"type": "string", "description": "目标窗口标题片段（可选；注入前会确保它在最前，避免文字打进别的应用）"},
         "hwnd": {"type": "integer", "description": "目标窗口句柄（可选，来自 list_windows）"}}}},
    {"name": "paste", "description":
        "粘贴剪贴板内容（Ctrl+V）；可传 text 先写入剪贴板再粘贴。"
        "注入前会确保目标窗口在最前（抢不到就拒绝），避免粘到别的应用。",
     "inputSchema": {"type": "object", "properties": {
         "title": {"type": "string", "description": "目标窗口标题片段（可选；注入前会确保它在最前，避免文字打进别的应用）"},
         "hwnd": {"type": "integer", "description": "目标窗口句柄（可选，来自 list_windows）"},
         "text": {"type": "string", "description": "可选：先写入剪贴板的内容"}}}},
    {"name": "open_virtual_desktop", "description":
        "虚拟桌面容器：new=新建独立虚拟桌面并切入（AI 在该桌面静默操控，用户主桌面窗口不受打扰）；"
        "back=返回用户桌面（任务结束时必须调用）；next/prev=切换相邻桌面。",
     "inputSchema": {"type": "object", "properties": {
         "action": {"type": "string", "description": "new/back/next/prev，默认 new"}}}},
    {"name": "control", "description":
        "操控状态查询与控制：status=查询当前状态；take_over=主动交还控制权给用户；"
        "resume=清除接管/暂停标记恢复操控（仅当用户明确说「继续」后才可调用）。",
     "inputSchema": {"type": "object", "properties": {
         "action": {"type": "string", "description": "status/take_over/resume"}}}},
    {"name": "open_app", "description":
        "**打开/启动应用（任务栏里没有它时的兜底）**：一次调用完成「找到该应用的窗口 → "
        "没运行就启动它 → 等窗口出现 → 最小化就恢复 → 切到前台 → 实测校验」。"
        "顺序上先试 taskbar_open（应用已在跑时点任务栏图标更快、也不会开出第二个实例）；"
        "任务栏和「↑」面板里都没有它、或任务栏自动隐藏时才用本工具。"
        "**不要**用 list_windows 反复找窗口（那是任务绕不出来的主因）。"
        "name 传用户的原话即可（中文口语名也认，如「微信」「记事本」）；"
        "返回值给出 hwnd 与就绪状态，后续直接用该 hwnd 截图/读坐标。",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string",
                  "description": "应用名，例如「微信」「企业微信」「记事本」「Visual Studio Code」"},
         "timeout": {"type": "integer",
                     "description": "等待窗口出现的超时毫秒（默认 15000，启动慢的应用可加大）"}},
         "required": ["name"]}},
    {"name": "taskbar_open", "description":
        "**从任务栏打开/切换应用（首选，比 open_app 更直接）**：自动认出屏幕底部任务栏那条"
        "通栏、把里面的图标切成一个个槽位并**编号**，返回编号图；带 index=N 时直接点第 N 个，"
        "并回读落点、校验前台窗口是否真的切过去了。"
        "用户说「打开微信 / 切到 XX / XX 在后台」时：先 screen_shot 看一眼任务栏上有没有那个"
        "图标 —— 有就 taskbar_open(index=N) 一步切过去（应用已在运行时，点任务栏图标 = 切到"
        "它的窗口，不会重开一个）；图标不在可见区（任务栏放不下）就 "
        "taskbar_open(where=\"panel\") 先展开右侧「↑ 隐藏图标/后台运行的应用」面板，"
        "再把面板里的图标编号并点击。图标在任务栏和面板里都没有、或任务栏是自动隐藏的，"
        "才用 open_app(name=…) 启动。**不要再用 list_windows / 反复整屏截图去找应用窗口。**",
     "inputSchema": {"type": "object", "properties": {
         "index": {"type": "integer", "description": "要点第几个槽位（从 1 开始，编号见返回的图与表）；不填=只扫描并列编号，不点击"},
         "where": {"type": "string", "description": "taskbar=任务栏本体（默认）；panel=先展开右侧「↑」隐藏图标/后台应用面板，再在面板里定位"},
         "chevron": {"type": "integer", "description": "where=panel 时可显式指定「↑」按钮是任务栏里的第几个槽位（自动识别不准时用）"},
         "name": {"type": "string", "description": "要找的应用名（可选）：填了会额外回报它当前是已在最前 / 在后台 / 已最小化 / 没运行，避免你反复置顶"},
         "timeout": {"type": "integer", "description": "点击后等待前台切换的毫秒数，默认 250（启动慢的应用可加大）"}}}},
    {"name": "manage_window", "description":
        "窗口/应用管理：state 列出窗口（含最小化标记）；"
        "activate/focus 切到前台并实测校验；"
        "maximize/minimize/restore 调整显示状态；move 移动或改尺寸；close 关闭；"
        "wait 等待某标题窗口出现（弹窗延迟弹出时用，比固定 sleep 可靠）。"
        "**切到已在运行的应用请优先用 taskbar_open(index=N)，不要反复调本工具置顶。**",
     "inputSchema": {"type": "object", "properties": {
         "action": {"type": "string", "description":
                    "state/list/activate/focus/maximize/minimize/restore/move/close/wait"},
         "title": {"type": "string", "description": "窗口标题片段"},
         "hwnd": {"type": "integer", "description": "窗口句柄"},
         "x": {"type": "integer", "description": "move 时的目标 x（截图基准）"},
         "y": {"type": "integer", "description": "move 时的目标 y（截图基准）"},
         "w": {"type": "integer", "description": "move 时的目标宽度（0=不改）"},
         "h": {"type": "integer", "description": "move 时的目标高度（0=不改）"},
         "timeout": {"type": "integer", "description": "wait 时的超时毫秒，默认 10000"}}}},
    {"name": "aura", "description":
        "屏幕边缘彩色光环开关（AI 实际操控桌面时自动点亮，鼠标穿透不挡视线）。"
        "一般无需手动调用；用户抱怨「屏幕边缘闪得慌」时用 action=off 关掉。",
     "inputSchema": {"type": "object", "properties": {
         "action": {"type": "string", "description": "on/off/state（默认 state）"}}}},
    {"name": "web_url", "description":
        "返回本地可视化界面的访问地址（用 preview_open 在用户默认浏览器里打开给用户，实时观看操控过程与日志）。",
     "inputSchema": {"type": "object", "properties": {}}},
]

_HANDLERS = {
    "screen_info": tool_screen_info,
    "screen_shot": tool_screen_shot,
    "zoom_in": tool_zoom_in,
    "list_windows": tool_list_windows,
    "click_window": tool_click_window,
    "verify_point": tool_verify_point,
    "refine_point": tool_refine_point,
    "click": tool_click,
    "long_press": tool_long_press,
    "drag": tool_drag,
    "swipe": tool_swipe,
    "select": tool_select,
    "scroll": tool_scroll,
    "type_text": tool_type_text,
    "key_press": tool_key_press,
    "copy": tool_copy,
    "paste": tool_paste,
    "open_virtual_desktop": tool_open_virtual_desktop,
    "control": tool_control,
    "manage_window": tool_manage_window,
    "open_app": tool_open_app,
    "taskbar_open": tool_taskbar_open,
    "aura": tool_aura,
    "web_url": tool_web_url,
}


# ============================================================================
# 本地 HTTP 可视化界面（web UI）
# ============================================================================
_WEB_PORT = 0
_preview_lock = threading.Lock()
_preview_cache = {"ts": 0.0, "png": b"", "grid": False}


def _preview_png(grid: bool = False) -> bytes:
    """预览截图（带缓存：页面高频轮询时不为同一帧反复抓屏编码，性能优先）。"""
    now = time.time()
    with _preview_lock:
        if (_preview_cache["png"] and _preview_cache["grid"] == grid
                and now - _preview_cache["ts"] < PREVIEW_MIN_INTERVAL):
            return _preview_cache["png"]
    png, _iw, _ih, _ox, _oy, _w, _h = grab_screen(max_w=PREVIEW_MAX_W, grid=grid, level=1)
    with _preview_lock:
        _preview_cache.update({"ts": now, "png": png, "grid": grid})
    return png


def _state_payload() -> dict:
    _refresh_env()
    with _LOG_LOCK:
        log = list(_LOG)[-80:]
    return {"interrupted": bool(_STATE["interrupted"]),
            "paused": bool(_STATE["paused"]),
            "container": _STATE["container"],
            "view": _STATE["view"],
            "input": _STATE["input"],
            "screen": _STATE["screen"],
            "cursor": _STATE["cursor"],
            "foreground": _STATE["foreground"],
            "aura": bool(_STATE["aura"]),
            "active": time.time() < _STATE["active_until"],
            "session_remain": max(0, int(_STATE["active_until"] - time.time())),
            "log": log}


def _control(action: str) -> str:
    """Web UI 控制动作（用户亲手操作）：接管 / 暂停 / 恢复 / 清空日志。"""
    a = str(action or "").strip().lower()
    if a == "take_over":
        mark_interrupted("用户在可视化界面点击接管")
        return "已接管：AI 后续操控将被拒绝"
    if a == "pause":
        _STATE["paused"] = True
        _log("user", "暂停 AI 操控")
        return "已暂停：AI 后续操控将被拒绝"
    if a == "resume":
        _STATE["interrupted"] = False
        _STATE["paused"] = False
        _touch_session()
        _log("user", "恢复 AI 操控")
        return "已恢复：AI 可继续操控"
    if a == "clear_log":
        with _LOG_LOCK:
            _LOG.clear()
        return "日志已清空"
    return f"未知操作: {action}"


_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>电脑操控 · 可视化预览</title>
<style>
:root{--bg:#000;--panel:#0b0c0e;--line:#23262b;--text:#e9ebee;--muted:#9aa0a8;
--blue:#123a6b;--blue2:#2f6fd0;--warn:#c98a3a;--ok:#4a9e6a}
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;background:var(--bg);color:var(--text);display:flex;flex-direction:column;
font:14px/1.55 "Microsoft YaHei",system-ui,-apple-system,sans-serif}
header{display:flex;justify-content:space-between;align-items:center;gap:16px;
padding:12px 18px;border-bottom:1px solid var(--line);background:#05060a;flex:none}
.brand{display:flex;align-items:center;gap:10px;font-weight:600;letter-spacing:.4px}
.chips{display:flex;gap:8px;flex-wrap:wrap}
.chip{border:1px solid var(--line);border-radius:999px;padding:3px 12px;
color:var(--muted);font-size:12px;white-space:nowrap}
.chip.on{border-color:var(--blue2);color:#cfe0ff}
.chip.warn{border-color:var(--warn);color:#f0c891}
main{flex:1;min-height:0;display:grid;grid-template-columns:minmax(0,1fr) 374px;
gap:14px;padding:14px 18px}
@media(max-width:1080px){main{grid-template-columns:1fr;overflow:auto}}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px}
main > .card{display:flex;flex-direction:column;min-height:0}
.bar{display:flex;align-items:center;justify-content:space-between;gap:10px;
padding:9px 12px;border-bottom:1px solid var(--line);color:var(--muted);font-size:12px;flex:none}
.stage{flex:1;min-height:0;display:flex;align-items:center;justify-content:center;padding:10px}
.stage img{max-width:100%;max-height:100%;border-radius:6px;
border:1px solid var(--line);background:#000}
.panel{display:flex;flex-direction:column;gap:14px;min-height:0}
.actions{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;padding:12px;flex:none}
button{display:flex;align-items:center;justify-content:center;gap:6px;
background:#10141c;color:var(--text);border:1px solid var(--line);border-radius:8px;
padding:9px 6px;font:inherit;font-size:13px;cursor:pointer;transition:.15s}
button:hover{border-color:var(--blue2);background:#131a26}
button.primary{border-color:var(--blue);background:#0e1c31}
button.primary:hover{background:#122b4d}
button svg{width:15px;height:15px;stroke:currentColor;fill:none;
stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round}
.msg{margin:0 12px;padding:8px 10px;border-radius:8px;font-size:12px;
color:var(--muted);border:1px dashed var(--line);min-height:32px}
.msg.err{color:#f0a5a5;border-color:#5c2b2b}
.log{display:flex;flex-direction:column;min-height:0;flex:1}
.logbar{display:flex;align-items:center;justify-content:space-between;
padding:9px 12px;border-bottom:1px solid var(--line);color:var(--muted);font-size:12px}
#log{list-style:none;margin:0;padding:8px 12px 14px;overflow:auto;flex:1;min-height:0;
font-family:Consolas,"Microsoft YaHei",monospace;font-size:12px;line-height:1.7}
#log li{color:#c7ccd3;border-bottom:1px solid #14171c;padding:2px 0;word-break:break-all}
#log li .t{color:#5d6773;margin-right:8px}
#log li.ai .x{color:#bcd2f5}
#log li.user .x{color:#f0c891}
#log li.sys .x{color:#8d97a3}
label.sw{display:flex;align-items:center;gap:6px;cursor:pointer;user-select:none}
</style></head>
<body>
<header>
  <div class="brand">
    <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="#7fa8e0"
      stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
      <rect x="2.5" y="4" width="19" height="13" rx="2"/><path d="M8 20h8"/><path d="M12 17v3"/>
    </svg>
    电脑操控 · 可视化预览
  </div>
  <div class="chips">
    <span class="chip" id="chipAura">光环：待命</span>
    <span class="chip" id="chipContainer">容器：前台桌面</span>
    <span class="chip" id="chipInput">输入：—</span>
    <span class="chip" id="chipState">状态：待命</span>
    <span class="chip" id="chipScreen">屏幕：—</span>
  </div>
</header>
<main>
  <section class="card">
    <div class="bar">
      <span id="screenMeta">等待画面…</span>
      <span style="display:flex;gap:14px;align-items:center">
        <label class="sw"><input type="checkbox" id="grid"> 网格</label>
        <label class="sw"><input type="checkbox" id="auto" checked> 实时刷新</label>
        <button id="btnShot" style="padding:5px 10px">
          <svg viewBox="0 0 24 24"><path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 5v6h-6"/></svg>
          刷新
        </button>
      </span>
    </div>
    <div class="stage"><img id="shot" alt="屏幕画面"></div>
  </section>
  <aside class="panel">
    <div class="card">
      <div class="actions">
        <button id="btnTakeOver" class="primary">
          <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 2"/></svg>
          接管
        </button>
        <button id="btnPause">
          <svg viewBox="0 0 24 24"><path d="M9 6v12"/><path d="M15 6v12"/></svg>
          暂停
        </button>
        <button id="btnResume">
          <svg viewBox="0 0 24 24"><path d="M8 6l10 6-10 6z"/></svg>
          恢复
        </button>
      </div>
      <div class="msg" id="msg">待命中。AI 开始操控后这里会显示实时状态；移动鼠标即可打断 AI 操作。</div>
    </div>
    <div class="card log">
      <div class="logbar">
        <span>操作日志</span>
        <button id="btnClear" style="padding:3px 9px;font-size:12px">清空</button>
      </div>
      <ol id="log"></ol>
    </div>
  </aside>
</main>
<script>
(function(){
  var el=function(id){return document.getElementById(id)};
  var lastShot=0,shotTimer=null,busy=false;
  function show(text,err){el("msg").textContent=text;el("msg").className="msg"+(err?" err":"")}
  function control(action){
    fetch("/api/control",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({action:action})})
      .then(function(r){return r.json()})
      .then(function(d){show(d.result||d.error||"",!!d.error)})
      .catch(function(){show("操作失败：本地服务无响应",true)});
  }
  function paint(s){
    var au=el("chipAura");
    au.textContent="光环："+(s.aura?"显示中":"待命");
    au.className="chip"+(s.aura?" on":"");
    el("chipContainer").textContent="容器："+(s.container==="virtual_desktop"?"独立虚拟桌面":"前台桌面");
    el("chipContainer").className="chip"+(s.container==="virtual_desktop"?" on":"");
    el("chipInput").textContent="输入："+(s.input==="touch"?"触控":"鼠标");
    var st=el("chipState"),warn=s.interrupted||s.paused;
    st.textContent="状态："+(s.paused?"已暂停":(s.interrupted?"用户已接管":(s.active?"AI 操控中":"待命")));
    st.className="chip"+(warn?" warn":(s.active?" on":""));
    el("chipScreen").textContent="屏幕："+s.screen[0]+"×"+s.screen[1]
      +"　鼠标："+s.cursor[0]+","+s.cursor[1];
    el("screenMeta").textContent=(s.foreground.title?("前台："+s.foreground.title+"　"):"")
      +"会话剩余 "+s.session_remain+"s";
    var log=el("log");log.innerHTML="";
    (s.log||[]).slice().reverse().forEach(function(it){
      var li=document.createElement("li");li.className=it.who;
      var t=document.createElement("span");t.className="t";t.textContent=it.t;
      var x=document.createElement("span");x.className="x";x.textContent=it.text;
      li.appendChild(t);li.appendChild(x);log.appendChild(li);
    });
    return s;
  }
  function pollState(){
    fetch("/api/state",{cache:"no-store"}).then(function(r){return r.json()})
      .then(paint).catch(function(){});
  }
  function loadShot(){
    if(busy)return;busy=true;
    var img=el("shot");
    img.onload=img.onerror=function(){busy=false};
    img.src="/api/screenshot?grid="+(el("grid").checked?1:0)+"&t="+Date.now();
  }
  function schedule(){
    if(shotTimer){clearTimeout(shotTimer);shotTimer=null}
    if(!el("auto").checked)return;
    var wait=el("chipState").className.indexOf("on")>=0?800:3000;
    setTimeout(function(){loadShot();schedule()},wait);
  }
  el("grid").addEventListener("change",loadShot);
  el("auto").addEventListener("change",schedule);
  el("btnShot").addEventListener("click",loadShot);
  el("btnTakeOver").addEventListener("click",function(){control("take_over")});
  el("btnPause").addEventListener("click",function(){control("pause")});
  el("btnResume").addEventListener("click",function(){control("resume")});
  el("btnClear").addEventListener("click",function(){control("clear_log")});
  pollState();loadShot();schedule();
  setInterval(pollState,1000);
})();
</script>
</body></html>
"""


class _HttpHandler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _json(self, code: int, data: dict):
        self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            self._send(200, _PAGE.encode("utf-8"), "text/html; charset=utf-8")
            return
        if parsed.path == "/api/state":
            self._json(200, _state_payload())
            return
        if parsed.path == "/api/screenshot":
            q = urllib.parse.parse_qs(parsed.query)
            grid = q.get("grid", ["0"])[0] not in ("0", "false", "no")
            try:
                self._send(200, _preview_png(grid), "image/png")
            except Exception as e:
                self._json(503, {"error": f"截屏失败: {e}"})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        try:
            args = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            args = {}
        if parsed.path == "/api/control":
            try:
                self._json(200, {"result": _control(str((args or {}).get("action") or ""))})
            except Exception as e:
                self._json(500, {"error": str(e)})
            return
        self._json(404, {"error": "not found"})

    def log_message(self, *a):
        pass        # 静默：页面高频轮询，不能刷日志（也绝不可写 stdout）


def ensure_http() -> int:
    """启动本地可视化服务（幂等），返回端口；失败返回 0。"""
    global _WEB_PORT
    if _WEB_PORT:
        return _WEB_PORT
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _HttpHandler)
    srv.daemon_threads = True
    _WEB_PORT = int(srv.server_address[1])
    _STATE["web_port"] = _WEB_PORT
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _log("sys", f"可视化界面已启动：http://127.0.0.1:{_WEB_PORT}/")
    return _WEB_PORT


# ============================================================================
# MCP（stdio）：JSON-RPC 分发
# ============================================================================

def _handle(msg: dict):
    """处理单个请求/通知；通知（无 id）返回 None 不回复。"""
    method = msg.get("method", "")
    params = msg.get("params", {}) or {}
    rid = msg.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": MCP_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": APP_NAME, "version": "1.0"},
        }}
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        name = params.get("name", "")
        fn = _HANDLERS.get(name)
        if fn is None:
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32601, "message": f"未知工具: {name}"}}
        # 操控会话：只有**真正的操控动作**才开启/续期，纯查询不开启。
        # 为什么必须区分：会话期内「真人鼠标/键盘输入 = 用户接管」。若连
        # screen_info / screen_shot / list_windows 这类只读查询也开启会话，用户
        # 在 AI 只是「看一眼」的时候随手动一下鼠标，就会被判成接管 —— 之后所有
        # 点击都被拒绝，用户却完全没打算接管（典型的误伤，实测复现过）。
        if name in _CONTROL_TOOLS:
            _touch_session()
        # 屏幕光环：只在**真正会改变桌面状态**的操控工具调用时点亮，
        # 纯查询（截屏/列窗口/看环境）不点亮，避免用户被无意义闪烁打扰。
        if name in _CONTROL_TOOLS:
            _aura_on()
        try:
            out = fn(params.get("arguments", {}) or {})
            content = out if isinstance(out, list) else [{"type": "text", "text": str(out)}]
            result = {"content": content, "isError": False}
        except Exception as e:
            result = {"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}],
                      "isError": True}
        return {"jsonrpc": "2.0", "id": rid, "result": result}
    return {"jsonrpc": "2.0", "id": rid,
            "error": {"code": -32601, "message": f"未知方法: {method}"}}


def main():
    _init_dpi_awareness()
    ensure_http()          # 本地可视化服务随进程启动，用户/页面随时可访问
    _watcher.start()       # 真人输入打断检测
    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
    for line in stdin:     # stdin 关闭（客户端断开）→ 循环退出，进程自然结束
        if not line.strip():
            continue
        try:
            msg = json.loads(line.decode("utf-8", "replace"))
        except json.JSONDecodeError:
            continue
        resp = _handle(msg)
        if resp is not None:
            stdout.write(json.dumps(resp, ensure_ascii=False).encode("utf-8") + b"\n")
            stdout.flush()


# ---------- 离线自检（python server.py --selftest）：不做任何输入注入 ----------

def _selftest() -> int:
    """起本地 HTTP → 拉页面 → 调端点 → 验证「接管/暂停」状态机；只读检查截屏能力。

    判据：页面字节数 ≥ 200、/api/state 返回 ok、接管/暂停/恢复状态机正确。
    截屏能力单独记录（无桌面会话的环境可能失败，不代表插件不可用），且绝不注入任何输入。
    """
    import urllib.request

    out = {"ok": True, "page_bytes": 0, "endpoints": {}, "capture": "skipped",
           "tools": [t["name"] for t in TOOLS], "error": ""}

    def _get(path):
        with urllib.request.urlopen(base + path, timeout=10) as r:
            return r.read()

    def _post(path, payload):
        req = urllib.request.Request(base + path,
                                     data=json.dumps(payload).encode("utf-8"),
                                     method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8", "replace"))

    base = ""
    try:
        _init_dpi_awareness()
        ensure_http()
        base = f"http://127.0.0.1:{_WEB_PORT}"
        out["page_bytes"] = len(_get("/"))
    except Exception as e:
        out["ok"] = False
        out["error"] = f"HTTP 服务或页面加载失败: {e}"

    if out["ok"]:
        try:
            state = json.loads(_get("/api/state").decode("utf-8", "replace"))
            out["endpoints"]["state"] = "ok" if "log" in state else "缺少 log 字段"
        except Exception as e:
            out["endpoints"]["state"] = f"异常: {e}"
        # 状态机：接管 → 操控被拒 → 恢复放行；暂停同理（经真实 HTTP POST 驱动，
        # 拒绝判定只查守卫文本，绝不真的注入输入）
        try:
            r1 = _post("/api/control", {"action": "take_over"})
            refused = _guard("click")
            _post("/api/control", {"action": "resume"})
            ok_refuse = "接管" in str(r1.get("result", "")) and "用户已接管" in refused
            r2 = _post("/api/control", {"action": "pause"})
            refused2 = _guard("scroll")
            _post("/api/control", {"action": "resume"})
            ok_pause = "暂停" in str(r2.get("result", "")) and "已被用户暂停" in refused2
            out["endpoints"]["control"] = "ok" if (ok_refuse and ok_pause) else \
                f"状态机异常（接管拒绝={ok_refuse} 暂停拒绝={ok_pause}）"
        except Exception as e:
            out["endpoints"]["control"] = f"异常: {e}"
        # 只读能力：环境信息 + 截屏（截屏失败不改判，仅记录）
        try:
            info = tool_screen_info({})
            out["endpoints"]["screen_info"] = "ok" if "屏幕物理分辨率" in info else "输出异常"
        except Exception as e:
            out["endpoints"]["screen_info"] = f"异常: {e}"
        try:
            png, iw, ih, _ox, _oy, _w, _h = grab_screen(max_w=PREVIEW_MAX_W, level=1)
            out["capture"] = f"ok {iw}x{ih} {len(png)}B"
        except Exception as e:
            out["capture"] = f"不可用: {e}"
        # 窗口枚举（只读：只列窗口，绝不点击/输入）
        try:
            wins = list_windows()
            # 「枚举成功」就是 ok，哪怕当前桌面上确实没有可列窗口 —— 空结果是正常
            # 输出，不是故障。自检的 endpoints 全部要求 ok，写成「无窗口」会让
            # 调用方把正常环境误判成异常。
            out["endpoints"]["windows"] = "ok"
            out["windows"] = {"count": len(wins),
                              "named": [w["title"] for w in wins if w["title"]][:8]}
        except Exception as e:
            out["endpoints"]["windows"] = f"异常: {e}"
        # 光环：只查状态与能力，不真的长时间显示（避免自检时闪屏）
        try:
            if aura is None:
                out["endpoints"]["aura"] = "未加载"
            else:
                st = aura.aura_state()
                out["endpoints"]["aura"] = "ok" if "光环" in st["capability"] else st["capability"]
        except Exception as e:
            out["endpoints"]["aura"] = f"异常: {e}"
        # 工具注册完整性：TOOLS 与 _HANDLERS 必须一一对应（漏注册会静默不可用）
        try:
            declared = {t["name"] for t in TOOLS}
            handled = set(_HANDLERS)
            missing = declared - handled
            extra = handled - declared
            out["endpoints"]["tools"] = "ok" if not (missing or extra) else \
                f"未注册={sorted(missing)} 多余={sorted(extra)}"
        except Exception as e:
            out["endpoints"]["tools"] = f"异常: {e}"

    _STATE["interrupted"] = False
    _STATE["paused"] = False
    # 关键端点必须全部 ok 才算自检通过。
    # aura / windows 属「能力探测」：光环模块缺失不影响操控，某些受限会话下
    # 也可能枚举不到窗口，因此它们只记录状态，不参与总判据。
    _CORE = ("state", "control", "screen_info", "tools")
    out["ok"] = out["ok"] and all(out["endpoints"].get(k) == "ok" for k in _CORE)
    sys.stdout.buffer.write(json.dumps(out, ensure_ascii=False).encode("utf-8") + b"\n")
    sys.stdout.buffer.flush()
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    main()