"""扩展热加载：刚生成好的 skill / MCP / plugin 在**同一次任务内**即可被 agent 调用。

覆盖两条链路，都真实执行（不是纸面断言）：
  · `McpManager.reload` 的**增量**语义：未变服务器的连接必须原样保留（对象同一）、
    新增/配置变更/移除各自被正确处理 —— 用 FakeTransport 精确断言建连次数；
  · `AgentEngine.reload_extensions`：真起一个由插件生成器产出的 stdio MCP server（子进程
    握手），新工具必须出现在引擎的工具列表里；新落盘的技能必须被技能库读到并并入本任务。
"""
import json
import os
import sys

import pytest

from zhuzhu_Copilot.core import (agent_engine, agent_mcp, agent_plugins,
                                 agent_skills, agent_tools)
from zhuzhu_Copilot.core.agent_mcp import McpError, McpManager


class _FakeTransport:
    """按实例顺序从 tool_sets 取工具（与 test_agent_mcp 同款）：可精确计数建连次数。"""
    counter = 0
    tool_sets = []

    def __init__(self, *a, **kw):
        idx = _FakeTransport.counter
        _FakeTransport.counter += 1
        self.tools = _FakeTransport.tool_sets[idx] if idx < len(_FakeTransport.tool_sets) else []

    def request(self, method, params):
        if method == "tools/list":
            return {"tools": self.tools}
        if method == "tools/call":
            return {"content": [{"type": "text", "text": "ok"}]}
        return {}

    def close(self):
        pass


@pytest.fixture()
def fake_transports(monkeypatch):
    monkeypatch.setattr(agent_mcp, "_StdioTransport", _FakeTransport)
    monkeypatch.setattr(agent_mcp, "_SSETransport", _FakeTransport)
    _FakeTransport.counter = 0
    _FakeTransport.tool_sets = []
    return _FakeTransport


def _tool(name):
    return [{"name": name, "description": name,
             "inputSchema": {"type": "object", "properties": {}}}]


def _names(mgr) -> list:
    return [t["function"]["name"] for t in mgr.tool_schemas()]


# ══════════════ 1. MCP 增量重连 ══════════════

def test_reload_keeps_unchanged_connections(fake_transports):
    """未变的服务器**不许重连**：连接对象同一、不新建传输（否则会打断正在用的服务器）。"""
    fake_transports.tool_sets = [_tool("read"), _tool("write")]
    mgr = McpManager()
    mgr.connect_all([{"name": "a", "type": "stdio", "command": "x"}])
    client_a = mgr._clients["a"]
    made = fake_transports.counter

    mgr.reload([{"name": "a", "type": "stdio", "command": "x"},
                {"name": "b", "type": "stdio", "command": "y"}])

    assert mgr._clients["a"] is client_a, "未变服务器必须复用原连接"
    assert fake_transports.counter == made + 1, "只应为新增的 b 建连"
    assert mgr.last_reload == {"added": ["b"], "updated": [], "removed": []}
    assert _names(mgr) == ["read", "write"]


def test_reload_updates_changed_and_drops_removed(fake_transports):
    fake_transports.tool_sets = [_tool("a_tool"), _tool("b_tool"), _tool("b2_tool")]
    mgr = McpManager()
    mgr.connect_all([{"name": "a", "type": "sse", "url": "http://a/sse"},
                     {"name": "b", "type": "sse", "url": "http://b/sse"}])
    client_a = mgr._clients["a"]

    # b 的配置变了 → 只重建 b（a 保留）
    mgr.reload([{"name": "a", "type": "sse", "url": "http://a/sse"},
                {"name": "b", "type": "sse", "url": "http://b2/sse"}])
    assert mgr._clients["a"] is client_a
    assert mgr.last_reload == {"added": [], "updated": ["b"], "removed": []}
    assert _names(mgr) == ["a_tool", "b2_tool"]

    # 移除 b → 断开并消失；a 仍在
    mgr.reload([{"name": "a", "type": "sse", "url": "http://a/sse"}])
    assert mgr.last_reload == {"added": [], "updated": [], "removed": ["b"]}
    assert _names(mgr) == ["a_tool"]
    assert mgr.server_for_tool("a_tool") == "a"


