"""管理员 API：登录、用户管理、订单管理、收款码管理"""
import io
import os
import uuid
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query, Request
from sqlalchemy import text, func
from sqlalchemy.ext.asyncio import AsyncSession
from PIL import Image

from app.config import settings
from app.database import get_db
from app.redis_client import get_redis
from app.core.deps import get_current_admin
from app.core.security import (verify_password, create_admin_jwt, decode_jwt,
                               hash_password, hash_answer)
from app.core import session as session_store
from app.schemas import (AdminLoginRequest, AdminAdjustPointsRequest,
                         AdminBatchUsersRequest, AdminBatchOrdersRequest,
                         AdminSetMembershipRequest, AdminBatchPointsRequest,
                         AdminBatchMembershipRequest, AdminAnnouncementRequest,
                         AdminCreateUserRequest, DEFAULT_INITIAL_POINTS)
from app.services.ws_manager import ws_manager
from app.services import order_service, avatar_service, auth_service
from app.services.order_service import MEMBERSHIP_TIERS

router = APIRouter(prefix="/api/admin", tags=["管理员"])

QRCODE_DIR = os.path.join("static", "uploads", "qrcodes")
ALLOWED_IMG_TYPES = {"image/jpeg", "image/png"}
MAX_IMG_SIZE = 2 * 1024 * 1024


def get_client_ip(request: Request) -> str:
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.post("/login")
async def admin_login(
    req: AdminLoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
):
    """管理员登录（含失败限流与会话管理）"""
    account = f"admin:{req.username}"
    ip = get_client_ip(request)

    # 账号级 / IP 级锁定检查
    for key, label in ((account, "管理员账号"), (f"ip:{ip}", "IP")):
        ttl = await redis.ttl(f"login_lock:{key}")
        if ttl and ttl > 0:
            raise HTTPException(status_code=429, detail=f"{label}已锁定，请稍后再试")

    result = await db.execute(
        text("SELECT id, username, password_hash FROM admins WHERE username = :u"),
        {"u": req.username},
    )
    row = result.mappings().first()

    if not row or not verify_password(req.password, row["password_hash"]):
        # 记录失败并可能锁定
        for key in (account, f"ip:{ip}"):
            count = await redis.incr(f"login_fail:{key}")
            if count == 1:
                await redis.expire(f"login_fail:{key}", settings.login_fail_window_minutes * 60)
            if count >= settings.admin_max_fail:
                await redis.setex(f"login_lock:{key}", settings.admin_lock_minutes * 60, "1")
                await redis.delete(f"login_fail:{key}")
                raise HTTPException(status_code=429, detail="登录失败次数过多，管理员账号已锁定15分钟")
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    # 登录成功：清理失败计数
    for key in (account, f"ip:{ip}"):
        await redis.delete(f"login_fail:{key}")

    token = create_admin_jwt(row["id"], row["username"])
    payload = decode_jwt(token)
    await session_store.create_session(redis, payload["jti"], {
        "user_id": row["id"], "type": "admin", "username": row["username"],
    })
    return {"code": 0, "data": {"token": token}}


@router.get("/users")
async def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str = Query(""),
    admin: dict = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """用户列表（分页+搜索）"""
    offset = (page - 1) * page_size

    # 构建查询
    where = "WHERE status != 'deleted'"
    params = {}
    if keyword:
        where += " AND username LIKE :kw"
        params["kw"] = f"%{keyword}%"

    # 总数
    total_result = await db.execute(
        text(f"SELECT COUNT(*) FROM users {where}"), params
    )
    total = total_result.scalar()

    # 列表
    list_result = await db.execute(
        text(f"""
            SELECT id, username, avatar, points, membership_type, membership_expire,
                   status, register_ip, created_at
            FROM users {where}
            ORDER BY id DESC
            LIMIT :limit OFFSET :offset
        """),
        {**params, "limit": page_size, "offset": offset},
    )
    rows = list_result.mappings().all()

    user_list = []
    for row in rows:
        exp = row["membership_expire"]
        user_list.append({
            "id": row["id"],
            "username": row["username"],
            "avatar": row["avatar"],
            "points": row["points"],
            "membership_type": row["membership_type"],
            "membership_expire": exp.strftime("%Y-%m-%d") if exp else "",
            "status": row["status"],
            "register_ip": row["register_ip"],
            "created_at": row["created_at"].strftime("%Y-%m-%d %H:%M:%S") if row["created_at"] else None,
        })

    return {"code": 0, "data": {"total": total, "page": page, "list": user_list}}


