# -*- coding: utf-8 -*-
"""测试 QGraphicsDropShadowEffect 对 QSS 圆角输入框的阴影形状。"""
import sys, os
SRC = r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\src"
sys.path.insert(0, SRC)
from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout, QPlainTextEdit
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import QGraphicsDropShadowEffect

OUT = r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\tmp_uiux"
app = QApplication(sys.argv)
w = QWidget()
w.setStyleSheet("QWidget { background: #101216; }")
lay = QVBoxLayout(w)
lay.setContentsMargins(60, 60, 60, 60)
e = QPlainTextEdit()
e.setFixedHeight(44)
e.setStyleSheet("QPlainTextEdit { background: #12151B; color: #F3F5F9; border: none;"
                "border-radius: 10px; padding: 5px 10px; }")
eff = QGraphicsDropShadowEffect(e)
eff.setBlurRadius(24)
eff.setOffset(0, 0)
c = QColor("#2F52D8"); c.setAlpha(120)
eff.setColor(c)
e.setGraphicsEffect(eff)
lay.addWidget(e)
w.resize(1200, 160)
w.show()
app.processEvents()
w.grab().save(os.path.join(OUT, "glow_test_drop.png"))
print("saved")
app.quit()
