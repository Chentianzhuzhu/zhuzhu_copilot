# -*- coding: utf-8 -*-
"""真实面板：聚焦底部输入框，分别截暗色/浅色并裁剪输入框区域。"""
import sys, os
SRC = r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\src"
sys.path.insert(0, SRC)
os.chdir(SRC)
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer
from zhuzhu_Copilot.ui import agent_panel as ap
from zhuzhu_Copilot import app_identity
from PIL import Image

OUT = r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\tmp_uiux"
app = QApplication(sys.argv)
panel = ap.AgentPanel(None)
panel.resize(1280, 820)
panel.show()


def crop_input(theme_name):
    full = os.path.join(OUT, f"full_{theme_name}.png")
    panel.grab().save(full)
    im = Image.open(full)
    w, h = im.size
    # 底部 120px 高的输入条区域
    c = im.crop((0, h - 120, w, h))
    c.save(os.path.join(OUT, f"realinput_{theme_name}.png"))
    print("saved", theme_name)


def to_dark():
    app_identity.qsettings().setValue("agent_theme", "dark")
    panel._retheme()
    QTimer.singleShot(900, step_dark)


def step_dark():
    panel.input.setFocus()
    QTimer.singleShot(400, lambda: (crop_input("dark"), QTimer.singleShot(100, to_light)))


def to_light():
    app_identity.qsettings().setValue("agent_theme", "light")
    panel._retheme()
    QTimer.singleShot(900, step_light)


def step_light():
    panel.input.setFocus()
    QTimer.singleShot(400, lambda: (crop_input("light"), QTimer.singleShot(100, finish)))


def finish():
    app.quit()


QTimer.singleShot(2000, to_dark)
sys.exit(app.exec())
