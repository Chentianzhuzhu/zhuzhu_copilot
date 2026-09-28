# -*- coding: utf-8 -*-
"""工具专属矢量图标回归（每个工具都要有自己的图标，不能一律用同一个通用图标）。

守护：
1. 覆盖：全部注册工具都在 `TOOL_ICON_SPEC` 里登记（新增工具忘登记会失败并点名）；
2. 唯一：任意两个工具的「族底图 + 动作角标」组合都不相同（否则两个工具看起来一样）；
3. 可绘制：每个图标都真的画出可见像素（防笔误导致空白图标）；
4. 规范：无 emoji、无硬编码颜色（颜色由调用方注入，随主题变化）；
5. 分流：面板统一入口 `_chat_icon` 对工具名走专属图标、对通用 UI 图标走 `_line_icon`。
"""
import os
import re
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PyQt6.QtWidgets import QApplication                            # noqa: E402

from zhuzhu_Copilot.core import agent_tools                        # noqa: E402
from zhuzhu_Copilot.ui import agent_panel as ap                    # noqa: E402
from zhuzhu_Copilot.ui import tool_icons as ti                     # noqa: E402

app = QApplication.instance() or QApplication([])

MODULE = (Path(__file__).resolve().parents[1] / "src" / "zhuzhu_Copilot"
          / "ui" / "tool_icons.py")
EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F]")
COLOR_RE = re.compile(r"#[0-9A-Fa-f]{6}\b")

TOOL_NAMES = [t["function"]["name"] for t in agent_tools.TOOLS]
ICON_SIZE = 18


def _opaque_pixels(icon, size: int = ICON_SIZE) -> int:
    img = icon.pixmap(size, size).toImage()
    return sum(1 for y in range(size) for x in range(size)
               if img.pixelColor(x, y).alpha() > 8)


def _pixel_signature(icon, size: int = ICON_SIZE):
    """图标的像素指纹（逐点读色，避免依赖不同 PyQt 版本的 bits() 返回类型）"""
    img = icon.pixmap(size, size).toImage()
    return tuple((img.pixelColor(x, y).alpha(), img.pixelColor(x, y).rgb())
                 for y in range(size) for x in range(size))


def test_every_registered_tool_is_mapped():
    """扩展性守卫：新增工具必须登记专属图标；未登记会被点名。"""
    missing = [n for n in TOOL_NAMES if n not in ti.TOOL_ICON_SPEC]
    assert not missing, f"以下工具没有专属图标（请补 TOOL_ICON_SPEC）：{missing}"


def test_no_stale_entries_after_tool_removal():
    """反向守卫：工具被删除后图标表也要跟着清理，避免残留死配置。"""
    stale = [n for n in ti.TOOL_ICON_SPEC if n not in TOOL_NAMES]
    assert not stale, f"图标表存在已不存在的工具：{stale}"


def test_no_two_tools_share_the_same_icon_combo():
    """「专属」的硬定义：组合两两不同，否则界面上会出现一模一样的两个工具图标。"""
    seen = {}
    for name in TOOL_NAMES:
        seen.setdefault(ti.spec_of(name), []).append(name)
    dup = {k: v for k, v in seen.items() if len(v) > 1}
    assert not dup, f"这些工具共用了同一个图标组合：{dup}"
    assert len(seen) == len(TOOL_NAMES), "图标组合数应等于工具数"


def test_spec_parts_are_registered():
    """表里引用的底图/角标都必须有实现（拼错 key 会画出不完整的图标）。"""
    for name in TOOL_NAMES:
        base, orna = ti.spec_of(name)
        assert base in ti._ICON_BASE, f"{name} 的底图 {base} 未实现"
        assert orna in ti._ICON_ORNAMENT, f"{name} 的角标 {orna} 未实现"


def test_every_tool_icon_draws_visible_strokes():
    """每个工具的图标都要画出足够多的线条像素（防空白/笔误）。"""
    blank = [(n, _opaque_pixels(ti.tool_icon(n, ICON_SIZE, "#9BA3B0")))
             for n in TOOL_NAMES
             if _opaque_pixels(ti.tool_icon(n, ICON_SIZE, "#9BA3B0")) < 20]
    assert not blank, f"以下工具图标几乎空白：{blank}"


def test_icons_differ_from_each_other_pixelwise():
    """像素级抽查：组合不同 ⇒ 渲染结果必须不同（防止某个绘制函数被写空）。"""
    sigs = {}
    for name in TOOL_NAMES:
        sigs.setdefault(_pixel_signature(ti.tool_icon(name, ICON_SIZE, "#9BA3B0")),
                        []).append(name)
    dup = {v[0]: v for v in sigs.values() if len(v) > 1}
    assert not dup, f"以下工具渲染结果完全相同：{dup}"


def test_unknown_tool_falls_back_to_a_drawable_icon():
    """未登记的工具名也必须能画出图标（不能渲染空白），便于工具先上线后补图标。"""
    assert ti.spec_of("brand_new_tool") == ti.FALLBACK_SPEC
    assert _opaque_pixels(ti.tool_icon("brand_new_tool", ICON_SIZE, "#9BA3B0")) >= 20


def test_icon_module_has_no_emoji_and_no_hardcoded_colors():
    src = MODULE.read_text(encoding="utf-8")
    assert not EMOJI_RE.findall(src), "图标模块出现 emoji"
    assert not COLOR_RE.findall(src), "图标模块出现硬编码颜色（应由调用方注入）"


