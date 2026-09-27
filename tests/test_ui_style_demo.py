# -*- coding: utf-8 -*-
"""ui_style_demo 风格演示页 v2 的静态结构回归测试。

校验点：
1. 五种主题（light/paper/dark/glass/terminal）切换按钮齐全
2. 事件渲染器（user/ai/think/tool/exec/stream）与矢量图标齐备
3. 核心交互节点：耗时徽章、思考气泡、命令块、折叠按钮、虚线分区
4. 页面内置 SELF-CHECK 自检逻辑
5. 全页禁止 emoji（UI 约束），仅允许 SVG 矢量图标
6. 颜色约束：仅使用纯黑/淡灰/白/深蓝系，无杂色
"""
import re
from pathlib import Path

DEMO_HTML = Path(__file__).resolve().parent.parent / "ui_style_demo" / "index.html"
THEMES = ["light", "paper", "dark", "glass", "terminal"]
RENDERERS = ["user", "ai", "think", "tool", "exec", "stream"]
ICON_KEYS = ["clock", "think", "tool", "exec", "down"]
NODES = [".cost-ribbon", ".think-bubble", ".tool-call", ".cmd", ".stream", ".fold-btn", ".ai-turn", ".vline"]
EMOJI_RANGE = "[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u2764\u2B50]"


def test_demo_file_exists():
    assert DEMO_HTML.is_file(), f"缺失演示文件: {DEMO_HTML}"


def test_default_theme_is_glass():
    """最终选定毛玻璃质感，默认主题应为 glass。"""
    html = DEMO_HTML.read_text(encoding="utf-8")
    assert '<body data-theme="glass">' in html, "默认主题不是 glass"
    assert 'data-theme="glass" class="active"' in html, "glass 按钮未默认激活"


def test_body_never_folds():
    """正文回复永不收起：正文渲染器不应包含折叠控件。"""
    html = DEMO_HTML.read_text(encoding="utf-8")
    m = re.search(r"stream\(\)\s*\{(.*?)\n  \}", html, re.S)
    assert m, "未找到 stream 渲染器"
    body = m.group(1)
    assert "fold-ctrl" not in body, "正文渲染器不应含折叠控件"
    assert "fold-btn" not in body, "正文渲染器不应含折叠按钮"


def test_think_fold_by_lines():
    """思考过程：正文 >5 行自动折叠。"""
    html = DEMO_HTML.read_text(encoding="utf-8")
    assert ".think-txt" in html, "缺少思考内容折叠容器"
    assert "lines > rows" in html or "> rows" in html, "缺少按行数折叠判定"


def test_proc_collapse_on_report():
    """AI 完成汇报后收起全部过程，只留正文；可点击按钮查看执行过程。"""
    html = DEMO_HTML.read_text(encoding="utf-8")
    assert ".ai-proc" in html, "缺少过程区容器"
    assert ".ai-turn.done .ai-proc" in html, "缺少过程区收起样式"
    assert "查看执行过程" in html and "收起执行过程" in html, "缺少过程区展开/收起按钮"
    # 过程区不含 stream（正文），正文独立且不参与收起
    assert "it.type !== 'stream'" in html, "过程区应排除正文"
    assert "it.type === 'stream'" in html, "正文应独立渲染"


def test_theme_switcher_buttons():
    html = DEMO_HTML.read_text(encoding="utf-8")
    for theme in THEMES:
        assert f'data-theme="{theme}"' in html, f"缺少主题按钮: {theme}"


def test_body_dataset_bindings():
    html = DEMO_HTML.read_text(encoding="utf-8")
    for theme in THEMES:
        assert f'body[data-theme="{theme}"]' in html, f"缺少主题样式: {theme}"


def test_event_renderers_and_icons():
    html = DEMO_HTML.read_text(encoding="utf-8")
    for kind in RENDERERS:
        assert re.search(rf"{kind}\s*\(", html), f"缺少事件渲染器: {kind}"
    for key in ICON_KEYS:
        assert re.search(rf"{key}\s*:", html), f"缺少图标定义: {key}"
    assert "<circle" in html and "<path" in html, "缺少 SVG 矢量图标"


def test_core_interaction_nodes():
    html = DEMO_HTML.read_text(encoding="utf-8")
    for node in NODES:
        assert node in html, f"缺少核心节点样式: {node}"


def test_stream_fold_logic():
    html = DEMO_HTML.read_text(encoding="utf-8")
    assert "folded" in html, "缺少折叠态样式"
    assert "lines > 5" in html or "> 5" in html, "缺少超5行折叠判定"
    assert "继续查看" in html, "缺少折叠按钮文案"


def test_builtin_self_check():
    html = DEMO_HTML.read_text(encoding="utf-8")
    assert "selfCheck" in html, "缺少 SELF-CHECK 自检逻辑"


def test_no_emoji_in_source():
    html = DEMO_HTML.read_text(encoding="utf-8")
    hits = re.findall(EMOJI_RANGE, html)
    assert not hits, f"检测到 emoji: {hits}"


def test_palette_constraint():
    html = DEMO_HTML.read_text(encoding="utf-8")
    found = re.findall(r"#([0-9A-Fa-f]{6}|[0-9A-Fa-f]{3})\b", html)
    allowed = {
        "0a0d12", "ffffff", "fff", "eef0f3", "f3f5f8", "1b3a5c", "13203a",
        "0d1526", "0e131c", "1d2633", "16305c", "7fb0ff", "1b4a8a",
        "123161", "22334f", "1f2d47", "e3e7ee", "3a4653", "141b26",
        "4e5b6c", "93a6c2", "94a6c2", "e8eef8", "0a0b0f", "9cc0ff",
        "0f1626", "111826", "8cb4ff", "6e87ad", "d7e2f2", "8a9ab5",
        "13203a", "c9d9f5", "8cafff", "e3edff", "d9e6fa", "243040",
        "10213e", "1b3a63", "0a0f18", "edf0f5", "fafbfc", "e6e9ef",
        "f2f4f8", "59657a", "101725", "4a5568", "c9d2de", "1f2937",
        "070a12", "16263f", "8fb8ff", "a6c4ff", "edf2fb", "a9b8d2",
        "7d93b8", "c3ccd9", "8a94a6", "2a3d61", "2a3444", "5e7390",
        "000", "dfeaff",
    }
    banned = {b.lower() for b in found} - allowed
    assert not banned, f"检测到超出约束的颜色值: {banned}"