# -*- coding: utf-8 -*-
"""事件流聊天气泡「1:1 复刻 demo」的结构 / 样式 / 行为回归。

被测对象：
- `src/winapp_migrator/ui/agent_chat_bubbles.py`：事件流气泡组件（外形与排版）
- `src/winapp_migrator/ui/agent_panel.py`：段 → 区块的结构映射与回合渲染入口

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

from winapp_migrator.ui import agent_chat_bubbles as cb              # noqa: E402
from winapp_migrator.ui import agent_panel as ap                     # noqa: E402

app = QApplication.instance() or QApplication([])

DEMO_HTML = Path(__file__).resolve().parents[1] / "ui_style_demo" / "index.html"
MODULE_PATH = (Path(__file__).resolve().parents[1] / "src" / "winapp_migrator"
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
    hover="#272C36", panel="#181B21",
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
    from winapp_migrator.ui import tokens as tk
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
    点击后展开为全文并可再收起。"""
    tb = host.add(cb.ThinkBubble(STYLE, ap._line_icon))
    tb.set_content("PLANNING", f"<div>{LONG_THINK}</div>")
    host.show()
    line_h = tb._line_height()
    assert tb._folded is True
    assert tb._body.maximumHeight() == line_h * 5
    assert tb._fold_btn.text() == "继续查看"
    assert not tb._fold_btn.isHidden(), "超行折叠必须给出展开入口"

    tb._fold_btn.click()
    app.processEvents()
    assert tb._folded is False
    assert tb._body.maximumHeight() == 16777215, "展开后须解除行数截断"
    assert tb._fold_btn.text() == "收起"

    tb._fold_btn.click()
    app.processEvents()
    assert tb._folded is True and tb._body.maximumHeight() == line_h * 5


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
    .ai-proc）；点击开关可再展开，且用户选择不被后续重渲染覆盖。"""
    turn = host.add(cb.ChatTurn(STYLE, ap._line_icon))
    blocks = [
        (cb.KIND_THINK, {"tag": "PLANNING", "body": "<div>思考</div>"}, ("k0", 0)),
        (cb.KIND_TOOL, {"name": "scan_drives", "meta": "target=D:/", "params": {}}, ("k1", 0)),
        (cb.KIND_STREAM, {"html": "<div>正文</div>"}, ("k2", 0)),
    ]
    turn.render(blocks, live=True, done=False)
    host.show()
    assert all(not w.isHidden() for _k, _s, w, _sp in turn._items), "进行中：过程区展开"

    turn.render(blocks, cost=12.4, live=False, sys_meta="14:02:31 → 14:02:43", done=True)
    app.processEvents()
    hidden = {k: w.isHidden() for k, _s, w, _sp in turn._items}
    assert hidden[cb.KIND_THINK] and hidden[cb.KIND_TOOL], "完成后过程类区块须收起"
    assert not hidden[cb.KIND_STREAM], "正文永不收起（demo .stream）"
    assert not turn._toggle.isHidden() and turn._toggle.text() == "查看执行过程"
    assert turn._ribbon._text.text() == "12.4s"
    assert turn._sys.text() == "14:02:31 → 14:02:43"

    # 折叠后高度必须真实收缩（否则正文与开关之间会留大片空白）
    collapsed_h = turn.heightForWidth(760)
    turn._toggle.click()
    app.processEvents()
    expanded_h = turn.heightForWidth(760)
    assert turn._toggle.text() == "收起执行过程"
    assert collapsed_h < expanded_h, f"折叠后高度未收缩：{collapsed_h} vs {expanded_h}"

    # 用户手动展开后，后续重渲染（如窗口缩放）不得自动收起
    turn.render(blocks, cost=12.4, live=False, sys_meta="14:02:31 → 14:02:43", done=True)
    app.processEvents()
    assert all(not w.isHidden() for _k, _s, w, _sp in turn._items), \
        "重渲染不得覆盖用户手动展开的选择"


def test_incremental_render_only_rebuilds_changed_block(host):
    """流式增量：签名未变的块必须复用同一控件实例，只有变化的末块被重建。"""
    turn = host.add(cb.ChatTurn(STYLE, ap._line_icon))
    blocks = [
        (cb.KIND_THINK, {"tag": "PLANNING", "body": "<div>思考</div>"}, ("a", 0)),
        (cb.KIND_CMD, {"label": "run_command", "cmd": "dir", "out": "ok"}, ("b", 0)),
        (cb.KIND_STREAM, {"html": "<div>正文</div>"}, ("c", 0)),
    ]
    turn.render(blocks, live=True)
    host.show()
    before = [id(w) for _k, _s, w, _sp in turn._items]

    turn.render(blocks, live=True)
    assert [id(w) for _k, _s, w, _sp in turn._items] == before, "同签名不得重建控件"

    grown = blocks[:2] + [(cb.KIND_STREAM, {"html": "<div>正文 + 追加</div>"}, ("c", 1))]
    turn.render(grown, live=True)
    after = [id(w) for _k, _s, w, _sp in turn._items]
    assert after[:2] == before[:2], "未变化的块应复用"
    assert after[2] != before[2], "内容变化的末块应重建"


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
