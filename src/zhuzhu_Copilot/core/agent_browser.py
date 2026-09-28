"""浏览器操控（CDP / Chrome DevTools Protocol）：独立浏览器实例 + 网页级操作

核心设计（满足「完全不影响用户其他操作」）：
- 用**独立 user-data-dir + 动态调试端口**启动一个专用 Edge/Chrome 实例，
  与用户正在使用的浏览器完全隔离；AI 只通过 DevTools 协议操控该实例，
  不碰用户的鼠标键盘，也不抢占用户浏览器窗口。
- 截图用 `Page.captureScreenshot`（网页级截图，非整屏），自动缩放控制体积。
- 解析/操作网页直接走 `Runtime.evaluate` 执行 JS（读 DOM = 解析 HTML/CSS，
  操作 = 执行 JS / 派发鼠标输入），比屏幕像素定位更稳更快。
- WebSocket 客户端用标准库实现（socket + base64 + hashlib + struct），
  不依赖第三方 websocket 库，便于 Cython 打包。

对外工具（execute_tool 注册）：
- browser_open(engine, headless)       启动独立浏览器实例
- browser_navigate(url)                打开网页
- browser_snapshot()                   截图 + 可交互元素语义清单 [id]
- browser_click(id/text)               按编号/文字点击元素
- browser_type(text, id)               向输入框输入
- browser_eval(js)                     执行 JS 并返回结果
- browser_html(selector)               读取网页 HTML/文本
- browser_close()                      关闭独立浏览器实例
"""

from zhuzhu_Copilot import app_identity
import base64
import hashlib
import json
import os
import re
import socket
import struct
import subprocess
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

# 浏览器可执行文件候选（Windows 常见安装路径）
_BROWSER_CANDIDATES = [
    os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)") + "\\Microsoft\\Edge\\Application\\msedge.exe",
    os.environ.get("ProgramFiles", "C:\\Program Files") + "\\Microsoft\\Edge\\Application\\msedge.exe",
    os.environ.get("ProgramFiles", "C:\\Program Files") + "\\Google\\Chrome\\Application\\chrome.exe",
    os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)") + "\\Google\\Chrome\\Application\\chrome.exe",
]
# 屏幕截图发送给视觉模型的统一宽度（控制图片体积，避免 base64 过大超时）
_MODEL_W = 960


def find_browser(engine: str = "") -> str:
    """返回可用的浏览器可执行文件路径；engine 传 edge/chrome 可优先匹配。"""
    e = (engine or "").strip().lower()
    # 用户指定引擎时优先
    if e:
        for p in _BROWSER_CANDIDATES:
            if (("edge" in e.lower()) != ("msedge" in p.lower())) and not (
                    ("chrome" in e.lower()) or ("edge" in e.lower())):
                continue
            if ("edge" in e.lower() and "msedge" not in p.lower()):
                continue
            if ("chrome" in e.lower() and "chrome.exe" not in p.lower()):
                continue
            if os.path.isfile(p):
                return p
    for p in _BROWSER_CANDIDATES:
        if os.path.isfile(p):
            return p
    return ""


# ------------------------------------------------------------
# 精简 WebSocket 客户端（RFC 6455，客户端必须掩码）
# ------------------------------------------------------------
class _WS:
    def __init__(self, timeout: float = 20.0):
        self._sock = None
        self._timeout = timeout

    def connect(self, url: str):
        """url: ws://host:port/path"""
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            host, port = parsed.hostname, parsed.port or 80
            path = parsed.path or "/"
        except Exception:
            host, port, path = "127.0.0.1", 80, "/"
        self._sock = socket.create_connection((host, port), timeout=self._timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET {path} HTTP/1.1\r\n"
               f"Host: {host}:{port}\r\n"
               f"Upgrade: websocket\r\n"
               f"Connection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\n"
               f"Sec-WebSocket-Version: 13\r\n\r\n")
        self._sock.sendall(req.encode())
        resp = self._recv_http()
        if " 101 " not in resp.split("\r\n", 1)[0]:
            raise RuntimeError(f"WebSocket 握手失败: {resp.splitlines()[0] if resp else '无响应'}")

    def _recv_http(self) -> str:
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = self._sock.recv(4096)
            if not chunk:
                break
            data += chunk
        return data.decode("utf-8", "replace")

    def _send_frame(self, opcode: int, payload: bytes):
        # 客户端帧必须掩码
        mask = os.urandom(4)
        header = bytes([0x80 | opcode])
        n = len(payload)
        if n < 126:
            header += bytes([0x80 | n])
        elif n < 65536:
            header += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            header += bytes([0x80 | 127]) + struct.pack(">Q", n)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self._sock.sendall(header + mask + masked)

    def _recv_frame(self) -> tuple:
        """返回 (opcode, payload_bytes)。处理分片与延长长度。"""
        head = self._recv_exact(2)
        fin = head[0] & 0x80
        opcode = head[0] & 0x0F
        masked = head[1] & 0x80
        n = head[1] & 0x7F
        if n == 126:
            n = struct.unpack(">H", self._recv_exact(2))[0]
        elif n == 127:
            n = struct.unpack(">Q", self._recv_exact(8))[0]
        mask = self._recv_exact(4) if masked else b""
        payload = self._recv_exact(n)
        if masked:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return opcode, payload

    def _recv_exact(self, n: int) -> bytes:
        buf = b""
        self._sock.settimeout(self._timeout)
        while len(buf) < n:
            chunk = self._sock.recv(n - len(buf))
            if not chunk:
                raise RuntimeError("WebSocket 连接已断开")
            buf += chunk
        return buf

    def send_text(self, text: str):
        self._send_frame(0x1, text.encode("utf-8"))

    def recv_text(self) -> str:
        opcode, payload = self._recv_frame()
        if opcode == 0x8:  # close
            raise RuntimeError("WebSocket 已关闭")
        if opcode == 0x9:  # ping -> pong
            self._send_frame(0xA, payload)
            return self.recv_text()
        return payload.decode("utf-8", "replace")

    def close(self):
        try:
            self._send_frame(0x8, b"")
        except Exception:
            pass
        try:
            if self._sock:
                self._sock.close()
        except Exception:
            pass
        self._sock = None


