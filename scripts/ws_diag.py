#!/usr/bin/env python3
"""诊断 /authapp/ws 的 WS 400 问题"""
import asyncio
import json
import sys
import websockets


async def try_connect(url, headers):
    try:
        async with websockets.connect(url, open_timeout=10, close_timeout=5,
                                      additional_headers=headers,
                                      origin=headers.get("Host")) as ws:
            print("CONNECT_OK", url, "hdr", headers)
            await ws.send(json.dumps({"type": "ping"}))
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=6)
                print("PONG", msg)
            except asyncio.TimeoutError:
                print("NO_PONG")
            await ws.close()
            return True
    except Exception as e:
        print("CONNECT_FAIL", url, "hdr", headers, "->", type(e).__name__, e)
        return False


async def main():
    token = sys.argv[1]
    variants = [
        ("ws://127.0.0.1/authapp/ws?token=" + token, {"Host": "chentian.dpdns.org"}),
        ("ws://127.0.0.1/authapp/ws?token=" + token, {"Host": "39.104.28.191"}),
        ("ws://39.104.28.191/authapp/ws?token=" + token, {"Host": "chentian.dpdns.org"}),
    ]
    for url, hdr in variants:
        await try_connect(url, hdr)

asyncio.run(main())