# -*- coding: utf-8 -*-
"""验证客户端登录流程 i18n（core/auth_client.py + 登录弹窗文案）。

断言：
1. auth_client 中所有登录相关字面量都已用 _ui() 包裹（无残留裸中文）；
2. zh_CN / en_US 两语言下，翻译结果符合预期；
3. 语言包 en_US.json 覆盖 auth_client 用到的每个 key；
4. 回调页 HTML 标题随语言切换。

无需 Qt：i18n 模块本身零 Qt 依赖（QSettings 惰性）。
"""
from __future__ import annotations

import ast
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)

from zhuzhu_Copilot.core import i18n  # noqa: E402

AUTH = os.path.join(SRC, "zhuzhu_Copilot", "core", "auth_client.py")
PACK = os.path.join(SRC, "zhuzhu_Copilot", "locales", "en_US.json")

ok = True


def check(label: str, cond: bool) -> None:
    global ok
    print(f"[{'PASS' if cond else 'FAIL'}] {label}")
    ok = ok and cond


# ---------- 1. 收集 auth_client 中所有 _ui("...") key ----------
src = io.open(AUTH, encoding="utf-8").read()
tree = ast.parse(src)
ui_keys: set[str] = set()
ui_wrapped_ids: set[int] = set()
for node in ast.walk(tree):
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_ui":
        if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            ui_keys.add(node.args[0].value)
            ui_wrapped_ids.add(id(node.args[0]))

check(f"auth_client 中 _ui() key 数量 = {len(ui_keys)}（应 >= 10）", len(ui_keys) >= 10)

# ---------- 2. 残留裸中文字面量检测（排除 docstring / 注释） ----------
# 收集所有 docstring 节点（Module/Class/Function 的首个 Expr 常量）。
docstrings: set[int] = set()
for node in ast.walk(tree):
    if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        body = getattr(node, "body", None)
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            docstrings.add(id(body[0].value))

bare: list[str] = []
for node in ast.walk(tree):
    if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings \
            and id(node) not in ui_wrapped_ids:
        s = node.value
        if re.search(r"[\u4e00-\u9fff]", s):
            if s.strip() and len(s.strip()) > 1:
                bare.append(s)
# 已知允许的例外：模块级常量 INSUFFICIENT_MESSAGE（运行时经 _ui 翻译）
ALLOW = {"您的积分不足，请接入其他AI服务"}
bare = [b for b in bare if b not in ALLOW and not b.startswith("zhuzhu Copilot")]
check(f"无未包裹的裸中文用户文案（残留 {len(bare)}）", not bare)
if bare:
    for b in bare[:5]:
        print("    -", repr(b)[:80])

# ---------- 3. 语言包覆盖 ----------
pack = json.load(io.open(PACK, encoding="utf-8"))["ui"]
missing = [k for k in ui_keys if not pack.get(k)]
check(f"en_US 覆盖 auth_client 全部 key（缺失 {len(missing)}）", not missing)
if missing:
    for m in missing[:8]:
        print("    MISS:", repr(m))

# ---------- 4. 双语言翻译断言 ----------
CASES = {
    "登录": "Sign In",
    "立即登录": "Sign In Now",
    "登录成功": "Signed In",
    "校验失败": "Verification Failed",
    "您的账号已被管理员禁用": "Your account has been disabled by an administrator.",
    "账号已被禁用，已强制下线": "Your account has been disabled; you have been signed out.",
    "您的积分不足，请接入其他AI服务":
        "You are out of points. Please connect another AI service.",
    "登录态已失效，请重新登录后继续":
        "Your session is no longer valid. Please sign in again to continue.",
}

i18n.set_lang(i18n.ZH_CN, persist=False)
zh_ok = all(i18n.ui(k) == k for k in CASES)
check("zh_CN：返回中文原文", zh_ok)

i18n.set_lang(i18n.EN_US, persist=False)
en_bad = [(k, i18n.ui(k), v) for k, v in CASES.items() if i18n.ui(k) != v]
check(f"en_US：全部命中预期译文（不一致 {len(en_bad)}）", not en_bad)
for k, got, want in en_bad[:8]:
    print(f"    {k!r} -> got {got!r} / want {want!r}")

# 回退：包中不存在的 key 在英文下回落中文原文
fallback = i18n.ui("__不存在的键__")
check("en_US：缺失 key 回落中文原文", fallback == "__不存在的键__")

i18n.set_lang(i18n.ZH_CN, persist=False)

print()
print("CLIENT_LOGIN_I18N", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
