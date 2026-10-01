"""玻璃填充回归：带底色的控件必须跟着玻璃参数走（不再残留旧的实色块）。

背景（用户反馈）：设置页左侧导航 / 右侧各子项，以及工作树、Git、任务清单、代码预览
四个面板里的控件底，在开启磨砂玻璃后仍是不透明的旧实色。根因是这些 QSS 只在构造时
取一次玻璃色，而浮窗在启动时就已经建好 —— 之后开玻璃 / 换壁纸不会重设。
"""
import pytest


@pytest.fixture
def glass_state():
    """保存 / 恢复全局玻璃参数（用例内可自由切换 enabled）。"""
    from zhuzhu_Copilot.core import app_glass

    before = app_glass.params()
    yield app_glass
    app_glass.set_params(before, persist=False)


@pytest.fixture
def offscreen_app():
    """确保存在 QApplication（构造真实控件用）。"""
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def test_gsurface_and_gfill_follow_glass_switch(glass_state):
    """大表面 / 小控件的取色入口：玻璃关=不透明原色，开=半透明玻璃。"""
    from zhuzhu_Copilot.ui import agent_panel as ap

    glass_state.set_fields(persist=False, enabled=False)
    assert ap._gsurface(ap.PANEL).lower() == ap.PANEL.lower()
    assert ap._gfill(ap.PANEL).lower() == ap.PANEL.lower()

    glass_state.set_fields(persist=False, enabled=True)
    for fn in (ap._gsurface, ap._gfill):
        assert fn(ap.PANEL).startswith("rgba("), "玻璃开启后必须是半透明玻璃填充"


def test_gsurface_alpha_has_readable_floor(glass_state):
    """大表面走可读性地板：透明度再高，alpha 也不低于小控件的刻度。"""
    from zhuzhu_Copilot.ui import agent_panel as ap

    glass_state.set_fields(persist=False, enabled=True, opacity=1.0)
    surf = float(ap._gsurface(ap.PANEL).rsplit(",", 1)[1].rstrip(")"))
    ctrl = float(ap._gfill(ap.PANEL).rsplit(",", 1)[1].rstrip(")"))
    assert surf > ctrl, "大表面（列表/输入区）必须比小控件更不透明，否则正文压不住壁纸"


@pytest.mark.parametrize("cls_name,attr", [("WorktreeWindow", "tree"),
                                           ("GitLogWindow", "list")])
def test_side_panels_restyle_on_glass_change(glass_state, offscreen_app,
                                             cls_name, attr):
    """工作树 / Git 面板：玻璃开启后调 refresh_glass_qss 必须换成玻璃底。"""
    from zhuzhu_Copilot.ui import agent_panel as ap

    glass_state.set_fields(persist=False, enabled=False)
    win = getattr(ap, cls_name)(None)
    try:
        target = getattr(win, attr)
        assert "rgba" not in target.styleSheet(), "玻璃关闭时应为不透明主题色"
        glass_state.set_fields(persist=False, enabled=True)
        win.refresh_glass_qss()
        assert "rgba(" in target.styleSheet(), "开玻璃后仍是旧实色底（残留）"
    finally:
        win.close()


def test_todos_panel_restyle_on_glass_change(glass_state, offscreen_app, tmp_path):
    """任务清单底色画在内层 TodosPanel 上：窗口需转调，且跟随壁纸状态。"""
    from zhuzhu_Copilot.ui import agent_panel as ap

    glass_state.set_fields(persist=False, enabled=False)
    win = ap.TodosWindow(None)
    try:
        assert "rgba" not in win.panel.styleSheet()
        img = tmp_path / "w.png"
        pm = ap.QPixmap(32, 32)
        pm.fill(ap.QColor("#2F52D8"))
        assert pm.save(str(img), "PNG")
        glass_state.set_fields(persist=False, enabled=True, bg_image=str(img))
        win.refresh_glass_qss()
        assert "transparent" in win.panel.styleSheet(), "有壁纸时根底交给玻璃内核绘制"
    finally:
        win.close()


def test_code_preview_restyle_is_wired(glass_state):
    """预览面板：必须提供 refresh_glass_qss，且窗口内控件底一律走 _gsurface。

    这里不构造真实 CodePreviewWindow —— 它会拉起 WebEngine 子进程，
    在离屏 pytest 会话里偶发进程级中止（0xC0000409）。构造路径由
    `scripts/_probe_settings_residual.py` 实测覆盖。
    """
    from pathlib import Path

    from zhuzhu_Copilot.ui import agent_panel as ap

    src = Path(ap.__file__).read_text(encoding="utf-8")
    seg = src[src.index("class CodePreviewWindow("):src.index("class TodosWindow(")]
    assert "def refresh_glass_qss(self)" in seg
    assert "background: {surf}" in seg or "background: {_gsurface(PANEL)}" in seg
    assert "self.refresh_glass_qss()" in seg, "构造末尾必须调用一次（懒建页也在其中）"


def test_glass_refresh_restyles_all_float_windows(glass_state):
    """主面板刷新玻璃时必须逐个重设四个浮窗（否则开玻璃后仍是旧实色）。

    用 `AgentPanel.__new__` 轻代理：只验证遍历接线，不构造整个面板。
    """
    from zhuzhu_Copilot.ui import agent_panel as ap

    panel = ap.AgentPanel.__new__(ap.AgentPanel)
    seen = []

    class _Stub:
        def refresh_glass_qss(self):
            seen.append(self)

    stubs = [_Stub() for _ in range(4)]
    panel._wt_win, panel._git_win, panel._todos_win, panel._code_win = stubs
    ap.AgentPanel._restyle_float_windows(panel)
    assert seen == stubs, "四个浮窗都必须被重设样式"


def test_missing_float_window_is_tolerated(glass_state):
    """浮窗未创建（None）或重设失败不应影响其余面板刷新。"""
    from zhuzhu_Copilot.ui import agent_panel as ap

    panel = ap.AgentPanel.__new__(ap.AgentPanel)
    ok = []

    class _Boom:
        def refresh_glass_qss(self):
            raise RuntimeError("构造期控件缺失")

    class _Ok:
        def refresh_glass_qss(self):
            ok.append(self)

    good = _Ok()
    panel._wt_win, panel._git_win, panel._todos_win, panel._code_win = (
        None, _Boom(), None, good)
    ap.AgentPanel._restyle_float_windows(panel)
    assert ok == [good]


def test_compact_button_is_not_squeezed():
    """外观页「选择图片 / 清除」曾被同行更高的控件挤到 16px（padding 1px）。"""
    from zhuzhu_Copilot.ui import agent_panel as ap
    from zhuzhu_Copilot.ui.tokens import SPACING_XS

    assert "padding: 1px" not in ap._BTN_COMPACT
    assert f"padding: {SPACING_XS}px" in ap._BTN_COMPACT


def test_general_page_has_no_duplicate_layout():
    """通用页曾把同一个 theme_row 布局 addLayout 两次（旧版残留代码）。"""
    from pathlib import Path

    from zhuzhu_Copilot.ui import agent_panel as ap

    src = Path(ap.__file__).read_text(encoding="utf-8")
    assert src.count("lay.addLayout(theme_row)") == 1
