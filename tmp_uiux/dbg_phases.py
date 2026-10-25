# -*- coding: utf-8 -*-
"""分phase实测：遮罩创建/抓图/show/repaint 各花多少 ms。"""
import sys
import time

sys.path.insert(0, "src")
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer

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

# 手动复刻 _save 的遮罩段，逐 phase 计时
t = [time.monotonic()]
ov = ap._LoadingOverlay(dlg, dlg._ACCENT, "x", dlg._TEXT)
t.append(time.monotonic()); print("构造 overlay      : %.1f ms" % ((t[-1]-t[-2])*1000))
ov.setGeometry(dlg.rect())
t.append(time.monotonic()); print("setGeometry       : %.1f ms" % ((t[-1]-t[-2])*1000))
raw = dlg.grab()
t.append(time.monotonic()); print("dlg.grab()        : %.1f ms" % ((t[-1]-t[-2])*1000))
ov.raise_()
ov.show()
t.append(time.monotonic()); print("raise+show        : %.1f ms" % ((t[-1]-t[-2])*1000))
ov._frame = 0
ov._t0 = time.monotonic()
ov._timer.start()
QTimer.singleShot(0, ov._blur_async)
t.append(time.monotonic()); print("timer+singleShot  : %.1f ms" % ((t[-1]-t[-2])*1000))
ov.repaint()
t.append(time.monotonic()); print("repaint(首帧上屏) : %.1f ms" % ((t[-1]-t[-2])*1000))
print("合计(点击→首帧)   : %.1f ms" % ((t[-1]-t[0])*1000))
print("grab 尺寸         :", raw.width(), "x", raw.height(), "DPR", raw.devicePixelRatio())

# 模糊异步耗时
t2 = time.monotonic()
ov._blur_async()
print("_blur_async 耗时  : %.1f ms" % ((time.monotonic()-t2)*1000))
print("模糊就位          :", ov._blur is not None)
dlg.close()
