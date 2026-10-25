# -*- coding: utf-8 -*-
"""UI/UX 五招落地离屏冒烟：构造关键控件 + 深浅双主题渲染截图。"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout,
                             QLabel, QLineEdit, QPushButton, QListWidget)
from PyQt6.QtGui import QPixmap, QPainter
from PyQt6.QtCore import Qt

app = QApplication(sys.argv)

from zhuzhu_Copilot.ui import styles
from zhuzhu_Copilot.ui import agent_panel as ap
from zhuzhu_Copilot.ui import agent_chat_bubbles as cb
from zhuzhu_Copilot.ui import motion
from zhuzhu_Copilot.ui.widgets import SwitchButton, ArrowComboBox

FAIL = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        FAIL.append(name)


# ---------- 招 1：全局 QSS 无输入描边、层级三档 ----------
import re as _re
for theme in ("dark", "light"):
    styles.set_palette(theme)
    qss = styles.GLOBAL_QSS
    check(f"[{theme}] 全表零 border: 1px", not _re.findall(r"border: 1px", qss))
    check(f"[{theme}] QMenu 用 elevated 且无描边", styles.PALETTE["elevated"] in qss)
    # 层级亮度差：技能口径「5~12 个 8bit 亮度值」（≈2%~4.7%）
    def lum(h):
        c = h.lstrip("#")
        return (int(c[0:2], 16) + int(c[2:4], 16) + int(c[4:6], 16)) / 3.0
    steps = []
    keys = ["bg_top", "card", "elevated"] if theme == "dark" else ["bg_top", "card"]
    for a, b in zip(keys, keys[1:]):
        d = lum(styles.PALETTE[b]) - lum(styles.PALETTE[a])
        steps.append(round(d, 1))
        check(f"[{theme}] {a}->{b} 8bit 步进 {d:.1f} 在 5~12", 5 <= d <= 12)
    print(f"  [{theme}] 层级步进(8bit): {steps}")

# ---------- 招 2：字重随主题降档 ----------
styles.set_palette("dark")
check("暗色 title 字重 600", "font-weight: 600" in styles.GLOBAL_QSS.split("QLabel#title")[1][:120])
styles.set_palette("light")
check("浅色 title 字重 700", "font-weight: 700" in styles.GLOBAL_QSS.split("QLabel#title")[1][:120])

# ---------- 招 4：等宽数字栈 ----------
check("numeric_font_css 含 Segoe UI", "Segoe UI" in styles.numeric_font_css())

# ---------- 招 5：motion 注册表 ----------
check("motion.snap 是 OutBack", motion.snap().type() == motion.QEasingCurve.Type.OutBack)
check("motion.hover 110ms", motion.HOVER_MS == 110)

# ---------- agent_panel 派生常量（双主题） ----------
for theme in ("dark", "light"):
    ap._apply_colors(ap._THEMES[theme], force=True)
    check(f"[{theme}] _BTN_GHOST 无描边", "border: 1px" not in ap._BTN_GHOST)
    check(f"[{theme}] _QCOMBO 无描边", "border: 1px" not in ap._QCOMBO)
    check(f"[{theme}] _input_qss 非壁纸无描边", "border: 1px" not in ap._input_qss())
    check(f"[{theme}] _global_dialog_qss 按钮无描边",
          "border: 1px" not in _re.findall(r"QDialogButtonBox QPushButton \{[^}]*\}", ap._global_dialog_qss())[0])

# ---------- ChatStyle 快照含字重与数字栈 ----------
st = cb.ChatStyle(card="#1F232C", border="#272C36", border_soft="#333A46",
                  dash="#333A46", text="#F3F5F9", text_dim="#9BA3B0",
                  accent="#2F52D8", muted="#9BA3B0", icon_shell="#272C36",
                  icon_color="#9BA3B0", tag_bg="#333A46", tag_fg="#9BA3B0",
                  user_bg="#2F52D8", user_fg="#FFFFFF", cmd_fg="#F3F5F9",
                  ok_fg="#5B82F6", hover="#272C36", panel="#181B21", bg="#101216")
check("ChatStyle.w_strong 默认 600", st.w_strong == 600)
check("ChatStyle.font_numeric 非空", "Segoe UI" in st.font_numeric)

# ---------- 控件构造 + 截图 ----------
def shot(theme):
    styles.set_palette(theme)
    ap._apply_colors(ap._THEMES[theme], force=True)
    app.setStyleSheet(styles.GLOBAL_QSS)
    w = QWidget()
    w.setObjectName("shotRoot")
    lay = QVBoxLayout(w)
    lay.setSpacing(10)
    lay.setContentsMargins(16, 16, 16, 16)
    # 标题 + 输入 + 按钮排
    t = QLabel("AI AGENT")
    t.setStyleSheet(f"color: {styles.PALETTE['primary_hover']}; font-size: 16px;"
                    f" font-weight: {styles.FONT_WEIGHT['dark' if theme=='dark' else 'light']['title']};")
    lay.addWidget(t)
    le = QLineEdit("聚焦我看看凹陷加深")
    lay.addWidget(le)
    row = QHBoxLayout()
    b1 = QPushButton("主按钮")
    b2 = QPushButton("次按钮")
    b2.setObjectName("secondary")
    sw = SwitchButton(True)
    cm = ArrowComboBox()
    cm.addItems(["会话甲", "会话乙"])
    for x in (b1, b2, sw, cm):
        row.addWidget(x)
    lay.addLayout(row)
    lw = QListWidget()
    lw.addItems("列表项一 列表项二".split())
    lay.addWidget(lw)
    # 聊天气泡：CostRibbon + ThinkBubble + CmdBlock
    sty = cb.ChatStyle(
        card=ap.AI_BG, border=ap.BORDER, border_soft=ap.BORDER_SOFT,
        dash=ap.BORDER_SOFT, text=ap.TEXT, text_dim=ap.TEXT_DIM,
        accent=ap.ACCENT, muted=ap.TEXT_DIM, icon_shell=ap.HOVER,
        icon_color=ap.ICON_GRAY, tag_bg=ap._mix_hex(ap.BORDER_SOFT, ap.AI_BG, 0.55),
        tag_fg=ap.TEXT_DIM, user_bg=ap.USER_BG, user_fg="#FFFFFF",
        cmd_fg=ap.TEXT, ok_fg=ap.LINK_COLOR, hover=ap.HOVER, panel=ap.PANEL,
        bg=ap.BG, think_bg=ap._mix_hex(ap.ACCENT, ap.AI_BG, 0.10),
        think_border=ap._mix_hex(ap.ACCENT, ap.BORDER, 0.30),
        tag_plan_bg=ap._mix_hex(ap.BORDER_SOFT, ap.AI_BG, 0.55),
        tag_exec_bg=ap._mix_hex(ap.ACCENT, ap.AI_BG, 0.32),
        tool_shell=ap._mix_hex(ap.BORDER_SOFT, ap.HOVER, 0.22),
        band=ap.BG, out_fg=ap.LINK_COLOR, out_line=ap.ACCENT,
        dot=ap._base_color("BORDER"),
        w_title=ap._W_TITLE, w_strong=ap._W_STRONG,
        font_numeric=styles.numeric_font_css().replace("font-family: ", "").rstrip(";"),
    )
    rib = cb.CostRibbon(sty, None)
    rib.start()
    lay.addWidget(rib)
    from PyQt6.QtGui import QIcon
    _icons = lambda name, size, color: QIcon()
    tb = cb.ThinkBubble(sty, _icons)
    tb.set_content("PLANNING", "这是一个思考过程气泡：深蓝淡染底、无描边，"
                               "与正文面底靠色阶+色相拉开层次。")
    lay.addWidget(tb)
    w.setStyleSheet(f"QWidget#shotRoot {{ background: {styles.PALETTE['bg_top']}; }}")
    w.resize(560, 460)
    w.show()
    app.processEvents()
    pm = QPixmap(w.size())
    w.render(pm)
    out = os.path.join(os.path.dirname(__file__), f"shot_{theme}.png")
    pm.save(out)
    print("saved", out)
    w.close()


for th in ("dark", "light"):
    try:
        shot(th)
    except Exception as e:
        import traceback
        traceback.print_exc()
        FAIL.append(f"shot-{th}")

print()
print("FAILURES:", FAIL if FAIL else "none")
sys.exit(1 if FAIL else 0)
