"""可视化预览：把 AI 生成的可视化产物送到**用户自己的浏览器**里展示，并可刷新。

为什么要有这一层
----------------
1. 「看到」才算交付。AI 产出页面 / 原型 / 图表 / 报告后，用户需要在自己的浏览器里
   看到结果；内置 WebView 面板承担的是 `browser_*` 网页自动化，不负责展示产物。
2. 外部浏览器进程无法被本进程直接触发 reload。要让用户在产物变化后看到最新内容，
   只有两条路：重新拉起一个 URL（会开新标签页），或者让**页面自己**去问有没有新版本。
   本模块选后者：起一个只监听回环地址的本地小服务托管预览页面，页面内注入轮询脚本，
   内容版本一变就 `location.reload()`。
3. 「刷新」因此有两个来源，二者共用同一份版本号：
   · agent 主动调 `preview_refresh`（每完成一步都能刷新给用户看）；
   · 程序侧兜底 —— 托管源（本地文件）的 mtime 变化时自动 bump 版本。

安全：仅绑定 127.0.0.1、端口由系统分配；只提供 / 与 /v 两个只读端点，无任何写入接口。
网络防御模块对回环地址不做封禁（见 network_defense._is_loopback）。
"""

import os
import threading
import urllib.parse
import webbrowser
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# 预览页面内的版本轮询间隔（毫秒）：越小越跟手，越大越省。700ms ≈ 人眼无感的跟手延迟
PREVIEW_POLL_MS = 700
# 服务端监听托管源文件变化的间隔（秒）：文件写入与页面刷新之间的最大滞后
SOURCE_WATCH_INTERVAL = 0.9
# 单次读取托管源文件的字节上限（防超大产物把内存打满；超出则提示走浏览器直接打开）
MAX_PREVIEW_BYTES = 8 * 1024 * 1024


def _esc(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _poll_script() -> str:
    """注入到预览页面的版本轮询脚本（无外部依赖，不阻断页面自身脚本）。"""
    return (
        "<script>(function(){var v=null;"
        "function tick(){fetch('/v',{cache:'no-store'})"
        ".then(function(r){return r.text();})"
        ".then(function(t){if(v===null){v=t;return;}if(t!==v){location.reload();}})"
        ".catch(function(){});}"
        f"setInterval(tick,{int(PREVIEW_POLL_MS)});tick();}})();</script>"
    )


def _inject(html: str) -> str:
    """把轮询脚本插到页面末尾（有 </body> 就插在它前面）。"""
    script = _poll_script()
    low = html.lower()
    pos = low.rfind("</body>")
    if pos >= 0:
        return html[:pos] + script + html[pos:]
    return html + script


def _blank_page(title: str, text: str) -> str:
    """无内容时的占位页（用户浏览器不应看到空白/报错）"""
    return ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
            f"<title>{_esc(title or '预览')}</title></head>"
            "<body style=\"margin:0;height:100vh;display:flex;align-items:center;"
            "justify-content:center;background:#101216;color:#9BA3B0;"
            "font-family:'Microsoft YaHei',system-ui,sans-serif;font-size:15px;\">"
            f"<div>{_esc(text)}</div></body></html>")