@router.post("/users")
async def create_user(
    req: AdminCreateUserRequest,
    admin: dict = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
):
    """管理后台新增账号（用户名/ 密码 / 头像 / 会员 / 积分）。

    与用户自助注册的差异：**不受「同一 IP 只能注册一个」限制**（后台代建账号
    本就该绕过该限制），且密保可留空——留空时用固定占位哈希，用户日后仍可通过
    登录页的密保流程自行设置。
    """
    username = (req.username or "").strip()
    if not username:
        raise HTTPException(status_code=400, detail="用户名不能为空")

    mtype = (req.membership_type or "free").strip().lower()
    if mtype not in MEMBERSHIP_TIERS:
        raise HTTPException(
            status_code=400,
            detail=f"会员类型不支持（应为 {'/'.join(MEMBERSHIP_TIERS)}）")

    exists = await db.execute(
        text("SELECT id FROM users WHERE username = :u"), {"u": username})
    if exists.scalar():
        raise HTTPException(status_code=409, detail="用户名已存在")

    # 到期日：复用批量设置会员的归一化与校验（YYYY-MM-DD / ISO）
    expire_val, _keep = _parse_expire(req.membership_expire, mtype, None)

    # 头像：base64 落盘（校验规则与用户注册完全一致）
    try:
        avatar_url = avatar_service.save_avatar_base64(req.avatar or "")
    except avatar_service.AvatarError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        raise HTTPException(status_code=400, detail="头像处理失败")

    points = DEFAULT_INITIAL_POINTS if req.points is None else int(req.points)
    sq = (req.security_question or "").strip() or "管理员创建"
    sa_raw = (req.security_answer or "").strip() or sq
    register_ip = (req.register_ip or "").strip() or None

    result = await db.execute(
        text("""
            INSERT INTO users
                (username, password_hash, security_question, security_answer,
                 avatar, points, membership_type, membership_expire, register_ip)
            VALUES (:u, :ph, :sq, :sa, :av, :pts, :mt, :exp, :ip)
        """),
        {
            "u": username,
            "ph": hash_password(req.password),
            "sq": sq,
            "sa": hash_answer(sa_raw),
            "av": avatar_url or "",
            "pts": points,
            "mt": mtype,
            "exp": expire_val,
            "ip": register_ip,
        },
    )
    user_id = result.lastrowid

    # 注册 IP 入登记表（与自助注册一致，避免该 IP 之后仍能自助注册）
    if register_ip:
        await db.execute(
            text("INSERT IGNORE INTO ip_registry (ip, user_id) VALUES (:ip, :uid)"),
            {"ip": register_ip, "uid": user_id},
        )
        await redis.set(f"register_ip:{register_ip}", "1")

    # 初始积分流水（points=0 时不记流水，避免噪声）
    if points:
        await db.execute(
            text("""
                INSERT INTO points_log (user_id, change_amount, balance_after, reason, detail)
                VALUES (:uid, :ca, :ca, 'admin_create', :detail)
            """),
            {"uid": user_id, "ca": points, "detail": "管理员创建账号赠送积分"},
        )

    await db.commit()
    await redis.delete(f"points_cache:{user_id}")

    return {"code": 0, "data": {
        "id": user_id, "username": username,
        "points": points, "membership_type": mtype,
        "membership_expire": _expire_str(expire_val),
        "avatar": avatar_url or "",
    }}


