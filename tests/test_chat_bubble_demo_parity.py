# -*- coding: utf-8 -*-
"""事件流聊天气泡「1:1 复刻 demo」的结构 / 样式 / 行为回归。

被测对象：
- `src/zhuzhu_Copilot/ui/agent_chat_bubbles.py`：事件流气泡组件（外形与排版）
- `src/zhuzhu_Copilot/ui/agent_panel.py`：段 → 区块的结构映射与回合渲染入口

守护三类契约：
1. 结构对应：demo（ui_style_demo/index.html）的每个可见节点都有明确对应的组件，
   且组件注释标注了其 demo 来源（禁止「差不多像」的模糊实现）；
2. 样式参数：圆角 / 内边距 / 虚线节奏 / 图标壳尺寸与 demo 一致；颜色一律由
   ChatStyle 注入，组件模块内不得出现 #RRGGBB 字面量（换肤能力的硬前提）；
3. 关键行为：思考正文超行自动折叠、回合完成后过程区整体收起且可再展开、
   流式增量只重建内容变化的块、折叠后回合高度真实收缩。
"""
import os
import re
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest                                                        # noqa: E402
from PyQt6.QtCore import Qt                                          # noqa: E402
from PyQt6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget  # noqa: E402

from zhuzhu_Copilot.ui import agent_chat_bubbles as cb              # noqa: E402
from zhuzhu_Copilot.ui import agent_panel as ap                     # noqa: E402

app = QApplication.instance() or QApplication([])

DEMO_HTML = Path(__file__).resolve().parents[1] / "ui_style_demo" / "index.html"
MODULE_PATH = (Path(__file__).resolve().parents[1] / "src" / "zhuzhu_Copilot"
               / "ui" / "agent_chat_bubbles.py")
EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F]")
COLOR_RE = re.compile(r"#[0-9A-Fa-f]{6}\b")

# demo 的关键可见节点 → 必须在组件模块中以「demo .xxx」标注来源（可追溯，不靠猜）
DEMO_NODES = (
    "msg", "ai-turn", "cost-ribbon", "vline", "think-bubble", "tb-head",
    "tool-call", "tc-icon", "kv", "cmd", "stream", "proc-btn", "fold-btn",
    "dots", "sys-meta", "ai-proc",
)

STYLE = cb.ChatStyle(
    card="#1F232C", border="#272C36", border_soft="#333A46", dash="#333A46",
    text="#F3F5F9", text_dim="#9BA3B0", accent="#2F52D8", muted="#9BA3B0",
    icon_shell="#272C36", icon_color="#2F52D8", tag_bg="#2A3040", tag_fg="#9BA3B0",
    user_bg="#2F52D8", user_fg="#FFFFFF", cmd_fg="#F3F5F9", ok_fg="#5B82F6",
    hover="#272C36", panel="#181B21", bg="#101216",
)

LONG_THINK = "用户要求扫描全盘部署包并生成迁移清单，先枚举安装产物再按指纹过滤。" * 24


class Host:
    """离屏宿主：把组件放进真实布局并显示，使其获得真实宽度与 resize 事件。

    面板本身不在此构造（构造会带起 MCP/托盘等副作用），渲染链路由轻代理覆盖。
    """

    def __init__(self, width: int = 760, height: int = 1200):
        self.w = QWidget()
        self.lay = QVBoxLayout(self.w)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(0)
        self.w.resize(width, height)

    def add(self, widget):
        self.lay.addWidget(widget)
        return widget

    def show(self):
        self.w.show()
        for _ in range(4):
            app.processEvents()

    def close(self):
        self.w.hide()
        self.w.deleteLater()


@pytest.fixture()
def host():
    h = Host()
    yield h
    h.close()


def _blocks(segs):
    """面板同款「段 → 区块」映射（不构造面板，只借其渲染能力）"""
    p = ap.AgentPanel.__new__(ap.AgentPanel)
    p._seg_cache = {}
    p._font_scale = lambda: 1.0
    p._ai_turn_max_width = lambda: 520
    return p._seg_blocks(segs)


def _seg_block(seg, i=0):
    return _blocks([seg])[0]


# ---------- 1. 结构对应：demo 节点都有组件承载 ----------

def test_every_demo_node_is_implemented_and_attributed():
    """demo 的每个关键节点都要在组件模块里标注来源，避免「自创风格」而非复刻。"""
    src = MODULE_PATH.read_text(encoding="utf-8")
    missing = [n for n in DEMO_NODES if f".{n}" not in src]
    assert not missing, f"组件模块缺少这些 demo 节点的对应实现/标注：{missing}"


def test_demo_file_still_has_those_nodes():
    """反向校验：断言用的节点确实来自 demo，而不是测试自造。"""
    html = DEMO_HTML.read_text(encoding="utf-8")
    missing = [n for n in DEMO_NODES if f".{n}" not in html]
    assert not missing, f"demo 中不存在这些节点（测试基准失准）：{missing}"


