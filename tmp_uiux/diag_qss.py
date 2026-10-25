# -*- coding: utf-8 -*-
import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\src")
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import qInstallMessageHandler, QtMsgType
msgs = []
def cb(mode, ctx, msg):
    msgs.append(f"[{mode}] {msg}")
qInstallMessageHandler(cb)
app = QApplication(sys.argv)
from zhuzhu_Copilot.ui import agent_panel as ap
dlg = ap._AgentSettingsDialog()
dlg.show()
app.processEvents()
for m in msgs:
    if "stylesheet" in m or "styleshee" in m:
        print(m)
print("=== total msgs:", len(msgs))
