# -*- coding: utf-8 -*-
import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\src")
from PyQt6.QtWidgets import QApplication, QLineEdit, QPlainTextEdit
app = QApplication(sys.argv)
from zhuzhu_Copilot.ui import agent_panel as ap
dlg = ap._AgentSettingsDialog()
# 构建全部页面（触发懒加载）
try:
    dlg._ensure_all_pages()
except Exception as e:
    print("ensure warn", e)
# 遍历所有输入控件，打印它们自己的 styleSheet
for cls in (QLineEdit, QPlainTextEdit):
    seen = set()
    for w in dlg.findChildren(cls):
        if id(w) in seen: continue
        seen.add(id(w))
        ss = w.styleSheet()
        if ss:
            print(f"--- {cls.__name__} obj={w.objectName()!r} parent={w.parent().__class__.__name__}")
            print(repr(ss))
