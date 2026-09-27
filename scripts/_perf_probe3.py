# -*- coding: utf-8 -*-
"""cProfile 剖析 AgentPanel 构造 + 延迟初始化（离屏）。输出 pstats 前 40。"""
import os, sys, cProfile, pstats, io, time
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, r'C:\Users\zhuzhu\Desktop\my first android app\src')

from PyQt6.QtWidgets import QApplication
app = QApplication([])

import winapp_migrator.ui.agent_panel as M

pr = cProfile.Profile()
pr.enable()
panel = M.AgentPanel()
app.processEvents()      # flush singleShot(0)
time.sleep(0.05)
app.processEvents()
panel.close()
pr.disable()

s = io.StringIO()
ps = pstats.Stats(pr, stream=s).sort_stats('cumulative')
ps.print_stats(45)
out = s.getvalue()
# 只保留与时间相关行
for line in out.splitlines():
    if line.strip() and (line.split() and line.split()[0].replace('.','').isdigit()):
        print(line)
