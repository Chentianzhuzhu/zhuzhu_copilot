"""诊断：AI 自动命名会话为何不生效（写入 sessions.json / 刷新下拉 / 是否被覆盖）。"""
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from PyQt6.QtWidgets import QApplication

app = QApplication([])

from winapp_migrator.ui import agent_panel as ap

tmp = Path(tempfile.mkdtemp(prefix="sessname_"))
p = ap.AgentPanel()
p._sessions_dir = lambda: tmp          # 隔离：不碰用户真实会话数据
for _ in range(8):
    app.processEvents()

sid = p._create_session()["id"] if hasattr(p, "_create_session") else None
print("新会话 id:", sid)

# 模拟「新对话」首次发送
p._session_id = sid
p._session_name = "新对话"
p._bind_sess(sid) if hasattr(p, "_bind_sess") else None
p._refresh_session_combo()
print("命名前 combo:", [p.session_combo.itemText(i) for i in range(p.session_combo.count())])
print("命名前 sessions.json:", json.dumps(
    [s.get("name") for s in p._load_session_list()], ensure_ascii=False))

TEXT = "帮我写一个快速排序的 Python 实现"
p._auto_name_session(TEXT)
print("命名后 _session_name:", repr(p._session_name))
print("命名后 sessions.json:", json.dumps(
    [s.get("name") for s in p._load_session_list()], ensure_ascii=False))
print("命名后 combo:", [p.session_combo.itemText(i) for i in range(p.session_combo.count())])

# 再走一次持久化（真实发送路径会在命名后落盘），确认名称不被覆盖
p._persist_current()
print("_persist_current 后 sessions.json:", json.dumps(
    [s.get("name") for s in p._load_session_list()], ensure_ascii=False))

# 模拟长文本/换行首条消息（用户常见：先贴一段需求）
p2_name = "新对话"
p._session_name = "新对话"
p._auto_name_session("\n\n   ")
print("空白文本命名后:", repr(p._session_name), "（应保持 新对话 或明确处理）")
