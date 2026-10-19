"""积分服务：原子扣减、查询

积分余额缓存位于 L2 远程共享节点（``points:cache:{uid}``），见 app/redis_client.py。
扣减用 Lua 脚本一次往返原子完成（查余额 + 校验 + 扣减 + 刷 TTL），
修复了旧版「先 GET 再 DECRBY」非原子导致的高并发负余额问题。
"""
import asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
import redis.asyncio as aioredis

from app.redis_client import redis_l2, points_cache_key
from app.services.ws_manager import ws_manager

# L2 原子扣减：KEYS[1]=余额key ARGV[1]=扣减额 ARGV[2]=缓存未命中时的DB回退值 ARGV[3]=TTL
# 返回 [新余额, 扣减前余额]；余额不足返回 [-1, 当前余额]。
_DEDUCT_LUA = """
local cur = redis.call('GET', KEYS[1])
if not cur then
    cur = ARGV[2]
    redis.call('SET', KEYS[1], cur, 'EX', tonumber(ARGV[3]))
end
cur = tonumber(cur)
local amt = tonumber(ARGV[1])
if cur < amt then
    return {-1, cur}
end
local nv = redis.call('DECRBY', KEYS[1], amt)
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[3]))
return {nv, cur}
"""

_POINTS_CACHE_TTL = 300  # 秒


async def get_points_balance(user_id: int, db: AsyncSession, redis: aioredis.Redis) -> dict:
    """查询积分余额和会员类型

    ``redis`` 参数保留（历史签名兼容），余额缓存一律走 L2。
    """
    key = points_cache_key(user_id)
    cached = await redis_l2.get(key)
    if cached:
        points = int(cached)
    else:
        result = await db.execute(
            text("SELECT points, membership_type FROM users WHERE id = :uid"),
            {"uid": user_id},
        )
        row = result.mappings().first()
        if not row:
            return {"points": 0, "membership_type": "free"}
        points = row["points"]
        await redis_l2.setex(key, _POINTS_CACHE_TTL, str(points))

    result = await db.execute(
        text("SELECT membership_type FROM users WHERE id = :uid"),
        {"uid": user_id},
    )
    mt_row = result.mappings().first()
    membership_type = mt_row["membership_type"] if mt_row else "free"

    return {"points": points, "membership_type": membership_type}


async def consume_points(
    user_id: int,
    amount: int,
    model: str,
    task_id: str | None,
    db: AsyncSession,
    redis: aioredis.Redis,
) -> dict:
    """
    积分消耗：L2 原子扣减（Lua 单往返），异步写 MySQL 流水
    返回: {"remaining": int}
    异常: 积分不足抛 ValueError 402
    """
    key = points_cache_key(user_id)
    cached = await redis_l2.get(key)
    if cached is not None:
        fallback = int(cached)
    else:
        # 缓存未命中：从 DB 取真实余额作为回退值（Lua 内会原子写入缓存）
        result = await db.execute(
            text("SELECT points FROM users WHERE id = :uid"),
            {"uid": user_id},
        )
        row = result.mappings().first()
        if not row:
            raise ValueError("用户不存在", 401)
        fallback = row["points"]

    # L2 原子扣减（一次往返；并发请求在 Redis 内串行执行，不会扣成负数）
    res = await redis_l2.eval(_DEDUCT_LUA, 1, key, amount, fallback, _POINTS_CACHE_TTL)
    new_points, cur_at_deduct = int(res[0]), int(res[1])
    if new_points < 0:
        raise ValueError("积分不足", 402, {"remaining": cur_at_deduct})

    # 异步写 MySQL 流水 + 更新数据库积分
    async def async_write_log():
        async with AsyncSessionLocal() as async_db:
            await async_db.execute(
                text("UPDATE users SET points = points - :amt WHERE id = :uid"),
                {"amt": amount, "uid": user_id},
            )
            await async_db.execute(
                text("""
                    INSERT INTO points_log (user_id, change_amount, balance_after, reason, detail)
                    VALUES (:uid, :ca, :ba, 'consume', :detail)
                """),
                {
                    "uid": user_id,
                    "ca": -amount,
                    "ba": new_points,
                    "detail": f"模型:{model}" + (f" task:{task_id}" if task_id else ""),
                },
            )
            await async_db.commit()

    # 延迟执行异步写库
    asyncio.create_task(async_write_log())

    # WebSocket 推送积分更新
    await ws_manager.send_to_user(user_id, {
        "type": "points_update",
        "data": {
            "points": new_points,
            "change": -amount,
            "reason": "consume",
        },
    })

    return {"remaining": new_points}


# 延迟导入避免循环引用
from app.database import AsyncSessionLocal
