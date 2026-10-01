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
    """下拉 / 菜单弹出层底必须走磨砂入口，不得退回深色面板常量。

    这条曾经断言 `_popup_bg = _glass_tip_bg()` —— 但那正是"下拉仍是深色"的来源：
    `_glass_tip_bg()` = 壁纸平均色 × frost + 主题深色底 × (1-frost)，frost 偏小时
    就是一块深色板；而这里（应用级 QSS）是所有没自带 QSS 的下拉的**唯一**弹层底来源。
    现在统一走 `_popup_glass()`（与面板同一块玻璃的合成色）。
    """
    from pathlib import Path

    from zhuzhu_Copilot.ui import agent_panel as ap

    src = Path(ap.__file__).read_text(encoding="utf-8")
    bad = [ln.strip() for ln in src.splitlines()
           if ("QAbstractItemView" in ln or "QMenu {{" in ln)
           and "background: {PANEL}" in ln]
    assert not bad, f"弹出层仍是深色面板常量：{bad[:3]}"
    assert "_popup_bg = _popup_glass()" in src, "应用级弹层底未走磨砂玻璃入口"
    dark = [ln.strip() for ln in src.splitlines()
            if ("QAbstractItemView" in ln or "QMenu {{" in ln
                or "QMenu { {" in ln) and "_glass_tip_bg()" in ln]
    assert not dark, f"弹出层仍用深色实色入口（_glass_tip_bg 只该给 Tooltip）：{dark[:3]}"


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
    """下拉 / 菜单弹出层底必须是**真半透明**，且底色取「与面板同一块玻璃」的合成色。

    只半透明不够：早先底是 `frost_surface_color()`（壁纸平均色压在主题深色底上混出的
    实色），frost 偏小时就是深色块 —— 这才是「下拉菜单是深色背景」的根因。
    现在 rgb 部分由 `_frost_surface()` 给出（见 `test_frost_surface_matches_what_the_panel_actually_renders`），
    这里只管「保留透明度」这一半契约。
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


def test_confirm_box_colors_are_unconditional(glass_state):
    """确认弹窗（删除对话等）的取色不得放进条件分支。

    回归：`_box_bg` / `_btn_bg` / `_btn_bd` 原先只在 `if _glass_chrome():` 里赋值，
    玻璃关闭时点「删除对话」→ UnboundLocalError 崩溃。
    这三个取色入口内部已处理「玻璃关闭回退主题色」，无条件调用即可。
    """
    from pathlib import Path

    from zhuzhu_Copilot.ui import agent_panel as ap

    src = Path(ap.__file__).read_text(encoding="utf-8")
    lines = src.splitlines()
    assert "_box_bg = _popup_glass()" in src, "确认弹窗底色未走玻璃入口"
    for i, ln in enumerate(lines):
        if "_box_bg = _popup_glass()" in ln:
            prev = lines[max(0, i - 1)].strip()
            assert not prev.startswith("if "), f"取色被放回条件分支：{prev}"

    # 三个入口本身在玻璃关/开两种状态下都必须有值（不会返回 None）
    for enabled in (False, True):
        glass_state.set_fields(persist=False, enabled=enabled)
        assert ap._popup_glass() and ap._gfill(ap.AI_BG) and ap._gedge()


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


def _popup_luma_and_colors(img) -> tuple:
    """抓图 → (平均亮度, 颜色分桶数)；离屏无字形，按粗网格采样即可。"""
    lums, colors = [], set()
    for y in range(0, img.height(), 3):
        for x in range(0, img.width(), 3):
            r, g, b, _ = img.pixelColor(x, y).getRgb()
            lums.append((r + g + b) / 3.0)
            colors.add((r // 16, g // 16, b // 16))
    return sum(lums) / max(1, len(lums)), len(colors)


def _mean_of(css: str) -> float:
    """颜色串 → 平均亮度（#RRGGBB / #AARRGGBB / rgba(r,g,b,a) 都支持）。"""
    from PyQt6.QtGui import QColor
    return sum(QColor(css).getRgb()[:3]) / 3.0


def _bright_wallpaper(tmp_path):
    """亮色壁纸（左暖橙、右青蓝）：亮/暗在像素上一眼可分，用于玻璃类断言。"""
    from PyQt6.QtGui import QColor, QImage, QLinearGradient, QPainter

    wall = tmp_path / "wall.png"
    img = QImage(1280, 800, QImage.Format.Format_ARGB32)
    p = QPainter(img)
    g = QLinearGradient(0, 0, 1280, 0)
    g.setColorAt(0.0, QColor("#FFD08A"))
    g.setColorAt(1.0, QColor("#7FD8E8"))
    p.fillRect(img.rect(), g)
    p.end()
    img.save(str(wall))
    return wall


