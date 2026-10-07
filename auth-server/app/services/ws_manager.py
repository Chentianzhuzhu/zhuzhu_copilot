"""WebSocket 连接管理：维护 user_id -> connections 映射，支持点对点推送。

多进程说明（重要）
------------------
线上以 ``uvicorn --workers N`` 运行，每个 worker 是**独立进程、独立内存**，
因此 ``active_connections`` 只包含「本进程」持有的连接。若管理员推送请求
落在 worker A，而目标用户的 WS 连接挂在 worker B，则本地查找为空 →
推送会被**静默丢弃**（表现为「前端偶尔收不到 / 有时推不出去」）。

解决：所有跨用户推送（``send_to_user`` / ``broadcast``）统一改为
**经 Redis pub/sub 中转**：

    worker A (HTTP)  --PUBLISH-->  redis 频道  --SUBSCRIBE-->  每个 worker
                                                              └─ 投递给本进程持有的连接

订阅端在应用启动时拉起（见 ``start_subscriber``），逐条在本地投递，
既保留「按 user_id 精确投递」，也天然支持多 worker 横向扩展。
"""
import asyncio
import json
import logging
import os
import uuid
from typing import Any, Dict, List

from fastapi import WebSocket

logger = logging.getLogger(__name__)

# 本进程唯一标识：写入在线集合，便于断开时精确移除自己的标记
WORKER_ID = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"

# Redis 广播频道：所有 WS 消息经此中转，保证跨 worker 可达
WS_CHANNEL = "zhuzhu:ws:broadcast"
# 在线状态的 Redis 键前缀：每个持有该用户连接的 worker 用「集合」记录自己，
# 便于跨 worker 统计「该用户是否在线」（用于推送计数与可观测性）。
ONLINE_KEY_PREFIX = "zhuzhu:ws:online:"
# 在线标记过期时间（秒）：连接断开时主动删除，异常退出则由 TTL 兜底。
ONLINE_TTL = 120


