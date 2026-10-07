# -*- coding: utf-8 -*-
"""验证两处修复：

1. 订阅计划始终显示：_memb_load_products 的后台线程**只 emit 数据**，
   重建卡片在主线程执行 → 服务端数据回来后三张订阅卡仍在且带服务端价格。
   （修复前：跨线程重建导致卡片全部 isVisible=False，整排消失）
2. 积分包（无 membership_type 的加购项）：free / pro / max **任何订阅均可购买**。
"""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QLabel  # noqa: E402

app = QApplication.instance() or QApplication([])

from zhuzhu_Copilot.core import auth_client as ac  # noqa: E402
from zhuzhu_Copilot.ui import agent_panel as ap  # noqa: E402

ok = True


def check(label, cond, extra=""):
    global ok
    print(f"[{'PASS' if cond else 'FAIL'}] {label}{(' | ' + str(extra)) if extra and not cond else ''}")
    ok = ok and bool(cond)


# ---- 服务端商品返回：价格与模板不同，用来证明「重建确实用上了服务端数据」 ----
SERVER_PRICE = {"free": 0, "pro": 9, "max": 19}
SERVER_PAYLOAD = {
    "code": 0,
    "data": {"memberships": [
        {"membership_type": "free", "id": "free", "price": 0, "points": 0,
         "purchasable": False, "action": "purchase"},
        {"membership_type": "pro", "id": "pro_monthly", "price": 9, "points": 1200,
         "purchasable": True, "action": "renew"},
        {"membership_type": "max", "id": "max_monthly", "price": 19, "points": 2500,
         "purchasable": True, "action": "renew"},
    ]},
}


class _Sig:
    """极简信号替身：只支持 connect（_build_membership_page 会连接若干 auth 信号）。"""

    def connect(self, *_a, **_k):
        pass


class _FakeAuth:
    server = "http://stub.local"
    token = "tkn"

    def __init__(self, tier):
        self._tier = tier
        for n in ("points_updated", "membership_updated", "order_paid",
                  "login_success", "logged_out"):
            setattr(self, n, _Sig())

    def current_user(self):
        return {"membership_type": self._tier, "membership_expire": "2099-01-01 00:00:00"}

    def is_logged_in(self):
        return True

    def get_points(self):
        return 1234

    def login_with_browser(self):
        pass


def build_dialog(tier):
    fake = _FakeAuth(tier)
    ac.get_auth_client = lambda: fake           # noqa: ARG005
    ac._http_get_json = lambda *a, **k: (True, SERVER_PAYLOAD, None)  # noqa: ARG005
    dlg = ap._AgentSettingsDialog()
    page = dlg._build_membership_page()
    page.show()
    # 等后台线程 emit + 主线程槽重建完成
    deadline = time.time() + 5.0
    while time.time() < deadline:
        app.processEvents()
        if dlg._memb_membership_products[1].get("price") == SERVER_PRICE["pro"]:
            app.processEvents()
            break
        time.sleep(0.02)
    app.processEvents()
    return dlg, page


# ============ 1. 订阅卡片：重建后仍可见且有服务端价格 ============
print("--- 1. 订阅计划显示（后台线程 → 主线程重建） ---")
dlg, page = build_dialog("pro")
row = dlg._memb_sub_row

cards = []
for i in range(row.count()):
    w = row.itemAt(i).widget()
    if w is not None:
        texts = [l.text() for l in w.findChildren(QLabel)]
        cards.append((w.isVisible(), w.height(), w.width(), texts))

print(f"  会员订阅卡片数 = {len(cards)}")
for vis, h, w, texts in cards:
    print(f"    visible={vis} {w}x{h} texts={texts[:3]}")

check("重建后三张订阅卡仍在（3 张）", len(cards) == 3, len(cards))
check("★ 三张订阅卡全部可见（修复前整排 isVisible=False → 只剩标题）",
      all(c[0] for c in cards), [c[0] for c in cards])
check("★ 卡片高度正常（>40px，非 480 之类的失控几何）",
      all(c[1] > 40 for c in cards), [c[1] for c in cards])
check("★ 卡片用上了服务端价格（Pro=9/1200、Max=19/2500，而非内置兜底的 7/14）",
      any("9" in t and "1200" in t for _, _, _, ts in cards for t in ts)
      and any("19" in t and "2500" in t for _, _, _, ts in cards for t in ts),
      [ts for _, _, _, ts in cards])

# 按钮态：pro 用户 → free 置灰、pro/max 可点
btns = {pid: b.isEnabled() for pid, (_c, b) in dlg._memb_card_widgets.items()}
print(f"  订阅按钮可用性: {btns}")
check("pro 用户：max_monthly 可购买", btns.get("max_monthly") is True, btns)
check("pro 用户：free 档置灰", btns.get("free") is False, btns)

page.grab().save(os.path.join(ROOT, "scripts", "_verify_membership_page.png"))

# ============ 2. 积分包：任何订阅均可购买 ============
print("\n--- 2. 积分包：任何订阅等级均可购买 ---")
obj = ap._AgentSettingsDialog.__new__(ap._AgentSettingsDialog)
packs = [dict(p) for p in ap._AgentSettingsDialog._POINTPACK_PRODUCTS]
check(f"积分包模板 {len(packs)} 项且均无 membership_type（加购项）",
      len(packs) == 3 and all(not p.get("membership_type") for p in packs))

for tier in ("free", "pro", "max"):
    labels = []
    all_buyable = True
    for p in packs:
        label, tip, can_buy = obj._memb_card_state(p, tier)
        labels.append(f"{p['id']}:{label}/{'可' if can_buy else '禁'}")
        all_buyable = all_buyable and can_buy
    check(f"★ {tier:4s} 用户：3 个积分包均可购买", all_buyable, labels)
    print(f"    {tier:4s} → {labels}")

# 实做卡片验证 enabled 状态
obj._PANEL2, obj._TEXT, obj._DIM = "#1E1E1E", "#fff", "#8A8A8A"
obj._ACCENT, obj._ACCENT_HOVER, obj._BORDER = "#2E5A87", "#4A90D9", "#333"
obj._memb_auth = _FakeAuth("max")
for p in packs:
    card = obj._memb_product_card(p)
    btn = card._buy_btn
    check(f"Max 用户下积分包 {p['id']} 按钮可点（enabled=True）",
          btn.isEnabled() is True and btn.text() == ap._ui("购买"),
          f"{btn.isEnabled()} {btn.text()}")

print(f"\n{'ALL PASS' if ok else 'HAS FAILURES'}  (截图: scripts/_verify_membership_page.png)")
sys.exit(0 if ok else 1)
