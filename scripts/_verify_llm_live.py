"""LLM 深度分析验证：真实 HTTP 请求打到本地测试服务器，验证请求体/解析/降级链路"""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from winapp_migrator.core.security_engine import llm_analyzer

RECEIVED = {}


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        RECEIVED["path"] = self.path
        RECEIVED["model"] = body.get("model")
        RECEIVED["has_key"] = bool((self.headers.get("Authorization") or "").startswith("Bearer "))
        resp = {"choices": [{"message": {"content": json.dumps(
            {"verdict": "malicious", "confidence": 92, "reason": "下载执行编码命令"})}}]}
        data = json.dumps(resp).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


srv = HTTPServer(("127.0.0.1", 0), _Handler)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()

cfg = {"enabled": True, "base_url": f"http://127.0.0.1:{port}/v1",
       "api_key": "test-key", "model": "test-model", "timeout_s": 5.0}
sample = {"kind": "script", "script": "powershell -enc " + "A" * 100,
          "path": "C:/x/evil.bat", "name": "evil.bat"}

res = llm_analyzer.analyze(sample, cfg)
assert res and res["verdict"] == "malicious" and res["confidence"] == 92, res
assert RECEIVED.get("model") == "test-model", RECEIVED
assert RECEIVED.get("path") == "/v1/chat/completions", RECEIVED
assert RECEIVED.get("has_key"), RECEIVED
print("[OK] analyze 真实POST:", RECEIVED)

# 解析失败/服务错误 → None 降级
res2 = llm_analyzer._parse("not json at all")
assert res2 is None
bad = dict(cfg, base_url="http://127.0.0.1:1/v1")  # 不可达端口
assert llm_analyzer.analyze(sample, bad) is None
print("[OK] 降级: 不可达服务返回 None")

assert llm_analyzer.analyze(sample, {}) is None   # 配置不全 → None
assert llm_analyzer._parse('{"verdict":"clean","confidence":10,"reason":"ok"}')["verdict"] == "clean"
srv.shutdown()
print("全部通过")