# -*- coding: utf-8 -*-
"""真实界面验证：构造 AgentPanel，分别截暗色 / 浅色主题整窗图，自动退出。"""
import sys, os
SRC = r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\src"
sys.path.insert(0, SRC)
os.chdir(SRC)
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer
from zhuzhu_Copilot.ui import agent_panel as ap
from zhuzhu_Copilot import app_identity

OUT = r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\tmp_uiux"
app = QApplication(sys.argv)
panel = ap.AgentPanel(None)
panel.resize(1280, 820)
panel.show()


def shot(theme_name, then):
    panel.grab().save(os.path.join(OUT, f"real_{theme_name}.png"))
    print("saved", theme_name)
    QTimer.singleShot(100, then)


def to_dark():
    app_identity.qsettings().setValue("agent_theme", "dark")
    panel._retheme()
    QTimer.singleShot(1000, lambda: shot("dark", to_light))


def to_light():
    app_identity.qsettings().setValue("agent_theme", "light")
    panel._retheme()
    QTimer.singleShot(1000, lambda: shot("light", finish))


def finish():
    app.quit()


# 先等面板初始化（后台线程）完成，再切暗色
QTimer.singleShot(2000, to_dark)
sys.exit(app.exec())