# ------------------------------------------------------------
# CDP 浏览器控制器（单例）
# ------------------------------------------------------------
class BrowserController:
    def __init__(self):
        self._proc = None
        self._ws = None
        self._profile_dir = None
        self._page_id = 0
        self._lock = threading.Lock()
        self._browser_path = ""
        self._started = False
        self._port = 0
        self._dom_sessions = set()  # 已启用 DOM 域的会话 id 集合
        self._runtime_sessions = set()  # 已启用 Runtime 域的会话 id 集合
        self._id_map = {}   # data-wmb-id -> 定位句柄 {session,ctx,wmbid} 或 {session,backendNodeId}
        self._sessions = {}  # 子目标会话 id -> {type,url,title}（跨域 iframe / 弹窗页）
        self._frame_ctx = {}  # (session, frameId) -> executionContextId（各 frame 执行上下文）
        self._active_session = ""  # 当前操作的目标会话（空串=根会话/主标签页）

    # ---------- 生命周期 ----------
    def start(self, engine: str = "", headless: bool = False,
              window_pos: tuple = None) -> dict:
        """启动独立浏览器实例并连接 CDP。返回 (ok, message/页面清单)。

        window_pos: 可选 (left, top) 启动窗口位置。传 (-32000,-32000) 可让窗口
        启动即移出屏幕（后台化），不依赖运行期 setWindowBounds 成功与否——
        浏览器级端点拿 windowId 需显式 targetId，取不到时静默失败，窗口会一直
        弹在屏幕前，表现为「每次操作都打开浏览器」。
        """
        with self._lock:
            if self._started and self._proc and self._proc.poll() is None:
                return True, "浏览器已连接（独立实例运行中）"
            path = find_browser(engine)
            if not path:
                return False, "未找到 Edge/Chrome 浏览器，请先安装浏览器"
            self._browser_path = path
            # 固定持久 profile + 动态调试端口：
            # 用固定目录保存 cookie/token/登录态，关闭浏览器不删除，避免用户重复登录
            self._profile_dir = self._persistent_profile_dir()
            os.makedirs(self._profile_dir, exist_ok=True)
            self._kill_stale_profile_processes()
            port = self._pick_port()
            self._port = port
            self._dom_sessions = set()
            self._runtime_sessions = set()
            self._frame_ctx = {}
            self._id_map = {}
            cmd = [path, f"--remote-debugging-port={port}",
                   f"--user-data-dir={self._profile_dir}"]
            if headless:
                cmd.append("--headless")
            if window_pos:
                cmd.append("--window-position=%d,%d" % (
                    int(window_pos[0]), int(window_pos[1])))
            cmd += ["--no-first-run", "--no-default-browser-check",
                    "--disable-default-apps", "--disable-sync",
                    "--disable-background-networking", "--window-size=1280,800",
                    # 窗口移出屏幕（后台化）时 Chromium 会节流页面定时器/渲染，
                    # 导致网页版回复流式渲染极慢（实测 150s+）；显式禁用
                    "--disable-background-timer-throttling",
                    "--disable-renderer-backgrounding",
                    "about:blank"]
            try:
                self._proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                              stderr=subprocess.DEVNULL)
            except OSError as e:
                return False, f"启动浏览器失败: {e}"
            # 等待 DevTools 端口就绪
            deadline = time.time() + 20
            page = {}
            while time.time() < deadline:
                page = self._find_page_target(port)
                if page:
                    break
                time.sleep(0.3)
            if not page:
                self._cleanup()
                return False, "浏览器已启动但 DevTools 未就绪（可能被安全软件拦截）"
            try:
                # 连接浏览器级端点（而非单个页面），才能对跨域 iframe（OOPIF）/
                # 弹窗页等所有目标建立独立会话。
                bws = self._find_browser_ws(port)
                self._ws = _WS(timeout=20.0)
                self._ws.connect(bws or page.get("webSocketDebuggerUrl"))
                self._page_id = 1
                self._sessions = {}
                self._active_session = ""
                self._started = True
                # 浏览器级自动附加后续所有目标（新页面/跨域 iframe/弹窗），
                # 每个目标获得独立会话 id，元素定位/点击在所属会话内执行，
                # 突破单页会话无法访问跨进程帧（OOPIF）的限制。
                if bws:
                    try:
                        self._call("Target.setDiscoverTargets", {"discover": True})
                        self._call("Target.setAutoAttach",
                                   {"autoAttach": True, "flatten": True,
                                    "waitForDebuggerOnStart": False})
                        r = self._call("Target.attachToTarget",
                                       {"targetId": page.get("targetId"),
                                        "flatten": True})
                        sid = r.get("sessionId")
                        if sid:
                            self._active_session = sid
                            self._sessions[sid] = {"type": "page",
                                                   "url": (page.get("url") or "")[:200],
                                                   "title": (page.get("title") or "")[:100]}
                    except Exception:
                        pass
                return True, (f"已启动独立浏览器（{Path(path).name}，端口 {port}）。"
                              f"可用 browser_navigate 打开网页，browser_snapshot 查看页面。")
            except Exception as e:
                self._cleanup()
                return False, f"连接浏览器失败: {e}"

    def stop(self) -> dict:
        """关闭独立浏览器实例（不影响用户浏览器）。登录态/token 保留，下次打开自动恢复。"""
        with self._lock:
            self._cleanup()
            return True, ("已关闭浏览器操控实例（不影响用户正在使用的浏览器）。"
                          "登录态已保留，下次打开浏览器无需重复登录")

    @staticmethod
    def _persistent_profile_dir() -> str:
        """持久浏览器 profile 目录（~/.zhuzhu_Copilot/browser_profile）。
        存放 cookie/token/登录态，跨会话保留，避免用户重复登录。"""
        root = app_identity.data_root() / "browser_profile"
        return str(root)

    def _cleanup(self):
        # 优先优雅关闭（CDP Browser.close 会触发 cookie/登录态刷盘，保证 token 落盘持久化），
        # 避免强制杀进程丢失未刷盘的 cookie；优雅失败再兜底强杀。
        if self._ws and self._started:
            try:
                self._ws.send_text(json.dumps(
                    {"id": self._next_id(), "method": "Browser.close", "params": {}}))
                deadline = time.time() + 5
                while time.time() < deadline and self._proc and self._proc.poll() is None:
                    time.sleep(0.2)
            except Exception:
                pass
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None
        if self._proc and self._proc.poll() is None:
            try:
                subprocess.run(["taskkill", "/F", "/T", "/PID",
                                str(self._proc.pid)],
                               capture_output=True, timeout=10)
            except Exception:
                pass
        self._proc = None
        # 保留 profile 目录（cookie/token/登录态），不删除，避免用户重复登录
        self._profile_dir = None
        self._started = False
        self._dom_sessions = set()
        self._runtime_sessions = set()
        self._frame_ctx = {}
        self._id_map = {}
        self._sessions = {}
        self._active_session = ""

    def _pick_port(self) -> int:
        """选一个空闲端口（9000-9500 区间试探）。"""
        for port in range(9222, 9500):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                try:
                    s.bind(("127.0.0.1", port))
                    return port
                except OSError:
                    continue
        return 9333

    def _kill_stale_profile_processes(self):
        """清理仍占用本持久 profile 的残留浏览器进程。
        持久 profile 会被复用，若上次关闭不彻底（Edge 后台进程/启动加速）仍占着 profile 锁，
        新实例会忽略 --remote-debugging-port 并转投旧进程导致连不上 CDP。
        用 powershell Get-CimInstance 枚举（wmic 在新版 Windows 已移除，不可用）。"""
        if not self._profile_dir:
            return
        try:
            import subprocess as _sp
            ps = (f"Get-CimInstance Win32_Process -Filter \"Name='msedge.exe' or Name='chrome.exe'\" | "
                  f"Where-Object {{ $_.CommandLine -like '*{self._profile_dir}*' }} | "
                  f"Select-Object -ExpandProperty ProcessId")
            out = _sp.run(["powershell", "-NoProfile", "-Command", ps],
                          capture_output=True, timeout=15)
            pids = []
            for line in out.stdout.decode("utf-8", "replace").splitlines():
                line = line.strip()
                if line.isdigit():
                    pids.append(int(line))
            for pid in pids:
                try:
                    _sp.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                            capture_output=True, timeout=10)
                except Exception:
                    pass
        except Exception:
            pass

    def _find_page_target(self, port: int) -> dict:
        """通过 /json/list 找到第一个 page target（返回 {targetId,url,title,webSocketDebuggerUrl}）。"""
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/json/list", timeout=2) as r:
                targets = json.loads(r.read().decode("utf-8", "replace"))
            for t in targets:
                if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                    return {"targetId": t.get("id"),
                            "url": (t.get("url") or ""),
                            "title": (t.get("title") or ""),
                            "webSocketDebuggerUrl": t["webSocketDebuggerUrl"]}
        except Exception:
            pass
        return {}

    def _find_browser_ws(self, port: int) -> str:
        """通过 /json/version 获取浏览器级 WebSocket 端点（可控制所有目标）。"""
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/json/version", timeout=2) as r:
                v = json.loads(r.read().decode("utf-8", "replace"))
            return v.get("webSocketDebuggerUrl") or ""
        except Exception:
            return ""

    # ---------- CDP 命令 ----------
    def _call(self, method: str, params: dict, session: str = "",
              timeout: float = 20.0) -> dict:
        """发送 CDP 命令，返回 result 字典（如失败抛异常）。
        session 为子目标会话 id（跨域 iframe / 弹窗页）；空串表示当前根会话。
        收到事件（如 Target.attachedToTarget）时先记录再继续等待本命令的应答。"""
        if not self._ws or not self._started:
            raise RuntimeError("浏览器未连接，请先 browser_open")
        mid = self._next_id()
        payload = {"id": mid, "method": method, "params": params or {}}
        if session:
            payload["sessionId"] = session
        self._ws.send_text(json.dumps(payload))
        deadline = time.time() + timeout
        self._ws._sock.settimeout(timeout)
        while time.time() < deadline:
            try:
                msg = json.loads(self._ws.recv_text())
            except Exception:
                raise RuntimeError("浏览器通信中断")
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(msg["error"].get("message", "CDP 错误"))
                return msg.get("result", {})
            if msg.get("method"):
                self._on_event(msg)
        raise RuntimeError(f"CDP 命令 {method} 超时")

    def _on_event(self, msg: dict):
        """处理 CDP 事件：记录 Target.attachedToTarget（跨域 iframe/弹窗页的新会话）
        与 Runtime.executionContextCreated（各 frame 的执行上下文，供跨 frame JS 操作）。"""
        method = msg.get("method")
        params = msg.get("params") or {}
        sid = msg.get("sessionId") or ""
        if method == "Target.attachedToTarget":
            info = params.get("targetInfo") or {}
            s2 = params.get("sessionId")
            if s2:
                self._sessions[s2] = {"type": info.get("type") or "other",
                                      "url": (info.get("url") or "")[:200],
                                      "title": (info.get("title") or "")[:100]}
        elif method == "Target.detachedFromTarget":
            s2 = params.get("sessionId")
            if s2:
                self._sessions.pop(s2, None)
        elif method == "Runtime.executionContextCreated":
            ctx = params.get("context") or {}
            aux = ctx.get("auxData") or {}
            fid = aux.get("frameId")
            if fid and aux.get("isDefault") is not False:
                self._frame_ctx[(sid or self._active_session, fid)] = ctx.get("id")
        elif method == "Runtime.executionContextDestroyed" or method == "Runtime.executionContextsCleared":
            if method == "Runtime.executionContextsCleared":
                self._frame_ctx.clear()

    def _next_id(self) -> int:
        self._page_id += 1
        return self._page_id

    # ---------- 导航 / 截图 ----------
    def navigate(self, url: str) -> dict:
        url = (url or "").strip()
        if not url:
            return False, "缺少要打开的网址 url"
        if not url.lower().startswith(("http://", "https://", "about:", "file://")):
            url = "https://" + url
        self._call("Page.navigate", {"url": url}, session=self._active_session)
        # 等待加载：轮询 document.readyState 到 complete（上限 8s），
        # 替代固定 sleep —— 静态页秒开，重 JS 页等渲染，不白等也不误判未加载完
        deadline = time.time() + 8.0
        while time.time() < deadline:
            time.sleep(0.25)
            try:
                r = self._eval("document.readyState")
                if str(r.get("text", "")).strip() == "complete":
                    break
            except Exception:
                pass
        return True, f"已打开 {url}"

    def screenshot(self) -> str:
        """返回网页截图 data URL（自动缩放控制体积）。"""
        res = self._call("Page.captureScreenshot", {"format": "png",
                                                    "captureBeyondViewport": False},
                         session=self._active_session)
        b64 = res.get("data", "")
        if not b64:
            # 尝试 jpeg 兜底
            res = self._call("Page.captureScreenshot", {"format": "jpeg",
                                                        "quality": 80},
                             session=self._active_session)
            b64 = res.get("data", "")
        raw = base64.b64decode(b64)
        try:
            from PIL import Image
            import io
            im = Image.open(io.BytesIO(raw))
            if im.width > _MODEL_W:
                h = int(im.height * _MODEL_W / im.width)
                im = im.resize((_MODEL_W, h), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, "PNG")
            raw = buf.getvalue()
        except Exception:
            pass
        return "data:image/png;base64," + base64.b64encode(raw).decode()

    # ---------- DOM 解析（解析 HTML/CSS） ----------
    def _eval(self, js: str, session: str = None) -> dict:
        res = self._call("Runtime.evaluate",
                         {"expression": js, "returnByValue": True,
                          "awaitPromise": True},
                         session=self._active_session if session is None else session)
        if res.get("exceptionDetails"):
            return {"ok": False,
                    "text": "JS 执行异常: " + json.dumps(
                        res["exceptionDetails"].get("text", ""), ensure_ascii=False)}
        val = res.get("result", {}).get("value")
        return {"ok": True, "text": val if isinstance(val, str) else json.dumps(
            val, ensure_ascii=False) if val is not None else ""}

    # 可交互元素判定（CDP DOM 穿透遍历：同源/跨域 iframe + 开放/封闭 shadow DOM 全部可达）
    _INTERACTIVE_TAGS = {"a", "button", "input", "textarea", "select"}
    _INTERACTIVE_ROLES = {"button", "link", "checkbox", "textbox", "menuitem", "tab",
                          "option", "radio", "switch", "combobox", "dialog"}

    # 在元素所属 frame 上下文触发真实点击（DOM.resolveNode 自动定位到所属 frame，
    # 跨域 iframe 内也能执行；userGesture 保证事件带用户手势，兼容 React/Vue 合成事件）
    _CDP_CLICK_JS = r"""
function(){
  var rbt = ['button','input','textarea','select','a'];
  var tag = (this.tagName||'').toLowerCase();
  var target = this;
  if (rbt.indexOf(tag) < 0) {
    var anc = this;
    while (anc && anc.tagName && anc.tagName.toLowerCase() !== 'body') {
      var at = anc.tagName.toLowerCase();
      if (rbt.indexOf(at) >= 0 || (anc.getAttribute && (anc.getAttribute('onclick')
          || anc.getAttribute('role') === 'button'
          || anc.getAttribute('aria-label') || anc.getAttribute('title')))) {
        target = anc; break;
      }
      anc = anc.parentElement;
    }
  }
  var fired = false;
  try {
    target.click();
    var win = (target.ownerDocument || document).defaultView;
    var ev = new win.MouseEvent('click', {bubbles: true, cancelable: true, view: win});
    target.dispatchEvent(ev);
    fired = true;
  } catch(e){}
  return fired;
}
"""

    # 在元素所属 frame 上下文聚焦（跨域 iframe 输入框也能聚焦）
    _CDP_FOCUS_JS = r"""
function(){
  try {
    this.focus();
    var d = this.ownerDocument || document;
    return d.activeElement === this || (d.activeElement && this.contains(d.activeElement));
  } catch(e){ return false; }
}
"""

    def _ensure_dom(self, session: str):
        """为指定会话启用 DOM 域（跨域 iframe / 弹窗页各自独立会话）。"""
        if session not in self._dom_sessions:
            try:
                self._call("DOM.enable", {}, session=session)
            except Exception:
                pass
            self._dom_sessions.add(session)

    def _session_tree(self, session: str) -> list:
        """用 CDP DOM 域获取某会话（页面或 iframe）的穿透节点数组。
        pierce 穿透同源 iframe 与开放/封闭 shadow DOM。"""
        self._ensure_dom(session)
        try:
            doc = self._call("DOM.getDocument", {"depth": -1, "pierce": True},
                             session=session)
        except Exception:
            return []
        root = doc.get("root") or {}
        nodes = []
        stack = [root]
        while stack:
            n = stack.pop()
            nodes.append(n)
            children = n.get("children") or []
            if children:
                stack.extend(children)
        return nodes

    def _dom_tree(self, session: str = None) -> list:
        """兼容接口：活动会话的穿透节点数组。"""
        return self._session_tree(self._active_session if session is None else session)

    @staticmethod
    def _node_attrs(node: dict) -> dict:
        raw = node.get("attributes") or []
        attrs = {}
        for i in range(0, len(raw) - 1, 2):
            attrs[raw[i]] = raw[i + 1]
        return attrs

    def _mark_ids(self) -> list:
        """遍历活动页全部 frame（含跨域 iframe / shadow DOM）的可交互元素。
        用各 frame 的**执行上下文**（Runtime contextId）跑 JS，跨源 iframe 也能读到，
        返回 [{id,tag,text,type,placeholder,label,class}]；并把 id -> 定位句柄
        {session,ctx,wmbid,offL,offT} 存入 _id_map，点击/输入在元素所属 frame 上下文内执行。
        OOPIF 会话（独立目标）同样处理。"""
        out = []
        id_map = {}
        self._scan_frames(self._active_session, out, id_map)
        for sid, info in list(self._sessions.items()):
            if info.get("type") == "iframe" and sid != self._active_session:
                self._scan_frames(sid, out, id_map)
        self._id_map = id_map
        return out

    # ---------- frame 上下文遍历（跨域 iframe 可达） ----------
    _INTERACTIVE_SELECTOR = (
        'a,button,input,textarea,select,'
        '[role="button"],[role="link"],[role="checkbox"],[role="textbox"],'
        '[role="menuitem"],[role="tab"],[role="option"],[role="radio"],'
        '[contenteditable="true"],[onclick],'
        '[aria-label],[title],[data-testid],'
        '[class*="close" i],[class*="Close"],.close,.btn-close,.icon-close'
    )

    # 在某 frame 上下文内遍历其可交互元素（含 open shadow DOM），
    # 写入 data-wmb-id 并返回 [{id,text,placeholder,type,label,class}]。
    # 额外做"结构性关闭按钮"识别：弹窗（position:fixed 大层）右上角的纯图标元素
    # 往往就是关闭按钮，但无 aria-label/title/文字/close class（如抖音登录弹窗），
    # 靠位置启发式把它标记为「关闭」加入清单，AI 即可按编号点击。
    _FRAME_MARK_JS = r"""
(function(){
  var sel = __SEL__;
  var out = [];
  var idx = 0;
  // 清除上一次标记残留的 data-wmb-id / data-wmb-close（属性持久留在 DOM 上，
  // 不清除会导致第二次标记时旧元素被跳过、编号错位）
  var olds = document.querySelectorAll('[data-wmb-id],[data-wmb-close]');
  for (var oi=0;oi<olds.length;oi++){
    olds[oi].removeAttribute('data-wmb-id');
    olds[oi].removeAttribute('data-wmb-close');
  }
  function txt(el){
    if (el.getAttribute && el.getAttribute('data-wmb-close')) return '关闭';
    var t = (el.innerText||el.value||'').trim();
    if (!t) t = (el.getAttribute('aria-label')||'').trim();
    if (!t) t = (el.title||'').trim();
    if (!t) t = (el.placeholder||'').trim();
    if (!t) t = (el.getAttribute('data-testid')||'').trim();
    return t.replace(/\s+/g,' ').slice(0,60);
  }
  function handle(el){
    var r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return;
    idx++;
    el.setAttribute('data-wmb-id', String(idx));
    var text = txt(el);
    var placed = (el.placeholder||'').trim();
    var cls = (typeof el.className === 'string' ? el.className : '') || '';
    var tag = el.tagName.toLowerCase();
    var label = tag + (tag==='input' && el.type ? '(' + el.type + ')' : '')
                + '「' + (text || placed) + '」';
    out.push({id: idx, tag: tag, text: text, placeholder: placed,
              type: el.type||'', label: label, class: cls});
  }
  // 结构性关闭按钮：弹窗（fixed 遮罩/层）内部的最大面板，取其右上角纯图标元素
  function scanPopupClose(){
    var all = document.querySelectorAll('*');
    var vw = window.innerWidth, vh = window.innerHeight;
    var panels = [];
    var i, j;
    // 1) 收集候选弹窗面板：fixed 层（z>=100 或带弹窗关键词）内部的最大适中子容器；
    //    若 fixed 层自身非全屏（即弹窗本体），直接用它。
    for (i=0;i<all.length;i++){
      var el = all[i];
      var cs;
      try { cs = getComputedStyle(el); } catch(e){ continue; }
      if (cs.position !== 'fixed') continue;
      var r = el.getBoundingClientRect();
      if (r.width < 200 || r.height < 200) continue;
      var z = parseInt(cs.zIndex, 10) || 0;
      var s = ((typeof el.className === 'string' ? el.className : '') || '') + ' ' + (el.id||'');
      if (z < 100 && !/login|modal|popup|dialog|mask|qrcode|panel/i.test(s)) continue;
      var fullscreen = r.width >= vw * 0.9 && r.height >= vh * 0.9;
      if (fullscreen){
        // 全屏遮罩：内部最大的"面板"子容器（尺寸适中、位于视口内）
        var bestChild = null, bestArea = 0;
        var inner = el.querySelectorAll('*');
        for (j=0;j<inner.length;j++){
          var ch = inner[j];
          var cr = ch.getBoundingClientRect();
          if (cr.width < 200 || cr.height < 150) continue;
          if (cr.width > vw * 0.95 || cr.height > vh * 0.95) continue;
          if (cr.left < -1 || cr.top < -1) continue;
          var a = cr.width * cr.height;
          if (a > bestArea){ bestArea = a; bestChild = ch; }
        }
        if (bestChild) panels.push(bestChild);
      } else {
        panels.push(el);
      }
    }
    if (!panels.length) return null;
    // 取面积最大的面板
    var best = null, bestArea = 0;
    for (i=0;i<panels.length;i++){
      var pr = panels[i].getBoundingClientRect();
      var a = pr.width * pr.height;
      if (a > bestArea){ bestArea = a; best = panels[i]; }
    }
    var pr = best.getBoundingClientRect();
    var x0 = pr.right - 70, x1 = pr.right + 1, y0 = pr.top, y1 = pr.top + 70;
    var cands = [];
    var inner = best.querySelectorAll('*');
    for (var k=0;k<inner.length;k++){
      var el2 = inner[k];
      var er = el2.getBoundingClientRect();
      if (er.width < 16 || er.height < 16 || er.width > 90 || er.height > 90) continue;
      if (er.right < x0 || er.left > x1 || er.bottom < y0 || er.top > y1) continue;
      var hasIcon = el2.tagName === 'SVG' || el2.tagName === 'IMG' ||
                    (el2.querySelector && el2.querySelector('svg,img'));
      if (!hasIcon) continue;
      var t2 = (el2.innerText||'').trim();
      if (t2) continue;
      cands.push({el: el2, area: er.width * er.height});
    }
    if (!cands.length) return null;
    cands.sort(function(a,b){ return a.area - b.area; });
    return cands[0].el;
  }
  function walkDoc(doc){
    var nodes;
    try { nodes = doc.querySelectorAll(sel); } catch(e){ nodes = []; }
    for (var i=0;i<nodes.length;i++) handle(nodes[i]);
    // open shadow DOM
    var all = doc.querySelectorAll('*');
    for (var s=0;s<all.length;s++){
      var sr = all[s].shadowRoot;
      if (sr && sr !== doc) walkDoc(sr);
    }
  }
  walkDoc(document);
  // 弹窗右上角纯图标关闭按钮（无任何语义属性时兜底识别）
  var closeEl = scanPopupClose();
  if (closeEl && !closeEl.getAttribute('data-wmb-id')){
    closeEl.setAttribute('data-wmb-close','1');
    handle(closeEl);
  }
  return JSON.stringify(out);
})()
"""

    def _ensure_runtime(self, session: str):
        if session not in self._runtime_sessions:
            try:
                self._call("Runtime.enable", {}, session=session)
            except Exception:
                pass
            self._runtime_sessions.add(session)

    def _get_frames(self, session: str) -> list:
        """Page.getFrameTree → [{frameId,parentId}]（含跨域 iframe）。"""
        try:
            tree = self._call("Page.getFrameTree", {}, session=session)
        except Exception:
            return []
        frames = []

        def walk(node):
            f = node.get("frame") or {}
            frames.append({"frameId": f.get("id"),
                           "parentId": f.get("parentId") or ""})
            for ch in node.get("childFrames") or []:
                walk(ch)

        root = tree.get("frameTree") or {}
        if root:
            walk(root)
        return frames

    def _owner_rect(self, session: str, frame_id: str) -> tuple:
        """iframe 元素在其父 frame 视口中的矩形 (left,top,width,height)。
        DOM.getFrameOwner 返回父 frame 内的 owner 节点，resolve 后量取矩形。"""
        try:
            r = self._call("DOM.getFrameOwner", {"frameId": frame_id}, session=session)
            bid = r.get("backendNodeId")
            if not bid:
                return (0, 0, 0, 0)
            o = self._call("DOM.resolveNode", {"backendNodeId": bid}, session=session)
            oid = (o.get("object") or {}).get("objectId")
            if not oid:
                return (0, 0, 0, 0)
            res = self._call("Runtime.callFunctionOn", {
                "objectId": oid,
                "functionDeclaration": "function(){ var r=this.getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; }",
                "returnByValue": True}, session=session)
            val = res.get("result", {}).get("value")
            if isinstance(val, list) and len(val) == 4:
                return tuple(val)
        except Exception:
            pass
        return (0, 0, 0, 0)

    def _frame_offsets(self, session: str, frames: list) -> dict:
        """递归累计每个 frame 相对主视口的偏移（父偏移 + iframe 矩形）。"""
        if not frames:
            return {}
        top = frames[0]["frameId"]
        offsets = {top: (0, 0)}
        children = {}
        for f in frames:
            p = f.get("parentId")
            if p:
                children.setdefault(p, []).append(f["frameId"])
        stack = [top]
        while stack:
            fid = stack.pop()
            for cf in children.get(fid, []):
                ox, oy, _w, _h = self._owner_rect(session, cf)
                bx, by = offsets.get(fid, (0, 0))
                offsets[cf] = (bx + ox, by + oy)
                stack.append(cf)
        return offsets

    def _scan_frames(self, session: str, out: list, id_map: dict):
        self._ensure_runtime(session)
        # 派发几次指令确保 executionContextCreated 事件都收齐（含各 frame 上下文）
        for _ in range(2):
            self._call("Runtime.evaluate", {"expression": "1", "returnByValue": True},
                       session=session)
        frames = self._get_frames(session)
        if not frames:
            # 兜底：DOM pierce 扫描（无法取 frame 上下文时）
            self._scan_mark(session, out, id_map)
            return
        offsets = self._frame_offsets(session, frames)
        js = self._FRAME_MARK_JS.replace("__SEL__", json.dumps(self._INTERACTIVE_SELECTOR))
        for f in frames:
            fid = f["frameId"]
            ctx = self._frame_ctx.get((session, fid))
            if ctx is None:
                continue
            off = offsets.get(fid, (0, 0))
            try:
                res = self._call("Runtime.evaluate",
                                 {"expression": js, "returnByValue": True,
                                  "contextId": ctx}, session=session)
            except Exception:
                continue
            data = res.get("result", {}).get("value")
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except Exception:
                    data = None
            if not isinstance(data, list):
                continue
            for e in data:
                idx = len(out) + 1
                out.append({"id": idx,
                            "tag": e.get("tag", ""),
                            "text": e.get("text", ""),
                            "placeholder": e.get("placeholder", ""),
                            "type": e.get("type", ""),
                            "label": e.get("label", ""),
                            "class": e.get("class", "")})
                wm = e.get("id")
                if wm:
                    id_map[idx] = {"session": session, "ctx": ctx, "wmbid": wm,
                                   "offL": off[0], "offT": off[1]}

    def _scan_mark(self, session: str, out: list, id_map: dict):
        """兜底：DOM pierce 扫描某会话（无 frame 上下文可用时）。"""
        for node in self._session_tree(session):
            if node.get("nodeType") != 1:
                continue
            name = (node.get("nodeName") or "").lower()
            if name in ("script", "style", "noscript", "head", "meta", "link", "title"):
                continue
            attrs = self._node_attrs(node)
            role = (attrs.get("role") or "").lower()
            cls = attrs.get("class") or ""
            if not (name in self._INTERACTIVE_TAGS
                    or role in self._INTERACTIVE_ROLES
                    or attrs.get("contenteditable") == "true"
                    or "onclick" in attrs
                    or "aria-label" in attrs
                    or "title" in attrs
                    or "data-testid" in attrs
                    or "close" in cls.lower()
                    or "cancel" in cls.lower()):
                continue
            text_parts = []
            for ch in node.get("children") or []:
                if ch.get("nodeType") == 3 and (ch.get("nodeValue") or "").strip():
                    text_parts.append(ch["nodeValue"].strip())
            text = " ".join(text_parts)
            if not text:
                text = (attrs.get("aria-label") or "").strip()
            if not text:
                text = (attrs.get("title") or "").strip()
            if not text:
                text = (attrs.get("placeholder") or "").strip()
            if not text:
                text = (attrs.get("data-testid") or "").strip()
            text = re.sub(r"\s+", " ", text)[:60]
            idx = len(out) + 1
            bid = node.get("backendNodeId")
            placed = (attrs.get("placeholder") or "").strip()
            label = name
            if name == "input" and attrs.get("type"):
                label += "(" + attrs["type"] + ")"
            out.append({"id": idx, "tag": name, "text": text,
                        "placeholder": placed, "type": attrs.get("type") or "",
                        "label": label + "「" + (text or placed) + "」", "class": cls})
            if bid:
                id_map[idx] = {"session": session, "backendNodeId": bid}

    def get_interactive(self) -> list:
        return self._mark_ids()

    def summarize(self, limit: int = 40) -> str:
        elems = self.get_interactive()
        if not elems:
            return "（页面无可点击元素）"
        rows = []
        for e in elems[:limit]:
            rows.append(f"[{e['id']}] ({e['label']})")
        return "\n".join(rows)

    # ---------- 元素定位（frame 上下文 / CDP DOM 穿透双路径） ----------
    def _find_backend(self, eid: int = None, selector: str = None) -> dict:
        """按编号或 CSS 选择器返回定位句柄。
        eid：从 _id_map 取 → {session,ctx,wmbid,offL,offT}（frame 上下文）或
             {session,backendNodeId}（兜底 pierce）；
        selector：在活动页用 DOM.querySelector 解析 → {session,backendNodeId}。
        若 eid 不在缓存（DOM 已变化/未快照）自动重标记。"""
        if eid is not None:
            if not self._id_map or eid not in self._id_map:
                self._mark_ids()
            h = self._id_map.get(eid)
            return dict(h) if h else None
        if selector:
            try:
                doc = self._call("DOM.getDocument", {"depth": 1, "pierce": False},
                                 session=self._active_session)
                root_id = (doc.get("root") or {}).get("nodeId")
                if not root_id:
                    return None
                res = self._call("DOM.querySelector",
                                 {"nodeId": root_id, "selector": selector},
                                 session=self._active_session)
                node_id = res.get("nodeId")
                if not node_id or node_id == 0:
                    return None
                desc = self._call("DOM.describeNode", {"nodeId": node_id},
                                  session=self._active_session)
                bid = (desc.get("node") or {}).get("backendNodeId")
                return {"session": self._active_session,
                        "backendNodeId": bid} if bid else None
            except Exception:
                return None
        return None

    def _locate_point(self, eid: int = None, selector: str = None) -> dict:
        """返回元素在主视口的可点中心坐标 {x,y,w,h,session}。
        frame 上下文元素：在元素所属 frame 上下文内重新量取矩形 + 累计偏移；
        selector/兜底元素：DOM.getBoxModel（活动页视口）。供 Input 事件兜底点击。"""
        el = self._find_backend(eid, selector)
        if not el:
            return None
        if el.get("ctx") is not None and el.get("wmbid") is not None:
            js = (self._FIND_BY_ID_FN +
                  "(function(){ var e=findById(document, \"%d\"); "
                  "if(!e) return 'null'; "
                  "var r=e.getBoundingClientRect(); return JSON.stringify("
                  "{x:Math.round(r.left+r.width/2),y:Math.round(r.top+r.height/2),"
                  "w:Math.round(r.width),h:Math.round(r.height)}); })()") % int(el["wmbid"])
            try:
                res = self._call("Runtime.evaluate",
                                 {"expression": js, "returnByValue": True,
                                  "contextId": el["ctx"]}, session=el["session"])
                d = json.loads(res.get("result", {}).get("value"))
            except Exception:
                d = None
            if not d:
                return None
            return {"x": d["x"] + el.get("offL", 0),
                    "y": d["y"] + el.get("offT", 0),
                    "w": d["w"], "h": d["h"], "session": el["session"]}
        if el.get("backendNodeId"):
            try:
                m = self._call("DOM.getBoxModel",
                               {"backendNodeId": el["backendNodeId"]},
                               session=el["session"])
            except Exception:
                return None
            model = m.get("model") or {}
            border = model.get("border") or []
            if len(border) < 8:
                return None
            x = round(sum(border[0::2]) / 4.0)
            y = round(sum(border[1::2]) / 4.0)
            w = model.get("width") or round(abs(border[2] - border[0]))
            h = model.get("height") or round(abs(border[5] - border[1]))
            return {"x": x, "y": y, "w": w, "h": h, "session": el["session"]}
        return None

    @staticmethod
    def _locate_js(eid: int = None, selector: str = None) -> str:
        """生成定位元素的 JS 表达式（主文档内）：优先 eid（data-wmb-id），否则 selector。
        注意：仅用于主文档上下文，iframe 内元素需用 CDP 定位路径。"""
        if eid is not None and eid > 0:
            return f"document.querySelector('[data-wmb-id=\"{int(eid)}\"]')"
        if selector:
            return f"document.querySelector({json.dumps(selector)})"
        return "null"

    # 关闭按钮语义关键词（中英文 + 常见图标字符）
    _CLOSE_KEYS = ("关闭", "close", "取消", "x", "×", "✕", "✖", "✘")

    def find_by_text(self, text: str) -> int:
        """按文字查找元素编号（递归 iframe/shadow 清单）：先精确匹配，再最短子串命中。
        若目标是关闭/取消类按钮，自动扩展匹配关键词（含 aria-label/title/× 字符）。找不到返回 0。"""
        key = (text or "").strip()
        if not key:
            return 0
        elems = self.get_interactive()
        # 精确匹配（text 或 placeholder 与 key 完全相等）
        for e in elems:
            t = (e.get("text") or "").strip()
            p = (e.get("placeholder") or "").strip()
            if t == key or p == key:
                return e["id"]
        # 最短子串命中（越短越精确）
        best, best_len = 0, 10 ** 9
        for e in elems:
            t = (e.get("text") or "").strip()
            p = (e.get("placeholder") or "").strip()
            for cand in (t, p):
                if cand and key in cand and len(cand) < best_len:
                    best, best_len = e["id"], len(cand)
        if best:
            return best
        # 关闭/取消类按钮：按语义关键词扩展匹配（图标按钮无文字，靠 aria-label/title/× 字符）
        if any(k in key.lower() for k in self._CLOSE_KEYS):
            return self._find_close(elems)
        return 0

    def _find_close(self, elems: list = None) -> int:
        """定位关闭/取消类按钮（CDP 穿透清单，跨域 iframe/shadow DOM 同策略）：
        优先精确语义，其次 text/标签子串，最后 class 含 close/cancel。
        返回元素编号；找不到返回 0。"""
        elems = elems if elems is not None else self.get_interactive()
        exact = ("关闭", "取消", "close", "×", "✕", "✖", "✘", "x")
        for e in elems:
            if (e.get("text") or "").strip().lower() in exact:
                return e["id"]
        for e in elems:
            t = (e.get("text") or "").strip().lower()
            label = (e.get("label") or "").lower()
            if any(k in t or k in label for k in ("关闭", "取消", "close")):
                return e["id"]
        for e in elems:
            cls = (e.get("class") or "").lower()
            if "close" in cls or "cancel" in cls:
                return e["id"]
        return 0

    # 在某 frame 上下文内按 data-wmb-id 定位并真实点击（跨源 iframe / open shadow DOM 内同样生效）。
    # findById 递归搜索 open shadow root；先 el.click()，再用合成 click 事件兜底；
    # svg/span 图标按钮自动向上找可点击祖先。
    _FIND_BY_ID_FN = r"""
function findById(doc, id){
  var el = doc.querySelector('[data-wmb-id="' + id + '"]');
  if (el) return el;
  var all = doc.querySelectorAll('*');
  for (var i=0;i<all.length;i++){
    var sr = all[i].shadowRoot;
    if (sr){ var e2 = findById(sr, id); if (e2) return e2; }
  }
  return null;
}
"""

    _CLICK_BY_ID_JS = _FIND_BY_ID_FN + r"""
(function(){
  var el = findById(document, "%d");
  if (!el) return false;
  var rbt = ['button','input','textarea','select','a'];
  var tag = el.tagName.toLowerCase();
  var target = el;
  if (rbt.indexOf(tag) < 0) {
    var anc = el;
    while (anc && anc.tagName && anc.tagName.toLowerCase() !== 'body') {
      var at = anc.tagName.toLowerCase();
      if (rbt.indexOf(at) >= 0 || (anc.getAttribute && (anc.getAttribute('onclick')
          || anc.getAttribute('role') === 'button'
          || anc.getAttribute('aria-label') || anc.getAttribute('title')))) {
        target = anc; break;
      }
      anc = anc.parentElement;
    }
  }
  var fired = false;
  try {
    target.click();
    var win = (target.ownerDocument || document).defaultView;
    var ev = new win.MouseEvent('click', {bubbles: true, cancelable: true, view: win});
    target.dispatchEvent(ev);
    fired = true;
  } catch(e){}
  return fired;
})()
"""

    _FOCUS_BY_ID_JS = _FIND_BY_ID_FN + r"""
(function(){
  var el = findById(document, "%d");
  if (!el) return false;
  try {
    el.focus();
    var d = el.ownerDocument || document;
    return d.activeElement === el || (d.activeElement && el.contains(d.activeElement));
  } catch(e){ return false; }
})()
"""

    _CLEAR_BY_ID_JS = _FIND_BY_ID_FN + r"""
(function(){
  var el = findById(document, "%d");
  if (!el) return false;
  try { el.value=''; return true; } catch(e){ return false; }
})()
"""

    def _native_click(self, eid: int = None, selector: str = None) -> bool:
        """在元素所属 frame 上下文触发真实点击（跨域 iframe / shadow DOM 通用）。
        先 el.click()，再用合成 click 事件兜底；svg/span 图标按钮自动向上找可点击祖先。"""
        el = self._find_backend(eid, selector)
        if not el:
            return False
        try:
            if el.get("ctx") is not None and el.get("wmbid") is not None:
                res = self._call("Runtime.evaluate",
                                 {"expression": self._CLICK_BY_ID_JS % int(el["wmbid"]),
                                  "returnByValue": True, "userGesture": True,
                                  "awaitPromise": True, "contextId": el["ctx"]},
                                 session=el["session"])
                return res.get("result", {}).get("value") is True
            if el.get("backendNodeId"):
                o = self._call("DOM.resolveNode",
                               {"backendNodeId": el["backendNodeId"]},
                               session=el["session"])
                oid = (o.get("object") or {}).get("objectId")
                if not oid:
                    return False
                r = self._call("Runtime.callFunctionOn", {
                    "objectId": oid, "functionDeclaration": self._CDP_CLICK_JS,
                    "returnByValue": True, "userGesture": True, "awaitPromise": True},
                    session=el["session"])
                return r.get("result", {}).get("value") is True
        except Exception:
            pass
        return False

    def _page_snapshot_marker(self) -> str:
        """点击前记录页面状态指纹，供点击后校验是否变化。
        JS 指纹（URL+标题+主文档文本/元素数）+ 各 frame 上下文的全页元素数：
        即使关闭按钮在跨域 iframe 里（主文档不变），弹窗消失也会让对应 frame 元素数变化。"""
        res = self._eval(
            "JSON.stringify({u: location.href, t: document.title,"
            " n: document.body ? document.body.innerText.length : 0,"
            " e: document.getElementsByTagName('*').length})")
        base = res.get("text", "")
        total = 0
        try:
            sessions = [self._active_session]
            sessions += [sid for sid, i in self._sessions.items()
                         if i.get("type") == "iframe" and sid != self._active_session]
            for s in sessions:
                self._ensure_runtime(s)
                self._call("Runtime.evaluate", {"expression": "1", "returnByValue": True},
                           session=s)
                for f in self._get_frames(s):
                    ctx = self._frame_ctx.get((s, f["frameId"]))
                    if ctx is None:
                        continue
                    r = self._call("Runtime.evaluate", {
                        "expression": "(function(){ return document.getElementsByTagName('*').length; })()",
                        "returnByValue": True, "contextId": ctx}, session=s)
                    v = r.get("result", {}).get("value")
                    if isinstance(v, (int, float)):
                        total += int(v)
        except Exception:
            pass
        return base + "|N=" + str(total)

    def click(self, eid: int = None, text: str = None, selector: str = None,
              button: str = "left") -> tuple:
        """点击元素。eid（编号）/ text（文字）/ selector（CSS 选择器）三选一。返回 (ok, message, data_url)。
        优先原生 click（含合成事件兜底，跨域 iframe 内同样有效）；
        不适用时按元素所属会话坐标派发 Input 事件；点击后校验页面变化。"""
        if eid is None and text:
            eid = self.find_by_text(text)
        if (eid is None or eid <= 0) and not selector:
            return False, "未找到目标元素" + (f"（文字「{text}」）" if text else ""), None
        before = self._page_snapshot_marker()
        btn = {"left": "left", "right": "right", "middle": "middle"}.get(
            (button or "left").lower(), "left")
        # 标准元素 + 左键 → 原生 click（含合成事件兜底）
        if btn == "left":
            try:
                if self._native_click(eid, selector):
                    shot = self.screenshot()
                    after = self._page_snapshot_marker()
                    changed = (before and after and before != after)
                    return True, (f"已点击目标（原生 click）" +
                                  ("，页面已变化" if changed else "")), shot
            except Exception:
                pass
        # 坐标派发兜底（在元素所属会话内）
        pos = self._locate_point(eid, selector)
        if not pos:
            return False, f"目标定位失败", None
        x, y = pos["x"], pos["y"]
        try:
            self._call("Input.dispatchMouseEvent",
                       {"type": "mousePressed", "x": x, "y": y,
                        "button": btn, "clickCount": 1}, session=pos["session"])
            self._call("Input.dispatchMouseEvent",
                       {"type": "mouseReleased", "x": x, "y": y,
                        "button": btn, "clickCount": 1}, session=pos["session"])
        except Exception as e:
            return False, f"点击失败: {e}", None
        shot = self.screenshot()
        return True, f"已点击目标（{x},{y}）", shot

    def _focus_input(self, eid: int = None, selector: str = None) -> bool:
        """在元素所属 frame 上下文聚焦输入框（跨域 iframe 也能）。"""
        el = self._find_backend(eid, selector)
        if not el:
            return False
        try:
            if el.get("ctx") is not None and el.get("wmbid") is not None:
                res = self._call("Runtime.evaluate",
                                 {"expression": self._FOCUS_BY_ID_JS % int(el["wmbid"]),
                                  "returnByValue": True, "userGesture": True,
                                  "contextId": el["ctx"]}, session=el["session"])
                return res.get("result", {}).get("value") is True
            if el.get("backendNodeId"):
                o = self._call("DOM.resolveNode",
                               {"backendNodeId": el["backendNodeId"]},
                               session=el["session"])
                oid = (o.get("object") or {}).get("objectId")
                if not oid:
                    return False
                r = self._call("Runtime.callFunctionOn", {
                    "objectId": oid, "functionDeclaration": self._CDP_FOCUS_JS,
                    "returnByValue": True, "userGesture": True}, session=el["session"])
                return r.get("result", {}).get("value") is True
        except Exception:
            pass
        return False

    def _clear_input(self, eid: int = None, selector: str = None) -> bool:
        """在元素所属 frame 上下文清空输入框（跨域 iframe 也有效）。"""
        el = self._find_backend(eid, selector)
        if not el:
            return False
        try:
            if el.get("ctx") is not None and el.get("wmbid") is not None:
                res = self._call("Runtime.evaluate",
                                 {"expression": self._CLEAR_BY_ID_JS % int(el["wmbid"]),
                                  "returnByValue": True, "userGesture": True,
                                  "contextId": el["ctx"]}, session=el["session"])
                return res.get("result", {}).get("value") is True
            if el.get("backendNodeId"):
                o = self._call("DOM.resolveNode",
                               {"backendNodeId": el["backendNodeId"]},
                               session=el["session"])
                oid = (o.get("object") or {}).get("objectId")
                if not oid:
                    return False
                r = self._call("Runtime.callFunctionOn", {
                    "objectId": oid,
                    "functionDeclaration": "function(){ try { this.value=''; return true; } catch(e){ return false; } }",
                    "returnByValue": True, "userGesture": True}, session=el["session"])
                return r.get("result", {}).get("value") is True
        except Exception:
            pass
        return False

    def type_text(self, text: str, eid: int = None, target: str = None,
                  selector: str = None) -> tuple:
        """向输入框输入。eid / target 文字 / selector 三选一定位。优先 focus + insertText（跨域 iframe 通用）。"""
        if eid is None and target:
            eid = self.find_by_text(target)
        if (eid is None or eid <= 0) and not selector:
            return False, "未找到目标输入框" + (f"（{target}）" if target else ""), None
        el = self._find_backend(eid, selector)
        if not el:
            return False, "输入框定位失败", None
        sess = el["session"]
        try:
            if not self._focus_input(eid, selector):
                # focus 失败 → 元素所属会话坐标点击聚焦
                pos = self._locate_point(eid, selector)
                if not pos:
                    return False, "输入框定位失败", None
                self._call("Input.dispatchMouseEvent",
                           {"type": "mousePressed", "x": pos["x"], "y": pos["y"],
                            "button": "left", "clickCount": 1}, session=sess)
                self._call("Input.dispatchMouseEvent",
                           {"type": "mouseReleased", "x": pos["x"], "y": pos["y"],
                            "button": "left", "clickCount": 1}, session=sess)
            # 在元素自身会话清空后输入（Input.insertText 送入当前聚焦的会话）
            self._clear_input(eid, selector)
            self._call("Input.insertText", {"text": text}, session=sess)
        except Exception as e:
            return False, f"输入失败: {e}", None
        shot = self.screenshot()
        return True, f"已向输入框输入「{text}」", shot

    # ---------- 滑动（滚动） ----------
    _SCROLL_BODY = r"""
  var el = %s;
  var d = %s; var amt = %d;
  var view = el === document.scrollingElement || el === document.documentElement ||
             el === document.body;
  function vh(){ return (view ? window.innerHeight : el.clientHeight) || 800; }
  var step = amt > 0 ? amt : Math.round(vh() * 0.8);
  var before = view ? window.scrollY : el.scrollTop;
  if (d === 'top') { if (view) window.scrollTo(0,0); else el.scrollTop = 0; }
  else if (d === 'bottom') { if (view) window.scrollTo(0, el.scrollHeight||document.body.scrollHeight); else el.scrollTop = el.scrollHeight; }
  else {
    var dx = 0, dy = 0;
    if (d === 'down') dy = step;
    else if (d === 'up') dy = -step;
    else if (d === 'right') dx = step;
    else if (d === 'left') dx = -step;
    else return JSON.stringify({ok:false, error:'未知方向: '+d});
    if (view) window.scrollBy({left:dx, top:dy, behavior:'auto'});
    else el.scrollBy({left:dx, top:dy, behavior:'auto'});
  }
  var after = view ? window.scrollY : el.scrollTop;
  return JSON.stringify({ok:true, moved: after - before, pos: after});
"""

    def _scroll_cdp(self, el: dict, direction: str, amount: int) -> tuple:
        """在元素所属 frame 上下文滚动指定容器（跨域 iframe 内滚动条也能滚动）。"""
        dir_ = (direction or "down").strip().lower()
        amt = int(amount) if amount else 0
        try:
            if el.get("ctx") is not None and el.get("wmbid") is not None:
                js = (self._FIND_BY_ID_FN + "(function(){" + (self._SCROLL_BODY % (
                    "findById(document, \"%d\")" % int(el["wmbid"]),
                    json.dumps(dir_), amt)) + "})()")
                res = self._call("Runtime.evaluate",
                                 {"expression": js, "returnByValue": True,
                                  "awaitPromise": True, "contextId": el["ctx"]},
                                 session=el["session"])
                val = res.get("result", {}).get("value")
            elif el.get("backendNodeId"):
                o = self._call("DOM.resolveNode", {"backendNodeId": el["backendNodeId"]},
                               session=el["session"])
                oid = (o.get("object") or {}).get("objectId")
                if not oid:
                    return False, "滚动目标定位失败", None
                js = "(function(){" + (self._SCROLL_BODY % ("this", json.dumps(dir_), amt)) + "})()"
                res = self._call("Runtime.callFunctionOn", {
                    "objectId": oid, "functionDeclaration": js,
                    "returnByValue": True, "awaitPromise": True}, session=el["session"])
                val = res.get("result", {}).get("value")
            else:
                return False, "滚动目标定位失败", None
            if isinstance(val, dict) and val.get("ok") is False:
                return False, val.get("error", "滚动失败"), None
        except Exception as e:
            return False, f"滚动失败: {e}", None
        shot = self.screenshot()
        return True, f"已滚动页面（{dir_}）", shot

    def scroll(self, direction: str = "down", amount: int = None,
               eid: int = None, selector: str = None) -> tuple:
        """滚动页面或容器。
        - direction: up/down/left/right/top/bottom（top=滚到顶部，bottom=滚到底部）
        - amount: 像素步长（默认一屏 80% 高度）
        - eid / selector: 指定滚动容器（可选；留空滚动整个窗口，CDP 定位支持跨域 iframe）
        返回 (ok, message, data_url)。"""
        # 指定容器：优先 frame 上下文 / CDP 穿透定位（跨域 iframe 内容器也有效）
        if eid is not None or selector:
            el = self._find_backend(eid, selector)
            if el:
                return self._scroll_cdp(el, direction, amount)
            if eid is not None:
                return False, "滚动容器定位失败", None
        if eid is None and not selector:
            el_js = "document.scrollingElement || document.documentElement"
        else:
            el_js = self._locate_js(eid, selector)
        amount = int(amount) if amount else 0
        dir_ = (direction or "down").strip().lower()
        js = r"""
(function(){
  var el = %s;
  if (!el) return JSON.stringify({ok:false, error:'未找到滚动目标'});
  var d = %s;
  var amt = %d;
  var view = el === document.scrollingElement || el === document.documentElement ||
             el === document.body;
  function vh(){ return (view ? window.innerHeight : el.clientHeight) || 800; }
  var step = amt > 0 ? amt : Math.round(vh() * 0.8);
  var before = view ? window.scrollY : el.scrollTop;
  if (d === 'top') { if (view) window.scrollTo(0,0); else el.scrollTop = 0; }
  else if (d === 'bottom') { if (view) window.scrollTo(0, el.scrollHeight||document.body.scrollHeight); else el.scrollTop = el.scrollHeight; }
  else {
    var dx = 0, dy = 0;
    if (d === 'down') dy = step;
    else if (d === 'up') dy = -step;
    else if (d === 'right') dx = step;
    else if (d === 'left') dx = -step;
    else return JSON.stringify({ok:false, error:'未知方向: '+d});
    if (view) window.scrollBy({left:dx, top:dy, behavior:'auto'});
    else el.scrollBy({left:dx, top:dy, behavior:'auto'});
  }
  var after = view ? window.scrollY : el.scrollTop;
  return JSON.stringify({ok:true, moved: after - before, pos: after});
})()
""" % (el_js, json.dumps(dir_), amount)
        res = self._eval(js)
        if not res.get("ok"):
            return False, "滚动失败", None
        try:
            d = json.loads(res.get("text"))
            if not d.get("ok"):
                return False, d.get("error", "滚动失败"), None
        except Exception:
            pass
        shot = self.screenshot()
        return True, f"已滚动页面（{dir_}）", shot

    def eval(self, js: str) -> dict:
        if not (js or "").strip():
            return {"ok": False, "text": "缺少 JS 代码"}
        return self._eval(js)

    def html(self, selector: str = "") -> dict:
        """读取网页 HTML/文本。selector 留空返回 <body> 文本摘要。"""
        js = r"""
(function(){
  var sel = %s;
  var el = sel ? document.querySelector(sel) : null;
  if (!el && sel) return JSON.stringify({error:'选择器未匹配: '+sel});
  var node = el || document.body;
  var html = node.outerHTML ? node.outerHTML : '';
  var text = (node.innerText||'').trim().replace(/\n{3,}/g,'\n\n');
  return JSON.stringify({html: html.slice(0, 6000), text: text.slice(0, 4000),
                         len: html.length});
})()
""" % json.dumps(selector or "")
        return self._eval(js)

    # ---------- 多标签页（弹窗/新窗口） ----------
    def list_pages(self) -> list:
        """列出独立浏览器实例内的所有页面标签（活动页 + window.open 弹出的新窗口/新标签）。
        返回 [{id,title,url,session}]，供切换到弹窗页再操作。"""
        pages = []
        try:
            t = self._eval("JSON.stringify({title: document.title||'', url: location.href||''})")
            d = json.loads(t.get("text") or "{}")
            pages.append({"id": 1, "title": (d.get("title") or "")[:40],
                          "url": (d.get("url") or "")[:80], "session": self._active_session})
        except Exception:
            pages.append({"id": 1, "title": "", "url": "", "session": self._active_session})
        for sid, info in self._sessions.items():
            if info.get("type") == "page" and sid != self._active_session:
                pages.append({"id": len(pages) + 1,
                              "title": (info.get("title") or "")[:40],
                              "url": (info.get("url") or "")[:80],
                              "session": sid})
        return pages

    def switch_tab(self, index: int) -> tuple:
        """切换到指定编号的页面标签（后续 browser_snapshot/browser_click 等针对该标签）。
        返回 (ok, message)。弹窗/新窗口里的关闭按钮：切到对应标签后 snapshot 再点击。"""
        if not self._started:
            return False, "浏览器未启动"
        pages = self.list_pages()
        if index < 1 or index > len(pages):
            return False, f"标签页编号超出范围（共 {len(pages)} 个）"
        p = pages[index - 1]
        self._active_session = p["session"]
        self._id_map = {}  # 强制重新标记
        return True, f"已切换到标签页: {p['title']} ({p['url']})"


# 全局单例
_CTRL = BrowserController()


def controller() -> BrowserController:
    return _CTRL