def test_reload_keeps_dedup_prefix_rules(fake_transports):
    """增量重连后跨服务器同名去重规则必须与全量连接一致（否则调用会路由错服务器）。"""
    fake_transports.tool_sets = [_tool("read"), _tool("read")]
    mgr = McpManager()
    mgr.connect_all([{"name": "srv_a", "type": "stdio", "command": "x"},
                     {"name": "srv_b", "type": "stdio", "command": "y"}])
    assert _names(mgr) == ["read", "srv_b_read"]

    fake_transports.tool_sets.append(_tool("extra"))
    mgr.reload([{"name": "srv_a", "type": "stdio", "command": "x"},
                {"name": "srv_b", "type": "stdio", "command": "y"},
                {"name": "srv_c", "type": "stdio", "command": "z"}])
    assert _names(mgr) == ["read", "srv_b_read", "extra"]
    assert mgr.server_for_tool("read") == "srv_a"
    assert mgr.server_for_tool("srv_b_read") == "srv_b"
    # 工具名不被重复抓取污染：同一工具始终只有一个聚合名
    assert len(set(_names(mgr))) == len(_names(mgr))


def test_reload_reports_broken_new_server(fake_transports, monkeypatch):
    """新增服务器连不上：只记错误、不影响已有连接与工具。"""
    fake_transports.tool_sets = [_tool("a_tool"), _tool("bad")]

    def _boom(self, method, params):
        raise McpError("boom")

    mgr = McpManager()
    mgr.connect_all([{"name": "a", "type": "stdio", "command": "x"}])
    monkeypatch.setattr(_FakeTransport, "request", _boom, raising=False)
    mgr.reload([{"name": "a", "type": "stdio", "command": "x"},
                {"name": "bad", "type": "stdio", "command": "y"}])
    assert _names(mgr) == ["a_tool"], "坏服务器不得影响已有工具"
    assert len(mgr.errors) == 1 and "[bad]" in mgr.errors[0]


# ══════════════ 2. 引擎热加载（真实 stdio MCP + 真实技能落盘） ══════════════

@pytest.fixture()
def iso(tmp_path, monkeypatch):
    """隔离技能/插件/MCP 配置目录（引擎与工具都读写这些路径）"""
    monkeypatch.setattr(agent_skills, "CONFIG_DIR", tmp_path / "agent")
    monkeypatch.setattr(agent_plugins, "PLUGINS_DIR", tmp_path / "plugins")
    monkeypatch.setattr(agent_plugins, "_plugin_interpreter", lambda: sys.executable)
    agent_skills._MCP_CACHE["data"] = None
    agent_skills.invalidate_skills_cache()
    agent_skills.invalidate_settings_cache()
    agent_plugins.invalidate_index()
    yield tmp_path
    agent_skills._MCP_CACHE["data"] = None
    agent_skills.invalidate_skills_cache()
    agent_plugins.invalidate_index()


def _engine(mgr=None):
    class _StubLLM:
        model = "stub"
        fell_back = False
        silent_fallback = False

    return agent_engine.AgentEngine(_StubLLM(), mcp_manager=mgr, text_only=True)


def _register_generated_mcp_plugin(home) -> tuple:
    """用插件生成器产出一个真能跑的 MCP server，并写进 mcp_servers.json（模拟 create_plugin 之后）"""
    spec = {"name": "hotplug", "summary": "热加载演示",
            "tools": [{"name": "echo", "description": "回显",
                       "input_schema": {"type": "object",
                                        "properties": {"t": {"type": "string"}},
                                        "required": ["t"]},
                       "implementation": "return str(args.get('t', ''))"}]}
    ok, err = agent_plugins._write_plugin_files("hotplug", "mcp", spec)
    assert ok, err
    server = agent_plugins.plugin_dir("hotplug") / "server.py"
    servers = [{"name": "hotplug-mcp", "type": "stdio",
                "command": sys.executable, "args": [str(server)]}]
    assert agent_skills.save_mcp_servers(servers)
    return "hotplug-mcp", "echo"


