# -*- coding: utf-8 -*-
"""回归验证：头像正圆/清晰度、个人信息面板头像不被裁剪、订阅三档按钮态。

覆盖用户提出的三类问题：
1. 头像框必须是**正圆**（不是椭圆）、正确适配、渲染清晰；
2. 个人信息面板头像**完整显示**、不被边缘裁剪或遮挡；
3. 订阅计划**固定展示三档**（Basic/免费版、Pro、Max），并按当前档位动态调整按钮：
   已订阅 Max ⇒ Basic 与 Pro 的订阅按钮为**灰色禁用**（不可点击）。
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QColor, QPainter, QPixmap  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from zhuzhu_Copilot.ui import agent_panel as ap  # noqa: E402

ok = True


def check(label, cond, extra=""):
    global ok
    print(f"[{'PASS' if cond else 'FAIL'}] {label}{(' | ' + str(extra)) if extra and not cond else ''}")
    ok = ok and bool(cond)


def make_src(w=300, h=200):
    """非正方形源图（真实用户照片多为非正方形）——旧实现正是被它拉成椭圆。"""
    pm = QPixmap(w, h)
    pm.fill(QColor("#3b6ea5"))
    p = QPainter(pm)
    p.setPen(QColor("#ffffff"))
    p.drawRect(0, 0, w - 1, h - 1)
    p.drawLine(0, 0, w - 1, h - 1)
    p.drawLine(w - 1, 0, 0, h - 1)
    p.end()
    return pm


src = make_src()
Panel = ap._ProfilePanel
Settings = ap._AgentSettingsDialog

# ============ 1. _circle_avatar：正圆 + 逻辑尺寸 + 对称留白 + 高清 ============
print("---- 1. 头像裁剪：正圆 / 尺寸 / 对称留白 / 清晰度 ----")
for size, gap in ((32, 6), (32, 0), (56, 0), (64, 8)):
    pm = ap._circle_avatar(src, size, gap)
    lw = pm.deviceIndependentSize().width()
    lh = pm.deviceIndependentSize().height()
    check(f"_circle_avatar(size={size}, gap={gap}) 逻辑尺寸 = {lw:g}x{lh:g}"
          f"（期望 {size + 2 * gap}x{size}，即控件适配尺寸）",
          round(lw) == size + 2 * gap and round(lh) == size, f"{lw}x{lh}")

    img = pm.toImage()
    dpr = pm.devicePixelRatio()
    cols = [x for x in range(img.width())
            if any(img.pixelColor(x, y).alpha() > 0 for y in range(img.height()))]
    rows = [y for y in range(img.height())
            if any(img.pixelColor(x, y).alpha() > 0 for x in range(img.width()))]
    cw = (max(cols) - min(cols) + 1) if cols else 0
    ch = (max(rows) - min(rows) + 1) if rows else 0
    check(f"  ★ 圆形宽=高 → 正圆而非椭圆 (w={cw} h={ch} 比={cw / max(1, ch):.3f}，"
          f"旧实现=1.375 椭圆)",
          ch > 0 and abs(cw - ch) <= 2, f"{cw}x{ch}")
    if gap:
        left_blank = min(cols) if cols else 0
        right_blank = (img.width() - 1 - max(cols)) if cols else 0
        check(f"  左右留白对称（左={left_blank} 右={right_blank}，"
              f"均为 {int(round(gap * dpr))}；旧实现左侧=0 全挤在右侧）",
              abs(left_blank - right_blank) <= 2, f"L={left_blank} R={right_blank}")

# 高清渲染：显式 DPR 下物理像素放大、逻辑尺寸不变（避免被放大而发虚）
pm_hi = ap._circle_avatar(src, 56, 0, dpr=2.0)
check(f"DPR=2 时物理像素 {pm_hi.width()}px ≥ 112（逻辑仍 "
      f"{pm_hi.deviceIndependentSize().width():g}px）→ 高清渲染不再发虚",
      pm_hi.width() >= 112 and abs(pm_hi.deviceIndependentSize().width() - 56) < 0.6,
      f"dev={pm_hi.width()} dpr={pm_hi.devicePixelRatio()}")

# 源图自带 dpr（如 Retina 屏加载的照片）：必须先归一到物理像素，
# 否则 scaled() 按逻辑尺寸、copy() 按设备像素 → 裁剪错位。
src_dpr2 = QPixmap(src)
src_dpr2.setDevicePixelRatio(2.0)
pm_d = ap._circle_avatar(src_dpr2, 32, 6)
dl = pm_d.deviceIndependentSize()
_dimg = pm_d.toImage()
_dcols = [x for x in range(_dimg.width())
          if any(_dimg.pixelColor(x, y).alpha() > 0 for y in range(_dimg.height()))]
_drows = [y for y in range(_dimg.height())
          if any(_dimg.pixelColor(x, y).alpha() > 0 for x in range(_dimg.width()))]
_dw = max(_dcols) - min(_dcols) + 1
_dh = max(_drows) - min(_drows) + 1
check(f"源图 dpr=2 时仍得逻辑 {dl.width():g}x{dl.height():g}（期望 44x32）且为正圆"
      f"（{_dw}x{_dh}）",
      round(dl.width()) == 44 and round(dl.height()) == 32 and abs(_dw - _dh) <= 2,
      f"logical={dl.width()}x{dl.height()} circle={_dw}x{_dh}")

# ============ 2. 个人信息面板：头像完整、不被裁剪或遮挡 ============
print("\n---- 2. 个人信息面板：头像完整 / 不被裁剪遮挡 ----")


def build_panel(username):
    pn = Panel(None)
    pn.set_profile({
        "username": username,
        "avatar_pixmap": src,
        "points": 1100,
        "membership_type": "max",
        "membership_expire": "2025-11-07 23:59:59",
        "user_id": "41",
        "checkin": {"checked_in": False},
    })
    pn.resize(Panel.PREFERRED_WIDTH, 300)
    pn.show()
    app.processEvents()
    return pn


panel = build_panel("zhuzhu_very_long_user_name")
av = panel._avatar
pix = av.pixmap()
content = av.contentsRect()
check(f"★ 头像位图 {pix.deviceIndependentSize().width():g}"
      f"x{pix.deviceIndependentSize().height():g} ≤ 内容区 "
      f"{content.width()}x{content.height()}（不被边缘裁剪）",
      pix.deviceIndependentSize().width() <= content.width() + 0.01
      and pix.deviceIndependentSize().height() <= content.height() + 0.01,
      f"pix={pix.width()}x{pix.height()} content={content.getRect()}")
check(f"头像控件外框宽=高={av.width()}（正圆，非椭圆）", av.width() == av.height())
check("头像未被面板边缘裁掉", av.geometry().right() <= panel.width())
check(f"头像与用户名不重叠（头像右 {av.geometry().right()} < 用户名左 "
      f"{panel._username.geometry().left()}）→ 不被挤压遮挡",
      av.geometry().right() < panel._username.geometry().left())

user = panel._username
check("长用户名已省略并保留完整 tooltip（不再溢出盖住签到按钮）",
      user.text() != "zhuzhu_very_long_user_name"
      and user.toolTip() == "zhuzhu_very_long_user_name"
      and user.width() <= Panel._USERNAME_MAX_PX,
      f"text={user.text()!r} tip={user.toolTip()!r} w={user.width()}")

# 关键回归：用户名长度不再影响面板宽度（旧实现 312px 名字把宽度顶到 508）
w_long = build_panel("zhuzhu_very_long_user_name").sizeHint().width()
w_short = build_panel("zz").sizeHint().width()
check(f"★ 面板宽度与用户名长度无关（长名 {w_long} == 短名 {w_short}）"
      f"→ 长名不再把面板撑爆挤压头像行",
      w_long == w_short, f"long={w_long} short={w_short}")

panel.grab().save(os.path.join(ROOT, "scripts", "_verify_profile_panel.png"))

# ============ 3. 订阅计划：固定三档 + 按档位动态按钮 ============
print("\n---- 3. 订阅计划：固定三档 + 按当前档位动态按钮 ----")
products = [dict(p) for p in Settings._MEMBERSHIP_PRODUCTS]
check(f"固定三档模板 = {[p['name'] for p in products]}（Basic/免费版、Pro、Max）",
      [p["membership_type"] for p in products] == ["free", "pro", "max"])


class _FakeAuth:
    def __init__(self, tier):
        self._tier = tier

    def current_user(self):
        return {"membership_type": self._tier}

    def is_logged_in(self):
        return True

    def get_points(self):
        return 1100


def states_for(tier):
    """走真实的 _memb_card_state（含本地档位序兜底）。"""
    obj = Settings.__new__(Settings)
    return {p["membership_type"]: obj._memb_card_state(p, tier)[::2]   # (label, can_buy)
            for p in products}


s_free, s_pro, s_max = states_for("free"), states_for("pro"), states_for("max")
print(f"  free 用户: {s_free}")
print(f"  pro  用户: {s_pro}")
print(f"  max  用户: {s_max}")

# 文案随界面语言变化 → 断言用真实 i18n 取值，避免硬编码中文/英文
U = ap._ui
check("三档**始终**都在（任一档位下都有 free/pro/max 三张卡，不因服务端少返回而消失）",
      all(set(s) == {"free", "pro", "max"} for s in (s_free, s_pro, s_max)))
check("free 用户：免费版=当前版本(禁用)；Pro/Max 可购买",
      s_free["free"] == (U("当前版本"), False)
      and s_free["pro"][1] and s_free["max"][1], s_free)

check("★ Max 用户：Basic（免费版）按钮灰色禁用不可点",
      s_max["free"] == (U("不可购买"), False), s_max["free"])
check("★ Max 用户：Pro 按钮灰色禁用不可点",
      s_max["pro"] == (U("不可购买"), False), s_max["pro"])
check("Max 用户：Max 档自身仍可续费（可点）", s_max["max"][1] is True, s_max["max"])
check("pro 用户：免费版禁用；Pro/Max 均可用（Pro=续费、Max=升级由服务端 action 决定）",
      s_pro["free"] == (U("不可购买"), False)
      and s_pro["pro"][1] and s_pro["max"][1], s_pro)

# 服务端漏回 purchasable 时，本地档位序必须兜住（原缺陷：Max 仍能点 Pro 购买）
leaky = {"action": "purchase", "price": 7, "points": 1000,
         "membership_type": "pro", "name": "Pro 版"}
obj = Settings.__new__(Settings)
label, tip, can_buy = obj._memb_card_state(leaky, "max")
check("★ 服务端未回 purchasable=False 时，Max 用户的 Pro 仍被本地档位序置灰",
      can_buy is False and label == U("不可购买"), f"{label=} {can_buy=}")

# 禁用按钮必须有 :disabled 灰态（否则沿用强调色，看不出「灰色禁用」）
obj._PANEL2, obj._TEXT, obj._DIM = "#1E1E1E", "#FFFFFF", "#8A8A8A"
obj._ACCENT, obj._ACCENT_HOVER, obj._BORDER = "#2E5A87", "#4A90D9", "#333333"
obj._memb_auth = _FakeAuth("max")
card = obj._memb_product_card({**products[1], "price": 7, "points": 1000})
btn = card._buy_btn
check("禁用按钮带 :disabled 灰色样式（背景/文字/描边均为灰）",
      ":disabled" in btn.styleSheet() and obj._DIM in btn.styleSheet(),
      btn.styleSheet())
check("Max 用户下 Pro 卡片按钮 enabled=False 且文案=不可购买",
      btn.isEnabled() is False and btn.text() == U("不可购买"),
      f"{btn.isEnabled()} {btn.text()}")
card_ok = obj._memb_product_card({**products[2], "price": 14, "points": 2000})
check("Max 用户下 Max 卡片按钮仍可用（enabled=True）",
      card_ok._buy_btn.isEnabled() is True, card_ok._buy_btn.text())

print(f"\n{'ALL PASS' if ok else 'HAS FAILURES'}  (截图: scripts/_verify_profile_panel.png)")
sys.exit(0 if ok else 1)
