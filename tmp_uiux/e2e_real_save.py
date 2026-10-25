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
for _ in range(30): app.processEvents()
dlg = ap._AgentSettingsDialog(panel)
dlg.show()
for _ in range(20): app.processEvents()
dlg._save()
def grab(tag, when):
    def f():
        ov = dlg._overlay
        ang = ov._angle if ov is not None else None
        dlg.grab().save(os.path.join(OUT, f"real_{tag}.png"))
        print(f"{tag} ({when}ms): overlay visible={ov.isVisible() if ov else None} angle={ang}")
    QTimer.singleShot(when, f)
grab("t1", 300)
grab("t2", 600)
grab("t3", 900)
def done():
    print("after 1300ms dlg visible:", dlg.isVisible(), "result:", dlg.result())
    app.quit()
QTimer.singleShot(1300, done)
sys.exit(app.exec())
