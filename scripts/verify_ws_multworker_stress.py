"""压力验证：多 worker 下反复推送，公告/积分/会员必须「每次都到达」。

在服务器本机跑：为测试用户建立 1 条真实 WS 连接（它会被随机分配到某个 worker），
然后连续 N 次触发管理端推送（每次 HTTP 请求也可能落到不同 worker），
断言**每一次**都收到。修复前（内存连接表、无 Redis 中转）会大量丢失。
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
    from app.redis_client import get_redis
    from app.database import AsyncSessionLocal
    from sqlalchemy import text

    async with AsyncSessionLocal() as db:
        row = (await db.execute(text(
            "SELECT id, username, points FROM users WHERE username='zhuzhu'"))).fetchone()
    uid, uname, pts0 = int(row[0]), row[1], int(row[2] or 0)

    token = create_jwt(uid, uname)
    jti = decode_jwt(token)["jti"]
    redis = await get_redis()
    await session_store.create_session(redis, jti,
        {"user_id": uid, "type": "user", "username": uname})

    st, body = post("/api/admin/login", {"username": "zhutianlaing", "password": "windows10"})
    atok = ((body.get("data") or {}).get("token")) if isinstance(body, dict) else ""
    if not atok:
        print("!! admin login 失败"); return

    ROUNDS = 20
    received = []
    stop = {"v": False}

    async def listener():
        url = f"ws://127.0.0.1:8000/ws?token={token}"
        async with websockets.connect(url, open_timeout=10) as ws:
            print("   <= WS 已连接")
            while not stop["v"]:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=1)
                except asyncio.TimeoutError:
                    continue
                msg = json.loads(raw)
                if msg.get("type") == "announcement":
                    received.append((msg.get("data") or {}).get("content"))

    task = asyncio.create_task(listener())
    await asyncio.sleep(2.0)

    # 连续推送 N 次唯一内容；每次间隔很短，最大化「HTTP 与 WS 落在不同 worker」的概率
    expect = []
    for i in range(ROUNDS):
        txt = f"压测公告#{i}"
        expect.append(txt)
        st, body = post("/api/admin/announcement", {"content": txt, "enabled": True}, atok)
        await asyncio.sleep(0.15)

    # 等待收齐
    deadline = asyncio.get_event_loop().time() + 8
    while len(received) < ROUNDS and asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.2)

    stop["v"] = True
    try:
        await asyncio.wait_for(task, timeout=4)
    except Exception:
        pass

    got = set(received)
    missing = [e for e in expect if e not in got]
    print(f"[*] 发送 {ROUNDS} 条，收到 {len(set(received))} 条（去重）")
    if missing:
        print(f"    !! 丢失 {len(missing)} 条: {missing[:5]}")
    ok = not missing
    print("WS_MULTIWORKER_STRESS:", "PASS" if ok else "FAIL")

    # 恢复
    post("/api/admin/announcement", {"content": ""}, atok)
    await session_store.revoke_session(redis, jti)
    sys.stdout.flush()
    os._exit(0 if ok else 1)


asyncio.run(main())
'''

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username=USER, password=PASS,
          timeout=30, allow_agent=False, look_for_keys=False)
sftp = c.open_sftp()
with sftp.open("/tmp/verify_ws_stress.py", "w") as f:
    f.write(S)
sftp.close()
stdin, stdout, stderr = c.exec_command(
    "cd /www/auth-server && .venv/bin/python /tmp/verify_ws_stress.py", timeout=240)
print(stdout.read().decode("utf-8", "replace"))
e = stderr.read().decode("utf-8", "replace")
if e.strip():
    print("---- stderr ----")
    print(e[:3000])
c.close()
