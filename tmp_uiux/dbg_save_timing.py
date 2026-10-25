# -*- coding: utf-8 -*-
"""实测：点保存 → 遮罩首绘延迟（真实 Windows 平台）+ 当帧截图。"""
import sys
import time

sys.path.insert(0, "src")
from PyQt6.QtWidgets import QApplication

app = QApplication(sys.argv)
from zhuzhu_Copilot.ui import agent_panel as ap

# 屏蔽落盘副作用
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

painted = []
orig_paint = ap._LoadingOverlay.paintEvent
def rec(self, e):
    if not painted:
        painted.append(time.monotonic())
    orig_paint(self, e)
ap._LoadingOverlay.paintEvent = rec

marks = {}
orig_ensure = dlg._ensure_all_pages
def rec_ensure():
    marks["heavy"] = time.monotonic()
    orig_ensure()
dlg._ensure_all_pages = rec_ensure

t0 = time.monotonic()
dlg._save()
t_end = time.monotonic()
ov = dlg._overlay
print("遮罩可见(同步)      :", ov.isVisible())
print("首绘延迟            : %.1f ms" % ((painted[0] - t0) * 1000 if painted else -1))
print("_save() 总耗时      : %.1f ms" % ((t_end - t0) * 1000))
print("重活(_ensure)开始时点: %.1f ms（首绘应 ≤ 此值）" % ((marks.get('heavy', t_end) - t0) * 1000))
print("遮罩铺满对话框      :", ov.geometry() == dlg.rect())

# 当帧截图（不泵事件，模拟用户视角的第一帧）
screen = app.primaryScreen()
pm = screen.grabWindow(dlg.winId())
pm.save("tmp_uiux/save_overlay_frame0.png")
print("frame0 saved")

# 泵事件后模糊就位
for _ in range(10):
    app.processEvents()
print("模糊层就位          :", ov._blur is not None, "| 原图释放:", ov._raw is None)
pm2 = screen.grabWindow(dlg.winId())
pm2.save("tmp_uiux/save_overlay_blur.png")
print("blur frame saved")
dlg.close()
