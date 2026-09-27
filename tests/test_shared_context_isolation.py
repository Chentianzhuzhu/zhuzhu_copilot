"""共同上下文空间的「对话隔离」回归。

要求：**每个对话之间隔离共享上文空间，仅同一对话之间的 Agent 开启共享**
——同一对话内的主 Agent 与各子 Agent / 成员工作流共用一份空间；
不同对话即使使用同名空间（含默认空间）也互不可见、互不覆盖。

覆盖：
1. 默认空间按对话各自解析（同名不同实例）；活跃空间按对话记录
2. 空间列表/统计/关闭/回收的对话过滤
3. 同对话内存写共享；跨对话同名空间互不可见
4. 并发派发（线程池）的对话作用域下传与隔离
5. 引擎任务线程落地所属对话并在结束时复位；停止只停本对话成员
6. 成员后台运行器（team_start）线程携带对话
7. UI 接线（源码守卫）：引擎带 conversation、会话切换绑定对话、删对话回收空间
"""
import inspect
import json
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from winapp_migrator.core import (agent_context, agent_subagent, agent_team_run)


def _reset():
    agent_context.reset_all()


# ---------- 1. 默认空间 / 活跃空间按对话解析 ----------
def test_default_space_resolves_per_conversation():
    _reset()
    agent_context.set_conversation("convA")
    sid_a = agent_context.open_space(agent_context.DEFAULT_SPACE, seed="A 议题", owner="main")
    agent_context.append(sid_a, "main", "A 的产出")
    assert sid_a == agent_context.DEFAULT_SPACE, "返回逻辑 id（对话无关），便于会话落盘复用"
    assert agent_context.has_space(agent_context.DEFAULT_SPACE)

    # 切到另一对话：同名空间不存在（未开启），读到的是空
    agent_context.set_conversation("convB")
    assert not agent_context.has_space(agent_context.DEFAULT_SPACE)
    assert agent_context.render(agent_context.DEFAULT_SPACE) == ""
    assert agent_context.entries(agent_context.DEFAULT_SPACE) == []

    # B 自己开一份同名空间：互不串内容
    sid_b = agent_context.open_space(agent_context.DEFAULT_SPACE, seed="B 议题", owner="main")
    assert sid_b == sid_a == agent_context.DEFAULT_SPACE
    assert "A 的产出" not in agent_context.render(sid_b)
    assert "B 议题" in agent_context.render(sid_b)

    # 回到 A：内容完好，未被 B 覆盖
    agent_context.set_conversation("convA")
    body_a = agent_context.render(sid_a)
    assert "A 的产出" in body_a and "B 议题" not in body_a


def test_active_space_is_per_conversation():
    _reset()
    agent_context.set_conversation("convA")
    agent_context.open_space("t1", seed="A")
    assert agent_context.active_space() == "t1" and agent_context.current() == "t1"

    # 另一对话无活跃空间（不回退到 A）
    agent_context.set_conversation("convB")
    assert agent_context.active_space() == "" and agent_context.current() == ""

    agent_context.open_space("t2", seed="B")
    assert agent_context.active_space() == "t2"

    agent_context.set_conversation("convA")
    assert agent_context.active_space() == "t1", "各对话独立记录活跃空间"


def test_list_stats_close_are_conversation_scoped():
    _reset()
    agent_context.set_conversation("convA")
    agent_context.open_space("a1", seed="s", owner="pm")
    agent_context.append("a1", "pm", "x")
    agent_context.set_conversation("convB")
    agent_context.open_space("b1", seed="s")

    names = [s["space"] for s in agent_context.list_spaces()]
    assert names == ["b1"], "默认只列当前对话的空间"
    assert {s["space"] for s in agent_context.list_spaces(all_conversations=True)} == {"a1", "b1"}
    assert agent_context.stats("b1")["conv"] == "convB"
    assert agent_context.stats("a1")["exists"] is False, "跨对话空间对当前对话不可见"

    # 关闭只作用于当前对话；reclaim 只回收该对话的空间
    assert agent_context.close_conversation("convA") == 1
    assert agent_context.list_spaces(all_conversations=True) and \
        [s["space"] for s in agent_context.list_spaces(all_conversations=True)] == ["b1"]
    assert agent_context.has_space("b1")


