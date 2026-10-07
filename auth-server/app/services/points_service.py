"""积分服务：原子扣减、查询"""
import asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
import redis.asyncio as aioredis

from app.services.ws_manager import ws_manager


async def get_points_balance(user_id: int, db: AsyncSession, redis: aioredis.Redis) -> dict:
    """查询积分余额和会员类型"""
    # 先查 Redis 缓存
    cached = await redis.get(f"points_cache:{user_id}")
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
        await redis.setex(f"points_cache:{user_id}", 300, str(points))

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
    积分消耗：Redis 原子扣减，异步写 MySQL 流水
    返回: {"remaining": int}
    异常: 积分不足抛 ValueError 402
    """
    # 获取当前积分（从缓存或数据库）
    cached = await redis.get(f"points_cache:{user_id}")
    if cached is not None:
        current_points = int(cached)
    else:
        result = await db.execute(
            text("SELECT points FROM users WHERE id = :uid"),
            {"uid": user_id},
        )
        row = result.mappings().first()
        if not row:
            raise ValueError("用户不存在", 401)
        current_points = row["points"]
        await redis.setex(f"points_cache:{user_id}", 300, str(current_points))

    # 检查积分是否足够
    if current_points < amount:
        raise ValueError("积分不足", 402, {"remaining": current_points})

    # Redis 原子扣减
    new_points = await redis.decrby(f"points_cache:{user_id}", amount)
    await redis.expire(f"points_cache:{user_id}", 300)  # 刷新TTL

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
