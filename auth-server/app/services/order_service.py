"""订单服务：创建订单、确认到账、发放积分/会员

会员等级与购买规则（唯一事实来源，本模块）
------------------------------------------
- 等级序由``MEMBERSHIP_TIERS`` 声明（``free < pro < max``），新增等级只需在此追加一项；
- ``_resolve_target_tier`` 统一裁决「买了这个商品之后，用户应该是什么等级」，从而保证：
    * Pro 买 Max → 升为 Max（保留Pro 剩余时长，叠加 30 天）；
    * Max 买 Max → 续费，时长在当前到期日上叠加；
    * Max 买 Pro → 视为续费 Pro，不降级（等级只升不降）；
    * 积分一律照发。
  换句话说：**等级只升不降，时长只叠加不覆盖**，因此不存在「买便宜的把贵的顶掉」的口子。
"""
from datetime import datetime, timedelta
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
import redis.asyncio as aioredis

from app.services.ws_manager import ws_manager


# 会员等级序：从低到高。rank 由下标推导，禁止在业务代码里写死数字比较。
MEMBERSHIP_TIERS: tuple[str, ...] = ("free", "pro", "max")

# 商品定义
# rank 字段不写死，改由 MEMBERSHIP_TIERS 下标推导（见_tier_rank）。
PRODUCTS = {
    # 会员类
    "pro_monthly": {
        "order_type": "membership",
        "name": "Pro版",
        "price": 7.00,
        "points": 1000,
        "duration_days": 30,
        "membership_type": "pro",
    },
    "max_monthly": {
        "order_type": "membership",
        "name": "Max版",
        "price": 14.00,
        "points": 2000,
        "duration_days": 30,
        "membership_type": "max",
    },
    # 积分包类
    "points_150": {
        "order_type": "points",
        "name": "150积分",
        "price": 1.00,
        "points": 150,
    },
    "points_750": {
        "order_type": "points",
        "name": "750积分",
        "price": 5.00,
        "points": 750,
    },
    "points_1500": {
        "order_type": "points",
        "name": "1500积分",
        "price": 10.00,
        "points": 1500,
    },
}


def _tier_rank(tier: str) -> int:
    """等级序号；未登记的等级按 free（0）处理，保证脏数据不会把用户抬到高位。"""
    try:
        return MEMBERSHIP_TIERS.index(tier)
    except ValueError:
        return 0


def _resolve_target_tier(current_tier: str, target_tier: str) -> str:
    """裁决成交后的会员等级：**只升不降**。

    - free → pro/max：按目标等级升级；
    - pro→ max：升级；
    - max → max：续费，等级不变；
    - max → pro：等级仍为 max（否则买Pro 就能把 Max 顶掉，形成降级漏洞）。
    """
    return target_tier if _tier_rank(target_tier) > _tier_rank(current_tier) else current_tier


def generate_order_id() -> str:
    """生成订单号：ORD + 日期 + 4位序号"""
    now = datetime.now()
    return f"ORD{now.strftime('%Y%m%d')}{now.microsecond // 10000:04d}"


async def get_user_membership(user_id: int, db: AsyncSession) -> tuple[str, datetime | None]:
    """读取用户当前（等级, 到期时间）。"""
    result = await db.execute(
        text("SELECT membership_type, membership_expire FROM users WHERE id = :uid"),
        {"uid": user_id},
    )
    row = result.mappings().first()
    if not row:
        raise ValueError("用户不存在", 401)
    tier = row["membership_type"] or "free"
    expire = row["membership_expire"]
    # 已过期视为 free：过期 Max 不应再被当作Max 拦截购买 Pro
    if expire is not None and expire <= datetime.now():
        tier = "free"
    return tier, expire


