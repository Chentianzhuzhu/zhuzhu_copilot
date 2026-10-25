# -*- coding: utf-8 -*-
"""本地模拟流式（无需 API / 不发请求）：直接构造 ChatTurn 并逐段增长内容，
用来验证招 3「落字浮现 / 水墨晕染」与招 1/2/4 在气泡上的实际效果。

运行：用系统 Python 3.13
  python sim_stream.py
窗口会自动按时间线推进，每 110ms 增长一点，并在关键帧截图到 stream_sim/。
"""
import sys
import os
import html as _html

SRC = r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\src"
sys.path.insert(0, SRC)
os.chdir(SRC)

from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout, QScrollArea
from PyQt6.QtCore import QTimer, Qt
from zhuzhu_Copilot.ui import agent_panel as ap
from zhuzhu_Copilot.ui import agent_chat_bubbles as cb

OUT = r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\tmp_uiux\stream_sim"
os.makedirs(OUT, exist_ok=True)

app = QApplication(sys.argv)


def make_style():
    """与 AgentPanel._chat_style() 同源（模块级常量 + _mix_hex 派生）。"""
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
        w_title=ap._W_TITLE, w_strong=ap._W_STRONG,
        font_numeric=ap.NUMERIC_FONT_STACK,
    )


def icon_provider(kind, size, color):
    return ap._line_icon(kind, size, color)


# ---------- 窗口 ----------
scroll = QScrollArea()
scroll.setWindowTitle("stream sim")
scroll.setStyleSheet(f"QScrollArea {{ background: {ap.BG}; border: none; }}")
host = QWidget()
host.setStyleSheet(f"background: {ap.BG};")
hl = QVBoxLayout(host)
hl.setContentsMargins(24, 20, 24, 20)
turn = cb.ChatTurn(make_style(), icon_provider)
hl.addWidget(turn)
scroll.setWidget(host)
scroll.setWidgetResizable(True)
scroll.resize(780, 740)
scroll.show()

# ---------- 模拟内容 ----------
THINK = ("我需要先理解用户的需求，然后规划一个清晰的实现步骤。首先检查项目的整体结构，"
         "确认相关模块的位置；接着定位到具体的代码文件，逐步分析现有逻辑；"
         "最后给出修改方案并验证效果。这个过程中要注意保持代码风格的一致性，"
         "避免引入新的问题，同时还要兼顾暗色模式下的可读性。")

FINAL = ("好的，我已经完成了对界面的美化。本次主要从**层次感**、**字重**、**流式动画**"
         "和**数字对齐**四个方面进行了优化，让整体观感更接近大厂客户端的精致质感。\n\n"
         "具体改动包括：\n\n1. 去除了多余的描边，改用微对比的色阶分层；\n"
         "2. 暗色模式下字重统一减细一档，避免发糊；\n"
         "3. 流式输出加入水墨晕染渐显，落字更柔和；\n"
         "4. 实时跳动的数字锁定等宽字体，不再左右抖动。\n\n"
         "如果你还有其他想调整的地方，随时告诉我。")

state = {"think": "", "tool": False, "cmd": False, "final": "", "done": False}
shot = {"n": 0}


def shot_png(tag):
    n = shot["n"]
    app.processEvents()
    # 滚动到底部，让最新内容可见
    bar = scroll.verticalScrollBar()
    bar.setValue(bar.maximum())
    app.processEvents()
    scroll.grab().save(os.path.join(OUT, f"{n:03d}_{tag}.png"))
    shot["n"] += 1


def build_blocks():
    # sig 必须随内容变化（真实 _seg_sig 含长度 + 尾部），否则复用逻辑判定为
    # 「内容未变」而跳过更新。
    blocks = []
    if state["think"]:
        body = _html.escape(state["think"]).replace("\n", "<br>")
        blocks.append((cb.KIND_THINK,
                       {"tag": "规划中", "body": body, "sid": "t1"},
                       ("think", len(state["think"]))))
    if state["tool"]:
        blocks.append((cb.KIND_TOOL,
                       {"name": "读取文件", "meta": "ui/agent_panel.py",
                        "params": {"path": "src/zhuzhu_Copilot/ui/agent_panel.py"},
                        "ico": "read_file", "tip": "读取主面板源码"},
                       ("tool",)))
    if state["cmd"]:
        blocks.append((cb.KIND_CMD,
                       {"label": "运行应用",
                        "cmd": "<span style='color:%s'>python main.py</span>" % ap.TEXT,
                        "out": "<span style='color:%s'>启动成功，窗口已显示</span>"
                               % ap.LINK_COLOR},
                       ("cmd",)))
    if state["final"]:
        try:
            body_html = ap._md_to_html(state["final"])
        except Exception:
            body_html = _html.escape(state["final"]).replace("\n", "<br>")
        blocks.append((cb.KIND_STREAM, {"html": body_html},
                       ("final", len(state["final"]))))
    return blocks


def render(live):
    turn._style = make_style()
    turn.render(build_blocks(), live=live, done=state["done"])
    # 高度由面板在宽度确定后重算；这里手动触发一次
    w = turn.width()
    if w > 0:
        turn.relayout_heights(w)
    host.adjustSize()


# 时间线：思考逐字 → 工具 → 命令 → 正文逐字 → 收敛
THINK_STEP = 5
FINAL_STEP = 4


def tick():
    # 阶段 A：思考增长
    if len(state["think"]) < len(THINK):
        i = min(len(state["think"]) + THINK_STEP, len(THINK))
        state["think"] = THINK[:i]
        render(True)
        if shot["n"] % 4 == 0:
            shot_png("think")
        return
    # 阶段 B：工具
    if not state["tool"]:
        state["tool"] = True
        render(True)
        shot_png("tool")
        return
    # 阶段 C：命令
    if not state["cmd"]:
        state["cmd"] = True
        render(True)
        shot_png("cmd")
        return
    # 阶段 D：正文增长
    if len(state["final"]) < len(FINAL):
        i = min(len(state["final"]) + FINAL_STEP, len(FINAL))
        state["final"] = FINAL[:i]
        render(True)
        # 每 7 帧截一张，抓落字过程中的水墨晕染
        state["ftick"] = state.get("ftick", 0) + 1
        if state["ftick"] in (3, 10, 20, 30):
            shot_png(f"final{state['ftick']}")
        return
    # 阶段 E：收敛
    if not state["done"]:
        state["done"] = True
        render(False)
        shot_png("done")
        timer.stop()
        print("done; shots in", OUT)
        # 收敛后多截一帧（等落字浮现动画走完），再自动退出
        QTimer.singleShot(1600, lambda: (shot_png("settled"), app.quit()))


timer = QTimer()
timer.setInterval(110)
timer.timeout.connect(tick)
timer.start()

# 先给一帧初始布局
app.processEvents()
render(True)

sys.exit(app.exec())
