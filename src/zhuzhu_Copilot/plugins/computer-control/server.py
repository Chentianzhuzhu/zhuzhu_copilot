"""电脑操控插件（MCP stdio 工具 + 本地 HTTP 可视化）: computer-control

三件套（技能 + MCP 工具 + 本地 HTTP 浏览器界面）
------------------------------------------------
- MCP（stdio）：向 AI 暴露点击/长按/拖拽/滑动/选择/输入/按键/复制/粘贴/截屏/窗口/
  虚拟桌面等工具，全部真实 Win32 调用，零第三方依赖（ctypes + 标准库）。
- HTTP（127.0.0.1 随机端口）：给用户看的可视化界面 —— 实时截图（与 AI 同基准的
  网格刻度）、操作日志、接管/暂停/恢复按钮；与 MCP 工具共用同一份模块级 _STATE。
- 感知：GDI 截屏（StretchBlt + 网格/刻度/准星叠加 + 手写 PNG），以 MCP image 内容项
  返回给视觉模型；坐标基准与 AI 所见图完全一致，杜绝缩放换算误差。
- 输入：触控优先（InjectTouchInput，真实 Win10/11 触控语义：tap/长按/惯性滑动），
  无触控设备自动回退 SendInput 鼠标键盘；所有注入事件带签名，供打断检测区分
  「AI 注入」与「真人输入」。
- 打断：全局低级钩子（WH_MOUSE_LL / WH_KEYBOARD_LL）监听真人鼠标/键盘输入，命中即置
  interrupted，后续操控工具一律拒绝执行并把控制权交还用户（Web UI 可一键接管/暂停/恢复）。

进程约束：本进程以 MCP stdio 方式被拉起，**stdout 只能输出 JSON-RPC** —— 日志一律不写 stdout。
"""

import base64
import ctypes
import json
import math
import re
import sys
import threading
import time
import urllib.parse
import zlib
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP_NAME = "computer-control"
MCP_VERSION = "2024-11-05"

# ---- 可调参数（集中在此，便于扩展/调优） ----
MODEL_MAX_W = 1280          # 返回给视觉模型的截图最大宽度（与主程序视觉基准一致）
PREVIEW_MAX_W = 960         # Web UI 预览截图最大宽度（性能优先：预览不需要全分辨率）
PREVIEW_MIN_INTERVAL = 0.25  # Web 预览两次抓图的最小间隔（秒）：页面高频轮询时复用缓存
GRID_CELLS = 16             # 叠加在截图上的网格数量（N×N），刻度数字即图像像素值
SESSION_TTL_S = 300.0       # 操控会话滑动有效期：期内真人输入才算「接管」
INJECT_SIGNATURE = 0x5A5A5A5A       # SendInput dwExtraInfo 签名：标记 AI 自身注入
INJECT_GRACE_S = 0.35       # 注入后抑制窗口：钩子在此窗口内不把事件判为真人输入
LOG_MAX = 200               # 操作日志上限（环形）
DEFAULT_LONG_PRESS_S = 0.8
MOVE_STEP_S = 0.012         # 真人式移动的单步间隔
ZOOM_MAX_PX = 2400          # 放大图最大边长（防超大图撑爆视觉输入）

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
PS_SOLID, TRANSPARENT, NULL_BRUSH = 0, 1, 5


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
    """坐标网格 + 像素刻度：刻度数字与图像同基准，模型读数可直接作为工具坐标。"""
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


def _draw_cursor(dc, sx, sy, sw, sh, tw, th, pt):
    """红色准星标记当前鼠标位置（源坐标系 → 图像坐标），鼠标不在区域内不画。"""
    if not pt:
        return
    px, py = pt
    if not (sx <= px <= sx + sw and sy <= py <= sy + sh):
        return
    x = int((px - sx) * tw / sw)
    y = int((py - sy) * th / sh)
    _gdi_line(dc, x - 14, y, x + 14, y, 0x005533FF, 2)
    _gdi_line(dc, x, y - 14, x, y + 14, 0x005533FF, 2)
    pen = gdi32.CreatePen(PS_SOLID, 2, 0x005533FF)
    old_pen = gdi32.SelectObject(dc, pen)
    old_brush = gdi32.SelectObject(dc, gdi32.GetStockObject(NULL_BRUSH))   # 空心圆环
    try:
        gdi32.Ellipse(dc, x - 15, y - 15, x + 15, y + 15)
    finally:
        gdi32.SelectObject(dc, old_brush)
        gdi32.SelectObject(dc, old_pen)
        gdi32.DeleteObject(pen)