# ---------- 2. 样式参数：几何与 demo 一致 + 无硬编码颜色 ----------

def test_component_module_has_no_hardcoded_colors():
    """颜色必须全部由 ChatStyle 注入：模块内出现 #RRGGBB 就说明有人写死了主题色，
    换肤（深/浅色 + 自定义 UI/UX 包）会立刻失效。"""
    hits = COLOR_RE.findall(MODULE_PATH.read_text(encoding="utf-8"))
    assert not hits, f"组件模块出现硬编码颜色：{hits}"


def test_component_module_has_no_emoji():
    hits = EMOJI_RE.findall(MODULE_PATH.read_text(encoding="utf-8"))
    assert not hits, f"组件模块出现 emoji：{hits}"


def test_tokens_carry_demo_geometry():
    """demo 派生的几何常量落在 tokens（禁止散落魔法数值）。"""
    from zhuzhu_Copilot.ui import tokens as tk
    assert (tk.BUBBLE_RADIUS_USER, tk.BUBBLE_RADIUS_USER_TAIL) == (18, 6)   # .msg
    assert tk.BUBBLE_RADIUS_THINK_TAIL == 6                                # .think-bubble
    assert (tk.RADIUS_CMD, tk.RADIUS_TILE, tk.RADIUS_CHIP) == (10, 9, 7)   # .cmd/.tc-icon/.kv
    assert tk.RADIUS_PILL == 999                                           # .tag/.fold-btn
    assert tk.THINK_FOLD_LINES == 5                                        # 超 5 行折叠
    assert tk.DASH_PATTERN == (4, 4)                                       # 虚线节奏


def test_user_bubble_is_asymmetric_demo_bubble(host):
    """用户气泡 = demo .msg：深蓝底 + 非对称圆角 18/18/6/18 + padding 13px 18px。"""
    ub = host.add(cb.UserBubble(STYLE))
    ub.setText("扫描 D 盘全部软件安装包")
    host.show()
    ss = ub.styleSheet()
    assert "border-top-left-radius: 18px" in ss
    assert "border-top-right-radius: 18px" in ss
    assert "border-bottom-left-radius: 18px" in ss
    assert "border-bottom-right-radius: 6px" in ss, "右下角须为小尾角（demo 6px）"
    assert "padding: 13px 18px" in ss
    assert STYLE.user_bg in ss and STYLE.user_fg.split()[0] in ss


def test_ai_turn_has_no_filled_bubble(host):
    """AI 回合 = demo .ai-turn：不设填充背景（旧实现是整块填充气泡），
    分区靠 paintEvent 的虚线，故控件不得带任何背景色声明。"""
    turn = host.add(cb.ChatTurn(STYLE, ap._line_icon))
    turn.render([(cb.KIND_STREAM, {"html": "<div>正文</div>"}, ("t", 0))], live=False,
                cost=1.0, done=True)
    host.show()
    assert "background" not in turn.styleSheet()
    pen = cb._dashed_pen(STYLE.dash)
    assert pen.dashPattern() == [4.0, 4.0], "虚线节奏须与 demo 的 linear-gradient 一致"


# ---------- 3. 行为：折叠 / 过程区 / 增量 ----------

def test_think_bubble_folds_over_five_lines_and_expands(host):
    """思考气泡 = demo .think-bubble：正文超 5 行自动折叠（尾部渐隐 + 「继续查看」），
    点击后展开为全文并可再收起。

    折叠机制为 **min-only 钉定**（钉 body 最小高度 = 5 行高，不设 maximumHeight 截断）：
    设上限会在「新内容已 setText、上限仍是旧值」的瞬间裁掉文字，反复触发即闪烁。
    """
    tb = host.add(cb.ThinkBubble(STYLE, ap._line_icon))
    tb.set_content("PLANNING", f"<div>{LONG_THINK}</div>")
    host.show()
    line_h = tb._line_height()
    assert tb._folded is True
    assert tb._body.minimumHeight() == line_h * 5
    assert tb._body.maximumHeight() == 16777215, "折叠靠钉下界，不设上限截断"
    assert tb._fold_btn.text() == "继续查看"
    assert not tb._fold_btn.isHidden(), "超行折叠必须给出展开入口"

    tb._fold_btn.click()
    app.processEvents()
    assert tb._folded is False
    assert tb._body.minimumHeight() > line_h * 5, "展开后下界须回到全文高度"
    assert tb._fold_btn.text() == "收起"

    tb._fold_btn.click()
    app.processEvents()
    assert tb._folded is True and tb._body.minimumHeight() == line_h * 5


