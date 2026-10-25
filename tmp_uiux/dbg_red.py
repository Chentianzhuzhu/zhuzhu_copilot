# -*- coding: utf-8 -*-
"""判定实验：paintEvent 的绘制内容能否到达离屏渲染结果。"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout
from PyQt6.QtGui import QPainter, QPen, QColor, QPixmap, QIcon
from PyQt6.QtCore import Qt

app = QApplication(sys.argv)
from zhuzhu_Copilot.ui import agent_panel as ap
from zhuzhu_Copilot.ui import agent_chat_bubbles as cb


def patched(self, e):
    p = QPainter(self)
    p.fillRect(self.rect(), QColor(255, 0, 0, 120))
    p.setPen(QPen(QColor("#FF0000"), 1, Qt.PenStyle.CustomDashLine))
    p.setDashPattern([4, 4])
    p.drawLine(cb.LEFT_GUTTER, self._inset, self.width() - 1, self._inset)
    p.end()


cb.ChatTurn.paintEvent = patched

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
turn.render([(cb.KIND_STREAM, {"html": "<div>body</div>"}, ("s", 0))],
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
pm = QPixmap(w.size())
w.render(pm)
pm.save(os.path.join(os.path.dirname(__file__), "dbg_red.png"))
im = pm.toImage()
top = turn.geometry().y()
y = top + turn._inset
print("turn top:", top, "inset:", turn._inset)
print("fill:", [im.pixelColor(x, top + 5).name() for x in (30, 100, 300, 570)])
print("dash:", [im.pixelColor(x, y).name() for x in (40, 100, 300, 500, 570)])