def evaluate_purchase(current_tier: str, product: dict) -> dict:
    """评估「当前等级买这个商品」是否允许，返回可购性与展示用信息。

    规则（与文档一致）：
    - Max 有效期内禁止购买 Pro（避免用户误操作花钱买了更低的档）；
    - Max 有效期内允许购买 Max —— 语义为「续费」，时长在当前到期日上叠加；
    - 其它组合均允许。
    """
    target = product.get("membership_type")
    allowed = True
    reason = ""            # 拒绝原因（allowed=False 时用于接口返回）
    action = "purchase"    # purchase | upgrade | renew
    if target:
        cur_rank, tgt_rank = _tier_rank(current_tier), _tier_rank(target)
        if cur_rank > tgt_rank:
            allowed = False
            reason = (f"您当前是{_tier_label(current_tier)}，"
                      f"无法购买{_tier_label(target)}。")
        elif cur_rank == tgt_rank and cur_rank > 0:
            action = "renew"        # 同档续费
        elif tgt_rank > cur_rank and cur_rank > 0:
            action = "upgrade"      # 付费档位之间升档
        # cur_rank == 0（免费用户首购）保持 action = "purchase"
    return {"allowed": allowed, "reason": reason, "action": action,
            "target_tier": target, "current_tier": current_tier}


def _tier_label(tier: str) -> str:
    """等级中文名（错误提示用；客户端有 i18n，此处仅服务端提示）。"""
    return {"free": "免费版", "pro": "Pro版", "max": "Max版"}.get(tier, tier)


async def create_order(
    user_id: int,
    product_id: str,
    pay_method: str,
    db: AsyncSession,
    redis: aioredis.Redis,
) -> dict:
    """
    创建订单
    返回: 订单信息 dict
    """
    if product_id not in PRODUCTS:
        raise ValueError("商品不存在", 400)

    if pay_method not in ("alipay", "wechat"):
        raise ValueError("支付方式无效", 400)

    product = PRODUCTS[product_id]

    # 下单前校验购买资格：Max 有效期内不允许买 Pro，避免用户白付一次钱。
    current_tier, _expire = await get_user_membership(user_id, db)
    verdict = evaluate_purchase(current_tier, product)
    if not verdict["allowed"]:
        raise ValueError(verdict["reason"], 400)

    order_id = generate_order_id()

    # 插入订单
    await db.execute(
        text("""
            INSERT INTO orders (id, user_id, order_type, product_id, amount, points_amount, pay_method, status)
            VALUES (:oid, :uid, :ot, :pid, :amt, :pa, :pm, 'pending')
        """),
        {
            "oid": order_id,
            "uid": user_id,
            "ot": product["order_type"],
            "pid": product_id,
            "amt": product["price"],
            "pa": product["points"],
            "pm": pay_method,
        },
    )

    # 获取收款码URL
    qr_key = f"{pay_method}_qrcode"
    qr_result = await db.execute(
        text("SELECT config_value FROM system_config WHERE config_key = :k"),
        {"k": qr_key},
    )
    qr_row = qr_result.mappings().first()
    qrcode_url = qr_row["config_value"] if qr_row else ""

    return {
        "order_id": order_id,
        "product_id": product_id,
        "amount": product["price"],
        "pay_method": pay_method,
        "qrcode_url": qrcode_url,
        "status": "pending",
        # purchase=新购/升级, renew=同档续费（客户端据此把按钮文案显示为「续费」）
        "action": verdict["action"],
    }


async def get_order_status(order_id: str, user_id: int, db: AsyncSession) -> dict:
    """查询订单状态"""
    result = await db.execute(
        text("SELECT * FROM orders WHERE id = :oid AND user_id = :uid"),
        {"oid": order_id, "uid": user_id},
    )
    row = result.mappings().first()
    if not row:
        raise ValueError("订单不存在", 404)

    return {
        "order_id": row["id"],
        "product_id": row["product_id"],
        "order_type": row["order_type"],
        "amount": float(row["amount"]),
        "points_amount": row["points_amount"],
        "pay_method": row["pay_method"],
        "status": row["status"],
        "created_at": row["created_at"].strftime("%Y-%m-%d %H:%M:%S") if row["created_at"] else None,
        "paid_at": row["paid_at"].strftime("%Y-%m-%d %H:%M:%S") if row["paid_at"] else None,
    }


