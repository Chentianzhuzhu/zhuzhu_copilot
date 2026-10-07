"""每日签到 API：查询签到状态、执行签到"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.redis_client import get_redis
from app.core.deps import get_current_user
from app.services import checkin_service

router = APIRouter(prefix="/api/checkin", tags=["签到"])


@router.get("/status")
async def get_status(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """今日签到状态：是否可签、当前等级可得积分、今日已得积分与累计天数。"""
    result = await checkin_service.get_status(current_user["id"], db)
    return {"code": 0, "data": result}


@router.post("")
async def do_checkin(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
):
    """执行签到（每日一次）。重复签到返回 400「今日已签到」。"""
    try:
        result = await checkin_service.check_in(
            user_id=current_user["id"], db=db, redis=redis)
    except ValueError as e:
        msg, code = e.args[0], e.args[1]
        raise HTTPException(status_code=code, detail=msg)
    return {"code": 0, "data": result}