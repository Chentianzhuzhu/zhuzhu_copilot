"""FastAPI 应用入口"""
import asyncio
import json
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import engine
from app.redis_client import redis
from app.core.security import decode_jwt, assert_jwt_secret_strength
from app.core.session import is_session_valid
from app.services.ws_manager import ws_manager
from app.api import auth, points, shop, admin, agreement, checkin

# 日志配置
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时连接检查，关闭时清理"""
    logger.info("zhuzhu Copilot 认证服务启动中...")
    # 安全自检：生产环境拒绝弱/默认 JWT 密钥启动
    assert_jwt_secret_strength()
    # 预热连接
    try:
        await redis.ping()
        logger.info("Redis 连接成功")
    except Exception as e:
        logger.warning(f"Redis 连接失败: {e}")

    # 启动 WS 跨 worker 订阅端：HTTP 推送经 Redis 广播，由持有连接的 worker 投递。
    # 没有它，多 worker 部署下管理端推送会漏给「连接挂在别的 worker」的用户。
    try:
        await ws_manager.start_subscriber()
        logger.info("WS 跨进程订阅端已启动")
    except Exception as e:
        logger.warning(f"WS 订阅端启动失败: {e}")

    yield

    # 关闭时清理
    try:
        await ws_manager.stop_subscriber()
    except Exception:
        pass
    await redis.close()
    await engine.dispose()
    logger.info("服务已关闭")


app = FastAPI(
    title="zhuzhu Copilot 认证服务",
    description="账号系统后端 API",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS 中间件（生产环境配置具体来源，避免 * 搭配 credentials）
_cors_origins = settings.cors_origin_list
_use_credentials = "*" not in _cors_origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_use_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载 API 路由
app.include_router(auth.router)
app.include_router(points.router)
app.include_router(shop.router)
app.include_router(admin.router)
app.include_router(agreement.router)
app.include_router(checkin.router)

# 挂载静态文件
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str = ""):
    """
    WebSocket 连接端点
    连接: ws://host:8000/ws?token=<user_token>
    """
    # Token 认证
    if not token:
        await websocket.close(code=1008, reason="缺少token")
        return

    try:
        payload = decode_jwt(token)
        if payload.get("type") != "user":
            await websocket.close(code=1008, reason="非用户Token")
            return
        user_id = int(payload["sub"])
        jti = payload.get("jti")
        # 服务端会话校验：登出 / 被强制下线后，WS 不再放行
        if not jti or not await is_session_valid(redis, jti, "user"):
            await websocket.close(code=1008, reason="登录态已失效")
            return
    except Exception:
        await websocket.close(code=1008, reason="Token无效")
        return

    # 注册连接
    await ws_manager.connect(user_id, websocket)

    # 心跳超时计时
    last_ping_task = asyncio.create_task(heartbeat_timeout(websocket, user_id))

    try:
        while True:
            # 接收消息
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type")

            if msg_type == "ping":
                # 回复 pong
                await websocket.send_text(json.dumps({"type": "pong"}))
                # 重置心跳计时
                last_ping_task.cancel()
                last_ping_task = asyncio.create_task(heartbeat_timeout(websocket, user_id))
                # 续期 Redis 在线标记：长连接不能因 TTL 过期而被判「离线」
                try:
                    await ws_manager.refresh_online(user_id)
                except Exception:
                    pass

    except WebSocketDisconnect:
        await ws_manager.disconnect(user_id, websocket)
    except Exception as e:
        logger.error(f"WebSocket 错误: {e}")
        await ws_manager.disconnect(user_id, websocket)
    finally:
        last_ping_task.cancel()


async def heartbeat_timeout(websocket: WebSocket, user_id: int):
    """90秒未收到ping则断开"""
    try:
        await asyncio.sleep(90)
        await websocket.close(code=1001, reason="心跳超时")
    except asyncio.CancelledError:
        pass


@app.get("/")
async def root():
    """根路径健康检查"""
    return {"code": 0, "message": "zhuzhu Copilot 认证服务运行中"}
