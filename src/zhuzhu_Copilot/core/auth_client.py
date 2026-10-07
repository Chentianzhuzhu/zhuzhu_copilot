"""账号系统客户端：浏览器扫码登录回调、token 持久化、WebSocket 推送、积分消耗。

与 auth-server/API_SPEC.md 严格对齐：
- 登录：本地 127.0.0.1:18765 临时回调服务器 + 系统浏览器打开登录页
- 认证：Bearer Token，QSettings 持久化（token / 用户信息 / 积分）
- WebSocket：后台 QThread 长连接，指数退避自动重连，30s 心跳
- 积分：内置默认模型（agnes）按 token 增量实时 POST /api/points/consume

网络请求统一走 urllib（与 feedback_client.py 同一模式，浏览器 UA 防 CDN 403）；
WebSocket 走 websockets 异步库（缺失时 WS 静默降级，不影响登录/积分主流程）。
"""

import base64
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PyQt6.QtCore import QObject, pyqtSignal, QThread, QTimer

from zhuzhu_Copilot import app_identity
from zhuzhu_Copilot.core.i18n import ui as _ui

# ---------- 模块级常量 ----------
DEFAULT_AUTH_SERVER = "https://chentian.dpdns.org/authapp"   # 账号服务端地址（域名，可在设置页修改）
CALLBACK_HOST = "127.0.0.1"                     # 本地回调服务器监听地址
CALLBACK_PORT = 18765                           # 本地回调服务器端口
LOGIN_TIMEOUT_S = 120                           # 浏览器登录等待超时
LOGIN_POLL_INTERVAL_S = 1.5                     # 服务端中转轮询间隔（秒）
HEARTBEAT_INTERVAL_S = 30                       # WebSocket 心跳间隔
# 断线重连指数退避间隔（秒）：1/2/4/8/16/30 封顶
RECONNECT_DELAYS = (1, 2, 4, 8, 16, 30)
BUILTIN_MODEL_NAME = "agens"                    # 积分消耗上报的模型名（API 规范约定）
POINTS_PER_1K_TOKENS = 30                       # 消耗系数 0.03x：每 1000 token 消耗 30 积分
TOKEN_FLUSH_THRESHOLD = 100.0                   # 累计约 100 token 触发一次扣费
INSUFFICIENT_MESSAGE = "您的积分不足，请接入其他AI服务"
PLATFORM_ID = "zhuzhu-copilot"                  # 登录页 URL 的 platform 标识
LOGIN_STATE_PREFIX = "state="                   # 登录页 URL 的 state 参数名
# token 有效期（小时）。服务端 JWT_EXPIRE_HOURS 默认 24h；剩余不足该小时数时提前续期。
TOKEN_REFRESH_THRESHOLD_H = 6
# 周期巡检间隔（秒）：token 续期 + 账号状态兜底探测（WS 断线时仍能发现禁用/删除）。
# 默认 5 分钟后每隔 60s 再探测一次；此处取 120s 平衡实时性与请求量。
ACCOUNT_CHECK_INTERVAL_S = 120
# 系统公告轮询间隔（秒）：每 30s 拉取一次公开公告接口 /api/announcement。
# 公告经 WS 实时推送为主，轮询作为兜底（WS 断线/多端时仍能及时同步）。
ANNOUNCEMENT_POLL_INTERVAL_S = 30
_HTTP_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 zhuzhuCopilot")


def _client_version() -> str:
    """客户端版本号（取自 update_check.APP_VERSION，失败返回 dev）。"""
    try:
        from zhuzhu_Copilot.update_check import APP_VERSION
        return str(APP_VERSION or "dev")
    except Exception:
        return "dev"


def is_builtin_base_url(base_url: str) -> bool:
    """是否内置默认服务商（agnes）连接：与 agent_llm.DEFAULT_BASE_URL 同源判定。
    独立实现避免循环导入（agent_engine 已依赖本模块）。"""
    from zhuzhu_Copilot.core import agent_llm
    return (base_url or "").strip().rstrip("/").startswith(
        (agent_llm.DEFAULT_BASE_URL or "").rstrip("/"))


def _http_get_json(url: str, token: str = "", timeout: int = 15):
    """GET JSON：返回 (ok, data_dict, status_code)。统一浏览器 UA。"""
    headers = {"User-Agent": _HTTP_UA}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return True, json.loads(resp.read().decode("utf-8")), resp.status
    except urllib.error.HTTPError as e:
        try:
            data = json.loads(e.read().decode("utf-8"))
        except Exception:
            data = {}
        return False, data, e.code
    except Exception:
        return False, {}, 0