async def confirm_order(
    order_id: str,
    db: AsyncSession,
    redis: aioredis.Redis,
) -> dict:
    """
    管理员确认订单到账：标记paid，发放积分/会员
    """
    # 查询订单
    result = await db.execute(
        text("SELECT * FROM orders WHERE id = :oid FOR UPDATE"),
        {"oid": order_id},
    )
    row = result.mappings().first()
    if not row:
        raise ValueError("订单不存在", 404)

    if row["status"] == "paid":
        raise ValueError("订单已支付", 400)

    product = PRODUCTS.get(row["product_id"])
    if not product:
        raise ValueError("商品配置异常", 500)

    # 更新订单状态
    await db.execute(
        text("UPDATE orders SET status = 'paid', paid_at = NOW() WHERE id = :oid"),
        {"oid": order_id},
    )

    user_id = row["user_id"]
    points_added = product["points"]
    membership_after: str | None = None   # 仅会员类订单有值，供返回给管理端

    # 发放积分 / 开通会员
    if product["order_type"] == "membership":
        target_tier = product["membership_type"]
        days = product["duration_days"]

        user_result = await db.execute(
            text("SELECT membership_type, membership_expire FROM users WHERE id = :uid"),
            {"uid": user_id},
        )
        user_row = user_result.mappings().first()
        if not user_row:
            raise ValueError("用户不存在", 404)

        now = datetime.now()
        cur_expire = user_row["membership_expire"]
        cur_tier = user_row["membership_type"] or "free"
        # 过期会员按 free 处理，避免过期数据参与等级裁决
        if cur_expire is not None and cur_expire <= now:
            cur_tier = "free"

        # 等级只升不降：Max 买 Pro（历史订单/绕过下单校验的路径）不会把 Max 降掉
        membership_type = _resolve_target_tier(cur_tier, target_tier)
        membership_after = membership_type

        # 时长只叠加不覆盖：Pro 升级 Max → Pro 剩余天数 + 30 天
        if cur_expire is not None and cur_expire > now:
            new_expire = cur_expire + timedelta(days=days)
        else:
            new_expire = now + timedelta(days=days)

        await db.execute(
            text("""
                UPDATE users SET membership_type = :mt, membership_expire = :me,
                points = points + :pts WHERE id = :uid
            """),
            {"mt": membership_type, "me": new_expire, "pts": points_added, "uid": user_id},
        )
    else:
        # 纯积分包：直接加积分
        await db.execute(
            text("UPDATE users SET points = points + :pts WHERE id = :uid"),
            {"pts": points_added, "uid": user_id},
        )

    # 会员状态变更需通知客户端刷新会员类型/到期日（等级或时长任一变化都要推）
    if membership_after is not None:
        after_result = await db.execute(
            text("SELECT membership_type, membership_expire FROM users WHERE id = :uid"),
            {"uid": user_id},
        )
        after_row = after_result.mappings().first()
        if after_row:
            await ws_manager.send_to_user(user_id, {
                "type": "membership_update",
                "data": {
                    "membership_type": after_row["membership_type"],
                    "membership_expire": (
                        after_row["membership_expire"].strftime("%Y-%m-%d %H:%M:%S")
                        if after_row["membership_expire"] else None
                    ),
                },
            })

    # 写积分流水
    await db.execute(
        text("""
            INSERT INTO points_log (user_id, change_amount, balance_after, reason, detail)
            SELECT :uid, :ca, points, :reason, :detail FROM users WHERE id = :uid
        """),
        {
            "uid": user_id,
            "ca": points_added,
            "reason": "membership" if product["order_type"] == "membership" else "purchase",
            "detail": f"订单:{order_id} 商品:{product['name']}",
        },
    )

    # 清除积分缓存
    await redis.delete(f"points_cache:{user_id}")

    # WebSocket 推送
    await ws_manager.send_to_user(user_id, {
        "type": "order_paid",
        "data": {
            "order_id": order_id,
            "points_added": points_added,
        },
    })

    # 推送积分更新
    user_result = await db.execute(
        text("SELECT points FROM users WHERE id = :uid"), {"uid": user_id}
    )
    new_points = user_result.scalar()
    await ws_manager.send_to_user(user_id, {
        "type": "points_update",
        "data": {
            "points": new_points,
            "change": points_added,
            "reason": "purchase",
        },
    })

    return {
        "order_id": order_id,
        "points_added": points_added,
        "status": "paid",
        "membership_type": membership_after,
    }
