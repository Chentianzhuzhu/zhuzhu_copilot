"""本地只读静态服务器：给打工猫桌宠的 3D 预览页提供 http 环境。

用途：浏览器不允许 file:// 页面用 fetch 读 GLB，所以起一个小服务器。
    python serve_preview.py            # 默认 127.0.0.1:8770
    python serve_preview.py 9000       # 指定端口

只读、只服务 preview_lib 目录、绑定本机回环地址，不对外暴露。
"""
import functools
import http.server
import os
import socketserver
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "preview_lib")


class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".glb": "model/gltf-binary",
        ".js": "application/javascript",
        ".npz": "application/octet-stream",
    }

    def log_message(self, fmt, *args):   # 静音访问日志，别刷屏
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8770
    handler = functools.partial(Handler, directory=ROOT)
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", port), handler) as httpd:
        print(f"打工猫预览页已启动：http://127.0.0.1:{port}/")
        print(f"服务目录：{ROOT}")
        sys.stdout.flush()
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("已停止")


if __name__ == "__main__":
    main()
