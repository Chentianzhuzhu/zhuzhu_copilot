# -*- coding: utf-8 -*-
"""浅色主题下的保存遮罩截图（真实平台）。"""
import sys

sys.path.insert(0, "src")
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QSettings

app = QApplication(sys.argv)
from zhuzhu_Copilot.ui import agent_panel as ap
from zhuzhu_Copilot import app_identity

# 切浅色主题
app_identity.qsettings().setValue("agent_theme", "light")

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
screen = app.primaryScreen()

dlg._save()
# 让异步模糊算完
for _ in range(8):
    app.processEvents()
pm = screen.grabWindow(dlg.winId())
pm.save("tmp_uiux/save_overlay_light.png")
print("light overlay saved; blur ready:", dlg._overlay._blur is not None)
dlg.close()
app_identity.qsettings().setValue("agent_theme", "dark")
