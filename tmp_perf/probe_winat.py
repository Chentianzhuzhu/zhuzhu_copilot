"""探针：看 (1697,358) 这个点上到底是哪扇窗口（判断提示层是否挡住了点击）。"""
import importlib.util
import sys
import time
import ctypes
from pathlib import Path

SERVER = Path(r"c:\Users\zhuzhu\Desktop\zhuzhu Copilot\src\zhuzhu_Copilot\plugins\computer-control\server.py")
spec = importlib.util.spec_from_file_location("ccserver", SERVER)
mod = importlib.util.module_from_spec(spec)
sys.modules["ccserver"] = mod
spec.loader.exec_module(mod)
mod._init_dpi_awareness()


class W(ctypes.Structure):
    _fields_ = [("h", ctypes.c_void_p)]


for pt in [(1697, 358), (805, 858), (1760, 235), (960, 540)]:
    h = mod.window_at(*pt)
    cls = mod.win32_class_name(h)
    title = mod.window_title(h)
    pid = ctypes.wintypes.DWORD()
    mod.user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
    print(pt, "->", h, repr(title), cls, "pid", pid.value, "own", mod.is_own_window(h))
print("our pid", __import__("os").getpid())
