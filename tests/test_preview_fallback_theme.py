# -*- coding: utf-8 -*-
"""预览主题色解析与降级渲染的回归测试。

背景（真实缺陷）：预览主题色解析函数曾引用本模块**不存在**的 `PALETTE` 全局名，
抛 NameError → 保真渲染与降级渲染**同时**失败 → 预览只剩纯文本，用户看到
"无法预览完整样式"，且日志报 `NameError: name 'PALETTE' is not defined`。

本文件守护三条契约：
1. 主题色解析**任何情况下都不抛异常**，且返回 6 个非空颜色键；
2. 模块颜色常量缺失时能逐级回退（styles.PALETTE → office 内置默认）；
3. 降级渲染路径确实输出带样式的 HTML（表头底纹/边框），而非纯文本。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

import pytest
from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from winapp_migrator.ui import agent_panel as ap  # noqa: E402

KEYS = ("bg", "card", "text", "dim", "border", "accent")


def test_theme_resolution_never_raises_and_returns_full_palette():
    """必须可调用且返回完整配色（回归：曾因引用未定义的 PALETTE 抛 NameError）。"""
    theme = ap._office_preview_theme()
    assert isinstance(theme, dict)
    for k in KEYS:
        assert theme.get(k), f"缺少颜色键 {k}"
        assert str(theme[k]).startswith("#"), f"{k} 应为 HEX 颜色，实际 {theme[k]!r}"


def test_theme_resolution_falls_back_when_module_colors_missing(monkeypatch):
    """模块级颜色常量缺失（未 apply_theme / 自定义包异常）时仍须给出配色，不得抛错。"""
    for name in ("BG", "BG_BOTTOM", "PANEL", "CARD", "TEXT", "TEXT_DIM",
                 "BORDER", "ACCENT"):
        monkeypatch.delitem(ap.__dict__, name, raising=False)
    theme = ap._office_preview_theme()          # 不得抛异常
    for k in KEYS:
        assert theme.get(k), f"回退后仍缺 {k}"


def test_theme_reflects_module_colors(monkeypatch):
    """模块颜色存在时应优先采用（单一数据源：跟随主题切换）。"""
    monkeypatch.setitem(ap.__dict__, "TEXT", "#123456")
    monkeypatch.setitem(ap.__dict__, "CARD", "#654321")
    theme = ap._office_preview_theme()
    assert theme["text"] == "#123456"
    assert theme["card"] == "#654321"


@pytest.fixture(scope="module")
def xlsx_file(tmp_path_factory):
    from winapp_migrator.office import xlsx_builder
    p = str(tmp_path_factory.mktemp("fx") / "s.xlsx")
    xlsx_builder.build_xlsx(p, [{"name": "表一", "rows": [["标题", "值"], ["甲", 1]]}],
                            style={})
    return p


def test_fallback_renderer_outputs_styled_html(xlsx_file):
    """降级渲染必须带样式（底纹/边框/字号），不能退化成纯文本。

    这条同时守住"主题解析失败会连带否决降级渲染"的链路——历史缺陷就是
    主题解析抛错导致这里也拿不到样式。
    """
    html = ap._office_preview_html(xlsx_file, "xlsx")
    assert html, "降级渲染不应为空"
    assert "background-color:" in html, "缺少表头底纹"
    assert "border:1px solid" in html, "缺少单元格边框"
    assert "<table" in html, "缺少表格结构"
