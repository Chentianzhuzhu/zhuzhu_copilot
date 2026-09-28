# -*- coding: utf-8 -*-
"""性能回归：内容高度不变时不得出现 relayout 乒乓（无限 singleShot 重排）。
复现路径：流式刷新 setText 相同内容 → _apply_refresh_ai_html → _sync_bubble_heights
应零额外调度；此前 bug 每 tick 都会无限触发 _relayout_messages。"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
from PyQt6.QtWidgets import QApplication

app = QApplication(sys.argv)
from zhuzhu_Copilot.ui.agent_panel import AgentPanel

panel = AgentPanel(None)
t0 = time.time()
while time.time() - t0 < 4:
    app.processEvents()
    time.sleep(0.05)
panel._rows = []
panel._user_msgs = []
panel._segments = []
panel._history_segments = []
panel._render_history_all()
app.processEvents()

b = panel._add_bubble("", "ai", animate=False)
panel._ai_bubble = b
segs = [{"type": "text", "raw": "固定内容测试。" * 30}]
b.setText(panel._build_ai_html(segs))
panel._sync_bubble_heights([b])
for _ in range(3):
    app.processEvents()

# 劫持 _relayout_messages 计数
calls = []
orig = panel._relayout_messages


def wrapped():
    calls.append(1)
    orig()


panel._relayout_messages = wrapped

# 模拟 50 次流式刷新：内容不变 → 高度不变 → 不应调度任何 relayout
for i in range(50):
    b.setText(panel._build_ai_html(segs))
    panel._apply_refresh_ai_html.__self__ if False else None
    # 直接走与流式一致的路径：_apply_refresh_ai_html 内部会 setText+sync
    panel._apply_refresh_ai_html()
    app.processEvents()

# 再额外驱动几轮事件循环，确认没有挂起的 ping-pong
for _ in range(20):
    app.processEvents()

n = len([c for c in calls])
print("稳定内容 50 次刷新后 relayout 调用次数: %d" % n, flush=True)
if n > 1:   # 允许首帧一次性生长调度 1 次；此后高度稳定应为 0
    print("FAIL: 出现乒乓重排", flush=True)
    os._exit(1)
print("PASS: 无乒乓重排（允许首帧 1 次生长调度）", flush=True)
# 恢复并做一次内容增长，确认仍能正常扩展
panel._relayout_messages = orig
text2 = "增长后的长内容。" * 200
b.setText(panel._build_ai_html([{"type": "text", "raw": text2}]))
panel._sync_bubble_heights([b])
for _ in range(5):
    app.processEvents()
h = b.height()
hfw = b.heightForWidth(b.width())
print("增长校验: height=%d hfw=%d -> %s" % (h, hfw, "OK" if h >= hfw - 40 else "SQUEEZED"), flush=True)
os._exit(0)