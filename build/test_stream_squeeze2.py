# -*- coding: utf-8 -*-
"""真实 AgentPanel 流式复现：_on_delta 分块推送，结束后检查气泡高度与内容是否一致。"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
from PyQt6.QtWidgets import QApplication

app = QApplication(sys.argv)
from winapp_migrator.ui.agent_panel import AgentPanel

panel = AgentPanel(None)
# 先等后台会话加载稳定（避免异步 _render_history_all 竞赛破坏待测气泡）
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

LONG = ("这是一段用于复现流式生成时气泡挤压问题的较长中文回复正文，包含思考过程和操作展示：" * 14) + \
       ("code-block-very-long-unbroken-token-" * 50) + \
       ("流式输出会在同一气泡内反复 setText，检查高度是否按宽度正确换行折算。" * 12)

# 模拟流式：分块推送 _on_delta，并驱动节流定时器
chunk = len(LONG) // 10
for i in range(10):
    panel._on_delta(LONG[i * chunk: (i + 1) * chunk])
    app.processEvents()
    time.sleep(0.08)   # 让 _refresh_ai_html 的 60ms+ 节流定时器触发
app.processEvents()
time.sleep(0.3)        # 确保最后一轮 setText 完成

b = panel._ai_bubble
if b is None:
    print("NO BUBBLE", flush=True)
    os._exit(1)
w_lbl = b.width()
print("bubble rect:", b.geometry().getRect(), flush=True)
h = b.height()
hfw = b.heightForWidth(w_lbl)
print("label width=%d heightForWidth=%d actual_height=%d" % (w_lbl, hfw, h), flush=True)
if hfw > 0 and h < hfw:
    print("SQUEEZED: actual < heightForWidth", flush=True)
else:
    print("NOT squeezed", flush=True)

# 容器高度核对：所有行高度和 vs 容器内容高度
cont = panel.msg_area.widget()
print("container h=%d, msg_lay sizeHint h=%d" % (cont.height(), panel.msg_lay.sizeHint().height()), flush=True)

# 修复验证：强制布局激活后是否自愈
panel._relayout_messages()
for _ in range(5):
    app.processEvents()
h2 = b.height()
hfw2 = b.heightForWidth(b.width())
print("after relayout: height=%d heightForWidth=%d" % (h2, hfw2), flush=True)
print("healed" if h2 >= hfw2 else "STILL SQUEEZED", flush=True)

# 逐层探测 heightForWidth 链
cont = panel.msg_area.widget()
print("cont.heightForWidth:", cont.heightForWidth(cont.width()), "| cont.sizeHint.h:", cont.sizeHint().height(), flush=True)
print("msg_lay.heightForWidth:", panel.msg_lay.heightForWidth(cont.width()), "| sizeHint.h:", panel.msg_lay.sizeHint().height(), flush=True)
v = getattr(b, "_wrap_lay", None)
if v is not None:
    top = v.itemAt(0).layout()
    wrap = v.parentWidget()
    print("v.heightForWidth:", v.heightForWidth(cont.width()), flush=True)
    print("top.heightForWidth:", top.heightForWidth(cont.width()), flush=True)
    print("wrap.heightForWidth:", wrap.heightForWidth(cont.width()), "| wrap.hasHeightFor:", wrap.sizePolicy().hasHeightForWidth(), flush=True)
    # 对照组：直接用旧结构（label 直入 msg_lay 的 HBox 行）
    from PyQt6.QtWidgets import QLabel, QHBoxLayout
    lbl2 = QLabel(f"<span>{'对照组超长文本，' * 80}</span>")
    lbl2.setWordWrap(True)
    lbl2.setMaximumWidth(b.width())
    row2 = QHBoxLayout()
    row2.addWidget(lbl2, 0)
    row2.addStretch(1)
    panel.msg_lay.insertLayout(panel.msg_lay.count() - 1, row2)
    app.processEvents()
    print("direct-label row hfw:", row2.heightForWidth(cont.width()), "| msg_lay hfw now:", panel.msg_lay.heightForWidth(cont.width()), flush=True)
os._exit(0)