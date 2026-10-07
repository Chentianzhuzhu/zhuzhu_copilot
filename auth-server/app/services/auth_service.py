"""认证服务：注册、登录（含账号/IP 锁定）、会话管理、Token 刷新"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
import redis.asyncio as aioredis

from app.config import settings
from app.core.security import (hash_password, verify_password, hash_answer,
                               verify_answer, create_jwt)
from app.core import session as session_store
from app.services.ws_manager import ws_manager


# ============ 用户名占用与释放 ============

#: users.username 列宽（VARCHAR(32)）。释放后缀必须在此长度内，否则写库报错。
USERNAME_MAX_LEN = 32
#: 软删除用户的用户名后缀标记。带 `#del#` 的名字视为「已释放」，不再对用户可见。
DELETED_USERNAME_MARK = "#del#"


def freed_username(username: str, user_id: int) -> str:
    """构造软删除用户的新用户名，把原用户名释放出来。

    背景：``users.username`` 是 UNIQUE 列，而删除只是把 ``status`` 改成
    ``deleted``（行保留以留审计线索）。若不动用户名，该名字会被永久占死，
    后台/注册都无法再用同一个名字建号。

    做法：追加 ``#del#<id>``（id 唯一，故结果必唯一），并保证总长不超过列宽。

    注意唯一性检查仍覆盖全部行（含已删除）——因为 UNIQUE 跨全部行生效，
    只看未删除行会让 INSERT 撞约束报 500。同名可复用完全依赖本函数。
    """
    suffix = f"{DELETED_USERNAME_MARK}{user_id}"
    keep = max(1, USERNAME_MAX_LEN - len(suffix))
    return f"{(username or '')[:keep]}{suffix}"


# ============ 限流 / 锁定 ============

async def check_ip_registered(ip: str, db: AsyncSession, redis: aioredis.Redis) -> bool:
    """检查该IP是否已注册过账号"""
    cached = await redis.get(f"register_ip:{ip}")
    if cached:
        return True
    result = await db.execute(
        text("SELECT id FROM ip_registry WHERE ip = :ip"), {"ip": ip}
    )
    if result.scalar():
        await redis.set(f"register_ip:{ip}", "1")
        return True
    return False


def _lock_key(account: str) -> str:
    return f"login_lock:{account}"


def _fail_key(account: str) -> str:
    return f"login_fail:{account}"


async def _is_locked(redis: aioredis.Redis, account: str) -> int | None:
    """返回剩余锁定秒数（未锁定返回 None）"""
    ttl = await redis.ttl(_lock_key(account))
    return ttl if ttl and ttl > 0 else None


async def _record_failure(redis: aioredis.Redis, account: str,
                          max_fail: int, lock_minutes: int) -> tuple:
    """
    记录一次失败，返回 (locked, remaining_seconds)。
    在窗口内累计失败达到阈值，将账号/IP 锁定。
    """
    key = _fail_key(account)
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, settings.login_fail_window_minutes * 60)
    if count >= max_fail:
        await redis.setex(_lock_key(account), lock_minutes * 60, "1")
        await redis.delete(key)
        return True, lock_minutes * 60
    return False, 0


async def _clear_failure(redis: aioredis.Redis, account: str):
    await redis.delete(_fail_key(account))


async def check_login_locked(ip: str, redis: aioredis.Redis) -> bool:
    """（兼容旧接口）检查IP是否被登录锁定"""
    return (await _is_locked(redis, f"ip:{ip}")) is not None


async def record_login_fail(ip: str, redis: aioredis.Redis):
    """（兼容旧接口）记录登录失败，IP 5 次后锁定 5 分钟"""
    await _record_failure(redis, f"ip:{ip}",
                          settings.login_max_fail, settings.login_lock_minutes)


async def clear_login_fail(ip: str, redis: aioredis.Redis):
    """（兼容旧接口）登录成功后清除 IP 失败计数"""
    await _clear_failure(redis, f"ip:{ip}")


# ============ 账号状态读取 ============

async def _get_user_by_username(username: str, db: AsyncSession):
    result = await db.execute(
        text("""
            SELECT id, username, password_hash, avatar, points,
                   membership_type, membership_expire, status, register_ip
            FROM users WHERE username = :u AND status != 'deleted'
        """),
        {"u": username},
    )
    return result.mappings().first()


# ============ 注册 ============

async def register_user(
    username: str,
    password: str,
    security_question: str,
    security_answer: str,
    register_ip: str,
    avatar_base64: str | None,
    db: AsyncSession,
    redis: aioredis.Redis,
    avatar_url: str | None = None,
) -> dict:
    """注册新用户，返回 {"user_id", "username", "token"}；失败抛 ValueError(msg, code)"""
    # 覆盖全部行（含已删除）：UNIQUE 跨全部行生效。
    # 已删除用户的用户名在删除时已被释放（freed_username），故同名可重新注册。
    result = await db.execute(
        text("SELECT id FROM users WHERE username = :u"), {"u": username}
    )
    if result.scalar():
        raise ValueError("用户名已存在", 409)

    if await check_ip_registered(register_ip, db, redis):
        raise ValueError("该IP已注册过账号", 409)

    pwd_hash = hash_password(password)
    ans_hash = hash_answer(security_answer)
    avatar = avatar_url or ""

    result = await db.execute(
        text("""
            INSERT INTO users (username, password_hash, security_question, security_answer, avatar, points, register_ip)
            VALUES (:u, :ph, :sq, :sa, :av, 500, :ip)
        """),
        {"u": username, "ph": pwd_hash, "sq": security_question, "sa": ans_hash, "av": avatar, "ip": register_ip},
    )
    user_id = result.lastrowid

    await db.execute(
        text("INSERT INTO ip_registry (ip, user_id) VALUES (:ip, :uid)"),
        {"ip": register_ip, "uid": user_id},
    )
    await db.execute(
        text("""
            INSERT INTO points_log (user_id, change_amount, balance_after, reason, detail)
            VALUES (:uid, 500, 500, 'register', '注册赠送500积分')
        """),
        {"uid": user_id},
    )
    await redis.set(f"register_ip:{register_ip}", "1")

    token = await issue_user_session(user_id, username, redis)
    return {"user_id": user_id, "username": username, "token": token}


# ============ 忘记密码 / 密保重置 ============

async def get_security_question(username: str, db: AsyncSession) -> str:
    """按用户名查询密保问题（仅返回问题，绝不返回答案哈希）。"""
    result = await db.execute(
        text("SELECT security_question FROM users "
             "WHERE username = :u AND status = 'active'"),
        {"u": username},
    )
    row = result.mappings().first()
    if not row:
        raise ValueError("该用户名不存在或账号不可用", 404)
    if not row["security_question"]:
        raise ValueError("该账号未设置密保问题，请联系管理员", 400)
    return row["security_question"]


async def reset_password_by_security(
    username: str,
    security_answer: str,
    new_password: str,
    db: AsyncSession,
    redis: aioredis.Redis,
) -> None:
    """校验密保答案并重置密码；失败按账号限流，防暴力猜测。

    - 连续失败达阈值 → 锁定该账号密码重置一段时间；
    - 成功 → 清除失败计数、撤销该用户全部会话（旧 token 立即失效）。
    """
    account = f"reset:{username}"
    locked_ttl = await _is_locked(redis, account)
    if locked_ttl:
        raise ValueError(f"尝试次数过多，请 {max(1, locked_ttl // 60)} 分钟后再试", 429)

    result = await db.execute(
        text("SELECT id, security_answer FROM users "
             "WHERE username = :u AND status = 'active'"),
        {"u": username},
    )
    row = result.mappings().first()
    if not row or not row["security_answer"]:
        # 用户不存在也记为一次失败，避免通过响应差异枚举用户名
        await _record_failure(redis, account,
                              settings.login_max_fail, settings.login_lock_minutes)
        raise ValueError("用户名或密保答案不正确", 400)

    if not verify_answer(security_answer, row["security_answer"]):
        locked, _ = await _record_failure(redis, account,
                                          settings.login_max_fail,
                                          settings.login_lock_minutes)
        if locked:
            raise ValueError("尝试次数过多，账号已临时锁定，请稍后再试", 429)
        raise ValueError("用户名或密保答案不正确", 400)

    user_id = row["id"]
    await db.execute(
        text("UPDATE users SET password_hash = :ph WHERE id = :uid"),
        {"ph": hash_password(new_password), "uid": user_id},
    )
    await db.commit()

    await _clear_failure(redis, account)
    # 重置密码后旧会话全部失效，强制重新登录
    try:
        from app.core import session as session_store
        await session_store.revoke_all_sessions(redis, user_id)
    except Exception:
        pass


# ============ 登录 ============

async def login_user(
    username: str,
    password: str,
    ip: str,
    db: AsyncSession,
    redis: aioredis.Redis,
) -> dict:
    """
    登录验证（账号级 + IP 级双向限流锁定）。
    返回 {"token", "user": {...}}；失败抛 ValueError(msg, code, extra?)
    """
    username = (username or "").strip()
    account = f"user:{username}"

    # 1. 账号锁定检查
    acct_locked = await _is_locked(redis, account)
    if acct_locked:
        raise ValueError("账号已锁定，请稍后再试", 429, {"remaining": acct_locked, "lock": "account"})
    # 2. IP 锁定检查
    ip_locked = await _is_locked(redis, f"ip:{ip}")
    if ip_locked:
        raise ValueError("IP 已锁定，请稍后再试", 429, {"remaining": ip_locked, "lock": "ip"})

    row = await _get_user_by_username(username, db)

    # 统一凭据错误提示，避免账号枚举
    if not row or not verify_password(password, row["password_hash"]):
        # 同时记录账号级与 IP 级失败
        acct_locked_after, acct_remaining = await _record_failure(
            redis, account, settings.login_max_fail, settings.login_lock_minutes)
        _, _ = await _record_failure(
            redis, f"ip:{ip}", settings.login_max_fail, settings.login_lock_minutes)
        # 持久化失败计数（账号存在时）
        if row is not None:
            await db.execute(
                text("UPDATE users SET login_fail_count = login_fail_count + 1 WHERE id = :uid"),
                {"uid": row["id"]},
            )
        if acct_locked_after:
            raise ValueError("账号已锁定，请稍后再试", 429, {"remaining": acct_remaining, "lock": "account"})
        attempts = await redis.incr(f"login_attempts:{account}")
        await redis.expire(f"login_attempts:{account}", settings.login_fail_window_minutes * 60)
        left = max(0, settings.login_max_fail - attempts)
        raise ValueError("用户名或密码错误", 401, {"attempts_left": left})

    if row["status"] == "disabled":
        raise ValueError("账号已被禁用，请联系管理员", 403)
    if row["status"] == "deleted":
        raise ValueError("账号不存在", 401)

    # 登录成功，清除失败计数并记录登录时间
    await _clear_failure(redis, account)
    await _clear_failure(redis, f"ip:{ip}")
    await db.execute(
        text("UPDATE users SET login_fail_count = 0, last_login_at = NOW() WHERE id = :uid"),
        {"uid": row["id"]},
    )

    token = await issue_user_session(row["id"], row["username"], redis)

    return {
        "token": token,
        "user": {
            "id": row["id"],
            "username": row["username"],
            "avatar": row["avatar"],
            "points": row["points"],
            "membership_type": row["membership_type"],
            "membership_expire": row["membership_expire"].strftime("%Y-%m-%d %H:%M:%S") if row["membership_expire"] else None,
        },
    }


# ============ 会话 ============

async def issue_user_session(user_id: int, username: str, redis: aioredis.Redis) -> str:
    """生成带 jti 的 JWT 并写入 Redis 会话"""
    token = create_jwt(user_id, username)
    payload = decode_own_token(token)
    await session_store.create_session(redis, payload["jti"], {
        "user_id": user_id, "type": "user", "username": username,
    })
    return token


def decode_own_token(token: str) -> dict:
    from app.core.security import decode_jwt
    return decode_jwt(token)


async def refresh_session(token: str, db: AsyncSession, redis: aioredis.Redis) -> str:
    """登录态续期：校验旧 token 与用户状态，签发新 token 并撤销旧会话"""
    from app.core.security import decode_jwt
    try:
        payload = decode_jwt(token)
    except Exception:
        raise ValueError("Token无效或已过期", 401)

    if payload.get("type") != "user":
        raise ValueError("非用户Token", 403)

    jti = payload.get("jti")
    if not jti or not await session_store.is_session_valid(redis, jti, "user"):
        raise ValueError("登录态已失效，请重新登录", 401)

    user_id = int(payload["sub"])
    result = await db.execute(
        text("SELECT id, username, status FROM users WHERE id = :uid"),
        {"uid": user_id},
    )
    row = result.mappings().first()
    if not row or row["status"] == "deleted":
        await session_store.revoke_session(redis, jti)
        raise ValueError("账号不存在", 401)
    if row["status"] == "disabled":
        await session_store.revoke_session(redis, jti)
        raise ValueError("账号已被禁用", 403)

    # 撤销旧会话，签发新会话
    await session_store.revoke_session(redis, jti)
    new_token = await issue_user_session(user_id, row["username"], redis)
    return new_token


async def logout_user(jti: str, redis: aioredis.Redis):
    """登出：撤销该 token 的服务端会话，立即失效"""
    await session_store.revoke_session(redis, jti)