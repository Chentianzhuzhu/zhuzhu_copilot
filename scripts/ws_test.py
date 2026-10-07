#!/usr/bin/env python3
"""WebSocket 鉴权与连接验证（含经 Nginx 公网路径）。
用法: python3.11 ws_test.py <token> [base_url]
"""
import asyncio
import json
import sys

token = sys.argv[1]
base = sys.argv[2] if len(sys.argv) > 2 else "ws://127.0.0.1:8000"
url = f"{base}/ws?token={token}"


async def main():
    import websockets
    try:
        async with websockets.connect(url, open_timeout=10, close_timeout=5) as ws:
            print("CONNECT_OK", url)
            # 发心跳
            await ws.send(json.dumps({"type": "ping"}))
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=8)
                print("PONG", msg)
            except asyncio.TimeoutError:
                print("NO_PONG")
            # 等待一点时间后正常关闭
            await ws.close()
            return 0
    except Exception as e:
        print("CONNECT_FAIL", url, "->", type(e).__name__, e)
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))