# -*- coding: utf-8 -*-
import sys, os
SRC = r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\src"
sys.path.insert(0, SRC)
os.chdir(SRC)
from PyQt6.QtWidgets import QApplication, QLabel
from PyQt6.QtCore import Qt
from zhuzhu_Copilot.ui import agent_panel as ap

app = QApplication(sys.argv)

FINAL = ("好的，我已经完成了对界面的美化。本次主要从**层次感**、**字重**、**流式动画**"
         "和**数字对齐**四个方面进行了优化，让整体观感更接近大厂客户端的精致质感。\n\n"
         "具体改动包括：\n\n1. 去除了多余的描边，改用微对比的色阶分层；\n"
         "2. 暗色模式下字重统一减细一档，避免发糊。")

htm = ap._md_to_html(FINAL)
print("=== HTML len:", len(htm))
print(htm[:600])
print("...")

lbl = QLabel()
lbl.setTextFormat(Qt.TextFormat.RichText)
lbl.setWordWrap(True)
lbl.setText(htm)
lbl.resize(660, 400)
print("hfw(660) =", lbl.heightForWidth(660))
print("sizeHint =", lbl.sizeHint())
doc = lbl.document()
print("doc textWidth =", doc.textWidth())
print("doc size =", doc.size())
# 手动设 textWidth
doc.setTextWidth(660)
print("after setTextWidth(660) doc size =", doc.size())
