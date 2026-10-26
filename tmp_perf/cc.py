"""临时驱动：对 computer-control MCP 发一次或多次 tools/call，存下图并打印文本。

用法：
  python tmp_perf/cc.py tmp_perf/actions.jsonl
  其中 actions.jsonl 每行一个 {"name": "...", "arguments": {...}}
"""
import base64
import faulthandler
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "src" / "zhuzhu_Copilot" / "plugins" / "computer-control" / "server.py"
SHOTS = ROOT / "tmp_perf" / "shots"
SHOTS.mkdir(parents=True, exist_ok=True)
ACTIONS = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "tmp_perf" / "actions.jsonl"


def log(msg):
    print(f"[driver {time.strftime('%H:%M:%S')}] {msg}", flush=True)


log("starting server")
proc = subprocess.Popen([sys.executable, str(SERVER)], stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        cwd=str(SERVER.parent))
n = [0]


def stderr_pump():
    for line in proc.stderr:
        log("server-err: " + line.decode("utf-8", "replace").rstrip())


threading.Thread(target=stderr_pump, daemon=True).start()
faulthandler.dump_traceback_later(20, exit=False)
log(f"server pid={proc.pid}")


def call(method, params=None):
    n[0] += 1
    payload = json.dumps({"jsonrpc": "2.0", "id": n[0], "method": method,
                          "params": params or {}})
    log(f"-> {method} id={n[0]}")
    proc.stdin.write((payload + "\n").encode())
    proc.stdin.flush()
    line = proc.stdout.readline()
    log(f"<- {method} {len(line)} bytes")
    return json.loads(line.decode("utf-8", "replace"))


call("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                    "clientInfo": {"name": "driver", "version": "1"}})
# 通知没有 id、协议规定不产生响应 —— 只写不读
proc.stdin.write((json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized",
                              "params": {}}) + "\n").encode())
proc.stdin.flush()
log("initialized")

actions = [json.loads(ln) for ln in ACTIONS.read_text(encoding="utf-8").splitlines()
           if ln.strip()]
for req in actions:
    t0 = time.perf_counter()
    resp = call("tools/call", {"name": req.get("name"), "arguments": req.get("arguments") or {}})
    cost = (time.perf_counter() - t0) * 1000
    res = resp.get("result", {})
    items = res.get("content", [])
    texts = [i.get("text", "") for i in items if i.get("type") == "text"]
    print(f"=== {req.get('name')} ({cost:.0f}ms) isError={res.get('isError')}")
    print("\n".join(texts))
    for i, item in enumerate(items):
        if item.get("type") == "image" and item.get("data"):
            out = SHOTS / f"shot_{int(time.time() * 1000)}_{i}.png"
            out.write_bytes(base64.b64decode(item["data"]))
            print(f"[image saved] {out}")
faulthandler.cancel_dump_traceback_later()
proc.stdin.close()
try:
    proc.wait(timeout=3)
except subprocess.TimeoutExpired:
    proc.kill()
log("done")
