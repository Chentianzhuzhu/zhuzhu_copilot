#!/bin/bash
# 创建固定的 WS 测试用户并用其验证 WebSocket（含公网 Nginx 路径）
set -e
cd /www/auth-server
TOKEN=$(.venv/bin/python - <<'PY'
import asyncio, json, urllib.request, urllib.error, bcrypt, sys

# 1) 确保存在测试用户 wstest / WsPass99
HASH = bcrypt.hashpw(b'WsPass99', bcrypt.gensalt(12)).decode()
print(HASH, file=sys.stderr)
PY
)
echo "hash generated"

# 用 SQL 创建/更新测试用户（python 直接通过 mysql 太绕，直接用 python + pymysql）
/www/auth-server/.venv/bin/python - <<'PY'
import bcrypt, subprocess
h = bcrypt.hashpw(b'WsPass99', bcrypt.gensalt(12)).decode()
sql = f"USE zhuzhu_copilot_auth; INSERT INTO users (username,password_hash,security_question,security_answer,status) VALUES ('wstest','{h}','q','a','active') ON DUPLICATE KEY UPDATE password_hash=VALUES(password_hash), status='active';"
p = subprocess.run(["mysql","-uroot","-pwindows10","-e",sql], capture_output=True, text=True)
print("sql rc", p.returncode, p.stderr)
PY

# 2) 登录拿 token
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/api/auth/login -H 'Content-Type: application/json' -d '{"username":"wstest","password":"WsPass99"}' | /www/auth-server/.venv/bin/python -c "import sys,json; print(json.load(sys.stdin)['data']['token'])")
echo "token len: ${#TOKEN}"

# 3) 本地 WS
echo "--- 本地 WS ---"
/www/auth-server/.venv/bin/python /tmp/ws_test.py "$TOKEN" "ws://127.0.0.1:8000"

# 4) 经 Nginx 公网路径（err/no-encrypted, use ws:// IP）
echo "--- 经 Nginx WS ---"
/www/auth-server/.venv/bin/python /tmp/ws_test.py "$TOKEN" "ws://39.104.28.191"

# 5) 错误 token 应被拒
echo "--- 错误 token ---"
/www/auth-server/.venv/bin/python /tmp/ws_test.py "bad.token.here" "ws://127.0.0.1:8000" || true