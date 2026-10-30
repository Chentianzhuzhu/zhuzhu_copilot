# -*- coding: utf-8 -*-
"""MCP 请求重试策略回归：只重试幂等方法，工具调用绝不重试。

为什么必须锁死：`tools/call` 发出后管道写失败/丢弃，并不代表服务端没执行。
重试会让 click / type_text 这类动作**执行两次**（用户侧就是「明明点一下却点了两下」）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zhuzhu_Copilot.core import agent_mcp  # noqa: E402


class _Counter:
    def __init__(self, fail: bool = True):
        self.n = 0
        self.fail = fail

    def __call__(self, method, params, timeout=None):
        self.n += 1
        if self.fail:
            raise agent_mcp.McpError("MCP 发送失败: broken pipe")
        return {"ok": True}


def _transport(fake):
    t = agent_mcp._StdioTransport.__new__(agent_mcp._StdioTransport)
    t._dead = ""
    t.request = fake          # 实例属性遮蔽类方法：只测重试策略
    return t


def test_tool_call_is_never_retried():
    fake = _Counter()
    t = _transport(fake)
    try:
        t.call("tools/call", {"name": "click", "arguments": {}})
    except agent_mcp.McpError:
        pass
    else:
        raise AssertionError("应当抛错")
    assert fake.n == 1, "工具调用重试会导致动作执行两次"


def test_readonly_methods_still_retry_once():
    fake = _Counter()
    t = _transport(fake)
    try:
        t.call("tools/list", {})
    except agent_mcp.McpError:
        pass
    assert fake.n == 2, "只读请求应重试一次（管道抖动不该直接失败）"


def test_disconnected_is_not_retried():
    fake = _Counter()

    def boom(method, params, timeout=None):
        fake.n += 1
        raise agent_mcp.McpError("MCP 连接已断开：读循环退出")

    t = _transport(boom)
    try:
        t.call("tools/list", {})
    except agent_mcp.McpError:
        pass
    assert fake.n == 1, "连接已断开时重试没有意义"
