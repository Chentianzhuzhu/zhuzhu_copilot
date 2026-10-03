"""长会话渲染路径 cProfile：定位 _render_history_all 的数秒耗时构成。"""
import sys
import os
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from zhuzhu_Copilot import app_identity
import cProfile
import io
import json
import pstats
import time

from PyQt6.QtWidgets import QApplication

app = QApplication([])
from zhuzhu_Copilot.ui import agent_panel as ap

SESS = app_identity.data_root() / "agent" / "sessions"
f = max(SESS.glob("*.ui.json"), key=lambda p: p.stat().st_size)
data = json.loads(f.read_text(encoding="utf-8"))
rows = data.get("rows") or []
segs = data.get("segments") or []
ums = data.get("user_msgs") or []

panel = ap.AgentPanel()
panel.resize(1500, 950)
panel.show()
for _ in range(10):
    app.processEvents()

panel._user_msgs = list(ums)
panel._rows = [dict(r) for r in rows]
panel._history_segments = [dict(s) for s in segs]
panel._seg_cache.clear()

pr = cProfile.Profile()
t0 = time.perf_counter()
pr.enable()
panel._render_history_all()
for _ in range(10):
    app.processEvents()
pr.disable()
print(f"render total = {time.perf_counter() - t0:.2f}s  bubbles={len(panel._bubble_widgets)}",
      flush=True)
s = io.StringIO()
pstats.Stats(pr, stream=s).sort_stats("tottime").print_stats(22)
print(s.getvalue()[:5000], flush=True)
os._exit(0)