@router.post("/users/{user_id}/disable")
async def disable_user(
    user_id: int,
    admin: dict = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
):
    """禁用用户：撤销全部会话 + WebSocket 强制下线（幂等，重复禁用不报错）。"""
    result = await db.execute(
        text("SELECT id, username FROM users WHERE id = :uid AND status != 'deleted'"),
        {"uid": user_id},
    )
    if not result.mappings().first():
        raise HTTPException(status_code=404, detail="用户不存在")

    await db.execute(
        text("UPDATE users SET status = 'disabled' WHERE id = :uid"),
        {"uid": user_id},
    )

    # 先推送强制下线（此时连接仍在，保证能收到），再撤销会话
    await ws_manager.send_to_user(user_id, {
        "type": "force_logout",
        "data": {"reason": "账号已被管理员禁用"},
    })

    # 撤销该用户全部服务端会话，API/WS 立即拦截
    from app.core import session as sess_store
    await sess_store.revoke_all_sessions(redis, user_id)

    return {"code": 0, "message": "用户已禁用"}


@router.post("/users/{user_id}/enable")
async def enable_user(
    user_id: int,
    admin: dict = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """解封用户：恢复 active 后即可立即重新登录使用。"""
    result = await db.execute(
        text("SELECT id, status FROM users WHERE id = :uid AND status != 'deleted'"),
        {"uid": user_id},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="用户不存在")
    if row["status"] != "disabled":
        # 已是正常状态：幂等返回成功，避免后台重复点击报错
        return {"code": 0, "message": "用户当前未被禁用"}

    await db.execute(
        text("UPDATE users SET status = 'active' WHERE id = :uid"),
        {"uid": user_id},
    )

    return {"code": 0, "message": "用户已解封"}


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: int,
    admin: dict = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
):
    """删除用户（软删除）：撤销全部会话并推送强制下线。

    同时**释放用户名**（追加 ``#del#<id>`` 后缀）：username 是 UNIQUE 列，
    若原样保留，这个用户名就被永久占用，之后无法再用同名建号。
    """
    result = await db.execute(
        text("SELECT id, username FROM users WHERE id = :uid AND status != 'deleted'"),
        {"uid": user_id},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="用户不存在")

    await db.execute(
        text("UPDATE users SET status = 'deleted', username = :un WHERE id = :uid"),
        {"uid": user_id, "un": auth_service.freed_username(row["username"], user_id)},
    )

    # 先推送强制下线（连接仍在），再撤销会话
    await ws_manager.send_to_user(user_id, {
        "type": "force_logout",
        "data": {"reason": "账号已被删除"},
    })

    # 撤销该用户全部服务端会话（API/WS 立即拦截）
    from app.core import session as sess_store
    await sess_store.revoke_all_sessions(redis, user_id)

    return {"code": 0, "message": "用户已删除"}


@router.post("/users/batch")
async def batch_users(
    req: AdminBatchUsersRequest,
    admin: dict = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
):
    """批量操作用户：action ∈ {disable, enable, delete}。

    - disable/delete 会推送 WebSocket 强制下线并撤销全部会话；
    - enable 恢复为 active，用户可立即重新登录；
    - 幂等：对已处于目标状态/已删除的用户跳过，不报错；
    - 返回 {success, failed, skipped, ids}。
    """
    action = (req.action or "").strip().lower()
    if action not in ("disable", "enable", "delete"):
        raise HTTPException(status_code=400, detail="不支持的批量操作类型")
    ids = list(dict.fromkeys(int(i) for i in (req.ids or [])))
    if not ids:
        raise HTTPException(status_code=400, detail="请至少选择一个用户")

    from app.core import session as sess_store

    success, failed, skipped = 0, 0, 0
    for uid in ids:
        try:
            result = await db.execute(
                text("SELECT id, status, username FROM users "
                     "WHERE id = :uid AND status != 'deleted'"),
                {"uid": uid},
            )
            row = result.mappings().first()
            if not row:
                skipped += 1
                continue

            if action == "enable":
                if row["status"] == "active":
                    skipped += 1
                    continue
                await db.execute(
                    text("UPDATE users SET status = 'active' WHERE id = :uid"),
                    {"uid": uid},
                )
                success += 1
            elif action == "disable":
                await db.execute(
                    text("UPDATE users SET status = 'disabled' WHERE id = :uid"),
                    {"uid": uid},
                )
                await ws_manager.send_to_user(uid, {
                    "type": "force_logout",
                    "data": {"reason": "账号已被管理员禁用"},
                })
                await sess_store.revoke_all_sessions(redis, uid)
                success += 1
            else:  # delete
                # 与单个删除同款：释放用户名，否则该名字被永久占用
                await db.execute(
                    text("UPDATE users SET status = 'deleted', username = :un "
                         "WHERE id = :uid"),
                    {"uid": uid,
                     "un": auth_service.freed_username(row["username"], uid)},
                )
                await ws_manager.send_to_user(uid, {
                    "type": "force_logout",
                    "data": {"reason": "账号已被删除"},
                })
                await sess_store.revoke_all_sessions(redis, uid)
                success += 1
        except Exception:
            failed += 1

    await db.commit()
    return {"code": 0, "data": {
        "success": success,
        "failed": failed,
        "skipped": skipped,
        "ids": ids,
    }}

