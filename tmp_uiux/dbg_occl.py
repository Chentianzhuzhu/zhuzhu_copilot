# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, "src")
from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout
from PyQt6.QtGui import QIcon

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
app.processEvents(); app.processEvents()
screen = app.primaryScreen()

def scan():
    im = screen.grabWindow(w.winId()).toImage()
    y = turn.geometry().y() + turn._inset
    hits = [x for x in range(20, 600) if im.pixelColor(x, y).name() not in ("#101216", "#0b0d11")]
    return len(hits), (hits[0], hits[-1]) if hits else None

print("正常（_box 可见）:", scan())
turn._box.hide()
app.processEvents(); app.processEvents()
print("隐藏 _box 后     :", scan())
turn._box.show()
# 再验证：把虚线改到 _box 外面画不了，但可以把 _box 下移——直接改 _inset 看虚线是否出现
turn._box.show()
app.processEvents()
