"""气泡 HTML 体积与 QLabel 富文本布局耗时（定位长对话卡顿的最小复现）。

输出：HTML 相对原文的放大倍率、setText / heightForWidth / resize 的单次耗时。
用 os._exit(0) 跳过 Qt 退出期（离屏下大窗口析构偶发崩溃，与结论无关）。
"""
from zhuzhu_Copilot import app_identity
import json
import os
import pathlib
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, "src")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QLabel

app = QApplication([])
from zhuzhu_Copilot.ui import agent_panel as ap

SESS = app_identity.data_root() / "agent" / "sessions"
f = max(SESS.glob("*.ui.json"), key=lambda p: p.stat().st_size)
d = json.loads(f.read_text(encoding="utf-8"))
segs = d.get("segments") or []
raw = sum(len(s.get(k) or "") for s in segs
          for k in ("text", "content", "title", "html") if isinstance(s.get(k), str))
print(f"会话 {f.name}: segments={len(segs)} 原文={raw}", flush=True)

panel = ap.AgentPanel()
panel.resize(1500, 950)
panel.show()
for _ in range(10):
    app.processEvents()

t0 = time.perf_counter()
html = panel._build_ai_html(segs)
print(f"_build_ai_html: HTML={len(html)} 字符  放大 {len(html) / max(raw, 1):.1f}x  "
      f"耗时 {(time.perf_counter() - t0) * 1000:.1f}ms", flush=True)

lab = QLabel()
lab.setWordWrap(True)
lab.setTextFormat(Qt.TextFormat.RichText)
lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
t0 = time.perf_counter()
lab.setText(html)
print(f"QLabel.setText(HTML)   {(time.perf_counter() - t0) * 1000:8.1f} ms", flush=True)
lab.resize(900, 100)
for w in (900, 901, 950):
    t0 = time.perf_counter()
    h = lab.heightForWidth(w)
    print(f"heightForWidth({w})    {(time.perf_counter() - t0) * 1000:8.1f} ms  -> h={h}",
          flush=True)

# 对照：同样内容按 4 万字符切块后，逐块布局的总耗时
def _chunks(s, n):
    return [s[i:i + n] for i in range(0, len(s), n)]

total = 0.0
for c in _chunks(html, 40000):
    l2 = QLabel()
    l2.setWordWrap(True)
    l2.setTextFormat(Qt.TextFormat.RichText)
    l2.setText(c)
    l2.resize(900, 100)
    t0 = time.perf_counter()
    l2.heightForWidth(900)
    total += time.perf_counter() - t0
print(f"切块对照：{len(_chunks(html, 40000))} 块逐块 heightForWidth 合计 {total * 1000:.1f} ms",
      flush=True)
os._exit(0)