class _Handler(BaseHTTPRequestHandler):
    """只读端点：/ = 预览页面，/v = 内容版本号（页面轮询用）。

    绑定到具体的 Hub 实例（而不是模块单例）：多个 Hub 各自起服务时互不串内容。
    """

    def __init__(self, *args, hub: "PreviewHub" = None, **kwargs):
        self._hub = hub
        super().__init__(*args, **kwargs)

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        hub = self._hub
        if path in ("/", "/index.html"):
            self._send(200, hub.page().encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/v":
            self._send(200, str(hub.version).encode("utf-8"), "text/plain; charset=utf-8")
        else:
            self._send(404, b"not found", "text/plain; charset=utf-8")

    def log_message(self, *a):     # 静默：预览页每 700ms 一次轮询，不能刷日志
        pass


class PreviewHub:
    """本地预览托管 + 版本号（线程安全）。"""

    def __init__(self):
        self._lock = threading.RLock()
        self._html = ""
        self._title = ""
        self._version = 0
        self._source = ""          # 托管源文件路径（内容变化即自动刷新，见 reload_source）
        self._port = 0
        self._server = None
        self._watcher = None
        self._watch_stop = threading.Event()

    # ---------- 状态 ----------
    @property
    def version(self) -> int:
        with self._lock:
            return self._version

    @property
    def source(self) -> str:
        with self._lock:
            return self._source

    def url(self) -> str:
        return f"http://127.0.0.1:{self._port}/" if self._port else ""

    def page(self) -> str:
        with self._lock:
            html = self._html
            title = self._title
        if not html:
            return _blank_page(title, "AI 还没有可预览的内容。")
        return _inject(html)

    def has_content(self) -> bool:
        """是否已有可展示的预览内容（用于给出准确的成功/失败提示）"""
        with self._lock:
            return bool(self._html)

    # ---------- 内容 ----------
    def set_html(self, html: str, title: str = ""):
        with self._lock:
            self._html = str(html or "")
            if title:
                self._title = str(title)
            self._source = ""

    def set_source(self, path: str) -> tuple:
        """把预览内容指向本地文件：立即读取一次（后续按内容变化自动跟随）。"""
        p = Path(str(path or "")).expanduser()
        if not p.is_file():
            return False, f"文件不存在: {path}"
        try:
            if p.stat().st_size > MAX_PREVIEW_BYTES:
                return False, (f"文件过大（>{MAX_PREVIEW_BYTES // (1024 * 1024)}MB），"
                               f"请直接在浏览器打开: {p}")
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return False, f"读取失败: {e}"
        with self._lock:
            self._html = text
            self._title = p.stem
            self._source = str(p)
        return True, ""

    def drop_source(self):
        with self._lock:
            self._source = ""

    def bump(self) -> int:
        """版本 +1：已打开的预览页面会在一个轮询周期内自刷新。"""
        with self._lock:
            self._version += 1
            return self._version

    def reload_source(self) -> bool:
        """重新读取托管源文件；**内容**有变化则返回 True（调用方决定是否 bump）。

        变化判定以内容为准，不能只看 mtime+size：Windows 文件时间的时钟刻度约 15.6ms，
        两次相隔极近的写入会拿到同一个 mtime；若此时大小又恰好相同（例如把页面里的 A
        改成 B 这类等长改写），就会被误判成「没变」而漏掉刷新 —— CI 上就是这样漏的，
        对用户则表现为「AI 改完产物，浏览器里还是旧的」。预览文件本身有尺寸上限
        （MAX_PREVIEW_BYTES），重读一次判断内容是否变化的代价可忽略。
        """
        with self._lock:
            src = self._source
            old = self._html
        if not src:
            return False
        try:
            text = Path(src).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        if text == old:
            return False          # 只是被读取 → 不打扰用户正在看的页面
        ok, _msg = self.set_source(src)
        return ok

    # ---------- 服务 ----------
    def ensure_server(self) -> int:
        """启动（或复用）本地预览服务，返回端口号；失败返回 0。"""
        with self._lock:
            if self._port:
                return self._port
            try:
                srv = ThreadingHTTPServer(("127.0.0.1", 0),
                                          partial(_Handler, hub=self))
            except OSError:
                return 0
            srv.daemon_threads = True
            self._server = srv
            self._port = int(srv.server_address[1])
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            self._watch_stop.clear()
            self._watcher = threading.Thread(target=self._watch_loop, daemon=True)
            self._watcher.start()
            return self._port

    def _watch_loop(self):
        """兜底自动刷新：托管源文件被改写（AI 又写了一遍文件）→ 自动 bump 版本。"""
        while not self._watch_stop.wait(SOURCE_WATCH_INTERVAL):
            try:
                if not self.source:
                    continue
                if self.reload_source():
                    self.bump()
            except Exception:
                continue

    def shutdown(self):
        """收尾：停掉监听与服务（服务线程为守护线程，这里显式关闭避免端口悬挂）"""
        self._watch_stop.set()
        try:
            if self._server is not None:
                self._server.shutdown()
                self._server.server_close()
        except Exception:
            pass
        self._server = None
        self._port = 0


_HUB = PreviewHub()
_OPENED_URL = ""       # 最近一次交给外部浏览器的预览地址


def _open_external(url: str) -> tuple:
    """在**用户默认浏览器**打开（不指定/不强制任何浏览器）。"""
    if not url:
        return False, "无可用预览地址"
    try:
        if hasattr(os, "startfile"):          # Windows：走系统文件关联 = 用户默认浏览器
            os.startfile(url)                 # type: ignore[attr-defined]
            return True, ""
    except OSError as e:
        reason = str(e)
        try:
            if webbrowser.open(url):
                return True, ""
        except Exception:
            pass
        return False, f"无法打开浏览器: {reason}"
    try:
        if webbrowser.open(url):
            return True, ""
    except Exception as e:
        return False, f"无法打开浏览器: {e}"
    return False, "无法打开浏览器（未找到可用的默认浏览器）"


def open_preview(path: str = "", html: str = "", url: str = "", title: str = "") -> tuple:
    """在用户默认浏览器里打开可视化预览。

    - url  ：直接打开给定网址（不经过本地托管）
    - path ：打开本地 HTML 产物（经本地托管，因此支持刷新与自动兜底刷新）
    - html ：直接给一段 HTML（同样经本地托管）
    三者按 url > path > html 取第一个有效项。
    """
    global _OPENED_URL
    url = str(url or "").strip()
    if url:
        ok, msg = _open_external(url)
        if ok:
            _OPENED_URL = url
        return ok, (f"已在你的浏览器打开: {url}" if ok else msg)
    path = str(path or "").strip()
    if path:
        ok, msg = _HUB.set_source(path)
        if not ok:
            return False, msg
    elif html:
        _HUB.set_html(html, title)
    if not _HUB.has_content():
        return False, "没有可预览的内容：请给出要展示的 HTML 文件路径、HTML 内容或网址"
    port = _HUB.ensure_server()
    if not port:
        return False, "本地预览服务启动失败（端口不可用）"
    _HUB.bump()
    link = _HUB.url()
    ok, msg = _open_external(link)
    if ok:
        _OPENED_URL = link
        return True, f"已在你的浏览器打开预览: {link}"
    return False, msg


def refresh_preview() -> tuple:
    """刷新已在用户浏览器中打开的预览页面（agent 每完成一步都可以调用）。"""
    global _OPENED_URL
    if not _OPENED_URL:
        return False, "当前没有已打开的预览；请先调用 preview_open 打开展示"
    if _HUB.source:
        _HUB.reload_source()          # 文件已被改写 → 先把最新内容读进来
    _HUB.bump()                       # 版本变化 → 页面在一个轮询周期内自刷新
    if not _HUB.url():
        ok, msg = _open_external(_OPENED_URL)   # 本地服务未起（曾直接打开网址）
        return (True, f"已重新打开: {_OPENED_URL}") if ok else (False, msg)
    return True, f"已刷新浏览器中的预览: {_HUB.url()}"


def notify_source_changed(path: str) -> bool:
    """程序侧兜底入口：AI 改写/重写了某个文件时调用。

    只有当该文件正是当前托管源时才动作 —— 否则会把用户正在看的页面换成别的东西。
    """
    p = str(path or "").strip()
    if not p:
        return False
    try:
        same = os.path.normcase(os.path.abspath(p)) == \
            os.path.normcase(os.path.abspath(_HUB.source or ""))
    except Exception:
        same = False
    if not same:
        return False
    if not _HUB.reload_source():
        return False      # 文件没变（例如只是被读取）→ 不打扰用户正在看的页面
    _HUB.bump()
    return True


def is_open() -> bool:
    """是否有已打开的预览（供提示词/工具给出准确状态）"""
    return bool(_OPENED_URL)


def current_url() -> str:
    return _HUB.url() or _OPENED_URL


def shutdown():
    """退出时收尾：关闭本地预览服务（并复位端口，下次使用时重新分配）"""
    _HUB.shutdown()
