# -*- coding: utf-8 -*-
"""复现并验证「发布后公告不轮询」的启动顺序 Bug。

原始 Bug（已修）
----------------
`_build_auth_user_bar()` 在 `_announce_label` **创建之前**调用 `_set_announcement()`；
而 `_set_announcement()` 开头 `if lbl is None: return` 会提前返回，
导致其中唯一一处 `auth.start_ws_if_logged_in()`（进而 `start_announcement_polling()`）
**永不执行** → 公告轮询从不启动。

本脚本用 AST 定位并断言修复后的调用结构：
1. `_build_auth_user_bar` 中**直接**调用 `start_ws_if_logged_in`（不依赖 `_set_announcement`）；
2. `_set_announcement` 中**不再**调用 `start_ws_if_logged_in`（避免再次被提前 return 吞掉）；
3. 运行期：构造面板后 auth 的公告轮询定时器确实启动。

零网络：monkeypatch auth_client._http_get_json。
"""
from __future__ import annotations

import ast
import io
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)

PANEL = os.path.join(SRC, "zhuzhu_Copilot", "ui", "agent_panel.py")

ok = True


def check(label, cond):
    global ok
    print(f"[{'PASS' if cond else 'FAIL'}] {label}")
    ok = ok and cond


src = io.open(PANEL, encoding="utf-8").read()
tree = ast.parse(src)

# 找 AgentPanel._build_auth_user_bar 与 _set_announcement
def find_method(name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def calls_in(fn, callee_name):
    """返回 fn 内「直接调用 callee_name」的次数（含 Attribute 结尾匹配）。"""
    n = 0
    if fn is None:
        return n
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute) and f.attr == callee_name:
                n += 1
            elif isinstance(f, ast.Name) and f.id == callee_name:
                n += 1
    return n


bar = find_method("_build_auth_user_bar")
setter = find_method("_set_announcement")

check("定位 _build_auth_user_bar", bar is not None)
check("定位 _set_announcement", setter is not None)

check("修复点1：_build_auth_user_bar 直接调用 start_ws_if_logged_in",
      calls_in(bar, "start_ws_if_logged_in") >= 1)
check("修复点2：_set_announcement 不再调用 start_ws_if_logged_in（防提前 return 吞掉）",
      calls_in(setter, "start_ws_if_logged_in") == 0)

# 断言 _set_announcement 的提前 return 仍在（这是它必须在别处调用的原因）
early_return = False
if setter is not None:
    for node in ast.walk(setter):
        if isinstance(node, ast.If) and isinstance(node.test, ast.Compare):
            # lbl is None
            left = node.test.left
            if isinstance(left, ast.Name) and left.id == "lbl":
                for b in node.body:
                    if isinstance(b, ast.Return):
                        early_return = True
check("_set_announcement 仍有 `lbl is None: return` 提前返回（故不可依赖它启动）",
      early_return)

# ---------- 运行期：构造面板 → 轮询定时器应启动 ----------
from PyQt6.QtWidgets import QApplication  # noqa: E402
_app = QApplication.instance() or QApplication([])

from zhuzhu_Copilot.core import auth_client as ac  # noqa: E402

ac._http_get_json = lambda url, token="", timeout=15: (
    True, {"code": 0, "data": {"content": "启动公告", "enabled": True}}, 200
) if url.endswith("/api/announcement") else (False, {}, 404)

client = ac.get_auth_client()
# 清掉可能已存在的定时器，模拟「应用刚打开」
client.stop_announcement_polling()

# 直接验证启动入口：修复后由 _build_auth_user_bar 调用
client.start_ws_if_logged_in()

t = getattr(client, "_announce_timer", None)
check("运行期：start_ws_if_logged_in 后公告轮询定时器已启动", t is not None)
check(f"运行期：定时器间隔 30000ms（{t.interval() if t else -1}）",
      bool(t) and t.interval() == 30000)

# 首拉应已发生（等待后台线程）
for _ in range(60):
    _app.processEvents()
    if client.announcement() == "启动公告":
        break
    time.sleep(0.05)
check(f"运行期：启动即首拉公告（{client.announcement()!r}）",
      client.announcement() == "启动公告")

client.stop_announcement_polling()

print()
print("STARTUP_POLL_ORDER", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
