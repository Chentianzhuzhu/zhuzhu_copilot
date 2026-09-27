# scripts/_verify_llm_analyzer.py
import json, os, sys, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

FAILS = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ((" :: " + detail) if detail else ""))
    if not cond:
        FAILS.append(name)

from winapp_migrator.core.security_engine.llm_analyzer import analyze, _parse

# 1) 关闭/缺配置 → 直接返回 None（不请求网络）
check("未启用返回None", analyze({"a": 1}, {"enabled": False}) is None)
check("缺base_url返回None", analyze({"a": 1}, {"enabled": True, "model": "m"}) is None)

# 2) 掉线路径：指向本机未监听端口 → 快速失败返回 None（降级）
import socket
s = socket.socket()
s.bind(("127.0.0.1", 0))
port = s.getsockname()[1]
s.close()
check("网络不可达快速失败", analyze({"x": 1},
      {"enabled": True, "base_url": f"http://127.0.0.1:{port}", "model": "m", "timeout_s": 3.0}) is None)

# 3) 协议验证：本地 HTTP 服务返回真实 /chat/completions 响应结构
received = {}

class H(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        received["body"] = json.loads(self.rfile.read(n).decode("utf-8"))
        received["auth"] = self.headers.get("Authorization", "")
        resp = {"choices": [{"message": {"content":
                '{"verdict": "suspicious", "confidence": 66, "reason": "pe import pattern"}'}}]}
        data = json.dumps(resp).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
    def log_message(self, *a):
        pass

srv = HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
try:
    r = analyze({"summary": "imports virtualalloc"}, {
        "enabled": True, "base_url": f"http://127.0.0.1:{srv.server_port}",
        "model": "probe-model", "api_key": "k1", "timeout_s": 8.0})
    check("协议响应解析", r is not None and r.get("verdict") == "suspicious", str(r))
    check("置信度归一", isinstance((r or {}).get("confidence"), int))
    check("请求载荷真实", (received.get("body") or {}).get("model") == "probe-model",
          str((received.get("body") or {}).get("model")))
    check("鉴权头传递", received.get("auth") == "Bearer k1")
finally:
    srv.shutdown()

# 4) 解析函数容错
check("解析纯JSON", _parse('{"verdict":"clean","confidence":10}')["verdict"] == "clean")
check("解析围栏JSON", _parse('前置说明\n{"verdict":"malicious","confidence":99}')["verdict"] == "malicious")
check("非法verdict返回None", _parse('{"verdict":"weird","confidence":50}') is None)

print("TOTAL", len(FAILS), "FAILURES")
sys.exit(1 if FAILS else 0)