def test_panel_dispatches_tool_names_to_dedicated_icons():
    """面板入口分流：工具名 → 专属图标；通用 UI 图标（clock/chev/tool） → _line_icon。"""
    p = ap.AgentPanel.__new__(ap.AgentPanel)
    for tool in ("run_command", "read_file", "browser_click"):
        icon = p._chat_icon(tool, ICON_SIZE, "#9BA3B0")
        assert not icon.pixmap(ICON_SIZE, ICON_SIZE).isNull()
        assert _opaque_pixels(icon) >= 20
    # 通用 UI 图标仍走既有线条图标集（不因分流而画空）
    for kind in ("clock", "chev", "think", "tool"):
        assert _opaque_pixels(p._chat_icon(kind, ICON_SIZE, "#9BA3B0")) >= 8


def test_tool_row_shows_the_tool_specific_icon():
    """工具调用行的图标壳会随工具名刷新为专属图标（而非通用扳手）。"""
    from zhuzhu_Copilot.ui import agent_chat_bubbles as cb
    p = ap.AgentPanel.__new__(ap.AgentPanel)
    style = cb.ChatStyle(
        card="#1F232C", border="#272C36", border_soft="#333A46", dash="#333A46",
        text="#F3F5F9", text_dim="#9BA3B0", accent="#2F52D8", muted="#9BA3B0",
        icon_shell="#272C36", icon_color="#2F52D8", tag_bg="#2A3040", tag_fg="#9BA3B0",
        user_bg="#2F52D8", user_fg="#FFFFFF", cmd_fg="#F3F5F9", ok_fg="#5B82F6",
        hover="#272C36", panel="#181B21", bg="#101216")
    row = cb.ToolCallRow(style, p._chat_icon)
    def _tile_sig() -> tuple:
        img = row._icon.pixmap().toImage()
        return tuple((img.pixelColor(x, y).alpha(), img.pixelColor(x, y).rgb())
                     for y in range(img.height()) for x in range(img.width()))

    row.set_content("read_file", "", {})
    read_sig = _tile_sig()
    row.set_content("write_file", "", {})
    assert read_sig != _tile_sig(), "工具行图标未随工具名切换"


def test_ui_pseudo_kind_icons_are_visible():
    """技能调用 / 并行执行等伪 kind 行也要有可见图标（防止图标壳内空白）。"""
    p = ap.AgentPanel.__new__(ap.AgentPanel)
    for kind in ("正在调用技能 lark-base", "skill", "正在并行执行 3 个工具"):
        assert _opaque_pixels(p._chat_icon(kind, ICON_SIZE, "#9BA3B0")) >= 8, kind


def test_ui_icon_spec_parts_are_registered():
    """UI_ICON_SPEC 每个取值里的底图/角标都必须真实存在（写错 key 会画出空图标）。"""
    for kind, (base, orna) in ti.UI_ICON_SPEC.items():
        assert base in ti._ICON_BASE, f"{kind} 的底图 {base} 未实现"
        assert orna in ti._ICON_ORNAMENT, f"{kind} 的角标 {orna} 未实现"


def test_tool_row_uses_explicit_ico_kind():
    """payload 带的 ico 决定图标壳：技能行（ico=skill）不再落到展示文案的兜底图标上。"""
    from zhuzhu_Copilot.ui import agent_chat_bubbles as cb
    p = ap.AgentPanel.__new__(ap.AgentPanel)
    style = cb.ChatStyle(
        card="#1F232C", border="#272C36", border_soft="#333A46", dash="#333A46",
        text="#F3F5F9", text_dim="#9BA3B0", accent="#2F52D8", muted="#9BA3B0",
        icon_shell="#272C36", icon_color="#2F52D8", tag_bg="#2A3040", tag_fg="#9BA3B0",
        user_bg="#2F52D8", user_fg="#FFFFFF", cmd_fg="#F3F5F9", ok_fg="#5B82F6",
        hover="#272C36", panel="#181B21", bg="#101216")
    row = cb.ToolCallRow(style, p._chat_icon)

    def _tile_sig() -> tuple:
        img = row._icon.pixmap().toImage()
        return tuple((img.pixelColor(x, y).alpha(), img.pixelColor(x, y).rgb())
                     for y in range(img.height()) for x in range(img.width()))

    row.set_content("调用技能", "lark-base", {}, ico="skill")
    assert row._title.text() == "调用技能"
    assert row._meta.text() == "lark-base"
    skill_sig = _tile_sig()
    row.set_content("未知展示文案", "", {})
    assert skill_sig != _tile_sig(), "ico 未参与图标解析（仍按 name 兜底）"


def test_skill_op_segment_carries_ico_and_meta():
    """技能 op 段的 KIND_TOOL payload 必须带 ico=skill 与技能名 meta（供图标壳与副标题）。"""
    p = ap.AgentPanel.__new__(ap.AgentPanel)
    from zhuzhu_Copilot.ui import agent_chat_bubbles as cb
    kind, payload = p._seg_block(
        {"type": "op", "name": "调用技能", "meta": "lark-base", "ico": "skill",
         "html": "▎调用技能 lark-base"}, 0, "op", 14, 11, 13, 400)
    assert kind == cb.KIND_TOOL
    assert payload["name"] == "调用技能"
    assert payload["meta"] == "lark-base"
    assert payload["ico"] == "skill"
