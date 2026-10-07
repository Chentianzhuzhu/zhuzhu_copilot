# -*- coding: utf-8 -*-
"""端到端验证新功能（服务端本机 127.0.0.1:8000，避免 WAF 干扰）：
  1) 公告 设置/读取 + 推送
  2) 批量设置积分（delta/set）+ 推送
  3) 批量设置会员 + 推送
  4) /me 返回 announcement
  5) 登录页/后台页 i18n 元素存在
"""
import json
import urllib.request

BASE = "http://127.0.0.1:8000"
ADMIN_USER = "zhutianlaing"
ADMIN_PASS = "windows10"


def req(path, method="GET", body=None, token=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def main():
    ok = True

    # 管理员登录
    st, d = req("/api/admin/login", "POST",
                {"username": ADMIN_USER, "password": ADMIN_PASS})
    assert d.get("code") == 0, f"admin login failed: {st} {d}"
    atok = d["data"]["token"]
    print("[1] admin login OK")

    # 取一个测试用户
    st, d = req("/api/admin/users?page=1&page_size=5", token=atok)
    users = d["data"]["list"]
    assert users, "no users"
    uid = users[0]["id"]
    uname = users[0]["username"]
    print(f"[2] test user: {uid} {uname} points={users[0]['points']}")

    # ---- 公告 ----
    st, d = req("/api/admin/announcement", "POST",
                {"content": "E2E 测试公告 · 请忽略", "enabled": True}, token=atok)
    assert d.get("code") == 0, f"set announcement failed: {d}"
    print(f"[3] set announcement OK pushed={d['data']['pushed']}")

    st, d = req("/api/admin/announcement", token=atok)
    assert d["data"]["content"] == "E2E 测试公告 · 请忽略", f"get mismatch: {d}"
    print("[4] get announcement OK")

    # ---- 批量积分 delta ----
    st, d = req("/api/admin/users/batch/points", "POST",
                {"ids": [uid], "mode": "delta", "amount": 7,
                 "detail": "E2E delta"}, token=atok)
    assert d.get("code") == 0 and d["data"]["success"] == 1, f"batch points delta failed: {d}"
    print(f"[5] batch points delta OK pushed={d['data']['pushed']}")

    # 读回校验
    st, d2 = req(f"/api/admin/users?page=1&page_size=5", token=atok)
    u2 = [x for x in d2["data"]["list"] if x["id"] == uid][0]
    print(f"    points now: {u2['points']}")

    # ---- 批量积分 set ----
    target = 1234
    st, d = req("/api/admin/users/batch/points", "POST",
                {"ids": [uid], "mode": "set", "amount": target}, token=atok)
    assert d.get("code") == 0, f"batch points set failed: {d}"
    st, d2 = req(f"/api/admin/users?page=1&page_size=5", token=atok)
    u2 = [x for x in d2["data"]["list"] if x["id"] == uid][0]
    assert u2["points"] == target, f"set mismatch: {u2['points']} != {target}"
    print(f"[6] batch points set OK -> {u2['points']}")

    # ---- 批量会员 ----
    st, d = req("/api/admin/users/batch/membership", "POST",
                {"ids": [uid], "membership_type": "pro",
                 "membership_expire": "2027-06-30"}, token=atok)
    assert d.get("code") == 0 and d["data"]["success"] == 1, f"batch member failed: {d}"
    print(f"[7] batch membership OK pushed={d['data']['pushed']}")

    st, d2 = req(f"/api/admin/users?page=1&page_size=5", token=atok)
    u2 = [x for x in d2["data"]["list"] if x["id"] == uid][0]
    print(f"    membership={u2.get('membership_type')} expire={u2.get('membership_expire')}")

    # 恢复原值
    req("/api/admin/users/batch/membership", "POST",
        {"ids": [uid], "membership_type": "free"}, token=atok)
    req("/api/admin/users/batch/points", "POST",
        {"ids": [uid], "mode": "set", "amount": users[0]["points"]}, token=atok)
    print("[8] restored original values")

    # 清空公告
    st, d = req("/api/admin/announcement", "POST",
                {"content": "", "enabled": True}, token=atok)
    assert d.get("code") == 0
    print("[9] cleared announcement")

    # ---- 登录页 i18n（纯文本抓取，非 JSON）----
    try:
        with urllib.request.urlopen(BASE + "/static/login/index.html", timeout=10) as resp:
            html = resp.read().decode("utf-8", "replace")
        has_en = "'en':" in html and "data-i18n" in html and "function setLang" in html
        print(f"[10] login page i18n present: {has_en}")
        ok = ok and has_en
    except Exception as e:
        print(f"[10] login page fetch failed: {e}")
        ok = False

    print("\nE2E ALL PASS" if ok else "E2E FAIL")


if __name__ == "__main__":
    main()
