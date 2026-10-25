# -*- coding: utf-8 -*-
"""验证 _EmergeBand 在流式期间是否可见、是否在记录行龄。"""
import sys, os
SRC = r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\src"
sys.path.insert(0, SRC)
os.chdir(SRC)
from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout
from zhuzhu_Copilot.ui import agent_panel as ap
from zhuzhu_Copilot.ui import agent_chat_bubbles as cb

app = QApplication(sys.argv)

def make_style():
    return cb.ChatStyle(
        card=ap.AI_BG, border=ap.BORDER, border_soft=ap.BORDER_SOFT, dash=ap.BORDER_SOFT,
        text=ap.TEXT, text_dim=ap.TEXT_DIM, accent=ap.ACCENT, muted=ap.TEXT_DIM,
        icon_shell=ap.HOVER, icon_color=ap.ICON_GRAY,
        tag_bg=ap._mix_hex(ap.BORDER_SOFT, ap.AI_BG, 0.55), tag_fg=ap.TEXT_DIM,
        user_bg=ap.USER_BG, user_fg="#FFFFFF", cmd_fg=ap.TEXT, ok_fg=ap.LINK_COLOR,
        hover=ap.HOVER, panel=ap.PANEL, bg=ap.BG,
        think_bg=ap._mix_hex(ap.ACCENT, ap.AI_BG, 0.10),
        think_border=ap._mix_hex(ap.ACCENT, ap.BORDER, 0.30),
        tag_plan_bg=ap._mix_hex(ap.BORDER_SOFT, ap.AI_BG, 0.55),
        tag_exec_bg=ap._mix_hex(ap.ACCENT, ap.AI_BG, 0.32),
        tool_shell=ap._mix_hex(ap.BORDER_SOFT, ap.HOVER, 0.22),
        band=ap.BG, out_fg=ap.LINK_COLOR, out_line=ap.ACCENT,
        dot=ap._base_color("BORDER"),
        w_title=ap._W_TITLE, w_strong=ap._W_STRONG, font_numeric=ap.NUMERIC_FONT_STACK)

host = QWidget()
host.setStyleSheet(f"background:{ap.BG};")
lay = QVBoxLayout(host)
turn = cb.ChatTurn(make_style(), lambda k,s,c: ap._line_icon(k,s,c))
lay.addWidget(turn)
host.resize(700, 600)
host.show()

FINAL = ("好的，我已经完成了对界面的美化。本次主要从**层次感**、**字重**、**流式动画**"
         "和**数字对齐**四个方面进行了优化，让整体观感更接近大厂客户端的精致质感。\n\n"
         "具体改动包括：\n\n1. 去除了多余的描边，改用微对比的色阶分层；\n"
         "2. 暗色模式下字重统一减细一档，避免发糊。\n"
         "3. 流式输出加入水墨晕染渐显，落字更柔和。")

for n in (4, 40, 100, 160):
    txt = FINAL[:n]
    htm = ap._md_to_html(txt)
    blocks = [(cb.KIND_STREAM, {"html": htm}, ("f", n))]
    turn.render(blocks, live=True, done=False)
    app.processEvents()
    turn.relayout_heights(turn.width())
    app.processEvents()
    print(f"--- n={n} items={len(turn._items)}")
    for r in turn._items:
        w = r.widget
        print("    ref kind=", r.kind, "widget=", type(w).__name__ if w is not None else None)
        em = getattr(w, "_emerge", None) if w is not None else None
        if em is not None:
            print(f"    emerge_visible={em.isVisible()} live={em._live} "
                  f"hist={len(em._hist)} timerActive={em._timer.isActive()} h={em._h}")
