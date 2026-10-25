# -*- coding: utf-8 -*-
import sys, os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
SRC = r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\src"
sys.path.insert(0, SRC)
from PyQt6.QtWidgets import QApplication
app = QApplication(sys.argv)
import zhuzhu_Copilot.ui.agent_panel as ap
panel = ap.AgentPanel()
panel.show()
for _ in range(30): app.processEvents()
dlg = ap._AgentSettingsDialog(panel)
dlg.show()
for _ in range(20): app.processEvents()
print("dlg rect:", dlg.rect(), "size:", dlg.size())
dlg._save()
for _ in range(10): app.processEvents()
ov = dlg._overlay
print("overlay geometry:", ov.geometry())
print("overlay isVisible:", ov.isVisible())
print("overlay isVisibleTo(dlg):", ov.isVisibleTo(dlg))
print("overlay parent:", ov.parent().__class__.__name__)
# 检查遮罩是否被其他子控件盖住：列出对话框最后添加的子控件（z-order）
kids = dlg.children()
print("last 6 children:", [c.__class__.__name__ for c in kids[-6:]])
dlg.grab().save(r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\tmp_uiux\dbg_dlg.png")
print("done")
