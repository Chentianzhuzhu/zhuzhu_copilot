"""气泡 HTML 体积归因：按段类型 / 折叠态统计生成的富文本体积与耗时。"""
import json
import os
import pathlib
import sys
import time
from collections import Counter

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, "src")

from PyQt6.QtWidgets import QApplication

app = QApplication([])
from winapp_migrator.ui import agent_panel as ap

SESS = pathlib.Path.home() / ".winapp_migrator" / "agent" / "sessions"
f = max(SESS.glob("*.ui.json"), key=lambda p: p.stat().st_size)
data = json.loads(f.read_text(encoding="utf-8"))
segs = data.get("segments") or []

panel = ap.AgentPanel()
panel.resize(1500, 950)
for _ in range(8):
    app.processEvents()

s = panel._font_scale()
f_main, f_sm, f_op = int(14 * s), int(11 * s), int(13 * s)
img_w = max(200, int(panel._bubble_max_width() * 0.4))

size_by_type = Counter()
time_by_type = Counter()
n_by_type = Counter()
collapsed_by_type = Counter()
top = []
for i, seg in enumerate(segs):
    t = seg["type"]
    t0 = time.perf_counter()
    html = panel._render_seg_html(seg, i, t, f_main, f_sm, f_op, img_w) or ""
    dt = (time.perf_counter() - t0) * 1000
    size_by_type[t] += len(html)
    time_by_type[t] += dt
    n_by_type[t] += 1
    if seg.get("collapsed"):
        collapsed_by_type[t] += 1
    top.append((len(html), t, bool(seg.get("collapsed")), dt))

print(f"会话 {f.name} 段数={len(segs)}", flush=True)
print(f"{'类型':10s} {'段数':>5s} {'已折叠':>6s} {'HTML字符':>10s} {'生成耗时ms':>10s}")
for t, n in n_by_type.most_common():
    print(f"{t:10s} {n:5d} {collapsed_by_type[t]:6d} {size_by_type[t]:10d} "
          f"{time_by_type[t]:10.1f}", flush=True)
total = sum(size_by_type.values())
print(f"合计 HTML = {total} 字符  生成耗时 = {sum(time_by_type.values()):.0f} ms", flush=True)
top.sort(reverse=True)
print("\n最大的 8 段：", flush=True)
for sz, t, col, dt in top[:8]:
    print(f"  {t:8s} collapsed={col!s:5s} {sz:8d} 字符  {dt:7.1f} ms", flush=True)
os._exit(0)
