# -*- coding: utf-8 -*-
"""输入行「圆形按钮 / 下拉箭头」这类**可感知样式**的回归（真实 AgentPanel）。

两条缺陷的共同点是「样式写在代码里却看不出来」，所以断言口径也不同：
  1. 「优化提示词」是 42px 正圆钮：圆角 = 直径一半**且必须带可见描边** ——
     透明底 + border:none 时圆角无从体现（用户两次反馈「没有圆形的 UI 附着样式」）；
  2. 历史对话下拉必须自绘箭头：QComboBox 一旦被样式表接管 ::drop-down，原生箭头
     就不再绘制，必须与模型下拉一样用 `_ArrowComboBox`（否则箭头整块消失）。

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
    p.resize(1400, 900)
    p.show()
    _pump(400)
    yield p
    # 必须把顶层窗口收起来：留一个可见的大窗口会让后续用例（CopilotPanel 的开合动画
    # 用例）在事件循环里被拖住 —— 跨文件随机顺序下表现为「某次收起失败」。
    # 只 hide 不 destroy：面板后台线程（MCP 等）还在跑，销毁会让它们向已删对象发信号。
    try:
        p.hide()
        _pump(30)
    except Exception:
        pass
    mp.undo()


def test_optimize_button_is_a_visible_circle(panel):
    """圆钮要「看得出来是圆的」：圆角 = 直径一半，且必须有底 + 描边。

    只把 border-radius 改成一半是不够的：`background: transparent; border: none` 下
    圆角与矩形完全同形，用户看到的仍是一个裸图标（所以第一次修复后反馈照旧）。
    """
    btn = panel.optimize_btn
    d = btn.width()
    assert d == btn.height() and d > 0, f"正圆前提是宽高相等，实得 {btn.width()}x{btn.height()}"
    qss = btn.styleSheet()
    assert f"border-radius: {d // 2}px" in qss, "圆角必须取直径一半，否则是圆角方块"
    assert "border: 1px solid" in qss, "没有描边，圆角不可感知（圆形附着样式缺失）"
    assert "background:" in qss, "透明底时圆角同样看不出（必须有实心底）"
    assert "QPushButton:hover" in qss, "圆钮需要 hover 反馈"


def test_optimize_button_matches_upload_button_style(panel):
    """「优化提示词」必须与「+」上传按钮**同款皮肤**（用户明确要求「和上传文件按钮一样」）。

    两者同排同尺寸，样式一旦漂移就会看起来不属于同一组控件；这里直接断言 QSS 完全相同，
    并要求它们共用同一个皮肤函数（避免以后改一处漏一处）。
    """
    a, o = panel.attach_btn, panel.optimize_btn
    assert (a.width(), a.height()) == (o.width(), o.height()) == (42, 42)
    assert a.styleSheet() == o.styleSheet(), \
        f"两个圆钮皮肤不一致：\n上传={a.styleSheet()}\n优化={o.styleSheet()}"
    src = Path(ap.__file__).read_text(encoding="utf-8")
    assert src.count("_round_icon_btn_qss(_ROUND_BTN_D)") >= 2, \
        "两个圆钮必须共用同一个皮肤函数（_round_icon_btn_qss）"


def test_session_combo_uses_self_drawing_arrow(panel):
    """会话下拉必须自绘箭头：`_QCOMBO` 接管 ::drop-down 后原生箭头不再绘制。"""
    assert isinstance(panel.session_combo, ap._ArrowComboBox), \
        "会话下拉缺少自绘箭头（原生箭头被样式表屏蔽 → 箭头消失）"


def test_session_combo_arrow_is_actually_painted(panel):
    """自绘箭头必须真的落在像素上（离屏抓图，右侧下拉区应有深色笔画）。"""
    img = panel.session_combo.grab().toImage()
    x0, x1 = max(1, img.width() - 18), max(2, img.width() - 6)
    dark = sum(1 for y in range(3, img.height() - 3)
               for x in range(x0, x1)
               if sum(img.pixelColor(x, y).getRgb()[:3]) < 460)
    assert dark >= 6, f"会话下拉右侧未画出箭头（深色像素仅 {dark} 个）"


def test_round_buttons_keep_their_own_diameter_radius():
    """圆钮的圆角必须等于各自直径的一半 —— 用圆角档位会把圆钮画成圆角方块。"""
    src = Path(ap.__file__).read_text(encoding="utf-8")
    assert "border-radius: 17px" in src, "发送按钮（34px 圆钮）圆角应为 17"
    assert "border-radius: {diameter // 2}px" in src, "42px 圆钮圆角应由直径推出（21）"
