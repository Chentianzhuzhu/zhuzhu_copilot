"""默认团队模式修复回归：成员主 Agent 派发/监督链路。

覆盖本次修复：
1. load_llm_client 对"未定制/缺失 llm.py"的工作流返回来源 DEFAULT_WORKFLOW
   （此前返回工作流名 → run_agent_llm 误判目标工作流有专属 client，用 settings
   默认配置重建覆盖传入 client → 成员与主 Agent 配置错位、开工失败/卡住）
2. run_agent_llm 对未定制工作流沿用传入 client；仅对真正定制 llm.py 的工作流覆盖
3. look_context 对成员"异常"附失败原因、"运行中"附开始时间（监督可诊断）
"""
import json
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from winapp_migrator.core import agent_subagent, agent_tools, agent_workflow


def _mk_wf(tmp_path, monkeypatch, name, llm_custom=False):
    """构造工作流目录：llm_custom=True 时放一个提供 LLMClient 的自定义 llm.py。"""
    wf_dir = tmp_path / name
    wf_dir.mkdir(parents=True, exist_ok=True)
    (wf_dir / "workflow.json").write_text(
        json.dumps({"name": name, "enabled": True}), encoding="utf-8")
    if llm_custom:
        (wf_dir / "llm.py").write_text(
            "class LLMClient:\n"
            "    def __init__(self, base_url=None, api_key=None, model=None,\n"
            "                 protocol='chat', **kw):\n"
            "        self.kind = 'custom'\n"
            "        self.base_url = base_url\n"
            "        self.model = model\n"
            "    def chat_stream(self, messages, **kw):\n"
            "        return {'text': 'custom', 'tool_calls': [], 'usage': {}}\n",
            encoding="utf-8")
    monkeypatch.setattr(agent_workflow, "workflow_dir", lambda n: wf_dir)
    return wf_dir


# ---------- 1. 来源标记修复 ----------
def test_load_llm_client_untouched_returns_default_source(tmp_path, monkeypatch):
    """未定制/缺失 llm.py 的工作流：load_llm_client 来源必须是 DEFAULT_WORKFLOW，
    使调用方判定"该工作流未提供专属 client"（否则 run_agent_llm 会误覆盖 client）。"""
    _mk_wf(tmp_path, monkeypatch, "backend_dev", llm_custom=False)
    cfg = {"base_url": "http://x/v1", "api_key": "k", "model": "m"}
    client, src = agent_workflow.load_llm_client(cfg, workflow="backend_dev")
    assert client is not None
    assert src == agent_workflow.DEFAULT_WORKFLOW, \
        f"未定制工作流来源应为 {agent_workflow.DEFAULT_WORKFLOW}，实为 {src!r}"


def test_load_llm_client_custom_returns_workflow_source(tmp_path, monkeypatch):
    """定制了 llm.py 的工作流：来源为工作流名，run_agent_llm 据此覆盖 client。"""
    _mk_wf(tmp_path, monkeypatch, "custom_wf", llm_custom=True)
    cfg = {"base_url": "http://x/v1", "api_key": "k", "model": "m"}
    client, src = agent_workflow.load_llm_client(cfg, workflow="custom_wf")
    assert client is not None
    assert src == "custom_wf"
    assert getattr(client, "kind", None) == "custom", "应加载工作流自定义 LLMClient"


# ---------- 2. run_agent_llm 沿用/覆盖语义 ----------
class _PassLLM:
    """记录是否被调用；返回纯文本，无工具调用。"""
    model = "stub"
    tokens = {"prompt": 0, "completion": 0}
    def __init__(self):
        self.calls = []
        self.saw_system = ""
    def chat_stream(self, messages, **kw):
        self.calls.append(messages)
        self.saw_system = next((m.get("content") or ""
                                for m in messages if m.get("role") == "system"), "")
        return {"text": "成员结论：完成", "tool_calls": [], "usage": {},
                "cache": {"hit": 0, "miss": 0}}


def test_run_agent_llm_keeps_passed_client_when_untouched(tmp_path, monkeypatch):
    """未定制 llm.py 的成员：run_agent_llm 必须沿用传入 client（被真实调用），
    不得用 settings 默认重建覆盖——否则成员与主 Agent 配置错位、开工失败。"""
    _mk_wf(tmp_path, monkeypatch, "backend_dev", llm_custom=False)
    stub = _PassLLM()
    out = agent_subagent.run_agent_llm(stub, "backend_dev", "实现登录接口",
                                       agent_id="wf:backend_dev")
    assert out == "成员结论：完成"
    assert len(stub.calls) == 1, "未定制工作流应沿用传入 client 执行（stub 必须被调用）"
    assert "后端开发" in stub.saw_system or "backend_dev" in stub.saw_system


def test_run_agent_llm_uses_custom_client_when_customized(tmp_path, monkeypatch):
    """定制了 llm.py 的工作流：run_agent_llm 用其专属 client 覆盖传入 client。"""
    _mk_wf(tmp_path, monkeypatch, "custom_wf", llm_custom=True)
    stub = _PassLLM()
    out = agent_subagent.run_agent_llm(stub, "custom_wf", "任务",
                                       agent_id="wf:custom_wf")
    assert out == "custom", "定制工作流应使用其自定义 LLMClient（返回 custom）"
    assert len(stub.calls) == 0, "定制工作流不应沿用传入 client"


