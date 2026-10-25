# -*- coding: utf-8 -*-
import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
src = r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\src"
sys.path.insert(0, src)
from PyQt6.QtWidgets import QApplication
app = QApplication(sys.argv)
from zhuzhu_Copilot.ui import agent_panel as ap
# 强制暗色（与用户截图一致）
try:
    ap.apply_theme("dark")
except Exception as e:
    print("theme warn", e)
w = ap.CodePreviewWindow()
w.resize(ap.CodePreviewWindow.WIDTH, 600)
w.show()
app.processEvents()
# grab 整个工具栏（mode_row）
mw = w.mode_row
pm = mw.grab()
pm.save(r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\tmp_uiux\src_toolbar.png")
# 单独 grab 两个箭头按钮
w.undo_btn.grab().save(r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\tmp_uiux\src_undo.png")
w.redo_btn.grab().save(r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\tmp_uiux\src_redo.png")
print("done")
