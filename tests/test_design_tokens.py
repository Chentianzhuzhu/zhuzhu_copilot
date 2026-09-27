"""P1-1 / P1-2 回归：Design Tokens 系统化 + 顶栏降载。

验证三件事：
- tokens 常量合法且圆角有序；
- 按钮/下拉/对话框共用的模块级 QSS 生产者与液态玻璃下拉、主窗口全局 QSS
  一律消费 tokens 常量，不再散落 6px / 10px 等魔法值；
- 顶栏降载：tokens/工作流/模型统一并入「上下文统计」浮层（原 status_btn 已删除），
  缺失控件（骨架）时同样安全兜底、不抛异常。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from PyQt6.QtWidgets import QApplication, QComboBox, QLabel

_app = QApplication.instance() or QApplication([])

from winapp_migrator.ui import agent_panel as ap
from winapp_migrator.ui import styles as st
from winapp_migrator.ui import tokens


def test_tokens_positive_and_ordered():
    """几何/字阶/间距常量均为正整数，且圆角从小到大有序"""
    for v in (tokens.RADIUS_SM, tokens.RADIUS_MD, tokens.RADIUS_LG,
              tokens.FONT_CAPTION, tokens.FONT_SMALL, tokens.FONT_BODY,
              tokens.FONT_BASE, tokens.FONT_TITLE, tokens.FONT_LARGE,
              tokens.FONT_HERO, tokens.SPACING_XS, tokens.SPACING_SM,
              tokens.SPACING_MD, tokens.SPACING_LG, tokens.SPACING_XL):
        assert isinstance(v, int) and v > 0
    assert tokens.RADIUS_SM < tokens.RADIUS_MD < tokens.RADIUS_LG


def _reapply(theme: str) -> None:
    """重建模块级派生 QSS 常量（深/浅主题各验一轮）"""
    ap._apply_colors(dict(ap._THEMES[theme]))


def test_module_qss_producers_consume_tokens():
    """按钮/下拉/对话框共用 QSS 常量由 Design Tokens 驱动（深/浅主题同规格）"""
    for theme in ("dark", "light"):
        _reapply(theme)
        assert f"border-radius: {tokens.RADIUS_SM}px" in ap._BTN_GHOST
        assert f"border-radius: {tokens.RADIUS_SM}px" in ap._QCOMBO
        assert f"border-radius: {tokens.RADIUS_SM}px" in ap._BTN_ICON
        assert f"border-radius: {tokens.RADIUS_SM}px" in ap._BTN_DANGER
        assert f"font-size: {tokens.FONT_SMALL}px" in ap._BTN_GHOST
        assert f"font-size: {tokens.FONT_BODY}px" in ap._BTN_PRIMARY
        # 已收敛：不再散落 6px / 10px 圆角魔法值
        assert "border-radius: 6px" not in ap._BTN_COMPACT
        assert "border-radius: 10px" not in ap._BTN_DANGER


def test_glass_popup_consumes_tokens():
    """液态玻璃下拉默认圆角来自 tokens（控件 SM / 弹出视图 MD）"""
    _reapply("dark")
    assert f"border-radius: {tokens.RADIUS_SM}px" in ap._glass_combo_qss()
    assert f"border-radius: {tokens.RADIUS_MD}px" in ap._glass_combo_view_qss()


def test_global_qss_consumes_tokens():
    """主窗口全局 QSS 的圆角/字阶走 tokens"""
    assert f"border-radius: {tokens.RADIUS_SM}px" in st.GLOBAL_QSS
    assert f"border-radius: {tokens.RADIUS_MD}px" in st.GLOBAL_QSS
    assert f"font-size: {tokens.FONT_BASE}px" in st.GLOBAL_QSS


def test_status_info_merged_into_token_stats_popover():
    """顶栏降载（新口径）：原 status_btn 的 tokens/工作流/模型信息已并入上下文统计浮层。

    工作流优先取"实际生效名"（不带 wf_label 的展示前缀），取不到时回退标签文本；
    骨架期缺控件（无 model_combo / wf_label）同样安全兜底、不抛异常。"""
    p = ap.AgentPanel.__new__(ap.AgentPanel)
    p.wf_label = QLabel("当前工作流: coding")
    p.model_combo = QComboBox()
    p.model_combo.addItem("deepseek-v3")
    p._effective_workflow = lambda: "coding"     # 生效工作流名优先于标签展示文本
    p._sess = {}
    p._session_id = ""
    p._engine = None
    stats = p._token_stats()
    assert stats["workflow"] == "coding"
    assert stats["model"] == "deepseek-v3"
    pop = ap._TokenStatsPopover()
    pop.set_stats(stats)
    assert pop._vals["workflow"].text() == "coding"
    assert pop._vals["model"].text() == "deepseek-v3"
    pop.close()

    # 骨架场景（无 wf_label / model_combo，且无法解析生效工作流）：兜底为默认名且不抛异常
    p2 = ap.AgentPanel.__new__(ap.AgentPanel)
    stats2 = p2._token_stats()
    assert stats2["workflow"] == "内置默认工作流"
    assert stats2["model"] == ""