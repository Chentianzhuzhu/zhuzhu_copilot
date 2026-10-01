"""zhuzhu Copilot 的本地数据服务：把桌面端已有的**真实数据**以 JSON 暴露给 Web 前端。

**为什么要有它**：桌面端每个窗口（主面板 / 四个浮窗 / 设置页 / 下拉 / 菜单）都是**独立顶层
窗口**，各自渲染一份背景 —— 相邻面板的壁纸无法逐像素连续（用户反馈的「每个模块会重复使用
一张背景」）。Web 前端是**单文档**：整页一张背景，所有面板与弹层都只是同一张图上的
`backdrop-filter` 层，这个问题从结构上就不存在。

**设计约束**：
  · 零新增依赖：只用标准库 ``http.server``；
  · **不重复实现**桌面端的颜色推导：令牌（CSS 变量）由 `agent_panel` 里
    同一批入口算出来，前端只负责套用 —— 两边永远同一套颜色，不存在"第二份真值"；
  · 只读：不改用户数据；
  · Qt 用离屏平台（`QT_QPA_PLATFORM=offscreen`）：颜色推导要走 QColor/QPixmap，
    没有 QGuiApplication 时 QPixmap 会让进程直接崩（历史坑）。

用法::

    python -m zhuzhu_Copilot.web.server [--host 127.0.0.1] [--port 8765]

环境变量：``ZHUZHU_WEB_HOST`` / ``ZHUZHU_WEB_PORT``。
"""
from __future__ import annotations

import json
import mimetypes
import os
import socket
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

# Qt 必须在任何 PyQt6 导入之前定下平台；离屏平台没有窗口系统依赖。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
# 单张壁纸的上限：只在服务端做一次流式转发，不整块读进内存
_CHUNK = 256 * 1024


# ══════════════════════════ 数据读取（可直接被测试调用） ══════════════════════════

_qt_app = None


def ensure_qt():
    """确保存在 QApplication（离屏）。

    **必须持有引用**：`QApplication([])` 不赋值给任何名字时会被 Python 立刻回收，
    `QApplication.instance()` 随即变 None —— 之后任何 `QPixmap` 都会让进程直接崩
    （0xC0000409），而且崩在解码里、看不出跟"app 没了"有关。
    """
    global _qt_app
    if _qt_app is not None:
        return
    from PyQt6.QtWidgets import QApplication
    _qt_app = QApplication.instance() or QApplication([])


def _agent_panel():
    ensure_qt()
    from zhuzhu_Copilot.ui import agent_panel
    return agent_panel


def _app_wallpaper():
    ensure_qt()
    from zhuzhu_Copilot.core import app_wallpaper
    return app_wallpaper


def sessions_dir() -> Path:
    """会话目录：复用桌面端的 `agent_skills.CONFIG_DIR`，不另写一份路径。"""
    from zhuzhu_Copilot.core import agent_skills
    return Path(agent_skills.CONFIG_DIR) / "sessions"


def _palette(ap) -> dict:
    """主题色板 → 前端 CSS 变量（键名即桌面端模块常量的名字，便于对照）。"""
    return {
        "text": ap.TEXT,
        "textDim": ap.TEXT_DIM,
        "accent": ap.ACCENT,
        "accentHover": ap.ACCENT_HOVER,
        "border": ap.BORDER,
        "borderSoft": ap.BORDER_SOFT,
        "bg": ap.BG,
        "panel": ap.PANEL,
        "card": ap.CARD,
        "aiBg": ap.AI_BG,
        "userBg": ap.USER_BG,
        "link": ap.LINK_COLOR,
        "ok": ap.OK,
        "warn": ap.WARN,
        "err": ap.ERR,
        # 浅底上要用的深字（`_popup_fg` 在浅色弹层底上就是取它）—— 两个值都来自色板
        "textOnLight": ap._THEMES["light"]["TEXT"],
    }


def _rgb(color: str) -> str:
    """归一成 `#RRGGBB`（色板里可能带 alpha，直接给 CSS 会不认）。"""
    from PyQt6.QtGui import QColor
    return QColor(color).name()


def wallpaper_info() -> Optional[dict]:
    """当前背景图信息（无壁纸 / 无法解码时返回 None）。"""
    w = _app_wallpaper()
    p = w.params()
    path = p.bg_image or ""
    if not path or not w.active():
        return None
    f = Path(path)
    try:
        mtime = f.stat().st_mtime_ns
    except OSError:
        return None
    return {
        "url": f"/api/wallpaper?v={mtime}",
        "name": f.name,
        "fit": p.bg_fit,
        "blur": p.bg_blur,      # 高斯模糊半径（px）：web 端按此对齐桌面观感
        "dim": p.bg_dim,        # 压暗强度（%）
        "bytes": f.stat().st_size,
    }


def theme_payload() -> dict:
    """`GET /api/theme`：主题色板 + 壁纸信息（颜色只有桌面端这一个来源）。"""
    return {
        "palette": _palette(_agent_panel()),
        "wallpaper": wallpaper_info(),
    }


def wallpaper_file() -> Optional[Path]:
    w = _app_wallpaper()
    path = w.params().bg_image or ""
    if not path or not w.active():
        return None
    return Path(path)


