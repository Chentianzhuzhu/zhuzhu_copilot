# -*- coding: utf-8 -*-
"""用真实 _DropTextEdit + _input_qss 复现聚焦光晕形状。"""
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
lay.setContentsMargins(60, 60, 60, 60)
e = ap._DropTextEdit()
e.setStyleSheet(ap._input_qss())
e.setGraphicsEffect(ap._focus_glow_effect(e))
lay.addWidget(e)
w.resize(1200, 160)
w.show()
e.setFocus()
for _ in range(5):
    app.processEvents()
w.grab().save(os.path.join(OUT, "glow_test_real.png"))
print("saved")
app.quit()
