"""安全模块：密码哈希、JWT 生成与验证"""
from datetime import datetime, timedelta, timezone
import secrets
import uuid
import bcrypt
import jwt
from app.config import settings

# bcrypt 加密盐值成本因子（2^12 = 4096 轮，平衡安全与登录耗时）
# 每次 hash_password 都会用 bcrypt.gensalt() 生成**独立随机盐**并写入哈希串：
#   $2b$12$<22字符盐><31字符摘要>
# 相同密码两次调用产生不同哈希，验证时从哈希串中取出原盐比对，
# 因此无需（也不应）另行保存固定盐值。
BCRYPT_ROUNDS = 12

# 默认/示例密钥黑名单：生产环境禁止用这些值签发 token
_WEAK_JWT_SECRETS = {
    "", "default_secret_change_in_production",
    "change_this_to_a_random_secret_key_in_production",
    "secret", "changeme", "please_change_me",
}


def generate_jwt_secret() -> str:
    """生成 64 字节强随机 JWT 密钥（用于首次部署写入 .env）。"""
    return secrets.token_urlsafe(64)


def assert_jwt_secret_strength():
    """启动自检：require_strong_jwt_secret 开启时拒绝弱/默认密钥启动。

    仅在显式开启时强制，避免开发环境被卡住；生产 .env 应设
    REQUIRE_STRONG_JWT_SECRET=true。
    """
    if not settings.require_strong_jwt_secret:
        return
    secret = (settings.jwt_secret or "").strip()
    if secret in _WEAK_JWT_SECRETS or len(secret) < 32:
        raise RuntimeError(
            "JWT_SECRET 过弱：生产环境必须使用 ≥32 字符的强随机密钥"
            "（可用 `python -c \"import secrets;print(secrets.token_urlsafe(64))\"` 生成）。"
            "请更新 .env 后重启服务。"
        )


def hash_password(password: str) -> str:
    """bcrypt 哈希密码（每次独立随机盐，成本因子 BCRYPT_ROUNDS）"""
    salt = bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """验证密码是否匹配哈希"""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False


def hash_answer(answer: str) -> str:
    """密保答案哈希（同密码）"""
    return hash_password(answer)


def verify_answer(answer: str, answer_hash: str) -> bool:
    """验证密保答案"""
    return verify_password(answer, answer_hash)


def _new_jti() -> str:
    return uuid.uuid4().hex


def create_jwt(user_id: int, username: str, expire_hours: int | None = None) -> str:
    """生成用户 JWT Token（含唯一 jti，便于服务端会话管理/撤销）"""
    now = datetime.now(timezone.utc)
    hours = expire_hours or settings.jwt_expire_hours
    payload = {
        "sub": str(user_id),
        "username": username,
        "jti": _new_jti(),
        "exp": now + timedelta(hours=hours),
        "iat": now,
        "type": "user",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_admin_jwt(admin_id: int, username: str, expire_hours: int | None = None) -> str:
    """生成管理员 JWT Token（含唯一 jti）"""
    now = datetime.now(timezone.utc)
    hours = expire_hours or 2
    payload = {
        "sub": str(admin_id),
        "username": username,
        "jti": _new_jti(),
        "exp": now + timedelta(hours=hours),
        "iat": now,
        "type": "admin",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_jwt(token: str) -> dict:
    """解码 JWT，返回 payload（含 jti）；无效或过期则抛出异常"""
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])