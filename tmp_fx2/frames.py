"""高速抓帧：用 GDI BitBlt 抓一条竖条，逐帧给出「窗口可见区域的上下边缘」。

目的：区分
  A) 我们的浮层在折叠（上边缘不动，下边缘上移 → 高度收缩）
  B) Windows 自带的 DWM 最小化动画（整块内容缩小并向下沉向任务栏）
  C) 两者叠加（= 用户说的「重复播放」）
"""
import ctypes
import ctypes.wintypes as wt
import sys
import threading
import time

u32 = ctypes.windll.user32
g32 = ctypes.windll.gdi32

EP = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
u32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
u32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]

WM_SYSCOMMAND = 0x0112
SC_MIN = 0xF020
SC_RES = 0xF120

STRIP_W = 120
SCR_H = 1080
STRIP_X = 900


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
                ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


def find():
    out = []

    def cb(h, _l):
        n = u32.GetWindowTextLengthW(h)
        if n:
            b = ctypes.create_unicode_buffer(n + 1)
            u32.GetWindowTextW(h, b, n + 1)
            if b.value == "zhuzhu Copilot":
                r = wt.RECT()
                u32.GetWindowRect(h, ctypes.byref(r))
                out.append((h, r.right - r.left, r.bottom - r.top))
        return True

    u32.EnumWindows(EP(cb), 0)
    for h, w, hh in out:
        if w > 400 and hh > 300:
            return h
    return None


hwnd = find()
if not hwnd:
    print("NO_TARGET")
    sys.exit(1)

screen_dc = u32.GetDC(0)
mem_dc = g32.CreateCompatibleDC(screen_dc)
bmi = BITMAPINFO()
bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
bmi.bmiHeader.biWidth = STRIP_W
bmi.bmiHeader.biHeight = -SCR_H      # top-down
bmi.bmiHeader.biPlanes = 1
bmi.bmiHeader.biBitCount = 32
bmi.bmiHeader.biCompression = 0
ppv = ctypes.c_void_p()
g32.CreateDIBSection.restype = ctypes.c_void_p
hbmp = g32.CreateDIBSection(screen_dc, ctypes.byref(bmi), 0, ctypes.byref(ppv), None, 0)
g32.SelectObject(mem_dc, hbmp)
BUF = (ctypes.c_ubyte * (STRIP_W * SCR_H * 4)).from_address(ppv.value)


def shot():
    g32.BitBlt(mem_dc, 0, 0, STRIP_W, SCR_H, screen_dc, STRIP_X, 0, 0x00CC0020)
    return bytes(BUF)


def profile(img):
    """每一行的平均亮度（灰度），返回长度 SCR_H 的列表。"""
    out = [0] * SCR_H
    for y in range(SCR_H):
        base = y * STRIP_W * 4
        s = 0
        for x in range(0, STRIP_W, 4):
            i = base + x * 4
            s += img[i] + img[i + 1] + img[i + 2]
        out[y] = s // (STRIP_W // 4 * 3)
    return out


def diff_rows(a, b, thr=10):
    """返回相对基线的差异行区间。"""
    rows = [y for y in range(SCR_H) if abs(a[y] - b[y]) > thr]
    return (rows[0], rows[-1]) if rows else (None, None)


baseline = profile(shot())
print("baseline captured, target", hwnd)

# 保证目标窗口在最前面（否则 BitBlt 抓到的是 IDE）
u32.ShowWindow(hwnd, 9)
u32.SetForegroundWindow(hwnd)
time.sleep(0.8)
baseline = profile(shot())


def poster(delay, cmd):
    def _f():
        time.sleep(delay)
        u32.PostMessageW(hwnd, WM_SYSCOMMAND, cmd, 0)
    threading.Thread(target=_f, daemon=True).start()


def record(label, cmd, span=0.9):
    print("=== %s ===" % label)
    poster(0.12, cmd)
    t0 = time.monotonic()
    frames = []
    while time.monotonic() - t0 < span:
        frames.append((time.monotonic() - t0, shot()))
    for ts, img in frames:
        top, bot = diff_rows(baseline, profile(img))
        print("  +%4.0fms top=%-5s bot=%-5s h=%s" % (
            ts * 1000, top, bot, (bot - top + 1) if top is not None else 0))
    print("  frames:", len(frames))


record("minimize", SC_MIN)
time.sleep(1.2)
baseline = profile(shot())
record("restore", SC_RES)

g32.DeleteObject(hbmp)
g32.DeleteDC(mem_dc)
u32.ReleaseDC(0, screen_dc)
