"""认证 API：注册、登录、登出、Token续期、用户信息、头像上传"""
import io
import os
import uuid
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from PIL import Image

from app.database import get_db
from app.redis_client import get_redis
from app.core.deps import get_current_user
from app.core import login_state
from app.config import settings
from app.schemas import (RegisterRequest, LoginRequest, LoginResultRequest,
                         SecurityQuestionRequest, ResetPasswordRequest)
from app.services import auth_service, avatar_service

router = APIRouter(prefix="/api/auth", tags=["认证"])

# 头像落盘/校验已抽到 services/avatar_service（管理后台建号共用）；
# 此处保留别名以兼容既有引用。
AVATAR_DIR = avatar_service.AVATAR_DIR
ALLOWED_AVATAR_TYPES = {"image/jpeg", "image/png", "image/gif"}
MAX_AVATAR_SIZE = avatar_service.MAX_AVATAR_SIZE


def get_client_ip(request: Request) -> str:
    """获取客户端真实IP（优先信任反向代理透传的 X-Real-IP / X-Forwarded-For）"""
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def _verify_login_state(redis, state: str) -> bool:
    """校验登录握手 state 是否有效（非破坏性）。

    require_login_state 关闭时放行空 state；开启时空 state 直接拒绝。
    这里不消费 state——登录/注册成功后还需用它写入 login_result 供客户端取回；
    真正的一次性由 take_login_result 的 GETDEL 与 TTL 保证。
    """
    if not state:
        return not settings.require_login_state
    payload = await login_state.verify_state(redis, state)
    return payload is not None


def validate_image_header(file_bytes: bytes) -> bool:
    """用 Pillow 验证图片文件头是否合法（转发到共享实现，避免两份逻辑）。"""
    return avatar_service.validate_image_header(file_bytes)


@router.post("/state")
async def issue_login_state(
    request: Request,
    platform: str = "",
    version: str = "",
    redis=Depends(get_redis),
):
    """申请一次性登录握手 state（防 login CSRF / 重放）。

    客户端在打开登录页前调用，把返回的 state 拼进登录页 URL 与后续登录请求。
    登录/注册成功后服务端消费该 state，杜绝「伪造回调注入他人 token」。
    """
    state = await login_state.issue_state(
        redis, platform=platform, version=version,
        client_ip=get_client_ip(request),
    )
    return {
        "code": 0,
        "message": "ok",
        "data": {
            "state": state,
            "expires_in": settings.login_state_ttl_seconds,
        },
    }


@router.post("/login_result")
async def post_login_result(
    req: LoginResultRequest,
    redis=Depends(get_redis),
):
    """登录页把登录结果回传服务端（服务端中转回跳）。

    解决「HTTPS 登录页 → http://127.0.0.1 本地回调」被浏览器混合内容/私有网络
    策略拦截的问题：登录页改把结果写到服务端，桌面客户端再轮询取回。
    仅在 state 仍有效时接受，防止伪造 state 注入他人 token。
    """
    ok = await login_state.store_login_result(
        redis, req.state or "", req.token or "", req.user or {},
    )
    if not ok:
        raise HTTPException(status_code=400, detail="登录校验已失效")
    return {"code": 0, "message": "ok"}


@router.get("/login_result")
async def get_login_result(
    state: str = "",
    redis=Depends(get_redis),
):
    """桌面客户端轮询取回登录结果（一次性 GETDEL，取走即删）。

    返回 {code:0, data:{token, user}}；尚未就绪时返回 data:null（客户端继续轮询）。
    """
    if not state:
        raise HTTPException(status_code=400, detail="缺少 state")
    result = await login_state.take_login_result(redis, state)
    return {"code": 0, "data": result}


