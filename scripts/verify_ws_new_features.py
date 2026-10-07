"""WS 实时推送验证：公告 / 批量积分 / 批量会员（服务端本机执行）。

用服务端 create_jwt 为 zhuzhu 签发 token 并在 Redis 建立合法会话，
建立真实 WebSocket，然后依次触发三类管理员批量操作，断言实时收到。
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
    from app.core.security import create_jwt, decode_jwt
    from app.core import session as session_store
    from app.database import AsyncSessionLocal
    from app.redis_client import get_redis
    from sqlalchemy import text

    async with AsyncSessionLocal() as db:
        row = (await db.execute(text(
            "SELECT id, username, points, membership_type FROM users WHERE username='zhuzhu'"))).fetchone()
    if not row:
        print("!! 找不到 zhuzhu"); return
    uid, uname, pts0, mt0 = int(row[0]), row[1], int(row[2] or 0), row[3]
    print(f"[0] uid={uid} points={pts0} membership={mt0}")

    token = create_jwt(uid, uname)
    jti = decode_jwt(token)["jti"]
    redis = await get_redis()
    await session_store.create_session(redis, jti,
        {"user_id": uid, "type": "user", "username": uname})

    st, body = post("/api/admin/login", {"username": "zhutianlaing", "password": "windows10"})
    atok = ((body.get("data") or {}).get("token")) if isinstance(body, dict) else ""
    print("[1] admin login:", st, bool(atok))
    if not atok:
        return

    got = {}

    async def listener():
        url = f"ws://127.0.0.1:8000/ws?token={token}"
        async with websockets.connect(url, open_timeout=10) as ws:
            got["connected"] = True
            print("   <= WS 已连接")
            end = asyncio.get_event_loop().time() + 14
            while asyncio.get_event_loop().time() < end:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=2)
                except asyncio.TimeoutError:
                    continue
                msg = json.loads(raw)
                t = msg.get("type")
                print("   <= WS:", t, json.dumps(msg.get("data"), ensure_ascii=False)[:70])
                got[t] = msg.get("data")
                if {"announcement", "points_update", "membership_update"} <= set(got):
                    return

    task = asyncio.create_task(listener())
    await asyncio.sleep(2.5)
    print("[2] connected =", got.get("connected"))

    st, body = post("/api/admin/announcement",
                    {"content": "WS 推送验证公告", "enabled": True}, atok)
    print("[3] set announcement:", st, (body.get("data") or {}).get("pushed"))
    await asyncio.sleep(1.0)

    st, body = post("/api/admin/users/batch/points",
                    {"ids": [uid], "mode": "delta", "amount": 3}, atok)
    print("[4] batch points +3:", st, (body.get("data") or {}).get("pushed"))
    await asyncio.sleep(1.0)

    st, body = post("/api/admin/users/batch/membership",
                    {"ids": [uid], "membership_type": "max"}, atok)
    print("[5] batch membership:", st, (body.get("data") or {}).get("pushed"))

    try:
        await asyncio.wait_for(task, timeout=16)
    except asyncio.TimeoutError:
        pass

    need = {"announcement", "points_update", "membership_update"}
    have = set(got)
    print(f"[6] received: {sorted(have)}")
    ok = need.issubset(have)
    if ok:
        pu = got["points_update"] or {}
        mu = got["membership_update"] or {}
        ann = got["announcement"] or {}
        print(f"    points_update.points={pu.get('points')} (期望 {pts0+3})")
        print(f"    membership_update.type={mu.get('membership_type')} (期望 max)")
        print(f"    announcement.content={ann.get('content')!r}")
        ok = (int(pu.get("points", -1)) == pts0 + 3
              and mu.get("membership_type") == "max"
              and "WS 推送验证公告" == ann.get("content"))
    print("WS_PUSH_ALL:", "PASS" if ok else "FAIL")

    # 恢复
    post("/api/admin/users/batch/membership", {"ids": [uid], "membership_type": mt0 or "free"}, atok)
    post("/api/admin/users/batch/points", {"ids": [uid], "mode": "set", "amount": pts0}, atok)
    post("/api/admin/announcement", {"content": ""}, atok)
    await session_store.revoke_session(redis, jti)
    print("[7] restored & session revoked")
    sys.stdout.flush()
    os._exit(0 if ok else 1)


asyncio.run(main())
'''

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username=USER, password=PASS,
          timeout=30, allow_agent=False, look_for_keys=False)
sftp = c.open_sftp()
with sftp.open("/tmp/verify_ws_new.py", "w") as f:
    f.write(S)
sftp.close()
stdin, stdout, stderr = c.exec_command(
    "cd /www/auth-server && .venv/bin/python /tmp/verify_ws_new.py", timeout=180)
print(stdout.read().decode("utf-8", "replace"))
e = stderr.read().decode("utf-8", "replace")
if e.strip():
    print("---- stderr ----")
    print(e[:3000])
c.close()
