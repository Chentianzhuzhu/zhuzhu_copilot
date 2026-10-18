# -*- coding: utf-8 -*-
"""验证：个人信息面板「无中间菜单 / 挂在胶囊下方 / 宽度自适应」。

离屏跑（QT_QPA_PLATFORM=offscreen）。离屏字体是等宽方块，字符宽偏大，
因此这里的宽度数值只用于「相对关系」判断（不挤压、右缘对齐、向左展开）。
"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from PyQt6.QtWidgets import QApplication, QWidget, QPushButton   # noqa: E402
from PyQt6.QtCore import QRect, QPoint                            # noqa: E402
from PyQt6.QtGui import QFont                                     # noqa: E402

APP = QApplication.instance() or QApplication(sys.argv)
APP.setFont(QFont("Arial", 10))

from zhuzhu_Copilot.core import i18n              # noqa: E402
from zhuzhu_Copilot.ui import agent_panel as AP    # noqa: E402

i18n.set_lang("en_US", persist=False)


def wait(ms):
    t0 = time.time()
    while (time.time() - t0) * 1000 < ms:
        APP.processEvents()
        time.sleep(0.004)


class Host(QWidget):
    """最小宿主：只借 AgentPanel 的尺寸/定位方法。"""

    _profile_panel_size = AP.AgentPanel._profile_panel_size
    _profile_panel_geometry = AP.AgentPanel._profile_panel_geometry
    _PROFILE_GAP = AP.AgentPanel._PROFILE_GAP

    def __init__(self):
        super().__init__()
        self.resize(1200, 820)
        self.setStyleSheet("background: #14161a;")
        self.chip = QPushButton("", self)
        self.chip.setFixedSize(150, 36)
        self.chip.move(320, 12)
        self._auth_user_btn = self.chip


host = Host()
host.show()
wait(120)
panel = AP._ProfilePanel(host)
panel._on_close_request = lambda *a: None
panel.show()
wait(80)

chip = QRect(host.chip.mapToGlobal(QPoint(0, 0)), host.chip.size())
origin = host.mapToGlobal(QPoint(0, 0))
print("host", host.width(), "x", host.height(), "| chip local",
      host.chip.geometry())

CASES = [
    ("short", "bob", "free", "", 41),
    ("long-en", "bartholomew.alexanderson.iv", "max", "2075-11-07 23:59:59", 41),
    ("longest", "the-extraordinarily-long-username-2026", "pro",
     "2075-11-07 23:59:59", 1234567),
]
ok = True
for tag, name, mtype, expire, uid in CASES:
    panel.set_profile({
        "username": name,
        "avatar_pixmap": None,
        "points": 1234567,
        "membership_type": mtype,
        "membership_expire": expire,
        "user_id": uid,
        "checkin": {"checked": False, "can_checkin": True, "reward": 5},
    })
    panel.apply_theme()
    w, h = host._profile_panel_size(panel)
    panel.apply_width(w)
    panel.resize(w, h)
    geo = host._profile_panel_geometry(panel)
    panel.setGeometry(geo)
    wait(60)

    # chip.right()/bottom() 是「含端点」坐标，面板右缘 = geo.x()+width
    right_gap = (chip.right() + 1) - (geo.x() + geo.width() + origin.x())
    top_gap = (geo.y() + origin.y()) - (chip.bottom() + 1)
    squeezed = []
    for lbl in (panel._username, panel._rows["membership_type"],
                panel._rows["membership_expire"], panel._member,
                panel._checkin_btn, panel._switch_btn, panel._logout_btn,
                panel._points):
        if lbl.width() + 1 < lbl.sizeHint().width():
            squeezed.append(f"{lbl.objectName() or type(lbl).__name__}"
                            f"({lbl.width()}<{lbl.sizeHint().width()})")
    elided = []
    if panel._username.text() != name:
        elided.append("username")
    if panel._rows["membership_expire"].text() != expire:
        elided.append("expire")
    case_ok = (right_gap == 0 and top_gap == AP.AgentPanel._PROFILE_GAP
               and not squeezed)
    ok = ok and case_ok
    print(f"[{tag}] {w}x{h} geo={geo.x()},{geo.y()} "
          f"right_gap={right_gap} top_gap={top_gap} "
          f"user={panel._username.text()!r} expire={panel._rows['membership_expire'].text()!r}")
    if squeezed:
        print("      squeezed:", ", ".join(squeezed))
    if elided:
        print("      elided:", ", ".join(elided), "(离屏等宽字体所致)")
    print("      ->", "PASS" if case_ok else "FAIL")

print("ALL:", "PASS" if ok else "FAIL")
