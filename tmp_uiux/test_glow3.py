# -*- coding: utf-8 -*-
"""通过真实 FocusIn 事件安装光晕（复现内部直角）。"""
import sys, os
SRC = r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\src"
sys.path.insert(0, SRC)
from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout
from PyQt6.QtCore import Qt
from zhuzhu_Copilot.ui import agent_panel as ap
from PIL import Image

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
ap._install_focus_glow(e)   # 事件过滤器
lay.addWidget(e)
w.resize(900, 130)
w.show()
e.setFocus()
for _ in range(8):
    app.processEvents()

pm = w.grab()
pm.save(os.path.join(OUT, "glow3.png"))
# 裁内部左上角
im = Image.open(os.path.join(OUT, "glow3.png")).convert("RGBA")
# 输入框在 y=40-84, x=40-860
cp = im.crop((38, 38, 85, 80))
ww, hh = cp.size
cp.resize((ww * 6, hh * 6), Image.NEAREST).save(os.path.join(OUT, "glow3_tl.png"))
print("done")
app.quit()
