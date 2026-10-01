# -*- coding: utf-8 -*-
"""「工具/命令输出挂在对应气泡下方」+「气泡外侧不再有状态提示小字」的契约回归。

两项用户要求在此各成一组断言：

A. 输出挂载（`agent_panel` 段层 + `agent_chat_bubbles.ToolCallRow` 块层）
   AI 每调用一个工具 / 执行一条命令，其输出必须**紧贴该次调用的下方**（同一区块内
   「调用在上、输出在下」，中间留固定间距），而不是另起一块「执行结果」。实现是
   `_on_result` 就地把输出续写进对应 op 段（`_attach_out_seg`），未命中才落回独立段。

B. 气泡底部外侧只剩打字指示器
   消息流里不得再插入任何状态小字（`_add_status` 已整体移除）；阻断/失败类提示
   改走右下角通知（`_notify_blocked` → `_toast`），不再占用消息流。
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest                                                        # noqa: E402
from PyQt6.QtGui import QColor                                        # noqa: E402
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget        # noqa: E402

from zhuzhu_Copilot.ui import agent_chat_bubbles as cb              # noqa: E402
from zhuzhu_Copilot.ui import agent_panel as ap                     # noqa: E402

app = QApplication.instance() or QApplication([])

PANEL_PATH = (Path(__file__).resolve().parents[1] / "src" / "zhuzhu_Copilot"
              / "ui" / "agent_panel.py")

STYLE = cb.ChatStyle(
    card="#1F232C", border="#272C36", border_soft="#333A46", dash="#333A46",
    text="#F3F5F9", text_dim="#9BA3B0", accent="#2F52D8", muted="#9BA3B0",
    icon_shell="#272C36", icon_color="#2F52D8", tag_bg="#2A3040", tag_fg="#9BA3B0",
    user_bg="#2F52D8", user_fg="#FFFFFF", cmd_fg="#F3F5F9", ok_fg="#5B82F6",
    hover="#272C36", panel="#181B21", bg="#101216",
    think_bg="#20263A", think_border="#2C3A63", tag_plan_bg="#2A3040",
    tag_exec_bg="#243157", tool_shell="#22304F", out_fg="#5B82F6",
    out_line="#2F52D8",
)

LONG_THINK = "用户要求扫描全盘部署包并生成迁移清单，先枚举安装产物再按指纹过滤。" * 24


class Host:
    """离屏宿主：把组件放进真实布局并显示，使其获得真实宽度与几何。"""

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


def _seg_block(seg, i=0):
    """单段 → 区块（借面板的映射能力，不构造真实面板）"""
    p = ap.AgentPanel.__new__(ap.AgentPanel)
    p._seg_cache = {}
    p._font_scale = lambda: 1.0
    p._ai_turn_max_width = lambda: 520
    return p._seg_blocks([seg])[0]


def _res_panel(segs):
    """只带「结果落段 + 段映射」所需状态的最小面板替身"""
    p = ap.AgentPanel.__new__(ap.AgentPanel)
    p._segments = segs
    p._seg_cache = {}
    p._sess = {"s": {"last_cmd": "", "last_ask_q": ""}}
    p._session_id = "s"
    p._font_scale = lambda: 1.0
    p._ai_turn_max_width = lambda: 520
    p._stop_send_spin = lambda: None
    p._ensure_ai_bubble = lambda: None
    p._refresh_ai_html = lambda: None
    p._scroll_bottom = lambda: None
    p._call_panel = lambda *a, **k: None
    return p


# ---------- A. 输出挂载 ----------

def test_tool_result_is_written_into_its_own_tool_row():
    """工具输出必须就地续写进对应工具行（同一区块），不得另起「执行结果」区块。"""
    segs = [{"type": "op", "name": "read_file", "html": "▎read_file"}]
    p = _res_panel(segs)
    p._on_result("read_file", "第一行\n第二行", [])

    assert len(segs) == 1, f"输出另行成段了（应挂在工具行内）：{segs}"
    assert segs[0]["out"] == "第一行<br/>第二行", "换行需转成 <br/> 且原文保留"


def test_run_command_output_joins_the_same_command_block():
    """命令：命令全文 + 输出落在同一个命令块（`$ 命令` 在上、输出在下）。"""
    segs = [{"type": "op", "name": "run_command", "html": "▎run_command"}]
    p = _res_panel(segs)
    p._sess["s"]["last_cmd"] = "dir /b"
    p._on_result("run_command", "a.txt", [])

    assert len(segs) == 1
    kind, payload, _sig = _seg_block(segs[0])
    assert kind == cb.KIND_CMD, "带命令的 op 段应渲染成命令块"
    assert "dir /b" in payload["cmd"] and "a.txt" in payload["out"]


def test_command_is_mounted_before_result_so_block_kind_never_flips():
    """命令在确认钩子阶段就写进工具行（立刻是命令块），结果到达只补输出 ——
    区块类型全程不变，避免「工具行 → 命令块」中途替换控件造成闪烁。"""
    segs = [{"type": "op", "name": "run_command", "html": "▎run_command"}]
    p = _res_panel(segs)
    assert p._remember_cmd_seg(segs, '{"command": "dir /b"}') is True

    kind0, payload0, _sig0 = _seg_block(segs[0])
    assert kind0 == cb.KIND_CMD and "dir /b" in payload0["cmd"]
    assert payload0["out"] == "", "此刻还没有输出"

    p._on_result("run_command", "a.txt", [])
    kind1, payload1, _sig1 = _seg_block(segs[0])
    assert kind1 == cb.KIND_CMD, "结果到达后区块类型必须保持不变"
    assert "a.txt" in payload1["out"]


def test_output_is_attached_to_the_matching_tool_row_not_the_last_one():
    """一轮里多个工具：各自输出挂到各自的工具行（按名匹配，不串行）。"""
    segs = [
        {"type": "op", "name": "read_file", "html": "▎read_file"},
        {"type": "op", "name": "grep", "html": "▎grep"},
    ]
    p = _res_panel(segs)
    p._on_result("grep", "命中 3 处", [])
    p._on_result("read_file", "文件内容", [])

    assert segs[0]["out"] == "文件内容"
    assert segs[1]["out"] == "命中 3 处"


def test_same_tool_twice_attaches_output_in_execution_order():
    """同名工具连续调用两次：输出按执行顺序各归其位（先到的挂前一个工具行）。"""
    segs = [
        {"type": "op", "name": "grep", "html": "▎grep"},
        {"type": "text", "raw": "中间说明"},
        {"type": "op", "name": "grep", "html": "▎grep"},
    ]
    p = _res_panel(segs)
    p._on_result("grep", "第一次", [])
    p._on_result("grep", "第二次", [])

    assert segs[0]["out"] == "第一次"
    assert segs[2]["out"] == "第二次"


def test_result_without_tool_row_falls_back_to_its_own_segment():
    """找不到对应工具行（历史数据 / 无状态事件）时兜底成独立结果段，不丢内容。"""
    segs = [{"type": "text", "raw": "正文"}]
    p = _res_panel(segs)
    p._on_result("read_file", "文件内容", [])

    assert len(segs) == 2 and segs[-1]["type"] == "result"
    assert segs[-1]["html"] == "文件内容"


def test_tool_without_output_adds_no_block():
    """工具没产出内容时不落任何区块（调用行本身已在视图里，别留空壳块）。"""
    segs = [{"type": "op", "name": "read_file", "html": "▎read_file"}]
    p = _res_panel(segs)
    p._on_result("read_file", "   ", [])

    assert len(segs) == 1 and segs[0].get("out") is None


def test_ask_user_answer_keeps_its_own_card():
    """ask_user 的「提问 + 回答」是自带排版的富文本卡片，不并入工具输出区。"""
    segs = [{"type": "op", "name": "ask_user", "html": "▎ask_user"}]
    p = _res_panel(segs)
    p._sess["s"]["last_ask_q"] = "选哪个？"
    p._on_result("ask_user", "选 A", [])

    assert segs[0].get("out") is None, "提问卡片不得被塞进工具输出区"
    assert segs[-1]["type"] == "result" and "选 A" in segs[-1]["html"]


def test_seg_sig_covers_output_so_block_refreshes_when_result_arrives():
    """op 段签名必须包含 out：否则输出到达时签名不变 → 渲染缓存不刷新，输出不显示。"""
    seg = {"type": "op", "name": "read_file", "html": "▎read_file"}
    before = ap._seg_sig(seg)
    seg["out"] = "文件内容"
    assert ap._seg_sig(seg) != before


def test_long_output_folds_instead_of_truncating():
    """超长输出的**展示**必须是折叠而非截断：`_FoldMixin` 只把前缀铺进标签，
    全文始终留在块里 —— 点「展开全部」必须能看到被折叠掉的剩余内容。

    历史缺陷：单个工具块能长到数千像素（一次 600 行输出 ≈ 10 屏），把思考气泡与回复
    正文挤出视野、并让相邻区块互相遮挡。
    """
    row = cb.ToolCallRow(STYLE, ap._line_icon)
    long_out = "<br/>".join(f"第 {i} 行输出" for i in range(300))
    row.set_content("read_file", "", {}, out=long_out)

    assert row._fold_foldable(), "长输出必须判定为可折叠"
    assert row._fold_btn.text() == cb.OUT_FOLD_TEXT and not row._fold_holder.isHidden(), \
        "折叠态必须给出「展开全部」入口"
    shown = row._out.text()
    assert len(shown) < len(long_out), "折叠态不应把全文铺进标签（每次测量都要重排整篇）"
    assert row._out.minimumHeight() == row._fold_limit_h(), \
        "折叠态必须把输出标签钉在行数上限（否则块仍会被撑爆）"

    # 展开：全文必须回来（不截断）
    row._fold_toggle()
    assert row._fold_btn.text() == cb.FOLD_CLOSED_TEXT
    assert long_out in row._out.text() or row._out.text() == long_out, \
        "展开后必须看到完整输出（折叠不允许丢内容）"
    assert row._out.minimumHeight() > row._fold_limit_h()

    # 再收起：回到行数上限
    row._fold_toggle()
    assert row._out.minimumHeight() == row._fold_limit_h()


def test_short_output_has_no_fold_control():
    """短输出不出现折叠控件（工具行与输出区保持原样）"""
    row = cb.ToolCallRow(STYLE, ap._line_icon)
    row.set_content("read_file", "", {}, out="两行\n输出")
    assert not row._fold_foldable()
    assert row._fold_holder.isHidden()
    assert row._fold_extra_h() == 0, "不可折叠时开关不得占高度"


def test_on_result_keeps_full_output_for_expand():
    """端到端：超长工具结果落到 op 段时**不得截断**（展示由折叠负责，不是这里裁）。"""
    segs = [{"type": "op", "name": "read_file", "html": "▎read_file"}]
    p = _res_panel(segs)
    raw = "\n".join(f"第 {i} 行" for i in range(500))
    p._on_result("read_file", raw, [])

    out = segs[0].get("out") or ""
    assert out.count("<br/>") + 1 > cb.OUT_FOLD_LINES, "输出被截断了 → 展开后看不到全文"
    assert all(f"第 {i} 行" in out for i in (0, 250, 499)), "折叠前必须保留完整内容"


def test_safe_prefix_never_cuts_inside_a_tag():
    """折叠前缀不得切在标签中间（残片会原样显示成文字）"""
    def broken(s: str) -> bool:
        """末尾是否留下未闭合的标签"""
        return s.rfind("<") > s.rfind(">")

    html = "甲<br/>乙<br/>丙"
    assert cb.safe_prefix(html, 4) == "甲", "有换行边界时停在边界上"
    assert cb.safe_prefix(html, 10) == "甲<br/>乙", "停在预算内最后一个换行边界上"
    assert cb.safe_prefix(html, 999) == html, "预算够大时原样返回"

    # 无换行边界的单行超长输出：退化为字符前缀，但不得把标签切成两半
    for budget in range(1, 24):
        out = cb.safe_prefix("<span>x</span>yyyy", budget)
        assert not broken(out), f"预算 {budget} 切出了残片：{out!r}"
        assert "<span>x</span>yyyy".startswith(out), "必须是原文前缀"
    assert cb.safe_prefix("yyyy", 2) == "yy"


def test_tool_row_keeps_output_hidden_until_it_arrives(host):
    """无输出时工具行等价于原来的一行调用（输出区不占高度）。"""
    row = host.add(cb.ToolCallRow(STYLE, ap._line_icon))
    row.set_content("read_file", "path=a.txt", {})
    host.show()
    assert row._out_box.isHidden(), "没有输出时输出区必须收起"
    bare = row.heightForWidth(row.width() or 520)

    row.set_content("read_file", "path=a.txt", {}, out="42 行内容")
    app.processEvents()
    assert not row._out_box.isHidden()
    assert row.heightForWidth(row.width() or 520) > bare, "有输出时行高必须随之增长"


def test_tool_output_sits_below_the_call_with_a_gap(host):
    """输出紧贴工具行下方且**留出间距**（不是贴在标题上，也不是另起一块）。"""
    row = host.add(cb.ToolCallRow(STYLE, ap._line_icon))
    row.set_content("read_file", "path=a.txt", {}, out="第一行输出\n第二行输出")
    host.show()
    title_bottom = row._title.mapTo(row, row._title.rect().bottomLeft()).y()
    out_top = row._out.mapTo(row, row._out.rect().topLeft()).y()
    assert out_top - title_bottom >= cb.TOOL_OUT_GAP, \
        f"输出与工具行之间未留出 {cb.TOOL_OUT_GAP}px 间距（实得 {out_top - title_bottom}）"
    assert row._out.geometry().height() > 0, "输出正文被压扁/未展开"

    inner = 500 - cb.TOOL_ICON - row.layout().spacing()
    assert row._out_box.heightForWidth(inner) >= (cb.TOOL_OUT_GAP
                                                  + row._out.sizeHint().height()), \
        "输出区高度预算未包含与工具行之间的间距"


def test_turn_renders_output_inline_without_adding_a_block(host):
    """端到端：结果到达后回合里仍是同一批区块，且回合随之变高（输出立即可见）。"""
    segs = [
        {"type": "think", "html": "先读文件"},
        {"type": "op", "name": "read_file", "html": "▎read_file"},
    ]
    p = _res_panel(segs)
    turn = host.add(cb.ChatTurn(STYLE, ap._line_icon))
    turn.render(p._seg_blocks(segs), live=True)
    host.show()
    n_before = len(turn._items)
    h_before = turn.heightForWidth(turn.width())

    p._on_result("read_file", "文件内容 " * 30, [])
    turn.render(p._seg_blocks(segs), live=True)
    app.processEvents()

    assert len(turn._items) == n_before, "输出必须挂在工具行内，不得新增区块"
    assert turn.heightForWidth(turn.width()) > h_before, "输出到达后回合必须变高"
    last = turn._items[-1].widget
    assert not last._out_box.isHidden() and last._out.text(), "输出未渲染到工具行下方"


def test_output_accent_line_is_really_painted(host):
    """输出区左侧竖线必须真的画出强调色（QSS 底色在 QWidget 上默认不绘制）。"""
    row = host.add(cb.ToolCallRow(STYLE, ap._line_icon))
    row.set_content("read_file", "", {}, out="输出内容")
    host.show()
    img = row.grab().toImage()
    line = row._out_line
    center = line.mapTo(row, line.rect().center())
    got, want = img.pixelColor(center), QColor(STYLE.out_line)
    near = (abs(got.red() - want.red()) <= 12 and abs(got.green() - want.green()) <= 12
            and abs(got.blue() - want.blue()) <= 12)
    assert near, f"输出竖线未画出强调色：实得 {got.name()}，期望 {want.name()}"


# ---------- 超长输出：折叠在回合内真的限住了高度，展开能收回全文 ----------

def _long_output_html(rows: int = 400) -> str:
    return "<br/>".join(f"第 {i} 行：这是一段较长的工具输出内容。" for i in range(rows))


def test_turn_bounds_height_for_long_output_and_expands(host):
    """端到端：超长输出在回合里必须被折叠限高（不再把回合撑到数千像素），
    点「展开全部」后回合高度随之增长、完整内容可见（不丢内容）。

    历史缺陷：输出长度没有上限时，一个工具块就能长到数千像素，把思考气泡与回复
    正文挤出视野、并让相邻区块互相遮挡。
    """
    segs = [
        {"type": "think", "html": "先读文件再总结。" * 30},
        {"type": "op", "name": "read_file", "html": "▎read_file",
         "out": _long_output_html()},
        {"type": "text", "raw": "最终结论：一切正常。"},
    ]
    p = _res_panel(segs)
    turn = host.add(cb.ChatTurn(STYLE, ap._line_icon))
    turn.render(p._seg_blocks(segs), live=True)
    host.show()
    app.processEvents()

    tool = next(r.widget for r in turn._items if r.kind == cb.KIND_TOOL)
    line_h = tool._line_height()
    assert not tool._fold_holder.isHidden(), "超长输出必须给出「展开全部」入口"

    bounded = turn.heightForWidth(turn.width())
    # 输出被限在行数上限附近（允许思考块 + 正文 + 开关的固定开销），绝不应是数千像素
    assert bounded < line_h * (cb.OUT_FOLD_LINES + 40), \
        f"回合仍被超长输出撑爆：{bounded}px（折叠上限 {line_h * cb.OUT_FOLD_LINES}px）"

    full = tool._fold_full
    tool._fold_btn.click()
    app.processEvents()
    assert tool._out.text() == full, "展开后必须铺全文"
    assert turn.heightForWidth(turn.width()) > bounded + line_h * 100, \
        "展开后回合必须跟着长高（否则全文被回合总额压扁）"


def test_command_block_output_folds_too(host):
    """命令块（含「执行结果」段）的输出同样折叠：它是最容易产生超长文本的一类。"""
    segs = [{"type": "op", "name": "run_command", "html": "▎run_command",
             "cmd": "dir /s", "out": _long_output_html()}]
    p = _res_panel(segs)
    turn = host.add(cb.ChatTurn(STYLE, ap._line_icon))
    turn.render(p._seg_blocks(segs), live=True)
    host.show()
    app.processEvents()

    cmd = next(r.widget for r in turn._items if r.kind == cb.KIND_CMD)
    assert cmd._fold_foldable(), "长输出的命令块必须判定为可折叠"
    assert not cmd._fold_holder.isHidden()
    assert cmd._body.minimumHeight() == cmd._fold_limit_h(), "命令块输出未钉在折叠上限"
    cmd._fold_btn.click()
    app.processEvents()
    # 命令块是**两段**折叠区（`$ 命令` + 输出）：`_fold_full` 是两段拼接，
    # 各段标签只承载自己那一段。
    assert cmd._body.text() == cmd._fold_parts[1], "展开后命令块必须铺全文"


def test_command_text_folds_with_long_command(host):
    """命令**全文**（`$ ...`）同样参与折叠：超长命令不再被静默截断。

    以前命令段不折叠、只受 `_RESULT_TRUNCATE` 截断，用户看不到完整命令；现在
    「命令 + 输出」同属一个折叠区，任一段超长都会出现「展开全部」。
    """
    long_cmd = "dir " + ("--include=/very/long/path/segment " * 60)
    segs = [{"type": "op", "name": "run_command", "html": "▎run_command",
             "cmd": long_cmd, "out": "ok"}]
    p = _res_panel(segs)
    turn = host.add(cb.ChatTurn(STYLE, ap._line_icon))
    turn.render(p._seg_blocks(segs), live=True)
    host.show()
    app.processEvents()

    cmd = next(r.widget for r in turn._items if r.kind == cb.KIND_CMD)
    assert cmd._fold_foldable(), "超长命令必须判定为可折叠"
    limit = cmd._fold_limit_h()
    assert cmd._cmd.minimumHeight() <= limit + 2, "折叠态命令段必须压在行数上限内"
    cmd._fold_btn.click()
    app.processEvents()
    assert cmd._cmd.text() == cmd._fold_parts[0], "展开后命令段必须铺全文"
    assert cmd._body.text() == cmd._fold_parts[1], "展开是两段一起铺，输出段不受影响"


# ---------- B. 气泡底部外侧只剩打字指示器 ----------

def test_inline_status_hint_rows_are_gone():
    """消息流里不再有状态小字：`_add_status` 的定义与调用都已移除。"""
    src = PANEL_PATH.read_text(encoding="utf-8")
    assert "_add_status" not in src, "气泡底部外侧的状态提示小字必须清零"
    assert not hasattr(ap.AgentPanel, "_add_status")


def test_blocked_notice_goes_to_corner_notification():
    """阻断/失败提示改走右下角通知：不占消息流，但「点了没反应」仍有回执。"""
    p = ap.AgentPanel.__new__(ap.AgentPanel)
    calls = []
    p._toast = lambda *a, **k: calls.append((a, k))
    ap.AgentPanel._notify_blocked(p, "未知 Agent「x」，可用: y")

    assert len(calls) == 1, "阻断提示必须给出一次通知"
    args, kwargs = calls[0]
    assert args[0] == "操作未完成" and args[1] == "未知 Agent「x」，可用: y"
    assert kwargs.get("warn") is True

    ap.AgentPanel._notify_blocked(p, "x", "提示词优化失败")
    assert calls[1][0][0] == "提示词优化失败", "自定义标题需生效"


# ---------- 补充：思考气泡（展开不被截断 / 挤压 / 遮挡） ----------

def test_think_body_is_never_truncated_before_reaching_the_bubble():
    """思考正文在渲染层不得截断：截断会让「继续查看」展开后仍是一段残文。"""
    p = ap.AgentPanel.__new__(ap.AgentPanel)
    body = "推理" * 3000
    html = p._render_seg_html({"type": "think", "html": body}, 0, "think", 14, 11, 13, 300)
    assert body in html, "思考正文被截断（展开后看不到全文）"
    assert "…" not in html


def test_think_fold_repins_turn_height(host):
    """展开长思考必须回报外层重钉回合高度，否则正文被回合总额压扁/遮挡。"""
    turn = host.add(cb.ChatTurn(STYLE, ap._line_icon))
    turn.render([(cb.KIND_THINK, {"tag": "PLANNING", "body": f"<div>{LONG_THINK}</div>"},
                  ("t", 0))], live=True)
    host.show()
    tb = turn._items[0].widget
    folded_h = turn.heightForWidth(turn.width())

    hits = []
    turn.set_block_resize_handler(lambda: hits.append(1))
    assert not tb._fold_btn.isHidden(), "长思考必须给出展开入口"
    tb._fold_btn.click()
    app.processEvents()

    assert hits == [1], "折叠态变化必须回报外层一次"
    assert turn.heightForWidth(turn.width()) > folded_h, \
        "展开后回合高度没跟着长高 → 长正文会被压扁/遮挡"


def test_think_height_budget_covers_frame_border(host):
    """高度预算必须含 QFrame 描边：漏算会让真高比预算多 2px，最后一项被压掉两像素。"""
    tb = host.add(cb.ThinkBubble(STYLE, ap._line_icon))
    tb.set_content("PLANNING", f"<div>{LONG_THINK}</div>")
    host.show()
    m = tb.layout().contentsMargins()
    need = (2 * tb.frameWidth() + m.top() + m.bottom() + cb.THINK_ICON
            + cb.THINK_HEAD_GAP + tb._body.minimumHeight() + cb.THINK_HEAD_GAP
            + tb._fold_btn.sizeHint().height())
    assert tb.heightForWidth(tb.width()) >= need


# ---------- 补充：气泡层次色（更多颜色，但全部由 ChatStyle 注入） ----------

def test_chat_style_derives_layered_colors():
    """层次色由色板派生（不是写死），且与基础色确实不同 —— 否则等于没加颜色。"""
    style = ap.AgentPanel.__new__(ap.AgentPanel)._chat_style()
    assert style.think_bg and style.think_bg != style.card
    assert style.think_border and style.think_border != style.border
    assert style.tag_exec_bg != style.tag_bg, "EXEC 胶囊需与 PLANNING 区分"
    assert style.tool_shell and style.tool_shell != style.icon_shell
    assert style.out_fg and style.out_line


def test_chat_style_layered_colors_fall_back_when_absent():
    """老构造点（只给基础色）不得缺色：空串一律回退到对应基础色。"""
    bare = cb.ChatStyle(
        card="#111111", border="#222222", border_soft="#333333", dash="#333333",
        text="#ffffff", text_dim="#999999", accent="#2F52D8", muted="#999999",
        icon_shell="#272C36", icon_color="#2F52D8", tag_bg="#2A3040", tag_fg="#9BA3B0",
        user_bg="#2F52D8", user_fg="#FFFFFF", cmd_fg="#F3F5F9", ok_fg="#5B82F6",
        hover="#272C36", panel="#181B21", bg="#101216")
    assert bare.think_bg_of() == bare.card
    assert bare.think_border_of() == bare.border
    assert bare.tag_bg_of("EXEC") == bare.tag_bg
    assert bare.tag_bg_of("PLANNING") == bare.tag_bg
    assert bare.tool_shell_of() == bare.icon_shell
    assert bare.out_fg_of() == bare.ok_fg
    assert bare.out_line_of() == bare.accent


def test_components_apply_the_layered_colors(host):
    """层次色必须真的落到控件上（思考底/阶段胶囊/输出竖线）。"""
    tb = host.add(cb.ThinkBubble(STYLE, ap._line_icon))
    assert STYLE.think_bg in tb.styleSheet()
    assert STYLE.think_border in tb.styleSheet()

    tb.set_content("EXEC", "<div>x</div>")
    assert STYLE.tag_exec_bg in tb._tag.styleSheet(), "EXEC 阶段胶囊未取 EXEC 色"
    tb.set_content("PLANNING", "<div>x</div>")
    assert STYLE.tag_plan_bg in tb._tag.styleSheet(), "PLANNING 阶段胶囊未取规划色"

    row = host.add(cb.ToolCallRow(STYLE, ap._line_icon))
    assert STYLE.tool_shell in row._icon.styleSheet(), "工具图标壳未取层级壳色"
    assert STYLE.out_line in row._out_line.styleSheet(), "输出区竖线未取强调色"
    assert STYLE.out_fg in row._out.styleSheet(), "输出正文字色未生效"
