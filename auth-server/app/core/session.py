"""Redis 会话管理：JWT 加 jti 后，服务端在 Redis 维护会话状态，支持：
- 登录态持久化与滑动续期
- 登出立即失效（真正撤销 token，而非等其自然过期）
- 强制下线（禁用/删除账号时撤销该用户全部会话）
- 敏感接口统一鉴权拦截
"""
from datetime import datetime, timezone
import json
import logging
import redis.asyncio as aioredis

from app.config import settings

logger = logging.getLogger(__name__)

# jti -> json 会话信息。TTL 与 JWT 有效期一致，访问时滑动续期，空闲超过窗口即失效。
SESSION_KEY_PREFIX = "auth_session:"
# user_id -> set of jti（用于强制下线时批量撤销）
USER_SESSIONS_PREFIX = "user_sessions:"


def session_key(jti: str) -> str:
    return f"{SESSION_KEY_PREFIX}{jti}"


def _user_sessions_key(user_id: int) -> str:
    return f"{USER_SESSIONS_PREFIX}{user_id}"


async def create_session(redis: aioredis.Redis, jti: str, payload: dict) -> None:
    """登录成功后写入会话，并把 jti 登记到用户会话集合中"""
    value = {
        "user_id": payload.get("user_id"),
        "type": payload.get("type"),
        "username": payload.get("username"),
        "login_at": datetime.now(timezone.utc).isoformat(),
    }
    ttl = settings.jwt_expire_hours * 3600
    await redis.set(session_key(jti), _serialize(value), ex=ttl)
    user_id = payload.get("user_id")
    if user_id is not None:
        await redis.sadd(_user_sessions_key(user_id), jti)
        await redis.expire(_user_sessions_key(user_id), ttl)


async def is_session_valid(redis: aioredis.Redis, jti: str, expected_type: str) -> bool:
    """校验会话是否存在且类型匹配；校验通过后滑动续期。"""
    key = session_key(jti)
    raw = await redis.get(key)
    if not raw:
        return False
    value = _deserialize(raw)
    if not isinstance(value, dict) or value.get("type") != expected_type:
        return False
    # 滑动续期：每次使用都重置 TTL（登录态持久管理 / 续期）
    await redis.expire(key, settings.jwt_expire_hours * 3600)
    user_id = value.get("user_id")
    if user_id is not None:
        await redis.expire(_user_sessions_key(user_id), settings.jwt_expire_hours * 3600)
    return True


async def revoke_session(redis: aioredis.Redis, jti: str) -> None:
    """登出：撤销单个 token 会话，并解除用户会话登记。"""
    if not jti:
        return
    key = session_key(jti)
    raw = await redis.get(key)
    if raw:
        value = _deserialize(raw)
        user_id = value.get("user_id")
        if user_id is not None:
            await redis.srem(_user_sessions_key(user_id), jti)
    await redis.delete(key)


async def revoke_all_sessions(redis: aioredis.Redis, user_id: int) -> None:
    """强制下线：撤销该用户全部已登记会话（禁用/删除账号时调用）。"""
    set_key = _user_sessions_key(user_id)
    jtis = await redis.smembers(set_key)
    pipe = redis.pipeline()
    for jti in jtis:
        pipe.delete(session_key(jti))
    pipe.delete(set_key)
    await pipe.execute()


def _serialize(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False)


def _deserialize(raw: str) -> dict:
    try:
        return json.loads(raw)
    except Exception:
        return {}