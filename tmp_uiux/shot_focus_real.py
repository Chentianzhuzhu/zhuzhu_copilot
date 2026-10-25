# -*- coding: utf-8 -*-
"""真实面板：聚焦底部输入框，截图并裁剪四角放大。"""
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
    for _ in range(8):
        app.processEvents()
    QTimer.singleShot(300, grab)


def grab():
    pm = panel.grab()
    pm.save(os.path.join(OUT, "real_focus.png"))
    # 用 PIL 裁剪输入框四角
    im = Image.open(os.path.join(OUT, "real_focus.png")).convert("RGB")
    # 输入框大致在底部 y=740..800，左右各留边。先定位：裁整个底部条
    im.crop((0, 720, 1280, 820)).save(os.path.join(OUT, "real_focus_bottom.png"))
    app.quit()


QTimer.singleShot(2000, go)
sys.exit(app.exec())