def sessions_payload() -> list:
    """`GET /api/sessions`：真实会话列表，最近更新在前。"""
    f = sessions_dir() / "sessions.json"
    try:
        lst = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(lst, list):
        return []
    out = [s for s in lst if isinstance(s, dict) and s.get("id")]
    out.sort(key=lambda s: s.get("updated") or 0, reverse=True)
    return [{"id": s["id"], "name": s.get("name") or "新对话",
             "created": s.get("created") or 0, "updated": s.get("updated") or 0,
             "workdir": s.get("workdir") or ""} for s in out]


def _norm_seg(seg: dict) -> dict:
    """把桌面端的分段归一化成前端好渲染的形状（保留原文，不丢信息）。"""
    kind = seg.get("type") or ""
    if kind == "think":
        return {"kind": "think", "html": seg.get("html") or ""}
    if kind == "text":
        return {"kind": "text", "html": seg.get("raw") or seg.get("html") or ""}
    if kind == "op":
        return {"kind": "op", "name": seg.get("name") or "", "html": seg.get("html") or "",
                "tip": seg.get("tip") or "", "out": seg.get("out") or ""}
    if kind == "result":
        return {"kind": "result", "html": seg.get("html") or "", "cmd": seg.get("cmd") or ""}
    return {"kind": "other", "html": seg.get("html") or ""}


def session_payload(sid: str) -> Optional[dict]:
    """`GET /api/session/{id}`：某会话的对话行（真实 `.ui.json`）。"""
    if not sid or "/" in sid or "\\" in sid or ".." in sid:
        return None                      # 防目录穿越：id 只允许是纯文件名
    d = sessions_dir()
    f = d / f"{sid}.ui.json"
    if not f.is_file():
        return None
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return None
    meta = next((s for s in sessions_payload() if s["id"] == sid), {})
    messages = []
    for row in (data.get("rows") or []):
        if not isinstance(row, dict):
            continue
        if row.get("type") == "user":
            messages.append({"role": "user", "text": row.get("text") or ""})
        elif row.get("type") == "ai":
            messages.append({
                "role": "ai",
                "segs": [_norm_seg(s) for s in (row.get("segs") or []) if isinstance(s, dict)],
                "cost": row.get("cost"),
                "meta": row.get("meta") or "",
            })
    return {"id": sid, "name": meta.get("name") or "新对话",
            "workdir": meta.get("workdir") or "", "updated": meta.get("updated") or 0,
            "messages": messages}


# ══════════════════════════ HTTP 层 ══════════════════════════

def routes() -> dict:
    """路径 → 处理函数。返回值可以是 dict/list（JSON）或 Path（文件）。"""
    return {
        "/api/health": lambda: {"ok": True},
        "/api/theme": theme_payload,
        "/api/sessions": sessions_payload,
        "/api/wallpaper": wallpaper_file,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "zhuzhuCopilotWeb/1.0"

    def log_message(self, fmt, *args):        # 默认会往 stderr 打每条请求，太吵
        if os.environ.get("ZHUZHU_WEB_VERBOSE"):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # ---- 工具 ----
    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")

    def _json(self, obj, code: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: Path) -> None:
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        size = path.stat().st_size
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        # 原图按 mtime 做 URL 版本号，这里可以放心长缓存；不加则每次拖拽都重传几 MB
        self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        self._cors()
        self.end_headers()
        with path.open("rb") as fh:
            while True:
                chunk = fh.read(_CHUNK)
                if not chunk:
                    break
                self.wfile.write(chunk)

    # ---- 路由 ----
    def do_OPTIONS(self):                      # noqa: N802  (BaseHTTPRequestHandler 约定)
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):                          # noqa: N802
        raw = self.path.split("?", 1)[0].rstrip("/") or "/"
        table = routes()
        try:
            if raw in table:
                value = table[raw]()
                if value is None:
                    self._json({"error": "not found"}, 404)
                elif isinstance(value, Path):
                    self._file(value)
                else:
                    self._json(value)
                return
            if raw.startswith("/api/session/"):
                sid = raw[len("/api/session/"):]
                payload = session_payload(sid)
                self._json(payload if payload is not None else {"error": "no such session"},
                           200 if payload is not None else 404)
                return
            self._json({"error": "not found", "path": raw}, 404)
        except Exception as e:                 # 服务端异常也要给前端一个可读的错误
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)


def serve(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    """起服务（返回 server 对象，便于测试里 shutdown）。"""
    ensure_qt()
    httpd = ThreadingHTTPServer((host, port), Handler)
    return httpd


def _port_free(host: str, port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex((host, port)) != 0


def main(argv: Optional[list] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    host = os.environ.get("ZHUZHU_WEB_HOST", DEFAULT_HOST)
    port = int(os.environ.get("ZHUZHU_WEB_PORT", DEFAULT_PORT))
    if "--host" in argv:
        host = argv[argv.index("--host") + 1]
    if "--port" in argv:
        port = int(argv[argv.index("--port") + 1])
    if not _port_free(host, port):
        print(f"[web] 端口被占用：{host}:{port}（用 --port 换一个）", flush=True)
        return 1
    httpd = serve(host, port)
    print(f"[web] 数据服务已启动：http://{host}:{port}/api/health", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