# ---------- 2. 同对话共享 / 跨对话隔离 ----------
class _FakeLLM:
    model = "test-model"

    def __init__(self, reply="子结论"):
        self.reply = reply

    def chat_stream(self, messages, **kw):
        return {"text": self.reply, "tool_calls": []}


def test_agents_in_same_conversation_share_and_others_isolated():
    _reset()
    agent_context.set_conversation("convA")
    sid_a = agent_context.open_space(agent_context.DEFAULT_SPACE, seed="A 议题", owner="main")
    agent_context.append(sid_a, "main", "A 的既有产出")

    # 同对话：子 Agent 未显式指定空间 → 落到本对话的活跃空间（读得到、写得进）
    agent_subagent.dispatch_sub_agents(
        _FakeLLM("A 的子结论"),
        [{"title": "任务", "goal": "g", "sub": "s1"}], shared_context=True)
    body_a = agent_context.render(sid_a)
    assert "A 的既有产出" in body_a and "A 的子结论" in body_a

    # 另一对话：同名空间互不可见，写入不串到 A
    agent_context.set_conversation("convB")
    assert not agent_context.has_space(agent_context.DEFAULT_SPACE)
    agent_context.open_space(agent_context.DEFAULT_SPACE, seed="B 议题", owner="main")
    agent_subagent.dispatch_sub_agents(
        _FakeLLM("B 的子结论"),
        [{"title": "任务", "goal": "g", "sub": "s1"}], shared_context=True)

    agent_context.set_conversation("convA")
    body_a2 = agent_context.render(sid_a)
    assert "B 的子结论" not in body_a2 and "B 议题" not in body_a2


def test_dispatch_propagates_conversation_to_worker_threads(monkeypatch):
    """线程池工作线程无继承的线程局部 → 对话作用域必须在派发线程捕获并显式下传。"""
    _reset()
    agent_context.set_conversation("convX")
    captured = []

    def fake_run(llm, goal, **kw):
        captured.append((kw.get("conversation"), agent_context.current_conversation()))
        return f"done:{goal}"

    monkeypatch.setattr(agent_subagent, "run_sub_agent", fake_run)
    agent_subagent.dispatch_sub_agents(None, [{"title": "a", "goal": "A"},
                                              {"title": "b", "goal": "B"}])
    assert captured, "应并派发到工作线程"
    assert all(c[0] == "convX" for c in captured), "派发侧须把对话下传给工作线程"


def test_bind_shared_context_sets_conversation():
    _reset()
    sid = agent_subagent.bind_shared_context(True, source="sub", conversation="convZ")
    assert sid == agent_context.DEFAULT_SPACE
    assert agent_context.has_space(sid) and agent_context.current_conversation() == "convZ"
    # 换一个对话：默认空间不可见（未开启）
    agent_context.set_conversation("convY")
    assert not agent_context.has_space(agent_context.DEFAULT_SPACE)


# ---------- 3. 引擎任务线程 ----------
def _make_engine(monkeypatch, conversation="", text_only=True):
    from PyQt6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from winapp_migrator.core import agent_engine

    class _EngineLLM:
        model = "test-model"
        fell_back = False
        silent_fallback = False

        def __init__(self):
            self.tokens = {"prompt": 0, "completion": 0}

        def chat_stream(self, messages, **kw):
            return {"text": "ok", "tool_calls": [], "usage": None,
                    "cache": {"hit": 0, "miss": 0}}

    monkeypatch.setattr(agent_engine.agent_tts, "load_config", lambda: {"auto_read": False})
    monkeypatch.setattr(agent_engine.agent_sandbox, "disabled_tools", lambda: frozenset())
    monkeypatch.setattr(agent_engine.agent_sandbox, "tools_disabled_all", lambda: False)
    eng = agent_engine.AgentEngine(_EngineLLM(), text_only=text_only, direct=True,
                                   conversation=conversation)
    eng.on_status = lambda _s: None
    return eng


