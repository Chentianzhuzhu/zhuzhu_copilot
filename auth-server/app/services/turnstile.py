"""Cloudflare Turnstile 人机验证（应用账户注册/登录必须通过）。

分工：
  * 站点密钥（site key）是公开信息，由 ``GET /api/auth/captcha`` 下发给登录页，
    浏览器用它渲染验证组件；
  * 密钥（secret key）只在服务端使用，取自环境变量 ``TURNSTILE_SECRET_KEY``。

设计原则：**fail-closed**。token 为空、已过期、已用过，或校验服务不可用，
一律判失败——验证不通过就不允许注册/登录。

零新依赖：用标准库 urllib 发请求，扔到线程池里跑，不阻塞事件循环。
"""
import asyncio
import json
import logging
import urllib.parse
import urllib.request

from app.config import settings

logger = logging.getLogger(__name__)

VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
TIMEOUT_SECONDS = 8


def site_key() -> str:
    """公开站点密钥（前端渲染组件用）。"""
    return (settings.turnstile_site_key or "").strip()


def enabled() -> bool:
    """是否启用校验（关闭时 verify 直接放行，便于本地开发）。"""
    return bool(settings.turnstile_enabled)


def _post_verify(secret: str, token: str, remote_ip: str) -> bool:
    form = {"secret": secret, "response": token}
    if remote_ip:
        form["remoteip"] = remote_ip
    data = urllib.parse.urlencode(form).encode("utf-8")
    req = urllib.request.Request(
        VERIFY_URL, data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
        body = resp.read().decode("utf-8", "replace")
    obj = json.loads(body or "{}")
    if not obj.get("success"):
        # 只记错误码，绝不记 token 本身
        logger.info("Turnstile 校验未通过：%s", obj.get("error-codes"))
        return False
    return True


async def verify(token: str, remote_ip: str = "") -> bool:
    """校验前端提交的一次性 token。

    返回 True 仅当：未启用校验，或 Cloudflare 明确返回 success=true。
    """
    if not enabled():
        return True
    secret = (settings.turnstile_secret_key or "").strip()
    token = (token or "").strip()
    if not secret:
        # 启用却没配密钥：宁可拒绝也不静默放行（否则等于没有验证）
        logger.error("Turnstile 已启用但未配置 TURNSTILE_SECRET_KEY，注册/登录被拒绝")
        return False
    if not token:
        return False
    try:
        return await asyncio.to_thread(_post_verify, secret, token, remote_ip)
    except Exception as e:  # 网络/解析失败一律拒绝，让用户重试
        logger.warning("Turnstile 校验请求失败：%s", e)
        return False
