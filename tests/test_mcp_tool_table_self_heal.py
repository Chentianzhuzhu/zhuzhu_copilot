"""MCP 工具表自愈回归：启动竞态让工具表变空时，不能再让用户看到「未知 MCP 工具」。

实测链路：主进程比插件子进程早 7 秒起来 → 第一次 `initialize`/`tools/list` 握手失败 →
`_clients` 空 → 聚合工具表空 → **之后再也不补**。用户侧现象是
「[MCP 错误] 未知 MCP 工具: screen_shot」，而插件进程明明好着
（事后用 McpManager 手动连立刻成功，22 个工具、零错误）。

修法两处：
1. `McpManager.call_tool` 在「工具名不认识 + 整个连接表为空」时按上次配置自愈重连一次；
2. `AgentPanel` 挂 20s 看门狗，工具表为空就后台重连并发状态提示。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

import pytest

from zhuzhu_Copilot.core import agent_mcp

CFG = [{"name": "computer-control-mcp", "type": "stdio",
        "command": "python", "args": ["server.py"]}]


def test_self_heal_recovers_an_empty_tool_table(monkeypatch):
    """第一次握手失败 → 工具表空；自愈重连后工具回来。"""
    mgr = agent_mcp.McpManager()
    state = {"n": 0}

    def _fake_connect(c):
        state["n"] += 1
        if state["n"] == 1:
            return False                      # 启动竞态：第一次失败
        mgr._schemas["computer-control-mcp"] = [
            {"type": "function",
             "function": {"name": "screen_shot", "parameters": {}}}]
        mgr._clients["computer-control-mcp"] = object()
        return True

    monkeypatch.setattr(mgr, "_connect", _fake_connect)
    mgr.connect_all(CFG)
    assert mgr.tool_schemas() == [], "握手失败应当留下空表（这就是用户的现场）"
    assert mgr._self_heal() is True
    names = [t["function"]["name"] for t in mgr.tool_schemas()]
    assert names == ["screen_shot"], names


def test_unknown_tool_name_does_not_trigger_a_reconnect(monkeypatch):
    """模型拼错工具名时**不能**触发自愈（那会把现有连接全部断开）。"""
    mgr = agent_mcp.McpManager()

    class _FakeClient:
        def call_tool(self, name, args):
            return ("ok", [])

    mgr._clients["cc"] = _FakeClient()
    mgr._schemas["cc"] = [{"type": "function",
                           "function": {"name": "screen_shot", "parameters": {}}}]
    mgr._rebuild_aggregate()
    mgr._last_servers = CFG
    calls = []
    monkeypatch.setattr(mgr, "connect_all", lambda s: calls.append(s) or [])
    with pytest.raises(agent_mcp.McpError):
        mgr.call_tool("scren_shot", {})        # 拼错
    assert calls == [], "有连接时拼错名字不该重连"


def test_agent_panel_has_an_mcp_watchdog():
    """面板必须挂 MCP 自愈看门狗（否则启动竞态要等用户重启才好）。"""
    from zhuzhu_Copilot.ui import agent_panel as ap
    assert hasattr(ap.AgentPanel, "_start_mcp_watchdog")
    assert hasattr(ap.AgentPanel, "_mcp_self_heal")
    src = ap.__file__ and open(ap.__file__, encoding="utf-8").read()
    assert "_start_mcp_watchdog()" in src, "启动流程里要调用它"