class WSManager:
    """WebSocket 连接管理器（单进程内存 + Redis 跨进程中转）"""

    def __init__(self):
        # user_id -> [WebSocket, ...]（仅本进程持有的连接）
        self.active_connections: Dict[int, List[WebSocket]] = {}
        self._lock = asyncio.Lock()
        # pub/sub 订阅任务句柄（每 worker 一个）
        self._sub_task: asyncio.Task | None = None
        self._redis: Any = None

    # ---------- 连接生命周期 ----------
    async def _get_redis(self):
        """懒加载全局 Redis 客户端（避免 import 期循环依赖）。

        已注入实例（含测试替身）时直接复用，不再重复导入。
        """
        if self._redis is not None:
            return self._redis
        try:
            from app.redis_client import redis as r
            self._redis = r
        except Exception:
            self._redis = None
        return self._redis

    async def _mark_online(self, user_id: int) -> None:
        """在 Redis 记录「本 worker 持有该用户连接」，用于跨 worker 在线统计。"""
        r = await self._get_redis()
        if r is None:
            return
        try:
            key = f"{ONLINE_KEY_PREFIX}{user_id}"
            await r.sadd(key, WORKER_ID)
            await r.expire(key, ONLINE_TTL)
        except Exception:
            pass

    async def _unmark_online(self, user_id: int) -> None:
        """本 worker 已无该用户连接 → 从在线集合移除自己。"""
        r = await self._get_redis()
        if r is None:
            return
        try:
            key = f"{ONLINE_KEY_PREFIX}{user_id}"
            await r.srem(key, WORKER_ID)
        except Exception:
            pass

    async def _refresh_online_ttl(self, user_id: int) -> None:
        """续期在线标记（心跳时调用，异常退出的连接由 TTL 自动过期）。"""
        r = await self._get_redis()
        if r is None:
            return
        try:
            await r.expire(f"{ONLINE_KEY_PREFIX}{user_id}", ONLINE_TTL)
        except Exception:
            pass

    async def refresh_online(self, user_id: int) -> None:
        """对外接口：心跳续期在线标记（供 WS 端点调用）。"""
        await self._refresh_online_ttl(int(user_id))
        await self._mark_online(int(user_id))

    async def is_online(self, user_id: int) -> bool:
        """跨 worker 判断用户是否在线（任一本进程或兄弟 worker 持有连接）。"""
        uid = int(user_id)
        if self.local_connection_count(uid) > 0:
            return True
        r = await self._get_redis()
        if r is None:
            return False
        try:
            return await r.scard(f"{ONLINE_KEY_PREFIX}{uid}") > 0
        except Exception:
            return False

    async def count_online(self, user_ids) -> int:
        """统计给定用户集中「在线」的人数（跨 worker，基于 Redis 集合）。"""
        ids = [int(u) for u in user_ids]
        if not ids:
            return 0
        r = await self._get_redis()
        # 无 Redis：退化为本地统计
        if r is None:
            return sum(1 for u in ids if self.local_connection_count(u) > 0)
        n = 0
        try:
            pipe = r.pipeline()
            for u in ids:
                pipe.scard(f"{ONLINE_KEY_PREFIX}{u}")
            results = await pipe.execute()
            n = sum(1 for c in results if c and int(c) > 0)
        except Exception:
            n = 0
        # 补上本进程持有的、Redis 尚未记录的连接（正常不会发生，防御性兜底）
        if n == 0:
            n = sum(1 for u in ids if self.local_connection_count(u) > 0)
        return n

    async def total_online_connections(self) -> int:
        """跨 worker 统计「当前在线用户数」（以 Redis 在线集合的键数为准）。

        用于后台回显「已推送给 N 位在线用户」。Redis 不可用时退化为本进程连接数。
        """
        r = await self._get_redis()
        if r is not None:
            try:
                n = 0
                async for _key in r.scan_iter(match=f"{ONLINE_KEY_PREFIX}*", count=200):
                    n += 1
                return n
            except Exception:
                pass
        return sum(1 for conns in self.active_connections.values() if conns)

    async def connect(self, user_id: int, websocket: WebSocket):
        """新连接接入"""
        await websocket.accept()
        async with self._lock:
            if user_id not in self.active_connections:
                self.active_connections[user_id] = []
            self.active_connections[user_id].append(websocket)
        await self._mark_online(user_id)
        logger.info(f"WebSocket 用户 {user_id} 连接，当前连接数: {len(self.active_connections)}")

    async def disconnect(self, user_id: int, websocket: WebSocket):
        """连接断开"""
        async with self._lock:
            if user_id in self.active_connections:
                if websocket in self.active_connections[user_id]:
                    self.active_connections[user_id].remove(websocket)
                if not self.active_connections[user_id]:
                    del self.active_connections[user_id]
            still_local = bool(self.active_connections.get(user_id))
        # 本 worker 已无该用户连接 → 清除在线标记
        if not still_local:
            await self._unmark_online(user_id)
        logger.info(f"WebSocket 用户 {user_id} 断开")

    # ---------- 本地投递（只在订阅端调用） ----------
    async def _deliver_local(self, user_id: int, message: dict) -> int:
        """把消息投递给**本进程**持有的该用户连接，返回实际投递的连接数。

        无本地连接时返回 0（不代表失败：连接可能在别的 worker）。
        """
        async with self._lock:
            connections = self.active_connections.get(user_id, [])[:]

        if not connections:
            return 0

        text = json.dumps(message, ensure_ascii=False)
        dead = []
        delivered = 0
        for ws in connections:
            try:
                await ws.send_text(text)
                delivered += 1
            except Exception:
                dead.append(ws)

        # 成功投递即续期在线标记
        if delivered:
            await self._refresh_online_ttl(user_id)

        # 清理死连接
        if dead:
            async with self._lock:
                if user_id in self.active_connections:
                    for ws in dead:
                        if ws in self.active_connections[user_id]:
                            self.active_connections[user_id].remove(ws)
                    if not self.active_connections[user_id]:
                        del self.active_connections[user_id]
            if not self.local_connection_count(user_id):
                await self._unmark_online(user_id)
        return delivered

    async def _deliver_local_all(self, message: dict) -> int:
        """把消息投递给本进程的**全部**连接，返回投递连接数。"""
        async with self._lock:
            all_connections = []
            for conns in self.active_connections.values():
                all_connections.extend(conns)

        if not all_connections:
            return 0

        text = json.dumps(message, ensure_ascii=False)
        delivered = 0
        dead = []
        for ws in all_connections:
            try:
                await ws.send_text(text)
                delivered += 1
            except Exception:
                dead.append(ws)

        if dead:
            async with self._lock:
                for uid, conns in list(self.active_connections.items()):
                    for ws in dead:
                        if ws in conns:
                            conns.remove(ws)
                    if not conns:
                        del self.active_connections[uid]
        return delivered

    def local_connection_count(self, user_id: int | None = None) -> int:
        """本进程持有的连接数（user_id 为空则统计总数）——用于可观测性。"""
        if user_id is None:
            return sum(len(v) for v in self.active_connections.values())
        return len(self.active_connections.get(user_id, []))

    # ---------- 跨进程发布 ----------
    async def _publish(self, envelope: dict) -> None:
        """把消息发布到 Redis 频道；由所有 worker（含本进程）订阅后投递。

        Redis 不可用时**降级**为本地直接投递，保证单 worker / 开发环境下仍可用。
        """
        payload = json.dumps(envelope, ensure_ascii=False)
        r = await self._get_redis()
        if r is not None:
            try:
                await r.publish(WS_CHANNEL, payload)
                return
            except Exception as e:
                logger.warning(f"WS publish 失败，降级本地投递: {e}")
        # 降级：本地直接投递（订阅端未运行时也能工作）
        await self._dispatch(envelope)

    async def send_to_user(self, user_id: int, message: dict) -> int:
        """向指定用户推送消息（跨 worker 可达）。

        返回「本进程直接投递的连接数」；消息本身经 Redis 广播给所有 worker，
        由持有该连接的 worker 完成投递，因此返回值 0 **不代表未触达**。
        需要「是否在线」的准确判断请用 ``is_online`` / ``count_online``。
        """
        uid = int(user_id)
        local = self.local_connection_count(uid)
        await self._publish({"kind": "user", "user_id": uid, "message": message})
        return local

    async def broadcast(self, message: dict) -> int:
        """向所有在线用户广播（跨 worker 可达）。返回本进程直接投递的连接数。"""
        local = sum(len(v) for v in self.active_connections.values())
        await self._publish({"kind": "all", "message": message})
        return local

    # ---------- 订阅端 ----------
    async def _dispatch(self, envelope: dict) -> None:
        """按信封类型做本地投递（订阅回调与降级路径共用）。"""
        try:
            kind = envelope.get("kind")
            message = envelope.get("message") or {}
            if kind == "user":
                await self._deliver_local(int(envelope.get("user_id")), message)
            elif kind == "all":
                await self._deliver_local_all(message)
        except Exception as e:
            logger.warning(f"WS dispatch 异常: {e}")

    async def _run_subscriber(self) -> None:
        """常驻订阅：收到任意 worker 的广播即在本地投递。

        使用**专用** pub/sub 客户端（无读超时），避免空闲时被误判断连而反复重连。
        断线自动重连（Redis 重启 / 网络抖动都不影响）。
        """
        while True:
            pubsub = None
            try:
                from app.redis_client import make_pubsub_client
                sub_client = make_pubsub_client()
                pubsub = sub_client.pubsub()
                await pubsub.subscribe(WS_CHANNEL)
                logger.info(f"WS 订阅端已就绪: {WS_CHANNEL}")
                async for item in pubsub.listen():
                    if item.get("type") != "message":
                        continue
                    try:
                        envelope = json.loads(item.get("data") or "{}")
                    except Exception:
                        continue
                    await self._dispatch(envelope)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"WS 订阅端异常，2s 后重连: {e}")
                await asyncio.sleep(2)
            finally:
                if pubsub is not None:
                    try:
                        await pubsub.close()
                    except Exception:
                        pass

    async def start_subscriber(self) -> None:
        """启动订阅端（应用生命周期内调用一次；幂等）。"""
        if self._sub_task is not None and not self._sub_task.done():
            return
        self._sub_task = asyncio.create_task(self._run_subscriber())

    async def stop_subscriber(self) -> None:
        """停止订阅端（应用关闭时调用）。"""
        if self._sub_task is not None:
            self._sub_task.cancel()
            try:
                await self._sub_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
            self._sub_task = None


# 全局单例
ws_manager = WSManager()