def test_engine_reload_extensions_exposes_generated_mcp_tools(iso):
    """插件的 MCP server 刚登记 → reload_extensions 后引擎工具列表里就有它的工具。"""
    srv, tool = _register_generated_mcp_plugin(iso)
    eng = _engine(McpManager())
    assert tool not in [t["function"]["name"] for t in eng._all_tools()], \
        "前置：未热加载前不该出现（证明下面断言的有效性）"

    summary = eng.reload_extensions()

    assert "MCP 工具" in summary, summary
    assert srv in summary, f"摘要应写明新增的服务器：{summary}"
    assert tool in [t["function"]["name"] for t in eng._all_tools()], \
        f"热加载后工具必须可被 agent 调用：{summary}"
    # 再刷一次：未变 → 不重连（增量语义在引擎层同样成立）
    client = eng.mcp._clients[srv]
    again = eng.reload_extensions()
    assert eng.mcp._clients[srv] is client, "未变的服务器不应重连"
    assert "新增" not in again and "更新" not in again, again


def test_engine_reload_extensions_picks_up_new_skill(iso):
    """新落盘的技能必须被技能库读到，并并入本任务技能集（下一轮即注入其 instruction）。"""
    eng = _engine()
    eng._skills_at_start = {s.get("name") for s in agent_skills.load_skills()}
    ok, msg = agent_skills.create_md_skill(
        "hot_skill", "热加载演示技能",
        "# hot_skill\n\n## 触发\n当用户要求热加载演示时使用。\n\n## 流程\n1. 直接执行。\n")
    assert ok, msg

    summary = eng.reload_extensions()

    assert "hot_skill" in summary, summary
    assert "hot_skill" in eng._auto_skills, "新技能应并入本任务，供 _sync_skill_msg 注入"
    assert "hot_skill" in eng._task_skills


def test_engine_reload_extensions_survives_no_mcp():
    """没有 MCP 管理器时不得抛异常（纯技能场景）"""
    eng = _engine()
    assert isinstance(eng.reload_extensions(), str)


# ══════════════ 3. 生产者侧：生成类工具必须请求热加载 ══════════════

def test_create_tools_request_hot_reload(iso, monkeypatch):
    """create_skill / create_plugin / create_mcp 成功后都要带 reload_extensions 标记。

    没有这个标记，引擎就不会刷新技能缓存与 MCP 连接 —— 生成成功却调不动。
    """
    res = agent_tools._create_skill("flag_skill", "标记演示",
                                    "# flag_skill\n\n## 触发\n测试。\n")
    assert res.get("reload_extensions") is True, res

    def fake_spec(desc, kind):
        return {"name": "flagplug", "summary": "标记演示",
                "tools": [{"name": "echo", "description": "回显",
                           "input_schema": {"type": "object", "properties": {}},
                           "implementation": "return 'ok'"}]}

    monkeypatch.setattr(agent_plugins, "_ai_generate_spec", fake_spec)
    res = agent_tools._create_plugin("做一个演示插件", "mcp")
    assert res.get("reload_extensions") is True, res

    res2 = agent_tools._create_mcp({"name": "flag_mcp", "type": "stdio", "command": "python",
                                    "args": ["x.py"]})
    assert res2.get("reload_extensions") is True, res2
    assert "next" not in json.dumps(res2), "文案不应再写「发起下一轮任务后生效」"


def test_engine_tool_result_carries_reload_summary(iso):
    """走完整引擎路径：create_skill 的返回文案里应出现热加载摘要，且标记键被消费掉。"""
    eng = _engine()
    eng._skills_at_start = {s.get("name") for s in agent_skills.load_skills()}
    res = eng._execute("create_skill", {"name": "e2e_skill", "description": "端到端演示",
                                        "instruction": "# e2e_skill\n\n## 触发\n测试。\n"})
    text = res.get("text") or ""
    assert "已热加载扩展" in text, text
    assert "e2e_skill" in text, text
    assert "reload_extensions" not in res, "标记键必须被引擎消费，不得泄漏给模型"


def test_mcp_servers_json_roundtrip_after_save(iso):
    """save_mcp_servers 后 load 必须看到新服务器（热加载读的就是这份配置）"""
    assert agent_skills.load_mcp_servers() == []
    agent_skills.save_mcp_servers([{"name": "s1", "type": "stdio", "command": "python"}])
    assert [s["name"] for s in agent_skills.load_mcp_servers()] == ["s1"]
    assert os.path.isfile(os.path.join(str(agent_skills.CONFIG_DIR), "mcp_servers.json"))
