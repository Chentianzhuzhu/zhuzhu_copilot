"""验证订单删除/批量删除接口（服务端本机执行）。

1) 管理员登录
2) 插一条临时订单（直接写库），调 DELETE /api/admin/orders/{id} 删除
3) 再插两条，调 POST /api/admin/orders/batch 批量删除
4) 校验：不存在订单 → 404 / skipped
5) 清场
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
    from app.database import AsyncSessionLocal
    from sqlalchemy import text

    st, body = req("POST", "/api/admin/login",
                   {"username": "zhutianlaing", "password": "windows10"})
    atok = ((body.get("data") or {}).get("token") or "") if isinstance(body, dict) else ""
    print("[1] admin login:", st, bool(atok))
    if not atok:
        return

    # 插入 3 条临时订单
    ids = ["ORDTESTDEL001", "ORDTESTDEL002", "ORDTESTDEL003"]
    async with AsyncSessionLocal() as db:
        for oid in ids:
            await db.execute(text(
                "INSERT INTO orders (id,user_id,order_type,product_id,amount,points_amount,pay_method,status) "
                "VALUES (:i,14,'points','p_test',1.00,100,'alipay','pending')"), {"i": oid})
        await db.commit()
    print("[2] 插入临时订单:", ids)

    # 单个删除
    st, body = req("DELETE", "/api/admin/orders/" + ids[0], token=atok)
    print("[3] DELETE 单条:", st, body)

    # 确认已删
    async with AsyncSessionLocal() as db:
        n = (await db.execute(text(
            "SELECT COUNT(*) FROM orders WHERE id=:i"), {"i": ids[0]})).scalar()
    print("    DB 剩余该订单:", n)

    # 批量删除剩余两条 + 一条不存在的（应 skipped）
    st, body = req("POST", "/api/admin/orders/batch",
                   {"ids": [ids[1], ids[2], "ORD_NOT_EXIST_XX"]}, atok)
    print("[4] POST batch:", st, body)

    # 确认清场
    async with AsyncSessionLocal() as db:
        left = (await db.execute(text(
            "SELECT id FROM orders WHERE id IN :ids"), {"ids": tuple(ids)})).fetchall()
    print("[5] 剩余临时订单:", [r[0] for r in left])

    # 边界：空 ids → 400；不存在单条 → 404
    st, body = req("POST", "/api/admin/orders/batch", {"ids": []}, atok)
    print("[6] batch 空 ids:", st, body)
    st, body = req("DELETE", "/api/admin/orders/ORD_NOT_EXIST_XX", token=atok)
    print("[7] DELETE 不存在:", st, body)

    print("RESULT:", "PASS" if (
        n == 0 and st == 404) else "CHECK")
    sys.stdout.flush()
    os._exit(0)


asyncio.run(main())
'''

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username=USER, password=PASS,
          timeout=30, allow_agent=False, look_for_keys=False)
sftp = c.open_sftp()
with sftp.open("/tmp/_od.py", "w") as f:
    f.write(S)
sftp.close()
_, o, e = c.exec_command(
    "set -a; . /www/auth-server/.env; set +a; cd /www/auth-server && "
    "/www/auth-server/.venv/bin/python /tmp/_od.py 2>&1; rm -f /tmp/_od.py", timeout=120)
print(o.read().decode("utf-8", "replace"))
ee = e.read().decode("utf-8", "replace")
if ee.strip():
    print("STDERR:", ee[-1200:])
c.close()
