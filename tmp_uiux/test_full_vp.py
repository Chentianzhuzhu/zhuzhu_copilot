# -*- coding: utf-8 -*-
"""测试 viewport 占满 + 自绘完整圆角背景。"""
import sys, os
SRC = r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\src"
sys.path.insert(0, SRC)
from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout, QPlainTextEdit
from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QPainter, QColor
from PIL import Image

OUT = r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\tmp_uiux"
app = QApplication(sys.argv)


class Full(QPlainTextEdit):
    def paintEvent(self, e):
        p = QPainter(self.viewport())
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#12151B"))
        p.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), 10, 10)
        p.end()
        super().paintEvent(e)


w = QWidget()
w.setStyleSheet("QWidget { background: #808080; }")
lay = QVBoxLayout(w)
lay.setContentsMargins(50, 30, 50, 30)
e = Full()
e.setFixedHeight(44)
e.setStyleSheet("QPlainTextEdit { background: transparent; border: none;"
                "border-radius: 10px; padding: 0; color: #F3F5F9; }")
e.setPlaceholderText("/ for commad @ for agent")
e.document().setDocumentMargin(10)
lay.addWidget(e)
w.resize(800, 110)
w.show()
for _ in range(6):
    app.processEvents()
print("viewport geometry", e.viewport().geometry())
w.grab().save(os.path.join(OUT, "full_vp.png"))
# 裁左上角
im = Image.open(os.path.join(OUT, "full_vp.png")).convert("RGBA")
cp = im.crop((45, 25, 95, 65))
ww, hh = cp.size
cp.resize((ww * 6, hh * 6), Image.NEAREST).save(os.path.join(OUT, "full_vp_tl.png"))
print("done")
app.quit()
