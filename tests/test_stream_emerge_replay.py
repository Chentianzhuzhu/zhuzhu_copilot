# -*- coding: utf-8 -*-
"""流式输出「动画重复播放 / 气泡留大片空白」回归。

用户反馈两条：
  1. 思考过程 / 正文 / 工具调用 / 执行命令的浮现动画会重复播放若干遍；
  2. 思考过程较长时，气泡外侧上下出现大片空白。

根因（实测，见 git 历史）：
  · 宽度变化（面板缩放、滚动条出现/消失导致换行重算）与内容收缩（markdown 半解析）
    都会作废行龄历史；旧实现随后用「0 基线」重新起波 → 整块可见内容又被当成新落下的字
    重播一遍，宽度抖动几次就重播几次。
  · 回合/区块高度的「只增不减」棘轮没有任何放行条件：流式期间任何一次偏大的测量
    （半解析、工具块被移除）都会被永久钉住，多余空间被布局摊进区块里 → 气泡上下留白。

守护契约：
  A. 宽度变化不得重新起波；其后新内容产生的波必须从「已落定高度」起（不是 0）；
  B. 内容收缩后同样以当前高度为落定基线；
  C. 真实收缩（超过 HEIGHT_SHRINK_TOL）必须放行，回合高度要能变小；
  D. 同一段思考持续落字时，用户手动展开的折叠态不得被下一个 tick 复位。
"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PyQt6.QtCore import QRect                                     # noqa: E402
from PyQt6.QtGui import QColor, QIcon, QPixmap                     # noqa: E402
from PyQt6.QtTest import QTest                                     # noqa: E402
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget     # noqa: E402

from zhuzhu_Copilot.ui import agent_chat_bubbles as cb             # noqa: E402

app = QApplication.instance() or QApplication([])

STYLE = cb.ChatStyle(
    card="#1F232C", border="#272C36", border_soft="#333A46", dash="#333A46",
    text="#F3F5F9", text_dim="#9BA3B0", accent="#2F52D8", muted="#9BA3B0",
    icon_shell="#272C36", icon_color="#9BA3B0", tag_bg="#2A3040", tag_fg="#9BA3B0",
    user_bg="#2F52D8", user_fg="#FFFFFF", cmd_fg="#F3F5F9", ok_fg="#5B82F6",
    hover="#272C36", panel="#181B21", bg="#101216",
    tag_exec_bg="#243157", tool_shell="#22304F", out_fg="#5B82F6", out_line="#2F52D8")

BASE = "第一段正文，用于建立已落定的基线内容。" * 4
GROW = "新增的一段正文，只有它应该进入浮现。" * 3


def _icon(kind: str, size: int, color: str) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(QColor(color))
    return QIcon(pm)


def _html(text: str) -> str:
    return f'<div style="color:#F3F5F9;font-size:14px;">{text}</div>'


def _host(widget: QWidget, width: int = 760, height: int = 420) -> QWidget:
    """真实布局宿主：区块必须拿到真实宽度，浮现层/高度测量才有意义。"""
    win = QWidget()
    lay = QVBoxLayout(win)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(widget)
    lay.addStretch(1)
    win.resize(width, height)
    win.show()
    for _ in range(4):
        app.processEvents()
    return win


def _settle(block) -> None:
    """让首波浮现自然收敛，并把本层复位到「已落定」状态。"""
    QTest.qWait(cb.EMERGE_MS + 150)
    band = block._emerge
    band._timer.stop()
    band.hide()


# ---------- A. 宽度变化不得重播整块浮现 ----------
def test_width_change_does_not_replay_whole_block_emerge():
    blk = cb.StreamBlock(STYLE)
    win = _host(blk)
    try:
        blk.set_live(True)
        blk.set_html(_html(BASE))
        _settle(blk)
        band = blk._emerge
        band._hist = [(time.perf_counter() - cb.EMERGE_MS / 1000.0, band._h)]

        win.resize(win.width() - 40, win.height())      # 换行重算 = 宽度变化
        for _ in range(6):
            app.processEvents()

        assert band._hist == [], "宽度变化应作废行龄历史（换行后「第几行」已不是同一批字）"
        assert not band._timer.isActive(), \
            "宽度变化本身不得重新起波 —— 否则每次缩放/滚动条出现都会整块重播动画"
        assert band._settled_h > 0, "宽度变化时当前可见内容必须记为「已落定」"

        blk.set_html(_html(BASE + GROW))                # 新内容到达
        assert band._hist, "新内容应起一波浮现"
        assert band._hist[0][1] == band._settled_h > 0, \
            "新波必须从「已落定高度」起；基线为 0 就是整块内容重播（本次修复的缺陷）"
    finally:
        win.hide()
        win.deleteLater()
        app.processEvents()


# ---------- B. 内容收缩后同样以当前高度为落定基线 ----------
def test_content_shrink_sets_settle_baseline():
    blk = cb.StreamBlock(STYLE)
    win = _host(blk)
    try:
        blk.set_live(True)
        blk.set_html(_html(BASE))
        _settle(blk)
        band = blk._emerge
        real = band._area
        shrunk = max(1, int(real().height()) - 30)
        band._area = lambda: QRect(real().x(), real().y(), real().width(), shrunk)
        blk._emerge_touch()                             # 内容 touch → 走收缩分支
        assert band._settled_h == shrunk, "内容收缩后应把收缩后的高度记为落定基线"
        assert band._hist and band._hist[0][1] == shrunk, \
            "收缩后起波必须从收缩后的高度算起（基线 0 = 整块内容重播）"
        band._area = real
        blk.set_html(_html(BASE + GROW))
        assert band._hist[0][1] == band._settled_h > 0, "收缩后的新内容同样不得重播整块"
    finally:
        win.hide()
        win.deleteLater()
        app.processEvents()


# ---------- C. 高度棘轮必须放行真实收缩 ----------
def test_turn_height_releases_on_real_shrink():
    turn = cb.ChatTurn(STYLE, _icon)
    win = _host(turn)
    try:
        keep = (cb.KIND_STREAM, {"html": _html(BASE)}, ("s", 1))
        extra = [(cb.KIND_TOOL, {"name": "read_file", "meta": "", "params": {},
                                 "ico": None, "out": ""}, ("t", 2)),
                 (cb.KIND_CMD, {"label": "run_command", "cmd": "dir", "out": ""}, ("c", 3))]
        turn.render([keep] + extra, live=True, done=False)
        for _ in range(6):
            app.processEvents()
        big = turn.minimumHeight()
        assert big > 0

        turn.render([keep], live=True, done=False)      # 工具/命令块整体消失 = 真实收缩
        for _ in range(6):
            app.processEvents()
        small = turn.minimumHeight()
        assert big - small > cb.HEIGHT_SHRINK_TOL, (
            f"回合高度只从 {big} 缩到 {small}：真实收缩被「只增不减」棘轮锁死，"
            f"多余高度会摊进区块里变成气泡上下的大片空白")
    finally:
        win.hide()
        win.deleteLater()
        app.processEvents()


def test_small_jitter_still_clamped():
    """反向守护：小幅抖动（markdown 半解析）仍按「只增不减」处理，避免几何抖动。"""
    turn = cb.ChatTurn(STYLE, _icon)
    win = _host(turn)
    try:
        block = (cb.KIND_STREAM, {"html": _html(BASE)}, ("s", 1))
        turn.render([block], live=True, done=False)
        for _ in range(6):
            app.processEvents()
        base = turn.minimumHeight()
        turn.relayout_heights(turn.width(), monotonic=True, dirty=[])
        assert turn.minimumHeight() == base, "无内容变化时高度不得漂移"
    finally:
        win.hide()
        win.deleteLater()
        app.processEvents()


# ---------- D. 思考气泡的手动折叠态不被流式刷新复位 ----------
def _long_body(prefix: str = "") -> str:
    return prefix + "正在逐项核对上下文与候选方案，权衡利弊后再决定下一步动作。" * 40


def test_think_fold_state_survives_streaming_growth():
    think = cb.ThinkBubble(STYLE, _icon)
    win = _host(think)
    try:
        body = _long_body()
        think.set_live(True)
        think.set_content("PLANNING", _html(body), sid=0)
        assert think._fold_foldable(), "测试文本必须长到可折叠，否则本用例无意义"

        think._fold_toggle()                             # 用户点「继续查看」
        assert think._fold_open is True and think._fold_btn.text() == "收起"

        think.set_content("PLANNING", _html(body + "继续思考中。"), sid=0)   # 同一段继续落字
        assert think._fold_open is True, \
            "同一段思考继续落字时，用户的手动展开被下一个 tick 复位了（表现为状态反复回放）"
        assert think._fold_btn.text() == "收起", "折叠按钮文案被流式刷新重置"

        think.set_content("EXEC", _html(_long_body("另一段完全不同的思考：")), sid=7)
        assert think._fold_open is None, "换成另一段思考后应回到自动折叠判定"
    finally:
        win.hide()
        win.deleteLater()
        app.processEvents()
