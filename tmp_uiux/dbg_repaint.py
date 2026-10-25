# -*- coding: utf-8 -*-
"""验证 repaint() 是否同步调用 paintEvent；若否则找出真正的首帧路径。"""
import sys
import time

sys.path.insert(0, "src")
from PyQt6.QtWidgets import QApplication

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

events = []
orig = ap._LoadingOverlay.paintEvent
def rec(self, e):
    events.append(("paint", time.monotonic()))
    orig(self, e)
ap._LoadingOverlay.paintEvent = rec

t0 = time.monotonic()
dlg._save()
t_end = time.monotonic()
print("_save() 总耗时: %.1f ms" % ((t_end - t0) * 1000))
for name, ts in events[:6]:
    print("  %s @ %.1f ms" % (name, (ts - t0) * 1000))
print("遮罩可见:", dlg._overlay.isVisible(), "| 可见性即时查询:", dlg._overlay.isVisibleTo(dlg))
print("update 被禁用?", not dlg._overlay.updatesEnabled())
dlg.close()
