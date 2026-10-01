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


def test_focus_hint_is_glow_not_blue_border(glass_state, offscreen_app):
    """输入框聚焦提示 = 透明边缘 + 边缘泛光（不再用蓝色实线边框）。"""
    from PyQt6.QtWidgets import QLineEdit

    from zhuzhu_Copilot.ui import agent_panel as ap

    # 1) 源码里不得再有「:focus + 不透明实线边框」的写法（光边要经 _focus_edge）
    from pathlib import Path
    src = Path(ap.__file__).read_text(encoding="utf-8")
    bad = [ln.strip() for ln in src.splitlines()
           if ":focus" in ln and "border: 1px solid {" in ln
           and "_focus_edge" not in ln and "transparent" not in ln]
    assert not bad, f"仍有聚焦实线边框：{bad[:3]}"

    # 2) 聚焦时真的挂上泛光，失焦要立刻摘掉（否则一直走离屏渲染）。
    #    离屏平台下未显示的控件不会收到真实的 FocusIn，直接投递焦点事件。
    from PyQt6.QtCore import QEvent
    from PyQt6.QtGui import QFocusEvent

    le = QLineEdit()
    ap._install_focus_glow(le)
    offscreen_app.sendEvent(le, QFocusEvent(QEvent.Type.FocusIn))
    eff = le.graphicsEffect()
    assert eff is not None and eff.blurRadius() >= 8, "聚焦未产生边缘泛光"
    offscreen_app.sendEvent(le, QFocusEvent(QEvent.Type.FocusOut))
    assert le.graphicsEffect() is None, "失焦后泛光未移除"


def test_popup_and_menu_use_frosted_surface(glass_state):
    """下拉 / 菜单弹出层底必须走磨砂入口，不得退回深色面板常量。"""
    from pathlib import Path

    from zhuzhu_Copilot.ui import agent_panel as ap

    src = Path(ap.__file__).read_text(encoding="utf-8")
    bad = [ln.strip() for ln in src.splitlines()
           if ("QAbstractItemView" in ln or "QMenu {{" in ln)
           and "background: {PANEL}" in ln]
    assert not bad, f"弹出层仍是深色面板常量：{bad[:3]}"
    assert "_popup_bg = _glass_tip_bg()" in src


def test_focus_keeps_a_glowing_edge(glass_state):
    """聚焦 = **保留边缘**的磨砂玻璃高光边 + 外发光（不是取消边缘，也不用品牌蓝）。"""
    from pathlib import Path

    from zhuzhu_Copilot.ui import agent_panel as ap

    glass_state.set_fields(persist=False, enabled=True)
    edge = ap._focus_edge()
    assert edge.startswith("rgba(255, 255, 255"), f"应是玻璃白高光而非品牌蓝：{edge}"
    assert 0.4 <= _alpha(edge) <= 1.0, edge
    src = Path(ap.__file__).read_text(encoding="utf-8")
    n = sum(1 for ln in src.splitlines() if ":focus" in ln and "_focus_edge" in ln)
    assert n >= 5, f"聚焦光边接入点太少：{n}"


def test_popup_glass_is_translucent(glass_state):
    """下拉 / 菜单弹出层底必须是**真半透明**。

    原先走 frost_surface_color：它是把壁纸平均色压在主题深色底上混出的**实色**，
    壁纸偏深时混出来还是深色块 —— 这就是「下拉菜单没有磨砂玻璃材质」的根因。
    """
    from zhuzhu_Copilot.ui import agent_panel as ap

    glass_state.set_fields(persist=False, enabled=False)
    assert ap._popup_glass().lower() == ap.PANEL.lower(), "玻璃关闭应回退不透明主题色"
    glass_state.set_fields(persist=False, enabled=True)
    css = ap._popup_glass()
    assert css.startswith("rgba("), css
    assert 0.4 <= _alpha(css) < 0.95, f"弹出层应半透明（可透出下方内容）：{css}"


def test_popup_frost_targets_window_not_view(glass_state, offscreen_app):
    """半透明属性必须设在弹出**顶层窗口**上。

    反例（上一版的 bug）：对 view 自身调 setWindowFlags，会把它从 Qt 的 popup 容器里
    "拆"成独立窗口，破坏 popup 结构 —— 表现是弹出一块没有内容的纯深色矩形。
    """
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QComboBox

    from zhuzhu_Copilot.ui import agent_panel as ap

    combo = QComboBox()
    ap._harden_combo_popup(combo)
    assert combo._glass_popup_filter is not None, "未挂上弹出玻璃化过滤器"
    view = combo.view()
    before = view.windowFlags()
    ap._frost_popup_view(view)
    assert view.windowFlags() == before, "不得改动 view 自身的窗口标志"
    assert view.window().testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)


