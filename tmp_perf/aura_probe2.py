"""临时探针 2：确认光带线程是否真的在画、推帧是否成功。"""
import importlib.util
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
p = ROOT / "src" / "zhuzhu_Copilot" / "plugins" / "computer-control" / "aura.py"
spec = importlib.util.spec_from_file_location("aura_probe_mod2", p)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

calls = {"draw": 0, "push": 0, "ulw_ret": 0, "ulw_fail": 0}
orig_draw = m.Aura._draw_ring
orig_push = m._LayeredWindow.push


def draw(self, now):
    calls["draw"] += 1
    return orig_draw(self, now)


def push(self, sca=255):
    calls["push"] += 1
    r = orig_push(self, sca)
    return r


m.Aura._draw_ring = draw
m._LayeredWindow.push = push

a = m._aura
print("start ->", a.set_active(True) or True)
for i in range(6):
    time.sleep(0.4)
    print(i, "active", a.active, "stop", a._stop.is_set(),
          "ring_alive", bool(a._thread and a._thread.is_alive()),
          "hint_alive", bool(a._hint_thread and a._hint_thread.is_alive()),
          "start_ts", round(a._start_ts, 3),
          "cost_ms", round(a._frame_cost * 1000, 2),
          "failed", a._failed, a._error,
          "calls", calls)
a.set_active(False)
time.sleep(0.9)
print("final bands", len(a._bands), "stop", a._stop.is_set())
a.stop()