# ---------- 3. look_context 状态可诊断 ----------
def test_look_context_appends_error_reason(tmp_path, monkeypatch):
    """成员异常时 look_context 附失败原因（监督可诊断，而非只看状态名）。"""
    from winapp_migrator.core import agent_bus, agent_team_run
    _mk_wf(tmp_path, monkeypatch, "backend_dev")   # 真实工作流目录，_resolve_agent_id 可解析

    class _FakeTeamRun:
        @staticmethod
        def team_status_label(aid):
            return "异常"
        @staticmethod
        def team_status(aid):
            return {aid: {"error": True, "running": False, "started": "10:00:00"}}
        @staticmethod
        def team_result(aid):
            return "调用上游失败: connection reset"

    monkeypatch.setattr(agent_team_run, "team_status_label",
                        _FakeTeamRun.team_status_label)
    monkeypatch.setattr(agent_team_run, "team_status", _FakeTeamRun.team_status)
    monkeypatch.setattr(agent_team_run, "team_result", _FakeTeamRun.team_result)
    agent_bus.reset_ledgers()
    agent_bus.ledger("wf:backend_dev").add("tool", "read_file", "d:/a.py")
    out = agent_tools.execute_tool("look_context", {"agent": "backend_dev"},
                                   workflow="product_manager")["text"]
    assert "异常" in out and "失败原因" in out and "connection reset" in out


def test_look_context_appends_started_when_running(tmp_path, monkeypatch):
    """成员运行中时 look_context 附开始时间。"""
    from winapp_migrator.core import agent_bus, agent_team_run
    _mk_wf(tmp_path, monkeypatch, "backend_dev")   # 真实工作流目录，_resolve_agent_id 可解析

    class _FakeTeamRun:
        @staticmethod
        def team_status_label(aid):
            return "运行中"
        @staticmethod
        def team_status(aid):
            return {aid: {"error": False, "running": True, "started": "10:00:00"}}
        @staticmethod
        def team_result(aid):
            return ""

    monkeypatch.setattr(agent_team_run, "team_status_label",
                        _FakeTeamRun.team_status_label)
    monkeypatch.setattr(agent_team_run, "team_status", _FakeTeamRun.team_status)
    monkeypatch.setattr(agent_team_run, "team_result", _FakeTeamRun.team_result)
    agent_bus.reset_ledgers()
    agent_bus.ledger("wf:backend_dev").add("tool", "read_file", "d:/a.py")
    out = agent_tools.execute_tool("look_context", {"agent": "backend_dev"},
                                   workflow="product_manager")["text"]
    assert "运行中" in out and "开始于 10:00:00" in out


# ---------- 4. 成员 client 与主 Agent 同配置 + 失败续跑 ----------
def test_client_from_derives_same_config_instance(monkeypatch):
    """client_from 派生与主 Agent 同配置的独立 LLMClient（base_url/model 一致）。"""
    from winapp_migrator.core import agent_llm, agent_team_run
    main = agent_llm.LLMClient(base_url="http://my/v1", api_key="k1",
                               model="my-pro", protocol="responses")
    mem = agent_team_run.client_from(main)
    assert isinstance(mem, agent_llm.LLMClient)
    assert mem is not main, "成员必须用独立实例（线程隔离）"
    assert mem.base_url == "http://my/v1" and mem.model == "my-pro"
    assert mem.api_key == "k1" and mem.protocol == "responses"


def test_team_start_keeps_passed_client(tmp_path, monkeypatch):
    """team_start 传入 client 时成员沿用（run_agent_llm 实际使用），不再 settings 重建。"""
    from winapp_migrator.core import agent_team_run, agent_subagent, agent_bus, \
        agent_context
    _mk_wf(tmp_path, monkeypatch, "backend_dev")
    seen = {}

    def _fake_run(llm, wf, goal, **kw):
        seen["client"] = llm
        return "成员结论"

    monkeypatch.setattr(agent_subagent, "run_agent_llm", _fake_run)
    agent_context.reset_all()
    agent_bus.reset_bus()
    agent_team_run.team_reset()
    from winapp_migrator.core import agent_llm
    main = agent_llm.LLMClient(base_url="http://my/v1", api_key="k1",
                               model="my-pro")
    ok, _ = agent_team_run.team_start("wf:backend_dev", "backend_dev", "任务",
                                      client=main)
    assert ok
    end = time.time() + 5
    while time.time() < end:
        if agent_team_run.team_status("wf:backend_dev").get(
                "wf:backend_dev", {}).get("done"):
            break
        time.sleep(0.02)
    assert seen.get("client") is main, "成员应沿用传入的主 Agent 同款 client"


def test_run_loop_reruns_on_inbox_after_failure(tmp_path, monkeypatch):
    """成员执行失败但收件箱有催促消息时：续跑一轮处理消息（chat_with 可唤起）。"""
    from winapp_migrator.core import agent_team_run, agent_subagent, agent_bus, \
        agent_context
    _mk_wf(tmp_path, monkeypatch, "frontend_design")
    goals = []

    def _boom(llm, wf, goal, **kw):
        goals.append(goal)
        raise RuntimeError("上游连接失败")

    monkeypatch.setattr(agent_subagent, "run_agent_llm", _boom)
    agent_context.reset_all()
    agent_bus.reset_bus()
    agent_team_run.team_reset()
    assert agent_bus.send_message("product_manager", "wf:frontend_design",
                                  "请先复现再修", "chat")
    ok, _ = agent_team_run.team_start("wf:frontend_design", "frontend_design",
                                      "排查崩溃", client=object())
    assert ok
    end = time.time() + 5
    while time.time() < end:
        st = agent_team_run.team_status("wf:frontend_design").get(
            "wf:frontend_design", {})
        if st.get("done") or st.get("error"):
            break
        time.sleep(0.02)
    assert len(goals) == 2, f"失败后应续跑处理收件箱，实际 {len(goals)} 轮"
    assert "失败" in goals[1] and "上游连接失败" in goals[1]
    assert "请先复现再修" in goals[1]