def test_fold_button_is_not_squeezed(host):
    """折叠后正文必须被真正截到 5 行，且「继续查看」按钮保持完整高度。

    历史缺陷：钉高度时用了**全文**高度，超过折叠上限 → 折叠失效（正文全展开），
    且该块真实高度远超 heightForWidth 的估算，把整条回合的高度预算撑爆、最后一项
    （按钮）被压扁 —— 即用户报的「按钮被挤压」。
    """
    tb = host.add(cb.ThinkBubble(STYLE, ap._line_icon))
    tb.set_content("PLANNING", f"<div>{LONG_THINK}</div>")
    host.show()
    limit = tb._line_height() * 5
    assert tb._body.minimumHeight() == limit, "钉住的下界须与折叠上限一致"
    assert tb._body.maximumHeight() == 16777215, "折叠靠钉下界，不设上限"

    fb = tb._fold_btn
    assert fb.height() >= fb.sizeHint().height(), f"折叠按钮被压扁：{fb.size()} < {fb.sizeHint()}"
    assert fb.width() >= fb.sizeHint().width()

    m = tb.layout().contentsMargins()
    need = (m.top() + m.bottom() + cb.THINK_ICON + cb.THINK_HEAD_GAP + limit
            + cb.THINK_HEAD_GAP + fb.sizeHint().height())
    assert tb.heightForWidth(tb.width()) >= need, \
        "高度预算未覆盖头行+正文+按钮，布局会压缩最后一个子项"


def test_body_marking_keeps_only_last_stream():
    """正文归属标注：只有最后一段正文算「正文」，其余轮次回复并入过程区。

    同时必须**不就地改写**入参 payload —— 那些 dict 来自段级渲染缓存，就地加键会污染
    后续所有回合（缓存命中返回同一个 dict）。
    """
    blocks = [
        (cb.KIND_THINK, {"tag": "", "body": ""}, ("1", 0)),
        (cb.KIND_STREAM, {"html": "中间轮回复"}, ("2", 0)),
        (cb.KIND_CMD, {"label": "", "cmd": "", "out": ""}, ("3", 0)),
        (cb.KIND_STREAM, {"html": "最终回答"}, ("4", 0)),
    ]
    marked = ap.AgentPanel._mark_body_blocks(blocks)
    assert [p.get("proc") for k, p, _s in marked if k == cb.KIND_STREAM] == [True, False]
    assert [k for k, _p, _s in marked] == [b[0] for b in blocks], "不得改变顺序"
    assert "proc" not in blocks[1][1], "不得就地改写缓存中的 payload"


def test_no_stream_block_means_no_body_marking():
    """没有正文段时不做标注（该回合不参与「只留正文」的收起语义）。"""
    blocks = [
        (cb.KIND_THINK, {"tag": "", "body": ""}, ("1", 0)),
        (cb.KIND_CMD, {"label": "", "cmd": "", "out": ""}, ("2", 0)),
    ]
    assert ap.AgentPanel._mark_body_blocks(blocks) == blocks


def test_no_extra_blank_space_around_text(host):
    """文本块高度必须恒等于内容高度，且竖直顶端对齐。

    历史缺陷：只钉了最小高度 → 布局给的多余高度被 QLabel 吸收，而 QLabel 默认
    AlignVCenter 会把文字垂直居中 ⇒ 每段文字上下各留一大片空白。
    """
    turn = host.add(cb.ChatTurn(STYLE, ap._line_icon))
    turn.render([(cb.KIND_STREAM, {"html": "<div>一行简短回答。</div>"}, ("s", 0))],
                live=True)
    host.show()
    blk = turn._items[0].widget
    lbl = blk._body
    assert lbl.alignment() & Qt.AlignmentFlag.AlignTop, \
        "文本必须顶端对齐（否则多余高度会被上下均分）"
    need = lbl.heightForWidth(lbl.width())
    assert lbl.minimumHeight() == need, f"最小高度未钉到内容高度：{lbl.minimumHeight()} vs {need}"
    assert lbl.maximumHeight() == 16777215, "只钉下界，不设上限（设上限会在增量瞬间裁切）"
    assert lbl.height() == need, f"标签实际高度 {lbl.height()} ≠ 内容高度 {need}"
    assert blk.height() <= blk.heightForWidth(blk.width()) + 2, \
        "块高度不应超出内容高度（多余高度必须留在块外）"
    assert turn.heightForWidth(turn.width()) <= blk.height() + 80, "回合高度应贴合内容"


def test_short_think_bubble_shrinks_to_content(host):
    """思考正文不足 5 行时高度自适应：气泡高度 = 头行 + 正文，绝不预留 5 行。"""
    tb = host.add(cb.ThinkBubble(STYLE, ap._line_icon))
    tb.set_content("PLANNING", "<div>一句话思考</div>")
    host.show()
    limit = tb._line_height() * 5
    assert tb._folded is False, "短思考不应判定为可折叠"
    assert tb._body.maximumHeight() == 16777215, "短思考不得被限制到 5 行高度"
    assert tb._body.minimumHeight() < limit, "短思考不得被钉到 5 行高度"
    m = tb.layout().contentsMargins()
    # QFrame 的 1px 描边会把布局内区整体内缩：高度预算必须含上下两条描边，
    # 漏算会让真高比估算多 2px —— 展开长思考时最后一项（折叠按钮）被压掉两像素。
    bd = 2 * tb.frameWidth()
    need = (bd + m.top() + m.bottom() + cb.THINK_ICON + cb.THINK_HEAD_GAP
            + tb._body.minimumHeight())
    assert tb.heightForWidth(tb.width()) == need, "气泡应恰为描边 + 头行 + 正文高度"
    assert tb.height() <= need + 2, f"气泡实际高度 {tb.height()} 超出内容 {need}"
    assert tb._fold_btn.isHidden(), "短思考不应出现折叠按钮"