def test_gsurface_and_gfill_follow_glass_switch(glass_state):
    """大表面 / 小控件的取色入口：玻璃关=不透明原色，开=半透明玻璃。"""
    from zhuzhu_Copilot.ui import agent_panel as ap

    glass_state.set_fields(persist=False, enabled=False)
    assert ap._gsurface(ap.PANEL).lower() == ap.PANEL.lower()
    assert ap._gfill(ap.PANEL).lower() == ap.PANEL.lower()

    glass_state.set_fields(persist=False, enabled=True)
    for fn in (ap._gsurface, ap._gfill):
        assert fn(ap.PANEL).startswith("rgba("), "玻璃开启后必须是半透明玻璃填充"


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
        qss = target.styleSheet()
        assert "background: transparent" in qss, "容器底应完全透明（禁止深色附着）"
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


def test_restyle_targets_real_panel_attributes():
    """`_restyle_float_windows` 里写的名字必须是 AgentPanel 真实的浮窗属性。

    真实缺陷（本轮）：这里曾写成 `_wt_win` 等带上划线的名字，而面板实际属性是
    `wt_win` —— 遍历一个都取不到，浮窗永远刷不到新样式（用户反馈「工作树 / Git /
    任务清单仍有残留」），而桩件测试照样通过。所以这里必须对着源码交叉验证。
    """
    import re
    from pathlib import Path

    from zhuzhu_Copilot.ui import agent_panel as ap

    src = Path(ap.__file__).read_text(encoding="utf-8")
    seg = src[src.index("def _restyle_float_windows"):]
    seg = seg[:seg.index("\n    def ", 10)]
    names = re.findall(r'"(\w*win)"', seg)
    assert len(names) == 4, f"应覆盖四个浮窗，实际 {names}"
    for n in names:
        assert f"self.{n} = " in src, f"{n} 不是 AgentPanel 的真实浮窗属性（刷新会全部失效）"


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
    panel.wt_win, panel.git_win, panel.todos_win, panel.code_win = stubs
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
    panel.wt_win, panel.git_win, panel.todos_win, panel.code_win = (
        None, _Boom(), None, good)
    ap.AgentPanel._restyle_float_windows(panel)
    assert ok == [good]


def test_settings_dialog_restyles_on_glass_toggle(glass_state, offscreen_app):
    """设置页在玻璃开关切换后必须重建页面（否则整页控件停在旧实色上）。

    真实缺陷（本轮，用户直接反馈）：开着设置页勾选「启用磨砂玻璃材质」后，只有
    根背景变成壁纸，左侧导航与右侧各子项仍是构造时的实色 —— 即「原有 UI 元素残留」。
    """
    from zhuzhu_Copilot.ui import agent_panel as ap

    glass_state.set_fields(persist=False, enabled=False)
    dlg = ap._AgentSettingsDialog(None)
    try:
        assert "rgba" not in dlg.nav.styleSheet(), "玻璃关闭时应为不透明主题色"
        glass_state.set_fields(persist=False, enabled=True)   # 订阅回调 → 重建页面
        assert "background: transparent" in dlg.nav.styleSheet(), \
            "开玻璃后设置页导航底仍是不透明色块（应透明）"
    finally:
        dlg.done(0)


def test_rebuild_pages_keeps_unsaved_input(glass_state, offscreen_app):
    """重建页面不能丢掉用户尚未保存的文本（规则/提示词等编辑框）。"""
    from zhuzhu_Copilot.ui import agent_panel as ap

    glass_state.set_fields(persist=False, enabled=False)
    dlg = ap._AgentSettingsDialog(None)
    try:
        dlg._ensure_all_pages()
        dlg.rules_edit.setPlainText("每行一条规则")
        dlg.disable_tools_edit.setPlainText("grep")
        dlg._rebuild_pages()
        assert dlg.rules_edit.toPlainText() == "每行一条规则"
        assert dlg.disable_tools_edit.toPlainText() == "grep"
    finally:
        dlg.done(0)


