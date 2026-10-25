# -*- coding: utf-8 -*-
import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\src")
from PyQt6.QtWidgets import QApplication
app = QApplication(sys.argv)
from zhuzhu_Copilot.ui import agent_panel as ap
dlg = ap._AgentSettingsDialog.__new__(ap._AgentSettingsDialog)
# 手动填充 _* 变量（模拟 __init__）
dlg._BG = ap._base_color("BG")
dlg._PANEL = ap._base_color("PANEL")
dlg._PANEL2 = ap._base_color("CARD")
dlg._HOVER = ap._base_color("HOVER")
dlg._TEXT = ap.TEXT
dlg._DIM = ap.TEXT_DIM
dlg._ACCENT = ap.ACCENT
dlg._ACCENT_HOVER = ap.ACCENT_HOVER
dlg._DANGER = ap.ERR
dlg._DANGER_HOVER = ap.ERR
dlg._BORDER = ap._base_color("BORDER")
print("===== DIALOG QSS =====")
print(dlg._dialog_qss())
print("===== NAV QSS =====")
print(dlg._nav_qss())
