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

另有 ④ 壁纸透明态翻转（走快路径，不重建控件树）时 dock 栏容器必须一起刷新 ——
面板本身透明了、身后栏底仍是实色块，看上去就是「面板没透明」。

本文件用 `AgentPanel.__new__` 轻代理（不构造整面板，避免 MCP/托盘等副作用），
只调被测方法，不为控件调 show()。
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest                                                        # noqa: E402
from PyQt6.QtWidgets import (QApplication, QListWidget, QScrollArea,  # noqa: E402
                             QVBoxLayout, QWidget)

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


# ---------- ③b 滚动条槽底的「密密麻麻的麻点」 ----------
#
# 用户截图：Git 面板右侧一条**细密的点阵**（叠在自定义背景上）。
# 成因：Qt 的规矩是「样式表只接管了一部分时，没被提到的子控件退回**平台原生绘制**」——
# 旧实现的滚动条规则只写了 `:vertical` + `::handle`，漏掉槽底（add-page / sub-page），
# 于是滑块是主题色、上下槽底变成 Windows 原生的抖动(dither)图案。
# 合成窗口实测：旧规则 210 行里 184 行（88%）高频交替；补全后只剩滑块圆角的 10 行（5%）。

_SCROLLBAR_SUBCONTROLS = ("add-page", "sub-page", "add-line", "sub-line",
                          "up-arrow", "down-arrow", "left-arrow", "right-arrow")


def test_scrollbar_css_covers_every_subcontrol():
    """滚动条样式必须覆盖**全部**子控件（唯一来源：_scrollbar_css）。"""
    css = ap._scrollbar_css(6, 3, both=True)
    for token in _SCROLLBAR_SUBCONTROLS:
        assert token in css, f"滚动条样式漏了子控件 {token} → 会退回原生绘制（麻点）"


def test_global_qss_scrollbars_are_complete():
    """应用级 QSS 是下拉弹层 / 设置页 / 对话框的兜底，滚动条部分必须完整。

    回归：全局 QSS 曾手写「半套」规则（只有 :vertical + ::handle），用户在下拉菜单里
    看到的那排麻点就是这么来的。
    """
    g = ap._global_dialog_qss()
    for axis in ("vertical", "horizontal"):
        assert f"add-page:{axis}" in g, g[-600:]
        assert f"sub-page:{axis}" in g, g[-600:]


def test_git_panel_keeps_scrollbar_rules_after_surface_refresh():
    """Git 面板换肤后列表仍须带完整滚动条规则。

    回归（本次根因）：该窗口过去在 __init__ 与 apply_surface_theme 里**各自手写**
    列表 QSS 且都漏掉滚动条规则，于是换肤（换主题 / 开关自定义背景都会走）会把
    `_apply_side_scrollbars` 补上的规则覆盖掉，麻点随即出现。
    """
    win = ap.GitLogWindow()
    try:
        assert "add-page" in win.list.styleSheet(), "构造后列表就缺滚动条规则"
        win.apply_surface_theme()
        assert "add-page" in win.list.styleSheet(), \
            "apply_surface_theme 覆盖了滚动条规则 → 槽底会退回原生抖动图案"
    finally:
        win.close()
        win.deleteLater()


def test_side_scrollbars_complete_partial_qss():
    """`_apply_side_scrollbars` 必须能补全「半套」滚动条规则。

    判据用 add-page 而不是"有没有 QScrollBar"：只提过 QScrollBar 却漏掉槽底的半套规则
    照样会退化成原生麻点 —— 旧判据正是把它放过去了。
    """
    p = _proxy()
    holder = QWidget()
    partial = QListWidget(holder)
    partial.setStyleSheet(
        "QListWidget { background: transparent; }"
        "QScrollBar:vertical { background: transparent; width: 8px; }"
        "QScrollBar::handle:vertical { background: rgba(120,130,150,80); }")
    p.wt_win = holder
    for nm in ("git_win", "todos_win", "code_win"):
        setattr(p, nm, None)

    p._apply_side_scrollbars()
    qss = partial.styleSheet()
    assert "add-page:vertical" in qss, "半套滚动条规则未被补全"
    assert "sub-page:vertical" in qss
    # 幂等：再调一次不叠加
    p._apply_side_scrollbars()
    assert partial.styleSheet() == qss


