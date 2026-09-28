"""回归：重启 app 优先恢复到「最后一次使用」的对话页面，而非最新开启的对话。
根因：关闭时全部内存会话 updated 被统一刷新，updated 无法区分“最后停靠”的会话，
旧逻辑只能按 sessions.json 顺序落在最新创建的对话上。
修复：显式持久化最后停靠会话（last_session.txt），重启优先恢复（无内容门控）。
"""
import os, sys, json, tempfile, pathlib
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath("src"))
from PyQt6.QtWidgets import QApplication
app = QApplication([])
from zhuzhu_Copilot.ui import agent_panel as ap

d = pathlib.Path(tempfile.mkdtemp())
p = ap.AgentPanel.__new__(ap.AgentPanel)
p._sessions_dir = lambda: d

def write_sessions():
    # 模拟关闭时 updated 被统一刷成同一时刻：此时新旧会话 updated 相同
    lst = [{"id": "OLD", "name": "旧对话", "created": 100.0, "updated": 999.0},
           {"id": "NEW", "name": "新对话", "created": 500.0, "updated": 999.0}]
    (d / "sessions.json").write_text(json.dumps(lst), encoding="utf-8")
    (d / "OLD.ui.json").write_text(json.dumps({"user_msgs": ["你好"]}), encoding="utf-8")
    (d / "NEW.ui.json").write_text(json.dumps({"user_msgs": ["新对话内容"]}), encoding="utf-8")
    return lst

# 1) 最后一次停靠在 OLD（有内容）→ 重启恢复 OLD，而不是最新创建的 NEW
lst = write_sessions()
(d / "last_session.txt").write_text("OLD", encoding="utf-8")
assert p._read_last_session(lst) == "OLD", "未恢复最后停靠的旧对话页面"
print("1) 最后停靠(有内容)优先恢复 PASS")

# 2) 最后停靠的是空白「新对话」（无内容）→ 内容门控放行，回落 _pick_last_session
lst = write_sessions()
(d / "last_session.txt").write_text("NEW2", encoding="utf-8")
(d / "NEW2.ui.json").write_text(json.dumps({}), encoding="utf-8")
(d / "sessions.json").write_text(
    json.dumps(lst + [{"id": "NEW2", "name": "新对话", "created": 700.0, "updated": 999.0}]),
    encoding="utf-8")
assert p._read_last_session(lst + [{"id": "NEW2", "name": "新对话",
                                    "created": 700.0, "updated": 999.0}]) == "", \
    "空白最后停靠不应被恢复"
print("2) 空白最后停靠门控 PASS")

# 3) last_session.txt 指向已删除/不存在的会话 → 回落最近有内容会话
lst = write_sessions()
(d / "last_session.txt").write_text("GONE", encoding="utf-8")
assert p._read_last_session(lst) == "", "失效 id 不应恢复"
assert p._pick_last_session(sorted(lst, key=lambda s: s["updated"], reverse=True)) == "OLD"
print("3) 失效 id 回落最近有内容会话 PASS")

# 4) 记录与恢复钩子都接入（源码断言）
src = open(os.path.join(os.path.dirname(ap.__file__), "agent_panel.py"),
           encoding="utf-8").read()
assert src.count("self._record_last_session()") >= 3, "记录钩子未全量接入"
assert "_read_last_session(lst)" in src and 'last_session.txt' in src, "恢复钩子缺失"
print("4) 记录/恢复钩子接入 PASS")
print("ALL RESULT: PASS")