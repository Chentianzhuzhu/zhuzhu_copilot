"""工作团成员后台运行器：异步派发 + 消息驱动续跑 + 轨迹监督。

覆盖：
1. team_start 后台执行成员，完成后状态/结果可查
2. 收件箱有余量（chat_with 催促）→ 成员自动续跑处理消息
3. run_agent_llm（跨工作流主 Agent）工具轨迹写入 ContextLedger（look_context 数据源）
4. 引擎 _dispatch_tasks 对 agent= 任务默认异步投递（不阻塞主 Agent）
"""
import os
import sys
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from zhuzhu_Copilot.core import (
    agent_bus,
    agent_context,
    agent_engine,
    agent_llm,
    agent_subagent,
    agent_team_run,
)


def _clean():
    agent_bus.reset_bus()
    agent_bus.reset_ledgers()
    agent_context.reset_all()
    agent_context.set_current("")
    agent_context.set_source("")
    agent_team_run.team_reset()


def _fake_wf(tmp_path, monkeypatch, name="frontend_design"):
    """构造一个真实可用工作流目录并接入 workflow_dir 路由。"""
    from zhuzhu_Copilot.core import agent_workflow
    wf_dir = tmp_path / name
    wf_dir.mkdir(exist_ok=True)
    (wf_dir / "workflow.json").write_text(
        f'{{"name": "{name}", "enabled": true, "description": "t"}}',
        encoding="utf-8")
    monkeypatch.setattr(agent_workflow, "workflow_dir", lambda n: tmp_path / n)
    monkeypatch.setattr(agent_workflow, "load_llm_client",
                        lambda cfg, workflow=None: (None, ""))
    return agent_workflow


def _wait_done(aid, timeout=8.0):
    """等待成员进入终态（已完成/异常），避免线程启动竞态。"""
    end = time.time() + timeout
    while time.time() < end:
        st = agent_team_run.team_status(aid).get(aid)
        if st is not None and (st["done"] or st["error"]):
            return True
        time.sleep(0.02)
    return False


class _FakeLLM:
    """chat_stream 首轮返回 tool_calls（触发工具执行），随后结束。"""

    def __init__(self):
        self.calls = 0

    def chat_stream(self, messages, **kw):
        self.calls += 1
        if self.calls == 1:
            return {"text": "", "tool_calls": [{"id": "c1", "type": "function",
                                                "function": {"name": "get_time",
                                                             "arguments": "{}"}}],
                    "usage": {}}
        return {"text": "完成", "tool_calls": [], "usage": {}}


def test_team_start_runs_member(tmp_path, monkeypatch):
    """team_start 后台执行成员：完成后状态=已完成、结果可查。"""
    _fake_wf(tmp_path, monkeypatch)
    seen = {}

    def _fake_run(llm, wf, goal, **kw):
        seen["wf"] = wf
        seen["goal"] = goal
        return "成员结论"

    monkeypatch.setattr(agent_team_run, "_load_client", lambda wf: _FakeLLM())
    monkeypatch.setattr(agent_subagent, "run_agent_llm", _fake_run)
    _clean()
    ok, msg = agent_team_run.team_start("wf:frontend_design", "frontend_design",
                                        "设计登录页", context="需求文档")
    assert ok and "已派发" in msg
    assert _wait_done("wf:frontend_design")
    st = agent_team_run.team_status("wf:frontend_design")
    assert st["wf:frontend_design"]["done"] is True
    assert agent_team_run.team_result("wf:frontend_design") == "成员结论"
    assert seen == {"wf": "frontend_design", "goal": "设计登录页"}


def test_team_start_no_dup_when_running(tmp_path, monkeypatch):
    """同一成员已在后台运行时，重复派发不重复启动。"""
    _fake_wf(tmp_path, monkeypatch)

    def _slow_run(llm, wf, goal, **kw):
        time.sleep(0.5)
        return "ok"

    monkeypatch.setattr(agent_team_run, "_load_client", lambda wf: _FakeLLM())
    monkeypatch.setattr(agent_subagent, "run_agent_llm", _slow_run)
    _clean()
    ok1, _ = agent_team_run.team_start("wf:frontend_design", "frontend_design", "t1")
    assert ok1
    ok2, msg2 = agent_team_run.team_start("wf:frontend_design", "frontend_design", "t2")
    assert not ok2 and "运行中" in msg2
    assert _wait_done("wf:frontend_design")


