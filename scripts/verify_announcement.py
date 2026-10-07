# -*- coding: utf-8 -*-
"""验证顶栏公告位：无修饰、无内容隐藏、位置在用户名右侧 CPU 左侧。"""
import os, sys, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
SRC = r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\src"
sys.path.insert(0, SRC)
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QPoint, QRect
APP = QApplication.instance() or QApplication(sys.argv)
from zhuzhu_Copilot.ui import agent_panel as AP

p = AP.AgentPanel(None); p.resize(1500, 900); p.show()
def wait(ms):
    t0 = time.time()
    while (time.time() - t0) * 1000 < ms:
        APP.processEvents(); time.sleep(0.004)
wait(250)

lbl = p._announce_label
print("label exists:", lbl is not None)
print("initial visible:", lbl.isVisible(), "text:", repr(lbl.text()))

p._set_announcement({"content": "服务器将于今晚 23:00 维护，请提前保存工作"})
wait(60)
print("after set visible:", lbl.isVisible(), "text:", repr(lbl.text()))
ss = lbl.styleSheet()
print("has border none:", "border: none" in ss, "| has background transparent:", "background: transparent" in ss)

# 位置校验：公告中心 x 应位于 用户名按钮右缘 与 cpu 左缘 之间
btn = p._auth_user_btn
btn_r = btn.mapToGlobal(QPoint(0, 0)).x() + btn.width()
cpu_l = p.cpu_label.mapToGlobal(QPoint(0, 0)).x()
al = lbl.mapToGlobal(QPoint(0, 0)).x()
ar = al + lbl.width()
print(f"btn_right={btn_r} announce=[{al},{ar}] cpu_left={cpu_l}")
print("RIGHT_OF_USER:", "PASS" if al >= btn_r else "FAIL")
print("LEFT_OF_CPU:", "PASS" if ar <= cpu_l else "FAIL")

# 清空隐藏
p._set_announcement({"content": ""})
wait(60)
print("after clear visible:", lbl.isVisible())
print("HIDE_WHEN_EMPTY:", "PASS" if not lbl.isVisible() else "FAIL")

# 长文本截断
p._set_announcement({"content": "x" * 100})
wait(60)
print("truncated len:", len(lbl.text()), "endswith ellipsis:", lbl.text().endswith("…"))
print("TRUNCATE:", "PASS" if len(lbl.text()) <= 61 else "FAIL")
