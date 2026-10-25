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

# 触发真实保存流程（设置值不变，仅重新写入，无害）
dlg._save()
for _ in range(5):
    app.processEvents()

ov = getattr(dlg, "_overlay", None)
print("overlay created:", ov is not None, "visible:", ov.isVisible() if ov else None)

def shot(tag):
    dlg.grab().save(os.path.join(OUT, f"e2e_{tag}.png"))
    print(tag, "overlay visible:",
          getattr(dlg, "_overlay", None) is not None and dlg._overlay.isVisible())

QTimer.singleShot(120, lambda: shot("mid"))
QTimer.singleShot(600, lambda: shot("late"))

def check_done():
    print("dialog visible after 1100ms:", dlg.isVisible(),
          "result:", dlg.result())
    app.quit()

QTimer.singleShot(1100, check_done)
sys.exit(app.exec())