def _http_post_json(url: str, body: dict, token: str = "", timeout: int = 15):
    """POST JSON：返回 (ok, data_dict, status_code)。"""
    data = json.dumps(body).encode("utf-8")
    headers = {"User-Agent": _HTTP_UA, "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return True, json.loads(resp.read().decode("utf-8")), resp.status
    except urllib.error.HTTPError as e:
        try:
            data = json.loads(e.read().decode("utf-8"))
        except Exception:
            data = {}
        return False, data, e.code
    except Exception:
        return False, {}, 0


# ---------- 登录回调 HTTP 处理器 ----------
class _LoginCallbackHandler(BaseHTTPRequestHandler):
    """本地回调服务器：浏览器登录成功后服务端页面跳转回 /callback?token=..."""

    # 静默访问日志（默认打到 stderr，GUI 打包后无控制台）
    def log_message(self, *args):
        pass

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if not parsed.path.startswith("/callback"):
            self.send_response(404)
            self.end_headers()
            return
        qs = urllib.parse.parse_qs(parsed.query)

        def _first(key):
            v = qs.get(key) or qs.get(key.replace("_", ""))
            return v[0] if v else ""

        # 端口探测请求（登录页在导航投递前先探测端口是否在线）：直接回 204
        if _first("probe"):
            self._send_probe_ok()
            return

        token = _first("token")
        state = _first("state")
        user = {
            "id": _first("user_id") or _first("uid"),
            "username": _first("username"),
            "avatar": _first("avatar"),
            "points": _first("points"),
            "membership_type": _first("membership_type") or "free",
        }
        # 兼容：user 字段为 URL 编码的 JSON
        raw_user = _first("user")
        if raw_user:
            try:
                u = json.loads(urllib.parse.unquote(raw_user))
                if isinstance(u, dict):
                    for k in ("id", "username", "avatar", "points", "membership_type"):
                        if u.get(k) not in (None, ""):
                            user[k] = u[k]
            except Exception:
                pass
        # 回调成功页（根据 state 校验结果给不同文案），自动尝试关闭标签页
        cb = getattr(self.server, "_auth_callback", None)
        state_ok = True
        verifier = getattr(self.server, "_state_verifier", None)
        if callable(verifier):
            try:
                state_ok = bool(verifier(state))
            except Exception:
                state_ok = False
        if state_ok:
            title, desc = _ui("登录成功"), _ui("已成功返回 zhuzhu Copilot，本页面将自动关闭")
            color = "#2ecc71"
        else:
            title, desc = _ui("校验失败"), _ui("登录校验未通过（state 不匹配），请返回应用重新登录")
            color = "#e74c3c"
        html = (
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<title>" + title + "</title></head>"
            "<body style='font-family:Microsoft YaHei,Segoe UI,sans-serif;text-align:center;"
            "padding-top:80px;color:#333;background:#fafafa;'>"
            "<h2 style='color:" + color + ";'>" + title + "</h2>"
            "<p style='color:#666;'>" + desc + "</p>"
            + ("<script>setTimeout(function(){window.close();},800);</script>"
               if state_ok else "")
            + "</body></html>")
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)
        # 交给宿主处理（在本 handler 内持有 self.server 引用）
        if callable(cb):
            try:
                cb(token, user, state)
            except Exception:
                pass

    def _send_probe_ok(self):
        """端口探测响应：204 + CORS 头（供登录页 fetch/img 判定端口在线）。"""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def do_OPTIONS(self):
        """预检/探测兜底：统一允许跨源访问（仅本地回环，风险可控）。"""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()


class WebSocketClient(QThread):
    """账号 WebSocket 长连接（后台线程）：积分变动 / 强制下线 / 订单到账推送。

    自动重连（指数退避 1s/2s/4s/8s/16s/30s），每 30s 发 {"type":"ping"} 心跳。
    websockets 库缺失时整个线程静默空转（登录/积分功能不受影响）。
    """

    points_updated = pyqtSignal(int)          # 新积分余额
    force_logout_received = pyqtSignal(str)   # 强制下线原因
    order_paid_received = pyqtSignal(dict)    # 订单支付成功信息
    membership_updated = pyqtSignal(dict)     # 会员类型/到期日变动 {membership_type, membership_expire}
    announcement_received = pyqtSignal(dict)  # 系统公告变更 {content, enabled}
    avatar_received = pyqtSignal(dict)        # 头像地址变更 {avatar}
    checkin_received = pyqtSignal(dict)       # 签到结果 {points_added, points, tier, total_days}
    connected = pyqtSignal()
    disconnected = pyqtSignal()

    def __init__(self, server_url: str, token: str, parent=None):
        super().__init__(parent)
        self.server_url = (server_url or DEFAULT_AUTH_SERVER).rstrip("/")
        self._token = token or ""
        self._running = True

    def stop(self):
        self._running = False

    def run(self):
        try:
            import websockets  # noqa: F401  缺失时静默降级
        except Exception:
            return
        import asyncio
        ws_scheme = "wss" if self.server_url.startswith("https") else "ws"
        host_part = self.server_url.split("://", 1)[-1]
        ws_url = f"{ws_scheme}://{host_part}/ws?token={urllib.parse.quote(self._token)}"

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        backoff = 0
        try:
            while self._running:
                try:
                    loop.run_until_complete(self._connect(ws_url))
                except Exception:
                    pass
                if not self._running:
                    break
                try:
                    self.disconnected.emit()
                except Exception:
                    pass
                delay = RECONNECT_DELAYS[min(backoff, len(RECONNECT_DELAYS) - 1)]
                backoff += 1
                # 可中断的退避等待（0.1s 粒度，stop 时立刻退出）
                for _ in range(int(delay * 10)):
                    if not self._running:
                        break
                    time.sleep(0.1)
        finally:
            try:
                loop.close()
            except Exception:
                pass

    async def _connect(self, ws_url: str):
        """单次连接：recv 循环 + 心跳；断线抛出让外层重连。"""
        import asyncio
        import websockets
        async with websockets.connect(ws_url, ping_interval=None,
                                      open_timeout=15, close_timeout=5) as ws:
            self.connected.emit()
            last_ping = 0.0

            async def _recv_loop():
                async for raw in ws:
                    self._on_message(raw)

            recv_task = asyncio.create_task(_recv_loop())
            try:
                while self._running:
                    # 30s 心跳：send 后等 pong 由服务端异步回，不阻塞 recv
                    now = time.time()
                    if now - last_ping >= HEARTBEAT_INTERVAL_S:
                        await ws.send(json.dumps({"type": "ping"}))
                        last_ping = now
                    # 不阻塞地检查断线：recv_task 完成即连接断开
                    done, _ = await asyncio.wait({recv_task}, timeout=1.0)
                    if recv_task in done:
                        recv_task.result()   # 抛异常则上层重连
                        return
            finally:
                recv_task.cancel()

    def _on_message(self, raw):
        try:
            msg = json.loads(raw)
        except Exception:
            return
        mtype = msg.get("type")
        data = msg.get("data") or {}
        try:
            if mtype == "points_update":
                pts = data.get("points")
                if pts is not None:
                    self.points_updated.emit(int(pts))
            elif mtype == "force_logout":
                self.force_logout_received.emit(
                    data.get("reason") or _ui("您的账号已被管理员禁用"))
            elif mtype == "order_paid":
                self.order_paid_received.emit(data)
            elif mtype == "membership_update":
                self.membership_updated.emit(data)
            elif mtype == "announcement":
                self.announcement_received.emit(data)
            elif mtype == "checkin_result":
                self.checkin_received.emit(data)
            elif mtype == "avatar_update":
                self.avatar_received.emit(data)
        except Exception:
            pass


