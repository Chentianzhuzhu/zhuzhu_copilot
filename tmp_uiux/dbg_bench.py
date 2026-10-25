# -*- coding: utf-8 -*-
"""同进程对比：grab() vs render-scale 底图，各 3 次。"""
import sys
import time

sys.path.insert(0, "src")
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QPixmap, QPainter
from PyQt6.QtCore import Qt, QPoint

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

SCALE = 0.25
def grab_full():
    t = time.monotonic(); pm = dlg.grab(); return (time.monotonic()-t)*1000, pm
def grab_then_down():
    t = time.monotonic()
    pm = dlg.grab().scaled(int(dlg.width()*SCALE), int(dlg.height()*SCALE),
                           Qt.AspectRatioMode.IgnoreAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
    return (time.monotonic()-t)*1000, pm
def render_small():
    t = time.monotonic()
    sw, sh = int(dlg.width()*SCALE), int(dlg.height()*SCALE)
    pm = QPixmap(sw, sh); pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    try:
        p.scale(SCALE, SCALE)
        dlg.render(p, QPoint(0, 0))
    finally:
        p.end()
    return (time.monotonic()-t)*1000, pm

for name, fn in (("grab()", grab_full), ("grab+downscale", grab_then_down), ("render-scale", render_small)):
    ts = []
    for _ in range(3):
        ms, pm = fn(); ts.append(ms)
    print("%-16s: %s ms  (size %dx%d)" % (name, " ".join("%.1f" % x for x in ts), pm.width(), pm.height()))
dlg.close()