# ==================== 批量设置：积分 / 会员（分片并发推送） ====================

# 推送分片大小：每批并发推送的用户数。控制并发上限，避免一次性打开过多
# 协程 / 连接导致事件循环抖动（高并发下的稳定性与吞吐折中）。
PUSH_CHUNK_SIZE = 50


async def _chunked_push(user_ids: list, message_factory) -> int:
    """分片并发向多个用户推送 WebSocket 消息，返回**在线**触达的用户数。

    - 按 ``PUSH_CHUNK_SIZE`` 切片，每片内用 ``asyncio.gather`` 并发推送；
    - 片与片之间顺序执行，形成背压，避免瞬时打爆事件循环；
    - ``message_factory(uid)`` 可按用户构造不同消息（如携带各自的新余额）；
    - 单个用户推送异常不影响其他用户（gather 已带 return_exceptions）；
    - 消息经 Redis 广播跨 worker 投递，因此计数以「在线用户数」为准
      （``ws_manager.count_online``），而非本地投递返回值。
    """
    import asyncio
    ids = [int(u) for u in user_ids]
    for i in range(0, len(ids), PUSH_CHUNK_SIZE):
        chunk = ids[i:i + PUSH_CHUNK_SIZE]
        await asyncio.gather(
            *[ws_manager.send_to_user(uid, message_factory(uid)) for uid in chunk],
            return_exceptions=True,
        )
        # 让出控制权：长批量下保持事件循环对其他请求的响应性
        await asyncio.sleep(0)
    # 在线人数：跨 worker 统计（Redis 集合），真实反映实际触达
    try:
        return await ws_manager.count_online(ids)
    except Exception:
        return 0


