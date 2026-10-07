"""Redis 连接模块（异步）"""
import redis.asyncio as aioredis
from app.config import settings

# 全局 Redis 客户端（用于普通命令：GET/SET/PUBLISH/集合等）。
# socket_timeout=5：单条命令 5s 内必须返回，避免请求堆积。
redis: aioredis.Redis = aioredis.from_url(
    settings.redis_url,
    decode_responses=True,
    socket_connect_timeout=5,
    socket_timeout=5,
)


async def get_redis() -> aioredis.Redis:
    """获取 Redis 客户端（FastAPI 依赖注入）"""
    return redis


def make_pubsub_client() -> aioredis.Redis:
    """创建**专用** pub/sub 客户端。

    与全局客户端的关键差异：``socket_timeout=None``。
    pub/sub 的 ``listen()`` 会长时间阻塞等待消息，若沿用 5s 的 socket 超时，
    空闲时会被判定为「读超时」而反复断连重连（表现为日志刷 Timeout reading）。
    ``health_check_interval`` 用于定期 ping，防止中间设备/服务端回收空闲连接。
    """
    return aioredis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=None,          # 长阻塞读，不能设读超时
        health_check_interval=30,     # 30s 空闲即 ping，保活
    )