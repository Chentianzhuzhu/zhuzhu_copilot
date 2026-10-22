"""高速抓帧（窄竖条）：看窗口内容的上/下边缘随时间怎么走。

折叠（我们的浮层）：上边缘固定不动，下边缘上移 → 高度收缩。
DWM 系统最小化动画：整块内容等比缩小并朝任务栏方向沉 → 上边缘下移、下边缘也在动。
"""
import ctypes
import ctypes.wintypes as wt
import threading
import time

from PIL import Image, ImageDraw

u32 = ctypes.windll.user32
g32 = ctypes.windll.gdi32
EP = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
u32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
u32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]

WM_SYSCOMMAND = 0x0112
SC_MIN = 0xF020
SC_RES = 0xF120


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
    for it in out:
        if it[3] > 400 and it[4] > 300:
            return it
    return None


t = find()
if not t:
    raise SystemExit("NO_TARGET")
hwnd, x0, y0, w, h = t
print("target", hwnd, (x0, y0, w, h))

u32.ShowWindow(hwnd, 9)
u32.SetForegroundWindow(hwnd)
time.sleep(0.7)

STRIP_W = 96
STRIP_X = x0 + w // 2 - STRIP_W // 2
STRIP_Y = y0 - 20
STRIP_H = 1080 - STRIP_Y

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
bmi.bmiHeader.biWidth = STRIP_W
bmi.bmiHeader.biHeight = -STRIP_H
bmi.bmiHeader.biPlanes = 1
bmi.bmiHeader.biBitCount = 32
ppv = ctypes.c_void_p()
hbmp = g32.CreateDIBSection(screen_dc, ctypes.byref(bmi), 0, ctypes.byref(ppv), None, 0)
g32.SelectObject(mem_dc, hbmp)
BUF = (ctypes.c_ubyte * (STRIP_W * STRIP_H * 4)).from_address(ppv.value)


def shot():
    g32.BitBlt(mem_dc, 0, 0, STRIP_W, STRIP_H, screen_dc, STRIP_X, STRIP_Y, 0x00CC0020)
    return Image.frombuffer("RGB", (STRIP_W, STRIP_H), bytes(BUF), "raw", "BGRX", 0, 1)


def poster(delay, cmd):
    def _f():
        time.sleep(delay)
        u32.PostMessageW(hwnd, WM_SYSCOMMAND, cmd, 0)
    threading.Thread(target=_f, daemon=True).start()


def record(cmd, span=0.8):
    poster(0.15, cmd)
    t0 = time.monotonic()
    out = []
    while time.monotonic() - t0 < span:
        out.append(((time.monotonic() - t0) * 1000.0, shot()))
    return out


for name, cmd in (("min", SC_MIN), ("res", SC_RES)):
    frames = record(cmd)
    n = len(frames)
    sheet = Image.new("RGB", (n * (STRIP_W + 6), STRIP_H + 22), (30, 30, 30))
    d = ImageDraw.Draw(sheet)
    for i, (ts, img) in enumerate(frames):
        cx = i * (STRIP_W + 6)
        sheet.paste(img, (cx, 20))
        d.text((cx + 2, 4), "%d" % ts, fill=(255, 220, 80))
    sheet.save("tmp_fx2/strip_%s.png" % name)
    print(name, "frames", n, "->", sheet.size, "avg %.0fms/frame" % (800.0 / max(1, n)))
    time.sleep(1.4)

g32.DeleteObject(hbmp)
g32.DeleteDC(mem_dc)
