# -*- coding: utf-8 -*-
"""真实面板：聚焦输入框，单独 grab 输入框 widget（避免整面板 grab 崩溃）。"""
import sys, os
SRC = r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\src"
sys.path.insert(0, SRC)
os.chdir(SRC)
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer
from zhuzhu_Copilot.ui import agent_panel as ap
from PIL import Image

OUT = r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\tmp_uiux"
app = QApplication(sys.argv)
panel = ap.AgentPanel(None)
panel.resize(1280, 820)
panel.show()


def go():
    panel.input.setFocus()
    for _ in range(10):
        app.processEvents()
    QTimer.singleShot(400, grab)


def grab():
    try:
        inp = panel.input
        print("input geometry", inp.geometry(), "height", inp.height())
        pm = inp.grab()
        ok = pm.save(os.path.join(OUT, "real_input_grab.png"))
        print("save ok", ok)
    except Exception as ex:
        import traceback
        traceback.print_exc()
    app.quit()


QTimer.singleShot(2000, go)
sys.exit(app.exec())
