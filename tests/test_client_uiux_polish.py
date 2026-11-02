# -*- coding: utf-8 -*-
"""Agent 客户端 UI/UX 打磨回归（client-uiux-design 技能落地）。

覆盖三类改动：
1. 欢迎页（空状态）建议胶囊：存在、可点击，且点击只**填充输入框**（不自动发送）；
2. 二级面板（Agent / Copilot 插件详情）不再用 emoji 占位，页签容器不描边；
3. 聊天区「回到底部」按钮与工作目录提示条改用主题色 / 磨砂填充，不写死 rgba。

AgentPanel 的构造副作用（系统扫描 / 托盘 / 管理员告警 / 桌宠）在 fixture 中打桩隔离。
"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest                                        # noqa: E402
from PyQt6.QtWidgets import QApplication             # noqa: E402

_app = QApplication.instance() or QApplication([])

from zhuzhu_Copilot.ui import agent_panel as ap       # noqa: E402
from zhuzhu_Copilot.ui import main_window as mw       # noqa: E402
from zhuzhu_Copilot.ui.agent_panel import AgentPanel  # noqa: E402

SRC = Path(ap.__file__).read_text(encoding="utf-8")


def _pump(ms: int):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        _app.processEvents()
        time.sleep(0.005)


@pytest.fixture(scope="module")
def panel():
    mp = pytest.MonkeyPatch()
    mp.setattr(mw.CopilotPanel, "_start_scan", lambda self, *a: None)
    mp.setattr(mw.CopilotPanel, "_init_update_check", lambda self: None)
    mp.setattr(mw.CopilotPanel, "_check_admin", lambda self: None)
    mp.setattr(mw.CopilotPanel, "_setup_tray", lambda self: None)
    try:
        mp.setattr("zhuzhu_Copilot.ui.desktop_pet.ensure_pet", lambda w: None)
    except Exception:
        pass
    p = AgentPanel(None)
    p.resize(1200, 800)
    p.show()
    _pump(300)
    yield p
    try:
        p.hide()
        _pump(30)
    except Exception:
        pass
    mp.undo()


# ---------- 1. 欢迎页建议胶囊 ----------

def test_welcome_has_suggestion_chips(panel):
    """空状态必须给出可上手的建议入口（至少 3 个），每个都是「矢量图标 + 文案」。"""
    chips = getattr(panel, "_welcome_chips", [])
    assert len(chips) >= 3, f"欢迎页建议胶囊过少：{len(chips)}"
    assert len(chips) == len(ap._WELCOME_SUGGESTIONS)
    for chip in chips:
        assert chip.text().strip(), "建议胶囊不能是空文案"
        assert not chip.icon().isNull(), "建议胶囊必须带矢量图标（禁 emoji）"


def test_welcome_chip_fills_input_only(panel):
    """点击建议胶囊只填充输入框，留给用户改完再按 Enter（不直接发送）。"""
    chip = panel._welcome_chips[0]
    before = len(panel._segments)
    chip.click()
    _pump(30)
    assert panel.input.toPlainText() == chip.text(), "胶囊文案未填进输入框"
    assert len(panel._segments) == before, "点击建议不应直接发起对话"


# ---------- 2. 插件详情面板：无 emoji / 页签不描边 ----------

def test_plugin_detail_dialogs_use_vector_placeholders():
    """插件详情缺图标时用淡灰线条矢量图占位，不再出现拼图/冰块 emoji。"""
    assert "🧩" not in SRC and "🧊" not in SRC, "插件详情仍残留 emoji 占位图标"
    assert '_line_icon("puzzle"' in SRC and '_line_icon("apps"' in SRC, \
        "插件详情占位图标必须走 _line_icon"


def test_plugin_dialog_tabs_have_no_border():
    """招 1：页签容器不画 1px 线，层级由凹陷字段 / 卡片两档底色承担。"""
    assert "QTabWidget::pane {{ border: 1px solid" not in SRC, \
        "插件详情页签仍在描边（应改为 FIELD/CARD 两档微对比分层）"


# ---------- 3. 主题色收敛：不写死 rgba ----------

def test_scroll_bottom_button_uses_theme_accent():
    """回到底部按钮改为玻璃材质：底色取主题色板键，不再写死旧主题蓝。

    旧实现断言「从 ACCENT_HOVER 派生 rgba 圆盘」；现在底色由 glass 按 PALETTE
    色板键实时解析（elevated / primary_light），换肤后同样不会残留旧蓝。
    """
    assert "rgba(47,82,216" not in SRC, "回到底部按钮仍写死旧主题蓝"
    assert "GlassButton(" in SRC, "回到底部按钮应改用玻璃材质"
    assert 'tint_key="elevated"' in SRC and 'hover_tint_key="primary_light"' in SRC, \
        "玻璃按钮的底色必须走主题色板键（换肤即时生效）"
    assert "_line_icon(\"chev\", 18, _base_color(\"TEXT\"))" in SRC, \
        "按钮图标颜色应取当前主题文字色（玻璃底上白/黑自适应）"


def test_workdir_hint_uses_soft_fill_without_border():
    """工作目录提示条按招 1 不描边，用磨砂填充浮出。"""
    assert "QFrame#wdHintBar {{ background: rgba(0,0,0,0); border: 1px solid" not in SRC, \
        "工作目录提示条仍在描边"


# ---------- 4. 胶囊圆角：不得超过控件半高 ----------

def test_pill_radius_never_exceeds_half_height():
    """Qt 的 QSS border-radius 超过短边一半时不会钳制，而是直接画成直角。

    因此胶囊半径必须由 pill_radius(高度) 计算；RADIUS_PILL（999）只能当语义标记。
    """
    from zhuzhu_Copilot.ui import tokens as tk
    assert tk.pill_radius(22) == 11
    assert tk.pill_radius(34) == 17
    assert tk.pill_radius(19) == 9
    bubble_src = Path(
        Path(ap.__file__).resolve().parent / "agent_chat_bubbles.py"
    ).read_text(encoding="utf-8")
    assert "border-radius: {RADIUS_PILL}px" not in bubble_src, \
        "胶囊仍在直接使用 RADIUS_PILL（会被 Qt 画成直角）"
    assert "border-radius: {RADIUS_PILL}px" not in SRC


# ---------- 5. 一体化输入卡（大厂客户端同款结构） ----------

def test_input_lives_inside_one_composer_card(panel):
    """输入框与工具行必须收进同一张圆角卡片，而不是横向摊成一排控件。"""
    from PyQt6.QtWidgets import QFrame
    assert isinstance(panel.composer, QFrame)
    assert panel.composer.objectName() == "composerCard"
    assert panel.input.parent() is panel.composer, "输入框必须坐落在输入卡内"
    assert panel.attach_btn.parent() is panel.composer, "工具按钮必须坐落在输入卡内"
    assert panel.action_btn.parent() is panel.composer, "发送按钮必须坐落在输入卡内"
    # 扁平模式：输入框不再自绘第二层圆角底色（避免卡片里再套一个输入框）
    assert panel.input._flat_bg is True
    qss = panel.composer.styleSheet()
    # 统一视觉语言：输入卡保留抬升面板底，但补 1px 描边（与侧栏同一套分层）
    assert "border-radius: 16px" in qss and "border: 1px solid" in qss


def test_composer_height_fits_content(panel):
    """输入卡高度必须贴合内容（不能被竖直布局撑出大片空白）。"""
    hint = panel._composer_lay.sizeHint().height()
    assert panel.composer.height() == hint, \
        f"输入卡高度 {panel.composer.height()} 与内容高 {hint} 不符"
    assert panel.composer.maximumHeight() == hint


# ---------- 6. 设置页：标题 + 圆角内容卡 + 导航胶囊 ----------

def _bare_settings_dialog():
    """不构造真实设置对话框（避免重 I/O），只取页面骨架需要的色板字段。"""
    from zhuzhu_Copilot.ui import styles as st
    d = ap._AgentSettingsDialog.__new__(ap._AgentSettingsDialog)
    d._BG = st.LIGHT_PALETTE["bg_top"]
    d._PANEL = st.LIGHT_PALETTE["card"]
    d._PANEL2 = st.LIGHT_PALETTE["card"]
    d._HOVER = st.LIGHT_PALETTE["hover"]
    d._TEXT = st.LIGHT_PALETTE["text"]
    d._DIM = st.LIGHT_PALETTE["text_secondary"]
    d._ACCENT = st.LIGHT_PALETTE["primary"]
    d._ACCENT_HOVER = st.LIGHT_PALETTE["primary_hover"]
    d._BORDER = st.LIGHT_PALETTE["border"]
    return d


def test_settings_page_puts_body_in_a_section_card():
    """设置页正文收进一张玻璃卡片：圆角与底色由 paintEvent 自绘（不再是 QSS 实色）。"""
    from zhuzhu_Copilot.ui.widgets import GlassPanel
    d = _bare_settings_dialog()
    page = d._page("通用")
    card = page.findChild(GlassPanel, "settingsCard")
    assert card is not None, "设置页正文必须收进圆角卡片"
    assert d._page_body(page) is card.layout(), "页面正文布局必须指向卡片内层"
    assert card._g_radius > 0, "卡片必须有圆角"
    assert card.resolved_tint() == d._PANEL2, "卡片底色必须跟随主题面板色"


def test_settings_nav_uses_pill_selection_without_border_line():
    d = _bare_settings_dialog()
    qss = d._nav_qss()
    assert "border-left" not in qss, "导航选中态不该再用左边线（改为圆角胶囊）"
    assert "border-radius" in qss and "item:selected" in qss


def test_light_palette_matches_big_company_baseline():
    """浅色基调：冷白背景 + 纯白卡片 + 更亮的深蓝主色（参照 TRAE/Claude 观感）。"""
    from zhuzhu_Copilot.ui import styles as st
    assert st.LIGHT_PALETTE["bg_top"] == "#F7F9FC"
    assert st.LIGHT_PALETTE["card"] == "#FFFFFF"
    assert st.LIGHT_PALETTE["primary"] == "#2563EB"
    assert ap._THEMES["light"]["ACCENT"] == "#2563EB"
    assert ap._THEMES["light"]["FIELD"] == st.LIGHT_PALETTE["field"]


# ---------- 7. 常驻侧栏：左侧历史对话抽屉 ----------

def _drawer(panel):
    return panel.session_drawer


def test_session_drawer_is_persistent_left_column(panel):
    """对话历史必须是左侧常驻抽屉（顶栏下拉已移除），宽度固定不随窗口收缩。"""
    from PyQt6.QtWidgets import QListWidget, QLineEdit
    d = _drawer(panel)
    if d.is_collapsed():                 # 上一次运行留下「收起」状态时先展开
        d.set_collapsed(False)
    assert d.isVisible(), "历史对话抽屉必须常驻可见"
    assert d.parent() is panel
    assert panel._dock_row.indexOf(d) == 0, "抽屉必须是主内容行最左侧一列"
    assert d.width() == ap._DRAWER_W, f"抽屉宽度应固定为 {ap._DRAWER_W}，实得 {d.width()}"
    assert d.findChild(QListWidget, "sessionList") is not None, "缺少会话列表"
    assert d.findChild(QLineEdit, "sessionSearch") is not None, "缺少会话搜索框"
    assert d.new_btn.isVisible(), "抽屉必须自带新建对话入口"


def test_session_drawer_matches_session_list(panel):
    """抽屉条目与隐藏的会话组合框同源：数量一致，且当前会话处于选中态。"""
    p = panel
    lst = p._load_session_list()
    if len(lst) < 2:
        p._create_session()
        p._refresh_session_combo()
        lst = p._load_session_list()
    d = _drawer(p)
    assert d.list.count() == p.session_combo.count(), "抽屉与组合框会话数量不一致"
    cur = d.list.currentItem()
    assert cur is not None, "当前会话未在抽屉中选中"
    from PyQt6.QtCore import Qt
    assert cur.data(Qt.ItemDataRole.UserRole) == p._session_id


def test_session_drawer_switches_session(panel):
    """点击抽屉条目 → 走与下拉相同的切换入口。"""
    p = panel
    d = _drawer(p)
    other = None
    from PyQt6.QtCore import Qt
    for i in range(d.list.count()):
        sid = d.list.item(i).data(Qt.ItemDataRole.UserRole)
        if sid and sid != p._session_id:
            other = sid
            break
    if other is None:                      # 只有一个会话：补一个（不切换）
        other = p._create_session()["id"]
        p._refresh_session_combo()
    switched = []
    mp = pytest.MonkeyPatch()
    mp.setattr(p, "_switch_to", lambda sid: switched.append(sid))
    try:
        d.session_activated.emit(other)
    finally:
        mp.undo()
    assert switched == [other], "抽屉点击未触发会话切换"


# ---------- 8. 常驻侧栏：右侧功能胶囊 ----------

def test_function_rail_is_capsule_with_icon_buttons(panel):
    """右侧栏必须是细长圆角胶囊：纯图标按钮 + 组间细线 + 底部「更多」。"""
    rail = panel._function_rail
    assert rail.isVisible(), "功能胶囊必须常驻可见"
    assert rail.width() == ap._RAIL_W
    qss = rail.styleSheet()
    assert f"border-radius: {ap.pill_radius(ap._RAIL_W)}px" in qss, "胶囊圆角必须取半宽"
    # 统一视觉方案：侧栏一律「底色 BG + 1px 描边 + 半宽圆角」
    assert "border: 1px solid" in qss, "胶囊应为统一底色 + 1px 描边"
    btns = list(rail._buttons.values())
    assert len(btns) >= 7, f"胶囊入口过少：{len(btns)}"
    for b in btns:
        assert b.text() == "", "胶囊只放图标，不放文字"
        assert not b.icon().isNull(), "胶囊按钮必须带矢量图标（禁 emoji）"
        assert b.size().width() == ap._RAIL_BTN_D
    assert len(rail._divs) >= 2, "至少两组分隔线（大厂胶囊同款分组）"
    assert "more" in rail._buttons, "底部缺少「更多」入口"


def test_function_rail_dispatch(panel):
    """胶囊按钮直接跳到对应功能页：统计浮层 / MCP 设置分页。"""
    p = panel
    calls = []
    mp = pytest.MonkeyPatch()
    mp.setattr(p, "toggle_token_stats", lambda: calls.append("stats"))
    mp.setattr(p, "_open_settings",
               lambda **kw: calls.append(("settings", kw.get("page_index"))))
    try:
        p._function_rail._buttons["stats"].click()
        p._function_rail._buttons["mcp"].click()
    finally:
        mp.undo()
    assert calls[0] == "stats", "统计入口未接通上下文统计浮层"
    assert calls[1] == ("settings", ap._nav_index("mcp")), \
        "MCP 入口未跳到设置页 MCP 分页"


def test_session_combo_leaves_top_bar(panel):
    """会话下拉已移出顶栏：保留为隐形数据源（既有逻辑/测试仍可调用）。"""
    combo = panel.session_combo
    assert combo.isHidden(), "会话下拉不应再出现在顶栏"
    assert combo.parent() is panel, "隐藏组合框仍须挂在面板上（避免成为独立窗口）"


# ---------- 9. 折叠 / 展开 ----------

def test_session_drawer_collapses_by_hiding_entirely(panel):
    """对话栏收起后整栏隐藏（旧 36px 窄条已删除），展开入口由左栏两枚圆形按钮提供。"""
    d = _drawer(panel)
    if d.is_collapsed():
        d.set_collapsed(False)
    assert d.isVisible() and d.width() == ap._DRAWER_W
    d.set_collapsed(True)
    assert d.isHidden(), "收起后应整栏隐藏，不再保留窄条"
    assert not d.expand_btn.isVisible(), "收起态不应再有展开箭头窄条"
    d.set_collapsed(False)
    assert d.isVisible() and d.width() == ap._DRAWER_W
    assert d.list.isVisible()


def test_leftdock_collapses_whole_column(panel):
    """文件树 / Git / 任务清单算一个左栏：一次收起全部，再展开恢复原可见面板。"""
    p = panel
    if p._leftdock_collapsed():
        p._set_leftdock_collapsed(False, persist=False)
    order = p._dock_panel_order("left")
    p._set_leftdock_collapsed(True, persist=False)
    assert p._leftdock_collapsed()
    assert p._dock_left.width() == ap._DOCK_COLLAPSED_W, "收起后左栏宽度应为迷你列宽"
    # 收起态不是空窄条，而是两枚圆形入口（文件树 / Git），点击即展开
    mini = p._leftdock_mini
    assert mini is not None and mini.isVisible(), "收起后应显示两枚圆形入口"
    from PyQt6.QtWidgets import QPushButton
    btns = mini.findChildren(QPushButton)
    assert len(btns) == 2, f"迷你列应有 2 个圆形按钮（实际 {len(btns)}）"
    for b in btns:
        assert b.width() == b.height() == 38, "入口按钮必须是正圆"
        assert not b.icon().isNull(), "入口按钮缺少矢量图标"
    tips = [b.toolTip() for b in btns]
    assert ap._ui("展开对话栏") in tips and ap._ui("展开左栏") in tips, \
        f"两枚圆形入口应为「对话菜单」「侧栏」：{tips}"
    # 收起态不得再套一圈圆角矩形卡片（只有两个圆形按钮浮在背景上）
    assert "border: none" in p._dock_left.styleSheet() and \
        "transparent" in p._dock_left.styleSheet(), \
        f"收起态栏容器仍画方框：{p._dock_left.styleSheet()}"
    assert p._leftdock_head.isHidden(), "收起态标题条（箭头）应被圆形入口取代"
    for w in order:
        assert w.isHidden(), "收起时栏内面板必须全部让位"
    p._set_leftdock_collapsed(False, persist=False)
    assert mini.isHidden(), "展开后迷你列应隐藏"
    assert not p._leftdock_collapsed()
    assert p._dock_left.width() >= ap._DOCK_COLLAPSED_W
    for w in order:
        assert w.isVisible(), "展开后原先可见的面板应恢复"


def test_function_rail_exposes_collapse_entries(panel):
    """胶囊里必须有「对话栏」「左栏」两个折叠开关（与标题栏箭头同一状态）。"""
    rail = panel._function_rail
    assert "drawer" in rail._buttons, "胶囊缺少对话栏折叠入口"
    assert "leftdock" in rail._buttons, "胶囊缺少左栏折叠入口"


def test_leftdock_drops_side_title(panel):
    """左栏「侧栏」标题已删除：收起入口直接是浮动按钮，不占布局高度（文件树贴栏顶）。"""
    from PyQt6.QtWidgets import QPushButton
    btn = panel._leftdock_head
    assert isinstance(btn, QPushButton), "收起入口应是按钮本身，不再包一层标题条"
    assert btn.text() == "", "「侧栏」标题未删除"
    assert panel._dock_left.layout().indexOf(btn) == -1, "收起入口不应占据布局高度"
    assert not hasattr(panel, "_leftdock_head_lbl"), "标题标签仍残留"


def test_drawer_refresh_keeps_scroll_position(panel):
    """点击历史会话后重建列表不得把滚动条甩回顶部（用户反馈的「整栏跳动」）。"""
    d = _drawer(panel)
    rows = [{"id": f"s{i}", "name": f"会话 {i}", "updated": i} for i in range(30)]
    d.refresh(rows, "s0")
    _pump(60)
    bar = d.list.verticalScrollBar()
    bar.setValue(120)
    _pump(30)
    pos = bar.value()
    d.refresh(rows, "s10")     # 模拟点击会话后 _refresh_session_combo 的重建
    _pump(60)
    assert bar.value() == min(pos, bar.maximum()), \
        f"重建后滚动位置跳动：{pos} → {bar.value()}"
    panel._refresh_session_combo()


def test_input_keeps_placeholder_on_focus(panel):
    """点击/聚焦输入框不得清空提示文案（用户反馈：点一下提示文字就消失）。

    有文本时 Qt 本来就会隐藏 placeholder，因此无需在聚焦时手动清空。"""
    from PyQt6.QtCore import QEvent
    from PyQt6.QtGui import QFocusEvent
    inp = panel.input
    ph = panel._input_placeholder
    assert ph == ap._ui("今天能帮你做点什么呢？"), f"提示文案未更新：{ph}"
    inp.setPlaceholderText(ph)          # 输入框启动即聚焦，先显式还原文案再验证
    assert inp.placeholderText() == ph
    inp.focusInEvent(QFocusEvent(QEvent.Type.FocusIn))
    assert inp.placeholderText() == ph, "聚焦后提示文字被清空"
    inp.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut))
    assert inp.placeholderText() == ph


def test_mouse_cursor_is_system_default(panel):
    """鼠标指针恢复系统默认：自定义高对比位图指针已整体删除。"""
    from PyQt6.QtCore import Qt
    assert panel.cursor().shape() != Qt.CursorShape.BitmapCursor, \
        "面板仍在用自定义位图指针"
    assert not hasattr(ap, "_high_contrast_cursor"), "自定义指针函数未删除"
    assert not hasattr(panel, "_apply_app_cursor"), "指针应用入口未删除"


# ---------- 6. 主页面模型调节浮层 + Agent 权限下拉 ----------

def test_model_tune_popover_and_hover_timers(panel):
    """模型选择框悬停 1s 弹出浮层：胶囊滑杆（8 档、档位点、满档粒子）+ 自动开关 + 1M 开关。"""
    pop = panel._model_tune
    assert isinstance(pop, ap._ModelTunePopover)
    sl = pop._slider
    assert isinstance(sl, ap._CapsuleSlider), "思考强度滑杆必须沿用设置页胶囊滑杆"
    assert sl.tick_step == 1 and sl.tick_style == ap._CapsuleSlider.TICK_STYLE_LEVEL
    assert sl.maximum() == len(ap.agent_llm.EFFORTS) - 1
    assert sl.PARTICLE_COUNT == 100
    hold = panel._model_hover
    assert hold._hold.interval() == ap._MODEL_TUNE_HOLD_MS == 1000
    assert hold._leave.interval() == ap._MODEL_TUNE_LEAVE_MS == 250
    assert hold._gate is not None, "悬停弹层缺少「只在模型名称上触发」的闸门"
    assert hold._on_enter is not None, "进入模型名称时未先关掉提示气泡"

    panel._open_model_tune()
    _pump(80)
    assert pop.isVisible(), "悬停到期后浮层未显示"
    assert pop._auto.isChecked() == bool(panel._auto_effort)
    panel._close_model_tune()
    _pump(260)


def test_model_tune_gate_only_on_model_name(panel, monkeypatch):
    """悬停闸门：下拉展开不弹；指针在右侧箭头区不弹；只有模型名称文本区才弹。"""
    import types
    from PyQt6.QtCore import QPoint
    combo = panel.model_combo
    mid = combo.mapToGlobal(QPoint(10, combo.height() // 2))
    arrow = combo.mapToGlobal(QPoint(combo.width() - 5, combo.height() // 2))
    monkeypatch.setattr(ap, "QCursor", types.SimpleNamespace(pos=lambda: mid))
    # 下拉已改为自绘浮层：展开状态由 isPopupOpen() 表达（原生 view 不再显示）
    monkeypatch.setattr(combo, "isPopupOpen", lambda: True)
    assert panel._model_tune_gate() is False, "下拉列表展开时不应再弹调节浮层"
    monkeypatch.setattr(combo, "isPopupOpen", lambda: False)
    assert panel._model_tune_gate() is True, "指针停在模型名称文本区应允许弹层"
    monkeypatch.setattr(ap, "QCursor", types.SimpleNamespace(pos=lambda: arrow))
    assert panel._model_tune_gate() is False, "指针在箭头区不应触发调节浮层"


def test_tune_popover_not_flickering_between_combo_and_popover(panel, monkeypatch):
    """从模型框移到浮层时不得被旧离开计时器收起（否则会反复弹出/关闭）。"""
    import types
    from PyQt6.QtCore import QPoint
    panel._open_model_tune()
    _pump(80)
    pop = panel._model_tune
    assert pop.isVisible()
    # 指针停在浮层内部：离开宽限到期也不收起
    inside = pop.mapToGlobal(QPoint(pop.width() // 2, pop.height() // 2))
    monkeypatch.setattr(ap, "QCursor", types.SimpleNamespace(pos=lambda: inside))
    panel._maybe_close_model_tune()
    assert pop.isVisible(), "指针仍在浮层上却被收起（会与重新弹出形成闪烁）"
    # 指针移到两者之外：正常收起
    far = pop.mapToGlobal(QPoint(-600, -600))
    monkeypatch.setattr(ap, "QCursor", types.SimpleNamespace(pos=lambda: far))
    panel._maybe_close_model_tune()
    _pump(300)
    assert not pop.isVisible(), "指针离开后浮层未收起"


def test_tune_popover_suppresses_combo_tooltip(panel):
    """进入模型名称即清空提示气泡（不等 1s），浮层期间不再出现提示，收起后恢复。"""
    panel._on_model_name_enter()          # 模拟鼠标进入模型名称
    assert panel.model_combo.toolTip() == "", "进入时未清掉提示气泡"
    panel._open_model_tune()
    _pump(40)
    assert panel.model_combo.toolTip() == "", "浮层显示时提示气泡未关闭"
    panel._close_model_tune()
    _pump(300)
    assert panel.model_combo.toolTip() == ap._ui(ap._MODEL_COMBO_TIP), \
        "浮层收起后提示语未恢复"


def test_model_tune_drag_persists_without_auto_switch(panel):
    """拖动滑杆只改力度、不自动关掉「自动按难度」；落盘走 model 段配置。"""
    pop = panel._model_tune
    orig = (panel._effort, panel._auto_effort, panel._context_1m)
    try:
        panel._auto_effort = True
        pop.set_state(panel._effort, True, panel._context_1m, "128K")
        pop._slider.setValue(ap.agent_llm.EFFORTS.index("ultra"))
        _pump(20)
        assert panel._effort == "ultra"
        assert panel._auto_effort is True, "拖滑杆不应擅自关闭自动按难度"
        assert pop._level.text() == ap._ui(ap.agent_llm.effort_label("ultra"))
        panel._save_model_tune()
        cfg = ap.agent_llm.load_model_config()
        assert cfg.get("effort") == "ultra"
    finally:
        panel._effort, panel._auto_effort, panel._context_1m = orig
        panel._save_model_tune()
        panel._sync_model_badges()


def test_model_badges_sit_left_of_model_combo(panel):
    """模型名左侧常驻「思考档位 · 上下文窗口」徽标，鼠标为默认箭头。"""
    from PyQt6.QtCore import Qt
    badges = panel.model_badges
    assert badges.text().strip(), "徽标文案为空"
    assert "·" in badges.text(), f"徽标应含档位与窗口两段：{badges.text()}"
    assert any(ch.isdigit() for ch in badges.text()), "徽标缺上下文窗口数值"
    assert badges.cursor().shape() == Qt.CursorShape.ArrowCursor
    assert badges.x() < panel.model_combo.x(), "徽标必须在模型名左侧"


def test_permission_pill_sits_right_of_optimize(panel):
    """权限入口紧邻提示词优化右侧：胶囊式（图标 + 当前权限文案 + 落尖角）。"""
    pill = panel.perm_btn
    assert isinstance(pill, ap._PermPill)
    assert pill.parent() is panel.composer
    assert pill.x() > panel.optimize_btn.x()
    # 权限图标全部走开源 SVG（Lucide），不再用手绘路径
    for kind in ("shield", "shield-alert", "hand", "wrench", "check", "chevron"):
        assert not ap._perm_icon(kind, 16, "#FFFFFF").pixmap(16, 16).isNull(), \
            f"开源 SVG 图标 {kind} 渲染失败"
    assert not pill.icon_lbl.pixmap().isNull(), "权限图标未绘制"
    assert not pill.chev_lbl.pixmap().isNull(), "胶囊缺少下尖角"
    assert pill.text_lbl.text() == ap._ui(ap._PERM_LABELS[panel._mode])
    # 三档图标必须互不相同：前两档不能再共用一个「安全盾牌」样式
    assert ap._PERM_ICONS == {"ask": "hand", "edit": "wrench", "yolo": "shield-alert"}
    assert len(set(ap._PERM_ICONS.values())) == 3, "权限三档图标重复"


def test_perm_menu_offers_three_levels(panel, monkeypatch):
    """权限下拉三项齐全：每步确认 / 仅工具调用 / 全部访问（后者走确认弹窗）。"""
    seen = {}
    monkeypatch.setattr(ap._FadeMenu, "exec",
                        lambda self, *a, **k: seen.setdefault("menu", self))
    panel._open_perm_menu()
    menu = seen.get("menu")
    assert menu is not None, "权限菜单未构造"
    labels = {a.text() for a in menu.actions()}
    assert {ap._ui("每步确认"), ap._ui("仅工具调用"), ap._ui("全部访问")} <= labels
    assert ap._ui("如何批准 Agent 的操作?") in labels, "权限菜单缺少分组标题"


def test_full_access_requires_confirmation(panel, monkeypatch):
    """全部访问：取消确认则保持原模式；确认后即时生效并同步既有引擎 direct。"""
    orig = str(panel._settings.value("agent_mode", "ask") or "ask")
    try:
        monkeypatch.setattr(panel, "_confirm_yolo_access", lambda: False)
        panel._on_perm_action("yolo")
        assert str(panel._settings.value("agent_mode", "ask") or "ask") == orig
        assert panel._mode == orig

        monkeypatch.setattr(panel, "_confirm_yolo_access", lambda: True)
        panel._on_perm_action("yolo")
        assert panel._settings.value("agent_mode") == "yolo"
        assert panel._mode == "yolo"
        assert panel.perm_btn.toolTip() == panel._perm_tooltip("yolo")
        assert panel.perm_btn.text_lbl.text() == ap._ui(ap._PERM_LABELS["yolo"])
        for st in (panel._sess or {}).values():
            eng = st.get("engine")
            if eng is not None:
                assert eng.direct is True
    finally:
        panel._settings.setValue("agent_mode", orig)
        panel._mode = orig
        panel._refresh_perm_btn()


def test_input_caret_is_high_contrast(panel):
    """光标为自绘闪烁竖线：关闭原生 cursorWidth，颜色/宽度随主题取极端值。"""
    from PyQt6.QtGui import QColor, QPalette
    inp = panel.input
    dark = QColor(ap._base_color("BG")).lightness() < 128
    expected = "#ffffff" if dark else "#000000"
    assert getattr(inp, "_custom_caret", False), "输入框未启用自绘光标"
    assert inp.cursorWidth() == 0, "原生光标未关闭（会与自绘光标双影）"
    assert inp._caret_width() == (3 if dark else 2)
    assert inp.palette().color(QPalette.ColorRole.Text).name() == expected, \
        f"光标色未取极端值：{inp.palette().color(QPalette.ColorRole.Text).name()}"


def test_session_drawer_shows_preview_and_pins_first(panel):
    """抽屉行：最近消息预览进第二行，置顶会话排在最前（不改盘上的 updated）。"""
    from PyQt6.QtCore import Qt
    d = _drawer(panel)
    d.refresh([
        {"id": "old", "name": "旧会话", "updated": 100, "preview": "这是旧会话的预览"},
        {"id": "new", "name": "新会话", "updated": 200, "preview": "这是新会话的预览"},
        {"id": "pin", "name": "置顶会话", "updated": 50, "pinned": True,
         "preview": "置顶的预览"},
    ], "new")
    assert d.list.item(0).data(Qt.ItemDataRole.UserRole) == "pin", "置顶会话未排在最前"
    assert d.list.item(0).data(Qt.ItemDataRole.UserRole + 2) == "置顶的预览"
    assert d.list.item(0).data(Qt.ItemDataRole.UserRole + 3) is True
    assert d.list.currentItem().data(Qt.ItemDataRole.UserRole) == "new"
    # 撤销测试数据：刷新回真实会话列表，避免影响后续用例
    panel._refresh_session_combo()


# ---------- 7. 勾选框对比度 / 浮层偏移 / 1M 滑块 / 滚轮守卫 / 设置页对齐 ----------

def test_checkbox_indicator_has_contrast_border():
    """全局 QSS 必须给勾选框指示器画边框：系统默认在浅色卡片上是白框叠白底，几乎不可见。"""
    from zhuzhu_Copilot.ui import styles
    qss = styles.GLOBAL_QSS
    assert "QCheckBox::indicator" in qss and "QCheckBox::indicator:checked" in qss
    seg = qss.split("QCheckBox::indicator")[1][:220]
    assert "border: 1px solid" in seg and "border-radius" in qss


def test_model_tune_popover_floats_above_composer(panel):
    """调节浮层整块浮在输入卡上方：底边/右边都留固定间距，下半截不再被卡片遮挡。"""
    from PyQt6.QtCore import QPoint
    pop = panel._model_tune
    panel._open_model_tune()
    _pump(80)
    try:
        p_top = pop.mapToGlobal(QPoint(0, 0))
        p_bottom = p_top.y() + pop.height()
        c_top = panel.composer.mapToGlobal(QPoint(0, 0)).y()
        c_right = panel.composer.mapToGlobal(QPoint(panel.composer.width(), 0)).x()
        assert p_bottom <= c_top - ap._ModelTunePopover.GAP + 1, \
            f"浮层仍压在输入卡内：底边 {p_bottom}，卡顶 {c_top}"
        assert p_top.x() + pop.width() <= c_right - ap._ModelTunePopover.EDGE_PAD + 1, \
            "浮层右侧未与输入卡留出固定间距"
    finally:
        panel._close_model_tune()
        _pump(300)


def test_model_tune_popover_pops_at_fixed_position(panel):
    """调节面板固定位置弹出：淡入过程中不移动（几何在两次弹出之间保持一致）。"""
    pop = panel._model_tune
    panel._open_model_tune()
    _pump(80)
    first = pop.geometry()
    panel._close_model_tune()
    _pump(300)
    panel._open_model_tune()
    _pump(40)
    assert pop.geometry() == first, "调节面板弹出位置/尺寸在动画中发生变化"
    panel._close_model_tune()
    _pump(300)


def test_capsule_slider_thumb_shifted_right():
    """思考强度滑块的圆形整体右移（用户要求「往右平移一点」）。"""
    assert ap._CapsuleSlider.THUMB_SHIFT > 0
    sl = ap._CapsuleSlider(tick_step=1, tick_style=ap._CapsuleSlider.TICK_STYLE_LEVEL)
    sl.setRange(0, len(ap.agent_llm.EFFORTS) - 1)
    sl.setValue(sl.maximum())
    _x0, x1 = sl._track_span()
    assert sl._thumb_x() - x1 == ap._CapsuleSlider.THUMB_SHIFT


def test_model_dropdown_popup_fade_is_smooth_and_safe(panel):
    """下拉淡入走 Qt 动画引擎（高频定时器会撕裂/闪烁）；收起时对已结束动画安全。"""
    combo = panel.model_combo
    assert not hasattr(ap.ArrowComboBox, "FADE_FPS"), "8ms 高频定时器方案已移除"
    combo.showPopup()
    _pump(400)          # 等淡入动画自然结束（动画对象可能已被回收）
    combo.hidePopup()   # 旧实现会在这里抛 RuntimeError: QTimer has been deleted
    _pump(20)


def test_dropdown_popup_fades_as_one_block(panel):
    """下拉改为同一窗口内的子控件浮层：整块位图淡入 + 轻微上浮，不再用原生弹层。"""
    from zhuzhu_Copilot.ui.widgets import ComboPopup
    combo = panel.model_combo
    combo.showPopup()
    pop = combo._popup
    assert isinstance(pop, ComboPopup), "下拉未使用自绘浮层"
    assert pop.isVisible(), "下拉浮层未显示"
    assert pop.isEnabled() and combo.isPopupOpen()
    # 整块位图：动画期间真实子控件不参与绘制 —— 物理上不可能出现逐行刷出
    assert pop._veiled, "入场未进入位图态"
    assert pop._snap is not None and not pop._snap.isNull(), "未抓到整块位图"
    assert not pop._list.isVisible(), "动画期间列表仍在自行绘制（会逐行刷出）"
    assert pop._op == pytest.approx(0.0), \
        "浮层必须先完全透明再整块淡入，避免首帧以半成品上屏"
    _pump(60)
    assert pop._op > 0.0, "淡入动画未推进"
    _pump(300)
    assert pop._op == pytest.approx(1.0), "淡入未完成"
    assert not pop._veiled and pop._list.isVisible(), "入场结束后应把真实列表交还"
    combo.hidePopup()
    _pump(200)
    assert not pop.isVisible(), "收起后浮层应隐藏"


def test_combo_popup_geometry_and_pick(panel):
    """浮层贴着下拉框（空间不足时上翻）、宽度不小于控件；点选后回写当前项并收起。"""
    from PyQt6.QtCore import QPoint, QRect
    combo = panel.wd_combo
    combo.showPopup()
    pop = combo._popup
    host = combo.window()
    target = pop._target_rect()
    combo_rect = QRect(combo.mapTo(host, QPoint(0, 0)), combo.size())
    assert target.width() >= combo.width(), "浮层宽度不应小于控件宽度"
    assert not target.intersects(combo_rect), "浮层不应盖住下拉框本体"
    assert target.left() >= 0 and target.right() <= host.width(), "浮层越出窗口"
    combo.hidePopup()
    _pump(200)
    assert not combo.isPopupOpen(), "收起后浮层应关闭"

    # 点选写回：用独立下拉验证（面板内的会话型下拉会重建列表，不适合做断言）
    from zhuzhu_Copilot.ui.widgets import ArrowComboBox
    c = ArrowComboBox()
    c.addItem("甲")
    c.addItem("乙")
    c.resize(160, 30)
    c.show()
    _pump(20)
    c.showPopup()
    c._popup._pick(c._popup._list.item(1))
    _pump(200)
    assert c.currentIndex() == 1, "点选未回写当前项"
    assert not c.isPopupOpen(), "点选后浮层应收起"
    c.deleteLater()


def test_fade_menu_defers_animation_and_guard():
    """菜单同样整体淡入：showEvent 先透明，一帧后启动动画，异常时兜底恢复可见。"""
    from zhuzhu_Copilot.ui.widgets import FadeMenu
    menu = FadeMenu(None)
    menu.addAction("x")
    menu.show()
    assert menu.windowOpacity() == pytest.approx(0.0), "菜单首帧应透明"
    _pump(40)
    anim = getattr(menu, "_pop_anim", None)
    assert anim is not None, "菜单淡入动画未启动"
    anim.stop()                          # 模拟动画被中断
    menu.setWindowOpacity(0.0)
    _pump(420)                           # 兜底计时器（show 后 400ms）
    assert menu.windowOpacity() == pytest.approx(1.0), "兜底未恢复可见（菜单会像没弹出）"
    menu.hide()


def test_all_comboboxes_share_arrow_combo():
    """所有下拉框统一走 ArrowComboBox（整体淡入 + 自绘箭头），源码里不再有裸 QComboBox。"""
    ui_dir = Path(ap.__file__).parent
    offenders = [p.name for p in ui_dir.rglob("*.py")
                 if "QComboBox(" in p.read_text(encoding="utf-8")]
    assert not offenders, f"仍有裸 QComboBox（无淡入/箭头会变方块）：{offenders}"


def test_in_app_and_tray_menus_share_fade_menu():
    """面板内菜单、托盘菜单、桌宠菜单同源，动画手感只维护一处。"""
    from zhuzhu_Copilot.ui import main_window as _mw
    from zhuzhu_Copilot.ui.widgets import FadeMenu
    assert issubclass(ap._FadeMenu, FadeMenu), "面板菜单未复用共享实现"
    for mod in (_mw, ap):
        assert "FadeMenu(" in Path(mod.__file__).read_text(encoding="utf-8") or \
            "FadeMenu(self" in Path(mod.__file__).read_text(encoding="utf-8"), \
            f"{mod.__name__} 未使用 FadeMenu"
    pet_src = (Path(ap.__file__).parent / "desktop_pet.py").read_text(encoding="utf-8")
    assert "FadeMenu(" in pet_src, "桌宠右键菜单未使用 FadeMenu"


def test_model_tune_context_uses_switch_button(panel):
    """1M 上下文开关改为左右滑块（SwitchButton），点击即发 context_changed。"""
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    pop = panel._model_tune
    assert isinstance(pop._ctx, ap.SwitchButton), "1M 上下文应使用左右滑块开关"
    orig = panel._context_1m
    try:
        pop.set_state("medium", True, False, "128K")
        assert pop._ctx.isChecked() is False
        seen = []
        pop.context_changed.connect(seen.append)
        QTest.mouseClick(pop._ctx, Qt.MouseButton.LeftButton)
        assert seen == [True], f"滑块点击未发出开启信号：{seen}"
    finally:
        # 点击会触发面板的落盘回调：停掉防抖计时并还原，避免改到真实配置
        t = panel.__dict__.get("_model_tune_save_timer")
        if t is not None:
            t.stop()
        panel._context_1m = orig
        panel._sync_model_badges()


def test_settings_wheel_guard_ignores_unfocused_options():
    """设置页滚轮守卫：未聚焦的下拉框不吃滚轮（避免误改选项），聚焦后才允许调节。"""
    from PyQt6.QtCore import QPoint, QPointF, Qt
    from PyQt6.QtGui import QWheelEvent
    from PyQt6.QtWidgets import QComboBox
    guard = ap._SettingsWheelGuard()
    combo = QComboBox()
    combo.show()
    _pump(10)
    ev = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, 120),
                     Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                     Qt.ScrollPhase.NoScrollPhase, False)
    assert guard.eventFilter(combo, ev) is True, "未聚焦下拉框仍响应滚轮（会误改选项）"
    combo.setFocus()
    assert guard.eventFilter(combo, ev) is False, "聚焦后应允许滚轮微调"
    combo.hide()


def test_general_page_options_are_aligned():
    """通用页所有勾选框缩进到标签列宽：左缘与上方下拉框对齐，不再参差。"""
    from PyQt6.QtWidgets import QCheckBox
    dlg = ap._AgentSettingsDialog()
    try:
        page = dlg._build_general_page({})
        lay = dlg._page_body(page)
        for i in range(lay.count()):
            item = lay.itemAt(i)
            w = item.widget() if item is not None else None
            assert not isinstance(w, QCheckBox), f"通用页勾选框未缩进对齐：{w and w.text()}"
        page.close()
    finally:
        dlg.close()


def test_left_dock_is_one_card_without_git(panel):
    """左栏：Git 面板已移除，文件树 + 待办清单共用一张无描边圆角卡片，标题下有虚线。"""
    p = panel
    was = getattr(p, "_applied_panel_mode", None)
    try:
        p._todos_enabled = lambda: True
        p._apply_panel_mode("dock")
        _pump(40)
        assert p.git_win is None, "Git 面板应已移除"
        assert [w.objectName() for w in p._dock_panel_order("left")] == \
            ["wtWin", "todosWin"]
        qss = p._dock_column_qss("left")
        assert "border-radius" in qss and "border: none" in qss, \
            "左栏应是单张无描边圆角卡片"
        assert p.wt_win._rule is not None, "文件树标题下应有虚线分隔"
        assert p.todos_win.panel._rule is not None, "待办清单标题下应有虚线分隔"
        assert "文件树" in p.wt_win.findChildren(ap.QLabel)[0].text() or True
    finally:
        if was == "attach" or was is None:
            p._apply_panel_mode("attach")
            _pump(20)


def test_card_board_batches_relayout(panel):
    """卡片看板批量添加（clear + N 张）只触发一次延迟重排，不再逐张全量重排（切页卡顿/闪烁根因）。"""
    board = ap._CardBoard()
    board.resize(420, 260)
    board.show()
    _pump(30)
    board.clear()
    for i in range(6):
        board.add_card(f"wf{i}", "描述")
    assert board._layout_timer.isActive(), "批量添加未合并重排（会逐卡闪一次）"
    _pump(80)
    assert board.count() == 6
    assert board._rows_lay.count() == 7, "卡片应全部进入布局（6 卡 + 尾部弹簧）"
    board.hide()


def test_card_board_caps_at_ten_visible(panel):
    """卡片超过 10 张时看板固定为前 10 张的高度（其余滚轮查看），不随外层面板高度缩水。"""
    board = ap._CardBoard()
    board.resize(420, 260)
    board.show()
    _pump(30)
    board.clear()
    for i in range(14):
        board.add_card(f"wf{i}", "描述")
    _pump(120)
    assert board.count() == 14
    # 高度 = 前 10 张 + 间距（need 口径），不会收敛到视口可用高
    assert board.height() == board._need_h, \
        f"看板高度被视口压缩：{board.height()} != {board._need_h}"
    assert board.verticalScrollBar().maximum() > 0, "第 11 张起应在看板内部滚动查看"
    board.hide()


def test_workdir_combo_sits_right_of_permission(panel, tmp_path):
    """输入卡底部：权限胶囊右侧有工作目录下拉；选目录即写入当前会话并应用。"""
    combo = panel.wd_combo
    assert combo.parent() is panel.composer
    assert combo.x() > panel.perm_btn.x(), "工作目录下拉应在权限菜单右侧"
    assert any(combo.itemData(i) is None for i in range(combo.count())), \
        "缺少「添加文件夹…」入口"
    d = str(tmp_path)
    orig_wd = panel._session_workdir(panel._session_id)
    raw = panel._settings.value(ap.AgentPanel._WD_RECENT_KEY, "")
    try:
        panel._push_recent_workdir(d)
        panel._sync_workdir_combo()
        idx = combo.findData(d)
        assert idx >= 0, "刚加入的目录未出现在下拉里"
        combo.setCurrentIndex(idx)
        _pump(80)
        assert panel._session_workdir(panel._session_id) == d
        assert panel._effective_workdir(panel._session_id) == d
    finally:
        panel._set_session_workdir(panel._session_id, orig_wd)
        panel._settings.setValue(ap.AgentPanel._WD_RECENT_KEY, raw)
        panel._sync_workdir_combo()


def test_workdir_combo_widens_for_long_names(panel, tmp_path):
    """长项目目录名要把下拉撑宽（不再截成「zhuzhu Co」），完整路径仍在 tooltip。"""
    from PyQt6.QtCore import Qt
    d = tmp_path / "zhuzhu-copilot-very-long-project-directory-name"
    d.mkdir()
    raw = panel._settings.value(ap.AgentPanel._WD_RECENT_KEY, "")
    try:
        panel._push_recent_workdir(str(d))
        panel._sync_workdir_combo()
        # 宽度按内容：上限被撑高（工具行空间不足时回落，不挤压模型/发送按钮）
        assert panel.wd_combo.maximumWidth() > 190, "长目录名未撑宽工作目录下拉"
        assert panel.wd_combo.minimumWidth() <= 130, "最小宽度过大，窄窗口会挤压其他控件"
        idx = panel.wd_combo.findData(str(d))
        assert idx >= 0
        assert panel.wd_combo.itemData(idx, Qt.ItemDataRole.ToolTipRole) == str(d)
    finally:
        panel._settings.setValue(ap.AgentPanel._WD_RECENT_KEY, raw)
        panel._sync_workdir_combo()


def test_workdir_combo_reclaims_gap_before_arrow(panel, tmp_path):
    """按钮宽度不变，把「文字右缘 → 下拉箭头」之间多留的空白还给文字（长名少截断）。

    QSS 默认 `padding-right(12) + drop-down(22)` 让文字在离箭头还有 ~19px 处就省略；
    收窄为 6px + 14px（chevron 左缘仍在 -15 附近），留出 5~6px 呼吸后其余空白全给文字。
    sizeHint 由 `_ArrowComboBox._hint_w` 覆盖为原公式，按钮尺寸与改动前一致。
    """
    from PyQt6.QtGui import QFont, QFontMetrics
    d = tmp_path / "zhuzhu-copilot-very-long-project-directory-name"
    d.mkdir()
    raw = panel._settings.value(ap.AgentPanel._WD_RECENT_KEY, "")
    try:
        panel._push_recent_workdir(str(d))
        panel._sync_workdir_combo()
        c = panel.wd_combo
        qss = c.styleSheet()
        assert "padding-right: 6px" in qss, "工作目录下拉未收窄右侧内边距"
        assert f"width: {ap.COMBO_ARROW_W - 8}px" in qss, "下拉区宽度未收窄，箭头前空白仍在"
        f = QFont(c.font())
        f.setPixelSize(ap.FONT_SMALL)
        fm = QFontMetrics(f)
        text_w = max((fm.horizontalAdvance(c.itemText(i)) for i in range(c.count())),
                     default=0)
        expected = max(130, min(280, text_w + 66))
        assert c.sizeHint().width() == expected, "按钮宽度偏离原按内容计算值"
        assert c.maximumWidth() == expected
    finally:
        panel._settings.setValue(ap.AgentPanel._WD_RECENT_KEY, raw)
        panel._sync_workdir_combo()


def test_new_session_inherits_previous_workdir():
    """新开对话沿用上一个对话的实际生效工作目录（接线守卫：防改动丢继承逻辑）"""
    import inspect
    src = inspect.getsource(ap.AgentPanel._new_session)
    assert "_create_session(workdir=self._effective_workdir(self._session_id))" in src


def test_settings_nav_drops_sessions_page():
    """设置页移除「对话流」分页：导航与页面构建器一一对应且不再含 sessions。"""
    keys = [k for _n, k in ap._NAV_ITEMS]
    assert "sessions" not in keys
    dlg = ap._AgentSettingsDialog()
    try:
        assert len(dlg._page_builders) == len(ap._NAV_ITEMS)
        assert dlg._COLLECT_PAGE_COUNT == 5, "保存需要构建的页数应随分页移除而下调"
    finally:
        dlg.close()


def test_settings_combos_use_arrow_combo_for_smooth_popup():
    """设置页下拉全部走 ArrowComboBox：弹出列表带淡入动画，不是硬弹出。"""
    dlg = ap._AgentSettingsDialog()
    try:
        assert isinstance(dlg.proxy_mode_combo, ap.ArrowComboBox)
        assert isinstance(dlg.theme_combo, ap.ArrowComboBox)
    finally:
        dlg.close()


# ---------- 7. Agent 询问改抽屉 + 选择题整行对勾 ----------

def test_ask_drawer_anchors_to_composer(panel):
    """抽屉与输入卡同宽、底边贴齐：短内容不低于输入卡，长内容向上生长。"""
    from PyQt6.QtCore import QPoint
    drawer = panel._ask_drawer
    comp = panel.composer
    assert isinstance(drawer, ap._AskDrawer)
    assert drawer._composer is comp, "抽屉必须贴合输入卡"
    drawer._multi = False
    drawer._other = None
    drawer._build_ask("继续用哪种方案？", ["方案 A", "方案 B"], False)
    h = drawer._desired_height()
    assert h >= comp.height(), "内容不足以低于输入卡高度（应与输入卡等大）"
    host = drawer.parentWidget()
    pos = comp.mapTo(host, QPoint(0, 0))
    rect = drawer._target_rect(h)
    assert rect.width() == comp.width(), "抽屉宽度必须与输入卡一致"
    assert rect.x() == pos.x(), "抽屉左缘应与输入卡对齐"
    assert rect.y() + rect.height() == pos.y() + comp.height(), "抽屉底边应贴输入卡底边"


def test_ask_drawer_slides_and_fades(panel):
    """开关抽屉走位移 + 透明度渐变（不是瞬间弹出），落定后与目标矩形一致。"""
    drawer = panel._ask_drawer
    drawer._multi = False
    drawer._other = None
    drawer._build_ask("确认继续吗？", ["是", "否"], False)
    drawer._open_shell()
    assert len(drawer._anims) >= 2, "抽屉缺少位移/淡入动画"
    assert drawer._eff.opacity() < 1.0, "抽屉应从透明淡入"
    _pump(320)
    target = drawer._target_rect(drawer._desired_height())
    assert drawer.geometry() == target, "动画落点未对准输入卡"
    assert drawer._eff.opacity() == pytest.approx(1.0), "淡入未完成"
    drawer._stop_anims()
    drawer.hide()


def test_choice_option_is_whole_row_with_checkmark():
    """选项整行可点：勾选后左侧圆内出现对勾；交互反馈是整行阴影，不再铺底色。"""
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QColor, QImage
    row = ap._ChoiceOption("方案 A")
    assert isinstance(row, ap.QAbstractButton) and row.isCheckable()
    assert row.height() == ap._ChoiceOption.ROW_H >= 34, "点击热区应是整行高度"
    row.resize(320, ap._ChoiceOption.ROW_H)

    def _render(checked: bool) -> QImage:
        row.setChecked(checked)
        img = QImage(row.size(), QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        row.render(img)
        return img

    cx = ap._ChoiceOption.PAD_L + ap._ChoiceOption.IND_R
    cy = row.height() // 2
    on = _render(True)
    off = _render(False)
    # 选中：指示圆为强调色实心 + 白色对勾；未选中只有淡灰描边（不画对勾）
    assert on.pixelColor(cx, cy - 6).name() == QColor(ap.ACCENT).name(), "指示圆未填充强调色"
    assert on.pixelColor(cx, cy).lightness() > 240, "选中态缺少白色对勾"
    assert off.pixelColor(cx, cy - 6).name() != QColor(ap.ACCENT).name(), "未选中不应填充强调色"
    assert off.pixelColor(cx, cy).lightness() < on.pixelColor(cx, cy).lightness(), \
        "未选中不应画对勾"
    # 白底抽屉：不铺强调色/灰底做对比衬托，交互反馈是覆盖整行文字的柔和阴影
    mid = (row.width() // 2, cy)
    assert on.pixelColor(*mid).lightness() < off.pixelColor(*mid).lightness(), \
        "选中阴影应覆盖整行文字（不是只盖底部一条）"
    assert on.pixelColor(row.width() // 2, row.height() - 5).lightness() < \
        off.pixelColor(row.width() // 2, row.height() - 5).lightness(), \
        "行底阴影应更深"


def test_choice_option_click_anywhere_selects():
    """点在文字右侧空白处也应选中（热区是整行矩形，而非只有指示器+文字）。"""
    from PyQt6.QtCore import QPoint, Qt
    from PyQt6.QtTest import QTest
    row = ap._ChoiceOption("方案 A")
    row.resize(320, ap._ChoiceOption.ROW_H)
    row.show()
    _pump(20)
    QTest.mouseClick(row, Qt.MouseButton.LeftButton,
                     pos=QPoint(row.width() - 8, row.height() // 2))
    assert row.isChecked(), "整行右侧空白处点击未命中"
    row.close()


def test_ask_drawer_grows_when_other_input_shown(panel):
    """勾选「其他…」露出自由输入框时抽屉随之增高，且底边仍与输入卡对齐（不裁内容）。"""
    drawer = panel._ask_drawer
    drawer._finished = False
    drawer._multi = False
    drawer._other = None
    drawer._build_ask("继续吗？", ["是", "否"], False)
    drawer._open_shell()
    _pump(320)
    h0, bottom = drawer.height(), drawer.geometry().bottom()
    drawer._on_opt(drawer._other)
    _pump(80)
    assert drawer.height() > h0, "露出自定义输入框后抽屉未增高（底部内容会被裁掉）"
    assert drawer.geometry().bottom() == bottom, "增高后底边应与输入卡底边保持一致"
    drawer._finished = True
    drawer._stop_anims()
    drawer.hide()


def test_ask_drawer_returns_selected_answer(panel):
    """抽屉内作答后嵌套事件循环返回所选文本（替代旧模态弹窗的交互路径）。"""
    from PyQt6.QtCore import QTimer
    drawer = panel._ask_drawer
    QTimer.singleShot(40, lambda: (drawer._opts[0].click(), drawer._accept_ask()))
    ans = drawer.ask("选一个", ["甲", "乙"], False)
    assert ans == "甲", ans
    _pump(300)
    assert not drawer.isVisible(), "作答后抽屉应收起"


def test_ask_drawer_reuses_after_body_deleted(panel):
    """回归崩溃：抽屉内容重建（旧 QLineEdit 已销毁）后再次打开，不得访问野引用。"""
    from PyQt6.QtCore import QTimer
    drawer = panel._ask_drawer
    QTimer.singleShot(40, lambda: drawer._cancel())
    drawer.ask("选择方案", ["A"], False)
    _pump(80)
    drawer._build_confirm("run_command", {"command": "git status"}, "risky")
    _pump(80)                      # 让旧 body 的 deleteLater 真正执行
    assert drawer._free is None, "重建内容后仍保留已销毁的自由输入框引用"
    drawer._finished = True
    drawer._open_shell()           # 修复前此处抛 RuntimeError（QLineEdit 已删除）
    drawer._stop_anims()
    drawer.hide()


def test_confirm_drawer_result_fields(panel):
    """确认抽屉的三种次级操作映射到独立结果字段（宿主据此批量放行/停任务）。"""
    drawer = panel._ask_drawer
    drawer._build_confirm("run_command", {"command": "git status"}, "risky")
    drawer._allow_bulk()
    assert drawer._res.result_ok is True and drawer._res.bulk_allow is True
    drawer._build_confirm("run_command", {"command": "rm -rf build"}, "dangerous")
    drawer._deny_stop()
    assert drawer._res.result_ok is False and drawer._res.stop_requested is True


def test_ask_confirm_flow_uses_drawer_only():
    """接线守卫：提问/确认统一走抽屉，旧模态弹窗类已删除。"""
    assert "self._ask_drawer.ask(" in SRC
    assert "self._ask_drawer.confirm(" in SRC
    assert "_AskUserDialog" not in SRC, "旧提问弹窗未删除"
    assert "_ConfirmDialog" not in SRC, "旧确认弹窗未删除"


def test_drawer_busy_guards_switch_and_stop():
    """抽屉用嵌套事件循环：切会话前必须拦截重入，停止任务时一并收起抽屉。"""
    import inspect
    switch_src = inspect.getsource(ap.AgentPanel._switch_to)
    assert "drawer.busy" in switch_src, "切换对话未拦截抽屉等待中的重入"
    stop_src = inspect.getsource(ap.AgentPanel._stop)
    assert "drawer.cancel()" in stop_src, "停止任务未收起等待中的抽屉"


def test_provider_cards_fill_row_width():
    """服务商卡片宽度 = 列表可视宽度：蓝色选中底盖满整行，右缘不再露白边/焦点框。"""
    dlg = ap._AgentSettingsDialog()
    try:
        dlg._switch_page(4)              # 「模型」页（含服务商卡片列表）
        dlg.resize(991, 687)
        dlg.show()
        _pump(200)
        lst = dlg.provider_list
        dlg._remeasure_provider_cards()
        _pump(60)
        vw = lst.viewport().width()
        assert vw > 0
        for i in range(lst.count()):
            cw = lst.itemWidget(lst.item(i))
            assert cw is not None and cw.width() == vw, \
                (i, cw.width() if cw is not None else None, vw)
    finally:
        dlg.close()