def _capture_scaled(src_dc, sx, sy, sw, sh, tw, cells, cursor_pt=None) -> tuple:
    """抓取源 DC 区域 → 缩放到 tw 宽 → 叠加网格/刻度/准星 → PNG 字节。

    返回 (png, img_w, img_h)。坐标基准：图像像素 = (源坐标 - (sx,sy)) * tw/sw。
    """
    tw = max(1, int(tw))
    th = max(1, round(sh * tw / max(1, sw)))
    mem = gdi32.CreateCompatibleDC(src_dc)
    bmp = gdi32.CreateCompatibleBitmap(src_dc, tw, th)
    old = gdi32.SelectObject(mem, bmp)
    try:
        gdi32.SetStretchBltMode(mem, HALFTONE)
        gdi32.SetBrushOrgEx(mem, 0, 0, None)   # HALFTONE 需重置刷原点，否则缩放发糊
        gdi32.StretchBlt(mem, 0, 0, tw, th, src_dc, sx, sy, sw, sh, SRCCOPY)
        if cells > 0:
            _draw_grid(mem, tw, th, cells)
        _draw_cursor(mem, sx, sy, sw, sh, tw, th, cursor_pt)
        return _png_encode(_dib_bits(mem, bmp, tw, th), tw, th), tw, th
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


def grab_screen(max_w: int = MODEL_MAX_W, grid: bool = True, cursor: bool = True,
                level: int = 6) -> tuple:
    """整屏抓图（主显示器物理像素）。返回 (png, img_w, img_h, shot_w, shot_h)。"""
    sw, sh = screen_size()
    dc = user32.GetDC(0)
    try:
        png, iw, ih = _capture_scaled(dc, 0, 0, sw, sh, min(max_w, sw),
                                      GRID_CELLS if grid else 0, _cursor_pos())
        return png, iw, ih, sw, sh
    finally:
        user32.ReleaseDC(0, dc)


def grab_window(hwnd: int, max_w: int = MODEL_MAX_W, grid: bool = True) -> tuple:
    """指定窗口抓图（PrintWindow，不受其他窗口遮挡）。返回 (png, img_w, img_h, x, y, w, h)。"""
    rect = window_rect(hwnd)
    w, h = rect.right - rect.left, rect.bottom - rect.top
    if w <= 0 or h <= 0:
        raise RuntimeError("窗口尺寸无效")
    scr = user32.GetDC(0)
    mem = gdi32.CreateCompatibleDC(scr)
    bmp = gdi32.CreateCompatibleBitmap(scr, w, h)
    old = gdi32.SelectObject(mem, bmp)
    try:
        if not user32.PrintWindow(hwnd, mem, 2):    # PW_RENDERFULLCONTENT
            raise RuntimeError("PrintWindow 抓取失败")
        cx, cy = _cursor_pos()
        png, iw, ih = _capture_scaled(mem, 0, 0, w, h, min(max_w, w),
                                      GRID_CELLS if grid else 0, (cx - rect.left, cy - rect.top))
        return png, iw, ih, rect.left, rect.top, w, h
    finally:
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(0, scr)


