"""外部探针：模拟点任务栏图标（SC_MINIMIZE / SC_RESTORE），并报告窗口状态时间线。"""
import ctypes
import ctypes.wintypes as wt
import sys
import time

u32 = ctypes.windll.user32
EP = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
u32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
u32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
u32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]

WM_SYSCOMMAND = 0x0112
SC_MIN = 0xF020
SC_RES = 0xF120
SW_RESTORE = 9


def enum():
    out = []

    def cb(h, _l):
        n = u32.GetWindowTextLengthW(h)
        if n:
            b = ctypes.create_unicode_buffer(n + 1)
            u32.GetWindowTextW(h, b, n + 1)
            if b.value == "zhuzhu Copilot":
                r = wt.RECT()
                u32.GetWindowRect(h, ctypes.byref(r))
                out.append((h, b.value, (r.left, r.top, r.right - r.left, r.bottom - r.top)))
        return True

    u32.EnumWindows(EP(cb), 0)
    return out


def pick():
    for h, _t, r in enum():
        if r[2] > 400 and r[3] > 300:
            return h
    return None


h = pick()
if not h:
    print("NO_TARGET (window may be minimized)")
    sys.exit(1)
print("target", h)

if u32.IsIconic(h):
    u32.ShowWindow(h, SW_RESTORE)
    time.sleep(1.0)

for rnd in (1, 2):
    print("--- round %d ---" % rnd)
    t0 = time.monotonic()
    u32.PostMessageW(h, WM_SYSCOMMAND, SC_MIN, 0)
    for _ in range(14):
        time.sleep(0.05)
        print("  +%4.0fms iconic=%-5s vis=%s" % (
            (time.monotonic() - t0) * 1000, u32.IsIconic(h), u32.IsWindowVisible(h)))
    time.sleep(0.4)
    t0 = time.monotonic()
    u32.PostMessageW(h, WM_SYSCOMMAND, SC_RES, 0)
    for _ in range(14):
        time.sleep(0.05)
        r = wt.RECT()
        u32.GetWindowRect(h, ctypes.byref(r))
        print("  +%4.0fms iconic=%-5s rect=%s" % (
            (time.monotonic() - t0) * 1000, u32.IsIconic(h),
            (r.left, r.top, r.right - r.left, r.bottom - r.top)))
    time.sleep(0.6)
print("done")
