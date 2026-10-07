#!/usr/bin/env python3
"""验证 /authapp 前缀的 Nginx 路由（origin，Host 指向域名）"""
import json
import urllib.error
import urllib.request


def status(method, path, body=None):
    r = urllib.request.Request("http://127.0.0.1" + path,
                               data=json.dumps(body).encode() if body is not None else None,
                               method=method,
                               headers={"Host": "chentian.dpdns.org", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


print("authapp 根路径         :", status("GET", "/authapp/"))
print("authapp 登录(错密码)    :", status("POST", "/authapp/api/auth/login",
                                           {"username": "nouser09", "password": "x"}))
print("authapp /me(无token)   :", status("GET", "/authapp/api/auth/me"))
print("authapp 登录页          :", status("GET", "/authapp/static/login/index.html"))
print("原 update 根路径(保留)   :", status("GET", "/"))