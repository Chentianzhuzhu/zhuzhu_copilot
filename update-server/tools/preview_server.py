#!/usr/bin/env python3
"""本地官网预览服务（开发辅助，不是生产服务器）。

把 tools/run_tests.py --preview 生成的页面产物按线上同样的路由对外提供，
回放生成时使用的 /api/site 结构。只有指定线上抓取文件时才使用线上数据；
默认下载量、版本和时间戳是测试样例，预览不支持反馈写入：

  /                     → target/preview/pages/index.html
  /gallery /download /faq → 对应页面产物
  /api/site /api/version/latest → target/preview/pages/preview-site.json（真实结构）
  /site.webmanifest     → 按内容生成
  /css /js /favicon* /icon-* /og/* → src/main/resources/static
  /uploads/*            → target/preview/uploads（如已放入线上图片）

用法：
  python tools/run_tests.py --preview --preview-content target/preview/live-site.json
  python tools/preview_server.py --port 8099
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "src/main/resources/static"
PAGES = ROOT / "target/preview/pages"
UPLOADS = ROOT / "target/preview/uploads"

ROUTES = {
    "/": "index.html",
    "/index.html": "index.html",
    "/gallery": "gallery.html",
    "/download": "download.html",
    "/faq": "faq.html",
    "/about": "about.html",
    "/feedback": "feedback.html",
    "/sitemap": "sitemap.html",
}
STATIC_PREFIXES = ("/css/", "/js/", "/og/", "/uploads/")
STATIC_FILES = ("/favicon.ico", "/favicon.svg", "/apple-touch-icon.png",
                "/icon-192.png", "/icon-512.png")


def payload() -> dict:
    path = PAGES / "preview-site.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"content": {}, "totalDownloads": 0, "latest": None}


class Handler(BaseHTTPRequestHandler):
    server_version = "WinAppMigratorPreview"

    def log_message(self, fmt, *args):  # 静默访问日志，只保留错误
        if not str(args[0]).startswith("200"):
            sys.stderr.write("  %s\n" % (fmt % args))

    def _send(self, body: bytes, ctype: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _file(self, path: Path, ctype: str | None = None, status: int = 200) -> bool:
        if not path.is_file():
            return False
        guessed = ctype or mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        if guessed.startswith("text/") or guessed.endswith(("json", "xml", "svg+xml", "javascript")):
            guessed += "; charset=UTF-8"
        self._send(path.read_bytes(), guessed, status)
        return True

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
        route = urlparse(self.path).path

        # 接口回放：使用生成预览时的数据；默认是测试样例，不冒充线上数据
        if route == "/api/site":
            self._send(json.dumps(payload(), ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=UTF-8")
            return
        if route == "/api/version/latest":
            latest = payload().get("latest") or {}
            self._send(json.dumps(latest, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=UTF-8")
            return
        if route == "/site.webmanifest":
            content = payload().get("content") or {}
            name = content.get("title") or "WinAppMigrator"
            manifest = {
                "name": name, "short_name": name,
                "description": content.get("description", ""),
                "lang": "zh-CN", "start_url": "/", "scope": "/",
                "display": "standalone",
                "background_color": "#000000", "theme_color": "#000000",
                "icons": [
                    {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
                    {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
                    {"src": "/favicon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"},
                ],
            }
            self._send(json.dumps(manifest, ensure_ascii=False).encode("utf-8"),
                       "application/manifest+json; charset=UTF-8")
            return
        if route == "/robots.txt":
            self._send(b"User-agent: *\nAllow: /\nDisallow: /admin\nDisallow: /api/\n",
                       "text/plain; charset=UTF-8")
            return
        if route == "/sitemap.xml":
            base = f"http://{self.headers.get('Host', '127.0.0.1')}"
            urls = "".join(
                f"  <url><loc>{base}{p}</loc><priority>{pr}</priority></url>\n"
                for p, pr in (("/", "1.0"), ("/gallery", "0.8"), ("/download", "0.9"), ("/faq", "0.6"), ("/about", "0.6"), ("/feedback", "0.6"))
            )
            self._send(('<?xml version="1.0" encoding="UTF-8"?>\n'
                        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                        + urls + "</urlset>\n").encode("utf-8"),
                       "application/xml; charset=UTF-8")
            return

        # 上传资源（本地预览副本）
        if route.startswith("/uploads/"):
            if self._file(UPLOADS / route[len("/uploads/"):]):
                return

        # 静态资源
        if route.startswith(STATIC_PREFIXES) or route in STATIC_FILES:
            rel = route[len("/uploads/"):] if route.startswith("/uploads/") else route.lstrip("/")
            if self._file(STATIC / rel):
                return

        # 页面
        page = ROUTES.get(route)
        if page and self._file(PAGES / page, "text/html; charset=UTF-8"):
            return

        if self._file(PAGES / "404.html", "text/html; charset=UTF-8", status=404):
            return
        self._send(b"404 Not Found", "text/plain; charset=UTF-8", status=404)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("PREVIEW_PORT", 8099)))
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    if not (PAGES / "index.html").exists():
        print(f"[FAIL] 未找到页面产物：{PAGES}\n先执行：python tools/run_tests.py --preview "
              f"--preview-content target/preview/live-site.json", file=sys.stderr)
        return 2

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"预览地址：http://{args.host}:{args.port}/   （Ctrl+C 结束）")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
