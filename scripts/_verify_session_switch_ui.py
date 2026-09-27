"""回归：切走再切回运行中的会话 → 打字指示器恢复、发送按钮恢复为停止态（不再灰色）。
修复前：_switch_to 会 _hide_spinner 且按钮被复位为空闲灰色发送，切回后运行中的
任务无任何“进行中”UI。
"""
import os, sys, threading, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath("src"))
from PyQt6.QtWidgets import QApplication, QPushButton, QVBoxLayout
app = QApplication([])
from winapp_migrator.ui import agent_panel as ap

class _Anim:
    def __init__(self):
        self.active = False
    def start(self):
        self.active = True
    def stop(self):
        self.active = False
    def isActive(self):
        return self.active

def make_panel(running: bool, think_done: bool, segments: list):
    p = ap.AgentPanel.__new__(ap.AgentPanel)
    p._session_id = "A"
    st = {"engine": None, "user_stopped": False, "think_done": think_done,
          "think_start": time.time() if think_done else 0.0}
    if running:
        e = type("E", (), {})()
        e._thread = threading.Thread(target=lambda: time.sleep(60), daemon=True)
        e._thread.daemon = True
        e._thread.start()
        st["engine"] = e
    p._sess = {"A": st}
    p._segments = list(segments)
    p._eval_pending = None
    p._sub_stop = None
    p._spinner = None
    p._spinner_lbl = None
    p._spinner_row = None
    p._btn_icon_sz = 16
    p.msg_lay = QVBoxLayout()
    p.msg_lay.addStretch(1)
    p._scroll_bottom = lambda: None
    p.action_btn = QPushButton()
    p._action_anim = _Anim()
    p.input = type("_Inp", (), {"toPlainText": lambda self: ""})()
    p._set_action_idle()   # 模拟“切走时按钮被复位为灰色发送”
    return p

# 1) 运行中 + 思考未完成：恢复转圈指示器 + 忙碌按钮（非灰色）
p = make_panel(running=True, think_done=False, segments=[])
p._sync_task_ui()
assert p._spinner_row is not None, "打字指示器未恢复"
assert p._spinner_lbl is not None and p._spinner_lbl.text() == "AI 思考中…", \
    f"转圈文案异常: {p._spinner_lbl.text() if p._spinner_lbl else None}"
assert p.action_btn.toolTip() == "停止当前任务" and p._action_anim.isActive(), \
    "运行中按钮未恢复为忙碌（可停止）状态"
print("1) 思考中恢复：指示器+忙碌按钮 PASS")

# 2) 运行中 + 已开始输出：恢复停止图标（暂停可点），指示器显示已思考时长
p = make_panel(running=True, think_done=True, segments=[{"type": "text", "raw": "x"}])
p._sync_task_ui()
assert p._spinner_row is not None, "已输出任务缺少指示器"
assert "已思考" in p._spinner_lbl.text(), f"已思考时长未恢复: {p._spinner_lbl.text()}"
assert p.action_btn.toolTip() == "停止当前任务" and not p._action_anim.isActive(), \
    "已输出任务按钮应为停止图标（非转圈、非灰色）"
print("2) 已输出恢复：停止图标+已思考时长 PASS")

# 3) 运行中 + 用户已按停止：红底禁用停止态恢复
p = make_panel(running=True, think_done=False, segments=[])
p._sess["A"]["user_stopped"] = True
p._sync_task_ui()
assert p.action_btn.toolTip() == "停止中…" and not p.action_btn.isEnabled(), \
    "停止中状态未恢复"
print("3) 停止中恢复：红底禁用 PASS")

# 4) 空闲会话（任务已结束）：不加任何运行态（指示器仍无、按钮保持原样）
p = make_panel(running=False, think_done=False, segments=[])
p._sync_task_ui()   # 运行判定 False → 早退，不添加指示器
assert p._spinner_row is None, "空闲会话被错误添加指示器"
print("4) 空闲会话不误加 PASS")

# 5) 切换路径确实接入 _sync_task_ui（源码断言）
src = open(os.path.join(os.path.dirname(ap.__file__), "agent_panel.py"),
           encoding="utf-8").read()
assert src.count("self._sync_task_ui()") >= 3, "切换/收尾路径未全部接入 _sync_task_ui"
print("5) 切换/收尾路径接入 PASS")
print("ALL RESULT: PASS")