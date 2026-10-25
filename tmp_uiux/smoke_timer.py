# -*- coding: utf-8 -*-
"""计时行重构离屏验证：渲染完整 ChatTurn，检查新三段式结构与等宽数字。"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout
from PyQt6.QtGui import QPixmap, QIcon

app = QApplication(sys.argv)

from zhuzhu_Copilot.ui import agent_panel as ap
from zhuzhu_Copilot.ui import agent_chat_bubbles as cb

FAIL = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        FAIL.append(name)


# ---------- fmt_elapsed 全规则 ----------
f = cb.fmt_elapsed
cases = [
    (0, "已处理 0秒"), (9.7, "已处理 9秒"), (59, "已处理 59秒"),
    (60, "已处理 1分钟0秒"), (23 * 60 + 45, "已处理 23分钟45秒"),
    (3599, "已处理 59分钟59秒"), (3600, "已处理 1小时0分钟0秒"),
    (3600 + 23 * 60 + 45, "已处理 1小时23分钟45秒"),
    (7325, "已处理 2小时2分钟5秒"), (-3, "已处理 0秒"),
]
for secs, want in cases:
    check(f"fmt_elapsed({secs}) == {want}", f(secs) == want)

# ---------- 计时行无外壳 ----------
ap._apply_colors(ap._THEMES["dark"], force=True)
sty = cb.ChatStyle(
    card=ap.AI_BG, border=ap.BORDER, border_soft=ap.BORDER_SOFT,
    dash=ap.BORDER_SOFT, text=ap.TEXT, text_dim=ap.TEXT_DIM,
    accent=ap.ACCENT, muted=ap.TEXT_DIM, icon_shell=ap.HOVER,
    icon_color=ap.ICON_GRAY, tag_bg=ap._mix_hex(ap.BORDER_SOFT, ap.AI_BG, 0.55),
    tag_fg=ap.TEXT_DIM, user_bg=ap.USER_BG, user_fg="#FFFFFF",
    cmd_fg=ap.TEXT, ok_fg=ap.LINK_COLOR, hover=ap.HOVER, panel=ap.PANEL,
    bg=ap.BG, think_bg=ap._mix_hex(ap.ACCENT, ap.AI_BG, 0.10),
    think_border=ap._mix_hex(ap.ACCENT, ap.BORDER, 0.30),
    band=ap.BG, out_fg=ap.LINK_COLOR, out_line=ap.ACCENT,
    dot=ap._base_color("BORDER"),
    w_title=ap._W_TITLE, w_strong=ap._W_STRONG, w_emphasis=ap._W_EMPHASIS,
    font_numeric='"Segoe UI", "Microsoft YaHei UI", "Cascadia Mono", Consolas, monospace',
)
rb = cb.CostRibbon(sty)
rb.freeze(3725)
check("外壳零样式表", rb.styleSheet() == "")
check("文本无描边", "border: none" in rb._text.styleSheet())
check("等宽字体栈", sty.font_numeric in rb._text.styleSheet())
check("次要色", sty.text_dim in rb._text.styleSheet())
check("1小时2分钟5秒", rb._text.text() == "已处理 1小时2分钟5秒")
check("行高 TIMER_ROW_H", rb.height() == cb.TIMER_ROW_H)

# ---------- 完整回合结构截图 ----------
def shot(theme):
    ap._apply_colors(ap._THEMES[theme], force=True)
    style = cb.ChatStyle(
        card=ap.AI_BG, border=ap.BORDER, border_soft=ap.BORDER_SOFT,
        dash=ap.BORDER_SOFT, text=ap.TEXT, text_dim=ap.TEXT_DIM,
        accent=ap.ACCENT, muted=ap.TEXT_DIM, icon_shell=ap.HOVER,
        icon_color=ap.ICON_GRAY, tag_bg=ap._mix_hex(ap.BORDER_SOFT, ap.AI_BG, 0.55),
        tag_fg=ap.TEXT_DIM, user_bg=ap.USER_BG, user_fg="#FFFFFF",
        cmd_fg=ap.TEXT, ok_fg=ap.LINK_COLOR, hover=ap.HOVER, panel=ap.PANEL,
        bg=ap.BG, think_bg=ap._mix_hex(ap.ACCENT, ap.AI_BG, 0.10),
        think_border=ap._mix_hex(ap.ACCENT, ap.BORDER, 0.30),
        band=ap.BG, out_fg=ap.LINK_COLOR, out_line=ap.ACCENT,
        dot=ap._base_color("BORDER"),
        w_title=ap._W_TITLE, w_strong=ap._W_STRONG, w_emphasis=ap._W_EMPHASIS,
        font_numeric='"Segoe UI", "Microsoft YaHei UI", "Cascadia Mono", Consolas, monospace',
    )
    icons = lambda name, size, color: QIcon()
    w = QWidget()
    w.setStyleSheet(f"background: {ap.BG};")
    lay = QVBoxLayout(w)
    lay.setContentsMargins(20, 20, 20, 20)
    lay.setSpacing(16)
    for cost, label in ((3725, "1小时2分钟5秒"), (1425, "23分钟45秒"), (42, "42秒"), (None, "占位")):
        turn = cb.ChatTurn(style, icons)
        blocks = [
            (cb.KIND_STREAM, {"html": "<div>这是 agent 的输出内容："
                                      "计时行在其上方，虚线分隔，本段在虚线之下。</div>"},
             ("s", 0)),
        ]
        turn.render(blocks, cost=cost, live=False, done=True)
        turn.setMaximumWidth(560)
        lay.addWidget(turn)
    w.resize(620, 560)
    w.show()
    app.processEvents()
    pm = QPixmap(w.size())
    w.render(pm)
    out = os.path.join(os.path.dirname(__file__), f"timer_{theme}.png")
    pm.save(out)
    print("saved", out)
    # 结构断言：第一条回合
    turn0 = lay.itemAt(0).widget()
    rb0 = turn0._ribbon
    check(f"[{theme}] 计时行 y=0 且常驻", rb0.y() == 0 and not rb0.isHidden())
    check(f"[{theme}] 计时行 x=LEFT_GUTTER", rb0.x() == cb.LEFT_GUTTER)
    check(f"[{theme}] 虚线在计时行下", turn0._inset == cb.TIMER_ROW_H + cb.TIMER_DASH_GAP)
    check(f"[{theme}] 内容让位", turn0._box_lay.contentsMargins().top() >= cb.TIMER_ROW_H)
    check(f"[{theme}] 计时文案", rb0._text.text() == "已处理 1小时2分钟5秒")
    w.close()


for th in ("dark", "light"):
    try:
        shot(th)
    except Exception:
        import traceback
        traceback.print_exc()
        FAIL.append(f"shot-{th}")

print()
print("FAILURES:", FAIL if FAIL else "none")
sys.exit(1 if FAIL else 0)
