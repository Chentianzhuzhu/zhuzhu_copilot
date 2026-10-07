#!/usr/bin/env python3
"""最终公网冒烟测试：经 Nginx 公网路径完成 注册→登录→/me→refresh→登出 全流程。
注意：本脚本在服务器本地运行，BASE 用公网地址以覆盖 Nginx 转发链路。
"""
import json
import subprocess
import time
import urllib.error
import urllib.request
import uuid

PUB = "http://39.104.28.191"
PASS, FAIL = [], []


def req(method, path, body=None, token=None):
    url = PUB + path
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {"err": str(e)}


def check(name, cond, extra=""):
    if cond:
        PASS.append(name); print(f"[PASS] {name} {extra}")
    else:
        FAIL.append(name); print(f"[FAIL] {name} {extra}")


def main():
    # 清理可能残留的注册IP记录，保证可注册
    subprocess.run(["redis-cli", "del", "register_ip:127.0.0.1"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["mysql", "-uroot", "-pwindows10", "-e",
                    "DELETE FROM zhuzhu_copilot_auth.ip_registry WHERE ip='127.0.0.1';"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    u = "smoke_" + uuid.uuid4().hex[:6]
    pwd = "Smoke99!@"
    st, d = req("POST", "/api/auth/register", {
        "username": u, "password": pwd, "password_confirm": pwd,
        "security_question": "你的宠物名字是？", "security_answer": "x",
    })
    check("公网注册", st == 200 and d.get("data", {}).get("token"), f"st={st}")
    token = d.get("data", {}).get("token")

    st, d = req("POST", "/api/auth/login", {"username": u, "password": pwd})
    check("公网登录", st == 200 and d.get("data", {}).get("token"), f"st={st}")
    login_token = d.get("data", {}).get("token")

    st, d = req("GET", "/api/auth/me", token=login_token)
    check("公网 /me", st == 200 and d.get("data", {}).get("username") == u, f"st={st}")

    st, d = req("POST", "/api/auth/refresh", token=login_token)
    check("公网 refresh", st == 200 and d.get("data", {}).get("token"), f"st={st}")
    new_token = d.get("data", {}).get("token")
    st, _ = req("GET", "/api/auth/me", token=login_token)
    check("refresh后旧token失效", st == 401, f"st={st}")
    st, _ = req("GET", "/api/auth/me", token=new_token)
    check("refresh后新token有效", st == 200, f"st={st}")

    st, d = req("POST", "/api/auth/logout", token=new_token)
    check("公网 logout", st == 200, f"st={st}")
    st, _ = req("GET", "/api/auth/me", token=new_token)
    check("logout后token失效", st == 401, f"st={st}")

    # 清理测试账号
    subprocess.run(["mysql", "-uroot", "-pwindows10", "-e",
                    f"DELETE FROM zhuzhu_copilot_auth.users WHERE username='{u}';"
                    f"DELETE FROM zhuzhu_copilot_auth.ip_registry WHERE ip='127.0.0.1';"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    print("\n=== 公网冒烟测试 通过:", len(PASS), "失败:", len(FAIL), "===")
    if FAIL:
        raise SystemExit(1)
    print("ALL PUBLIC SMOKE TESTS PASSED")


if __name__ == "__main__":
    main()