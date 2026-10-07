#!/bin/bash
set -e
cd /www/auth-server
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/api/auth/login -H 'Content-Type: application/json' -d '{"username":"wstest","password":"WsPass99"}' | /www/auth-server/.venv/bin/python -c "import sys,json;print(json.load(sys.stdin)['data']['token'])" 2>/dev/null)
echo "== websockets 客户端 @ $(date +%T) =="
/www/auth-server/.venv/bin/python -c "
import asyncio,json,websockets
async def r():
  try:
    async with websockets.connect('ws://127.0.0.1/authapp/ws?token=$TOKEN',open_timeout=8,additional_headers={'Host':'chentian.dpdns.org'}) as ws:
      await ws.send(json.dumps({'type':'ping'})); print('PONG',await asyncio.wait_for(ws.recv(),5))
  except Exception as e:
    print('FAIL',type(e).__name__,e)
asyncio.run(r())
"
echo "== uvicorn 完整日志 (最近12行) =="
journalctl -u auth-server --no-pager -n 12 2>&1 | tail -12