class PointsConsumer:
    """积分消耗管理器：跟踪当前任务 token 消耗，按增量实时扣费。

    agens 模型消耗系数 0.03x（每 1000 token = 30 积分）；积分归零时置截断标志，
    输出末尾追加固定文案。feed() 在 LLM 流式输出回调中逐段调用（worker 线程）。
    """

    INSUFFICIENT_MESSAGE = INSUFFICIENT_MESSAGE

    def __init__(self, client: "AuthClient", model: str = BUILTIN_MODEL_NAME,
                 task_id: str = ""):
        self.client = client
        self.model = model
        self.task_id = task_id or uuid.uuid4().hex[:12]
        self._pending_tokens = 0.0     # 未结算的估算 token 数
        self._exhausted = False

    @staticmethod
    def estimate_tokens(text: str) -> float:
        """粗略估算 token 数：中文字符约 1.5 token/字、英文约 4 字符/token。
        折中取 len/4（流式阶段精度足够，结算以服务端流水为准）。"""
        return len(text) / 4.0

    def feed(self, text: str) -> bool:
        """喂入一段流式文本增量。返回 True 表示积分已耗尽（应截断任务）。"""
        if not text or self._exhausted:
            return self._exhausted
        self._pending_tokens += self.estimate_tokens(text)
        if self._pending_tokens >= TOKEN_FLUSH_THRESHOLD:
            tokens, self._pending_tokens = self._pending_tokens, 0.0
            amount = max(1, round(tokens * POINTS_PER_1K_TOKENS / 1000.0))
            if not self.client.consume_points(amount, self.model, self.task_id):
                self._exhausted = True
        return self._exhausted

    def finalize(self):
        """一轮 LLM 调用结束：结算剩余累计 token（不足阈值的零头）。"""
        if self._exhausted or self._pending_tokens < 1.0:
            return
        tokens, self._pending_tokens = self._pending_tokens, 0.0
        amount = max(1, round(tokens * POINTS_PER_1K_TOKENS / 1000.0))
        if not self.client.consume_points(amount, self.model, self.task_id):
            self._exhausted = True

    def should_stop(self) -> bool:
        return self._exhausted

    def get_insufficient_message(self) -> str:
        # 运行时查表：语言可在应用运行期间切换，常量需在取用时才翻译
        return _ui(self.INSUFFICIENT_MESSAGE)


