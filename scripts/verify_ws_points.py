"""WS 实时积分推送端到端验证（服务端本机执行）。

流程：
1) 用服务端 create_jwt 为 zhuzhu 签发 token，并在 Redis 建立合法会话
2) 以 ws://127.0.0.1:8000/ws?token=<token> 建立真实 WebSocket 连接
3) 管理员调用 /api/admin/users/{id}/points 调整 +1
4) 观察 WS 是否实时收到 points_update，数值是否等于“原积分+1”
5) 再把积分 -1 恢复原状，并撤销测试会话
"""
import os
import paramiko

# 服务器凭据只从环境变量读取（不写进仓库）：
#   REMOTE_HOST（默认 39.104.28.191）/ REMOTE_USER（默认 root）/ REMOTE_PASS
HOST = os.environ.get("REMOTE_HOST", "39.104.28.191")
USER = os.environ.get("REMOTE_USER", "root")
PASS = os.environ.get("REMOTE_PASS", "")
if not PASS:
    raise SystemExit("[FAIL] 请先设置环境变量 REMOTE_PASS（服务器 SSH 口令）")

S = r'''
import asyncio, json, sys, urllib.request, urllib.error, os
sys.path.insert(0, "/www/auth-server")

BASE = "http://127.0.0.1:8000"


def post(path, payload, token=None):
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = "Bearer " + token
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode(),
                                 headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        b = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(b)
        except Exception:
            return e.code, b


async def main():
    import websockets
    from app.core.security import create_jwt
    from app.core import session as session_store
    from app.database import AsyncSessionLocal
    from app.redis_client import get_redis
    from sqlalchemy import text
    from app.core.security import decode_jwt

    async with AsyncSessionLocal() as db:
        row = (await db.execute(text(
            "SELECT id, username, points FROM users WHERE username='zhuzhu'"))).fetchone()
    if not row:
        print("!! 找不到 zhuzhu")
        return
    uid, uname, pts0 = int(row[0]), row[1], int(row[2] or 0)
    print(f"[0] user id={uid} username={uname} points={pts0}")

    token = create_jwt(uid, uname)
    payload = decode_jwt(token)
    jti = payload["jti"]
    redis = await get_redis()
    await session_store.create_session(redis, jti, {
        "user_id": uid, "type": "user", "username": uname})
    print("[1] token+session 建立: jti=", jti[:8], "...")

    st, body = post("/api/admin/login", {"username": "zhutianlaing", "password": "windows10"})
    atok = ((body.get("data") or {}).get("token")
            or (body.get("data") or {}).get("access_token") or "") if isinstance(body, dict) else ""
    print("[2] admin login:", st, bool(atok))
    if not atok:
        return

    got = {}

    async def listener():
        url = f"ws://127.0.0.1:8000/ws?token={token}"
        async with websockets.connect(url, open_timeout=10) as ws:
            got["connected"] = True
            print("   <= WS 已连接")
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=15)
                msg = json.loads(raw)
                print("   <= WS:", msg)
                if msg.get("type") == "points_update":
                    got["points"] = (msg.get("data") or {}).get("points")
                    got["ok"] = True
                    return

    task = asyncio.create_task(listener())
    await asyncio.sleep(3.0)  # 等连接建立
    print("[3] connected =", got.get("connected"))

    st, body = post(f"/api/admin/users/{uid}/points",
                    {"amount": 1, "reason": "ws_verify"}, atok)
    print("[4] adjust +1:", st, body)

    try:
        await asyncio.wait_for(task, timeout=25)
    except asyncio.TimeoutError:
        print("!! 超时未收到 points_update")

    if got.get("ok"):
        ok = int(got["points"]) == pts0 + 1
        print(f"[5] 实时推送 points={got.get('points')} 期望={pts0 + 1} -> {'PASS' if ok else 'FAIL'}")
    else:
        print("[5] 未收到实时推送 -> FAIL")

    st, body = post(f"/api/admin/users/{uid}/points",
                    {"amount": -1, "reason": "ws_verify_restore"}, atok)
    print("[6] 恢复 -1:", st, body.get("data") if isinstance(body, dict) else body)
    await session_store.revoke_session(redis, jti)
    print("[7] 测试会话已撤销")
    sys.stdout.flush()
    os._exit(0)


asyncio.run(main())
'''

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username=USER, password=PASS,
          timeout=30, allow_agent=False, look_for_keys=False)
sftp = c.open_sftp()
with sftp.open("/tmp/_wsv.py", "w") as f:
    f.write(S)
sftp.close()
_, o, e = c.exec_command(
    "set -a; . /www/auth-server/.env; set +a; cd /www/auth-server && "
    "/www/auth-server/.venv/bin/python /tmp/_wsv.py 2>&1; rm -f /tmp/_wsv.py", timeout=120)
print(o.read().decode("utf-8", "replace"))
ee = e.read().decode("utf-8", "replace")
if ee.strip():
    print("STDERR:", ee[-1500:])
c.close()
