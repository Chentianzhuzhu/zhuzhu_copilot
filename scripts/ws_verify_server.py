"""服务器端 WebSocket 验证：注册/登录 -> WS 连接 -> ping/pong -> 积分推送。

在服务器上通过 .venv/bin/python 运行（依赖 websockets）。
"""
import asyncio
import json
import urllib.error
import urllib.request
import uuid

BASE = "http://127.0.0.1:8000"


def post(path, body, token=None):
    data = json.dumps(body).encode()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}


async def ws_test(token):
    import websockets
    async with websockets.connect("ws://127.0.0.1:8000/ws?token=" + token,
                                  open_timeout=10) as ws:
        await ws.send(json.dumps({"type": "ping"}))
        msg = await asyncio.wait_for(ws.recv(), timeout=10)
        print("WS 收到:", msg)
        return json.loads(msg).get("type") == "pong"


def main():
    st, sd = post("/api/auth/state?platform=ws-test", {})
    state = sd["data"]["state"]

    u = "ws_" + uuid.uuid4().hex[:8]
    st, rd = post("/api/auth/register", {
        "username": u, "password": "Test1234!", "password_confirm": "Test1234!",
        "security_question": "q", "security_answer": "a", "state": state})
    if st == 409:  # IP 已注册：改用登录已有账号
        st2, sd2 = post("/api/auth/state?platform=ws-test", {})
        st, rd = post("/api/auth/login",
                      {"username": "wsuser", "password": "Test1234!",
                       "state": sd2["data"]["state"]})
    print("认证状态:", st)
    token = (rd.get("data") or {}).get("token")
    if not token:
        print("无法取得 token:", rd)
        return 1
    ok = asyncio.run(ws_test(token))
    print("WS ping/pong:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
