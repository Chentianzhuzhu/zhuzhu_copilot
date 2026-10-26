"""探针：验证 window_title / is_own_window 是否正常（不吞异常）。"""
import importlib.util
import sys
import time
import traceback
from pathlib import Path

SERVER = Path(r"c:\Users\zhuzhu\Desktop\zhuzhu Copilot\src\zhuzhu_Copilot\plugins\computer-control\server.py")
spec = importlib.util.spec_from_file_location("ccserver", SERVER)
mod = importlib.util.module_from_spec(spec)
sys.modules["ccserver"] = mod
spec.loader.exec_module(mod)

QQ = 40307840
print("own pid", __import__("os").getpid())
try:
    print("is_own_window(QQ) =", mod.is_own_window(QQ))
except Exception:
    traceback.print_exc()
t0 = time.perf_counter()
try:
    print("window_title(QQ) =", repr(mod.window_title(QQ)), f"{(time.perf_counter()-t0)*1000:.1f}ms")
except Exception:
    traceback.print_exc()

# 逐个窗口跑一遍，打印任何异常与耗时
for hwnd in mod._enum_top_hwnds():
    try:
        own = mod.is_own_window(hwnd)
        t = time.perf_counter()
        title = mod.window_title(hwnd)
        dt = (time.perf_counter() - t) * 1000
        if own or dt > 20:
            print(f"hwnd={hwnd} own={own} title={title!r} {dt:.1f}ms", flush=True)
    except Exception as e:
        print(f"hwnd={hwnd} EXC {type(e).__name__}: {e}")
        traceback.print_exc()
print("scan done")
