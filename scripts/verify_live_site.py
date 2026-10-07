# -*- coding: utf-8 -*-
"""线上（https://chentian.dpdns.org/authapp）最终冒烟：静态页 + 公告 API 前缀。
需要浏览器 UA，否则被 WAF 403 code 1010。"""
import json, urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
BASE = "https://chentian.dpdns.org/authapp"


def get(path):
    r = urllib.request.Request(BASE + path)
    r.add_header("User-Agent", UA)
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def post(path, body, token=None):
    data = json.dumps(body).encode()
    r = urllib.request.Request(BASE + path, data=data, method="POST")
    r.add_header("User-Agent", UA)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


ok = True

# 1) 登录页含 i18n
st, html = get("/static/login/index.html")
c1 = st == 200 and "function setLang" in html and "'en':" in html and "lang-switch" in html
print(f"[1] login page i18n: {st} -> {'PASS' if c1 else 'FAIL'}")
ok = ok and c1

# 2) 后台页含公告页 + 批量按钮
st, html = get("/static/admin/index.html")
c2 = (st == 200 and 'data-page="announce"' in html and "openBatchPointsModal" in html
      and "openBatchMemberModal" in html and "function saveAnnouncement" in html)
print(f"[2] admin page announce+batch: {st} -> {'PASS' if c2 else 'FAIL'}")
ok = ok and c2

# 3) 公告 API（管理员登录后读取）
st, d = post("/api/admin/login", {"username": "zhutianlaing", "password": "windows10"})
tok = (d.get("data") or {}).get("token") if d.get("code") == 0 else None
c3 = st == 200 and bool(tok)
print(f"[3] admin login: {st} -> {'PASS' if c3 else 'FAIL'}")
ok = ok and c3

if tok:
    r = urllib.request.Request(BASE + "/api/admin/announcement")
    r.add_header("User-Agent", UA)
    r.add_header("Authorization", "Bearer " + tok)
    with urllib.request.urlopen(r, timeout=15) as resp:
        d = json.loads(resp.read().decode())
    c4 = d.get("code") == 0 and "content" in d.get("data", {})
    print(f"[4] announcement GET: {d.get('code')} -> {'PASS' if c4 else 'FAIL'}")
    ok = ok and c4

print("\nLIVE SITE:", "ALL PASS" if ok else "SOME FAIL")