@router.post("/register")
async def register(
    req: RegisterRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
):
    """用户注册"""
    if req.password != req.password_confirm:
        raise HTTPException(status_code=400, detail="两次密码不一致")

    if not req.security_question.strip():
        raise HTTPException(status_code=400, detail="密保问题不能为空")
    if not req.security_answer.strip():
        raise HTTPException(status_code=400, detail="密保答案不能为空")

    # 登录握手 state 校验（防 login CSRF / 重放）
    if not await _verify_login_state(redis, req.state or ""):
        raise HTTPException(status_code=400, detail="登录校验已失效，请返回应用重新登录")

    ip = get_client_ip(request)

    # 处理 base64 头像（落盘逻辑见 services/avatar_service，管理后台建号共用）
    avatar_url = None
    if req.avatar:
        try:
            avatar_url = avatar_service.save_avatar_base64(req.avatar)
        except avatar_service.AvatarError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception:
            raise HTTPException(status_code=400, detail="头像处理失败")

    try:
        result = await auth_service.register_user(
            username=req.username,
            password=req.password,
            security_question=req.security_question,
            security_answer=req.security_answer,
            register_ip=ip,
            avatar_base64=req.avatar,
            db=db,
            redis=redis,
            avatar_url=avatar_url,
        )
    except ValueError as e:
        msg, code = e.args[0], e.args[1]
        raise HTTPException(status_code=code, detail=msg)

    # 服务端中转回跳：暂存结果供桌面客户端轮询取回
    try:
        await login_state.store_login_result(
            redis, req.state or "",
            (result or {}).get("token", ""),
            (result or {}).get("user") or {},
        )
    except Exception:
        pass

    return {"code": 0, "message": "注册成功", "data": result}


@router.post("/login")
async def login(
    req: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
):
    """用户登录"""
    # 登录握手 state 校验（防 login CSRF / 重放）
    if not await _verify_login_state(redis, req.state or ""):
        raise HTTPException(status_code=400, detail="登录校验已失效，请返回应用重新登录")

    ip = get_client_ip(request)

    try:
        result = await auth_service.login_user(
            username=req.username,
            password=req.password,
            ip=ip,
            db=db,
            redis=redis,
        )
    except ValueError as e:
        msg, code = e.args[0], e.args[1]
        extra = e.args[2] if len(e.args) > 2 else None
        headers = {}
        if extra:
            if extra.get("lock"):
                headers["X-Lock-Type"] = str(extra.get("lock"))
                headers["X-Remaining"] = str(extra.get("remaining", 0))
                raise HTTPException(status_code=code, detail=msg, headers=headers)
            if extra.get("attempts_left") is not None:
                headers["X-Attempts-Left"] = str(extra.get("attempts_left"))
                raise HTTPException(status_code=code, detail=msg, headers=headers)
        raise HTTPException(status_code=code, detail=msg)

    # 服务端中转回跳：暂存结果供桌面客户端轮询取回
    try:
        await login_state.store_login_result(
            redis, req.state or "",
            (result or {}).get("token", ""),
            (result or {}).get("user") or {},
        )
    except Exception:
        pass

    return {"code": 0, "message": "登录成功", "data": result}


@router.post("/refresh")
async def refresh(
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
):
    """Token 续期：携带当前有效 token，签发新 token（滑动续期）"""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="未提供Token")
    token = auth_header.split(" ", 1)[1].strip()

    try:
        new_token = await auth_service.refresh_session(token, db, redis)
    except ValueError as e:
        msg, code = e.args[0], e.args[1]
        raise HTTPException(status_code=code, detail=msg)

    return {"code": 0, "message": "续期成功", "data": {"token": new_token}}


@router.get("/me")
async def get_me(current_user: dict = Depends(get_current_user),
                 db: AsyncSession = Depends(get_db)):
    """获取当前用户信息（附当前公告，供客户端启动时立即展示）。"""
    announcement = ""
    try:
        from sqlalchemy import text as _sql
        r = await db.execute(
            _sql("SELECT config_value FROM system_config WHERE config_key = 'announcement'")
        )
        row = r.mappings().first()
        content = (row["config_value"] if row else "") or ""
        r2 = await db.execute(
            _sql("SELECT config_value FROM system_config WHERE config_key = 'announcement_enabled'")
        )
        row2 = r2.mappings().first()
        enabled = (str(row2["config_value"]) != "0") if row2 else True
        announcement = content if (content and enabled) else ""
    except Exception:
        announcement = ""

    # 签到状态随/me 一并下发：客户端登录后无需额外请求即可渲染签到按钮。
    # 失败不阻断 /me（签到属于附加能力，不能影响登录主流程）。
    checkin: dict = {}
    try:
        from app.services import checkin_service
        checkin = await checkin_service.get_status(current_user["id"], db)
    except Exception:
        checkin = {}

    return {
        "code": 0,
        "data": {
            "id": current_user["id"],
            "username": current_user["username"],
            "avatar": current_user["avatar"],
            "points": current_user["points"],
            "membership_type": current_user["membership_type"],
            "membership_expire": current_user["membership_expire"],
            "announcement": announcement,
            "checkin": checkin,
        },
    }