def test_stream_refresh_tick_is_small_and_fixed(monkeypatch):
    """流式刷新节拍固定为小间隔（≈30fps）而不是随正文增长放宽到数百毫秒 ——
    后者观感是一跳一跳，用户要求「丝滑连贯」。超长正文仍保留兜底间隔。"""
    delays = []

    class _FakeTimer:
        @staticmethod
        def singleShot(ms, cb):
            delays.append(ms)

    monkeypatch.setattr(ap, "QTimer", _FakeTimer)
    p = ap.AgentPanel.__new__(ap.AgentPanel)
    p._segments = [{"type": "text", "raw": "x" * 100, "streaming": True}]
    p._ai_bubble = object()
    p._html_dirty = False
    p._refresh_ai_html()
    assert delays == [ap._STREAM_TICK_MS], f"普通正文节拍异常：{delays}"
    assert ap._STREAM_TICK_MS <= 40, "节拍应足够小才能连续落字"

    delays.clear()
    p._html_dirty = False
    p._segments = [{"type": "text", "raw": "x" * (ap._STREAM_BIG_CHARS + 1),
                    "streaming": True}]
    p._refresh_ai_html()
    assert delays == [60], f"超大正文应走兜底间隔：{delays}"


def test_short_think_bubble_has_no_fold_button(host):
    tb = host.add(cb.ThinkBubble(STYLE, ap._line_icon))
    tb.set_content("EXEC", "<div>很短的一句思考。</div>")
    host.show()
    assert tb._folded is False
    assert tb._fold_btn.isHidden(), "未超行时不应出现折叠按钮（demo fold-ctrl hidden）"


def test_multi_round_think_bubbles_fold_independently(host):
    """多轮思考各成独立气泡：折叠其中一个不得影响另一个。"""
    t1 = host.add(cb.ThinkBubble(STYLE, ap._line_icon))
    t2 = host.add(cb.ThinkBubble(STYLE, ap._line_icon))
    t1.set_content("PLANNING", f"<div>{LONG_THINK}</div>")
    t2.set_content("EXEC", f"<div>{LONG_THINK}</div>")
    host.show()
    assert t1._folded and t2._folded
    t1._fold_btn.click()
    app.processEvents()
    assert t1._folded is False and t2._folded is True


def test_process_area_collapses_after_done_and_can_reopen(host):
    """AI 完成汇报后过程区整体收起，只留正文与「查看执行过程」（demo .ai-turn.done
    .ai-proc）；点击开关可再展开，且用户选择不被后续重渲染覆盖。

    新设计：过程块控件**始终保留**，收起时仅隐藏（setVisible(False) + spacer 归零），
    展开时仅显示——避免展开时重建 20+ 复杂控件导致 700ms+ 卡顿。因此断言的是
    「过程块被隐藏」而不是「被销毁」。
    """
    turn = host.add(cb.ChatTurn(STYLE, ap._line_icon))
    blocks = [
        (cb.KIND_THINK, {"tag": "PLANNING", "body": "<div>思考</div>"}, ("k0", 0)),
        (cb.KIND_TOOL, {"name": "scan_drives", "meta": "target=D:/", "params": {}}, ("k1", 0)),
        (cb.KIND_STREAM, {"html": "<div>正文</div>"}, ("k2", 0)),
    ]
    turn.render(blocks, live=True, done=False)
    host.show()
    assert [r.kind for r in turn._items] == [cb.KIND_THINK, cb.KIND_TOOL, cb.KIND_STREAM], \
        "进行中：过程区全部建出并可见"
    assert all(not r.widget.isHidden() for r in turn._items)

    turn.render(blocks, cost=12.4, live=False, sys_meta="14:02:31 → 14:02:43", done=True)
    app.processEvents()
    assert [r.kind for r in turn._items] == [cb.KIND_THINK, cb.KIND_TOOL, cb.KIND_STREAM], \
        "完成后过程块控件保留（不销毁）"
    assert all(r.widget.isHidden() for r in turn._items if r.is_proc), \
        "完成后过程块应被隐藏"
    assert all(not r.widget.isHidden() for r in turn._items if not r.is_proc), \
        "正文块应保持可见"
    assert not turn._toggle.isHidden() and turn._toggle.text() == "查看执行过程"
    assert turn._ribbon._text.text() == "12.4s"
    assert turn._sys.text() == "14:02:31 → 14:02:43"

    # 折叠后高度必须真实收缩（否则正文与开关之间会留大片空白）
    collapsed_h = turn.heightForWidth(760)
    turn._toggle.click()
    app.processEvents()
    expanded_h = turn.heightForWidth(760)
    assert [r.kind for r in turn._items] == [cb.KIND_THINK, cb.KIND_TOOL, cb.KIND_STREAM], \
        "展开后过程块仍然保留（不重建）"
    assert all(not r.widget.isHidden() for r in turn._items), \
        "展开后所有块应可见"
    assert turn._toggle.text() == "收起执行过程"
    assert collapsed_h < expanded_h, f"折叠后高度未收缩：{collapsed_h} vs {expanded_h}"

    # 用户手动展开后，后续重渲染（如窗口缩放）不得自动收起
    turn.render(blocks, cost=12.4, live=False, sys_meta="14:02:31 → 14:02:43", done=True)
    app.processEvents()
    assert len(turn._items) == 3, "重渲染不得覆盖用户手动展开的选择"
    assert all(not r.widget.isHidden() for r in turn._items), "用户展开后应保持可见"

    # 再收起：过程块被隐藏（控件不销毁）
    turn._toggle.click()
    app.processEvents()
    assert len(turn._items) == 3 and turn._toggle.text() == "查看执行过程"
    assert all(r.widget.isHidden() for r in turn._items if r.is_proc)


