"""确认/提问弹窗「无限等待」+ 任务内确认去重冒烟验证（offscreen）。

验证点：
1. _ConfirmDialog / _AskUserDialog 不再创建超时定时器（无限等待用户作答）
2. 同一「工具+参数」本次任务内只弹一次确认窗，缓存命中直接复用上次结果
3. 任务结束（_show_end_badge）清理确认缓存，下个任务重新弹窗
"""

from zhuzhu_Copilot import app_identity
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                "..", "src")))

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication

app = QApplication([])

from zhuzhu_Copilot.ui import agent_panel as ap


def main() -> int:
    q = app_identity.qsettings()
    for k in ("dock_state/todosWin", "dock_state/gitLogWin",
              "dock_state/wtWin", "dock_state/codeWin"):
        q.remove(k)
    p = ap.AgentPanel()
    p.show()

    # 1) 弹窗不再创建超时定时器（无限等待）
    d = ap._ConfirmDialog("run_command", {"command": "x"}, "risky", p)
    assert not hasattr(d, "_timeout_timer"), "确认弹窗不应有超时定时器"
    a = ap._AskUserDialog("测试问题", None, p)
    assert not hasattr(a, "_timeout_timer"), "提问弹窗不应有超时定时器"
    print("1) 确认/提问弹窗无超时定时器 OK")

    # 2) 任务内确认去重：同一命令只弹一次
    p._session_id = "test_sid"
    p._sess["test_sid"] = p._new_sess_state("test_sid")
    p._mode = "ask"
    calls = []

    def fake_confirm(sid, name, args_json, level):
        calls.append((name, args_json))
        p._confirm_result = True
        p._confirm_evt.set()

    p.confirm_signal.connect(fake_confirm)
    ok1 = p._confirm_tool("test_sid", "run_command", {"command": "git push origin main"})
    ok2 = p._confirm_tool("test_sid", "run_command", {"command": "git push origin main"})
    ok3 = p._confirm_tool("test_sid", "run_command", {"command": "pip install numpy"})
    assert ok1 is True and ok2 is True and ok3 is True
    assert len(calls) == 2, f"应弹两次（不同命令），实际 {len(calls)}"
    st = p._sess["test_sid"]
    assert len(st.get("confirm_cache", {})) == 2
    print("2) 任务内确认去重 OK: 弹窗次数", len(calls))

    # 3) 任务结束清理缓存
    p._show_end_badge()
    assert "confirm_cache" not in p._sess["test_sid"]
    print("3) 任务结束清理确认缓存 OK")

    print("ALL CONFIRM CHECKS PASSED")
    return 0


if __name__ == "__main__":
    rc = 0
    try:
        rc = main()
    except Exception:  # noqa: BLE001 -- 冒烟脚本：异常必须打印并置失败
        import traceback
        traceback.print_exc()
        rc = 1
    finally:
        os._exit(rc)