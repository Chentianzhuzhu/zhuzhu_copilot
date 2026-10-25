# -*- coding: utf-8 -*-
"""计时行重构 · 真实平台终验：屏幕抓图 + 三段式结构像素断言 + 截图。"""
import sys

sys.path.insert(0, "src")

from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout
from PyQt6.QtGui import QIcon

app = QApplication(sys.argv)
from zhuzhu_Copilot.ui import agent_panel as ap
from zhuzhu_Copilot.ui import agent_chat_bubbles as cb

FAIL = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        FAIL.append(name)


def make_style():
    return cb.ChatStyle(
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


def shot(theme):
    ap._apply_colors(ap._THEMES[theme], force=True)
    style = make_style()
    icons = lambda n, s, c: QIcon()
    w = QWidget()
    w.setStyleSheet(f"background: {ap.BG};")
    lay = QVBoxLayout(w)
    lay.setContentsMargins(20, 20, 20, 20)
    lay.setSpacing(14)
    turns = []
    for cost, want in ((3725, "已处理 1小时2分钟5秒"),
                       (1425, "已处理 23分钟45秒"),
                       (42, "已处理 42秒"),
                       (None, "已处理 --")):
        turn = cb.ChatTurn(style, icons)
        turn.render([(cb.KIND_STREAM,
                      {"html": "<div>这是 agent 的输出内容，位于虚线下方。</div>"},
                      ("s", 0))],
                    cost=cost, live=False, done=True)
        turn.setMaximumWidth(560)
        lay.addWidget(turn)
        turns.append((turn, want))
    w.resize(620, 520)
    w.show()
    for _ in range(3):
        app.processEvents()
    screen = app.primaryScreen()
    pm = screen.grabWindow(w.winId())
    out = f"tmp_uiux/final_timer_{theme}.png"
    pm.save(out)
    print("saved", out)
    im = pm.toImage()
    bg_names = ("#101216", "#0b0d11") if theme == "dark" else ("#f7f8fb", "#edf0f5")

    def row_has(y):
        return any(im.pixelColor(x, y).name() not in bg_names for x in range(20, 600))

    for i, (turn, want) in enumerate(turns):
        top = turn.geometry().y()
        inset = turn._inset
        # 1) 计时行文本行（top..top+TIMER_ROW_H）有内容
        t_rows = [y for y in range(top, top + cb.TIMER_ROW_H) if row_has(y)]
        check(f"[{theme}] turn{i} 计时行有文本", bool(t_rows))
        # 2) 虚线行（top+inset）有内容且是「多段短线」（≥20 个非bg run）
        y = top + inset
        hits = [x for x in range(20, 600) if im.pixelColor(x, y).name() not in bg_names]
        runs = 0
        if hits:
            runs = 1 + sum(1 for a, b in zip(hits, hits[1:]) if b > a + 1)
        check(f"[{theme}] turn{i} 虚线完整可见（{len(hits)}px/{runs}段）",
              len(hits) >= 100 and runs >= 20)
        # 3) 虚线之下有正文内容
        body_rows = [yy for yy in range(y + 4, y + 40) if row_has(yy)]
        check(f"[{theme}] turn{i} 虚线下方有正文", bool(body_rows))
        # 4) 计时行在虚线之上
        check(f"[{theme}] turn{i} 计时行在虚线上方", t_rows and max(t_rows) < y)
        # 5) 文案
        check(f"[{theme}] turn{i} 文案「{want}」", turn._ribbon._text.text() == want)
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
