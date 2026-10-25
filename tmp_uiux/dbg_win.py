# -*- coding: utf-8 -*-
"""在真实 Windows 平台下渲染 ChatTurn，验证虚线是否被 _box 遮盖（vs 离屏怪癖）。"""
import sys

sys.path.insert(0, "src")

from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout
from PyQt6.QtGui import QPixmap, QIcon

app = QApplication(sys.argv)
from zhuzhu_Copilot.ui import agent_panel as ap
from zhuzhu_Copilot.ui import agent_chat_bubbles as cb

ap._apply_colors(ap._THEMES["dark"], force=True)
style = cb.ChatStyle(card=ap.AI_BG, border=ap.BORDER, border_soft=ap.BORDER_SOFT,
                     dash=ap.BORDER_SOFT, text=ap.TEXT, text_dim=ap.TEXT_DIM,
                     accent=ap.ACCENT, muted=ap.TEXT_DIM, icon_shell=ap.HOVER,
                     icon_color=ap.ICON_GRAY, tag_bg="#333A46", tag_fg=ap.TEXT_DIM,
                     user_bg=ap.USER_BG, user_fg="#FFF", cmd_fg=ap.TEXT,
                     ok_fg=ap.LINK_COLOR, hover=ap.HOVER, panel=ap.PANEL,
                     bg=ap.BG, band=ap.BG, w_emphasis=400,
                     font_numeric='"Segoe UI", "Cascadia Mono", Consolas, monospace')
icons = lambda n, s, c: QIcon()
turn = cb.ChatTurn(style, icons)
turn.render([(cb.KIND_STREAM, {"html": "<div>body text here</div>"}, ("s", 0))],
            cost=65, live=False, done=True)
turn.setMaximumWidth(560)
w = QWidget()
w.setStyleSheet("background: #101216;")
lay = QVBoxLayout(w)
lay.setContentsMargins(20, 20, 20, 20)
lay.addWidget(turn)
w.resize(620, 300)
w.show()
app.processEvents()
app.processEvents()
# 用屏幕实际抓图（真实 OS 合成路径），不用 QWidget.render（对半透明窗口不可靠）
screen = app.primaryScreen()
pm = screen.grabWindow(w.winId())
pm.save("tmp_uiux/dbg_win.png")
im = pm.toImage()
top = turn.geometry().y()
y = top + turn._inset
print("platform:", app.platformName(), "turn top:", top, "inset:", turn._inset)
# 整行扫描（4实4空虚线，单点采样会落在空隙里）
hits = [x for x in range(20, 600) if im.pixelColor(x, y).name() not in ("#101216", "#0b0d11")]
print("dash row non-bg count:", len(hits), "first/last:", (hits[0], hits[-1]) if hits else None)
runs = []
if hits:
    s = p = hits[0]
    for x in hits[1:]:
        if x == p + 1:
            p = x
        else:
            runs.append((s, p)); s = p = x
    runs.append((s, p))
print("dash runs:", runs[:6], "..." if len(runs) > 6 else "", "total runs:", len(runs))