def test_earlier_round_text_becomes_process_block(host):
    """多轮任务：AI 在工具循环之间的中间回复属过程，回合结束后必须一并收起，
    只留最后一段正文（否则折叠后仍残留 AI 文字）。"""
    turn = host.add(cb.ChatTurn(STYLE, ap._line_icon))
    blocks = [
        (cb.KIND_THINK, {"tag": "PLANNING", "body": "<div>思考</div>"}, ("a", 0)),
        (cb.KIND_STREAM, {"html": "<div>第一轮回复</div>", "proc": True}, ("b", 0)),
        (cb.KIND_CMD, {"label": "run_command", "cmd": "dir", "out": "ok"}, ("c", 0)),
        (cb.KIND_STREAM, {"html": "<div>最终回答</div>", "proc": False}, ("d", 0)),
    ]
    turn.render(blocks, live=True, done=False)
    host.show()
    assert len(turn._items) == 4, "进行中：中间回复也要显示"

    turn.render(blocks, cost=1.0, live=False, done=True)
    app.processEvents()
    # 新设计：过程块控件保留但隐藏，_items 仍含全部 4 块
    assert len(turn._items) == 4, f"折叠后控件应保留，实际 {len(turn._items)} 块"
    visible = [r.sig for r in turn._items if not r.widget.isHidden()]
    assert visible == [("d", 0)], f"折叠后只应留最终正文可见，实际 {visible}"
    hidden = [r.sig for r in turn._items if r.widget.isHidden()]
    assert hidden == [("a", 0), ("b", 0), ("c", 0)], f"过程块应被隐藏，实际 {hidden}"
    final = [r for r in turn._items if r.sig == ("d", 0)][0]
    assert "最终回答" in final.widget._body.text()


def test_incremental_render_only_rebuilds_changed_block(host):
    """流式增量：签名未变的块必须复用同一控件实例；内容变化的块**就地更新**
    （控件身份保持稳定），而不是销毁重建。

    旧实现把正在增长的末块每帧销毁重建（整棵子树重建+全量布局），是流式闪烁/卡顿的
    来源；就地 setText 只触发该标签重排，输出才丝滑连贯。
    """
    turn = host.add(cb.ChatTurn(STYLE, ap._line_icon))
    blocks = [
        (cb.KIND_THINK, {"tag": "PLANNING", "body": "<div>思考</div>"}, ("a", 0)),
        (cb.KIND_CMD, {"label": "run_command", "cmd": "dir", "out": "ok"}, ("b", 0)),
        (cb.KIND_STREAM, {"html": "<div>正文</div>"}, ("c", 0)),
    ]
    turn.render(blocks, live=True)
    host.show()
    for n, ref in enumerate(turn._items):
        ref.widget._reuse_mark = n

    turn.render(blocks, live=True)
    assert [getattr(r.widget, "_reuse_mark", None) for r in turn._items] == [0, 1, 2], \
        "同签名不得重建控件"

    grown = blocks[:2] + [(cb.KIND_STREAM, {"html": "<div>正文 + 追加</div>"}, ("c", 1))]
    turn.render(grown, live=True)
    marks = [getattr(r.widget, "_reuse_mark", None) for r in turn._items]
    assert marks[:2] == [0, 1], "未变化的块应复用同一控件"
    assert marks[2] == 2, "内容变化的末块应就地更新（保持同一控件，不销毁重建）"
    assert "正文 + 追加" in turn._items[2].widget._body.text(), "就地更新后内容须为最新"
    assert [r.sig for r in turn._items] == [("a", 0), ("b", 0), ("c", 1)]


