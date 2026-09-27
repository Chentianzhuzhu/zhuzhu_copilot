"""LLM 深度分析测试：请求/解析健壮性；本地 HTTP 服务验证真实请求（不依赖外网）"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from winapp_migrator.core.security_engine import llm_analyzer

RECEIVED = {}


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        RECEIVED.update({"path": self.path, "model": body.get("model"),
                         "auth": self.headers.get("Authorization", "")})
        content = json.dumps({"verdict": "malicious", "confidence": 92,
                              "reason": "编码命令下载执行"})
        resp = {"choices": [{"message": {"content": content}}]}
        data = json.dumps(resp).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
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


def _cfg(base_url):
    return {"enabled": True, "base_url": base_url, "api_key": "k",
            "model": "m", "timeout_s": 5.0}


def test_parse_variants():
    assert llm_analyzer._parse('{"verdict":"malicious","confidence":90,"reason":"x"}')["verdict"] == "malicious"
    assert llm_analyzer._parse('前缀 {"verdict":"clean","confidence":10,"reason":"ok"} 后缀')["verdict"] == "clean"
    assert llm_analyzer._parse("not json") is None
    assert llm_analyzer._parse('{"verdict":"unknown","confidence":90}') is None
    assert llm_analyzer._parse(None) is None
    # 置信度钳制 0-100
    assert llm_analyzer._parse('{"verdict":"clean","confidence":999}')["confidence"] == 100


def test_analyze_real_request(llm_server):
    sample = {"kind": "script", "script": "x", "path": "C:/a.bat", "name": "a.bat"}
    res = llm_analyzer.analyze(sample, _cfg(llm_server))
    assert res and res["verdict"] == "malicious"
    assert RECEIVED["path"] == "/v1/chat/completions"
    assert RECEIVED["model"] == "m"
    assert RECEIVED["auth"].startswith("Bearer k")


def test_degrade_on_unreachable():
    cfg = _cfg("http://127.0.0.1:1/v1")   # 必不可达端口
    assert llm_analyzer.analyze({"kind": "file"}, cfg) is None


def test_degrade_on_bad_cfg():
    assert llm_analyzer.analyze({"kind": "file"}, {}) is None
    assert llm_analyzer.analyze({"kind": "file"}, None) is None
    assert llm_analyzer.analyze({"kind": "file"}, {"enabled": True}) is None  # 缺 base_url/model


def test_resolve_cfg_respects_explicit_disable(monkeypatch):
    from winapp_migrator.core.security_engine.config import config
    monkeypatch.setitem(config.data.setdefault("llm", {}), "enabled", False)
    assert llm_analyzer.resolve_cfg() is None
    monkeypatch.setitem(config.data["llm"], "enabled", True)
    monkeypatch.setitem(config.data["llm"], "base_url", "http://127.0.0.1:9/v1")
    monkeypatch.setitem(config.data["llm"], "api_key", "x")
    monkeypatch.setitem(config.data["llm"], "model", "m")
    cfg = llm_analyzer.resolve_cfg()
    assert cfg and cfg["base_url"] == "http://127.0.0.1:9/v1"