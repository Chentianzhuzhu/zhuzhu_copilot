"""临时探针：真实 MCP stdio 冒烟 + 逐工具耗时统计（只做只读调用）。"""
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "src" / "zhuzhu_Copilot" / "plugins" / "computer-control" / "server.py"

proc = subprocess.Popen([sys.executable, str(SERVER)], stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                        cwd=str(SERVER.parent))
n = [0]


def call(method, params=None):
    n[0] += 1
    t0 = time.perf_counter()
    proc.stdin.write((json.dumps({"jsonrpc": "2.0", "id": n[0], "method": method,
                                  "params": params or {}}) + "\n").encode())
    proc.stdin.flush()
    line = proc.stdout.readline()
    cost = (time.perf_counter() - t0) * 1000
    return json.loads(line.decode("utf-8", "replace")), cost


def tool(name, args=None):
    resp, cost = call("tools/call", {"name": name, "arguments": args or {}})
    res = resp.get("result", {})
    items = res.get("content", [])
    text = "\n".join(i.get("text", "") for i in items if i.get("type") == "text")
    imgs = sum(1 for i in items if i.get("type") == "image")
    ok = not res.get("isError")
    print(f"{name:18s} {cost:7.1f}ms ok={ok} imgs={imgs} text[:100]={text[:100]!r}")
    if not ok:
        print("   FULL:", text[:500])
    return text, imgs


resp, cost = call("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                 "clientInfo": {"name": "probe", "version": "1"}})
print("initialize", f"{cost:.1f}ms", resp.get("result", {}).get("serverInfo"))
resp, cost = call("tools/list")
tools = resp["result"]["tools"]
print("tools/list", f"{cost:.1f}ms", "count", len(tools))
print("names:", [t["name"] for t in tools])
tool("screen_info")
tool("list_windows", {"limit": 8})
text, imgs = tool("screen_shot")
proc.stdin.close()
try:
    proc.wait(timeout=5)
except subprocess.TimeoutExpired:
    proc.kill()
