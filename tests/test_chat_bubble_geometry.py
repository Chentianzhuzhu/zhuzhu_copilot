# -*- coding: utf-8 -*-
"""聊天气泡几何回归：折叠态切换后的高度收缩 + 折叠渐隐遮罩的位置。

用户反馈的四类症状，本文件逐条钉死：

1. **折叠按钮被输出内容挤压 / 元素互相遮挡**
   块高度是外层布局分配的槽位。折叠开关一旦出现，块必须**重新分配**槽位把按钮完整
   装下；块高小于自身布局最小需求时，按钮被压扁、下一个块骑到本块上。

2. **折叠时黑色/白色阴影浮现在文字上方**
   渐隐遮罩必须贴在**折叠标签的底边**，压在正文中间就是错位。

3. **大片空白**
   折叠态切换后块高必须**真的收缩**回去。曾经 `_measure_block` 用
   `QWidget.minimumSizeHint()` 兜底，而它读 Qt 布局的内部缓存、且会把块自己被
   `setFixedHeight` 钉住的高度当成「最小需求」返回 —— 形成自我强化闭环：
   展开 1204px 后点「收起」，真实需求已回到 266px，块却仍占 1204px 且再也回不来。

4. **元素错位**
   块的实际高度必须等于它自己算出的槽位高度，否则内部子控件会溢出块边界。

判据口径（与生产实现同源，避免测试自己算出一套不同的数）：
  · 「块应有多高」= `ChatTurn._measure_block` 的口径（heightForWidth 与内层布局
    totalMinimumSize 的较大者）；
  · 「遮罩应贴哪」= 承载标签的底边；
  · 「按钮是否被挤压」= 按钮高度 vs `sizeHint().height()`。
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest                                                    # noqa: E402
from PyQt6.QtCore import QPoint, QRect, Qt                       # noqa: E402
from PyQt6.QtWidgets import (QApplication, QHBoxLayout, QLabel,   # noqa: E402
                             QScrollArea, QVBoxLayout, QWidget, QFrame)

from zhuzhu_Copilot.ui import agent_chat_bubbles as cb          # noqa: E402
from zhuzhu_Copilot.ui.agent_panel import _TurnWrap             # noqa: E402

app = QApplication.instance() or QApplication([])

STYLE = cb.ChatStyle(
    card="#1F232C", border="#272C36", border_soft="#333A46", dash="#333A46",
    text="#F3F5F9", text_dim="#9BA3B0", accent="#2F52D8", muted="#9BA3B0",
    icon_shell="#272C36", icon_color="#2F52D8", tag_bg="#2A3040", tag_fg="#9BA3B0",
    user_bg="#2F52D8", user_fg="#FFFFFF", cmd_fg="#F3F5F9", ok_fg="#5B82F6",
    hover="#272C36", panel="#181B21", bg="#101216",
    think_bg="#1b2130", think_border="#26314a", tag_plan_bg="#2A3040",
    tag_exec_bg="#22304e", tool_shell="#222838", band="#151a25",
    out_fg="#7f9cf0", out_line="#2F52D8", dot="#4a5163",
)

# 长输出必须远超折叠上限（OUT_FOLD_LINES），否则根本不会出现折叠开关
LONG_OUT = ("2026-10-06 12:00:00 INFO  worker heartbeat ok\n"
            "2026-10-06 12:00:01 INFO  task queued and dispatched\n" * 45)
LONG_THINK = "分析用户请求的可行方案，逐条列出取舍依据与实现成本，考虑边界情况。" * 70


def _icon(kind, size, color):
    from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(QColor(color), 1.6))
    path = QPainterPath()
    path.moveTo(size * .28, size * .5)
    path.lineTo(size * .5, size * .28)
    path.lineTo(size * .72, size * .5)
    path.lineTo(size * .5, size * .72)
    path.closeSubpath()
    p.drawPath(path)
    p.end()
    return QIcon(pm)


def _pump(n=6):
    for _ in range(n):
        app.processEvents()


class Host:
    """与 AgentPanel 消息区同构的宿主（QScrollArea + msg_lay + setMaximumWidth）。

    不同构的宿主不会激活布局，测出来的「重叠/越界」全是假象 —— 必须同构。
    """

    def __init__(self, width=760):
        self.root = QWidget()
        rl = QVBoxLayout(self.root)
        rl.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        cont = QWidget()
        self.lay = QVBoxLayout(cont)
        self.lay.setContentsMargins(6, 6, 6, 6)
        self.lay.addStretch(1)
        self.scroll.setWidget(cont)
        rl.addWidget(self.scroll)
        self.root.resize(width, 1400)
        self.root.show()
        _pump()
        self.wrap = None
        self.turn = None

    def max_w(self):
        m = self.lay.contentsMargins()
        return max(240, int(self.scroll.viewport().width())
                   - m.left() - m.right() - 8)

    def add_turn(self):
        wrap = _TurnWrap()
        v = QVBoxLayout(wrap)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(3)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        turn = cb.ChatTurn(STYLE, _icon)
        turn.setMaximumWidth(self.max_w())
        turn.relayout_heights(self.max_w())
        top.addWidget(turn, 1)
        v.addLayout(top)
        btm = QHBoxLayout()
        btm.addStretch(1)
        retry = QLabel("")
        retry.hide()
        btm.addWidget(retry, 0, Qt.AlignmentFlag.AlignVCenter)
        v.addLayout(btm)
        wrap.set_turn(turn)
        turn._wrap_sync = wrap.sync_height
        # 消息行插在末尾 stretch **之前**（与面板 `insertLayout(count-1, row)` 同义）
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(wrap, 1)
        self.lay.insertLayout(self.lay.count() - 1, row)
        _pump()
        self.wrap, self.turn = wrap, turn
        return turn, wrap

    def resize_to(self, width):
        self.root.resize(width, self.root.height())
        _pump(4)
        self.turn.setMaximumWidth(self.max_w())
        self.turn.relayout_heights(self.max_w())
        _pump(3)


def _specs(kind, payload, i=0):
    return [(kind, payload, i)]


def _blocks_of(turn, kind):
    for ref in turn._items:
        if ref.kind == kind and ref.widget is not None:
            return ref.widget
    return None


def _geom_in(wdg, child):
    return QRect(child.mapTo(wdg, QPoint(0, 0)), child.size())


# --------------------------------------------------------------------------
# 1. 折叠态往返：块高必须真的收缩回去（大片空白）
# --------------------------------------------------------------------------

@pytest.mark.parametrize("kind,payload", [
    (cb.KIND_TOOL, {"name": "run_command", "meta": "shell 2.4s",
                    "params": {"cmd": "npm test"}, "out": LONG_OUT}),
    (cb.KIND_CMD, {"label": "bash", "cmd": "npm test", "out": LONG_OUT}),
    (cb.KIND_THINK, {"tag": "PLANNING", "body": LONG_THINK, "sid": "s1"}),
])
def test_fold_round_trip_returns_to_folded_height(kind, payload):
    """展开 → 收起后，块高必须回到折叠态的真实高度。

    回归的根因：块高兜底曾用 `QWidget.minimumSizeHint()`，它读 Qt 布局内部缓存，
    并把块自己被 setFixedHeight 钉住的高度当成最小需求返回 —— 展开 1204px 后收起，
    `heightForWidth`/`totalMinimumSize` 都已回到折叠高度，块却仍被钉在 1204px，
    收起「看起来完全无效」，正文下方永久留一大片空白。
    """
    host = Host()
    turn, wrap = host.add_turn()
    turn.render(_specs(kind, payload), cost=1.0, live=False, done=False)
    _pump(10)
    blk = _blocks_of(turn, kind)
    assert blk is not None
    assert blk._fold_btn is not None and not blk._fold_btn.isHidden(), \
        "长输出必须出现折叠开关"

    folded_h = blk.height()
    turn_h_folded = turn.height()

    blk._fold_btn.click()          # 展开
    _pump(6)
    expanded_h = blk.height()
    assert expanded_h > folded_h, "点「展开全部」后块应变高"

    blk._fold_btn.click()          # 收起
    _pump(6)
    assert blk.height() == pytest.approx(folded_h, abs=3), \
        f"收起后块高应回到 {folded_h}，实际 {blk.height()}（折叠收不回去=大片空白）"
    assert blk.minimumHeight() == pytest.approx(folded_h, abs=3), \
        f"收起后钉住高度应回到 {folded_h}，实际 {blk.minimumHeight()}"
    assert turn.height() < turn_h_folded + expanded_h - folded_h + 6, \
        (f"回合高度必须跟着收缩：折叠 {turn_h_folded} / 展开 {turn.height()}")
    host.root.close()


@pytest.mark.parametrize("kind,payload", [
    (cb.KIND_TOOL, {"name": "run_command", "meta": "", "params": {},
                    "out": LONG_OUT}),
    (cb.KIND_CMD, {"label": "bash", "cmd": "x" * 400, "out": LONG_OUT}),
])
def test_repeated_fold_round_trips_are_stable(kind, payload):
    """反复展开/收起不得单向漂移（每次往返后高度都回到同一个折叠高度）。"""
    host = Host()
    turn, _ = host.add_turn()
    turn.render(_specs(kind, payload, 1), cost=1.0, live=False, done=False)
    _pump(10)
    blk = _blocks_of(turn, kind)
    base = blk.height()
    for i in range(4):
        blk._fold_btn.click()
        _pump(5)
        blk._fold_btn.click()
        _pump(5)
        assert blk.height() == pytest.approx(base, abs=3), \
            f"第 {i + 1} 次往返后高度漂移到 {blk.height()}（基准 {base}）"
    host.root.close()


# --------------------------------------------------------------------------
# 2. 折叠渐隐遮罩必须贴在折叠标签底边（阴影浮在文字上方）
# --------------------------------------------------------------------------

@pytest.mark.parametrize("kind,payload", [
    (cb.KIND_TOOL, {"name": "run_command", "meta": "shell", "params": {},
                    "out": LONG_OUT}),
    (cb.KIND_CMD, {"label": "bash", "cmd": "npm test", "out": LONG_OUT}),
    (cb.KIND_THINK, {"tag": "PLANNING", "body": LONG_THINK, "sid": "s1"}),
])
def test_fade_mask_sits_on_fold_label_bottom(kind, payload):
    """遮罩底边 == 折叠标签底边，且绝不越过标签顶。

    回归的根因：遮罩曾挂在**块**上、用 `lbl.mapTo(self, ...)` 跨层级换算坐标。承载
    标签嵌在多层嵌套布局里（工具行是 块 → `_body` → `_out_box` → 标签），而块的
    resizeEvent 与事件过滤器回调都早于父布局把标签挪到最终位置 —— 换算读到的是
    上一轮的父级几何。实测标签真实 y=63 却按 y=76 贴出（差 13px），渐隐带整体浮到
    正文中间；且后续回调读到的仍是同一个陈旧值，**永不自愈**。
    """
    host = Host()
    turn, _ = host.add_turn()
    turn.render(_specs(kind, payload, 2), cost=1.0, live=False, done=False)
    _pump(12)
    blk = _blocks_of(turn, kind)
    mask, lbl = blk._fold_mask, blk._fold_label()

    assert not mask.isHidden(), "折叠态必须显示渐隐遮罩"
    lg = _geom_in(blk, lbl)
    mg = _geom_in(blk, mask)
    assert mg.bottom() == lg.bottom(), \
        (f"遮罩底 {mg.bottom()} 应等于折叠标签底 {lg.bottom()}"
         f"（阴影浮在文字上方 {lg.bottom() - mg.bottom()}px）")
    assert mg.top() >= lg.top(), "遮罩不得越过标签顶"
    assert mg.width() == lbl.width(), "遮罩宽度应与标签一致"
    assert mask.height() == cb.FADE_H

    # 换宽度后必须重新归位（标签自身尺寸变化 → 事件过滤器重新贴）
    host.resize_to(560)
    lg = _geom_in(blk, lbl)
    mg = _geom_in(blk, blk._fold_mask)
    assert mg.bottom() == lg.bottom(), \
        f"换宽度后遮罩未归位：{mg.bottom()} vs {lg.bottom()}"
    host.root.close()


def test_fade_mask_follows_label_on_expand_and_refold():
    """展开（遮罩隐藏）→ 收起（遮罩重新贴回）后位置仍准确。"""
    host = Host()
    turn, _ = host.add_turn()
    turn.render(_specs(cb.KIND_TOOL, {"name": "run_command", "meta": "",
                                      "params": {}, "out": LONG_OUT}, 3),
                cost=1.0, live=False, done=False)
    _pump(12)
    blk = _blocks_of(turn, cb.KIND_TOOL)
    blk._fold_btn.click()          # 展开 → 遮罩应隐藏
    _pump(6)
    assert blk._fold_mask.isHidden(), "展开态不该有渐隐遮罩"
    blk._fold_btn.click()          # 收起 → 遮罩重新出现且位置准确
    _pump(6)
    lbl = blk._fold_label()
    lg = _geom_in(blk, lbl)
    mg = _geom_in(blk, blk._fold_mask)
    assert not blk._fold_mask.isHidden(), "收起后遮罩应重新出现"
    assert mg.bottom() == lg.bottom(), \
        f"收起后遮罩未归位：{mg.bottom()} vs {lg.bottom()}"
    host.root.close()


# --------------------------------------------------------------------------
# 3. 折叠按钮完整、块内子控件不越界（按钮被挤压 / 元素错位）
# --------------------------------------------------------------------------

@pytest.mark.parametrize("kind,payload", [
    (cb.KIND_TOOL, {"name": "run_command", "meta": "shell 2.4s",
                    "params": {"cmd": "npm test", "timeout": 120},
                     "out": LONG_OUT}),
    (cb.KIND_CMD, {"label": "bash", "cmd": "npm test", "out": LONG_OUT}),
    (cb.KIND_THINK, {"tag": "PLANNING", "body": LONG_THINK, "sid": "s1"}),
])
def test_fold_button_is_complete_inside_its_block(kind, payload):
    """折叠开关必须完整落在所属块内，且高度不小于 sizeHint（不被压扁）。"""
    host = Host()
    turn, _ = host.add_turn()
    turn.render(_specs(kind, payload, 4), cost=1.0, live=False, done=False)
    _pump(12)
    blk = _blocks_of(turn, kind)
    btn = blk._fold_btn
    assert not btn.isHidden()
    bg = _geom_in(blk, btn)
    assert bg.height() >= btn.sizeHint().height(), \
        f"折叠按钮被压扁 {bg.height()} < {btn.sizeHint().height()}"
    assert bg.bottom() <= blk.height(), \
        (f"折叠按钮底 {bg.bottom()} 超出块高 {blk.height()}"
         f"（按钮被输出内容挤压 {bg.bottom() - blk.height()}px）")
    host.root.close()


@pytest.mark.parametrize("kind,payload", [
    (cb.KIND_TOOL, {"name": "run_command", "meta": "shell 2.4s",
                    "params": {"cmd": "npm test", "timeout": 120},
                     "out": LONG_OUT}),
    (cb.KIND_CMD, {"label": "bash", "cmd": "npm test", "out": LONG_OUT}),
    (cb.KIND_THINK, {"tag": "PLANNING", "body": LONG_THINK, "sid": "s1"}),
    (cb.KIND_STREAM, {"html": "已完成上述改动。" * 30}),
])
def test_no_child_widget_escapes_its_block(kind, payload):
    """块内每个可见子控件都必须完整落在块矩形内（元素错位/越界的直接判据）。"""
    host = Host()
    turn, _ = host.add_turn()
    turn.render(_specs(kind, payload, 5), cost=1.0, live=False, done=False)
    _pump(12)
    blk = _blocks_of(turn, kind)
    br = QRect(0, 0, blk.width(), blk.height())
    bad = []
    for ch in blk.findChildren(QWidget):
        if ch is blk or ch.parent() is blk:
            continue
        if isinstance(ch, (cb._FadeMask, cb._EmergeBand)):
            continue        # 叠层控件：由各自的贴附逻辑保证
        try:
            if ch.isHidden() or ch.width() <= 0 or ch.height() <= 0:
                continue
        except RuntimeError:
            continue
        cg = _geom_in(blk, ch)
        if not br.contains(cg):
            over = max(0, cg.bottom() - br.bottom(), br.top() - cg.top(),
                       cg.right() - br.right())
            bad.append(f"{type(ch).__name__} 越界 {over}px @ {cg}")
    assert not bad, f"块内子控件越界（挤压/错位）：{bad}"
    host.root.close()


# --------------------------------------------------------------------------
# 4. 混合序列：块不重叠、无异常空白（元素互相遮挡 / 大片空白）
# --------------------------------------------------------------------------

def _mixed_blocks(think, out):
    return [
        (cb.KIND_THINK, {"tag": "PLANNING", "body": think, "sid": "s1"}, "t"),
        (cb.KIND_TOOL, {"name": "run_command", "meta": "shell 2.4s",
                        "params": {"cmd": "npm test", "timeout": 120},
                        "out": out}, "o"),
        (cb.KIND_CMD, {"label": "bash", "cmd": "npm test", "out": out}, "c"),
        (cb.KIND_STREAM, {"html": "已完成上述改动。" * 20}, "s"),
    ]


def _assert_no_overlap(turn):
    """按 `_box_lay` 的布局项顺序两两相邻比对。

    块序列里夹着「过程区开关 / 继续显示 / 系统时间行」，它们与块共用同一套布局项 ——
    只比块与块会把「块 → 开关 → 块」的真实占位误判成空白。

    空白判据按**设计间距**：块间距是 `BLOCK_GAP`（`BLOCK_GAP[think]=8` 等），因此
    「think → 工具行」本来就该有 8px；真正的异常是超出设计值的空档（多出来的才是
    「大片空白」）。
    """
    lay = turn._box_lay
    seq = []
    for i in range(lay.count()):
        it = lay.itemAt(i)
        w = it.widget() if it is not None else None
        if w is None or w.isHidden():
            continue
        g = it.geometry()
        kind = None
        for ref in turn._items:
            if ref.widget is w:
                kind = ref.kind
                break
        seq.append((type(w).__name__, g.y(), g.bottom(), kind))
    # 设计上允许的最大间距：块间距(BLOCK_GAP 最大 8) + 行内控件自身高度之外的余量
    max_gap = max(cb.BLOCK_GAP.values()) + 2
    for a, b in zip(seq, seq[1:]):
        d = b[1] - (a[2] + 1)
        assert d >= 0, f"{b[0]} 压住 {a[0]} {-d}px（元素互相遮挡）"
        # 两个相邻项都是块时才按 BLOCK_GAP 判；夹着行内控件时另算
        if a[3] and b[3]:
            want = max(cb.BLOCK_GAP.get(a[3], 0), cb.BLOCK_GAP.get(b[3], 0))
            assert d <= want + 2, \
                f"{a[0]} → {b[0]} 间距 {d}px 超出设计 {want}px（大片空白）"
        else:
            assert d <= max_gap, \
                f"{a[0]} → {b[0]} 间距 {d}px（大片空白）"


def test_mixed_turn_blocks_never_overlap_after_fold_and_resize():
    """混合序列在「折叠往返 + 连续缩放 + 换肤」后仍不重叠、无异常空白。"""
    host = Host(760)
    turn, wrap = host.add_turn()
    turn.render(_mixed_blocks(LONG_THINK, LONG_OUT), cost=1.0, live=False,
                done=False)
    _pump(12)
    _assert_no_overlap(turn)

    # 逐块折叠往返
    for ref in list(turn._items):
        blk = ref.widget
        if blk is None:
            continue
        btn = getattr(blk, "_fold_btn", None)
        if btn is None or btn.isHidden():
            continue
        btn.click()
        _pump(5)
        _assert_no_overlap(turn)
        btn.click()
        _pump(5)
        _assert_no_overlap(turn)

    # 连续缩放
    for w in (980, 860, 760, 640, 520, 460):
        host.resize_to(w)
        _assert_no_overlap(turn)

    # 换肤（QSS 变化会改标签度量，必须重新归位）
    light = STYLE.__class__(**{**STYLE.__dict__, "bg": "#FFFFFF",
                                "panel": "#F2F3F5", "band": "#FAFAFA",
                                "think_bg": "#EEF2FB", "text_dim": "#22252B",
                                "ok_fg": "#1F2937"})
    turn.restyle(light)
    _pump(8)
    _assert_no_overlap(turn)
    for ref in turn._items:
        blk = ref.widget
        if blk is None:
            continue
        mask = getattr(blk, "_fold_mask", None)
        if mask is not None and not mask.isHidden():
            lg = _geom_in(blk, blk._fold_label())
            mg = _geom_in(blk, mask)
            assert mg.bottom() == lg.bottom(), \
                f"换肤后 {ref.kind} 遮罩错位 {mg.bottom()} vs {lg.bottom()}"
    host.root.close()


def test_turn_height_has_no_large_blank_below_blocks():
    """包裹层不得比回合内容高出大片空白（AI 输出区下方大片空白）。"""
    host = Host(760)
    turn, wrap = host.add_turn()
    turn.render(_mixed_blocks(LONG_THINK, LONG_OUT), cost=1.0, live=False,
                done=True)
    _pump(12)
    for ref in turn._items:
        blk = ref.widget
        if blk is not None and getattr(blk, "_fold_btn", None) is not None \
                and not blk._fold_btn.isHidden():
            blk._fold_btn.click()
            _pump(6)
    extra = wrap.height() - turn.height()
    assert extra <= 24, f"包裹层比回合多出 {extra}px（大片空白）"
    host.root.close()