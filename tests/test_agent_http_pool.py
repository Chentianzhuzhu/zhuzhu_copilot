"""连接复用池回归：真实 socket 上的 keep-alive 复用、异常自愈与回退路径。

背景：Agent 长任务每轮一次流式请求，urllib 每次重建 TCP 连接（探针实测握手成本）。
agent_http 按主机复用 keep-alive 连接，本文件用本地 ThreadingHTTPServer 钉住契约：

  ① 完整读完 → 连接归还池，下一请求复用同一连接（同一客户端端口）；
  ② 提前 close / 读异常 → 连接丢弃，不复用半读的连接；
  ③ 状态码 >= 400 → 抛 urllib.error.HTTPError 且错误体可读；池中不留坏连接；
  ④ 池开关关闭（cap_http_pool）/ 代理配置 → 回退 urllib 原路径，功能不变；
  ⑤ 死连接复用失败 → 自动回退 urllib 一次（自愈）；
  ⑥ agent_llm 流式解析（文本/工具调用/usage/[DONE]）在池路径下逐字节等价。

约定：不 mock 传输层 —— 请求真打本地 socket，用服务端观察到的连接身份断言复用。
"""
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from zhuzhu_Copilot.core import agent_http, agent_llm                # noqa: E402
from zhuzhu_Copilot.core import agent_skills as sk                   # noqa: E402


# ---------------- 本地 SSE 服务器（真实 socket） ----------------

class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr):
        super().__init__(addr, _Handler)
        self.requests = []            # [(连接对象, Connection 头, 请求体)]
        self.sse_chunks = _sse_bytes([{"choices": [{"delta": {"content": "ok"}}]}])
        self.status = 200
        self.error_body = b"{}"


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):     # 静音测试日志
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b"{}"
        self.server.requests.append(
            (self.connection, self.headers.get("Connection"),
             json.loads(body.decode("utf-8") or "{}")))
        if self.server.status != 200:
            data = self.server.error_body
            self.send_response(self.server.status)
        else:
            data = self.server.sse_chunks
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _sse_bytes(objs) -> bytes:
    """OpenAI 兼容 SSE：data: {json}\\n\\n … + [DONE] 帧。"""
    out = b""
    for o in objs:
        out += b"data: " + json.dumps(o, ensure_ascii=False).encode("utf-8") + b"\n\n"
    return out + b"data: [DONE]\n\n"


@pytest.fixture()
def sse_server():
    srv = _Server(("127.0.0.1", 0))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    agent_http.close_idle()
    yield srv
    srv.shutdown()
    srv.server_close()
    agent_http.close_idle()


def _url(srv, path="/v1/chat/completions") -> str:
    return f"http://127.0.0.1:{srv.server_address[1]}{path}"


def _req(srv, path="/v1/chat/completions"):
    return urllib.request.Request(
        _url(srv, path),
        data=json.dumps({"model": "m", "stream": True}).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer k"},
        method="POST")


def _drain(resp) -> bytes:
    """按 agent_llm 的读法逐行读完（读到 EOF 触发连接归还）。"""
    out = []
    while True:
        raw = resp.readline()
        if not raw:
            break
        out.append(raw)
    return b"".join(out)


# ---------------- ① 复用 ----------------

def test_pool_reuses_connection_across_requests(sse_server):
    r1 = agent_http.open_stream(_req(sse_server), timeout=5)
    assert b"data:" in _drain(r1)
    r2 = agent_http.open_stream(_req(sse_server), timeout=5)
    assert b"data:" in _drain(r2)

    (conn1, hdr1, _), (conn2, hdr2, _) = sse_server.requests
    assert conn1 is conn2, "完整读完后连接必须归还池并被下一请求复用"
    assert hdr1 != "close" and hdr2 != "close", "池路径不得发 Connection: close"


def test_idle_recycle_opens_new_connection(sse_server, monkeypatch):
    monkeypatch.setattr(agent_http, "IDLE_RECYCLE_SECS", 0.01)
    _drain(agent_http.open_stream(_req(sse_server), timeout=5))
    time.sleep(0.05)
    _drain(agent_http.open_stream(_req(sse_server), timeout=5))
    conn1, conn2 = sse_server.requests[0][0], sse_server.requests[1][0]
    assert conn1 is not conn2, "空闲超限的连接必须回收重连（避免复用死连接）"


def test_pool_size_is_bounded(sse_server):
    # 并发占用：先同时开 N+1 条流（池空 → 各建连接），再全部读完归还 → 多余被关掉
    streams = [agent_http.open_stream(_req(sse_server), timeout=5)
               for _ in range(agent_http.MAX_IDLE_PER_HOST + 1)]
    for resp in streams:
        assert b"data:" in _drain(resp)
    bucket = agent_http._POOL[("http", "127.0.0.1", sse_server.server_address[1])]
    assert len(bucket) == agent_http.MAX_IDLE_PER_HOST, "空闲连接数必须受上限约束"


# ---------------- ② 提前关闭 / 读异常 → 丢弃 ----------------

def test_early_close_discards_connection(sse_server):
    r1 = agent_http.open_stream(_req(sse_server), timeout=5)
    r1.readline()                     # 只读一行就停（模拟 stop / 看门狗中断）
    r1.close()
    _drain(agent_http.open_stream(_req(sse_server), timeout=5))
    conn1, conn2 = sse_server.requests[0][0], sse_server.requests[1][0]
    assert conn1 is not conn2, "半读的连接不得复用（socket 内残留数据）"


# ---------------- ③ HTTP 错误：语义与 urlopen 一致 ----------------

