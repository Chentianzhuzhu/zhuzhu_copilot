# -*- coding: utf-8 -*-
"""对比不同 QSS 让背景跟随圆角的方案。"""
import sys, os
SRC = r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\src"
sys.path.insert(0, SRC)
from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout, QPlainTextEdit
from PIL import Image

OUT = r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\tmp_uiux"
app = QApplication(sys.argv)
w = QWidget()
w.setStyleSheet("QWidget { background: #404040; }")
lay = QVBoxLayout(w)
lay.setContentsMargins(50, 20, 50, 20)
lay.setSpacing(16)

FIELD = "#12151B"
R = 10
configs = {
    "none_border": f"border: none; border-radius: {R}px; background: {FIELD};",
    "same_border": f"border: 1px solid {FIELD}; border-radius: {R}px; background: {FIELD};",
    "trans_border": f"border: 1px solid transparent; border-radius: {R}px; background: {FIELD};",
}
for name, core in configs.items():
    e = QPlainTextEdit()
    e.setFixedHeight(40)
    e.setStyleSheet(f"QPlainTextEdit {{ {core} color: #F3F5F9; padding: 5px 10px; }}")
    lay.addWidget(e)
w.resize(700, 220)
w.show()
for _ in range(6):
    app.processEvents()
w.grab().save(os.path.join(OUT, "qss_radius_cmp.png"))
# 裁每个输入框左上角放大
im = Image.open(os.path.join(OUT, "qss_radius_cmp.png")).convert("RGBA")
# 输入框 y: 20..60, 76..116, 132..172
for i, name in enumerate(configs):
    y0 = 20 + i * 56
    cp = im.crop((45, y0 - 2, 90, y0 + 38))
    ww, hh = cp.size
    cp.resize((ww * 5, hh * 5), Image.NEAREST).save(os.path.join(OUT, f"qssrad_{name}.png"))
print("done")
app.quit()
