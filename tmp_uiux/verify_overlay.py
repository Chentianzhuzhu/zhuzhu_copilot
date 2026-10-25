# -*- coding: utf-8 -*-
import sys, os
SRC = r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\src"
sys.path.insert(0, SRC)
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer

OUT = r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\tmp_uiux"
app = QApplication(sys.argv)

import zhuzhu_Copilot.ui.agent_panel as ap

panel = ap.AgentPanel()
panel.show()
for _ in range(30):
    app.processEvents()

dlg = ap._AgentSettingsDialog(panel)
dlg.show()
for _ in range(20):
    app.processEvents()
print("dlg size", dlg.size())

ov = ap._LoadingOverlay(dlg, dlg._ACCENT,
                        ap._ui("请等待，正在应用更改中"), dlg._TEXT)
ov.start()

def grab(tag):
    dlg.grab().save(os.path.join(OUT, f"overlay_{tag}.png"))
    print("grabbed", tag, "frame", ov._frame, "angle_deg", round(ov._angle * 57.2958, 1))

QTimer.singleShot(80, lambda: grab("f0"))
QTimer.singleShot(300, lambda: grab("f1"))
QTimer.singleShot(520, lambda: grab("f2"))
QTimer.singleShot(700, app.quit)
sys.exit(app.exec())
