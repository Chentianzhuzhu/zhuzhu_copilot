# -*- coding: utf-8 -*-
"""语句级插桩：start()/repaint()/paintEvent/_build_pages_step 的时序。"""
import sys
import time

sys.path.insert(0, "src")
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QPoint

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

log = []
t0 = [0.0]
def mark(msg):
    log.append(((time.monotonic() - t0[0]) * 1000, msg))

orig_start = ap._LoadingOverlay.start
def rec_start(self):
    mark("start() 进入")
    orig_start(self)
    mark("start() 返回")
ap._LoadingOverlay.start = rec_start

orig_paint = ap._LoadingOverlay.paintEvent
def rec_paint(self, e):
    mark(">>> paintEvent")
    orig_paint(self, e)
ap._LoadingOverlay.paintEvent = rec_paint

orig_bps = ap._AgentSettingsDialog._build_pages_step
def rec_bps(self):
    mark("_build_pages_step 进入")
    r = orig_bps(self)
    mark("_build_pages_step 返回")
    return r
ap._AgentSettingsDialog._build_pages_step = rec_bps

t0[0] = time.monotonic()
mark("_save() 进入")
dlg._save()
mark("_save() 返回")
for ms, msg in log:
    print("%7.1f ms  %s" % (ms, msg))
dlg.close()
