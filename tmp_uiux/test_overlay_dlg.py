# -*- coding: utf-8 -*-
import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\src")
from PyQt6.QtWidgets import QApplication, QDialog
app = QApplication(sys.argv)
from zhuzhu_Copilot.ui import agent_panel as ap
par = QDialog()
par.resize(400, 300)
par.show()
for _ in range(5): app.processEvents()
ov = ap._LoadingOverlay(par, "#2F52D8", "请等待，正在应用更改中", "#FFFFFF")
ov.start()
for _ in range(5): app.processEvents()
print("plain QDialog -> overlay visible:", ov.isVisible())

# 现在测试真实设置对话框
panel = ap.AgentPanel()
panel.show()
for _ in range(20): app.processEvents()
dlg = ap._AgentSettingsDialog(panel)
dlg.show()
for _ in range(15): app.processEvents()
ov2 = ap._LoadingOverlay(dlg, "#2F52D8", "请等待，正在应用更改中", "#FFFFFF")
ov2.start()
for _ in range(10): app.processEvents()
print("real settings dlg -> overlay visible:", ov2.isVisible())
print("  dlg visible:", dlg.isVisible())
