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


# ---------------------------------------------------------------------------
# L2：远程共享缓存节点（积分余额缓存/原子扣减等正确性敏感、跨机共享的数据）。
# 会话/登录限流/pubsub 等强热路径仍走本机 redis（RTT <1ms）；
# L2 走远程节点（RTT ~39ms），因此对 L2 的操作必须保持「一次往返」粒度
# （用 pipeline / Lua），禁止循环逐条读写。
# 未配置 REDIS_L2_HOST 时 settings.redis_l2_url 回落本机，行为与旧版一致。
# ---------------------------------------------------------------------------
redis_l2: aioredis.Redis = aioredis.from_url(
    settings.redis_l2_url,
    decode_responses=True,
    socket_connect_timeout=5,
    socket_timeout=5,
)


async def get_redis_l2() -> aioredis.Redis:
    """获取 L2 远程缓存客户端（FastAPI 依赖注入）"""
    return redis_l2


def points_cache_key(user_id: int) -> str:
    """积分余额缓存 key（位于 L2）。

    前缀 ``points:`` 满足远程节点 api_app 账号的 ACL key 规则（~points:*）。
    注意：旧 key 名为 ``points_cache:{uid}``，不匹配 ACL 前缀，已统一迁移；
    旧 key 均为 300s TTL 临时缓存，迁移无持久化影响。
    """
    return f"points:cache:{user_id}"