def test_frost_surface_matches_what_the_panel_actually_renders(glass_state, tmp_path):
    """「合成后的玻璃面底色」必须等于「模糊壁纸 ⊕ 磨砂纱」，而不是深色主题底。

    这是「下拉仍是深色 / 正文闪黑」的共同根因：
    `frost_surface_color()` = 壁纸平均色 × frost + 主题底色 × (1-frost)，frost 偏小
    （用户可能调到接近 0）时结果 ≈ **纯深色主题底**（实测 #1F2227）；而面板真正渲染的是
    `_paint_glass_root()` = 模糊壁纸 + `root_veil_color()` 的纱，亮壁纸下是**中浅色**
    （实测 #7a8679）。两者亮度差 4 倍，凡是拿前者当「面板该有的样子」的浮层必然是深色。
    """
    from PyQt6.QtGui import QColor

    from zhuzhu_Copilot.core import app_glass

    from zhuzhu_Copilot.ui import agent_panel as ap

    glass_state.set_fields(persist=False, enabled=True,
                           bg_image=str(_bright_wallpaper(tmp_path)))
    # 用深色主题复现用户现场（截图就是深色 + 亮壁纸）：旧算法在这种组合下退化成
    # 深色主题底，而面板实际渲染出来的是「亮壁纸压纱」的浅色。
    ap._apply_colors(ap._THEMES["dark"], force=True, bump_version=False)
    try:
        surf = ap._frost_surface()
        veil = app_glass.root_veil_color(ap.PANEL)
        tint = app_glass.wallpaper_tint()
        a = veil.alphaF()
        want = QColor(*(int(round(c * (1 - a) + v * a))
                        for c, v in zip(tint.getRgb()[:3], veil.getRgb()[:3])))
        got = QColor(surf)
        assert all(abs(g - w) <= 3 for g, w in zip(got.getRgb()[:3], want.getRgb()[:3])), \
            f"合成玻璃色不对：{surf} vs 期望 {want.name()}"
        old = app_glass.frost_surface_color(ap.PANEL)
        assert _mean_of(surf) > _mean_of(old) + 30, (
            f"深色主题 + 亮壁纸下合成玻璃色仍偏暗：{surf}（旧算法 {old}）"
            "—— 弹出层底与落字遮色都会跟着变深")
    finally:
        ap.apply_theme()        # 还原成设置里的主题


def test_emerge_band_matches_the_panel_glass(glass_state, tmp_path):
    """落字浮现的遮色 == 面板那块玻璃，否则流式输出时每落一行就闪一块深色。

    回归（用户反馈「agent 输出正文时有黑色元素瞬间出现」）：遮色原取
    `frost_surface_color(PANEL)`（≈深色主题底），而正文坐在中浅色的玻璃面板上。
    遮色**必须不透明**（要盖住旧字），所以只能靠「取值贴近真实底色」来消隐。
    """
    from PyQt6.QtGui import QColor

    from zhuzhu_Copilot.ui import agent_panel as ap

    glass_state.set_fields(persist=False, enabled=True,
                           bg_image=str(_bright_wallpaper(tmp_path)))
    band = ap._glass_band()
    assert QColor(band).alpha() == 255, "遮色必须不透明，否则盖不住旧字"
    assert abs(_mean_of(band) - _mean_of(ap._frost_surface())) <= 2, \
        f"遮色 {band} 与面板玻璃 {ap._frost_surface()} 不一致 → 会闪色块"

    glass_state.set_fields(persist=False, enabled=False)
    assert ap._glass_band().lower() == ap.BG.lower(), "玻璃关闭时应回退主题底色"


def test_combo_popup_is_the_same_glass_as_the_panel(glass_state, offscreen_app, tmp_path):
    """下拉弹出层必须与面板是**同一块玻璃**，不能是一块深色板 —— 用像素判，不看 QSS 串。

    用户先后四次反馈「下拉菜单仍然是深色背景 / 鼠标附着样式仍为深色」。只断言
    `_popup_glass()` 是 rgba 半透明根本不成立：弹出层是独立顶层窗口，Qt 默认把它填成
    实色，QAbstractItemView 的样式底还会把自绘玻璃盖住 —— 所以只能抓真实像素来判。
    这里走真实链路：`_QCOMBO` 样式 + `_harden_combo_popup` + showPopup + 抓弹层窗口。
    """
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QComboBox

    from zhuzhu_Copilot.ui import agent_panel as ap

    glass_state.set_fields(persist=False, enabled=True,
                           bg_image=str(_bright_wallpaper(tmp_path)))
    ap.refresh_glass()          # 换壁纸后必须重建派生 QSS（`_QCOMBO` 是缓存串）
    panel_luma = _mean_of(ap._frost_surface())

    combo = QComboBox()
    combo.setStyleSheet(ap._QCOMBO)
    combo.addItems(["自动选择", "agnes-2.5-flash", "deepseek-flash", "deepseek-v4-pro"])
    combo.resize(240, 32)
    combo.show()
    ap._harden_combo_popup(combo)
    for _ in range(60):
        offscreen_app.processEvents()
    combo.showPopup()
    for _ in range(120):
        offscreen_app.processEvents()

    view = combo.view()
    assert view.isVisible(), "弹出层没显示，抓图无意义"
    assert view.window().testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground), \
        "弹出层未设半透明窗口属性：Qt 会把它填成实色，玻璃不可能透出"

    luma, ncol = _popup_luma_and_colors(view.window().grab().toImage())
    try:
        combo.hidePopup()
        combo.close()
    except Exception:
        pass
    for _ in range(20):
        offscreen_app.processEvents()

    assert ncol >= 5, f"弹出层几乎是一块纯色板（颜色种类 {ncol}）"
    assert luma >= panel_luma * 0.75, (
        f"弹出层比面板暗太多：弹层平均亮度 {luma:.1f}，面板玻璃 {panel_luma:.1f}"
        "（说明弹层底还是深色算法 / 自绘玻璃没上屏）")
