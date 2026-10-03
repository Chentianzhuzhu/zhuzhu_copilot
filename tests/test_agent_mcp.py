"""agent_mcp 测试：stdio/SSE 传输的聚合去重、工具调用路由与错误收集。

用 FakeTransport 替换真实传输，不启动子进程/不联网。"""
import os
import sys

import pytest

from zhuzhu_Copilot.core import agent_mcp
from zhuzhu_Copilot.core.agent_mcp import McpError, McpManager


class _FakeTransport:
    """按实例顺序从 tool_sets 取工具；fail_on 中标记的实例序号请求抛错。"""
    counter = 0
    tool_sets = []
    results = {}
    fail_on = set()

    def __init__(self, *a, **kw):
        idx = _FakeTransport.counter
        _FakeTransport.counter += 1
        self._fail = idx in _FakeTransport.fail_on
        self.tools = _FakeTransport.tool_sets[idx] if idx < len(_FakeTransport.tool_sets) else []
        self._results = _FakeTransport.results

    def request(self, method, params):
        if self._fail:
            raise McpError("fake transport failure")
        if method == "tools/list":
            return {"tools": self.tools}
        if method == "tools/call":
            name = params.get("name", "")
            if name in self._results:
                return self._results[name]
            return {"content": [{"type": "text", "text": f"ok:{name}"}]}
        return {}

    def close(self):
        pass


@pytest.fixture()
def fake_transports(monkeypatch):
    monkeypatch.setattr(agent_mcp, "_StdioTransport", _FakeTransport)
    monkeypatch.setattr(agent_mcp, "_SSETransport", _FakeTransport)
    _FakeTransport.counter = 0
    _FakeTransport.tool_sets = []
    _FakeTransport.results = {}
    _FakeTransport.fail_on = set()
    return _FakeTransport


def test_safe_stdio_command_not_frozen():
    # 非打包模式：原样返回命令
    cfg = {"command": "python", "args": ["server.py"]}
    assert agent_mcp._safe_stdio_command(cfg) == "python"


def test_safe_stdio_command_frozen_replaces_self_exe(monkeypatch):
    monkeypatch.setattr(agent_mcp.sys, "frozen", True, raising=False)
    monkeypatch.setattr("zhuzhu_Copilot.core.agent_runtime.python_interpreter",
                        lambda: "python-test")
    cfg = {"command": os.path.realpath(sys.executable), "args": ["plugin/server.py"]}
    assert agent_mcp._safe_stdio_command(cfg) == "python-test"


def test_mcp_manager_aggregates_and_dedup(fake_transports):
    fake_transports.tool_sets = [
        [{"name": "read", "description": "read", "inputSchema": {"type": "object",
                                                                  "properties": {}}}],
        [{"name": "read", "description": "read", "inputSchema": {"type": "object",
                                                                  "properties": {}}},
         {"name": "write", "description": "write", "inputSchema": {"type": "object",
                                                                    "properties": {}}}],
    ]
    mgr = McpManager()
    tools = mgr.connect_all([
        {"name": "srv_a", "type": "stdio", "command": "x"},
        {"name": "srv_b", "type": "sse", "url": "http://example.com/sse"},
    ])
    names = [t["function"]["name"] for t in tools]
    # 同名工具跨服务器去重：后者加服务器名前缀
    assert names == ["read", "srv_b_read", "write"]
    assert mgr.errors == []
    assert mgr.server_for_tool("read") == "srv_a"
    assert mgr.server_for_tool("srv_b_read") == "srv_b"
    assert mgr.server_for_tool("write") == "srv_b"
    # 调用按原始名路由到对应服务器（返回 (文本, 图片dataURL列表)）
    assert mgr.call_tool("read", {}) == ("ok:read", [])
    assert mgr.call_tool("srv_b_read", {}) == ("ok:read", [])
    mgr.close_all()