def test_http_error_raises_with_readable_body(sse_server):
    sse_server.status = 429
    sse_server.error_body = json.dumps(
        {"error": {"message": "rate limited"}}).encode("utf-8")
    with pytest.raises(urllib.error.HTTPError) as ei:
        agent_http.open_stream(_req(sse_server), timeout=5)
    assert ei.value.code == 429
    assert b"rate limited" in ei.value.read(), "错误体必须可读（调用方重试文案依赖它）"

    sse_server.status = 200               # 错误连接已被丢弃：下一请求照常成功
    assert b"data:" in _drain(agent_http.open_stream(_req(sse_server), timeout=5))


# ---------------- ④ 开关 / 代理 → 回退 urllib 原路径 ----------------

def test_cap_switch_off_falls_back_to_urllib(sse_server, monkeypatch):
    monkeypatch.setattr(sk, "cap_enabled", lambda key: False)
    key = ("http", "127.0.0.1", sse_server.server_address[1])
    assert b"data:" in _drain(agent_http.open_stream(_req(sse_server), timeout=5))
    assert b"data:" in _drain(agent_http.open_stream(_req(sse_server), timeout=5))
    assert not agent_http._POOL.get(key), "关闭池开关后必须走 urllib 原路径（不建池）"
    conn1, conn2 = sse_server.requests[0][0], sse_server.requests[1][0]
    assert conn1 is not conn2, "urllib 原路径每次新建连接（与优化前行为一致）"


def test_proxy_config_disables_pool_for_remote_host(monkeypatch):
    monkeypatch.setattr(agent_http, "_PROXY_CHECK", [True])
    assert agent_http.pool_enabled("api.example.com") is False, "有代理时远程主机必须让路"
    assert agent_http.pool_enabled("127.0.0.1") is True, "本机直连不受系统代理影响"


# ---------------- ⑤ 死连接自愈 ----------------

def test_stale_pooled_connection_falls_back(sse_server):
    _drain(agent_http.open_stream(_req(sse_server), timeout=5))
    for bucket in agent_http._POOL.values():
        for ent in bucket:
            ent.conn.sock.close()        # 模拟对端已断开（sock 对象还在但不可用）
    resp = agent_http.open_stream(_req(sse_server), timeout=5)
    assert b"data:" in _drain(resp), "复用死连接失败必须自动回退 urllib 完成请求"


# ---------------- ⑥ 与 agent_llm 流式解析的集成（池路径逐字节等价） ----------------

def _client(srv) -> agent_llm.LLMClient:
    return agent_llm.LLMClient(base_url=_url(srv, "/v1"),
                               api_key="test-key", model="test-model", timeout=5)


def test_chat_stream_once_over_pool_parses_full_result(sse_server):
    sse_server.sse_chunks = _sse_bytes([
        {"choices": [{"delta": {"reasoning_content": "思考中"}}]},
        {"choices": [{"delta": {"content": "你"}}]},
        {"choices": [{"delta": {"content": "好"}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "call_1",
             "function": {"name": "read_file", "arguments": '{"path"'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": ': "a.txt"}'}}]}}]},
        {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                                  "total_tokens": 15,
                                  "prompt_cache_hit_tokens": 8,
                                  "prompt_cache_miss_tokens": 2}},
    ])
    client = _client(sse_server)
    seen = {"reasoning": [], "delta": []}
    got = client._chat_stream_once(
        [{"role": "user", "content": "hi"}],
        on_delta=seen["delta"].append, on_reasoning=seen["reasoning"].append)

    assert got["text"] == "你好"
    assert seen["delta"] == ["你", "好"] and seen["reasoning"] == ["思考中"]
    assert got["tool_calls"] == [{"id": "call_1", "type": "function",
                                  "function": {"name": "read_file",
                                               "arguments": '{"path": "a.txt"}'}}]
    assert got["usage"] == {"prompt_tokens": 10, "completion_tokens": 5,
                            "cache_hit": 8, "cache_miss": 2}
    assert got["cache"] == {"hit": 8, "miss": 2}

    # 第二轮：同一连接复用（长任务流的关键收益点）
    client._chat_stream_once([{"role": "user", "content": "hi"}])
    conn1, conn2 = sse_server.requests[0][0], sse_server.requests[1][0]
    assert conn1 is conn2, "长任务相邻两轮请求应复用同一连接"


def test_chat_stream_once_with_pool_disabled_still_completes(sse_server, monkeypatch):
    sse_server.sse_chunks = _sse_bytes([
        {"choices": [{"delta": {"content": "回退"}}]},
        {"choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 1,
                                  "total_tokens": 2}},
    ])
    monkeypatch.setattr(sk, "cap_enabled", lambda key: False)
    got = _client(sse_server)._chat_stream_once([{"role": "user", "content": "hi"}])
    assert got["text"] == "回退" and got["usage"]["prompt_tokens"] == 1
    assert not agent_http._POOL, "回退路径不得使用连接池"


def test_chat_stream_once_stop_discards_connection(sse_server):
    sse_server.sse_chunks = _sse_bytes([
        {"choices": [{"delta": {"content": "a"}}]},
        {"choices": [{"delta": {"content": "b"}}]},
    ])
    client = _client(sse_server)
    flag = {"stop": False}

    def on_delta(_chunk):
        flag["stop"] = True           # 首个增量后立即停止（模拟用户点停止）

    with pytest.raises(agent_llm.AgentLLMError):
        client._chat_stream_once([{"role": "user", "content": "hi"}],
                                 on_delta=on_delta, stop=lambda: flag["stop"])
    assert not agent_http._POOL.get(
        ("http", "127.0.0.1", sse_server.server_address[1])), \
        "stop 中断的连接不得归还池"