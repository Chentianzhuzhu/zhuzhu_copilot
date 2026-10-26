"""临时探针 3：光带线程卡住时 dump 栈。"""
import faulthandler
import importlib.util
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
p = ROOT / "src" / "zhuzhu_Copilot" / "plugins" / "computer-control" / "aura.py"
spec = importlib.util.spec_from_file_location("aura_probe_mod3", p)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

a = m._aura
a.set_active(True)
time.sleep(2.0)
print("frame_cost", a._frame_cost, "draw-called check via bands")
faulthandler.dump_traceback_later(0.1, exit=False)
time.sleep(0.2)
faulthandler.cancel_dump_traceback_later()
a.set_active(False)
time.sleep(1.0)
a.stop()
