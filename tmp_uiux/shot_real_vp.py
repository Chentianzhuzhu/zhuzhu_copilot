# -*- coding: utf-8 -*-
"""真实面板：聚焦输入框，grab viewport 与整体，检查 viewport 是否绘制直角背景。"""
import sys, os
SRC = r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\src"
sys.path.insert(0, SRC)
os.chdir(SRC)
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer, Qt
from zhuzhu_Copilot.ui import agent_panel as ap

OUT = r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\tmp_uiux"
app = QApplication(sys.argv)
panel = ap.AgentPanel(None)
panel.resize(1280, 820)
panel.show()


def go():
    inp = panel.input
    inp.setFocus()
    for _ in range(10):
        app.processEvents()
    QTimer.singleShot(300, grab)


def grab():
    inp = panel.input
    vp = inp.viewport()
    print("vp stylesheet:", vp.styleSheet()[:200])
    print("vp autoFill:", vp.autoFillBackground())
    print("vp WA_translucent:", vp.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))
    print("vp geometry:", vp.geometry())
    inp.grab().save(os.path.join(OUT, "rv_input.png"))
    vp.grab().save(os.path.join(OUT, "rv_vp.png"))
    print("saved")
    app.quit()


QTimer.singleShot(2000, go)
sys.exit(app.exec())
