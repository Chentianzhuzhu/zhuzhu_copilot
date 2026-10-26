"""探针：打开 QQ 聊天「...」菜单后，列出 QQ 进程的全部窗口与落点窗口，判断菜单为何点不动。"""
import ctypes
import importlib.util
import sys
import time
from ctypes import wintypes
from pathlib import Path

SERVER = Path(r"c:\Users\zhuzhu\Desktop\zhuzhu Copilot\src\zhuzhu_Copilot\plugins\computer-control\server.py")
spec = importlib.util.spec_from_file_location("ccserver", SERVER)
mod = importlib.util.module_from_spec(spec)
sys.modules["ccserver"] = mod
spec.loader.exec_module(mod)
mod._init_dpi_awareness()

u = mod.user32
# 先点开「...」菜单
print(mod.tool_click_window({"hwnd": 40307840, "rel_x": 984, "rel_y": 70,
                              "button": "left", "clicks": 1, "snap": False})[:60], flush=True)
time.sleep(0.8)

QQ_PID = 6928
rows = []


def cb(hwnd, lparam):
    pid = wintypes.DWORD()
    u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value != QQ_PID:
        return True
    r = mod.window_rect(hwnd)
    ex = mod._ex_style(hwnd)
    rows.append((hwnd, mod.win32_class_name(hwnd), bool(u.IsWindowVisible(hwnd)),
                 (r.left, r.top, r.right - r.left, r.bottom - r.top),
                 hex(ex), mod.window_title(hwnd)))
    return True


mod.EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
u.EnumWindows(mod.EnumWindowsProc(cb), 0)

print("QQ 进程窗口：")
for hwnd, cls, vis, (x, y, w, h), ex, title in rows:
    print(f"  {hwnd} vis={vis} {cls[:26]:26} rect=({x},{y},{w}x{h}) ex={ex} title={title!r}")

for pt in [(1600, 112), (1600, 358), (1700, 300)]:
    h = u.WindowFromPoint(wintypes.POINT(*pt))
    root = u.GetAncestor(h, 2) or h
    print(pt, "->", h, "root", root, mod.win32_class_name(h), "|", mod.win32_class_name(root),
          "| own", mod.is_own_window(root))
