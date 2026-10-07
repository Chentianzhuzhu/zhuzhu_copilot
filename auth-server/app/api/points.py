"""积分 API：查询余额、消耗"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.redis_client import get_redis
from app.core.deps import get_current_user
from app.schemas import PointsConsumeRequest
from app.services import points_service

router = APIRouter(prefix="/api/points", tags=["积分"])


@router.get("/balance")
async def get_balance(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
):
    """查询积分余额和会员类型"""
    result = await points_service.get_points_balance(current_user["id"], db, redis)
    return {"code": 0, "data": result}


@router.post("/consume")
async def consume(
    req: PointsConsumeRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
):
    """积分消耗"""
    try:
        result = await points_service.consume_points(
            user_id=current_user["id"],
            amount=req.amount,
            model=req.model,
            task_id=req.task_id,
            db=db,
            redis=redis,
        )
    except ValueError as e:
        msg, code = e.args[0], e.args[1]
        extra = e.args[2] if len(e.args) > 2 else {}
        raise HTTPException(status_code=code, detail=msg, headers={
            "X-Remaining": str(extra.get("remaining", 0))
        })

    return {"code": 0, "data": result}
