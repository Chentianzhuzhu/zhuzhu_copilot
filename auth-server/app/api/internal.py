"""内部接口：供 pay-server 等受信服务回调发货。

安全模型：请求必须携带 X-Internal-Token，与 auth-server 的
INTERNAL_API_TOKEN 环境变量严格相等（常量时间比较）。令牌未配置时
一律拒绝（fail-closed），防止未初始化时裸奔。
"""
import hmac

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.redis_client import get_redis
from app.schemas import InternalGrantRequest
from app.services import order_service

router = APIRouter(prefix="/api/internal", tags=["内部"], include_in_schema=False)


def _check_token(x_internal_token: str | None) -> None:
    expected = (settings.internal_api_token or "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="内部接口未启用")
    supplied = (x_internal_token or "").strip()
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=403, detail="内部令牌无效")


@router.post("/grant")
async def grant(
    req: InternalGrantRequest,
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
):
    """外部支付渠道（如 Stripe）支付成功后发货：加积分/开通会员。

    幂等性由调用方订单状态保证（webhook 重放时调用方先判重）；
    本接口按 auth-server PRODUCTS 发货，等级只升不降、时长只叠加。
    """
    _check_token(x_internal_token)

    product = order_service.PRODUCTS.get(req.product_id)
    if not product:
        raise HTTPException(status_code=400, detail="商品不存在")

    try:
        membership_after = await order_service.grant_benefits(
            req.user_id, product,
            f"支付:{req.source} 订单:{req.order_id} 商品:{product['name']}",
            req.order_id, db,
        )
    except ValueError as e:
        msg, code = e.args[0], e.args[1]
        raise HTTPException(status_code=code, detail=msg)

    return {"code": 0, "data": {"membership_type": membership_after}}
