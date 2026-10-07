"""每日签到服务：查询签到状态、执行签到、发放积分

设计要点
--------
1. **积分按等级发放，不硬编码在业务分支里**：等级 → 积分数的映射放在
   ``CHECKIN_REWARDS``，后续新增等级/调数值只改这一处；默认值可被
   ``system_config`` 表里的 ``checkin_reward_<tier>`` 覆盖（管理员可调，
   改完立即生效，无需改代码）。
2. **幂等靠数据库唯一键**而非「先查再插」：``daily_checkin`` 上的
   ``UNIQUE(user_id, checkin_date)`` 保证并发双击也只能签成一次，
   这是唯一可靠的防重手段（应用层判断在高并发下必然有竞态窗口）。
3. **日期口径**：以服务端本地日期为准（数据库 ``CURDATE()``），
   凌晨 00:00 自然切换为新的一天，无需定时任务清标记。
"""
from datetime import date

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
import redis.asyncio as aioredis

from app.services.ws_manager import ws_manager

# 等级 → 每日签到积分（可在 system_config 用 checkin_reward_<tier> 覆盖）
DEFAULT_CHECKIN_REWARDS: dict[str, int] = {
    "free": 50,
    "pro": 100,
    "max": 200,
}


async def _load_rewards(db: AsyncSession) -> dict[str, int]:
    """读取签到积分配置：system_config 覆盖 > 默认值。

    一次查询取回所有 ``checkin_reward_*`` 键，避免逐等级查库。
    """
    rewards = dict(DEFAULT_CHECKIN_REWARDS)
    try:
        result = await db.execute(
            text("SELECT config_key, config_value FROM system_config "
                 "WHERE config_key LIKE 'checkin_reward_%'")
        )
        for row in result.mappings():
            tier = str(row["config_key"])[len("checkin_reward_"):]
            try:
                rewards[tier] = max(0, int(row["config_value"]))
            except (TypeError, ValueError):
                continue
    except Exception:
        pass
    return rewards


async def _user_tier(user_id: int, db: AsyncSession) -> str:
    """当前有效等级：已过期视为 free（与订单侧口径保持一致）。"""
    result = await db.execute(
        text("SELECT membership_type, membership_expire FROM users WHERE id = :uid"),
        {"uid": user_id},
    )
    row = result.mappings().first()
    if not row:
        return "free"
    tier = row["membership_type"] or "free"
    expire = row["membership_expire"]
    if expire is not None:
        # 与 order_service 相同的判定：过期即降为 free
        expired = await db.execute(
            text("SELECT :exp <= NOW() AS expired"), {"exp": expire}
        )
        if expired.scalar():
            return "free"
    return tier


async def get_status(user_id: int, db: AsyncSession) -> dict:
    """查询今日签到状态（供客户端按钮态渲染与宣传文案展示）。

    返回 {can_checkin, checked, today, reward, points, tier, total_days}
    """
    rewards = await _load_rewards(db)
    tier = await _user_tier(user_id, db)
    reward = rewards.get(tier, rewards.get("free", 0))

    result = await db.execute(
        text("SELECT CURDATE() AS today")
    )
    today = result.scalar()

    checked_result = await db.execute(
        text("""
            SELECT COUNT(*) FROM daily_checkin
            WHERE user_id = :uid AND checkin_date = CURDATE()
        """),
        {"uid": user_id},
    )
    checked = int(checked_result.scalar() or 0) > 0

    total_result = await db.execute(
        text("SELECT COUNT(*) FROM daily_checkin WHERE user_id = :uid"),
        {"uid": user_id},
    )
    total_days = int(total_result.scalar() or 0)

    points_result = await db.execute(
        text("SELECT points FROM users WHERE id = :uid"), {"uid": user_id}
    )
    points = points_result.scalar() or 0

    return {
        "can_checkin": not checked,
        "checked": checked,
        "today": today.strftime("%Y-%m-%d") if hasattr(today, "strftime") else str(today),
        "reward": reward,
        "points": points,
        "tier": tier,
        "total_days": total_days,
    }


async def check_in(
    user_id: int,
    db: AsyncSession,
    redis: aioredis.Redis,
) -> dict:
    """执行签到：发积分 + 写流水 + 推送。

    幂等：依赖 ``daily_checkin`` 的 ``UNIQUE(user_id, checkin_date)``，
    重复签到抛 IntegrityError 后转成业务错误 400。
    """
    rewards = await _load_rewards(db)
    tier = await _user_tier(user_id, db)
    reward = rewards.get(tier, rewards.get("free", 0))

    if reward <= 0:
        raise ValueError("当前等级暂无可签到积分", 400)

    today_result = await db.execute(text("SELECT CURDATE() AS today"))
    today = today_result.scalar()
    today_str = today.strftime("%Y-%m-%d") if hasattr(today, "strftime") else str(today)

    # 插入签到记录；唯一键冲突 = 今日已签
    try:
        await db.execute(
            text("""
                INSERT INTO daily_checkin (user_id, checkin_date, points_awarded)
                VALUES (:uid, CURDATE(), :pts)
            """),
            {"uid": user_id, "pts": reward},
        )
    except IntegrityError:
        raise ValueError("今日已签到", 400)

    # 加积分（与users 更新同一事务）
    await db.execute(
        text("UPDATE users SET points = points + :pts WHERE id = :uid"),
        {"pts": reward, "uid": user_id},
    )

    # 写积分流水（balance_after 取更新后的真实余额）
    await db.execute(
        text("""
            INSERT INTO points_log (user_id, change_amount, balance_after, reason, detail)
            SELECT :uid, :ca, points, 'checkin', :detail FROM users WHERE id = :uid
        """),
        {"uid": user_id, "ca": reward,
         "detail": f"每日签到({today_str}) 等级:{tier}"},
    )

    # 清积分缓存
    await redis.delete(f"points_cache:{user_id}")

    new_points_result = await db.execute(
        text("SELECT points FROM users WHERE id = :uid"), {"uid": user_id}
    )
    new_points = int(new_points_result.scalar() or 0)

    total_result = await db.execute(
        text("SELECT COUNT(*) FROM daily_checkin WHERE user_id = :uid"),
        {"uid": user_id},
    )
    total_days = int(total_result.scalar() or 0)

    # WS 推送：积分变化 + 签到结果（客户端据此刷新按钮为「今日已签」）
    await ws_manager.send_to_user(user_id, {
        "type": "points_update",
        "data": {"points": new_points, "change": reward, "reason": "checkin"},
    })
    await ws_manager.send_to_user(user_id, {
        "type": "checkin_result",
        "data": {
            "success": True,
            "points_added": reward,
            "points": new_points,
            "tier": tier,
            "total_days": total_days,
            "today": today_str,
        },
    })

    return {
        "success": True,
        "points_added": reward,
        "points": new_points,
        "tier": tier,
        "total_days": total_days,
        "today": today_str,
    }


def today_str() -> str:
    """本地日期字符串（客户端兜底展示用）。"""
    return date.today().strftime("%Y-%m-%d")