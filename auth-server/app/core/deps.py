"""依赖注入：获取当前用户、管理员（含服务端会话校验与敏感接口拦截）"""
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.redis_client import get_redis
from app.core.security import decode_jwt
from app.core.session import is_session_valid
import redis.asyncio as aioredis

# Bearer Token 提取器
bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
) -> dict:
    """获取当前登录用户（普通用户），校验服务端会话并滑动续期"""
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录")

    token = credentials.credentials
    try:
        payload = decode_jwt(token)
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token无效或已过期")

    if payload.get("type") != "user":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="非用户Token")

    jti = payload.get("jti")
    # 服务端会话校验：登出后 token 立即失效；有效则滑动续期
    if not jti or not await is_session_valid(redis, jti, "user"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录态已失效，请重新登录")

    user_id = int(payload["sub"])

    # 查询用户状态
    result = await db.execute(
        text("SELECT id, username, avatar, points, membership_type, membership_expire, status FROM users WHERE id = :uid"),
        {"uid": user_id},
    )
    row = result.mappings().first()
    if not row:
        await revoke_jti_session(redis, jti)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在")

    if row["status"] == "deleted":
        await revoke_jti_session(redis, jti)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="账号已被删除")
    if row["status"] == "disabled":
        await revoke_jti_session(redis, jti)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号已被禁用")

    return {
        "id": row["id"],
        "username": row["username"],
        "avatar": row["avatar"],
        "points": row["points"],
        "membership_type": row["membership_type"],
        "membership_expire": row["membership_expire"].strftime("%Y-%m-%d %H:%M:%S") if row["membership_expire"] else None,
        "status": row["status"],
        "token": token,
        "jti": jti,
    }


async def get_current_admin(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
) -> dict:
    """获取当前登录管理员，校验服务端会话并滑动续期"""
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录")

    token = credentials.credentials
    try:
        payload = decode_jwt(token)
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token无效或已过期")

    if payload.get("type") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="非管理员Token")

    jti = payload.get("jti")
    if not jti or not await is_session_valid(redis, jti, "admin"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录态已失效，请重新登录")

    admin_id = int(payload["sub"])

    result = await db.execute(
        text("SELECT id, username FROM admins WHERE id = :aid"),
        {"aid": admin_id},
    )
    row = result.mappings().first()
    if not row:
        await revoke_jti_session(redis, jti)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="管理员不存在")

    return {"id": row["id"], "username": row["username"], "token": token, "jti": jti}


async def revoke_jti_session(redis: aioredis.Redis, jti: str):
    from app.core.session import revoke_session
    await revoke_session(redis, jti)