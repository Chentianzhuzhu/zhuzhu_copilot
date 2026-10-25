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
samples = []
def poll():
    ov = dlg._overlay
    if ov is not None:
        samples.append((clk.elapsed(), round(ov._angle, 2)))
    if clk.elapsed() < 2000 and dlg.isVisible():
        QTimer.singleShot(50, poll)
    else:
        print("n samples:", len(samples))
        for ms, a in samples:
            print(f"  {ms}ms angle={a}")
        app.quit()
QTimer.singleShot(0, poll)
sys.exit(app.exec())
