"""商城 API：商品列表、创建订单、订单查询、收款码"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.redis_client import get_redis
from app.core.deps import get_current_user
from app.core.security import decode_jwt
from app.schemas import CreateOrderRequest
from app.services import order_service

router = APIRouter(prefix="/api/shop", tags=["商城"])

# 商品列表允许未登录访问（价目页需展示），故用「可选鉴权」而非 get_current_user。
_optional_bearer = HTTPBearer(auto_error=False)


@router.get("/products")
async def get_products(
    credentials: HTTPAuthorizationCredentials | None = Depends(_optional_bearer),
    db: AsyncSession = Depends(get_db),
):
    """获取商品列表（含按当前等级的购买资格与按钮语义）。

    未登录也可访问（用于展示价目），此时按 free 评估。
    """
    current_tier = "free"
    if credentials is not None:
        try:
            payload = decode_jwt(credentials.credentials)
            if payload.get("type") == "user":
                current_tier, _expire = await order_service.get_user_membership(
                    int(payload["sub"]), db)
        except Exception:
            current_tier = "free"

    memberships, point_packs = [], []
    for pid, p in order_service.PRODUCTS.items():
        item = {
            "id": pid,
            "name": p["name"],
            "price": p["price"],
            "points": p["points"],
        }
        if p["order_type"] == "membership":
            item["duration_days"] = p["duration_days"]
            item["membership_type"] = p["membership_type"]
            verdict = order_service.evaluate_purchase(current_tier, p)
            # allowed=false → 客户端把卡片置灰并给出原因；action=renew → 按钮显示「续费」
            item["purchasable"] = verdict["allowed"]
            item["reason"] = verdict["reason"]
            item["action"] = verdict["action"]
            memberships.append(item)
        else:
            point_packs.append(item)

    return {
        "code": 0,
        "data": {
            "memberships": memberships,
            "point_packs": point_packs,
            "current_tier": current_tier,
        },
    }


@router.post("/order")
async def create_order(
    req: CreateOrderRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
):
    """创建订单"""
    try:
        result = await order_service.create_order(
            user_id=current_user["id"],
            product_id=req.product_id,
            pay_method=req.pay_method,
            db=db,
            redis=redis,
        )
    except ValueError as e:
        msg, code = e.args[0], e.args[1]
        raise HTTPException(status_code=code, detail=msg)

    return {"code": 0, "data": result}


@router.get("/order/{order_id}")
async def get_order(
    order_id: str,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """查询订单状态"""
    try:
        result = await order_service.get_order_status(order_id, current_user["id"], db)
    except ValueError as e:
        msg, code = e.args[0], e.args[1]
        raise HTTPException(status_code=code, detail=msg)

    return {"code": 0, "data": result}


@router.get("/qrcodes")
async def get_qrcodes(db: AsyncSession = Depends(get_db)):
    """获取收款码"""
    result = await db.execute(
        text("SELECT config_key, config_value FROM system_config WHERE config_key IN ('alipay_qrcode', 'wechat_qrcode')")
    )
    rows = result.mappings().all()
    qrcodes = {"alipay": "", "wechat": ""}
    for row in rows:
        if row["config_key"] == "alipay_qrcode":
            qrcodes["alipay"] = row["config_value"] or ""
        elif row["config_key"] == "wechat_qrcode":
            qrcodes["wechat"] = row["config_value"] or ""

    return {"code": 0, "data": qrcodes}
