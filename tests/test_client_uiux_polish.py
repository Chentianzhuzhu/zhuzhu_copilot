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
    """回到底部按钮的高亮必须由主题强调色派生，换肤后不残留旧蓝。"""
    assert "rgba(47,82,216" not in SRC, "回到底部按钮仍写死旧主题蓝"
    assert "_sb = QColor(ACCENT_HOVER)" in SRC, "回到底部按钮必须从 ACCENT_HOVER 派生"


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
    assert "border-radius: 16px" in qss and "border: none" in qss


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
    from PyQt6.QtWidgets import QFrame
    d = _bare_settings_dialog()
    page = d._page("通用")
    card = page.findChild(QFrame, "settingsCard")
    assert card is not None, "设置页正文必须收进圆角卡片"
    assert d._page_body(page) is card.layout(), "页面正文布局必须指向卡片内层"
    assert "border-radius:" in card.styleSheet(), "卡片必须有圆角"
    assert "background:" in card.styleSheet(), "卡片必须有分层底色"


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

def test_session_drawer_collapses_to_narrow_strip(panel):
    """对话栏收起后只留 36px 窄条 + 展开箭头，再点一次恢复完整列表。"""
    d = _drawer(panel)
    if d.is_collapsed():
        d.set_collapsed(False)
    assert d.width() == ap._DRAWER_W
    d.set_collapsed(True)
    assert d.width() == ap._DRAWER_COLLAPSED_W, "收起后应是窄条而不是整块消失"
    assert not d.list.isVisible() and not d.search.isVisible(), "收起后列表/搜索框应让位"
    assert d.expand_btn.isVisible(), "窄条上必须保留展开箭头"
    d.set_collapsed(False)
    assert d.width() == ap._DRAWER_W
    assert d.list.isVisible()


def test_leftdock_collapses_whole_column(panel):
    """文件树 / Git / 任务清单算一个左栏：一次收起全部，再展开恢复原可见面板。"""
    p = panel
    if p._leftdock_collapsed():
        p._set_leftdock_collapsed(False, persist=False)
    order = p._dock_panel_order("left")
    p._set_leftdock_collapsed(True, persist=False)
    assert p._leftdock_collapsed()
    assert p._dock_left.width() == ap._DOCK_COLLAPSED_W, "收起后左栏应为窄条"
    for w in order:
        assert w.isHidden(), "收起时栏内面板必须全部让位"
    p._set_leftdock_collapsed(False, persist=False)
    assert not p._leftdock_collapsed()
    assert p._dock_left.width() >= ap._DOCK_COLLAPSED_W
    for w in order:
        assert w.isVisible(), "展开后原先可见的面板应恢复"


def test_function_rail_exposes_collapse_entries(panel):
    """胶囊里必须有「对话栏」「左栏」两个折叠开关（与标题栏箭头同一状态）。"""
    rail = panel._function_rail
    assert "drawer" in rail._buttons, "胶囊缺少对话栏折叠入口"
    assert "leftdock" in rail._buttons, "胶囊缺少左栏折叠入口"
