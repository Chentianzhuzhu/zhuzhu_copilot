# -*- coding: utf-8 -*-
"""对比 dlg.grab() 与「render 到 1/4 小图」的耗时与输出尺寸。"""
import sys
import time

sys.path.insert(0, "src")
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QPixmap, QPainter
from PyQt6.QtCore import Qt, QRectF

app = QApplication(sys.argv)
from zhuzhu_Copilot.ui import agent_panel as ap

ap.agent_skills.save_settings = lambda data: True
ap.agent_skills.save_mcp_servers = lambda s: True
ap.agent_tts.save_config = lambda **kw: None
import zhuzhu_Copilot.core.agent_proxy_service as aps
aps.apply = lambda: None
aps.status = lambda: {}

dlg = ap._AgentSettingsDialog()
dlg.resize(1040, 860)
dlg.show()
for _ in range(6):
    app.processEvents()

# 冷/暖各测一次 grab
for i in range(2):
    t = time.monotonic()
    pm = dlg.grab()
    print("grab() #%d: %.1f ms -> %dx%d DPR=%s" % (i, (time.monotonic()-t)*1000, pm.width(), pm.height(), pm.devicePixelRatio()))

# render 到 1/4 小图（缩放光栅化）
sw, sh = max(1, int(dlg.width()*0.25)), max(1, int(dlg.height()*0.25))
for i in range(2):
    pm2 = QPixmap(sw, sh)
    pm2.fill(Qt.GlobalColor.transparent)
    t = time.monotonic()
    p = QPainter(pm2)
    try:
        dlg.render(p, QRectF(0, 0, sw, sh), QRectF(0, 0, dlg.width(), dlg.height()))
    finally:
        p.end()
    print("render(1/4) #%d: %.1f ms -> %dx%d" % (i, (time.monotonic()-t)*1000, pm2.width(), pm2.height()))
pm2.save("tmp_uiux/render_small.png")
pm.save("tmp_uiux/grab_full.png")
print("saved both")
dlg.close()