def _parse_expire(expire_raw: str, mtype: str, cur_expire):
    """解析会员到期日，返回 (expire_val, keep_expire)。

    - 非空：归一化并校验，返回 (归一值, False)；
    - 空 + free：清空，返回 (None, False)；
    - 空 + 付费：保留原值，返回 (None, True)（调用方用 cur_expire）。
    """
    raw = (expire_raw or "").strip()
    if raw:
        norm = raw.replace("T", " ").replace("Z", "").strip()
        if len(norm) == 10:      # YYYY-MM-DD
            norm = norm + " 23:59:59"
        elif len(norm) == 16:    # YYYY-MM-DD HH:MM
            norm = norm + ":00"
        try:
            from datetime import datetime
            datetime.strptime(norm, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            raise HTTPException(status_code=400, detail="到期日格式不正确")
        return norm, False
    if mtype == "free":
        return None, False
    return None, True


def _expire_str(v) -> str:
    """到期日 → 对外字符串（YYYY-MM-DD）。"""
    if v is None:
        return ""
    try:
        return v.strftime("%Y-%m-%d")
    except Exception:
        return str(v)[:10]


@router.post("/users/batch/points")
async def batch_adjust_points(
    req: AdminBatchPointsRequest,
    admin: dict = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
):
    """批量调整积分（mode ∈ {delta, set}），并对在线用户分片并发实时推送。

    - delta：余额 += amount（正增负减），不允许调成负数（跳过并计入 skipped）；
    - set：余额 = amount（不得为负）；
    - 返回 {success, failed, skipped, pushed, ids}
    """
    mode = (req.mode or "delta").strip().lower()
    if mode not in ("delta", "set"):
        raise HTTPException(status_code=400, detail="mode 仅支持 delta / set")
    ids = list(dict.fromkeys(int(i) for i in (req.ids or [])))
    if not ids:
        raise HTTPException(status_code=400, detail="请至少选择一个用户")

    reason = req.reason or ("admin_set_points" if mode == "set" else "admin_batch_points")

    success, failed, skipped = 0, 0, 0
    changed = {}            # uid -> (新余额, 变化量)
    for uid in ids:
        try:
            result = await db.execute(
                text("SELECT id, points FROM users WHERE id = :uid AND status != 'deleted'"),
                {"uid": uid},
            )
            row = result.mappings().first()
            if not row:
                skipped += 1
                continue

            cur = int(row["points"] or 0)
            new_balance = int(req.amount) if mode == "set" else cur + int(req.amount)
            if new_balance < 0:
                skipped += 1
                continue
            if mode == "delta" and int(req.amount) == 0:
                skipped += 1
                continue

            await db.execute(
                text("UPDATE users SET points = :pts WHERE id = :uid"),
                {"pts": new_balance, "uid": uid},
            )
            delta = new_balance - cur
            await db.execute(
                text("""
                    INSERT INTO points_log (user_id, change_amount, balance_after, reason, detail)
                    VALUES (:uid, :ca, :ba, :reason, :detail)
                """),
                {"uid": uid, "ca": delta, "ba": new_balance, "reason": reason,
                 "detail": req.detail or "管理员批量调整"},
            )
            try:
                await redis.delete(f"points_cache:{uid}")
            except Exception:
                pass
            changed[uid] = (new_balance, delta)
            success += 1
        except Exception:
            failed += 1

    await db.commit()

    # 分片并发推送：每个用户收到自己的新余额
    def _mk(uid):
        nb, delta = changed.get(uid, (None, 0))
        return {
            "type": "points_update",
            "data": {"points": nb, "change": delta, "reason": reason},
        }

    pushed = await _chunked_push(list(changed.keys()), _mk) if changed else 0
    return {"code": 0, "data": {
        "success": success, "failed": failed, "skipped": skipped,
        "pushed": pushed, "ids": ids,
    }}


@router.post("/users/batch/membership")
async def batch_set_membership(
    req: AdminBatchMembershipRequest,
    admin: dict = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """批量设置会员类型（可选到期日），并对在线用户分片并发实时推送。

    - membership_type ∈ {free, pro, max}
    - membership_expire 留空：free 清空、付费保留原到期日
    - 返回 {success, failed, skipped, pushed, ids}
    """
    mtype = (req.membership_type or "").strip().lower()
    if mtype not in ("free", "pro", "max"):
        raise HTTPException(status_code=400, detail="会员类型不支持（应为 free/pro/max）")
    ids = list(dict.fromkeys(int(i) for i in (req.ids or [])))
    if not ids:
        raise HTTPException(status_code=400, detail="请至少选择一个用户")

    success, failed, skipped = 0, 0, 0
    changed = {}            # uid -> (mtype, expire_str)
    for uid in ids:
        try:
            result = await db.execute(
                text("SELECT id, membership_type, membership_expire FROM users "
                     "WHERE id = :uid AND status != 'deleted'"),
                {"uid": uid},
            )
            row = result.mappings().first()
            if not row:
                skipped += 1
                continue

            expire_val, keep = _parse_expire(req.membership_expire, mtype,
                                             row["membership_expire"])
            if keep:
                await db.execute(
                    text("UPDATE users SET membership_type = :mt WHERE id = :uid"),
                    {"mt": mtype, "uid": uid},
                )
                new_expire = row["membership_expire"]
            else:
                await db.execute(
                    text("UPDATE users SET membership_type = :mt, membership_expire = :ex "
                         "WHERE id = :uid"),
                    {"mt": mtype, "ex": expire_val, "uid": uid},
                )
                new_expire = expire_val
            changed[uid] = (mtype, _expire_str(new_expire))
            success += 1
        except HTTPException:
            raise
        except Exception:
            failed += 1

    await db.commit()

    def _mk(uid):
        mt, ex = changed.get(uid, (mtype, ""))
        return {
            "type": "membership_update",
            "data": {"membership_type": mt, "membership_expire": ex},
        }

    pushed = await _chunked_push(list(changed.keys()), _mk) if changed else 0
    return {"code": 0, "data": {
        "success": success, "failed": failed, "skipped": skipped,
        "pushed": pushed, "ids": ids,
    }}


@router.post("/users/{user_id}/points")
async def adjust_points(
    user_id: int,
    req: AdminAdjustPointsRequest,
    admin: dict = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
):
    """调整用户积分（正增负减）"""
    result = await db.execute(
        text("SELECT id, points FROM users WHERE id = :uid AND status != 'deleted'"),
        {"uid": user_id},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="用户不存在")

    # 计算新余额
    new_balance = row["points"] + req.amount
    if new_balance < 0:
        raise HTTPException(status_code=400, detail="调整后积分不能为负数")

    # 更新积分
    await db.execute(
        text("UPDATE users SET points = :pts WHERE id = :uid"),
        {"pts": new_balance, "uid": user_id},
    )

    # 写流水
    await db.execute(
        text("""
            INSERT INTO points_log (user_id, change_amount, balance_after, reason, detail)
            VALUES (:uid, :ca, :ba, :reason, :detail)
        """),
        {
            "uid": user_id,
            "ca": req.amount,
            "ba": new_balance,
            "reason": req.reason,
            "detail": req.detail or "管理员调整",
        },
    )

    # 清除积分缓存
    await redis.delete(f"points_cache:{user_id}")

    # WebSocket 推送
    await ws_manager.send_to_user(user_id, {
        "type": "points_update",
        "data": {
            "points": new_balance,
            "change": req.amount,
            "reason": req.reason,
        },
    })

    return {"code": 0, "message": "积分调整成功", "data": {"new_balance": new_balance}}


@router.post("/users/{user_id}/membership")
async def set_membership(
    user_id: int,
    req: AdminSetMembershipRequest,
    admin: dict = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
):
    """设置用户会员类型（可选同时设置到期日），并 WebSocket 实时推送客户端。

    - membership_type ∈ {free, pro, max}
    - membership_expire：'YYYY-MM-DD' / ISO / 'YYYY-MM-DD HH:MM:SS'；
      留空且 type=free → 清空到期日；留空且 type≠free → 保持原有到期日不变。
    """
    mtype = (req.membership_type or "").strip().lower()
    if mtype not in ("free", "pro", "max"):
        raise HTTPException(status_code=400, detail="会员类型不支持（应为 free/pro/max）")

    result = await db.execute(
        text("SELECT id, membership_type, membership_expire FROM users "
             "WHERE id = :uid AND status != 'deleted'"),
        {"uid": user_id},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="用户不存在")

    # 解析到期日
    expire_raw = (req.membership_expire or "").strip()
    expire_val = None            # 传给 SQL 的值
    keep_expire = False          # True 表示不修改到期日
    if expire_raw:
        norm = expire_raw.replace("T", " ").replace("Z", "").strip()
        if len(norm) == 10:      # YYYY-MM-DD
            norm = norm + " 23:59:59"
        elif len(norm) == 16:    # YYYY-MM-DD HH:MM
            norm = norm + ":00"
        try:
            from datetime import datetime
            datetime.strptime(norm, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            raise HTTPException(status_code=400, detail="到期日格式不正确")
        expire_val = norm
    elif mtype == "free":
        expire_val = None        # free 清空到期
    else:
        keep_expire = True       # 付费类型且未填 → 保留原到期日

    if keep_expire:
        await db.execute(
            text("UPDATE users SET membership_type = :mt WHERE id = :uid"),
            {"mt": mtype, "uid": user_id},
        )
        new_expire = row["membership_expire"]
    else:
        await db.execute(
            text("UPDATE users SET membership_type = :mt, membership_expire = :ex WHERE id = :uid"),
            {"mt": mtype, "ex": expire_val, "uid": user_id},
        )
        new_expire = expire_val

    await db.commit()

    # 到期日的对外字符串
    expire_str = ""
    if new_expire is not None:
        try:
            expire_str = new_expire.strftime("%Y-%m-%d")
        except Exception:
            expire_str = str(new_expire)[:10]

    # WebSocket 实时推送（客户端据此刷新会员类型/到期日）
    await ws_manager.send_to_user(user_id, {
        "type": "membership_update",
        "data": {
            "membership_type": mtype,
            "membership_expire": expire_str,
        },
    })

    return {"code": 0, "message": "会员类型已更新",
            "data": {"membership_type": mtype, "membership_expire": expire_str}}


@router.post("/qrcode")
async def set_qrcode(
    type: str = Form(...),
    file: UploadFile = File(...),
    admin: dict = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """设置收款码（上传图片替换）"""
    if type not in ("alipay", "wechat"):
        raise HTTPException(status_code=400, detail="type 必须为 alipay 或 wechat")

    if file.content_type not in ALLOWED_IMG_TYPES:
        raise HTTPException(status_code=400, detail="仅支持 jpg/png 格式")

    file_bytes = await file.read()
    if len(file_bytes) > MAX_IMG_SIZE:
        raise HTTPException(status_code=400, detail="图片大小不能超过2MB")

    # Pillow 验证
    try:
        Image.open(io.BytesIO(file_bytes)).verify()
    except Exception:
        raise HTTPException(status_code=400, detail="图片文件不合法")

    # 保存
    filename = f"{type}_{uuid.uuid4().hex}.png"
    os.makedirs(QRCODE_DIR, exist_ok=True)
    filepath = os.path.join(QRCODE_DIR, filename)
    with open(filepath, "wb") as f:
        f.write(file_bytes)

    qrcode_url = f"/static/uploads/qrcodes/{filename}"

    # 更新系统配置
    config_key = f"{type}_qrcode"
    await db.execute(
        text("""
            INSERT INTO system_config (config_key, config_value)
            VALUES (:k, :v)
            ON DUPLICATE KEY UPDATE config_value = :v
        """),
        {"k": config_key, "v": qrcode_url},
    )

    return {"code": 0, "data": {"qrcode_url": qrcode_url}}


@router.post("/orders/{order_id}/confirm")
async def confirm_order(
    order_id: str,
    admin: dict = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
):
    """确认订单到账（手动核对）"""
    try:
        result = await order_service.confirm_order(order_id, db, redis)
    except ValueError as e:
        msg, code = e.args[0], e.args[1]
        raise HTTPException(status_code=code, detail=msg)

    return {"code": 0, "data": result}


@router.get("/orders")
async def list_orders(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str = Query(""),
    admin: dict = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """订单列表（分页+状态筛选）"""
    offset = (page - 1) * page_size

    where = "WHERE 1=1"
    params = {}
    if status:
        where += " AND o.status = :st"
        params["st"] = status

    # 总数
    total_result = await db.execute(
        text(f"SELECT COUNT(*) FROM orders o {where}"), params
    )
    total = total_result.scalar()

    # 列表（关联用户名）
    list_result = await db.execute(
        text(f"""
            SELECT o.*, u.username
            FROM orders o
            LEFT JOIN users u ON o.user_id = u.id
            {where}
            ORDER BY o.created_at DESC
            LIMIT :limit OFFSET :offset
        """),
        {**params, "limit": page_size, "offset": offset},
    )
    rows = list_result.mappings().all()

    order_list = []
    for row in rows:
        order_list.append({
            "order_id": row["id"],
            "username": row["username"],
            "user_id": row["user_id"],
            "order_type": row["order_type"],
            "product_id": row["product_id"],
            "amount": float(row["amount"]),
            "points_amount": row["points_amount"],
            "pay_method": row["pay_method"],
            "status": row["status"],
            "created_at": row["created_at"].strftime("%Y-%m-%d %H:%M:%S") if row["created_at"] else None,
            "paid_at": row["paid_at"].strftime("%Y-%m-%d %H:%M:%S") if row["paid_at"] else None,
        })

    return {"code": 0, "data": {"total": total, "page": page, "list": order_list}}


@router.delete("/orders/{order_id}")
async def delete_order(
    order_id: str,
    admin: dict = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """删除单个订单（物理删除）。

    注意：仅删除订单记录本身，不回滚已发放的积分/会员（已支付订单若需回滚，
    请先手动调整积分）。
    """
    result = await db.execute(
        text("SELECT id FROM orders WHERE id = :oid"),
        {"oid": order_id},
    )
    if not result.mappings().first():
        raise HTTPException(status_code=404, detail="订单不存在")

    await db.execute(text("DELETE FROM orders WHERE id = :oid"), {"oid": order_id})
    await db.commit()
    return {"code": 0, "message": "订单已删除"}


@router.post("/orders/batch")
async def batch_delete_orders(
    req: AdminBatchOrdersRequest,
    admin: dict = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """批量删除订单（物理删除）。

    body: {ids: ["ORD...", ...]}
    - 去重后逐条删除，不存在的订单计入 skipped；
    - 返回 {success, failed, skipped, ids}。
    """
    ids = list(dict.fromkeys(str(i).strip() for i in (req.ids or []) if str(i).strip()))
    if not ids:
        raise HTTPException(status_code=400, detail="请至少选择一个订单")

    success, failed, skipped = 0, 0, 0
    for oid in ids:
        try:
            result = await db.execute(
                text("SELECT id FROM orders WHERE id = :oid"), {"oid": oid}
            )
            if not result.mappings().first():
                skipped += 1
                continue
            await db.execute(text("DELETE FROM orders WHERE id = :oid"), {"oid": oid})
            success += 1
        except Exception:
            failed += 1

    await db.commit()
    return {"code": 0, "data": {
        "success": success,
        "failed": failed,
        "skipped": skipped,
        "ids": ids,
    }}


# ==================== 系统公告 ====================

ANNOUNCE_KEY = "announcement"
ANNOUNCE_MAX = 200       # 公告最长字符数（顶栏展示，过长会挤压其他元素）


async def _read_config(db: AsyncSession, key: str, default=None):
    """读取 system_config 单项。"""
    result = await db.execute(
        text("SELECT config_value FROM system_config WHERE config_key = :k"),
        {"k": key},
    )
    row = result.mappings().first()
    return row["config_value"] if row and row["config_value"] is not None else default


async def _write_config(db: AsyncSession, key: str, value: str):
    """写入 system_config 单项（upsert）。"""
    await db.execute(
        text("""
            INSERT INTO system_config (config_key, config_value)
            VALUES (:k, :v)
            ON DUPLICATE KEY UPDATE config_value = :v
        """),
        {"k": key, "v": value},
    )


@router.get("/announcement")
async def get_announcement(
    admin: dict = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """读取当前公告（管理端编辑用）。"""
    content = await _read_config(db, ANNOUNCE_KEY, "")
    enabled_raw = await _read_config(db, ANNOUNCE_KEY + "_enabled", "1")
    return {"code": 0, "data": {
        "content": content or "",
        "enabled": str(enabled_raw) != "0",
    }}


@router.post("/announcement")
async def set_announcement(
    req: AdminAnnouncementRequest,
    admin: dict = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """设置系统公告并实时推送给所有在线用户。

    - content 为空字符串 → 清除公告（推空内容，客户端隐藏公告位）；
    - enabled=False → 保留内容但不展示；
    - 推送经 ``ws_manager.broadcast`` 走 Redis 广播，**跨 worker** 触达全部
      在线用户（多 worker 部署下不再出现「只推给本进程连接」的漏推）。
    """
    content = (req.content or "").strip()
    if len(content) > ANNOUNCE_MAX:
        raise HTTPException(status_code=400, detail=f"公告最长 {ANNOUNCE_MAX} 字")

    await _write_config(db, ANNOUNCE_KEY, content)
    await _write_config(db, ANNOUNCE_KEY + "_enabled", "1" if req.enabled else "0")
    await db.commit()

    shown = bool(content) and bool(req.enabled)
    msg = {"type": "announcement", "data": {
        "content": content if shown else "",
        "enabled": bool(req.enabled),
    }}
    # 广播到所有 worker 的在线连接；计数取「广播前在线连接数」用于回显
    try:
        online_before = int(await ws_manager.total_online_connections())
    except Exception:
        online_before = 0
    await ws_manager.broadcast(msg)

    return {"code": 0, "message": "公告已更新", "data": {
        "content": content, "enabled": bool(req.enabled), "pushed": online_before,
    }}
