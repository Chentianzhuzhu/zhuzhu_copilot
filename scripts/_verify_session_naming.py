"""回归验证：会话命名端到端（本地兜底 → 后台 AI 起名经信号回主线程覆盖）。

真实面板 + 隔离会话目录，走完整链路：_auto_name_session → 后台线程 → sess_name_signal
→ _on_ai_session_title → sessions.json / 下拉。AI 调用用替身（避免联网），其余全真。
"""
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from PyQt6.QtWidgets import QApplication

_app = QApplication([])

from zhuzhu_Copilot.core import agent_llm
from zhuzhu_Copilot.ui import agent_panel as ap

TMP = Path(tempfile.mkdtemp(prefix="sessname_"))
agent_llm.generate_session_title = lambda *a, **k: "快速排序实现"   # 替身：不联网

p = ap.AgentPanel()
p._sessions_dir = lambda: TMP          # 隔离：不动用户真实会话数据
for _ in range(8):
    _app.processEvents()

sid = p._create_session()["id"]
p._session_id, p._session_name = sid, "新对话"
p._bind_sess(sid)
p._refresh_session_combo()


def _name():
    for s in p._load_session_list():
        if s.get("id") == sid:
            return s.get("name"), s.get("name_source")
    return None, None


print("命名前:", _name())
p._auto_name_session("帮我写一个快速排序的 Python 实现")
print("本地命名后:", _name(), "| combo:", p.session_combo.currentText())

deadline = time.time() + 5
while time.time() < deadline:
    _app.processEvents()
    if _name()[1] == "ai":
        break
    time.sleep(0.02)
print("AI 起名后:", _name(), "| combo:", p.session_combo.currentText())

# 人工/Agent 改名后不得被后续消息覆盖
p._apply_session_name(sid, "我的自定义名字", source="manual")
p._auto_name_session("换一个完全不同的话题")
print("人工改名后再发消息:", _name())

name, src = _name()
fail = 0
if src != "manual" or name != "我的自定义名字":
    fail += 1
    print("ERR 人工名字被覆盖")
if p.session_combo.currentText() != "我的自定义名字":
    fail += 1
    print("ERR 下拉未同步")
print("失败项:", fail)
sys.exit(1 if fail else 0)
