# -*- coding: utf-8 -*-
import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\src")
from PyQt6.QtWidgets import QApplication, QWidget
app = QApplication(sys.argv)
from zhuzhu_Copilot.ui import agent_panel as ap
par = QWidget()
par.resize(400, 300)
par.show()
for _ in range(5): app.processEvents()
ov = ap._LoadingOverlay(par, "#2F52D8", "请等待，正在应用更改中", "#FFFFFF")
print("before start isHidden:", ov.isHidden())
ov.start()
for _ in range(5): app.processEvents()
print("overlay visible:", ov.isVisible(), "isHidden:", ov.isHidden(), "geometry:", ov.geometry())
print("parent visible:", par.isVisible())
par.grab().save(r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\tmp_uiux\ov_min.png")
ov.grab().save(r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\tmp_uiux\ov_min_overlay.png")
print("done")
