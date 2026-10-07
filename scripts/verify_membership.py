"""验证会员类型修改接口 + WS 实时推送（服务端本机执行）。

1) 为用户 zhuzhu 签发 token + 建 Redis 会话，连 ws
2) 管理员 POST /api/admin/users/14/membership 改为 pro（带到期日）
3) 观察 WS 是否实时收到 membership_update，字段是否一致
4) /api/auth/me 复核 DB 已更新
5) 恢复原值（free / 清空），并撤销测试会话
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
import asyncio, json, sys, os, urllib.request, urllib.error
sys.path.insert(0, "/www/auth-server")
BASE = "http://127.0.0.1:8000"


def req(method, path, payload=None, token=None):
    h = {}
    if token:
        h["Authorization"] = "Bearer " + token
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        h["Content-Type"] = "application/json"
    r = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode())
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
            "SELECT id, username, membership_type, membership_expire "
            "FROM users WHERE username='zhuzhu'"))).fetchone()
    uid, uname, mt0, ex0 = int(row[0]), row[1], row[2], row[3]
    print(f"[0] id={uid} type={mt0} expire={ex0}")

    token = create_jwt(uid, uname)
    jti = decode_jwt(token)["jti"]
    redis = await get_redis()
    await session_store.create_session(redis, jti,
        {"user_id": uid, "type": "user", "username": uname})

    st, body = req("POST", "/api/admin/login",
                   {"username": "zhutianlaing", "password": "windows10"})
    atok = ((body.get("data") or {}).get("token") or "") if isinstance(body, dict) else ""
    print("[1] admin login:", st, bool(atok))
    if not atok:
        return

    got = {}

    async def listener():
        async with websockets.connect(f"ws://127.0.0.1:8000/ws?token={token}",
                                      open_timeout=10) as ws:
            got["connected"] = True
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=15)
                msg = json.loads(raw)
                print("   <= WS:", msg)
                if msg.get("type") == "membership_update":
                    got.update(msg.get("data") or {})
                    return

    task = asyncio.create_task(listener())
    await asyncio.sleep(3.0)
    print("[2] connected =", got.get("connected"))

    st, body = req("POST", f"/api/admin/users/{uid}/membership",
                   {"membership_type": "pro", "membership_expire": "2027-01-31"}, atok)
    print("[3] set pro:", st, body)

    try:
        await asyncio.wait_for(task, timeout=25)
    except asyncio.TimeoutError:
        print("!! 超时未收到 membership_update")

    ok = got.get("membership_type") == "pro" and got.get("membership_expire") == "2027-01-31"
    print(f"[4] 实时推送 type={got.get('membership_type')} expire={got.get('membership_expire')} -> {'PASS' if ok else 'FAIL'}")

    # 校验 DB
    async with AsyncSessionLocal() as db:
        r2 = (await db.execute(text(
            "SELECT membership_type, membership_expire FROM users WHERE id=:i"),
            {"i": uid})).fetchone()
    print("[5] DB 复核:", r2[0], r2[1])
    # 校验 /me 返回
    st, me = req("GET", "/api/auth/me", token=token)
    print("[6] /me:", st, {k: (me.get("data") or {}).get(k) for k in ("membership_type", "membership_expire")} if isinstance(me, dict) else me)

    # 非法类型 → 400
    st, body = req("POST", f"/api/admin/users/{uid}/membership",
                   {"membership_type": "vip"}, atok)
    print("[7] 非法类型:", st, body)

    # 恢复
    st, body = req("POST", f"/api/admin/users/{uid}/membership",
                   {"membership_type": str(mt0 or "free"), "membership_expire": ""}, atok)
    print("[8] 恢复:", st, body)
    await session_store.revoke_session(redis, jti)
    sys.stdout.flush()
    os._exit(0)


asyncio.run(main())
'''

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username=USER, password=PASS,
          timeout=30, allow_agent=False, look_for_keys=False)
sftp = c.open_sftp()
with sftp.open("/tmp/_ms.py", "w") as f:
    f.write(S)
sftp.close()
_, o, e = c.exec_command(
    "set -a; . /www/auth-server/.env; set +a; cd /www/auth-server && "
    "/www/auth-server/.venv/bin/python /tmp/_ms.py 2>&1; rm -f /tmp/_ms.py", timeout=120)
print(o.read().decode("utf-8", "replace"))
ee = e.read().decode("utf-8", "replace")
if ee.strip():
    print("STDERR:", ee[-1200:])
c.close()
