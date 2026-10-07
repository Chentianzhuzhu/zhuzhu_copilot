# -*- coding: utf-8 -*-
"""验证客户端「会员 / 积分」相关文案的 i18n 覆盖。

断言：
1. 所有会员/积分 UI 字面量都以 _ui()/_uif()/_uim() 包裹（无残留裸中文）；
2. 商品兜底表 / 导航项中的中文原文保持为「键」（模块导入期不求值），
   展示时统一过 _ui/_uim，从而可随语言切换；
3. 语言包 en_US.json 覆盖全部会员/积分 key（非空）；
4. zh_CN 返回中文原文；en_US 返回预期英文；uif 占位符替换正确；
5. 积分不足提示（PointsConsumer.INSUFFICIENT_MESSAGE）可被翻译。

零 Qt 依赖：仅静态 AST 扫描 + core.i18n 运行时断言。
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

PANEL = os.path.join(SRC, "zhuzhu_Copilot", "ui", "agent_panel.py")
ENGINE = os.path.join(SRC, "zhuzhu_Copilot", "core", "agent_engine.py")
PACK = os.path.join(SRC, "zhuzhu_Copilot", "locales", "en_US.json")

# 会员 / 积分相关的领域关键词：用于在裸中文字面量里定位相关文案
KEYWORDS = ("积分", "会员", "订阅", "支付", "订单", "购买", "支付宝", "微信",
            "用户协议", "收款", "扫码", "入账")

# 允许保留为「中文键」的位置（模块级常量 / 兜底商品表 / 导航项 / 意图关键词），
# 其值在展示期统一经 _ui/_uim 翻译，故不视为未包裹文案。
ALLOW_EXACT = {
    "150 积分", "750 积分", "1500 积分",
    "每月 1000 积分，内置模型畅聊", "每月 2000 积分，重度用户推荐",
    "轻量补充", "热门推荐", "最划算",
    "Pro 版", "Max 版",
    "会员与积分",   # _NAV_ITEMS 导航项，展示过 _uim
    "微信 ClawBot",  # _NAV_ITEMS 导航项，展示过 _uim
    "收款记录",      # 意图关键词（suggestion/keyword 池），非 UI 文案
}

ok = True


def check(label: str, cond: bool) -> None:
    global ok
    print(f"[{'PASS' if cond else 'FAIL'}] {label}")
    ok = ok and cond


# ---------- 0. 收集 _ui()/_uif()/_uim() 包裹的 key ----------
def collect(src_path: str):
    src = io.open(src_path, encoding="utf-8").read()
    tree = ast.parse(src)
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))

    wrapped_ids: set[int] = set()
    keys: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id in ("_ui", "_uif", "_uim", "uim", "ui", "_ui_cb"):
            if node.args and isinstance(node.args[0], ast.Constant) \
                    and isinstance(node.args[0].value, str):
                wrapped_ids.add(id(node.args[0]))
                keys.add(node.args[0].value)

    bare: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and id(node) not in docstrings and id(node) not in wrapped_ids:
            s = node.value
            if re.search(r"[\u4e00-\u9fff]", s) and s.strip() and len(s.strip()) > 1:
                bare.append(s)
    return keys, bare


panel_keys, panel_bare = collect(PANEL)
engine_keys, engine_bare = collect(ENGINE)

membership_keys = {k for k in panel_keys if any(w in k for w in KEYWORDS)}
check(f"agent_panel 中会员/积分 _ui() key 数量 = {len(membership_keys)}（应 >= 30）",
      len(membership_keys) >= 30)

# ---------- 1. 残留裸中文（会员/积分领域） ----------
bare_hits = sorted({b for b in (panel_bare + engine_bare)
                    if any(w in b for w in KEYWORDS)})
unwrapped = [b for b in bare_hits if b not in ALLOW_EXACT]
check(f"无未包裹的会员/积分裸中文文案（残留 {len(unwrapped)}）", not unwrapped)
if unwrapped:
    for b in unwrapped[:10]:
        print("    -", repr(b)[:90])

# ---------- 2. 语言包覆盖 ----------
pack = json.load(io.open(PACK, encoding="utf-8"))["ui"]
missing = sorted(k for k in membership_keys if not pack.get(k))
check(f"en_US 覆盖全部会员/积分 key（缺失 {len(missing)}）", not missing)
if missing:
    for m in missing[:10]:
        print("    MISS:", repr(m))

# ---------- 3. 双语言 + 占位符替换断言 ----------
CASES = {
    "会员与积分": "Membership & Points",
    "会员订阅": "Membership Plans",
    "积分包": "Points Packs",
    "积分": "Points",
    "积分余额": "Points Balance",
    "实时": "Live",
    "购买": "Buy",
    "免费版": "Free",
    "Pro 会员": "Pro Member",
    "Max 会员": "Max Member",
    "会员类型": "Membership",
    "会员到期": "Membership Expires",
    "支付订单": "Pay Order",
    "支付方式：": "Payment method: ",
    "支付宝": "Alipay",
    "微信": "WeChat",
    "已提交": "Submitted",
    "正在加载收款码...": "Loading payment QR code...",
    "我已完成支付": "I have completed the payment",
    "同意并继续支付": "Agree and continue to pay",
    "请阅读用户协议：": "Please read the Terms of Service: ",
    "我已阅读并同意用户协议和使用条款": "I have read and agree to the Terms of Service",
    "用户协议与使用条款": "Terms of Service",
    "积分不足，任务已截断": "Insufficient points; the task was truncated.",
    "订单创建失败，请重试": "Failed to create the order. Please try again.",
    "收款码加载失败，请联系管理员配置":
        "Failed to load the payment QR code. Please contact the administrator.",
}

i18n.set_lang(i18n.ZH_CN, persist=False)
zh_bad = [(k, i18n.ui(k)) for k in CASES if i18n.ui(k) != k]
check(f"zh_CN：全部返回中文原文（不一致 {len(zh_bad)}）", not zh_bad)
for k, got in zh_bad[:6]:
    print(f"    {k!r} -> got {got!r}")

i18n.set_lang(i18n.EN_US, persist=False)
en_bad = [(k, i18n.ui(k), v) for k, v in CASES.items() if i18n.ui(k) != v]
check(f"en_US：全部命中预期译文（不一致 {len(en_bad)}）", not en_bad)
for k, got, want in en_bad[:8]:
    print(f"    {k!r} -> got {got!r} / want {want!r}")

# ---------- 4. uif 占位符替换（en_US 下参数应正确注入） ----------
fmt_bad = []

got = i18n.uif("商品：{name}\n金额：{price} 元", name="Pro Plan", price=7)
want = "Item: Pro Plan\nAmount: ¥7"
if got != want:
    fmt_bad.append(("商品：{name}\\n金额：{price} 元", got, want))

got = i18n.uif("{price} 元 / 月 · 送 {pts} 积分", price=14, pts="2,000")
want = "¥14/month · 2,000 points included"
if got != want:
    fmt_bad.append(("{price} 元 / 月 · 送 {pts} 积分", got, want))

got = i18n.uif("{price} 元 = {pts} 积分", price=5, pts=750)
want = "¥5 = 750 points"
if got != want:
    fmt_bad.append(("{price} 元 = {pts} 积分", got, want))

got = i18n.uif("订单号：{oid}（金额 {amt} 元）", oid="ZP123", amt=10)
want = "Order No.: ZP123 (amount ¥10)"
if got != want:
    fmt_bad.append(("订单号：{oid}（金额 {amt} 元）", got, want))

check(f"en_US：uif 占位符替换正确（失败 {len(fmt_bad)}）", not fmt_bad)
for k, got, want in fmt_bad[:6]:
    print(f"    {k!r} -> got {got!r} / want {want!r}")

# ---------- 5. 积分不足提示可翻译 ----------
from zhuzhu_Copilot.core.auth_client import PointsConsumer  # noqa: E402

msg = PointsConsumer.INSUFFICIENT_MESSAGE
check(f"en_US：积分不足提示已翻译（{msg!r}）",
      bool(pack.get(msg)) and i18n.ui(msg) == pack[msg])

# 回退语义：未收录键在英文下回落中文原文
check("en_US：缺失 key 回落中文原文", i18n.ui("__不存在的键__") == "__不存在的键__")

i18n.set_lang(i18n.ZH_CN, persist=False)

print()
print("MEMBERSHIP_I18N", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