def test_cmd_block_structure_matches_demo(host):
    """命令块 = demo .cmd：圆角 10 边框 + 标题栏三点 + 名称 + `$ 命令` + 输出行。"""
    blk = host.add(cb.CmdBlock(STYLE))
    blk.set_content("run_command", "python -m migrator scan", "indexed 214 packages")
    host.show()
    assert "border-radius: 10px" in blk.styleSheet()
    dots = [w for w in blk.findChildren(QLabel) if w.width() == 8 and w.height() == 8]
    assert len(dots) == 3, f"标题栏须有三个圆点，实际 {len(dots)}"
    assert blk._bar_label.text() == "run_command"
    assert "$" in blk._cmd.text() and "python -m migrator scan" in blk._cmd.text()
    assert STYLE.accent in blk._cmd.text(), "命令提示符须用强调色（demo .cmd .in .dol）"
    assert "indexed 214 packages" in blk._body.text()
    assert STYLE.ok_fg in blk._body.styleSheet(), "输出行用 demo --ok-fg（蓝，不用绿）"


def test_tool_row_uses_tile_and_param_chips(host):
    """工具调用行 = demo .tool-call：30x30 图标壳 + 工具名 + meta + 参数 chip（圆角 7）。"""
    row = host.add(cb.ToolCallRow(STYLE, ap._line_icon))
    row.set_content("scan_drives", "target=D:/ · recursive",
                    {"rule": "yara 38", "mode": "full"})
    host.show()
    assert row._icon.width() == 30 and row._icon.height() == 30
    assert "border-radius: 9px" in row._icon.styleSheet()
    chips = [w for w in row._chips.findChildren(QLabel)]
    assert len(chips) == 2
    assert all("border-radius: 7px" in c.styleSheet() for c in chips)
    assert row._title.text() == "scan_drives"
    assert row._meta.text() == "target=D:/ · recursive"


def test_tool_row_chips_wrap_instead_of_overflowing(host):
    """参数 chip 多到一行放不下时必须换行（demo .kv flex-wrap），不能撑破面板宽度。"""
    row = host.add(cb.ToolCallRow(STYLE, ap._line_icon))
    row.set_content("t", "", {f"k{i}": "v" * 12 for i in range(12)})
    host.show()
    inner = 300
    one_row = row._chip_lay.heightForWidth(inner)
    assert one_row > 0
    # 单行高度 < 两行高度：说明换行生效
    single = max(c.sizeHint().height() for c in row._chips.findChildren(QLabel))
    assert one_row >= single * 2, "chip 未换行（会溢出面板宽度）"


# ---------- 4. 面板映射：段 → 区块 ----------

def test_seg_mapping_covers_every_segment_kind():
    """段类型 → 区块映射：新增段类型走通用富文本块兜底，不会静默丢失内容。"""
    cases = [
        ({"type": "think", "html": "思考"}, cb.KIND_THINK),
        ({"type": "op", "html": "▎read_file", "name": "read_file"}, cb.KIND_TOOL),
        ({"type": "op", "html": "▎run_command", "name": "run_command", "cmd": "dir"},
         cb.KIND_CMD),
        ({"type": "result", "html": "输出", "cmd": "dir"}, cb.KIND_CMD),
        ({"type": "text", "raw": "正文"}, cb.KIND_STREAM),
        ({"type": "ask", "q": "问题", "answered": True, "answer": "答案"}, cb.KIND_RICH),
        ({"type": "mark", "html": "标记"}, cb.KIND_RICH),
    ]
    for seg, want in cases:
        kind, _payload, _sig = _seg_block(seg)
        assert kind == want, f"{seg['type']} → {kind}，期望 {want}"


def test_op_with_command_shows_command_in_title_bar():
    """run_command 的 op 段：命令全文进命令块（`$` 行），标题栏保留工具名。"""
    _kind, payload, _sig = _seg_block(
        {"type": "op", "html": "▎run_command", "name": "run_command",
         "cmd": "python -m migrator scan --drive D"})
    assert payload["label"] == "run_command"
    assert "python -m migrator scan" in payload["cmd"]
    assert payload["out"] == "", "命令块此时不应出现输出（结果段单独成块）"


def test_result_block_keeps_command_and_output_apart():
    _kind, payload, _sig = _seg_block(
        {"type": "result", "html": "indexed 214 packages", "cmd": "dir"})
    assert payload["label"] == "执行结果"
    assert "dir" in payload["cmd"]
    assert "indexed 214 packages" in payload["out"]


def test_segment_content_is_escaped_before_reaching_widget():
    """命令/输出进入富文本前必须转义，否则命令里的尖括号会被当成标签吞掉。"""
    _kind, payload, _sig = _seg_block(
        {"type": "op", "html": "▎run_command", "name": "run_command",
         "cmd": 'echo "<b>x</b>" > a.txt'})
    assert "&lt;b&gt;" in payload["cmd"]
    assert "<b>" not in payload["cmd"]


