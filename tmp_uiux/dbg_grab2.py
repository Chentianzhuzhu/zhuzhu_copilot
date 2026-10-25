# -*- coding: utf-8 -*-
"""精确判定：固定点像素变暗（遮罩压暗）+ 中心区域出现主题色圆点（转圈）。"""
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
PX = (520, 400)   # 窗口内一个稳定的卡片背景点

def accent_pixels(img):
    """中心 240x120 区域内『偏蓝且亮』的像素数（转圈圆点是主题蓝）。"""
    n = 0
    for y in range(340, 460, 3):
        for x in range(400, 640, 3):
            c = img.pixelColor(x, y)
            if c.blue() > 120 and c.blue() > c.red() + 40 and c.lightness() > 40:
                n += 1
    return n

base = screen.grabWindow(dlg.winId()).toImage()
b0 = base.pixelColor(*PX).name()
a0 = accent_pixels(base)

t0 = time.monotonic()
dlg._save()
t_save = (time.monotonic() - t0) * 1000

g0 = screen.grabWindow(dlg.winId()).toImage()
p0 = g0.pixelColor(*PX).name()
app.processEvents()
g1 = screen.grabWindow(dlg.winId()).toImage()
p1 = g1.pixelColor(*PX).name()

print("_save 返回 %.1fms | 遮罩可见=%s" % (t_save, dlg._overlay.isVisible()))
print("固定点颜色: 基线=%s 立即抓=%s 泵1帧=%s" % (b0, p0, p1))
print("压暗生效(立即抓): %s | 压暗生效(泵1帧): %s" % (p0 != b0, p1 != b0))
print("主题色圆点数: 基线=%d 立即抓=%d 泵1帧=%d" % (a0, accent_pixels(g0), accent_pixels(g1)))
g0.save("tmp_uiux/g2_t0.png"); g1.save("tmp_uiux/g2_t1.png")
dlg.close()
