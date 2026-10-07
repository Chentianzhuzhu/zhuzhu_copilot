# -*- coding: utf-8 -*-
"""验证：面板位置不随鼠标位置漂移 + 紧贴菜单右缘 + 关闭按钮可用。"""
import os, sys, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
SRC = r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\src"
sys.path.insert(0, SRC)
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QRect, QPoint
APP = QApplication.instance() or QApplication(sys.argv)
from zhuzhu_Copilot.ui import agent_panel as AP

p = AP.AgentPanel(None); p.resize(1400, 900); p.show()
def wait(ms):
    t0 = time.time()
    while (time.time() - t0) * 1000 < ms:
        APP.processEvents(); time.sleep(0.004)
wait(250)

panel = p._ensure_profile_panel()
btn = p._auth_user_btn
btn_g = QRect(btn.mapToGlobal(QPoint(0,0)), btn.size())
print("btn_global:", btn_g)

results = []
# 模拟 n 次「在不同鼠标位置点击用户按钮」——菜单弹出点由 _auth_menu_anchor_pos 决定，
# 与光标无关，所以结果应完全一致。
for cursor_at in [(btn_g.center()), QPoint(btn_g.left()+2, btn_g.top()+2),
                  QPoint(btn_g.right()-2, btn_g.bottom()-2)]:
    # 用 _AnchorQMenu 复刻菜单弹出+快照
    m = AP._AnchorQMenu(p)
    m.setMinimumWidth(132)
    a1 = m.addAction("个人信息"); m.addSeparator()
    m.addAction("切换账号"); m.addAction("退出登录")
    m._target = a1
    m.popup(p._auth_menu_anchor_pos())
    wait(40)
    ig, mg = m.snapshot_of(a1)
    m.hide()
    panel.set_profile(p._profile_panel_data())
    panel.apply_theme()
    w, h = p._profile_panel_size(panel)
    panel.resize(w, h)
    geo = p._profile_panel_geometry(panel, {"item": ig, "menu": mg})
    # 期望：panel.x == menu 右缘（紧贴）; panel.y == item 顶
    want_x = p.mapFromGlobal(mg.topLeft()).x() + mg.width()
    want_y = p.mapFromGlobal(ig.topLeft()).y()
    ok_x = (geo.x() == want_x)
    ok_y = (geo.y() == want_y)
    results.append((geo, ig, mg, ok_x, ok_y))
    print(f"cursor={cursor_at} menu_global={mg} item_global={ig} -> geo={geo} ok_x={ok_x} ok_y={ok_y}")

uniq = {r[0] for r in results}
print("unique panel geos:", uniq)
print("FIXED_POSITION:", "PASS" if len(uniq) == 1 else "FAIL")
print("SNUG_RIGHT:", "PASS" if all(r[3] for r in results) else "FAIL")
print("ALIGN_ITEM_TOP:", "PASS" if all(r[4] for r in results) else "FAIL")

# 关闭按钮
p.open_profile_panel(results[0][0])
wait(300)
print("opened:", panel.isVisible())
panel._close_btn.click()
wait(500)
print("CLOSE_BTN:", "PASS" if not panel.isVisible() else "FAIL")
