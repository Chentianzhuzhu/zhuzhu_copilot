"""登录握手 state 管理：防 login CSRF / 重放的「一次性随机票据」。

流程：
1. 客户端调用 POST /api/auth/state 申请 state（服务端生成随机 token，Redis 落库）；
2. 客户端打开登录页时带上 state（URL 参数），登录页在提交登录/注册时回传该 state；
3. 服务端校验 state 是否存在、未过期、未被消费；校验通过后立即删除（单次消费）；
4. 校验结果随回调原样返回，客户端比对本地 state 与回调 state 是否一致。

state 存于 Redis：`login_state:{state}` -> platform/version/created，TTL 由配置控制。

【服务端中转回跳】除本地 127.0.0.1 回调外，登录页还会把登录结果写入
`login_result:{state}`（短 TTL），桌面客户端轮询 /api/auth/login_result 取回。
该通道不依赖「HTTPS 页面 → http://127.0.0.1」的浏览器同源/混合内容策略，
因此在源码运行、浏览器拦截 localhost 等场景下依然能可靠把 token 交回应用。
"""
import json
import logging
import secrets
from datetime import datetime, timezone
import redis.asyncio as aioredis

from app.config import settings

logger = logging.getLogger(__name__)

LOGIN_STATE_PREFIX = "login_state:"
# 登录结果中转键：login_result:{state} -> {token, user, ...}（一次性取走后删除）
LOGIN_RESULT_PREFIX = "login_result:"


def _state_key(state: str) -> str:
    return f"{LOGIN_STATE_PREFIX}{state}"


def _result_key(state: str) -> str:
    return f"{LOGIN_RESULT_PREFIX}{state}"


def generate_state() -> str:
    """生成密码学安全的随机 state（32 字节 -> 64 位 hex）。"""
    return secrets.token_hex(32)


async def issue_state(redis: aioredis.Redis, platform: str = "", version: str = "",
                      client_ip: str = "") -> str:
    """签发一次性 state 并写入 Redis，返回 state 字符串。"""
    state = generate_state()
    value = {
        "platform": (platform or "").strip(),
        "version": (version or "").strip(),
        "client_ip": client_ip or "",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await redis.set(
        _state_key(state),
        json.dumps(value, ensure_ascii=False),
        ex=settings.login_state_ttl_seconds,
    )
    return state


async def verify_state(redis: aioredis.Redis, state: str) -> dict | None:
    """非破坏性校验 state 是否存在且未过期。有效返回 payload，无效返回 None。

    用于登录/注册阶段：此时不能立即消费 state（否则服务端中转回跳的
    login_result 校验会失败）。真正的一次性/防重放由 take_login_result 的
    GETDEL 与 state 自身的 TTL 保证。
    """
    if not state:
        return None
    try:
        raw = await redis.get(_state_key(state))
    except Exception:
        return None
    if not raw:
        return None
    try:
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


async def consume_state(redis: aioredis.Redis, state: str) -> dict | None:
    """校验并消费 state（原子 GETDEL）。有效返回其 payload；无效返回 None。

    使用 GETDEL 保证「校验通过即删除」的原子性，避免并发下同一 state 被消费两次。
    """
    if not state:
        return None
    key = _state_key(state)
    try:
        raw = await redis.getdel(key)
    except AttributeError:
        # 兼容旧版 redis-py（无 getdel）：非原子回退
        raw = await redis.get(key)
        if raw:
            await redis.delete(key)
    if not raw:
        return None
    try:
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


async def invalidation(redis: aioredis.Redis, state: str) -> None:
    """显式作废 state（登录失败/流程结束时可调用）。"""
    if not state:
        return
    try:
        await redis.delete(_state_key(state))
    except Exception:
        pass


async def store_login_result(redis: aioredis.Redis, state: str, token: str,
                             user: dict | None = None) -> bool:
    """登录成功后在服务端暂存结果，供桌面客户端轮询取回（中转回跳）。

    仅当 state 仍有效（未被消费/未过期）时才暂存，避免被伪造 state 注入 token。
    成功返回 True。
    """
    if not state or not token:
        return False
    # state 有效性由调用方（登录/注册校验）保证；这里再次确认其存在（不消费）
    try:
        exists = await redis.exists(_state_key(state))
    except Exception:
        exists = 0
    if not exists:
        return False
    value = {"token": token, "user": user or {},
             "created_at": datetime.now(timezone.utc).isoformat()}
    await redis.set(_result_key(state), json.dumps(value, ensure_ascii=False),
                    ex=settings.login_state_ttl_seconds)
    return True


async def take_login_result(redis: aioredis.Redis, state: str) -> dict | None:
    """桌面客户端轮询取回登录结果（原子 GETDEL，取走即删，防重放）。"""
    if not state:
        return None
    key = _result_key(state)
    try:
        raw = await redis.getdel(key)
    except AttributeError:
        raw = await redis.get(key)
        if raw:
            await redis.delete(key)
    if not raw:
        return None
    try:
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None
