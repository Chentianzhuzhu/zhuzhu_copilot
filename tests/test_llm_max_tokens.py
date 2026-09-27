"""LLM 请求输出预算（max_tokens）回归测试。

背景：本地「窗口 − 预留输出」的预算口径必须显式传给上游。不传时上游按自身默认
输出额度预留输入空间，其值可能远大于本地预留值 → 上游实际允许的输入上限比本地
预算更小，长对话末期输入挤掉输出空间直接 400（UI 显示 80% 时已超窗的根因之一）。

覆盖：
- chat 协议（/chat/completions）：max_tokens 写入请求体；
- responses 协议（/responses）：max_output_tokens 写入请求体；
- 不传 max_tokens 时保持原样（不改变上游默认行为）；
- 真实本地 HTTP 端点校验请求体（非 mock，真实网络请求）。
"""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from winapp_migrator.core import agent_llm  # noqa: E402

RECEIVED = {}


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        RECEIVED.update({"path": self.path, "payload": body})
        # 最小可用的 SSE 流：chat 结构；responses 路径仅要求 200 + 流可读
        data = (b'data: {"id":"x","choices":[{"delta":{"content":"ok"},'
                b'"finish_reason":"stop"}],"usage":'
                b'{"prompt_tokens":1,"completion_tokens":1}}\n\n'
                b"data: [DONE]\n\n")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


@pytest.fixture()
def llm_server():
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/v1"
    srv.shutdown()
    srv.server_close()


def test_chat_stream_sends_max_tokens(llm_server):
    client = agent_llm.LLMClient(base_url=llm_server, api_key="k",
                                 model="m", protocol="chat")
    client.chat_stream([{"role": "user", "content": "hi"}], max_tokens=32768)
    assert RECEIVED["path"].endswith("/chat/completions")
    assert RECEIVED["payload"]["max_tokens"] == 32768


def test_chat_stream_without_max_tokens_keeps_default(llm_server):
    """不传输出预算：请求体保持原样（上游按默认额度预留，不强行改动行为）"""
    client = agent_llm.LLMClient(base_url=llm_server, api_key="k",
                                 model="m", protocol="chat")
    client.chat_stream([{"role": "user", "content": "hi"}])
    assert "max_tokens" not in RECEIVED["payload"]


def test_responses_stream_sends_max_output_tokens(llm_server):
    client = agent_llm.LLMClient(base_url=llm_server, api_key="k",
                                 model="m", protocol="responses")
    client.chat_stream([{"role": "user", "content": "hi"}], max_tokens=8192)
    assert RECEIVED["path"].endswith("/responses")
    assert RECEIVED["payload"]["max_output_tokens"] == 8192


def test_zero_max_tokens_is_not_sent(llm_server):
    """max_tokens=0 视为未提供（调用方尚未确定预留值时不上游限制输出）"""
    client = agent_llm.LLMClient(base_url=llm_server, api_key="k",
                                 model="m", protocol="chat")
    client.chat_stream([{"role": "user", "content": "hi"}], max_tokens=0)
    assert "max_tokens" not in RECEIVED["payload"]