def grab_zoom(cx: int, cy: int, size: int, factor: int) -> tuple:
    """以物理坐标 (cx,cy) 为中心抓取 size×size 并放大 factor 倍（细网格便于精确定位）。"""
    sw, sh = screen_size()
    size = max(60, min(int(size), min(sw, sh)))
    factor = max(1, min(int(factor), 8))
    x0 = max(0, min(sw - size, int(cx - size / 2)))
    y0 = max(0, min(sh - size, int(cy - size / 2)))
    tw = min(size * factor, ZOOM_MAX_PX)
    dc = user32.GetDC(0)
    try:
        png, iw, ih = _capture_scaled(dc, x0, y0, size, size, tw, GRID_CELLS, (cx, cy))
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


def _move_absolute(x: int, y: int):
    """鼠标绝对移动（虚拟屏归一化，多显示器正确）。"""
    vx, vy, vw, vh = _virtual_screen()
    nx = int((int(x) - vx) * 65535 / max(1, vw - 1))
    ny = int((int(y) - vy) * 65535 / max(1, vh - 1))
    _send_mouse(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, nx, ny)


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


def do_click(x: int, y: int, button: str = "left", clicks: int = 1):
    """点击：左键优先真实触控 tap；触控不可用或注入失败时自动回退鼠标。"""
    if button == "left" and _touch_tap(x, y, clicks):
        return
    down, up = _mouse_button(button)
    _move_human(x, y)
    with _Injecting():
        for _ in range(max(1, clicks)):
            _send_mouse(down)
            time.sleep(0.04)
            _send_mouse(up)
            time.sleep(0.05)


def do_long_press(x: int, y: int, duration: float):
    """长按：触控可用时走触控长按（可呼出上下文菜单）；否则按住鼠标不动。"""
    if _touch_long_press(x, y, duration):
        return
    _move_human(x, y)
    with _Injecting():
        _send_mouse(MOUSEEVENTF_LEFTDOWN)
        time.sleep(max(0.2, duration))
        _send_mouse(MOUSEEVENTF_LEFTUP)


def do_drag(x1: int, y1: int, x2: int, y2: int, duration: float, hold: float = 0.15):
    """拖拽：触控可用时触控拖拽，否则鼠标拖拽。"""
    if _touch_drag(x1, y1, x2, y2, duration, hold):
        return
    steps = max(int(duration / 0.02), 8)
    _move_human(x1, y1)
    with _Injecting():
        _send_mouse(MOUSEEVENTF_LEFTDOWN)
        time.sleep(hold)
        for i in range(1, steps + 1):
            _move_absolute(int(x1 + (x2 - x1) * i / steps), int(y1 + (y2 - y1) * i / steps))
            time.sleep(duration / steps)
        _send_mouse(MOUSEEVENTF_LEFTUP)


def do_swipe(x1: int, y1: int, x2: int, y2: int, duration: float):
    """滑动：触控快速滑动（带速度→系统惯性滚动），无触控/注入失败时退化为鼠标拖动。"""
    if _touch_swipe(x1, y1, x2, y2, duration):
        return
    do_drag(x1, y1, x2, y2, duration, hold=0.02)


def do_scroll(delta: int):
    with _Injecting():
        _send_mouse(MOUSEEVENTF_WHEEL, 0, 0, int(delta) & 0xFFFFFFFF)


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
         "space": 0x20, "backspace": 0x08, "delete": 0x2E, "del": 0x2E,
         "insert": 0x2D, "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
         "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27, "capslock": 0x14,
         "printscreen": 0x2C, "apps": 0x5D, "menu": 0x5D,
         "+": 0xBB, "=": 0xBB, "-": 0xBD, "minus": 0xBD, ".": 0xBE, ",": 0xBC,
         "/": 0xBF, ";": 0xBA, "'": 0xDE, "[": 0xDB, "]": 0xDD, "\\": 0xDC, "`": 0xC0}


def key_vk(name: str) -> int:
    """键名 → 虚拟键码（字母/数字/修饰键/F1-F24/常用键名）。未知键名抛 ValueError。"""
    n = str(name or "").strip().lower()
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
    """按键/组合键：'enter'、'f5'、'ctrl+c'、'win+ctrl+d'。"""
    parts = [p.strip() for p in str(key or "").split("+") if p.strip()]
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


