# -*- coding: utf-8 -*-
import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\src")
from PyQt6.QtWidgets import QApplication, QLabel
from PyQt6.QtGui import QPixmap, QPainter, QColor
from PyQt6.QtCore import Qt
app = QApplication(sys.argv)
from zhuzhu_Copilot.ui import agent_panel as ap

S = 120
for name, svg in (("undo", ap._UNDO_SVG), ("redo", ap._REDO_SVG)):
    icon = ap._svg_icon(svg, S, "#FFFFFF")
    pm = QPixmap(S, S)
    pm.fill(QColor("#12151B"))
    p = QPainter(pm)
    icon.paint(p, 0, 0, S, S)
    p.end()
    pm.save(rf"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\tmp_uiux\clear_{name}.png")
print("done")
