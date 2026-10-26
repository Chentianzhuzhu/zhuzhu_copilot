"""探针：导入 server 后真实点击汉堡，再调 list_windows(kind=menu)，卡住就 dump 栈。"""
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
mod._init_dpi_awareness()
print("imported ok", flush=True)

faulthandler.dump_traceback_later(10, exit=False)

t0 = time.perf_counter()
print("list_windows before:", len(mod.list_windows(kinds=("menu",))),
      f"{(time.perf_counter()-t0)*1000:.0f}ms", flush=True)

t0 = time.perf_counter()
out = mod.tool_click_window({"hwnd": 40307840, "rel_x": 29, "rel_y": 693,
                             "button": "left", "clicks": 1, "snap": False})
print("click_window:", out[:200], f"{(time.perf_counter()-t0)*1000:.0f}ms", flush=True)

time.sleep(0.4)
t0 = time.perf_counter()
wins = mod.list_windows(kinds=("menu",))
print("menus:", [(w["hwnd"], w["title"], w["class"], w["w"], w["h"]) for w in wins],
      f"{(time.perf_counter()-t0)*1000:.0f}ms", flush=True)

t0 = time.perf_counter()
shot = mod.tool_screen_shot({"mode": "full"})
print("shot text:", shot[:120], f"{(time.perf_counter()-t0)*1000:.0f}ms", flush=True)
faulthandler.cancel_dump_traceback_later()
print("done", flush=True)
