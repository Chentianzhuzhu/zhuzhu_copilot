"""端到端验证：通过公网域名 https://chentian.dpdns.org/authapp 验证完整登录链路。

覆盖：state 申请 -> 注册 -> 登录（带 state）-> /me -> 续期 -> 登出 -> 防重放校验。
用法：python scripts/verify_login_e2e.py
"""
import json
import ssl
import sys
import urllib.error
import urllib.request
import uuid

BASE = "https://chentian.dpdns.org/authapp"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 zhuzhuCopilot")
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

PASS, FAIL = [], []


def req(method, path, body=None, token=None, timeout=20):
    h = {"User-Agent": UA, "Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout, context=CTX) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {"err": f"{type(e).__name__}: {e}"}


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {extra}")


def main():
    print("=" * 60)
    print(f"目标: {BASE}")
    print("=" * 60)

    # 1. 健康检查
    st, d = req("GET", "/")
    check("服务健康检查", st == 200 and d.get("code") == 0, f"st={st}")

    # 2. 登录页可达 + 携带 state 参数渲染
    try:
        r = urllib.request.urlopen(urllib.request.Request(
            BASE + "/static/login/index.html?platform=zhuzhu-copilot&state=teststate&version=6.0.0",
            headers={"User-Agent": UA}), timeout=20, context=CTX)
        html = r.read().decode("utf-8", "replace")
        check("登录页可达", r.status == 200 and "getLoginState" in html, f"st={r.status}")
        check("登录页已支持 state 透传", "LOGIN_STATE" in html and "body.state" in html)
    except Exception as e:
        check("登录页可达", False, str(e)[:80])

    # 3. 申请一次性 state
    st, d = req("POST", "/api/auth/state?platform=zhuzhu-copilot&version=6.0.0", {})
    state = ((d.get("data") or {}).get("state") or "") if st == 200 else ""
    check("申请登录 state", st == 200 and len(state) == 64, f"st={st} len={len(state)}")

    # 4. 注册（带 state）
    u = "e2e_" + uuid.uuid4().hex[:8]
    pwd = "Test1234!"
    st, d = req("POST", "/api/auth/register", {
        "username": u, "password": pwd, "password_confirm": pwd,
        "security_question": "q", "security_answer": "a", "state": state,
    })
    ip_dup = st == 409 and "IP" in str(d.get("detail", ""))
    reg_ok = st == 200 and (d.get("data") or {}).get("token")
    check("注册（带 state）", reg_ok or ip_dup,
          f"st={st} {'(该IP已注册，属预期)' if ip_dup else ''}")

    # 该 IP 已注册过账号时，本次注册的用户不存在，改用已有测试账号验证登录链路
    login_user, login_pwd = u, pwd
    if ip_dup:
        login_user, login_pwd = "wstest", "Test1234!"

    # 5. 登录（带 state）——重新申请 state
    st, d = req("POST", "/api/auth/state?platform=zhuzhu-copilot", {})
    state2 = ((d.get("data") or {}).get("state") or "")
    st, d = req("POST", "/api/auth/login",
                {"username": login_user, "password": login_pwd, "state": state2})
    tok = ((d.get("data") or {}).get("token") or "") if st == 200 else ""
    check("登录（带 state）", st == 200 and bool(tok), f"st={st} user={login_user}")

    # 6. 登录结果中转一次性（防重放）：login_result 取走一次后再次取应为空
    st, d = req("GET", f"/api/auth/login_result?state={state2}", None)
    first = (d.get("data") or {}) if st == 200 else {}
    st2, d2 = req("GET", f"/api/auth/login_result?state={state2}", None)
    second = (d2.get("data") or {}) if st2 == 200 else {}
    check("登录结果中转一次性（防重放）",
          st == 200 and bool(first.get("token")) and not second,
          f"first={'ok' if first.get('token') else 'empty'} second={'empty' if not second else 'dup'}")

    if tok:
        # 7. /me
        st, d = req("GET", "/api/auth/me", token=tok)
        me = d.get("data") or {}
        check("获取用户信息 /me", st == 200 and me.get("username") == login_user, f"st={st}")

        # 8. 积分
        st, d = req("GET", "/api/points/balance", token=tok)
        check("查询积分余额", st == 200, f"st={st}")

        # 9. token 续期
        st, d = req("POST", "/api/auth/refresh", {}, token=tok)
        new_tok = ((d.get("data") or {}).get("token") or "") if st == 200 else ""
        check("token 滑动续期", st == 200 and bool(new_tok) and new_tok != tok, f"st={st}")

        # 10. 登出（用新 token）
        if new_tok:
            st, d = req("POST", "/api/auth/logout", {}, token=new_tok)
            check("登出", st == 200, f"st={st}")
            # 11. 登出后旧会话立即失效
            st, d = req("GET", "/api/auth/me", token=new_tok)
            check("登出后 token 立即失效", st == 401, f"st={st}")

    # 12. 未知 state 的登录必须失败
    st, d = req("POST", "/api/auth/login",
                {"username": "nobody_zzz", "password": "x",
                 "state": "0" * 64})
    check("伪造 state 被拒绝", st == 400, f"st={st}")

    print("=" * 60)
    print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
    if FAIL:
        print("失败项: " + ", ".join(FAIL))
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