def test_settings_pages_fit_viewport_width(offscreen_app):
    """每页的「最小宽度需求」必须明显小于视口宽。

    真实缺陷（用户反馈「设置页右侧文字被挤压」）：插件页那排七个按钮把最小需求顶到
    742px（视口仅 759），字号/DPI 稍大就撑宽整个 QStackedWidget，把所有页的右侧内容
    （外观页的滑杆数值等）推出视口裁掉。按钮排改 FlowLayout 后降到 168px。
    这里留 25% 余量做底线，防止将来又加长按钮排。
    """
    from pathlib import Path as _P

    from PyQt6.QtGui import QFontDatabase

    from zhuzhu_Copilot.ui import agent_panel as ap

    # offscreen 平台无字体 → 度量会偏小，显式加载系统中文字体后再量
    for f in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simsun.ttc"):
        if _P(f).is_file():
            QFontDatabase.addApplicationFont(f)

    dlg = ap._AgentSettingsDialog(None)
    try:
        dlg.resize(991, 687)
        dlg._ensure_all_pages()
        vp = dlg._page_scroll.viewport().width()
        worst, worst_name = 0, ""
        for i in range(dlg.stack.count()):
            need = dlg.stack.widget(i).minimumSizeHint().width()
            if need > worst:
                worst = need
                worst_name = ap._NAV_ITEMS[i][0] if i < len(ap._NAV_ITEMS) else str(i)
        assert worst <= vp * 0.75, \
            f"「{worst_name}」页最小宽度需求 {worst} 逼近视口 {vp}，字号稍大就会裁掉右侧"
    finally:
        dlg.done(0)


def _alpha(css: str) -> float:
    """从 rgba(...) 串里取 alpha。"""
    return float(css.rsplit(",", 1)[1].rstrip(")"))


def _is_light_or_clear(css: str) -> bool:
    """背景色是「透明」或「白色系半透明」→ 不含深色附着（用户硬性要求）。"""
    c = (css or "").strip().lower()
    if c in ("transparent", "none", ""):
        return True
    if c.startswith("rgba("):
        parts = [float(x) for x in c[5:].rstrip(")").split(",")[:3]]
        return len(parts) == 3 and min(parts) >= 128
    if c.startswith("#") and len(c) == 7:
        return min(int(c[i:i + 2], 16) for i in (1, 3, 5)) >= 128
    return False


def test_gsurface_and_gfill_follow_glass_switch(glass_state):
    """取色入口：玻璃关回退不透明主题色；开则**容器透明 / 控件白色洗色**（禁止深色附着）。"""
    from zhuzhu_Copilot.ui import agent_panel as ap

    glass_state.set_fields(persist=False, enabled=False)
    assert ap._gsurface(ap.PANEL).lower() == ap.PANEL.lower()
    assert ap._gfill(ap.PANEL).lower() == ap.PANEL.lower()

    glass_state.set_fields(persist=False, enabled=True)
    assert ap._gsurface(ap.PANEL) == "transparent", "容器类表面必须完全透明（不得深色附着）"
    assert _is_light_or_clear(ap._gfill(ap.PANEL)), "小控件底必须是白色洗色而非深色"


def test_control_wash_is_white_not_dark(glass_state):
    """小控件补底是**白色**洗色且足够淡（否则又变成一块可见色块）。"""
    from zhuzhu_Copilot.ui import agent_panel as ap

    glass_state.set_fields(persist=False, enabled=True)
    wash = ap._gfill(ap.PANEL)
    assert wash.startswith("rgba(255, 255, 255"), wash
    assert 0.0 < _alpha(wash) <= 0.35, f"洗色过重：{wash}"


def test_interactive_states_are_white_and_not_dark(glass_state):
    """悬停 / 选中 / 附着态一律白色半透明（用户要求 40% 透明且非深色底）。"""
    from zhuzhu_Copilot.ui import agent_panel as ap

    glass_state.set_fields(persist=False, enabled=True)
    for css in (ap.HOVER_T, ap._sheer(0.10, ap.PANEL), ap._sheer(0.22, ap.PANEL)):
        assert _is_light_or_clear(css), f"{css} 是深色附着"
    assert ap.HOVER_T.endswith(", 0.250)"), ap.HOVER_T


def test_bubble_keeps_readable_floor_while_panels_are_clear(glass_state):
    """面板类表面完全透明；聊天气泡仍保留可读性地板（正文不能直接压在壁纸上）。"""
    from zhuzhu_Copilot.core import app_glass

    from zhuzhu_Copilot.ui import agent_panel as ap

    glass_state.set_fields(persist=False, enabled=True)
    assert ap._gsurface(ap.PANEL) == "transparent"
    bubble = app_glass.legible_fill(ap.PANEL)
    assert bubble.startswith("rgba(")
    assert _alpha(bubble) >= 0.4, "气泡缺可读性地板，正文会压在壁纸上"


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