def test_team_start_unlimited_concurrent_members(tmp_path, monkeypatch):
    """跨工作流主 Agent 派发数量不限：一次派发 8 个成员全部同时后台运行，
    不再受此前成员线程池 max_workers=4 的并发上限约束。"""
    from zhuzhu_Copilot.core import agent_workflow
    wfs = [f"wf{i}" for i in range(8)]
    for name in wfs:
        d = tmp_path / name
        d.mkdir(exist_ok=True)
        (d / "workflow.json").write_text(
            f'{{"name": "{name}", "enabled": true, "description": "t"}}',
            encoding="utf-8")
    monkeypatch.setattr(agent_workflow, "workflow_dir", lambda n: tmp_path / n)
    monkeypatch.setattr(agent_workflow, "load_llm_client",
                        lambda cfg, workflow=None: (None, ""))
    monkeypatch.setattr(agent_team_run, "_load_client", lambda wf: _FakeLLM())

    entered = []
    all_in = threading.Event()
    gate = threading.Event()

    def _blocking_run(llm, wf, goal, **kw):
        entered.append(wf)
        if len(entered) >= len(wfs):
            all_in.set()
        gate.wait(20)                  # 卡住全部成员，验证它们确实同时在跑
        return f"done:{wf}"

    monkeypatch.setattr(agent_subagent, "run_agent_llm", _blocking_run)
    _clean()
    try:
        for name in wfs:
            ok, msg = agent_team_run.team_start("wf:" + name, name, "任务")
            assert ok, msg
        assert all_in.wait(10), f"应 {len(wfs)} 个成员同时运行，实际进入 {len(entered)} 个"
        assert len(entered) == len(wfs)
    finally:
        gate.set()
    for name in wfs:
        assert _wait_done("wf:" + name), f"成员 {name} 未进入终态"


def test_message_driven_rerun(tmp_path, monkeypatch):
    """收件箱有余量（chat_with 催促）→ 成员自动续跑处理消息。"""
    _fake_wf(tmp_path, monkeypatch)
    goals = []

    def _fake_run(llm, wf, goal, **kw):
        goals.append(goal)
        return f"已处理: {goal[:10]}"

    monkeypatch.setattr(agent_team_run, "_load_client", lambda wf: _FakeLLM())
    monkeypatch.setattr(agent_subagent, "run_agent_llm", _fake_run)
    _clean()
    assert agent_bus.send_message("product_manager", "wf:frontend_design",
                                  "请加快进度", "chat")
    ok, _ = agent_team_run.team_start("wf:frontend_design", "frontend_design", "设计首页")
    assert ok
    assert _wait_done("wf:frontend_design")
    assert len(goals) >= 2, f"收到催促应续跑，实际目标数: {goals}"
    assert "收到来自其他 Agent 的消息" in goals[1]
    assert "请加快进度" in goals[1]


def test_run_agent_llm_writes_ledger(tmp_path, monkeypatch):
    """跨工作流主 Agent 工具轨迹写入 ContextLedger（look_context 数据源）。"""
    from zhuzhu_Copilot.core import agent_workflow
    _fake_wf(tmp_path, monkeypatch)
    monkeypatch.setattr(agent_workflow, "load_llm_client",
                        lambda cfg, workflow=None: (None, ""))
    _clean()
    out = agent_subagent.run_agent_llm(
        _FakeLLM(), "frontend_design", "设计登录页", agent_id="wf:frontend_design")
    assert out == "完成"
    led = agent_bus.render_ledger("wf:frontend_design", limit=20)
    assert "get_time" in led, f"成员工具轨迹未入库: {led}"


def test_dispatch_agent_async_routes_to_team(tmp_path, monkeypatch):
    """引擎派发 agent= 任务默认异步投递到成员后台运行器（不阻塞主 Agent）。"""
    _fake_wf(tmp_path, monkeypatch)
    calls = {}

    def _fake_team_start(agent_id, wf, goal, **kw):
        calls["agent_id"] = agent_id
        calls["wf"] = wf
        calls["goal"] = goal
        return True, "已派发"

    monkeypatch.setattr(agent_team_run, "team_start", _fake_team_start)
    eng = agent_engine.AgentEngine(llm=agent_llm.LLMClient())
    eng._task_groups = []   # 不裁剪，保持全量工具
    text = eng._dispatch_tasks([{
        "title": "派发前端", "goal": "设计登录页", "agent": "frontend_design",
        "context": "需求", "shared_context": True, "space": "s1", "async": True}])
    assert "已派发" in text
    assert calls == {"agent_id": "wf:frontend_design", "wf": "frontend_design",
                     "goal": "设计登录页"}
