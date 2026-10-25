# -*- coding: utf-8 -*-
import sys, os
SRC = r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\src"
sys.path.insert(0, SRC)
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer, QElapsedTimer
app = QApplication(sys.argv)
import zhuzhu_Copilot.ui.agent_panel as ap
panel = ap.AgentPanel(); panel.show()
for _ in range(30): app.processEvents()
dlg = ap._AgentSettingsDialog(panel); dlg.show()
for _ in range(20): app.processEvents()
clk = QElapsedTimer(); clk.start()
dlg._save()
last_visible = True
def poll():
    global last_visible
    if dlg.isVisible():
        QTimer.singleShot(100, poll)
    else:
        print(f"dialog closed at {clk.elapsed()}ms result={dlg.result()}")
        app.quit()
QTimer.singleShot(100, poll)
# 安全超时
QTimer.singleShot(15000, app.quit)
sys.exit(app.exec())