class AuthClient(QObject):
    """账号客户端单例：本地凭证管理 + 浏览器登录 + WebSocket 推送。

    信号均从后台线程发出，Qt 自动队列到 UI 线程，可直接更新界面。
    """

    login_success = pyqtSignal(dict)    # 登录成功（用户信息 dict）
    logged_out = pyqtSignal()           # 登出完成
    points_updated = pyqtSignal(int)     # 积分余额变动
    force_logout = pyqtSignal(str)      # 服务端强制下线（原因）
    order_paid = pyqtSignal(dict)       # 订单支付到账
    membership_updated = pyqtSignal(dict)  # 会员类型/到期日变动
    avatar_updated = pyqtSignal(str)       # 头像地址变动（新 URL）
    username_updated = pyqtSignal(str)     # 用户名变动（缓存修正，界面需重绘）
    announcement_updated = pyqtSignal(dict)  # 系统公告变更 {content, enabled}
    checkin_status_updated = pyqtSignal(dict)  # 签到状态变更 {can_checkin, checked, reward, ...}
    checkin_done = pyqtSignal(dict)            # 本机签到成功 {points_added, points, tier}
    checkin_failed = pyqtSignal(str)           # 签到失败原因（已签到/网络错误等）
    login_timeout = pyqtSignal()        # 浏览器登录超时
    login_rejected = pyqtSignal(str)    # 登录校验失败（state 不匹配等）
    session_expired = pyqtSignal(str)   # 登录态过期且自动续期失败（需重新登录）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ws = None            # WebSocketClient 实例
        self._cb_server = None     # 本地回调 HTTP 服务器
        self._cb_timer = None      # 登录超时 QTimer
        self._login_state = ""     # 本次登录的握手 state（防 CSRF / 重放）
        self._login_done = None    # threading.Event：本次登录是否已成功交接（幂等）
        self._announce_timer = None  # 公告轮询 QTimer（30s，启动即首拉）
        self._user = self._load_user()
        self._announcement = ""    # 当前系统公告（顶栏展示，/me 或 WS 推送更新）
        self._checkin = {}         # 最近一次签到状态快照（/me 或 WS 推送更新）
        # 清理历史版本遗留的自定义服务端地址（现已固定使用默认地址）
        self._clear_legacy_server_override()

    # ---------- 配置 / 持久化 ----------
    @property
    def server(self) -> str:
        """服务端地址：固定使用内置默认地址（不再支持自定义）。

        历史版本允许通过 QSettings(auth_server_url) 覆盖服务端地址，
        已取消该能力，统一走 DEFAULT_AUTH_SERVER，避免用户误配导致登录失败。
        """
        return DEFAULT_AUTH_SERVER

    def _clear_legacy_server_override(self):
        """清理历史版本遗留的自定义服务端地址，避免旧配置残留。"""
        try:
            q = app_identity.qsettings()
            if q.value("auth_server_url", ""):
                q.remove("auth_server_url")
                q.sync()
        except Exception:
            pass

    def _save_user(self, user: dict, token: str):
        try:
            q = app_identity.qsettings()
            q.setValue("auth_token", token or "")
            q.setValue("auth_user_json", json.dumps(user or {}, ensure_ascii=False))
            q.sync()
        except Exception:
            pass
        self._user = dict(user or {})

    def _load_user(self) -> dict:
        try:
            q = app_identity.qsettings()
            raw = q.value("auth_user_json", "")
            if raw:
                u = json.loads(raw)
                if isinstance(u, dict):
                    return u
        except Exception:
            pass
        return {}

    @property
    def token(self) -> str:
        try:
            return (app_identity.qsettings().value("auth_token", "") or "").strip()
        except Exception:
            return ""

    def is_logged_in(self) -> bool:
        return bool(self.token)

    def current_user(self):
        """返回 id/username/avatar/points/membership_type/membership_expire，未登录返回 None。"""
        if not self.is_logged_in():
            return None
        return {
            "id": self._user.get("id"),
            "username": self._user.get("username") or "",
            "avatar": self._user.get("avatar") or "",
            "points": int(self._user.get("points") or 0),
            "membership_type": self._user.get("membership_type") or "free",
            "membership_expire": self._user.get("membership_expire") or "",
        }

    def announcement(self) -> str:
        """当前系统公告内容（无公告返回空字符串）。"""
        return self._announcement or ""

    # ---------- 每日签到 ----------
    def checkin_state(self) -> dict:
        """最近一次已知签到状态（/me 下发或 WS 推送后更新）；无数据返回空 dict。

        字段：{can_checkin, checked, today, reward, points, tier, total_days}
        """
        return dict(self._checkin or {})

    def _apply_checkin_state(self, data: dict):
        """合并签到状态快照（部分字段更新不清空其它字段）。"""
        if not isinstance(data, dict) or not data:
            return
        merged = dict(self._checkin or {})
        merged.update({k: v for k, v in data.items() if v is not None})
        self._checkin = merged
        try:
            self.checkin_status_updated.emit(dict(merged))
        except Exception:
            pass

    def fetch_checkin_status_async(self):
        """后台线程拉取签到状态（登录后 / 面板打开时调用，不阻塞 UI）。"""
        if not self.is_logged_in():
            return

        def _work():
            ok, data, _ = _http_get_json(f"{self.server}/api/checkin/status",
                                         token=self.token, timeout=8)
            if ok and isinstance(data, dict) and data.get("code") == 0:
                self._apply_checkin_state(data.get("data") or {})

        try:
            threading.Thread(target=_work, daemon=True).start()
        except Exception:
            pass

    def do_checkin(self):
        """执行签到。同步阻塞调用（网络请求），由调用方放到后台线程。

        成功发 ``checkin_done``；失败发 ``checkin_failed``（原因已本地化）。
        """
        if not self.is_logged_in():
            try:
                self.checkin_failed.emit(_ui("请先登录后再签到"))
            except Exception:
                pass
            return False
        try:
            self.ensure_fresh_token()
            token = self.token
        except Exception:
            token = self.token

        ok, data, status = _http_post_json(f"{self.server}/api/checkin", {},
                                          token=token, timeout=10)
        if not ok or not isinstance(data, dict) or data.get("code") != 0:
            reason = ""
            if isinstance(data, dict):
                reason = str(data.get("detail") or data.get("message") or "")
            if not reason:
                if status == 401:
                    reason = _ui("登录已过期，请重新登录")
                else:
                    reason = _ui("签到失败，请稍后重试")
            try:
                self.checkin_failed.emit(reason)
            except Exception:
                pass
            return False

        payload = data.get("data") or {}
        # 同步本地积分，UI 立即可见（WS 推送到达前不会显示旧值）
        try:
            added = int(payload.get("points_added") or 0)
            if added and isinstance(self._user, dict):
                self._user["points"] = int(payload.get("points")
                                           or (self._user.get("points") or 0))
                self._save_user(self._user, token)
        except Exception:
            pass
        self._apply_checkin_state({
            "can_checkin": False, "checked": True,
            "reward": payload.get("points_added"),
            "points": payload.get("points"),
            "total_days": payload.get("total_days"),
            "today": payload.get("today"),
        })
        try:
            self.checkin_done.emit(payload)
        except Exception:
            pass
        return True

    # ---------- 系统公告轮询（30s，启动即首拉） ----------
    def start_announcement_polling(self):
        """启动公告轮询：立即拉取一次，随后每 ANNOUNCEMENT_POLL_INTERVAL_S 秒拉取。

        与 WS 推送互补：WS 负责秒级实时，轮询负责断线/多端兜底。
        幂等：重复调用不会叠加定时器。无需登录（走公开接口）。
        """
        if getattr(self, "_announce_timer", None) is not None:
            return
        try:
            t = QTimer(self)
            t.setInterval(ANNOUNCEMENT_POLL_INTERVAL_S * 1000)
            t.timeout.connect(self.poll_announcement_async)
            t.start()
            self._announce_timer = t
        except Exception:
            self._announce_timer = None
            return
        # 打开应用即首次轮询（异步，不阻塞 UI 启动）
        self.poll_announcement_async()

    def poll_announcement_async(self):
        """异步拉取一次公告（后台线程）；结果变化时经 announcement_updated 广播。"""
        try:
            threading.Thread(target=self._poll_announcement,
                             daemon=True).start()
        except Exception:
            pass

    def _poll_announcement(self):
        """拉取公开公告接口；内容有变化才广播（避免无谓刷新 UI）。"""
        try:
            ok, data, _ = _http_get_json(f"{self.server}/api/announcement",
                                         timeout=8)
            if not ok or not isinstance(data, dict):
                return
            d = data.get("data")
            content = ""
            if isinstance(d, dict):
                content = str(d.get("content") or "")
            elif isinstance(d, str):
                content = d
            if content == (self._announcement or ""):
                return
            self._announcement = content
            try:
                self.announcement_updated.emit({"content": content,
                                                "enabled": bool(content)})
            except Exception:
                pass
        except Exception:
            pass

    def stop_announcement_polling(self):
        """停止公告轮询（登出/退出时调用）。"""
        t = getattr(self, "_announce_timer", None)
        self._announce_timer = None
        if t is not None:
            try:
                t.stop()
            except Exception:
                pass

    def clear_credentials(self):
        """清除本地凭证（登出 / 强制下线时调用）。"""
        try:
            q = app_identity.qsettings()
            q.remove("auth_token")
            q.remove("auth_user_json")
            q.sync()
        except Exception:
            pass
        self._user = {}
        self._announcement = ""
        self._checkin = {}
        self._stop_ws()
        self.stop_announcement_polling()

    # ---------- 登录 ----------
    def _issue_login_state(self) -> str:
        """向服务端申请一次性登录握手 state；失败时本地生成兜底值。

        服务端 state 用于防 login CSRF / 重放：登录页提交时回传、服务端消费，
        回来后客户端再比对一致性。服务端不可用时返回本地随机值（服务端未强制
        state 时仍可登录，客户端校验依旧生效）。"""
        url = (f"{self.server}/api/auth/state"
               f"?platform={urllib.parse.quote(PLATFORM_ID)}"
               f"&version={urllib.parse.quote(_client_version())}")
        ok, data, _ = _http_post_json(url, {}, timeout=8)
        if ok and isinstance(data, dict):
            s = ((data.get("data") or {}).get("state") or "").strip()
            if s:
                return s
        # 兜底：本地生成的随机 state（与服务端无关联，仅用于客户端自查一致性）
        return uuid.uuid4().hex

    def login_with_browser(self):
        """启动本地回调服务器并打开系统浏览器登录页（全程非阻塞）。

        关键：申请 state（阻塞 HTTP，最长 8s）与打开浏览器都不在 UI 线程执行，
        否则网络慢时整个界面会「无响应」。这里仅做轻量的重入检查与状态初始化，
        真正的网络请求与 webbrowser.open 放进后台线程。

        登录页 URL 形如：
          {server}/static/login/index.html?platform=zhuzhu-copilot&state=<uuid>&version=<ver>&callback_port=18765
        浏览器登录成功后服务端页面跳转回 http://127.0.0.1:18765/callback?token=...&state=...；
        客户端校验回调 state 与本次登录 state 一致才接受 token。
        120s 未收到回调则超时关闭。"""
        if self._cb_server is not None or (self._login_done is not None
                                           and not self._login_done.is_set()):
            return   # 已有登录流程在进行
        # 本地回调服务器为「快通道」，可选：端口被占用时仍可依赖服务端中转轮询
        self._cb_server = None
        try:
            self._cb_server = ThreadingHTTPServer((CALLBACK_HOST, CALLBACK_PORT),
                                                  _LoginCallbackHandler)
        except OSError:
            self._cb_server = None   # 端口占用：退化为纯服务端中转模式
        self._login_state = ""
        self._login_done = threading.Event()
        if self._cb_server is not None:
            self._cb_server._auth_callback = self._on_login_callback
            self._cb_server._state_verifier = self._verify_callback_state
            threading.Thread(target=self._cb_server.serve_forever,
                             kwargs={"poll_interval": 0.5}, daemon=True).start()
        # 超时定时器（UI 线程）
        self._cb_timer = QTimer(self)
        self._cb_timer.setSingleShot(True)
        self._cb_timer.timeout.connect(self._on_login_timeout)
        self._cb_timer.start(LOGIN_TIMEOUT_S * 1000)
        # 后台线程：申请 state → 启动轮询兜底 → 打开浏览器（不阻塞 UI）
        threading.Thread(target=self._open_login_page, daemon=True).start()

    def _open_login_page(self):
        """后台线程：申请 state、启动中转轮询、打开浏览器登录页。

        任何一步失败都不静默吞掉：申请 state 失败会本地兜底；打开浏览器失败
        会发出 login_rejected 信号提示用户手动打开登录地址。
        """
        try:
            state = self._issue_login_state()
            if self._login_done is not None and self._login_done.is_set():
                return
            self._login_state = state
            # 服务端中转轮询：兜底「HTTPS 页面 → http://127.0.0.1」被浏览器拦截的场景
            threading.Thread(target=self._poll_login_result, daemon=True).start()
            url = (f"{self.server}/static/login/index.html"
                   f"?platform={urllib.parse.quote(PLATFORM_ID)}"
                   f"&state={urllib.parse.quote(state)}"
                   f"&version={urllib.parse.quote(_client_version())}"
                   f"&callback_port={CALLBACK_PORT}")
            opened = False
            try:
                opened = webbrowser.open(url)
            except Exception:
                opened = False
            if not opened:
                # 某些环境下 webbrowser.open 静默失败：退化为系统命令再试一次
                opened = self._open_url_fallback(url)
            if not opened:
                self.login_rejected.emit(
                    _ui("无法自动打开浏览器，请手动访问以下地址完成登录：\n") + url)
        except Exception:
            # 兜底：确保证 UI 不会因后台异常而无任何反馈
            try:
                self.login_rejected.emit(_ui("打开登录页失败，请稍后重试。"))
            except Exception:
                pass

    @staticmethod
    def _open_url_fallback(url: str) -> bool:
        """webbrowser.open 失败时的系统级兜底（Windows start / macOS open / Linux xdg-open）。

        注意：必须用 argv 形式传参，绝不拼 shell 字符串，避免命令注入 / URL 被截断。
        """
        try:
            import subprocess
            import sys as _sys
            if _sys.platform.startswith("win"):
                # start 是 cmd 内建命令，需经 cmd /c；URL 作为独立参数，不经 shell 拼接
                subprocess.Popen(["cmd", "/c", "start", "", url],
                                 close_fds=True)
            elif _sys.platform == "darwin":
                subprocess.Popen(["open", url], close_fds=True)
            else:
                subprocess.Popen(["xdg-open", url], close_fds=True)
            return True
        except Exception:
            return False

    def _poll_login_result(self):
        """后台线程：轮询服务端登录结果（中转回跳）。

        与本地回调并行：无论哪条通道先拿到 token，都走 _accept_login_result，
        由 _login_done 事件保证只处理一次。未配置 state（服务端未启用）时跳过。
        """
        state = (self._login_state or "").strip()
        if not state:
            return
        url = f"{self.server}/api/auth/login_result?state={urllib.parse.quote(state)}"
        deadline = time.time() + LOGIN_TIMEOUT_S
        while not self._login_done.is_set() and time.time() < deadline:
            try:
                ok, data, _ = _http_get_json(url, timeout=8)
                if ok and isinstance(data, dict):
                    payload = data.get("data")
                    if isinstance(payload, dict):
                        token = (payload.get("token") or "").strip()
                        user = payload.get("user") or {}
                        if token:
                            self._accept_login_result(token, user, state)
                            return
            except Exception:
                pass
            # 可中断休眠
            for _ in range(int(LOGIN_POLL_INTERVAL_S * 10)):
                if self._login_done.is_set():
                    return
                time.sleep(0.1)

    def _verify_callback_state(self, state: str) -> bool:
        """校验回调携带的 state 是否与本次登录发起时一致（防 login CSRF / 重放）。"""
        expected = (self._login_state or "").strip()
        got = (state or "").strip()
        if not expected:
            # 本次未申请 state（异常路径）：不阻断，交由 token 校验兜底
            return True
        return bool(got) and secrets.compare_digest(expected, got)

    def _on_login_callback(self, token: str, user: dict, state: str = ""):
        """本地回调线程：校验 state → 交接凭证（与轮询通道共用 _accept_login_result）。"""
        # state 校验不通过：拒绝本次凭证（防伪造回调注入他人 token）
        if not self._verify_callback_state(state):
            self._fail_login_rejected()
            return
        self._accept_login_result(token, user, state)

    def _accept_login_result(self, token: str, user: dict, state: str = ""):
        """统一的登录结果交接：落盘凭证 + 关回调服务器 + 发信号（幂等，仅处理一次）。

        本地回调与「服务端中转轮询」两条通道共用此入口；由 _login_done 事件
        保证竞态下只成功处理一次（先到先得）。
        """
        # 幂等保护：任一通道已成功处理则直接忽略
        done = getattr(self, "_login_done", None)
        if done is not None:
            if done.is_set():
                return
            done.set()
        # 二次 state 校验（轮询通道已按本地 state 发起，这里做一致性兜底）
        if state and not self._verify_callback_state(state):
            self._fail_login_rejected()
            return
        if self._cb_timer is not None:
            try:
                self._cb_timer.stop()
            except Exception:
                pass
            self._cb_timer = None
        if token:
            # 服务端可能未随结果带全用户信息，拉一次 /api/auth/me 补全
            full = self._fetch_me(token)
            if full:
                user = full
            user = user or {}
            self._save_user(user, token)
        self._close_callback_server()
        self._login_state = ""
        if token:
            try:
                self.login_success.emit(self.current_user() or {})
            except Exception:
                pass
            self._start_ws()

    def _fail_login_rejected(self):
        """登录校验失败：清理回调资源并提示重试。"""
        try:
            if getattr(self, "_login_done", None) is not None:
                self._login_done.set()
        except Exception:
            pass
        self._close_callback_server()
        self._login_state = ""
        try:
            self.login_rejected.emit(_ui("登录校验失败（state 不匹配），请重试"))
        except Exception:
            pass

    def _on_login_timeout(self):
        self._cb_timer = None
        self._login_state = ""
        try:
            if getattr(self, "_login_done", None) is not None:
                self._login_done.set()
        except Exception:
            pass
        self._close_callback_server()
        try:
            self.login_timeout.emit()
        except Exception:
            pass

    def _close_callback_server(self):
        srv = self._cb_server
        self._cb_server = None
        if srv is None:
            return
        try:
            threading.Thread(target=srv.shutdown, daemon=True).start()
        except Exception:
            pass
        try:
            srv.server_close()
        except Exception:
            pass

    def _fetch_me(self, token: str):
        ok, data, _ = _http_get_json(f"{self.server}/api/auth/me", token=token,
                                     timeout=10)
        if ok and isinstance(data, dict):
            d = data.get("data")
            # /me 附带当前公告：顺带缓存，供顶栏启动即展示
            if isinstance(d, dict) and "announcement" in d:
                self._announcement = str(d.get("announcement") or "")
            # /me 同时附带签到状态：登录后按钮态即可渲染，无需额外请求
            if isinstance(d, dict) and isinstance(d.get("checkin"), dict):
                self._checkin = dict(d.get("checkin"))
            return d
        return None

    # ---------- Token 续期 / 掉线自愈 ----------
    @staticmethod
    def _token_expires_soon(token: str, threshold_hours: float = TOKEN_REFRESH_THRESHOLD_H) -> bool:
        """解析 JWT 的 exp，判断是否临近过期（未带 exp 或解析失败按「不续期」处理）。"""
        if not token:
            return False
        try:
            parts = token.split(".")
            if len(parts) != 3:
                return False
            pad = "=" * (-len(parts[1]) % 4)
            payload = json.loads(base64.urlsafe_b64decode(parts[1] + pad).decode("utf-8"))
            exp = payload.get("exp")
            if not exp:
                return False
            return (float(exp) - time.time()) < threshold_hours * 3600
        except Exception:
            return False

    def refresh_token(self) -> bool:
        """调用 /api/auth/refresh 换新 token 并落盘。成功返回 True。

        服务端在返回新 token 的同时撤销旧会话（旧的 jti），因此必须立刻覆盖本地凭证，
        否则 WS 会因旧会话被撤销而掉线。
        """
        token = self.token
        if not token:
            return False
        ok, data, status = _http_post_json(f"{self.server}/api/auth/refresh",
                                           {}, token=token, timeout=10)
        if ok and isinstance(data, dict) and data.get("code") == 0:
            new_token = ((data.get("data") or {}).get("token") or "").strip()
            if new_token:
                self._save_user(self._user or {}, new_token)
                self._restart_ws_with_token()
                return True
        # 401/403：会话已失效，无法续期
        if status in (401, 403):
            self._on_session_expired()
        return False

    def _on_session_expired(self, reason: str = ""):
        """登录态彻底失效：清凭证、停 WS，并通知 UI 引导重新登录。"""
        self.clear_credentials()
        try:
            self.session_expired.emit(reason or _ui("登录已过期，请重新登录"))
        except Exception:
            pass

    def _on_force_logout(self, reason: str = ""):
        """服务端强制下线（禁用/删除）：清凭证、停 WS，并通知 UI 中断任务+弹窗。

        与 _on_session_expired 的区别：强制下线是「账号不可用」，
        UI 侧会中断当前内置模型任务并弹强制性提示（无「稍后」选项）。
        """
        self.clear_credentials()
        try:
            self.force_logout.emit(reason or _ui("您的账号已被管理员禁用"))
        except Exception:
            pass

    def _restart_ws_with_token(self):
        """token 更换后用新凭证重连 WebSocket（旧连接持有旧 token）。"""
        if self.is_logged_in():
            self._start_ws()

    def ensure_fresh_token(self) -> bool:
        """确保 token 可用：临近过期则自动续期。返回是否仍然持有可用登录态。

        供后台定时器与应用启动时调用，实现「长时间挂机不掉线」。
        """
        if not self.is_logged_in():
            return False
        token = self.token
        if not self._token_expires_soon(token):
            return True
        if self.refresh_token():
            return True
        # 续期失败但本地 token 尚未真正过期：仍可能可用，交由后续 401 兜底
        return not self._token_expires_soon(token, threshold_hours=0)

    def refresh_user_info(self):
        """从服务器拉取最新用户信息并更新本地缓存。"""
        if not self.is_logged_in():
            return
        u = self._fetch_me(self.token)
        if isinstance(u, dict) and u:
            self._save_user(u, self.token)

    # ---------- 登出 / 切换 ----------
    def logout(self):
        """调用服务端登出并清除本地凭证（HTTP 请求放后台线程，失败不阻塞本地清理）。"""
        token = self.token
        if token:
            try:
                _http_post_json(f"{self.server}/api/auth/logout", {}, token=token,
                                timeout=8)
            except Exception:
                pass
        self.clear_credentials()
        try:
            self.logged_out.emit()
        except Exception:
            pass

    def switch_account(self):
        """先登出再重新走浏览器登录。"""
        self.logout()
        self.login_with_browser()

    # ---------- 积分 ----------
    def get_points(self) -> int:
        u = self.current_user()
        return int(u["points"]) if u else 0

    def consume_points(self, amount: int, model: str = BUILTIN_MODEL_NAME,
                       task_id: str = "") -> bool:
        """上报积分消耗。返回 True 表示可继续；False 表示应中止当前任务。

        - 402 积分不足：明确中止（False）。
        - 401 登录态过期：自动续期一次并重试；续期失败则按强制下线处理并中止。
        - 403 账号被禁用/删除：立即强制下线并中止任务（返回 False）。
        - 其余网络/服务端错误：保守放行（True），避免断网误杀正在进行的任务。
        """
        token = self.token
        if not token:
            return False

        def _post():
            return _http_post_json(
                f"{self.server}/api/points/consume",
                {"amount": int(amount or 1), "model": model, "task_id": task_id},
                token=self.token, timeout=10)

        ok, data, status = _post()

        # 账号被禁用/删除：管理员操作后服务端返回 403，立即强制下线
        if not ok and status == 403:
            detail = ""
            try:
                detail = (data or {}).get("message") or (data or {}).get("detail") or ""
            except Exception:
                detail = ""
            self._on_force_logout(detail or _ui("账号已被禁用，已强制下线"))
            return False

        # 登录态过期：自动续期一次后重试（长时间任务挂机场景）
        if not ok and status == 401:
            if self.refresh_token():
                ok, data, status = _post()
                # 续期后仍 403：账号状态异常（禁用/删除）
                if not ok and status == 403:
                    self._on_force_logout(_ui("账号已被禁用，已强制下线"))
                    return False
            else:
                # 续期失败：登录态已失效（可能已过期，也可能账号被禁用/删除）。
                # 这里按「需重新登录」处理并中止本轮任务；若为账号被禁用，
                # 服务端通常也会经 WS 推送 force_logout（届时 UI 会显示强制下线提示）。
                self._on_session_expired(_ui("登录态已失效，请重新登录后继续"))
                return False

        if ok and isinstance(data, dict) and data.get("code") == 0:
            remaining = (data.get("data") or {}).get("remaining")
            if remaining is not None:
                try:
                    self._user["points"] = int(remaining)
                    self._persist_points(int(remaining))
                    self.points_updated.emit(int(remaining))
                except Exception:
                    pass
            return True
        # 402 积分不足：明确失败（截断任务）；其余网络/服务端错误保守放行，
        # 避免断网时误杀用户正在进行的输出。
        return False if status == 402 else True

    def _on_ws_avatar(self, data: dict):
        """头像变更实时推送：更新本地缓存地址并广播（UI 据此重新拉图）。

        服务端每次上传头像都会生成新的 UUID 文件名，因此「地址变了」即
        「头像内容变了」；客户端只需按新地址重新下载，无需比对图片内容。
        """
        try:
            d = data or {}
            avatar = str(d.get("avatar") or "")
            if not avatar:
                return
            self._user["avatar"] = avatar
            # 持久化（与 _persist_points 同款：写 auth_user_json）
            try:
                q = app_identity.qsettings()
                q.setValue("auth_user_json",
                           json.dumps(self._user, ensure_ascii=False))
                q.sync()
            except Exception:
                pass
            self.avatar_updated.emit(avatar)
        except Exception:
            pass

    def _persist_points(self, points: int):
        try:
            q = app_identity.qsettings()
            self._user["points"] = points
            q.setValue("auth_user_json",
                       json.dumps(self._user, ensure_ascii=False))
            q.sync()
        except Exception:
            pass

    def apply_points_update(self, points: int):
        """WS 推送积分变动后更新本地缓存（供 UI 刷新显示）。"""
        self._persist_points(int(points))
        try:
            self.points_updated.emit(int(points))
        except Exception:
            pass

    # ---------- WebSocket 生命周期 ----------
    def start_ws_if_logged_in(self):
        """应用启动时：已登录则自动恢复 WS 连接，并启动 token 续期巡检。

        同时（无论是否登录）启动系统公告轮询——公告走公开接口，
        未登录也需在顶栏展示。
        """
        if self.is_logged_in() and self._ws is None:
            self._start_ws()
        self._start_refresh_timer()
        # 公告轮询：打开应用即首拉，随后每 30s 一次
        self.start_announcement_polling()
        # 签到状态：启动即首拉（后台线程），保证按钮态与服务端一致
        self.fetch_checkin_status_async()

    def _start_refresh_timer(self):
        """启动周期性巡检：token 续期 + 账号状态兜底探测。

        与 WS 重连解耦：WS 负责实时推送，此定时器保证（1）登录态不因 token
        过期失效；（2）即便 WS 断线，账号被禁用/删除也能在巡检周期内被发现。
        """
        if getattr(self, "_refresh_timer", None) is not None:
            return
        try:
            t = QTimer(self)
            t.setInterval(ACCOUNT_CHECK_INTERVAL_S * 1000)
            t.timeout.connect(self._refresh_tick)
            t.start()
            self._refresh_timer = t
            # 启动即巡检一次（覆盖「隔夜启动」场景）
            QTimer.singleShot(3000, self._refresh_tick)
        except Exception:
            self._refresh_timer = None

    def _refresh_tick(self):
        """周期巡检：临近过期则续期；并兜底探测账号状态（禁用/删除即强制下线）。

        网络请求放后台线程，避免阻塞 UI。
        """
        if not self.is_logged_in():
            return
        need_refresh = self._token_expires_soon(self.token)
        threading.Thread(target=self._refresh_worker,
                         args=(need_refresh,), daemon=True).start()

    def _refresh_worker(self, need_refresh: bool = True):
        try:
            if need_refresh:
                self.ensure_fresh_token()
            # 账号状态兜底探测：WS 断线时也能及时发现禁用/删除
            self._probe_account_status()
        except Exception:
            pass

    def _probe_account_status(self):
        """调用 /api/auth/me 探测账号状态；403/401 视为已被禁用/删除，强制下线。

        成功时顺带同步「头像/会员」等可能被其它端（或后台）改动的字段——
        WS 断线期间的头像变更就靠这条轮询兜底，否则客户端会一直显示旧头像。
        """
        token = self.token
        if not token:
            return
        ok, data, status = _http_get_json(f"{self.server}/api/auth/me",
                                          token=token, timeout=10)
        if ok:
            self._sync_from_me(data)
            return
        if status == 403:
            detail = (data or {}).get("message") or (data or {}).get("detail") or ""
            self._on_force_logout(detail or _ui("账号已被禁用，已强制下线"))
        # 401 交由 consume_points / WS 的既有 401 处理，避免误清「短暂网络抖动」

    def _sync_from_me(self, data):
        """把 /me 返回的最新字段同步到本地缓存；有实质变化才广播。

        覆盖：头像、用户名、用户ID、会员信息。

        **用户名与 ID 必须同步**：本地 ``auth_user_json`` 是登录时写下的快照，
        而积分/会员会经 WS 推送刷新，用户名和 ID 却没有任何刷新通道——
        一旦缓存里是旧值（例如早期测试账号的 ``u`` / ``1``），界面会永远显示
        旧用户名，看起来就像「名字被挤压/显示不全」。
        仅在值真正变化时发信号，避免轮询周期内反复触发 UI 重绘。
        """
        try:
            d = (data or {}).get("data") if isinstance(data, dict) else None
            if not isinstance(d, dict):
                return
            changed_avatar = False
            new_avatar = str(d.get("avatar") or "")
            if new_avatar and new_avatar != (self._user.get("avatar") or ""):
                self._user["avatar"] = new_avatar
                changed_avatar = True

            changed_name = False
            new_name = str(d.get("username") or "")
            if new_name and new_name != str(self._user.get("username") or ""):
                self._user["username"] = new_name
                changed_name = True
            if d.get("id") is not None and str(d.get("id")) != str(
                    self._user.get("id") or ""):
                self._user["id"] = d.get("id")
                changed_name = True   # 与用户名同批发信号，界面一起刷新

            changed_member = False
            for k in ("membership_type", "membership_expire", "points"):
                if k in d and d.get(k) is not None:
                    if str(d.get(k)) != str(self._user.get(k) or ""):
                        self._user[k] = d.get(k)
                        changed_member = True

            if not (changed_avatar or changed_name or changed_member):
                return
            # 落盘（保持与其它写入路径一致）
            try:
                q = app_identity.qsettings()
                q.setValue("auth_user_json",
                           json.dumps(self._user, ensure_ascii=False))
                q.sync()
            except Exception:
                pass
            if changed_avatar:
                self.avatar_updated.emit(new_avatar)
            if changed_name:
                self.username_updated.emit(str(self._user.get("username") or ""))
            if changed_member:
                self.membership_updated.emit(dict(self._user))
        except Exception:
            pass

    def _start_ws(self):
        self._stop_ws()
        if not self.is_logged_in():
            return
        try:
            ws = WebSocketClient(self.server, self.token, parent=self)
            ws.points_updated.connect(self._on_ws_points)
            ws.force_logout_received.connect(self._on_ws_force_logout)
            ws.order_paid_received.connect(self.order_paid)
            ws.membership_updated.connect(self._on_ws_membership)
            ws.announcement_received.connect(self._on_ws_announcement)
            ws.avatar_received.connect(self._on_ws_avatar)
            ws.checkin_received.connect(self._on_ws_checkin)
            self._ws = ws
            ws.start()
        except Exception:
            self._ws = None

    def _stop_ws(self):
        ws = self._ws
        self._ws = None
        if ws is not None:
            try:
                ws.stop()
                ws.wait(2000)
            except Exception:
                pass

    def _on_ws_points(self, points: int):
        try:
            self._persist_points(points)
            self.points_updated.emit(points)
        except Exception:
            pass

    def _on_ws_membership(self, data: dict):
        """会员类型/到期日实时变动：更新本地缓存并广播给 UI。"""
        try:
            d = data or {}
            if d.get("membership_type"):
                self._user["membership_type"] = d.get("membership_type")
            if "membership_expire" in d:
                self._user["membership_expire"] = d.get("membership_expire") or ""
            # 持久化（与 _persist_points 同款：写 auth_user_json）
            try:
                q = app_identity.qsettings()
                q.setValue("auth_user_json",
                           json.dumps(self._user, ensure_ascii=False))
                q.sync()
            except Exception:
                pass
            self.membership_updated.emit(dict(self._user))
        except Exception:
            pass

    def _on_ws_announcement(self, data: dict):
        """系统公告实时变更：缓存内容并广播给 UI（顶栏公告位）。"""
        try:
            d = data or {}
            content = str(d.get("content") or "")
            self._announcement = content
            self.announcement_updated.emit({"content": content,
                                            "enabled": bool(d.get("enabled", True))})
        except Exception:
            pass

    def _on_ws_force_logout(self, reason: str):
        """服务端强制下线（WS 推送）：清除本地凭证并通知 UI 弹窗+中断任务。"""
        self._on_force_logout(reason)

    def _on_ws_checkin(self, data: dict):
        """签到结果实时推送（可能是本机签到，也可能是同账号另一端签到）。

        统一走状态合并 + 广播，保证按钮态在任何一端签到后都能立即变为「今日已签」。
        """
        try:
            d = data or {}
            self._apply_checkin_state({
                "can_checkin": False,
                "checked": bool(d.get("success", True)),
                "points_added": d.get("points_added"),
                "points": d.get("points"),
                "tier": d.get("tier"),
                "total_days": d.get("total_days"),
                "today": d.get("today"),
            })
            self.checkin_done.emit(dict(d))
        except Exception:
            pass


# ---------- 模块级单例 ----------
_singleton: AuthClient = None


def get_auth_client() -> AuthClient:
    """AuthClient 单例（首次调用在 UI 线程，信号槽连接安全）。"""
    global _singleton
    if _singleton is None:
        _singleton = AuthClient()
    return _singleton
