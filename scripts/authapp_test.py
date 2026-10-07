#!/usr/bin/env python3
"""在 /authapp 子路径 + 域名 Host 下验证完整登录流程（模拟客户端通过域名访问）。"""
import json
import subprocess
import time
import urllib.error
import urllib.request
import uuid

# 以域名 Host 打 origin 的 /authapp 前缀，等价于客户端访问 https://chentian.dpdns.org/authapp
HOST = "chentian.dpdns.org"
BASE = "http://127.0.0.1/authapp"
PASS, FAIL = [], []


def req(method, path, body=None, token=None):
    url = BASE + path
    h = {"Content-Type": "application/json", "Host": HOST}
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
    subprocess.run(["redis-cli", "del", "register_ip:127.0.0.1"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["mysql", "-uroot", "-pwindows10", "-e",
                    "DELETE FROM zhuzhu_copilot_auth.ip_registry WHERE ip='127.0.0.1';"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    u = "authapp_" + uuid.uuid4().hex[:6]
    pwd = "Authapp1!"
    st, d = req("POST", "/api/auth/register", {
        "username": u, "password": pwd, "password_confirm": pwd,
        "security_question": "你的宠物名字是？", "security_answer": "x",
    })
    check("子路径注册", st == 200 and d.get("data", {}).get("token"), f"st={st}")

    st, d = req("POST", "/api/auth/login", {"username": u, "password": pwd})
    check("子路径登录", st == 200 and d.get("data", {}).get("token"), f"st={st}")
    tok = d.get("data", {}).get("token")

    st, d = req("GET", "/api/auth/me", token=tok)
    check("子路径 /me", st == 200 and d.get("data", {}).get("username") == u, f"st={st}")

    st, d = req("POST", "/api/auth/refresh", token=tok)
    check("子路径 refresh", st == 200 and d.get("data", {}).get("token"), f"st={st}")
    newtok = d.get("data", {}).get("token")

    st, d = req("POST", "/api/auth/logout", token=newtok)
    check("子路径 logout", st == 200, f"st={st}")
    st, _ = req("GET", "/api/auth/me", token=newtok)
    check("logout后失效", st == 401, f"st={st}")

    subprocess.run(["mysql", "-uroot", "-pwindows10", "-e",
                    f"DELETE FROM zhuzhu_copilot_auth.users WHERE username='{u}';"
                    "DELETE FROM zhuzhu_copilot_auth.ip_registry WHERE ip='127.0.0.1';"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    print("\n=== /authapp 子路径测试 通过:", len(PASS), "失败:", len(FAIL), "===")
    if FAIL:
        raise SystemExit(1)
    print("ALL /authapp TESTS PASSED")


if __name__ == "__main__":
    main()