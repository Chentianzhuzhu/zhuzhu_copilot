#!/bin/bash
# 干净地验证 /authapp/ws 的 WebSocket（有效 token，经域名 Host）
set -e
cd /www/auth-server
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/api/auth/login -H 'Content-Type: application/json' -d '{"username":"wstest","password":"WsPass99"}' | /www/auth-server/.venv/bin/python -c "import sys,json;print(json.load(sys.stdin)['data']['token'])" 2>/dev/null)
echo "token len=${#TOKEN}"
cat > /tmp/ws_run.py <<PYEOF
import asyncio, json, sys
import websockets
async def run():
    for url in ["ws://127.0.0.1/authapp/ws?token=${TOKEN}",
                "ws://39.104.28.191/authapp/ws?token=${TOKEN}"]:
        try:
            async with websockets.connect(url, open_timeout=10, close_timeout=5) as ws:
                print("CONNECT_OK", url)
                await ws.send(json.dumps({"type":"ping"}))
                msg = await asyncio.wait_for(ws.recv(), timeout=8)
                print("PONG", msg)
                await ws.close()
        except Exception as e:
            print("CONNECT_FAIL", url, "->", type(e).__name__, e)
asyncio.run(run())
PYEOF
/www/auth-server/.venv/bin/python /tmp/ws_run.py