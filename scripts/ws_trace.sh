#!/bin/bash
set -e
cd /www/auth-server
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/api/auth/login -H 'Content-Type: application/json' -d '{"username":"wstest","password":"WsPass99"}' | /www/auth-server/.venv/bin/python -c "import sys,json;print(json.load(sys.stdin)['data']['token'])" 2>/dev/null)
echo "token len=${#TOKEN}  time=$(date +%T)"
# 通过域名块 /authapp/ws 发起 WS（Host=域）
/www/auth-server/.venv/bin/python -c "
import asyncio,json,websockets
async def r():
  try:
    async with websockets.connect('ws://127.0.0.1/authapp/ws?token=$TOKEN',open_timeout=8,additional_headers={'Host':'chentian.dpdns.org'}) as ws:
      await ws.send(json.dumps({'type':'ping'})); print('RESULT PONG',await asyncio.wait_for(ws.recv(),4))
  except Exception as e:
    print('RESULT FAIL',type(e).__name__,e)
asyncio.run(r())
"
sleep 1
echo "===== auth-server journal ====="
journalctl -u auth-server --no-pager --since '20 seconds ago' 2>&1 | grep -iE 'ws|WebSocket|404|400' | tail -6
echo "===== nginx access (last domain /authapp/ws) ====="
grep 'authapp/ws' /var/log/nginx/access.log | tail -3