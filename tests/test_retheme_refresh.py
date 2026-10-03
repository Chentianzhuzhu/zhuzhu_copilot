# -*- coding: utf-8 -*-
"""主题切换（`AgentPanel._retheme` 整面板重建）后的三处刷新契约。

用户反馈：切换深色/浅色后
  ① 文件树内容空白；
  ② 对话气泡向中间压缩；
  ③ 面板残留白色/深色元素。
三条都出在「重建后没有把状态重新落地」上，这里各守一条契约：

  ① 侧栏（工作树 / Git）重建出的窗口是**空壳**，必须显式 `refresh()` —— 停靠定位只
     在「原本不可见」时刷新，而新建窗口已 show 过，会被判为"已可见"而跳过；
  ② 气泡宽度按**当前**视口重设 —— 重建瞬间 `msg_area` 视口未就绪，AI 气泡会被钉在
     回退宽度（240px）上，之后视口变宽也不会自己跟着变；
  ③ 侧栏内的滚动区域必须**显式**带 `QScrollBar` 规则 —— 控件一旦设有样式表，未提及
     的子控件会退回系统默认外观，浅色主题下就是一条深灰滚动条。

本文件用 `AgentPanel.__new__` 轻代理（不构造整面板，避免 MCP/托盘等副作用），
只调被测方法，不为控件调 show()。
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest                                                        # noqa: E402
from PyQt6.QtWidgets import (QApplication, QScrollArea, QVBoxLayout,  # noqa: E402
                             QWidget)

from zhuzhu_Copilot.ui import agent_panel as ap                      # noqa: E402

app = QApplication.instance() or QApplication([])


def _proxy() -> "ap.AgentPanel":
    return ap.AgentPanel.__new__(ap.AgentPanel)


# ---------- ③ 侧栏滚动条样式 ----------

def test_side_scroll_areas_get_theme_scrollbar_qss():
    """侧栏内的滚动区域要显式带 QScrollBar 规则（否则浅色主题残留系统深灰滚动条）。"""
    p = _proxy()
    holder = QWidget()
    sa = QScrollArea(holder)
    p.wt_win = holder
    for nm in ("git_win", "todos_win", "code_win"):
        setattr(p, nm, None)

    p._apply_side_scrollbars()
    assert "QScrollBar:vertical" in sa.styleSheet()
    assert "QScrollBar::handle:vertical" in sa.styleSheet()


def test_side_scrollbar_qss_is_not_duplicated():
    """重复调用不得把规则叠加多份（每次主题切换都会调一次）。"""
    p = _proxy()
    holder = QWidget()
    sa = QScrollArea(holder)
    p.wt_win = holder
    for nm in ("git_win", "todos_win", "code_win"):
        setattr(p, nm, None)

    p._apply_side_scrollbars()
    first = sa.styleSheet()
    p._apply_side_scrollbars()
    assert sa.styleSheet() == first
    assert first.count("QScrollBar:vertical") == 1


def test_side_scrollbar_qss_keeps_existing_local_rules():
    """补样式必须**追加**，不能覆盖控件原有规则（如 QTreeWidget 的底色/选中态）。"""
    p = _proxy()
    holder = QWidget()
    sa = QScrollArea(holder)
    sa.setStyleSheet("QScrollArea { background: transparent; border: none; }")
    p.wt_win = holder
    for nm in ("git_win", "todos_win", "code_win"):
        setattr(p, nm, None)

    p._apply_side_scrollbars()
    qss = sa.styleSheet()
    assert "background: transparent" in qss, "原有规则被覆盖"
    assert "QScrollBar:vertical" in qss


# ---------- ② 气泡宽度跟随视口 ----------

class _FakeBubble(QWidget):
    def __init__(self, align: str):
        super().__init__()
        self.setProperty("align", align)


def _bubble_proxy(ai_w: int, usr_w: int):
    p = _proxy()
    host = QWidget()
    p.msg_area = QScrollArea(host)
    p.msg_lay = QVBoxLayout(host)
    p._bubble_widgets = [_FakeBubble("ai"), _FakeBubble("user")]
    p._bubble_alive = staticmethod(lambda w: True)
    p._ai_turn_max_width = lambda: ai_w
    p._bubble_max_width = lambda: usr_w
    return p


def test_relayout_rewrites_bubble_width_from_current_viewport():
    """重排按**当前**视口重设气泡宽度：主题切换重建瞬间视口未就绪，宽度会被钉在回退值。"""
    p = _bubble_proxy(240, 773)          # 重建瞬间：视口未就绪 → AI 回退 240
    p._relayout_messages()
    ai, user = p._bubble_widgets
    assert ai.maximumWidth() == 240
    assert user.maximumWidth() == 773

    p._ai_turn_max_width = lambda: 620    # 视口就绪 → 必须跟着变
    p._relayout_messages()
    assert ai.maximumWidth() == 620, "视口恢复后气泡宽度未跟随（表现为挤在中间）"
    assert user.maximumWidth() == 773


def test_relayout_bubble_width_is_idempotent():
    """宽度未变时不重复 setMaximumWidth（该方法被高频自愈路径调用）。"""
    p = _bubble_proxy(620, 773)
    p._relayout_messages()
    calls = []
    p._bubble_widgets[0].setMaximumWidth = lambda w: calls.append(w)
    p._relayout_messages()
    assert not calls


# ---------- ④ 应用级色板与面板色板必须同源 ----------

def _restore_palette(original: dict) -> None:
    """把 styles.PALETTE 还原回进入用例前的主题，避免污染同进程内其它用例。"""
    from zhuzhu_Copilot.ui import styles
    keep = "dark" if original.get("bg_top") == styles.DARK_PALETTE["bg_top"] else "light"
    styles.set_palette(keep)


def test_app_palette_follows_theme_setting(monkeypatch):
    """应用级色板必须跟随 agent_theme。

    真实缺陷：styles.PALETTE 的模块默认值是深色，而主窗口移除后没有人再调 set_palette，
    启动时调色板固定停在深色；agent_panel 却按设置出图（设置是浅色）→
    对话框深底 + 白色卡片混搭（用户反馈「深色模式下居然有浅色元素」）。
    """
    import main
    from PyQt6.QtGui import QPalette
    from zhuzhu_Copilot.ui import styles

    original = dict(styles.PALETTE)
    try:
        monkeypatch.setattr(main, "_resolve_theme_mode", lambda: "light")
        main._sync_app_palette(app)
        assert styles.PALETTE["bg_top"] == styles.LIGHT_PALETTE["bg_top"], "浅色设置应得到浅色应用色板"
        assert app.palette().color(QPalette.ColorRole.Window).name().lower() == \
            styles.LIGHT_PALETTE["bg_top"].lower(), "浅色色板必须真的下发到应用"

        monkeypatch.setattr(main, "_resolve_theme_mode", lambda: "dark")
        main._sync_app_palette(app)
        assert styles.PALETTE["bg_top"] == styles.DARK_PALETTE["bg_top"]
        assert app.palette().color(QPalette.ColorRole.Window).name().lower() == \
            styles.DARK_PALETTE["bg_top"].lower()
    finally:
        _restore_palette(original)


def test_apply_theme_syncs_both_palettes(monkeypatch):
    """agent_panel.apply_theme() 必须同时同步 styles.PALETTE，保证两套色板同源。

    否则在面板内切换主题时只更新一侧，混搭会再次出现。
    """
    from zhuzhu_Copilot.ui import styles

    original = dict(styles.PALETTE)
    try:
        monkeypatch.setattr(ap, "_resolve_theme", lambda: "dark")
        ap.apply_theme()
        assert styles.PALETTE["bg_top"] == styles.DARK_PALETTE["bg_top"], \
            "apply_theme(dark) 后应用级色板应同步为深色"

        monkeypatch.setattr(ap, "_resolve_theme", lambda: "light")
        ap.apply_theme()
        assert styles.PALETTE["bg_top"] == styles.LIGHT_PALETTE["bg_top"], \
            "apply_theme(light) 后应用级色板应同步为浅色"
    finally:
        _restore_palette(original)
        ap.apply_theme()          # 还原模块状态（_APPLIED_THEME 等），按真实设置重绑