def list_windows() -> list:
    """枚举可见顶层窗口（有标题、尺寸≥60×40），坐标为物理像素。"""
    out = []
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _cb(hwnd, _lp):
        if not user32.IsWindowVisible(hwnd):
            return True
        title = window_title(hwnd)
        if not title:
            return True
        r = window_rect(hwnd)
        w, h = r.right - r.left, r.bottom - r.top
        if w < 60 or h < 40:
            return True
        out.append({"hwnd": int(hwnd), "title": title,
                    "x": r.left, "y": r.top, "w": w, "h": h})
        return True

    user32.EnumWindows(proc(_cb), 0)
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


def _set_view(x0, y0, w, h, img_w, img_h, kind: str):
    _VIEW.update({"x0": float(x0), "y0": float(y0), "w": float(w), "h": float(h),
                  "img_w": float(img_w), "img_h": float(img_h)})
    _STATE["view"] = kind


def to_physical(x, y) -> tuple:
    """当前截图基准图像坐标 → 屏幕物理坐标（无基准时按物理坐标原样透传）。"""
    x, y = _num(x), _num(y)
    iw, ih = _VIEW["img_w"], _VIEW["img_h"]
    if iw <= 0 or ih <= 0 or _VIEW["w"] <= 0:
        return int(x), int(y)
    return (int(_VIEW["x0"] + x * _VIEW["w"] / iw),
            int(_VIEW["y0"] + y * _VIEW["h"] / ih))


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
    _STATE["input"] = "touch" if touch_ready() else "mouse"


# ============================================================================
# MCP 工具实现
# ============================================================================

def tool_screen_info(args: dict) -> str:
    _refresh_env()
    sw, sh = _STATE["screen"]
    remain = max(0, int(_STATE["active_until"] - time.time()))
    lines = [
        f"屏幕物理分辨率: {sw}×{sh}（进程已做 DPI 感知，坐标即物理像素）",
        f"输入方式: {touch_capability()}",
        f"操控容器: {'独立虚拟桌面' if _STATE['container'] == 'virtual_desktop' else '用户前台桌面'}",
        f"鼠标位置: {_STATE['cursor'][0]}, {_STATE['cursor'][1]}",
        f"前台窗口: {_STATE['foreground']['title'] or '(无)'}",
        f"接管状态: {'已被用户接管/暂停（操控被拒绝）' if _interrupted() else '正常'}"
        + f"；操控会话剩余 {remain}s（每次工具调用自动延长）",
        f"可视化界面: http://127.0.0.1:{_STATE['web_port']}/",
        "下一步建议: 先 screen_shot 截屏看界面 → 读出目标坐标 → 点击/输入 → 再截屏验证。",
    ]
    _log("ai", "查看环境信息")
    return "\n".join(lines)


def _shot_meta(desc: str) -> str:
    return (f"{desc}。图中叠加了 {GRID_CELLS}×{GRID_CELLS} 网格，两端刻度数字与图像像素同基准 —— "
            "click/drag 等工具的 x/y 直接用图中读到的数值，插件自动换算为屏幕物理坐标，无需再乘缩放比例。")


def tool_screen_shot(args: dict) -> list:
    mode = str(args.get("mode") or "full").strip().lower()
    grid = _bool(args.get("grid"), True)
    hwnd = _num(args.get("hwnd"), 0)
    title = str(args.get("title") or "")
    if mode == "window" or hwnd or title:
        target = find_window(title, hwnd)
        if not target:
            raise ValueError(f"未找到窗口（title={title!r} hwnd={hwnd}），可用 list_windows 查看")
        png, iw, ih, x, y, w, h = grab_window(target, grid=grid)
        _set_view(x, y, w, h, iw, ih, "window")
        desc = (f"已截取窗口「{window_title(target)}」（区域 {w}×{h}），图像 {iw}×{ih}"
                f"（窗口左上角对应屏幕 {x},{y}）")
        _log("ai", f"截屏：窗口「{window_title(target)}」")
    else:
        png, iw, ih, sw, sh = grab_screen(grid=grid)
        _set_view(0, 0, sw, sh, iw, ih, "full")
        desc = (f"已截取整屏 {sw}×{sh}，图像 {iw}×{ih}"
                f"（缩放比 {sw / iw:.2f}）")
        _log("ai", "截屏：整屏")
    _refresh_env()
    return [{"type": "text", "text": _shot_meta(desc)},
            {"type": "image", "data": base64.b64encode(png).decode(), "mimeType": "image/png"}]


