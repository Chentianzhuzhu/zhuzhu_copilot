# -*- coding: utf-8 -*-
"""复现长任务思考时气泡底部大片空白：检查 setMinimumHeight 钉住后内容收缩是否解锁。"""
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


def build_long_segs(with_results=True):
    segs = []
    for i in range(6):
        segs.append({"type": "think", "html": f"思考步骤 {i}：分析上下文与候选方案，权衡后再决策。" * 3,
                     "collapsed": True})
        segs.append({"type": "op", "html": f"▸ 执行操作 {i}：cal.py"})
    if with_results:
        for i in range(3):
            segs.append({"type": "result",
                         "html": f"命令输出 {i}：" + ("step-output-line\n" * 40),
                         "collapsed": False})
    segs.append({"type": "text", "raw": "长任务完成，最终结论。" * 3})
    return segs


b = panel._add_bubble("", "ai", animate=False)
panel._ai_bubble = b
segs = build_long_segs(with_results=True)
h1 = panel._build_ai_html(segs)
b.setText(h1)
panel._sync_bubble_heights([b])
for _ in range(5):
    app.processEvents()

h_exp = b.height()
hfw_exp = b.heightForWidth(b.width())
print("展开态: height=%d hfw=%d html_len=%d" % (h_exp, hfw_exp, len(h1)), flush=True)

# 模拟结果段自动折叠（内容收缩）
for s in segs:
    if s["type"] == "result":
        s["collapsed"] = True
h2 = panel._build_ai_html(segs)
b.setText(h2)
panel._sync_bubble_heights([b])   # 重新按收缩后内容钉最小高度
for _ in range(5):
    app.processEvents()

h_col = b.height()
hfw_col = b.heightForWidth(b.width())
print("折叠后: height=%d hfw=%d minH=%d html_len=%d 折叠标记=%d" % (
    h_col, hfw_col, b.minimumHeight(), len(h2), h2.count("已折叠 · 点击展开")), flush=True)
blank = h_col - hfw_col
print("空白高度: %d px -> %s" % (blank, "存在空白" if blank > 60 else "无空白"), flush=True)

# 探查收缩几何：显式激活消息流布局后再测气泡高度
panel._relayout_messages()
for _ in range(5):
    app.processEvents()
h3 = b.height()
print("显式relayout后: 气泡height=%d hfw=%d minH=%d" % (h3, b.heightForWidth(b.width()), b.minimumHeight()), flush=True)
try:
    wrap = getattr(b, "_wrap_lay", None).parentWidget()
    print("wrap h=%d w=%d | label h=%d" % (wrap.height(), wrap.width(), b.height()), flush=True)
except Exception as e:
    print("wrap probe exc:", e, flush=True)
try:
    row = wrap.parentWidget() if wrap is not None else None
    print("row(container child) probe:", type(row).__name__ if row is not None else "None", flush=True)
except Exception as e:
    print("row probe exc:", e, flush=True)
cont = panel.msg_area.widget()
print("container h=%d  msg_lay sizeHint h=%d  cont.minH=%d" % (
    cont.height(), panel.msg_lay.sizeHint().height(), cont.minimumHeight()), flush=True)
if h3 > b.heightForWidth(b.width()) + 50:
    print("仍有空白", flush=True)
    b.updateGeometry()
    b._wrap_lay.invalidate()
    b._wrap_lay.parentWidget().updateGeometry()
    panel.msg_lay.invalidate()
    panel.msg_lay.activate()
    panel.msg_area.widget().updateGeometry()
    app.processEvents()
    h4 = b.height()
    print("invalidate后再测: label h=%d hfw=%d" % (h4, b.heightForWidth(b.width())), flush=True)
else:
    print("已收缩无空白", flush=True)

# 对照：明确按固定宽度测 heightForWidth（不受布局宽度影响）
from PyQt6.QtWidgets import QLabel
ref = QLabel()
ref.setWordWrap(True)
ref.setMaximumWidth(b.maximumWidth())
ref.setText(h1)
app.processEvents()
ref.setText(h2)
app.processEvents()
W = b.maximumWidth() or 640
b1 = panel._build_ai_html(build_long_segs(True))
b.setText(b1)   # 重新展开
app.processEvents()
hfw_b_long = b.heightForWidth(W)
h2b = panel._build_ai_html(build_long_segs(False))
b.setText(h2b)
app.processEvents()
hfw_b_short = b.heightForWidth(W)
b.setMinimumHeight(0)   # 置零后再测，验证 minHeight 是否钉住 hfw
app.processEvents()
hfw_b_short0 = b.heightForWidth(W)
print("min置零后台含量: 置零前=%d 置零后=%d" % (hfw_b_short, hfw_b_short0), flush=True)
ref.setText(b1); app.processEvents()
hfw_r_long = ref.heightForWidth(W)
ref.setText(h2b); app.processEvents()
hfw_r_short = ref.heightForWidth(W)
print("面板b: 长hfw=%d 短hfw=%d | 纯label: 长hfw=%d 短hfw=%d" % (
    hfw_b_long, hfw_b_short, hfw_r_long, hfw_r_short), flush=True)
os._exit(0)