# ---------------------------------------------------------------------------
# 耗时徽章常驻：**每个 AI 回合都要有计时器样式**（用户硬性要求）
# ---------------------------------------------------------------------------
# 背景：老会话（未归档的最后一轮、异常结束的回合）磁盘上没有 cost 字段，旧实现
# 「拿不到耗时即 hide 徽章」→ 重启加载后整条回合的计时样式整块消失，与其它回合
# 不一致。现在改为：有 cost 用它，没有就用系统时间行的起止差，再没有就显示占位符，
# 但徽章**永不隐藏**。

def _plain_turn(host: Host) -> "cb.ChatTurn":
    turn = host.add(cb.ChatTurn(STYLE, ap._line_icon))
    turn.setMaximumWidth(760)
    return turn


def test_cost_ribbon_stays_visible_when_cost_missing():
    host = Host()
    turn = _plain_turn(host)
    host.show()
    turn.render([(cb.KIND_STREAM, {"html": "<div>正文</div>"}, ("s1", 0))],
                live=False, sys_meta="14:02:31 → 14:02:43", done=True)
    host.w.update()
    assert not turn._ribbon.isHidden(), "无 cost 数据时徽章也必须常驻（不得隐藏）"
    assert turn._ribbon._text.text() == "12.0s", "应由系统时间行的起止差推导耗时"


def test_cost_ribbon_falls_back_to_placeholder_without_any_time_data():
    host = Host()
    turn = _plain_turn(host)
    host.show()
    turn.render([(cb.KIND_STREAM, {"html": "<div>正文</div>"}, ("s1", 0))],
                live=False, sys_meta="", done=True)
    host.w.update()
    assert not turn._ribbon.isHidden(), "任何情况下都不隐藏徽章，保证样式一致"
    assert turn._ribbon._text.text() == cb.RIBBON_UNKNOWN_TEXT


def test_cost_from_meta_parses_only_well_formed_ranges():
    assert cb.cost_from_meta("14:02:31 → 14:02:43") == 12.0
    assert cb.cost_from_meta("23:59:50 → 00:00:10") == 20.0, "跨零点按次日处理"
    assert cb.cost_from_meta("14:02:31") is None, "没有区间不得瞎猜"
    assert cb.cost_from_meta("") is None
    assert cb.cost_from_meta("bad → data") is None


# ---------------------------------------------------------------------------
# 折叠判定：估算优先，**不为判定排版整篇富文本**（展开卡死的主因）
# ---------------------------------------------------------------------------
# 隔离实测：2 万字符的富文本铺进 QLabel 量一次高度要数十毫秒；长回合里上百块
# 累积成秒级主线程阻塞（表现为「点开执行过程直接无响应」）。判定改为先用显式
# 换行数拿下界、再用采样字宽外推，只有量级贴着阈值时才退回真实测量。

def _tool_turn_with(out_html: str, host: Host) -> "cb.ChatTurn":
    turn = _plain_turn(host)
    turn.render([(cb.KIND_TOOL, {"name": "scan_drives", "meta": "target=D:/",
                                 "params": {}, "out": out_html}, ("t1", 0))],
                live=False, done=False)
    return turn


def test_long_output_is_foldable_without_laying_out_full_text(monkeypatch):
    host = Host()
    turn = _tool_turn_with(("扫描结果 " + "x" * 60 + "<br/>") * 400, host)
    host.show()
    ref = next(r for r in turn._items if r.kind == cb.KIND_TOOL)

    measured = []
    orig = cb.ToolCallRow._fold_measure_one

    def spy(self, lbl, html, w):
        measured.append(w)
        return orig(self, lbl, html, w)

    monkeypatch.setattr(cb.ToolCallRow, "_fold_measure_one", spy)
    ref.widget._bump_content()               # 失效判定缓存 → 强制重算
    assert ref.widget._fold_foldable(), "数百行的输出必须判为可折叠"
    assert not measured, "显式换行数已超上限时不得为判定排版全文（估算即可定论）"


def test_short_output_is_not_foldable_without_laying_out_full_text(monkeypatch):
    host = Host()
    turn = _tool_turn_with("ok<br/>done", host)
    host.show()
    ref = next(r for r in turn._items if r.kind == cb.KIND_TOOL)

    measured = []
    orig = cb.ToolCallRow._fold_measure_one

    def spy(self, lbl, html, w):
        measured.append(w)
        return orig(self, lbl, html, w)

    monkeypatch.setattr(cb.ToolCallRow, "_fold_measure_one", spy)
    ref.widget._bump_content()
    assert not ref.widget._fold_foldable(), "两行输出不该出现折叠开关"
    assert not measured, "明显不足上限时同样不需要排版全文"


def test_fold_estimation_is_monotonic_in_content_length():
    """估算必须随内容单调增长（阈值判定才有意义），且不依赖真实排版。"""
    lbl = QLabel("")
    fm = lbl.fontMetrics()
    short = cb._fold_est_lines("abc", 700, fm)
    mid = cb._fold_est_lines("x" * 3000, 700, fm)
    long_ = cb._fold_est_lines("x" * 9000, 700, fm)
    assert short < mid < long_
    assert cb._fold_est_lines("", 700, fm) == 1, "空内容只有一行"
    # 显式换行数必须进下限：400 个 <br/> 至少 400 行，与宽度无关
    brs = cb._fold_est_lines("<br/>" * 20 + "x", 100000, fm)
    assert brs >= 20, brs


