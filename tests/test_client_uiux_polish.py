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
