# -*- coding: utf-8 -*-
"""最小诊断：StreamBlock 正文逐段增长时，高度是否正确跟随。"""
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
         "2. 暗色模式下字重统一减细一档，避免发糊；\n"
         "3. 流式输出加入水墨晕染渐显，落字更柔和；\n"
         "4. 实时跳动的数字锁定等宽字体，不再左右抖动。")

for n in (4, 20, 60, 120, len(FINAL)):
    txt = FINAL[:n]
    htm = ap._md_to_html(txt)
    blocks = [(cb.KIND_STREAM, {"html": htm}, "f1")]
    turn.render(blocks, live=(n < len(FINAL)), done=False)
    app.processEvents()
    turn.relayout_heights(turn.width())
    app.processEvents()
    # 找正文块
    refs = [r for r in turn._items if r.widget is not None]
    info = []
    for r in refs:
        w = r.widget
        body = getattr(w, "_body", None)
        if body is not None:
            raw_hfw = body.heightForWidth(660)
            info.append(f"blk_w={w.width()} body_minH={body.minimumHeight()} "
                        f"body_h={body.height()} raw_hfw660={raw_hfw} "
                        f"textlen={len(body.text())} wrap={body.wordWrap()}")
    print(f"n={n} turn_w={turn.width()} turn_h={turn.height()} | " + " ; ".join(info))
