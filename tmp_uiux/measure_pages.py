# -*- coding: utf-8 -*-
import sys, os, time
SRC = r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\src"
sys.path.insert(0, SRC)
from PyQt6.QtWidgets import QApplication
app = QApplication(sys.argv)
import zhuzhu_Copilot.ui.agent_panel as ap
panel = ap.AgentPanel(); panel.show()
for _ in range(30): app.processEvents()
dlg = ap._AgentSettingsDialog(panel); dlg.show()
for _ in range(20): app.processEvents()
# 逐个构建页面，测量每个页面耗时
for i in range(len(dlg._page_builders)):
    t = time.monotonic()
    page = dlg._page_builders[i]()
    _install = ap._install_focus_glow_all
    _install(page)
    wrapped = dlg._wrap_page(page)
    dlg.stack.addWidget(wrapped)
    dt = (time.monotonic()-t)*1000
    print(f"page {i}: {dt:.0f}ms")