def test_code_preview_views_carry_scrollbar_rules():
    """预览面板的内容视图（富文本 / 代码 / 图片滚动区）自带样式表时也要带滚动条规则。

    这里用**源码断言**而不是构造 CodePreviewWindow：该类在本测试模块的环境下构造会让
    进程直接退出（WebEngine 需要 ShareOpenGLContexts，见 tests/test_app_wallpaper.py
    顶部注释）。契约本身是"这三处 setStyleSheet 必须拼上 _scrollbar_css"，
    源码级断言足以守住，且不受平台不稳定影响（直接读盘，不用 inspect.getsource）。
    """
    src = (Path(__file__).resolve().parents[1]
           / "src/zhuzhu_Copilot/ui/agent_panel.py").read_text(encoding="utf-8")
    for name in ("html_view", "text", "img_scroll"):
        idx = src.find(f"self.{name}.setStyleSheet(")
        assert idx > 0, f"{name} 未找到样式表设置"
        seg = src[idx:idx + 600]
        assert "_scrollbar_css(" in seg, f"{name} 的样式表缺 _scrollbar_css（会出麻点）"


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


# ---------- ④ 壁纸透明态翻转：dock 栏容器必须跟着刷新 ----------

def test_apply_dock_surface_refreshes_columns(tmp_path):
    """设/清背景时 dock 栏容器必须一起换底。

    回归（用户反馈）：首次设置背景后，文件树 / Git / todos / 预览面板「底色不透明」。
    设/清背景走 `_retheme` **快路径**（不重建控件树，只逐面板 apply_surface_theme），
    面板自己透明了，但**身后那层 dockCol 栏底仍是实色块** —— 融入主面板时整片侧栏
    看上去就没透明。
    """
    from PyQt6.QtGui import QColor, QPixmap
    from zhuzhu_Copilot.core import app_wallpaper as aw

    p = _proxy()
    p._dock_left = QWidget()
    p._dock_left.setObjectName("dockColleft")
    p._dock_right = QWidget()
    p._dock_right.setObjectName("dockColright")
    img_path = tmp_path / "wall.png"
    pm = QPixmap(64, 64)
    pm.fill(QColor("#2F52D8"))
    assert pm.save(str(img_path), "PNG")
    try:
        # 无壁纸：栏底回落原始色板实色
        aw.set_fields(bg_image="", persist=False)
        ap.refresh_surface_mode()
        p._apply_dock_surface()
        assert ap._base_color("BG") in p._dock_left.styleSheet()
        assert "transparent" not in p._dock_left.styleSheet()

        # 首次设置背景（透明态 False → True，正是快路径）
        aw.set_fields(bg_image=str(img_path), persist=False)
        ap.refresh_surface_mode()
        p._apply_dock_surface()
        for col in (p._dock_left, p._dock_right):
            assert "background: transparent" in col.styleSheet(), \
                f"{col.objectName()} 栏底未随壁纸透明：{col.styleSheet()}"
    finally:
        aw.set_fields(bg_image="", persist=False)
        ap.refresh_surface_mode()
        p._dock_left.deleteLater()
        p._dock_right.deleteLater()


def test_retheme_fast_path_refreshes_dock_columns():
    """`_retheme` 快路径必须显式调用 `_apply_dock_surface()`（源码级接线契约）。

    只刷 4 个面板窗口、漏掉栏容器是这次缺陷的成因；两个调用点相距很远，
    用行为断言不易覆盖，故直接对快路径分支做源码断言（读盘，不用 inspect.getsource）。
    """
    src = (Path(__file__).resolve().parents[1]
           / "src/zhuzhu_Copilot/ui/agent_panel.py").read_text(encoding="utf-8")
    assert "if not _did_theme_change:" in src, "快路径分支不见了，本用例需同步更新"
    fast_path = src.split("if not _did_theme_change:")[1].split("慢路径开始拆树前")[0]
    assert "_apply_dock_surface()" in fast_path, \
        "快路径漏了 dock 栏容器刷新 → 面板透明但栏底仍是实色块"
