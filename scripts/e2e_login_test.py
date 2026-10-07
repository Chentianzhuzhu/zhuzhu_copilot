#!/usr/bin/env python3
"""登录功能全链路 E2E 测试（在服务器本地运行，指向 http://127.0.0.1:8000）。
覆盖：注册、正常登录、异常场景（密码错误/账号不存在/账号锁定/禁用）、
登录态续期（refresh）、退出登录（logout 后 token 失效）。"""
import json
import time
import urllib.error
import urllib.request
import uuid

BASE = "http://127.0.0.1:8000"
PASSED, FAILED = [], []


def req(method, path, body=None, token=None, headers=None):
    url = BASE + path
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    if headers:
        h.update(headers)
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode()), resp.headers
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode()), e.headers
        except Exception:
            return e.code, {}, e.headers
    except Exception as e:
        return 0, {"error": str(e)}, {}


def check(name, cond, extra=""):
    if cond:
        PASSED.append(name)
        print(f"[PASS] {name} {extra}")
    else:
        FAILED.append(name)
        print(f"[FAIL] {name} {extra}")


def main():
    suffix = uuid.uuid4().hex[:6]
    username = f"test_{suffix}"
    password = "Passw0rd!123"

    # 1. 注册
    st, d, hd = req("POST", "/api/auth/register", {
        "username": username, "password": password, "password_confirm": password,
        "security_question": "你的宠物名字是？", "security_answer": "小狗",
    })
    check("注册成功", st == 200 and d.get("code") == 0 and d["data"].get("token"), f"status={st}")
    if not (st == 200 and d.get("code") == 0):
        print(f"    resp={d}")
        return
    token = d["data"]["token"]
    uid = d["data"]["user_id"]

    # 2. 重复用户名注册 -> 409
    st, d, _ = req("POST", "/api/auth/register", {
        "username": username, "password": password, "password_confirm": password,
        "security_question": "你的宠物名字是？", "security_answer": "x",
    })
    check("重复用户名注册返回409", st == 409, f"status={st}")

    # 3. 正常登录
    st, d, hd = req("POST", "/api/auth/login", {"username": username, "password": password})
    check("正常登录成功", st == 200 and d.get("code") == 0 and d["data"].get("token"), f"status={st}")
    login_token = d["data"]["token"]

    # 4. 错误密码
    st, d, hd = req("POST", "/api/auth/login", {"username": username, "password": "wrongpass"})
    attempts_left = hd.get("X-Attempts-Left") if hasattr(hd, "get") else None
    msg = d.get("detail") or d.get("message") or ""
    check("错误密码返回401", st == 401 and "密码" in msg, f"status={st} attempts_left={attempts_left}")

    # 5. 账号不存在
    st, d, _ = req("POST", "/api/auth/login", {"username": f"nouser_{suffix}_zz", "password": "whatever1"})
    check("账号不存在返回401", st == 401, f"status={st}")

    # 6. /me 携带有效 token
    st, d, _ = req("GET", "/api/auth/me", token=login_token)
    check("/me 返回当前用户", st == 200 and d.get("data", {}).get("username") == username, f"status={st}")

    # 7. refresh 续期：旧 token 之后失效，新 token 可用
    st, d, _ = req("POST", "/api/auth/refresh", token=login_token)
    check("refresh 返回新token", st == 200 and d.get("data", {}).get("token"), f"status={st}")
    new_token = d["data"]["token"] if st == 200 else login_token
    st, d, _ = req("GET", "/api/auth/me", token=login_token)  # 旧 token 已被撤销
    check("refresh后旧token失效", st == 401, f"status={st}")
    st, d, _ = req("GET", "/api/auth/me", token=new_token)
    check("refresh后的新token可用", st == 200, f"status={st}")

    # 8. logout：token 立即失效
    st, d, _ = req("POST", "/api/auth/logout", token=new_token)
    check("logout 成功", st == 200, f"status={st}")
    st, d, _ = req("GET", "/api/auth/me", token=new_token)
    check("logout后 /me 返回401", st == 401, f"status={st}")

    # 9. 未带 token 访问受保护接口 -> 401
    st, d, _ = req("GET", "/api/auth/me")
    check("无token访问 /me 返回401", st == 401, f"status={st}")

    # 10. 错误 token -> 401
    st, d, _ = req("GET", "/api/auth/me", token="invalid.token.here")
    check("伪造token返回401", st == 401, f"status={st}")

    # 11. 账号锁定：连续失败 admin_max_fail 次（用一次性账号）
    lock_user = f"lock_{suffix}"
    req("POST", "/api/auth/register", {
        "username": lock_user, "password": password, "password_confirm": password,
        "security_question": "你的宠物名字是？", "security_answer": "猫",
    })
    locked = False
    for i in range(6):
        st, d, hd = req("POST", "/api/auth/login", {"username": lock_user, "password": "badpass"})
        if st == 429:
            locked = True
            break
    check("连续失败后账号锁定(429)", locked, f"final_status={st}")

    # 12. 锁定后即使正确密码也拒绝
    st, d, _ = req("POST", "/api/auth/login", {"username": lock_user, "password": password})
    check("锁定期间正确密码也返回429", st == 429, f"status={st}")

    # 释放本机 IP 与账号的锁定/失败计数，避免干扰后续用例
    import subprocess
    for k in ("login_lock:ip:127.0.0.1", "login_fail:ip:127.0.0.1",
              f"login_lock:user:{lock_user}", f"login_fail:user:{lock_user}",
              f"login_attempts:user:{lock_user}"):
        subprocess.Popen(["redis-cli", "del", k],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1)

    # 13. 管理员登录（后台敏感接口权限）
    st, d, _ = req("POST", "/api/admin/login", {"username": "admin", "password": "admin123"})
    check("管理员登录成功", st == 200 and d.get("data", {}).get("token"), f"status={st}")
    admin_token = d["data"]["token"]
    st, d, _ = req("GET", "/api/admin/users", token=admin_token)
    check("管理员访问后台用户列表", st == 200, f"status={st}")
    # 普通用户 token 不能访问后台
    st, d, _ = req("GET", "/api/admin/users", token=login_token)
    check("普通用户token访问后台被拒", st in (401, 403), f"status={st}")

    # 14. 禁用账号后登录被拒（403）
    st, d, _ = req("POST", f"/api/admin/users/{uid}/disable", body=None, token=admin_token)
    check("管理员禁用用户", st == 200, f"status={st}")
    # 重新登录被禁用账号
    req("POST", "/api/auth/logout", token=admin_token)  # 释放次数计数，避免影响
    st, d, _ = req("POST", "/api/auth/login", {"username": username, "password": password})
    dmsg = d.get("detail") or d.get("message") or ""
    check("禁用账号登录返回403", st == 403 and "禁用" in dmsg, f"status={st} msg={dmsg}")

    # 清理测试账号的锁定状态（避免影响后续）
    import subprocess
    for k in (f"login_lock:user:{lock_user}", f"login_fail:user:{lock_user}",
              f"login_attempts:user:{lock_user}", "login_lock:ip:127.0.0.1"):
        subprocess.Popen(["redis-cli", "del", k],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    print("\n===================\n总计通过:", len(PASSED), "失败:", len(FAILED))
    if FAILED:
        print("失败项:", FAILED)
        raise SystemExit(1)
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()