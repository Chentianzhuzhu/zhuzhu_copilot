# -*- coding: utf-8 -*-
"""诊断：FIELD 背景到底画在 frame 还是 viewport 上。"""
import sys, os
SRC = r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\src"
sys.path.insert(0, SRC)
from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout
from PyQt6.QtCore import Qt
from zhuzhu_Copilot.ui import agent_panel as ap

OUT = r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\tmp_uiux"
app = QApplication(sys.argv)
ap._apply_colors(ap._THEMES["dark"], force=True)

w = QWidget()
w.setStyleSheet(f"QWidget {{ background: {ap.BG}; }}")
lay = QVBoxLayout(w)
lay.setContentsMargins(40, 40, 40, 40)
e = ap._DropTextEdit()
e.setFixedHeight(44)
e.setStyleSheet(ap._input_qss())
lay.addWidget(e)
w.resize(900, 130)
w.show()
for _ in range(6):
    app.processEvents()

# 整个 widget
e.grab().save(os.path.join(OUT, "diag_frame.png"))
# 单独 grab viewport
vp = e.viewport()
print("viewport autoFill", vp.autoFillBackground())
print("viewport translucent attr", vp.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))
vp.grab().save(os.path.join(OUT, "diag_viewport.png"))
print("viewport geometry", vp.geometry())
print("frame geometry", e.rect())
app.quit()
