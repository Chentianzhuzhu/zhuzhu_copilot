"""本地验证：WS 跨 worker（多进程）推送经 Redis pub/sub 中转后可达。

不依赖真实 Redis / 网络：用一个内存 pub/sub 假 Redis 驱动两个 WSManager 实例
（模拟 uvicorn --workers 2 的两个独立进程），断言「worker A 发起的推送」
能到达「挂在 worker B 的连接」。同时覆盖降级路径（无 Redis 时本地直投）。
"""
import asyncio
import json
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "auth-server"))


# ---------- 内存版假 Redis（够用即可） ----------
class FakePubSub:
    def __init__(self, bus, channels):
        self._bus = bus
        self._channels = channels
        self._queue = asyncio.Queue()

    async def subscribe(self, channel):
        self._bus.setdefault(channel, []).append(self._queue)

    async def listen(self):
        while True:
            item = await self._queue.get()
            yield {"type": "message", "data": item}

    async def close(self):
        for ch, subs in self._bus.items():
            if self._queue in subs:
                subs.remove(self._queue)


class FakeRedis:
    """极简 Redis：只实现 ws_manager 用到的 publish/pubsub/sadd/srem/expire/
    scard/scan_iter/pipeline。"""

    def __init__(self):
        self.bus = {}
        self.sets = {}
        self.ttls = {}

    def pubsub(self):
        return FakePubSub(self.bus, None)

    async def publish(self, channel, data):
        for q in list(self.bus.get(channel, [])):
            await q.put(data)
        return len(self.bus.get(channel, []))

    async def sadd(self, key, member):
        self.sets.setdefault(key, set()).add(member)

    async def srem(self, key, member):
        self.sets.get(key, set()).discard(member)

    async def expire(self, key, ttl):
        self.ttls[key] = ttl

    async def scard(self, key):
        return len(self.sets.get(key, set()))

    async def scan_iter(self, match=None, count=None):
        prefix = (match or "").rstrip("*")
        for k in list(self.sets.keys()):
            if k.startswith(prefix):
                yield k

    def pipeline(self):
        return FakePipeline(self)


class FakePipeline:
    def __init__(self, r):
        self._r = r
        self._keys = []

    def scard(self, key):
        self._keys.append(key)

    async def execute(self):
        return [len(self._r.sets.get(k, set())) for k in self._keys]


class FakeWS:
    """记录收到的文本，模拟一条 WebSocket 连接。"""

    def __init__(self, name):
        self.name = name
        self.sent = []

    async def accept(self):
        pass

    async def send_text(self, text):
        self.sent.append(json.loads(text))

    def messages(self):
        return self.sent


def install_fake_redis_module(redis_obj):
    """把 app.redis_client 替换为内存假实现，供订阅端 make_pubsub_client 使用。"""
    pkg = sys.modules.get("app")
    if pkg is None:
        pkg = types.ModuleType("app")
        pkg.__path__ = []
        sys.modules["app"] = pkg
    rc = types.ModuleType("app.redis_client")

    async def get_redis():
        return redis_obj

    def make_pubsub_client():
        return redis_obj

    rc.get_redis = get_redis
    rc.make_pubsub_client = make_pubsub_client
    rc.redis = redis_obj
    sys.modules["app.redis_client"] = rc
    setattr(pkg, "redis_client", rc)


def load_manager(redis_obj, force_none=False):
    # 每个「worker」拥有独立的模块命名空间（模拟独立进程）
    name = f"ws_manager_worker_{id(redis_obj)}_{force_none}"
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    src = (ROOT / "auth-server/app/services/ws_manager.py").read_text(encoding="utf-8")
    exec(compile(src, name, "exec"), mod.__dict__)
    m = mod.WSManager()
    # 注入假 Redis（跳过 app.redis_client 导入）；force_none 表示模拟无 Redis
    m._redis = None if force_none else redis_obj
    if force_none:
        async def _no_redis():
            return None
        m._get_redis = _no_redis
    return m


async def scenario_cross_worker():
    bus_redis = FakeRedis()
    install_fake_redis_module(bus_redis)
    A = load_manager(bus_redis)
    B = load_manager(bus_redis)

    # worker B 持有 user 7 与 user 9 的连接；worker A 无连接
    ws7, ws9 = FakeWS("7"), FakeWS("9")
    await B.connect(7, ws7)
    await B.connect(9, ws9)

    # 两个 worker 都启动订阅端
    await A.start_subscriber()
    await B.start_subscriber()
    await asyncio.sleep(0.05)

    # worker A 发起推送（模拟管理端 HTTP 落在 A）
    await A.send_to_user(7, {"type": "announcement",
                             "data": {"content": "跨 worker 公告", "enabled": True}})
    await A.send_to_user(9, {"type": "membership_update",
                             "data": {"membership_type": "max"}})
    await A.broadcast({"type": "ping_all"})
    await asyncio.sleep(0.15)

    ok = True
    if not (ws7.messages() and ws7.messages()[0]["data"]["content"] == "跨 worker 公告"):
        print("  [x] worker A 推送的 announcement 未到达挂在 B 的 user7")
        ok = False
    if not (ws9.messages() and ws9.messages()[0]["type"] == "membership_update"):
        print("  [x] worker A 推送的 membership 未到达挂在 B 的 user9")
        ok = False
    if len(ws7.messages()) < 2 or ws7.messages()[-1]["type"] != "ping_all":
        print("  [x] broadcast 未到达 B 的连接")
        ok = False

    # 跨 worker 在线统计
    online = await A.count_online([7, 9, 999])
    if online != 2:
        print(f"  [x] count_online 期望 2，实际 {online}")
        ok = False
    total = await A.total_online_connections()
    if total != 2:
        print(f"  [x] total_online_connections 期望 2，实际 {total}")
        ok = False

    await A.stop_subscriber()
    await B.stop_subscriber()
    return ok


async def scenario_degrade_no_redis():
    """无 Redis：降级为本地直投，单 worker 场景仍可用。"""
    A = load_manager(None, force_none=True)
    ws = FakeWS("5")
    await A.connect(5, ws)
    await A.send_to_user(5, {"type": "points_update", "data": {"points": 42}})
    await asyncio.sleep(0.02)
    ok = bool(ws.messages()) and ws.messages()[0]["data"]["points"] == 42
    if not ok:
        print("  [x] 无 Redis 降级直投失败")
    return ok


async def _none():
    return None


async def main():
    print("[1] 跨 worker（Redis pub/sub 中转）…")
    ok1 = await scenario_cross_worker()
    print("[2] 无 Redis 降级（本地直投）…")
    ok2 = await scenario_degrade_no_redis()
    print("\nWS_MULTIWORKER_RELAY:", "PASS" if (ok1 and ok2) else "FAIL")
    return 0 if (ok1 and ok2) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
