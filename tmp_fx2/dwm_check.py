"""检测 DWM 自带最小化/还原动画是否在跑。

原理：我们的浮层恰好覆盖窗口矩形（下边缘 = 任务栏上边缘 1032），而 Windows 自带的
最小化动画会把窗口缩略图「沉入任务栏」—— 那部分像素落在任务栏条上（1034~1078），
浮层盖不到。任务栏本身是深色，缩略图掠过时该区域会明显变亮。

因此：统计任务栏条里「亮像素」比例。比例一直为 0 = 只有我们的折叠动画；
出现明显的亮块 = 系统动画又播了一遍（= 用户说的「重复播放」）。
"""
import ctypes
import ctypes.wintypes as wt
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

W, H = 900, 44


class BIH(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
                ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]


class BI(ctypes.Structure):
    _fields_ = [("bmiHeader", BIH), ("bmiColors", wt.DWORD * 3)]


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
                out.append((h, r.left, r.top, r.right - r.left, r.bottom - r.top))
        return True

    u32.EnumWindows(EP(cb), 0)
    big = [it for it in out if it[3] > 400 and it[4] > 300]
    return max(big, key=lambda it: it[3] * it[4]) if big else None


t = find()
if not t:
    raise SystemExit("NO_TARGET")
hwnd, x0, y0, w, h = t
print("target", hwnd, (x0, y0, w, h))

u32.ShowWindow(hwnd, 9)
u32.SetForegroundWindow(hwnd)
time.sleep(0.8)

u32.GetDC.restype = wt.HDC
g32.CreateCompatibleDC.restype = wt.HDC
g32.CreateCompatibleDC.argtypes = [wt.HDC]
g32.CreateDIBSection.restype = wt.HANDLE
g32.CreateDIBSection.argtypes = [wt.HDC, ctypes.POINTER(BI), wt.UINT,
                                 ctypes.POINTER(ctypes.c_void_p), wt.HANDLE, wt.DWORD]
g32.SelectObject.restype = wt.HANDLE
g32.SelectObject.argtypes = [wt.HDC, wt.HANDLE]
g32.BitBlt.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                       wt.HDC, ctypes.c_int, ctypes.c_int, wt.DWORD]
g32.DeleteObject.argtypes = [wt.HANDLE]
g32.DeleteDC.argtypes = [wt.HDC]

screen_dc = u32.GetDC(0)
mem_dc = g32.CreateCompatibleDC(screen_dc)
bmi = BI()
bmi.bmiHeader.biSize = ctypes.sizeof(BIH)
bmi.bmiHeader.biWidth = W
bmi.bmiHeader.biHeight = -H
bmi.bmiHeader.biPlanes = 1
bmi.bmiHeader.biBitCount = 32
ppv = ctypes.c_void_p()
hbmp = g32.CreateDIBSection(screen_dc, ctypes.byref(bmi), 0, ctypes.byref(ppv), None, 0)
g32.SelectObject(mem_dc, hbmp)
BUF = (ctypes.c_ubyte * (W * H * 4)).from_address(ppv.value)

CAP_X = x0 + (w - W) // 2
CAP_Y = 1034


def bright_ratio():
    g32.BitBlt(mem_dc, 0, 0, W, H, screen_dc, CAP_X, CAP_Y, 0x00CC0020)
    b = bytes(BUF)
    n = 0
    total = 0
    for i in range(0, len(b), 4 * 7):     # 抽样，约 1/7 像素
        if b[i] + b[i + 1] + b[i + 2] > 330:   # 平均亮度 > 110
            n += 1
        total += 1
    return n / total


def poster(delay, cmd):
    def _f():
        time.sleep(delay)
        u32.PostMessageW(hwnd, WM_SYSCOMMAND, cmd, 0)
    threading.Thread(target=_f, daemon=True).start()


for label, cmd in (("min", SC_MIN), ("res", SC_RES)):
    base = bright_ratio()
    poster(0.15, cmd)
    t0 = time.monotonic()
    rows = []
    while time.monotonic() - t0 < 0.75:
        rows.append(((time.monotonic() - t0) * 1000.0, bright_ratio()))
    peak = max(r[1] for r in rows)
    print("=== %s ===  baseline=%.3f  frames=%d  peak=%.3f  avg=%.3f" % (
        label, base, len(rows), peak, sum(r[1] for r in rows) / len(rows)))
    print("   " + " ".join("%.0f:%.2f" % (ts, v) for ts, v in rows[:28]))
    time.sleep(1.3)

g32.DeleteObject(hbmp)
g32.DeleteDC(mem_dc)