# ---------------------------------------------------------------------------
# 回合内区块**不得互相重叠**（用户反馈：思考 / 工具输出 / 命令输出 / 正文叠在一起）
# ---------------------------------------------------------------------------
# 根因：块高只钉了**下界**（setMinimumHeight），而 QVBoxLayout 会在
# 「最小值 < 推荐值 < 最大值」之间做弹性分配，槽位与块高对不上 —— 长回合里后一块
# 就骑到前一块身上。修法：块改用**固定高度**（min == max == 本块应有高度），并在
# 回合高度上以「内部布局的真实最小需求」兜底。

def _mixed_turn_blocks(reps: int = 4) -> list:
    out_html = "".join(f"第 {i} 行：这是一段较长的输出内容。<br/>" for i in range(30))
    blocks = []
    for rep in range(reps):
        blocks.append((cb.KIND_THINK, {"tag": "EXEC", "body": f"<div>{LONG_THINK}</div>",
                                       "sid": rep}, (f"th{rep}", 0)))
        blocks.append((cb.KIND_TOOL, {"name": "scan_drives", "meta": "target=D:/",
                                      "params": {}, "out": out_html}, (f"tl{rep}", 0)))
        blocks.append((cb.KIND_CMD, {"label": "run_command", "cmd": "dir /s",
                                     "out": out_html}, (f"cmd{rep}", 0)))
    blocks.append((cb.KIND_STREAM, {"html": "<div>最终结论：一切正常。</div>"}, ("body", 0)))
    return blocks


def _expand_fully(turn):
    turn._toggle.click()
    app.processEvents()
    for _ in range(60):
        if getattr(turn, "_reveal_rest", None):
            turn._reveal_page()
        app.processEvents()
        if not getattr(turn, "_reveal_pending", None) and not getattr(turn, "_reveal_rest", None):
            break
    turn.relayout_heights(turn.width())
    app.processEvents()


def _assert_no_overlap(turn):
    prev_bottom, prev_kind = None, None
    for ref in turn._items:
        w = ref.widget
        if w.isHidden():
            continue
        g = w.geometry()
        assert prev_bottom is None or g.top() >= prev_bottom - 1, (
            f"{ref.kind} 与上一块 {prev_kind} 重叠：top={g.top()} < 上一块 bottom={prev_bottom}")
        prev_bottom, prev_kind = g.bottom(), ref.kind


def test_expanded_turn_blocks_do_not_overlap():
    """展开执行过程后，思考/工具/命令/正文四类块的几何必须严格不重叠。"""
    host = Host(760, 4000)
    turn = _plain_turn(host)
    host.show()
    turn.render(_mixed_turn_blocks(), live=False, done=True)
    app.processEvents()

    _expand_fully(turn)
    _assert_no_overlap(turn)

    # 折回到收起态再展开一次：状态来回切换后同样不得错位
    turn._toggle.click()
    app.processEvents()
    _expand_fully(turn)
    _assert_no_overlap(turn)


def test_turn_height_covers_inner_layout_minimum():
    """回合高度必须 ≥ 内部布局的真实最小需求。

    只按逐块 heightForWidth 累加会少算（块内布局的 minimumSize 会被折叠态标签的
    minimumHeight 抬高，「继续显示」按钮也容易被漏掉）—— 少算时内容溢出容器，
    滚动区里下一个气泡会骑到这个回合上。
    """
    host = Host(760, 4000)
    turn = _plain_turn(host)
    host.show()
    turn.render(_mixed_turn_blocks(), live=False, done=True)
    app.processEvents()

    _expand_fully(turn)                       # 展开态（含「继续显示」）
    need = turn._outer_pad() + turn._box_lay.minimumSize().height()
    assert turn.minimumHeight() >= need - 2, \
        f"展开态回合高度 {turn.minimumHeight()} < 布局需求 {need}"

    turn._toggle.click()                      # 收起态
    app.processEvents()
    turn.relayout_heights(turn.width())
    app.processEvents()
    need = turn._outer_pad() + turn._box_lay.minimumSize().height()
    assert turn.minimumHeight() >= need - 2, \
        f"收起态回合高度 {turn.minimumHeight()} < 布局需求 {need}"


def test_blocks_use_fixed_height_not_only_minimum():
    """块高必须同时钉住上下界（固定高度），否则布局会弹性分配、槽位与块高对不上。"""
    host = Host(760, 4000)
    turn = _plain_turn(host)
    host.show()
    turn.render(_mixed_turn_blocks(1), live=False, done=True)
    app.processEvents()
    for ref in turn._items:
        w = ref.widget
        if w.isHidden():
            continue
        assert w.minimumHeight() == w.maximumHeight(), (
            f"{ref.kind} 未钉成固定高度：min={w.minimumHeight()} max={w.maximumHeight()}")