def test_engine_task_thread_scopes_and_restores_conversation(monkeypatch):
    _reset()
    eng = _make_engine(monkeypatch, conversation="convE")
    seen = {}
    real_open = agent_context.open_space

    def spy(*a, **kw):
        seen["conv"] = agent_context.current_conversation()
        return real_open(*a, **kw)

    monkeypatch.setattr(agent_context, "open_space", spy)
    eng.run("帮我写个插件")

    assert seen.get("conv") == "convE", "任务线程须落地引擎所属对话（自动开空间时可见）"
    assert agent_context.thread_local_conversation() == "", "任务结束须复位线程局部对话"

    # 空间落在 convE 名下；其他对话看不到
    agent_context.set_conversation("convE")
    mine = [s["space"] for s in agent_context.list_spaces()]
    assert mine, "任务开始应在该对话内自动开启共同上下文空间"
    agent_context.set_conversation("other")
    assert agent_context.list_spaces() == [], "其他对话不得看到该空间"


# ---------- 4. 成员后台运行器 ----------
def test_team_runner_thread_carries_conversation(monkeypatch):
    _reset()
    seen = {}

    def fake_run_agent_llm(llm, wf, goal, **kw):
        seen["conv"] = agent_context.current_conversation()
        seen["kw"] = kw.get("conversation")
        return "成员完成"

    monkeypatch.setattr(agent_subagent, "run_agent_llm", fake_run_agent_llm)
    monkeypatch.setattr(agent_team_run, "_load_client", lambda wf: None)
    agent_team_run.team_reset()
    ok, _msg = agent_team_run.team_start("wf:product_dev", "_default", "干活",
                                         shared=True, conversation="convT")
    assert ok
    for _ in range(200):
        if seen:
            break
        time.sleep(0.02)
    assert seen.get("conv") == "convT", "成员后台线程须落地派发方对话"
    assert seen.get("kw") == "convT"


def test_team_stop_all_scoped_by_conversation(monkeypatch):
    """停止某对话的任务，不应牵连其他对话仍在后台工作的成员。"""
    _reset()

    def fake_run(llm, wf, goal, **kw):
        stop = kw.get("stop") or (lambda: False)
        for _ in range(500):        # 长任务：仅在被停止时退出（否则一直“在跑”）
            if stop():
                return "（已停止）"
            time.sleep(0.01)
        return "x"

    monkeypatch.setattr(agent_subagent, "run_agent_llm", fake_run)
    monkeypatch.setattr(agent_team_run, "_load_client", lambda wf: None)
    agent_team_run.team_reset()
    agent_team_run.team_start("wf:a", "_default", "A 的活", conversation="convA")
    agent_team_run.team_start("wf:b", "_default", "B 的活", conversation="convB")
    time.sleep(0.05)   # 让两个成员线程进入执行
    agent_team_run.team_stop_all(conversation="convA")
    for _ in range(200):    # A 的成员应在下一检查点退出
        if not agent_team_run.team_running("wf:a"):
            break
        time.sleep(0.02)
    assert not agent_team_run.team_running("wf:a"), "本对话成员应被停止"
    assert agent_team_run.team_result("wf:a") == "（已停止）"
    assert agent_team_run.team_running("wf:b"), "其他对话的成员不应被停止"
    agent_team_run.team_reset()


# ---------- 5. UI 接线（源码守卫） ----------
def test_ui_wiring_guards():
    from winapp_migrator.ui import agent_panel
    src = inspect.getsource(agent_panel)
    assert "conversation=sid" in src, "引擎须携带所属会话（对话隔离）"
    assert "set_conversation_global(sid)" in inspect.getsource(
        agent_panel.AgentPanel._bind_sess), "会话切换须切换当前对话作用域"
    assert "close_conversation(sid)" in inspect.getsource(
        agent_panel.AgentPanel._delete_session), "删除对话须回收该对话的空间"
    assert "conversation=sid" in inspect.getsource(
        agent_panel.AgentPanel._subagent_worker), "@子Agent 线程须携带所属会话"
