"""工作团能力面（协作权限）：子 Agent 白名单扩展 + 注册权限字段 + 工具权限校验。

覆盖：
1. 工作流 tools.py 声明 SUB_AGENT_ALLOWED → 其子 Agent 工具集含自定义/内置协作工具
2. register_sub_agent 写入 allow_chat / share_context 并回读（缺省全开，显式关闭保留）
3. chat_with / look_context 对权限的拒绝与放行（真实工具调用，不 mock）
4. 子 Agent 扩展白名单与内置白名单的交集（未声明即向后兼容）
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from zhuzhu_Copilot.core import agent_bus, agent_context, agent_subagent, agent_tools


def _clean():
    agent_bus.reset_bus()
    agent_bus.reset_ledgers()
    agent_context.reset_all()
    agent_context.set_current("")
    agent_context.set_source("")


def test_whitelist_extra_from_workflow(tmp_path, monkeypatch):
    from zhuzhu_Copilot.core import agent_workflow
    monkeypatch.setattr(agent_workflow, "workflow_dir", lambda name: tmp_path / "wf_custom")
    wf_dir = tmp_path / "wf_custom"
    wf_dir.mkdir(exist_ok=True)
    (wf_dir / "tools.py").write_text(
        "TOOLS = []\n"
        "def execute_tool(name, args, allow_dangerous=False):\n"
        "    return {'text': 'x', 'images': []}\n"
        "SUB_AGENT_ALLOWED = ('chat_with', 'look_context')\n",
        encoding="utf-8")
    # 无声明的工作流：扩展白名单为空（向后兼容）
    assert agent_workflow.workflow_extra_whitelist("wf_custom") == {"chat_with", "look_context"}
    wh = agent_subagent.agent_subagent_whitelist("wf_custom")
    assert "chat_with" in wh and "look_context" in wh
    assert "read_file" in wh, "内置白名单仍在"


def test_register_subagent_perms(tmp_path, monkeypatch):
    """注册子 Agent 的 allow_chat / share_context 字段写入并回读（缺省全开）。"""
    from zhuzhu_Copilot.core import agent_workflow
    wf_dir = tmp_path / "wf_perms"
    wf_dir.mkdir(exist_ok=True)
    (wf_dir / "tools.py").write_text(
        "TOOLS = []\n"
        "def execute_tool(name, args, allow_dangerous=False):\n"
        "    return {'text': 'x', 'images': []}\n",
        encoding="utf-8")
    monkeypatch.setattr(agent_workflow, "workflow_dir",
                        lambda name: wf_dir)
    monkeypatch.setattr(agent_subagent, "_subagent_file",
                        lambda workflow="": wf_dir / "subagents.json")
    # 预置迁移标记：跳过存量"翻全开"迁移，验证迁移后的显式开关语义
    (wf_dir / "subagents.json").write_text('{"_migrated_v2": true}', encoding="utf-8")
    ok, _ = agent_subagent.register_subagent(
        "coder", "编码", "完成编码任务", allowed="read_file,write_file",
        workflow="wf_perms", allow_chat=True, share_context=True)
    assert ok
    recs = agent_subagent.registered_subagents("wf_perms")
    assert recs and recs[0]["name"] == "coder"
    assert recs[0]["allow_chat"] is True
    assert recs[0]["share_context"] is True
    # 缺省全开（共享全开策略：新注册默认 allow_chat/share_context 均为 True）
    ok, _ = agent_subagent.register_subagent(
        "plain", "普通", "普通任务", workflow="wf_perms")
    assert ok
    by_name = {r["name"]: r for r in agent_subagent.registered_subagents("wf_perms")}
    assert by_name["plain"]["allow_chat"] is True
    assert by_name["plain"]["share_context"] is True
    # 显式关闭仍保留（聊天/监督权限可逐项关）
    ok, _ = agent_subagent.register_subagent(
        "closed", "关闭", "任务", workflow="wf_perms", allow_chat=False, share_context=False)
    assert ok
    by_name = {r["name"]: r for r in agent_subagent.registered_subagents("wf_perms")}
    assert by_name["closed"]["allow_chat"] is False
    assert by_name["closed"]["share_context"] is False


def test_chat_with_permission_gate(tmp_path, monkeypatch):
    """chat_with 对未开 allow_chat 的子 Agent 拒绝；开启后放行（真实工具调用）。"""
    from zhuzhu_Copilot.core import agent_workflow
    wf_dir = tmp_path / "wf_chat"
    wf_dir.mkdir(exist_ok=True)
    (wf_dir / "tools.py").write_text(
        "TOOLS = []\n"
        "def execute_tool(name, args, allow_dangerous=False):\n"
        "    return {'text': 'x', 'images': []}\n",
        encoding="utf-8")
    monkeypatch.setattr(agent_workflow, "workflow_dir", lambda name: wf_dir)
    monkeypatch.setattr(agent_subagent, "_subagent_file",
                        lambda workflow="": wf_dir / "subagents.json")
    # 预置迁移标记（迁移后显式关闭才被保留，权限门才能测出拒绝分支）
    (wf_dir / "subagents.json").write_text('{"_migrated_v2": true}', encoding="utf-8")
    _clean()
    agent_subagent.register_subagent("no_chat", "无聊天", "任务", workflow="wf_chat",
                                     allow_chat=False)
    agent_subagent.register_subagent("ok_chat", "可聊天", "任务", workflow="wf_chat",
                                     allow_chat=True)
    res = agent_tools.execute_tool("chat_with",
                                   {"to": "no_chat", "text": "在吗"},
                                   workflow="wf_chat")
    assert "未开启聊天权限" in res["text"]
    res = agent_tools.execute_tool("chat_with",
                                   {"to": "ok_chat", "text": "在吗"},
                                   workflow="wf_chat")
    assert "已发送" in res["text"]
    assert agent_bus.message_count("sub:ok_chat") == 1


def test_look_context_permission_gate(tmp_path, monkeypatch):
    """look_context 对未开 share_context 的子 Agent 拒绝；开启后可看轨迹。"""
    from zhuzhu_Copilot.core import agent_workflow
    wf_dir = tmp_path / "wf_look"
    wf_dir.mkdir(exist_ok=True)
    (wf_dir / "tools.py").write_text(
        "TOOLS = []\n"
        "def execute_tool(name, args, allow_dangerous=False):\n"
        "    return {'text': 'x', 'images': []}\n",
        encoding="utf-8")
    monkeypatch.setattr(agent_workflow, "workflow_dir", lambda name: wf_dir)
    monkeypatch.setattr(agent_subagent, "_subagent_file",
                        lambda workflow="": wf_dir / "subagents.json")
    # 预置迁移标记（迁移后显式关闭才被保留，权限门才能测出拒绝分支）
    (wf_dir / "subagents.json").write_text('{"_migrated_v2": true}', encoding="utf-8")
    _clean()
    agent_subagent.register_subagent("private_a", "私有", "任务", workflow="wf_look",
                                     share_context=False)
    agent_subagent.register_subagent("open_a", "公开", "任务", workflow="wf_look",
                                     share_context=True)
    agent_bus.ledger("sub:private_a").add("tool", "read_file", "d:/a.py")
    agent_bus.ledger("sub:open_a").add("tool", "read_file", "d:/b.py")
    res = agent_tools.execute_tool("look_context", {"agent": "private_a"},
                                   workflow="wf_look")
    assert "未开启上下文共享" in res["text"]
    res = agent_tools.execute_tool("look_context", {"agent": "open_a"},
                                   workflow="wf_look")
    assert "d:/b.py" in res["text"] and "read_file" in res["text"]


def test_dispatch_survives_task_cut(monkeypatch):
    """团队派发常驻：任务类别裁剪后 dispatch_sub_agents 仍可用（_CORE_TOOLS）。"""
    from zhuzhu_Copilot.core import agent_engine, agent_llm
    eng = agent_engine.AgentEngine(llm=agent_llm.LLMClient())
    eng._task_groups = ["browser"]   # 模拟命中「浏览器」类别裁剪
    names = {t["function"]["name"] for t in eng._all_tools()}
    assert "dispatch_sub_agents" in names, "裁剪后团队派发工具必须保留"
    for n in ("chat_with", "look_context", "pause_agent", "resume_agent",
              "warn_agent", "shared_context"):
        assert n in names, f"裁剪后协作工具 {n} 必须保留"


def test_run_agent_llm_injects_inbox(tmp_path, monkeypatch):
    """chat_with 发给跨工作流主 Agent 的消息，在其任务开始时注入上下文。"""
    from zhuzhu_Copilot.core import agent_engine, agent_subagent, agent_workflow
    wf_dir = tmp_path / "backend_dev"
    wf_dir.mkdir(exist_ok=True)
    (wf_dir / "workflow.json").write_text(
        '{"name": "backend_dev", "enabled": true, "description": "t"}',
        encoding="utf-8")
    monkeypatch.setattr(agent_workflow, "workflow_dir", lambda name: wf_dir)
    # 隔离真实 LLM 客户端加载（跨工作流无自定义 llm.py 时会回退真实全局客户端，
    # 测试只验证收件箱注入逻辑，不触网）
    monkeypatch.setattr(agent_workflow, "load_llm_client",
                        lambda cfg, workflow=None: (None, ""))

    seen = {}

    class _FakeLLM:
        def chat_stream(self, messages, **kw):
            seen["msgs"] = list(messages)
            return {"text": "完成", "tool_calls": [], "usage": {}}

    _clean()
    assert agent_bus.send_message("main", "wf:backend_dev", "请先完成接口设计", "chat")
    out = agent_subagent.run_agent_llm(
        _FakeLLM(), "backend_dev", "实现登录接口", agent_id="wf:backend_dev")
    assert out == "完成"
    # build_content 返回 OpenAI 兼容 list（多模态分段），需提取文本段
    texts = []
    for m in seen["msgs"]:
        c = m.get("content") or ""
        if isinstance(c, list):
            texts.append(" ".join(s.get("text", "") for s in c if isinstance(s, dict)))
        else:
            texts.append(str(c))
    joined = "\n".join(texts)
    assert "收到来自其他 Agent 的消息" in joined, "收件箱消息未注入跨工作流主 Agent"
    assert "请先完成接口设计" in joined
    assert "实现登录接口" in joined