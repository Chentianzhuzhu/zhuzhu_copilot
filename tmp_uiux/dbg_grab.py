# -*- coding: utf-8 -*-
"""grabWindow 时序：_save 后立即抓 vs 泵一帧后抓，像素级确认遮罩是否上屏。"""
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

screen = app.primaryScreen()

def dim_ratio(img):
    """统计窗口中心区域比暗色主题页面更『压暗』的像素占比（遮罩把页面压暗一层）。"""
    n = tot = 0
    for y in range(300, 560, 8):
        for x in range(300, 740, 8):
            c = img.pixelColor(x, y)
            tot += 1
            if c.lightness() < 60:   # 页面卡片 #1F232C 亮度约 35，压暗后更低
                n += 1
    return n / max(1, tot)

# 基线（无遮罩）
base = screen.grabWindow(dlg.winId()).toImage()
print("基线 dim 占比: %.2f" % dim_ratio(base))

t0 = time.monotonic()
dlg._save()
print("_save 返回 @ %.1f ms, 遮罩可见=%s" % ((time.monotonic()-t0)*1000, dlg._overlay.isVisible()))

g0 = screen.grabWindow(dlg.winId()).toImage()
print("立即抓 dim 占比: %.2f" % dim_ratio(g0))
app.processEvents()
g1 = screen.grabWindow(dlg.winId()).toImage()
print("泵1帧后 dim 占比: %.2f" % dim_ratio(g1))
g0.save("tmp_uiux/grab_t0.png")
g1.save("tmp_uiux/grab_t1.png")
dlg.close()