def test_mcp_manager_collects_failed_server(fake_transports):
    fake_transports.tool_sets = [
        [{"name": "ok_tool", "description": "ok", "inputSchema": {"type": "object",
                                                                   "properties": {}}}],
        [{"name": "bad_tool", "description": "bad", "inputSchema": {"type": "object",
                                                                     "properties": {}}}],
    ]
    fake_transports.fail_on = {1}   # 第 2 个服务器（broken）连接失败
    mgr = McpManager()
    tools = mgr.connect_all([
        {"name": "good", "type": "stdio", "command": "x"},
        {"name": "broken", "type": "sse", "url": "http://example.com/sse"},
    ])
    assert [t["function"]["name"] for t in tools] == ["ok_tool"]
    # broken 服务器失败：工具不暴露，错误被收集
    assert len(mgr.errors) == 1 and "[broken]" in mgr.errors[0]
    mgr.close_all()


def test_mcp_tool_error_marked(fake_transports):
    fake_transports.tool_sets = [
        [{"name": "boom", "description": "b", "inputSchema": {"type": "object",
                                                               "properties": {}}}],
    ]
    fake_transports.results = {
        "boom": {"content": [{"type": "text", "text": "exploded"}], "isError": True},
    }
    mgr = McpManager()
    mgr.connect_all([{"name": "srv", "type": "sse", "url": "http://x/sse"}])
    text, images = mgr.call_tool("boom", {})
    assert text.startswith("[MCP 工具错误]")
    assert images == []
    mgr.close_all()


def test_mcp_tool_image_content_becomes_data_url(fake_transports):
    """MCP image 内容项 → data URL（供视觉模型查看，如电脑操控插件的屏幕截图）"""
    fake_transports.tool_sets = [
        [{"name": "shot", "description": "s", "inputSchema": {"type": "object",
                                                               "properties": {}}}],
    ]
    fake_transports.results = {
        "shot": {"content": [
            {"type": "text", "text": "已截屏"},
            {"type": "image", "data": "QUJD", "mimeType": "image/png"},
        ]},
    }
    mgr = McpManager()
    mgr.connect_all([{"name": "srv", "type": "sse", "url": "http://x/sse"}])
    text, images = mgr.call_tool("shot", {})
    assert text == "已截屏"
    assert images == ["data:image/png;base64,QUJD"]
    mgr.close_all()


def test_mcp_tool_image_only_result(fake_transports):
    """只有图片无文本时给出占位文本，避免空文本进上下文"""
    fake_transports.tool_sets = [
        [{"name": "shot", "description": "s", "inputSchema": {"type": "object",
                                                               "properties": {}}}],
    ]
    fake_transports.results = {
        "shot": {"content": [{"type": "image", "data": "QUJD",
                              "mimeType": "image/jpeg"}]},
    }
    mgr = McpManager()
    mgr.connect_all([{"name": "srv", "type": "sse", "url": "http://x/sse"}])
    text, images = mgr.call_tool("shot", {})
    assert text == "(图片结果)"
    assert images == ["data:image/jpeg;base64,QUJD"]
    mgr.close_all()


def test_tool_schemas_for_filters_by_server(fake_transports):
    fake_transports.tool_sets = [
        [{"name": "a_tool", "description": "a", "inputSchema": {"type": "object",
                                                                 "properties": {}}}],
        [{"name": "b_tool", "description": "b", "inputSchema": {"type": "object",
                                                                 "properties": {}}}],
    ]
    mgr = McpManager()
    mgr.connect_all([
        {"name": "srv_a", "type": "sse", "url": "http://a/sse"},
        {"name": "srv_b", "type": "sse", "url": "http://b/sse"},
    ])
    names_a = [t["function"]["name"] for t in mgr.tool_schemas_for({"srv_a"})]
    names_b = [t["function"]["name"] for t in mgr.tool_schemas_for({"srv_b"})]
    assert names_a == ["a_tool"]
    assert names_b == ["b_tool"]
    assert mgr.tool_schemas_for(set()) == []
    mgr.close_all()
