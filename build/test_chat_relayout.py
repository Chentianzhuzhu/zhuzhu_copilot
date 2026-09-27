# -*- coding: utf-8 -*-
"""离屏冒烟测试：验证聊天区两个修复点
1. _add_badge 附着到最后一条 AI 气泡包裹层（不再作为独立消息流行）
2. _render_history_all / _relayout_messages 批量重建无异常、事件循环推进后布局存活
运行：QT_QPA_PLATFORM=offscreen python build/test_chat_relayout.py
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from PyQt6.QtWidgets import QApplication, QLabel  # noqa: E402

app = QApplication(sys.argv)

from winapp_migrator.ui.agent_panel import AgentPanel  # noqa: E402

panel = AgentPanel(None)
# 面板初始化会后台异步加载最近会话历史（_load→switch_ready→_finish_switch→_render_history_all），
# 与测试的插入操作存在竞态（重建会自由测试气泡的包裹层）。先等加载稳定再进入已知状态。
import time as _t
_t0 = _t.time()
while _t.time() - _t0 < 4:
    app.processEvents()
    _t.sleep(0.05)
panel._rows = []
panel._user_msgs = []
panel._segments = []
panel._history_segments = []
panel._render_history_all()   # 清空为已知状态，后续插入不受异步改写
app.processEvents()

# ---- 1. 结束徽章与重试按钮同行（插入重试行首，不进入消息流独立行）----
b = panel._add_bubble("hello ai", "ai", animate=False)
app.processEvents()
assert getattr(b, "_wrap_lay", None) is not None, "AI 气泡缺少 _wrap_lay 反向引用"
assert getattr(b, "_retry_row", None) is not None, "AI 气泡缺少 _retry_row 反向引用"
panel._ai_bubble = b
msg_before = panel.msg_lay.count()
panel._add_badge("Successfully", "#22c55e")
row = b._retry_row
assert row.indexOf(b._retry_btn) >= 0, "重试按钮不在重试行"
badge_lbl = row.itemAt(0).widget()
assert badge_lbl is not None and "Successfully" in (badge_lbl.text() or ""), "徽章未插入重试行首"
assert row.indexOf(badge_lbl) >= 0, "徽章未附着到重试行"
assert panel.msg_lay.count() == msg_before, "徽章不应再插入 msg_lay 独立行"
print("PASS: 徽章与重试按钮同行（retry_row: 徽章%d、重试%d）" % (row.itemAt(0).widget() is badge_lbl, row.indexOf(b._retry_btn) >= 0))

# 徽章胶囊宽度：不得与聊天气泡同长（水平行内按内容自适应）
for _ in range(3):
    app.processEvents()
bubble_w = b.sizeHint().width()
badge_w = badge_lbl.sizeHint().width()
print("D: bubble_w=%d badge_sizeHint_w=%d" % (bubble_w, badge_w), flush=True)
assert badge_w * 2 < bubble_w, "徽章被拉伸到与聊天气泡同长"

# 事件循环推进后包裹层与气泡仍存活（防 C++ 回收回归）
for _ in range(3):
    app.processEvents()
assert panel._bubble_alive(b) and getattr(b, "_wrap_lay", None) is not None, "事件循环后包裹层被回收"

# ---- 2. 批量重建 + 布局自愈无异常 ----
panel._rows = [
    {"type": "user", "text": "第二轮提问"},
    {"type": "ai", "segs": [{"type": "text", "raw": "第二段较长的回答，用于验证批量重建时换行高度按真实宽度折算。" * 3}]},
    {"type": "user", "text": "第三轮提问"},
]
panel._user_msgs = ["第二轮提问", "第三轮提问"]
panel._render_history_all()
for _ in range(3):
    app.processEvents()
panel._relayout_messages()
print("PASS: _render_history_all + _relayout_messages 无异常")

# ---- 3. 面板状态未污染 ----
panel._refresh_session_combo()
panel._update_welcome()
print("PASS: 面板状态未污染")
os._exit(0)