@router.post("/logout")
async def logout(current_user: dict = Depends(get_current_user), redis=Depends(get_redis)):
    """登出：撤销服务端会话，token 立即失效"""
    await auth_service.logout_user(current_user.get("jti"), redis)
    return {"code": 0, "message": "登出成功"}


@router.post("/avatar")
async def upload_avatar(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """上传头像（multipart/form-data）"""
    if file.content_type not in ALLOWED_AVATAR_TYPES:
        raise HTTPException(status_code=400, detail="仅支持 jpg/png/gif 格式")

    file_bytes = await file.read()
    if len(file_bytes) > MAX_AVATAR_SIZE:
        raise HTTPException(status_code=400, detail="头像大小不能超过2MB")

    if not validate_image_header(file_bytes):
        raise HTTPException(status_code=400, detail="图片文件不合法")

    img = Image.open(io.BytesIO(file_bytes))
    ext_map = {"JPEG": "jpg", "PNG": "png", "GIF": "gif"}
    ext = ext_map.get(img.format, "png")

    filename = f"{uuid.uuid4().hex}.{ext}"
    os.makedirs(AVATAR_DIR, exist_ok=True)
    filepath = os.path.join(AVATAR_DIR, filename)
    with open(filepath, "wb") as f:
        f.write(file_bytes)

    avatar_url = f"/static/uploads/avatars/{filename}"

    from sqlalchemy import text
    await db.execute(
        text("UPDATE users SET avatar = :av WHERE id = :uid"),
        {"av": avatar_url, "uid": current_user["id"]},
    )

    return {"code": 0, "data": {"avatar": avatar_url}}


# ============ 忘记密码 / 密保重置 ============

@router.post("/security_question")
async def security_question(
    req: SecurityQuestionRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
):
    """忘记密码第一步：按用户名返回密保问题（IP 级限流，防枚举）。"""
    ip = get_client_ip(request)
    if await auth_service._is_locked(redis, f"ip:{ip}"):
        raise HTTPException(status_code=429, detail="操作过于频繁，请稍后再试")
    try:
        question = await auth_service.get_security_question(req.username.strip(), db)
    except ValueError as e:
        raise HTTPException(status_code=e.args[1], detail=e.args[0])
    return {"code": 0, "data": {"username": req.username.strip(),
                                "security_question": question}}


@router.post("/reset_password")
async def reset_password(
    req: ResetPasswordRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
):
    """忘记密码第二步：校验密保答案并重置密码（成功后旧会话全部失效）。"""
    if req.new_password != req.password_confirm:
        raise HTTPException(status_code=400, detail="两次输入的密码不一致")
    if len(req.new_password) < 6:
        raise HTTPException(status_code=400, detail="新密码长度至少 6 位")

    ip = get_client_ip(request)
    if await auth_service._is_locked(redis, f"reset_ip:{ip}"):
        raise HTTPException(status_code=429, detail="操作过于频繁，请稍后再试")

    try:
        await auth_service.reset_password_by_security(
            username=req.username.strip(),
            security_answer=req.security_answer.strip(),
            new_password=req.new_password,
            db=db,
            redis=redis,
        )
    except ValueError as e:
        # 记录 IP 级失败，防同一 IP 批量猜测不同账号
        await auth_service._record_failure(redis, f"reset_ip:{ip}",
                                           settings.login_max_fail * 3,
                                           settings.login_lock_minutes)
        raise HTTPException(status_code=e.args[1], detail=e.args[0])

    await auth_service._clear_failure(redis, f"reset_ip:{ip}")
    return {"code": 0, "message": "密码重置成功，请使用新密码登录"}