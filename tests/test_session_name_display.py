"""会话名显示回归（bug：AI 为对话起名后，收起态下拉用省略号截断名字）。

根因：`session_combo` 最大宽度过小（窄窗口 180 / 初始 200），而 QComboBox 收起态
文本超宽必用省略号截断。修复：提高宽度上限 + `_fit_session_combo_width` 按当前
会话名字体宽度动态抬高最小宽度（封顶档位最大宽度），保证收起态完整显示。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from PyQt6.QtWidgets import QApplication, QComboBox

_app = QApplication.instance() or QApplication([])

from winapp_migrator.ui import agent_panel as ap


def _combo(cap: int, floor: int = 150, name: str = "") -> QComboBox:
    """最小骨架面板 + 真实 QComboBox（文本宽度用控件字体计算，与真面板一致）"""
    p = ap.AgentPanel.__new__(ap.AgentPanel)
    p.session_combo = QComboBox()
    p.session_combo.setMinimumWidth(floor)
    p.session_combo.setMaximumWidth(cap)
    if name:
        p.session_combo.addItem(name)
        p.session_combo.setCurrentIndex(0)
    p._fit_session_combo_width()
    return p.session_combo


def _need(name: str) -> int:
    cb = QComboBox()
    return cb.fontMetrics().horizontalAdvance(name) + 56   # 与实现一致：箭头+左右内边距


def test_wide_and_narrow_hold_ai_title_16_chars():
    """AI 起名 ≤16 字（新上限）：宽(360)/窄(280)两档收起态文本区都容得下，minWidth 已自适应"""
    for cap in (360, 280):
        name = "帮我查一下今天北京天气和路况如何"   # 16 字（AI 起名上限）
        need = _need(name)
        cb = _combo(cap=cap, floor=130, name=name)
        assert need <= cb.maximumWidth(), f"cap={cap} 下 16 字 AI 名仍会截断"
        assert cb.minimumWidth() == need, f"cap={cap} 下最小宽度未按会话名自适应"


def test_wide_layout_holds_local_title_20_chars():
    """本地兜底名 ≤20 字（clean_title 上限）：宽窗口档(360)完整显示"""
    name = "帮我写一个快速排序的Python实现并且"   # len == 20
    assert len(name) == 20
    need = _need(name)
    cb = _combo(cap=360, name=name)
    assert need <= cb.maximumWidth(), "20 字本地名在宽档仍会截断"
    assert cb.minimumWidth() == need


def test_blank_name_keeps_floor_width():
    """空/未命名时回落到档位下限，不撑大顶栏"""
    assert _combo(cap=280, floor=130, name="").minimumWidth() == 130
    assert _combo(cap=360, floor=200, name="").minimumWidth() == 200


def test_oversized_name_capped_not_broken():
    """超长名（非正常路径）也不突破档位上限（封顶），避免挤压其它顶栏控件"""
    assert _combo(cap=360, name="超长名字" * 50).minimumWidth() == 360