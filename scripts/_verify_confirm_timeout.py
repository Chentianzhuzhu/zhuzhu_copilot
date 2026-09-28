"""验证：确认/提问等待已取消 600s 超时（无限等待），
能被用户应答/切换会话的 set() 唤醒且不崩溃。"""
import os, sys, threading, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath("src"))
from PyQt6.QtWidgets import QApplication
app = QApplication([])
from zhuzhu_Copilot.ui import agent_panel as ap

p = ap.AgentPanel.__new__(ap.AgentPanel)
p._sess = {"s1": {"title": "前台"}, "s2": {"title": "后台A"}, "s3": {"title": "后台B"}}
p._session_id = "s1"


class _Sig:
    def emit(self, *a):
        pass


p.evt_signal = _Sig()
p.confirm_signal = _Sig()
p.ask_signal = _Sig()
p._confirm_evt = threading.Event()
p._ask_evt = threading.Event()
p._confirm_result = False
p._ask_result = ""
p._mode = "standard"
p._task_auto_ids = set()
p._toast = lambda *a, **k: None

results = {}


def r(name, fn):
    def wrap():
        t0 = time.time()
        try:
            results[name] = ("ok", fn(), time.time() - t0)
        except Exception as e:
            results[name] = ("err", repr(e), time.time() - t0)
    t = threading.Thread(target=wrap, daemon=True)
    t.start()
    return t


t = r("f_confirm", lambda: p._confirm_tool("s1", "write_file", {"path": "x"}))
time.sleep(0.5)
p._confirm_result = True
p._confirm_evt.set()
t.join(3)

t = r("bg_confirm", lambda: p._confirm_tool("s2", "write_file", {"path": "x"}))
time.sleep(0.5)
p._sess["s2"]["confirm_result"] = True
p._sess["s2"]["confirm_evt"].set()
t.join(3)

t = r("f_ask", lambda: p._ask_user_tool("s1", {"question": "q"}))
time.sleep(0.5)
p._ask_result = "答案是 A"
p._ask_evt.set()
t.join(3)

t = r("bg_ask", lambda: p._ask_user_tool("s3", {"question": "q"}))
time.sleep(0.5)
p._sess["s3"]["ask_result"] = "后台答案"
p._sess["s3"]["ask_evt"].set()
t.join(3)

for k in ("f_confirm", "bg_confirm", "f_ask", "bg_ask"):
    print(k, results.get(k))

assert results["f_confirm"][0] == "ok" and results["f_confirm"][1] is True
assert results["bg_confirm"][0] == "ok" and results["bg_confirm"][1] is True
assert results["f_ask"][0] == "ok" and results["f_ask"][1] == "答案是 A"
assert results["bg_ask"][0] == "ok" and results["bg_ask"][1] == "后台答案"
assert all(v[2] < 3 for v in results.values())
print("SMOKE OK")
