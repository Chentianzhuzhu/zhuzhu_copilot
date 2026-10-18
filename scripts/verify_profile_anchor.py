# -*- coding: utf-8 -*-
"""验证：点顶栏用户胶囊直接开合个人信息面板 + 面板贴胶囊右缘下方 + 跟随重排。

跑法（离屏，不需要真显示器）：
    python scripts/verify_profile_anchor.py

检查项
1. 点击胶囊 = 打开面板（不再弹「个人信息/切换账号/退出登录」菜单）；
2. 再点一次 = 收起；
3. 面板右缘与胶囊右缘对齐（垂直方向），顶边在胶囊下方 _PROFILE_GAP 处；
4. 胶囊变宽 / 窗口缩放后，面板仍贴合（跟随逻辑生效）；
5. 面板内部所有左对齐元素的左边距一致（视觉对齐）。
"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from PyQt6.QtWidgets import QApplication          # noqa: E402
from PyQt6.QtGui import QFont, QFontDatabase     # noqa: E402
from PyQt6.QtCore import QPoint, QRect            # noqa: E402

APP = QApplication.instance() or QApplication(sys.argv)
_fams = QFontDatabase.families()
APP.setFont(QFont(next((f for f in ("Microsoft YaHei UI", "Segoe UI", "Arial")
                        if f in _fams), "Arial"), 10))

from zhuzhu_Copilot.core import i18n              # noqa: E402
from zhuzhu_Copilot.ui import agent_panel as AP    # noqa: E402

i18n.set_lang("en_US", persist=False)


def pump(ms: int):
    t0 = time.time()
    while (time.time() - t0) * 1000 < ms:
        APP.processEvents()
        time.sleep(0.004)


def rel(panel, btn):
    """面板相对胶囊的 (右缘偏差, 顶边间距)。"""
    chip = QRect(btn.mapTo(panel.parent(), QPoint(0, 0)), btn.size())
    geo = panel.geometry()
    return (geo.x() + geo.width() - (chip.x() + chip.width()),
            geo.y() - (chip.y() + chip.height()))


def main():
    p = AP.AgentPanel(None)
    p.resize(1280, 860)
    p.show()
    pump(500)

    btn = p._auth_user_btn
    panel = p._ensure_profile_panel()
    results = []

    # 1/2. 点击开合（无菜单）
    btn.click()
    pump(500)
    opened = panel.isVisible() and p._profile_panel_open
    btn.click()
    pump(500)
    closed = (not panel.isVisible()) and (not p._profile_panel_open)
    btn.click()
    pump(500)
    reopened = panel.isVisible()
    print(f"click-open={opened} click-close={closed} click-reopen={reopened} "
          f"menu-gone={not hasattr(p, '_auth_user_menu')}")
    results.append(("点击直开/再点收起", opened and closed and reopened
                    and not hasattr(p, "_auth_user_menu")))

    # 3. 贴合胶囊右缘 + 下方
    gap = rel(panel, btn)
    ok = gap == (0, p._PROFILE_GAP)
    print(f"anchor (right_delta, top_gap)={gap} expect=(0, {p._PROFILE_GAP})")
    results.append(("右缘对齐 + 正下方", ok))

    # 4. 胶囊变宽 / 窗口缩放后仍贴合
    btn.setMinimumWidth(btn.minimumWidth() + 40)
    pump(400)
    gap2 = rel(panel, btn)
    p.resize(1100, 800)
    pump(400)
    gap3 = rel(panel, btn)
    print(f"after chip widen={gap2} after window resize={gap3}")
    results.append(("跟随重排", gap2 == (0, p._PROFILE_GAP)
                    and gap3 == (0, p._PROFILE_GAP)))

    # 5. 面板内部左对齐（标题 / 头像 / 积分 / 键名 / 底部按钮）
    lefts = {"title": panel._title.x(), "avatar": panel._avatar.x(),
             "points_label": panel._pts_label.x(), "points": panel._points.x(),
             "switch_btn": panel._switch_btn.x()}
    for key in panel._rows:
        lefts["key_" + key] = panel._row_keys[key].x()
    aligned = len(set(lefts.values())) == 1
    print("left insets:", lefts, "aligned=", aligned)
    results.append(("左对齐", aligned))

    print("\n".join(f"{'PASS' if ok else 'FAIL'}  {name}" for name, ok in results))
    print("ALL:", "PASS" if all(ok for _n, ok in results) else "FAIL")
    return 0 if all(ok for _n, ok in results) else 1


if __name__ == "__main__":
    sys.exit(main())
