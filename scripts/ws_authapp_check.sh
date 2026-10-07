#!/bin/bash
# 验证 /authapp/ws 子路径下的 WebSocket（含域名 Host 头）鉴权链路
set -e
cd /www/auth-server

# 确保测试用户存在
/www/auth-server/.venv/bin/python - <<'PY'
import bcrypt, subprocess
h = bcrypt.hashpw(b'WsPass99', bcrypt.gensalt(12)).decode()
sql = f"USE zhuzhu_copilot_auth; INSERT INTO users (username,password_hash,security_question,security_answer,status) VALUES ('wstest','{h}','q','a','active') ON DUPLICATE KEY UPDATE password_hash=VALUES(password_hash), status='active';"
p = subprocess.run(["mysql","-uroot","-pwindows10","-e",sql], capture_output=True, text=True)
print("user rc", p.returncode)
PY

TOKEN=$(curl -s -X POST http://127.0.0.1:8000/api/auth/login -H 'Content-Type: application/json' -d '{"username":"wstest","password":"WsPass99"}' | .venv/bin/python -c "import sys,json;print(json.load(sys.stdin)['data']['token'])")
echo "token ok (${#TOKEN})"

echo "--- 经 Nginx /authapp/ws（域名 Host） ---"
/www/auth-server/.venv/bin/python - <<PY
import asyncio, sys, json
sys.argv=["ws_test", "$TOKEN"]
# 手动构造带 Host 头的 ws
import websockets, urllib.request
async def run():
    extra = {"Authorization": ""}
    try:
        async with websockets.connect("ws://127.0.0.1/authapp/ws?token=$TOKEN", open_timeout=10, close_timeout=5,
                                      additional_headers={"Host": "chentian.dpdns.org"}) as ws:
            print("CONNECT_OK ws://127.0.0.1/authapp/ws (Host=chendian)")
            await ws.send(json.dumps({"type":"ping"}))
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=8)
                print("PONG", msg)
            except asyncio.TimeoutError:
                print("NO_PONG")
    except Exception as e:
        print("CONNECT_FAIL", type(e).__name__, e)
        raise SystemExit(1)
asyncio.run(run())
PY