"""@工作流 持久切换回归（wf_return 自动切回已下线）。

覆盖本次修复：
1. 会话状态不再含 wf_return 键（下线自动切回的数据载体）
2. _send 不再记录切回目标；任务收尾不再消费切回（无"自动切回"逻辑）
3. @工作流+正文 直接走 _switch_session_workflow 持久切换
"""
import inspect
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from zhuzhu_Copilot.ui import agent_panel as ap

# 用 __new__ 构造（不跑完整 __init__，避免启动后台 git 加载线程在测试进程内触发
# "wrapped C/C++ object has been deleted" 崩溃），仅装配被测方法依赖的最小属性。
def _mk_panel():
    p = ap.AgentPanel.__new__(ap.AgentPanel)
    p._sess = {"t": p._new_sess_state("t")}
    p._session_id = "t"
    p._add_status = lambda *a, **k: None
    p._persist_current = lambda: None
    p._invalidate_cmd_cache = lambda: None
    p._update_wf_label = lambda: None
    return p, p._sess["t"]


def test_no_wf_return_in_session_state():
    """会话状态无 wf_return 键：自动切回机制已整体下线。"""
    _, st = _mk_panel()
    assert "wf_return" not in st, "会话状态不应再有 wf_return（自动切回已下线）"


def test_send_no_longer_records_switch_back():
    """_send 的 @工作流 分支只做持久切换：不设切回目标。"""
    src = inspect.getsource(ap.AgentPanel._send)
    assert '"wf_return"' not in src, "_send 不应再写入 wf_return"
    # 带正文的 @工作流 仍走 _switch_session_workflow（会话级持久切换）
    assert "self._switch_session_workflow(rname)" in src


def test_task_end_no_auto_switch_back():
    """任务收尾块不再含自动切回逻辑（wf_return 消费 / __global__ 哨兵均移除）。"""
    src = inspect.getsource(ap.AgentPanel)
    assert "自动切回原工作流" not in src, "任务收尾不应再自动切回原工作流"
    assert '"wf_return"' not in src, "面板源码不应再出现 wf_return 读写"


def test_workflow_switch_persists_after_task(monkeypatch):
    """@工作流+正文：先切换（持久），不记录任何切回目标（后续消息仍在同一工作流）。"""
    p, st = _mk_panel()
    calls = []
    orig = p._switch_session_workflow

    def _spy(name):
        calls.append(name)
        return orig(name)

    monkeypatch.setattr(p, "_switch_session_workflow", _spy)
    monkeypatch.setattr(p, "_launch_subagent", lambda *a, **k: None)
    # 引擎桩：避免用例依赖真实用户工作流目录/LLM 客户端（只验证状态语义）
    class _EngStub:
        workflow = "_default"
        persona = None
        _thread = None
        _messages = []
    monkeypatch.setattr(p, "_engine_for", lambda sid, _e=_EngStub(): _e)
    # 仅验证路由与切换语义（不真正发送：_do_send 需要完整引擎/UI 环境）
    _, route = p._resolve_at_route("@sansheng_liubu 帮我整理报告")
    assert route == ("workflow", "sansheng_liubu")
    assert calls == [], "@工作流+正文 不应产生额外切回目标记录（wf_return 已下线）"
    # 持久切换本身仍由 _switch_session_workflow 完成：
    # 手工执行一次切换并断言会话工作流保持（模拟任务结束后的状态）
    p._switch_session_workflow("product_manager")
    assert st.get("workflow") == "product_manager", "切换后会话工作流应保持（永久切换）"