def tool_zoom_in(args: dict) -> list:
    x, y = to_physical(args.get("x"), args.get("y"))
    size = _num(args.get("size"), 400)
    factor = _num(args.get("factor"), 3)
    png, iw, ih, x0, y0, region = grab_zoom(x, y, size, factor)
    _set_view(x0, y0, region, region, iw, ih, "zoom")
    _log("ai", f"放大查看 ({x},{y}) 区域 {region}px × {factor}")
    return [{"type": "text",
             "text": _shot_meta(f"已放大屏幕 ({x},{y}) 附近 {region}×{region} 区域为 {iw}×{ih}"
                                f"（放大 {iw / region:.1f} 倍）")},
            {"type": "image", "data": base64.b64encode(png).decode(), "mimeType": "image/png"}]


def tool_list_windows(args: dict) -> str:
    wins = list_windows()
    _log("ai", f"列出窗口（{len(wins)} 个）")
    if not wins:
        return "当前没有可见窗口"
    lines = [f"{i + 1}. [{w['hwnd']}] {w['title']} — 位置 {w['x']},{w['y']} 尺寸 {w['w']}×{w['h']}"
             for i, w in enumerate(wins[:40])]
    return ("可见窗口（hwnd / 标题 / 位置 / 尺寸）:\n" + "\n".join(lines)
            + "\n用 screen_shot(mode=\"window\", title=\"标题片段\") 只看某一个窗口。")


def tool_click(args: dict) -> str:
    reason = _guard("click")
    if reason:
        return reason
    x, y = to_physical(args.get("x"), args.get("y"))
    button = str(args.get("button") or "left").strip().lower()
    clicks = max(1, min(_num(args.get("clicks"), 1), 3))
    do_click(x, y, button, clicks)
    _log("ai", f"点击 {x},{y}（{button}×{clicks}，{touch_capability()}）")
    _refresh_env()
    return f"已点击 ({x},{y}) {button}×{clicks}（屏幕物理坐标）。请重新 screen_shot 验证结果。"


def tool_long_press(args: dict) -> str:
    reason = _guard("long_press")
    if reason:
        return reason
    x, y = to_physical(args.get("x"), args.get("y"))
    dur = max(0.2, float(_num(args.get("duration"), int(DEFAULT_LONG_PRESS_S * 1000)) / 1000.0))
    do_long_press(x, y, dur)
    _log("ai", f"长按 {x},{y}（{dur:.1f}s，{touch_capability()}）")
    _refresh_env()
    return (f"已长按 ({x},{y}) {dur:.1f}s。请重新 screen_shot 查看是否弹出菜单"
            "（无触控设备时如需右键菜单请用 click(button=\"right\")）。")


def tool_drag(args: dict) -> str:
    reason = _guard("drag")
    if reason:
        return reason
    x1, y1 = to_physical(args.get("x1"), args.get("y1"))
    x2, y2 = to_physical(args.get("x2"), args.get("y2"))
    dur = max(0.1, _num(args.get("duration"), 500) / 1000.0)
    do_drag(x1, y1, x2, y2, dur)
    _log("ai", f"拖拽 ({x1},{y1}) → ({x2},{y2})")
    _refresh_env()
    return f"已从 ({x1},{y1}) 拖拽到 ({x2},{y2})。请重新 screen_shot 验证结果。"


