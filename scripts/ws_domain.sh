#!/bin/bash
set -e
cd /www/auth-server
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/api/auth/login -H 'Content-Type: application/json' -d '{"username":"wstest","password":"WsPass99"}' | /www/auth-server/.venv/bin/python -c "import sys,json;print(json.load(sys.stdin)['data']['token'])" 2>/dev/null)
echo "token len=${#TOKEN}"
cat > /tmp/ws_domain.py <<PYEOF
import asyncio, json, sys
import websockets
async def run():
    url = "ws://127.0.0.1/authapp/ws?token=${TOKEN}"
    try:
        async with websockets.connect(url, open_timeout=10, close_timeout=5,
                                      additional_headers={"Host": "chentian.dpdns.org"},
                                      origin="https://chentian.dpdns.org") as ws:
            print("CONNECT_OK", url, "(Host=chentian.dpdns.org)")
            await ws.send(json.dumps({"type":"ping"}))
            msg = await asyncio.wait_for(ws.recv(), timeout=8)
            print("PONG", msg)
            await ws.close()
    except Exception as e:
        print("CONNECT_FAIL", url, "->", type(e).__name__, e)
        raise SystemExit(1)
asyncio.run(run())
PYEOF
/www/auth-server/.venv/bin/python /tmp/ws_domain.py