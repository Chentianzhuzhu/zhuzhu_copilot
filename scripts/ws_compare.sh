#!/bin/bash
set -e
cd /www/auth-server
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/api/auth/login -H 'Content-Type: application/json' -d '{"username":"wstest","password":"WsPass99"}' | /www/auth-server/.venv/bin/python -c "import sys,json;print(json.load(sys.stdin)['data']['token'])" 2>/dev/null)
echo "token len=${#TOKEN}"
echo "=== 原站直连 WS（应成功） ==="
/www/auth-server/.venv/bin/python -c "
import asyncio,json,websockets
async def r():
  async with websockets.connect('ws://127.0.0.1:8000/ws?token=$TOKEN',open_timeout=8) as ws:
    await ws.send(json.dumps({'type':'ping'})); print('DIRECT PONG',await asyncio.wait_for(ws.recv(),5))
asyncio.run(r())
"
echo "=== 经域名 /authapp/ws（Host=chendian） ==="
/www/auth-server/.venv/bin/python -c "
import asyncio,json,websockets
async def r():
  try:
    async with websockets.connect('ws://127.0.0.1/authapp/ws?token=$TOKEN',open_timeout=8,additional_headers={'Host':'chentian.dpdns.org'}) as ws:
      await ws.send(json.dumps({'type':'ping'})); print('AUTHAPP PONG',await asyncio.wait_for(ws.recv(),5))
  except Exception as e:
    print('AUTHAPP FAIL',type(e).__name__,e)
asyncio.run(r())
"
echo "=== auth-server 最近日志 ==="
journalctl -u auth-server --no-pager -n 8 2>&1 | grep -iE 'WebSocket|/ws' | tail -6