def tool_swipe(args: dict) -> str:
    reason = _guard("swipe")
    if reason:
        return reason
    x1, y1 = to_physical(args.get("x1"), args.get("y1"))
    x2, y2 = to_physical(args.get("x2"), args.get("y2"))
    dur = max(0.08, _num(args.get("duration"), 260) / 1000.0)
    do_swipe(x1, y1, x2, y2, dur)
    _log("ai", f"滑动 ({x1},{y1}) → ({x2},{y2})")
    _refresh_env()
    if touch_ready():
        return (f"已从 ({x1},{y1}) 滑动到 ({x2},{y2})（触控滑动，系统会带惯性）。"
                "请重新 screen_shot 看滚动结果；滚动窗口内容也可直接用 scroll。")
    return (f"已从 ({x1},{y1}) 滑动到 ({x2},{y2})（{touch_capability()}，按鼠标拖动执行）。"
            "滚动窗口内容建议改用 scroll(delta)。")


def tool_select(args: dict) -> str:
    reason = _guard("select")
    if reason:
        return reason
    x1, y1 = to_physical(args.get("x1"), args.get("y1"))
    x2, y2 = to_physical(args.get("x2"), args.get("y2"))
    do_drag(x1, y1, x2, y2, max(0.2, _num(args.get("duration"), 420) / 1000.0), hold=0.05)
    _log("ai", f"选择区域 ({x1},{y1}) → ({x2},{y2})")
    _refresh_env()
    return f"已拖选区域 ({x1},{y1}) → ({x2},{y2})，随后可用 copy 复制选中内容。"


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
    do_type_text(text)
    _log("ai", f"输入文本（{len(text)} 字符）：{text[:60]}")
    _refresh_env()
    return f"已输入 {len(text)} 个字符。请重新 screen_shot 确认输入是否正确。"


def tool_key_press(args: dict) -> str:
    reason = _guard("key_press")
    if reason:
        return reason
    key = str(args.get("key") or "").strip()
    if not key:
        raise ValueError("key 为空（如 enter / ctrl+c / win+ctrl+d）")
    do_key(key)
    _log("ai", f"按键 {key}")
    _refresh_env()
    return f"已发送按键 {key}。请重新 screen_shot 验证结果。"


def tool_copy(args: dict) -> str:
    reason = _guard("copy")
    if reason:
        return reason
    do_key("ctrl+c")
    time.sleep(0.18)
    text = clipboard_get()
    _log("ai", f"复制（剪贴板 {len(text)} 字符）")
    if not text:
        return "已发送 Ctrl+C，但剪贴板为空（可能没有选中内容）。"
    return f"已复制，剪贴板内容（{len(text)} 字符）：\n{text[:1500]}"


def tool_paste(args: dict) -> str:
    reason = _guard("paste")
    if reason:
        return reason
    text = args.get("text")
    if text not in (None, "") and not clipboard_set(str(text)):
        raise RuntimeError("写入剪贴板失败")
    do_key("ctrl+v")
    time.sleep(0.15)
    cur = clipboard_get()
    _log("ai", f"粘贴（剪贴板 {len(cur)} 字符）")
    return f"已发送 Ctrl+V 粘贴。剪贴板内容：{cur[:300] or '(空)'}。请重新 screen_shot 验证。"


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
            "请用 browser_open 打开给用户，用户可在该界面实时观看操控画面、查看操作日志，"
            "并可一键「接管 / 暂停 / 恢复」。")


