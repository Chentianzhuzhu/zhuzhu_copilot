"""验证分片并发推送：向大量（模拟）用户推送时不阻塞、无丢失。

用 _chunked_push 对 500 个（含大量不在线）用户推送，测量耗时并断言无异常返回。
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
import asyncio, sys, time, os
sys.path.insert(0, "/www/auth-server")
from app.api.admin import _chunked_push, PUSH_CHUNK_SIZE
from app.services.ws_manager import ws_manager

print("PUSH_CHUNK_SIZE =", PUSH_CHUNK_SIZE)


async def main():
    # 500 个用户 ID（绝大多数不在线；send_to_user 对无连接用户立即返回）
    ids = list(range(1, 501))
    t0 = time.perf_counter()
    pushed = await _chunked_push(ids, lambda uid: {"type": "points_update",
                                                    "data": {"points": uid}})
    dt = (time.perf_counter() - t0) * 1000
    print(f"push 500 users: {dt:.1f} ms, pushed={pushed}")
    assert pushed == 500, f"expected 500 no-exception results, got {pushed}"

    # 并发下事件循环仍可用：并行跑一个计时协程
    ticks = {"n": 0}
    async def ticker():
        while True:
            ticks["n"] += 1
            await asyncio.sleep(0.005)
    t = asyncio.create_task(ticker())
    t0 = time.perf_counter()
    await _chunked_push(list(range(1, 2001)), lambda uid: {"type": "ping"})
    dt2 = (time.perf_counter() - t0) * 1000
    t.cancel()
    print(f"push 2000 users: {dt2:.1f} ms, ticks while pushing={ticks['n']} (事件循环未被阻塞)")
    assert ticks["n"] > 0, "event loop starved during chunked push"
    print("CHUNKED_PUSH: PASS")
    sys.stdout.flush()
    os._exit(0)


asyncio.run(main())
'''

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username=USER, password=PASS,
          timeout=30, allow_agent=False, look_for_keys=False)
sftp = c.open_sftp()
with sftp.open("/tmp/verify_chunk.py", "w") as f:
    f.write(S)
sftp.close()
stdin, stdout, stderr = c.exec_command(
    "cd /www/auth-server && .venv/bin/python /tmp/verify_chunk.py", timeout=120)
print(stdout.read().decode("utf-8", "replace"))
e = stderr.read().decode("utf-8", "replace")
if e.strip():
    print("---- stderr ----"); print(e[:2000])
c.close()
