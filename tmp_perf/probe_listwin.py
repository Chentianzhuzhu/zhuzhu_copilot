"""探针：复现 list_windows(kind=menu) 卡住，并在 6s 后 dump 服务端栈。"""
import faulthandler
import importlib.util
import sys
import time
from pathlib import Path

SERVER = Path(r"c:\Users\zhuzhu\Desktop\zhuzhu Copilot\src\zhuzhu_Copilot\plugins\computer-control\server.py")
spec = importlib.util.spec_from_file_location("ccserver", SERVER)
mod = importlib.util.module_from_spec(spec)
sys.modules["ccserver"] = mod
spec.loader.exec_module(mod)
print("imported ok", flush=True)

faulthandler.dump_traceback_later(6, exit=False)

for kinds in [(), ("menu",), ("main",)]:
    t0 = time.perf_counter()
    try:
        r = mod.list_windows(kinds=kinds)
        print(f"kinds={kinds} -> {len(r)} windows in {(time.perf_counter()-t0)*1000:.0f}ms", flush=True)
        for w in r[:6]:
            print(f"   {w['hwnd']} {w['kind']} {w['title'][:30]!r} {w['class'][:30]}", flush=True)
    except Exception as e:
        print(f"kinds={kinds} raised {type(e).__name__}: {e}", flush=True)
faulthandler.cancel_dump_traceback_later()
print("done", flush=True)
