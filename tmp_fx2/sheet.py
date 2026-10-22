"""抓整窗缩略帧，拼时序图（每帧标注毫秒）。"""
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

# 抓一整个「窗口 + 下方任务栏」的区域，才能看到是否沉入任务栏
CAP_Y = y0 - 8
CAP_H = (1080 - CAP_Y)
CAP_X = x0 - 8
CAP_W = w + 16

screen_dc = u32.GetDC(0)
g32.CreateCompatibleDC.restype = wt.HDC
g32.CreateCompatibleDC.argtypes = [wt.HDC]
g32.CreateDIBSection.restype = wt.HANDLE
g32.CreateDIBSection.argtypes = [wt.HDC, ctypes.POINTER(BI), wt.UINT,
                                 ctypes.POINTER(ctypes.c_void_p), wt.HANDLE, wt.DWORD]
g32.SelectObject.restype = wt.HANDLE
g32.SelectObject.argtypes = [wt.HDC, wt.HANDLE]
g32.BitBlt.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                       wt.HDC, ctypes.c_int, ctypes.c_int, wt.DWORD]
mem_dc = g32.CreateCompatibleDC(screen_dc)
bmi = BI()
bmi.bmiHeader.biSize = ctypes.sizeof(BIH)
bmi.bmiHeader.biWidth = CAP_W
bmi.bmiHeader.biHeight = -CAP_H
bmi.bmiHeader.biPlanes = 1
bmi.bmiHeader.biBitCount = 32
bmi.bmiHeader.biCompression = 0
ppv = ctypes.c_void_p()
g32.CreateDIBSection.restype = ctypes.c_void_p
hbmp = g32.CreateDIBSection(screen_dc, ctypes.byref(bmi), 0, ctypes.byref(ppv), None, 0)
g32.SelectObject(mem_dc, hbmp)
BUF = (ctypes.c_ubyte * (CAP_W * CAP_H * 4)).from_address(ppv.value)


def shot():
    g32.BitBlt(mem_dc, 0, 0, CAP_W, CAP_H, screen_dc, CAP_X, CAP_Y, 0x00CC0020)
    img = Image.frombuffer("RGB", (CAP_W, CAP_H), bytes(BUF), "raw", "BGRX", 0, 1)
    return img.resize((CAP_W // 4, CAP_H // 4), Image.BILINEAR)


def poster(delay, cmd):
    def _f():
        time.sleep(delay)
        u32.PostMessageW(hwnd, WM_SYSCOMMAND, cmd, 0)
    threading.Thread(target=_f, daemon=True).start()


def record(cmd, span=0.85):
    poster(0.12, cmd)
    t0 = time.monotonic()
    out = []
    while time.monotonic() - t0 < span:
        out.append(((time.monotonic() - t0) * 1000.0, shot()))
    return out


for name, cmd in (("min", SC_MIN), ("res", SC_RES)):
    frames = record(cmd)
    cols = 6
    tw, th = frames[0][1].size
    rows = (len(frames) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * tw, rows * (th + 18)), (24, 24, 24))
    d = ImageDraw.Draw(sheet)
    for i, (ts, img) in enumerate(frames):
        cx, cy = (i % cols) * tw, (i // cols) * (th + 18)
        sheet.paste(img, (cx, cy + 18))
        d.text((cx + 4, cy + 3), "+%dms" % ts, fill=(255, 220, 80))
    sheet.save("tmp_fx2/sheet_%s.png" % name)
    print(name, "frames", len(frames), "->", sheet.size)
    time.sleep(1.3)

g32.DeleteObject(hbmp)
g32.DeleteDC(mem_dc)
u32.ReleaseDC(0, screen_dc)