TOOLS = [
    {"name": "screen_info", "description":
        "查看电脑操控环境：屏幕物理分辨率、输入方式（触控/鼠标）、操控容器（前台/虚拟桌面）、"
        "前台窗口、鼠标位置、接管与暂停状态。开始操控前先调用。",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "screen_shot", "description":
        "截屏并返回图像给你查看（叠加 16×16 坐标网格、像素刻度与鼠标准星）。"
        "坐标基准=图像像素，点击/拖拽等工具的 x/y 直接用图中读到的刻度数值即可（插件自动换算物理坐标）。"
        "mode=full 整屏（默认）；mode=window 只截某个窗口（配合 list_windows 的 title/hwnd）。"
        "每次行动前后都应截屏：先看准目标，行动后再验证结果。",
     "inputSchema": {"type": "object", "properties": {
         "mode": {"type": "string", "description": "full=整屏（默认），window=指定窗口"},
         "title": {"type": "string", "description": "窗口标题片段（mode=window 时用）"},
         "hwnd": {"type": "integer", "description": "窗口句柄（mode=window 时用，来自 list_windows）"},
         "grid": {"type": "boolean", "description": "是否叠加坐标网格（默认 true）"}}}},
    {"name": "zoom_in", "description":
        "放大屏幕局部以精确定位（两步定位第二步）：x/y 为当前截图基准坐标，"
        "size 为放大区域边长（默认 400），factor 为放大倍数（默认 3）。",
     "inputSchema": {"type": "object", "properties": {
         "x": {"type": "integer", "description": "中心 x（当前截图坐标）"},
         "y": {"type": "integer", "description": "中心 y（当前截图坐标）"},
         "size": {"type": "integer", "description": "放大区域边长，默认 400"},
         "factor": {"type": "integer", "description": "放大倍数，默认 3"}},
         "required": ["x", "y"]}},
    {"name": "list_windows", "description":
        "列出当前可见窗口（标题/位置/尺寸/句柄），用于选择操控目标窗口。",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "click", "description":
        "点击指定位置（有触控驱动的设备走真实触控 tap）。x/y 为当前截图基准坐标；"
        "button=left/right/middle；clicks=点击次数（1-3）。",
     "inputSchema": {"type": "object", "properties": {
         "x": {"type": "integer", "description": "x 坐标（截图基准）"},
         "y": {"type": "integer", "description": "y 坐标（截图基准）"},
         "button": {"type": "string", "description": "left（默认）/right/middle"},
         "clicks": {"type": "integer", "description": "点击次数，默认 1"}},
         "required": ["x", "y"]}},
    {"name": "long_press", "description":
        "长按指定位置：触控设备为真实触控长按（可呼出上下文菜单），无触控时为按住鼠标不动"
        "（需要右键菜单请用 click(button=\"right\")）。",
     "inputSchema": {"type": "object", "properties": {
         "x": {"type": "integer", "description": "x 坐标（截图基准）"},
         "y": {"type": "integer", "description": "y 坐标（截图基准）"},
         "duration": {"type": "integer", "description": "按住时长毫秒，默认 800"}},
         "required": ["x", "y"]}},
    {"name": "drag", "description":
        "拖拽：从 (x1,y1) 按住拖到 (x2,y2)，用于移动文件/图标、拖滑块、拖动窗口等。",
     "inputSchema": {"type": "object", "properties": {
         "x1": {"type": "integer", "description": "起点 x（截图基准）"},
         "y1": {"type": "integer", "description": "起点 y（截图基准）"},
         "x2": {"type": "integer", "description": "终点 x（截图基准）"},
         "y2": {"type": "integer", "description": "终点 y（截图基准）"},
         "duration": {"type": "integer", "description": "拖拽时长毫秒，默认 500"}},
         "required": ["x1", "y1", "x2", "y2"]}},
    {"name": "swipe", "description":
        "滑动：从 (x1,y1) 快速滑到 (x2,y2)。触控设备带系统惯性，适合滚动列表/翻页/下拉；"
        "无触控设备退化为鼠标拖动（滚动内容建议改用 scroll）。",
     "inputSchema": {"type": "object", "properties": {
         "x1": {"type": "integer", "description": "起点 x（截图基准）"},
         "y1": {"type": "integer", "description": "起点 y（截图基准）"},
         "x2": {"type": "integer", "description": "终点 x（截图基准）"},
         "y2": {"type": "integer", "description": "终点 y（截图基准）"},
         "duration": {"type": "integer", "description": "滑动时长毫秒，默认 260"}},
         "required": ["x1", "y1", "x2", "y2"]}},
    {"name": "select", "description":
        "选择：从 (x1,y1) 拖选到 (x2,y2) 选中文本/区域（随后可用 copy 复制）。",
     "inputSchema": {"type": "object", "properties": {
         "x1": {"type": "integer", "description": "起点 x（截图基准）"},
         "y1": {"type": "integer", "description": "起点 y（截图基准）"},
         "x2": {"type": "integer", "description": "终点 x（截图基准）"},
         "y2": {"type": "integer", "description": "终点 y（截图基准）"}},
         "required": ["x1", "y1", "x2", "y2"]}},
    {"name": "scroll", "description":
        "滚轮滚动：delta 正值向上、负值向下（120 为一格）；可传 x/y 先把指针移到该处再滚动。",
     "inputSchema": {"type": "object", "properties": {
         "delta": {"type": "integer", "description": "滚动量（正上负下，120=一格）"},
         "x": {"type": "integer", "description": "可选：滚动前把指针移到此处（截图基准 x）"},
         "y": {"type": "integer", "description": "可选：滚动前把指针移到此处（截图基准 y）"}},
         "required": ["delta"]}},
    {"name": "type_text", "description":
        "输入文本（Unicode 注入，支持中文/符号；先点击输入框使其获得焦点再调用）。",
     "inputSchema": {"type": "object", "properties": {
         "text": {"type": "string", "description": "要输入的文本"}},
         "required": ["text"]}},
    {"name": "key_press", "description":
        "按键/组合键：enter、tab、esc、backspace、f5、ctrl+c、ctrl+shift+s、win+ctrl+d 等。",
     "inputSchema": {"type": "object", "properties": {
         "key": {"type": "string", "description": "键名或组合键，如 ctrl+c"}},
         "required": ["key"]}},
    {"name": "copy", "description":
        "复制当前选中内容（Ctrl+C）并返回剪贴板文本（先 select 或点击目标再调用）。",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "paste", "description":
        "粘贴剪贴板内容（Ctrl+V）；可传 text 先写入剪贴板再粘贴。",
     "inputSchema": {"type": "object", "properties": {
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
    {"name": "web_url", "description":
        "返回本地可视化界面的访问地址（用 browser_open 打开给用户实时观看操控过程与日志）。",
     "inputSchema": {"type": "object", "properties": {}}},
]

_HANDLERS = {
    "screen_info": tool_screen_info,
    "screen_shot": tool_screen_shot,
    "zoom_in": tool_zoom_in,
    "list_windows": tool_list_windows,
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
    "web_url": tool_web_url,
}


# ============================================================================
# 本地 HTTP 可视化界面（web UI）
# ============================================================================
_WEB_PORT = 0
_preview_lock = threading.Lock()
_preview_cache = {"ts": 0.0, "png": b"", "grid": True}


def _preview_png(grid: bool) -> bytes:
    """预览截图（带缓存：页面高频轮询时不为同一帧反复抓屏编码，性能优先）。"""
    now = time.time()
    with _preview_lock:
        if (_preview_cache["png"] and _preview_cache["grid"] == grid
                and now - _preview_cache["ts"] < PREVIEW_MIN_INTERVAL):
            return _preview_cache["png"]
    png, _iw, _ih, _sw, _sh = grab_screen(max_w=PREVIEW_MAX_W, grid=grid, level=1)
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
        <label class="sw"><input type="checkbox" id="grid" checked> 网格</label>
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
            grid = q.get("grid", ["1"])[0] not in ("0", "false", "no")
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
        if name != "web_url":
            _touch_session()     # 任何操控相关调用都续期操控会话（真人输入据此判接管）
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
            png, iw, ih, _sw, _sh = grab_screen(max_w=PREVIEW_MAX_W, level=1)
            out["capture"] = f"ok {iw}x{ih} {len(png)}B"
        except Exception as e:
            out["capture"] = f"不可用: {e}"

    _STATE["interrupted"] = False
    _STATE["paused"] = False
    out["ok"] = out["ok"] and all(v == "ok" for v in out["endpoints"].values())
    sys.stdout.buffer.write(json.dumps(out, ensure_ascii=False).encode("utf-8") + b"\n")
    sys.stdout.buffer.flush()
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    main()