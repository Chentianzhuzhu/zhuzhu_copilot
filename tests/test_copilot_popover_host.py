"""AI 面板内 zhuzhu Copilot 入口按钮与浮层宿主的回归测试（真实 AgentPanel）。

验证三件事：
  1. 顶栏「zhuzhu Copilot」按钮位于「上下文统计」按钮左侧同一行；
  2. 点击可在按钮下方弹出真实 CopilotPanel 浮层（四分组齐备、不越界）；
  3. 再次点击 / 浮层自身关闭请求均可收起。

CopilotPanel 的磁盘扫描、托盘、管理员告警、桌宠在 fixture 中打桩隔离。
"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest                                       # noqa: E402
from PyQt6.QtCore import QPoint, Qt                 # noqa: E402
from PyQt6.QtWidgets import QApplication            # noqa: E402

_app = QApplication.instance() or QApplication([])

from winapp_migrator.ui import main_window as mw    # noqa: E402
from winapp_migrator.ui.agent_panel import AgentPanel  # noqa: E402


def _pump(ms: int):
    """推进事件循环（动画依赖真实时间）"""
    end = time.time() + ms / 1000.0
    while time.time() < end:
        _app.processEvents()
        time.sleep(0.005)


@pytest.fixture(scope="module")
def host():
    mp = pytest.MonkeyPatch()
    # 隔离 CopilotPanel 构造副作用：不真扫描系统 / 不建托盘与桌宠 / 不弹权限告警
    mp.setattr(mw.CopilotPanel, "_start_scan", lambda self, *a: None)
    mp.setattr(mw.CopilotPanel, "_init_update_check", lambda self: None)
    mp.setattr(mw.CopilotPanel, "_check_admin", lambda self: None)
    mp.setattr(mw.CopilotPanel, "_setup_tray", lambda self: None)
    try:
        mp.setattr("winapp_migrator.ui.desktop_pet.ensure_pet", lambda w: None)
    except Exception:
        pass
    panel = AgentPanel(None)
    panel.resize(1400, 900)
    panel.show()
    _pump(400)
    yield panel
    mp.undo()


def test_copilot_button_left_of_context_stats(host):
    """入口按钮位于上下文统计按钮左侧、同一行"""
    assert hasattr(host, "copilot_btn") and hasattr(host, "token_btn")
    p1 = host.copilot_btn.mapTo(host, QPoint(0, 0))
    p2 = host.token_btn.mapTo(host, QPoint(0, 0))
    assert p1.x() < p2.x(), f"入口按钮未在统计按钮左侧：{p1.x()} vs {p2.x()}"
    assert p1.y() == p2.y(), "入口按钮与统计按钮不在同一行"


def test_toggle_opens_real_popover(host):
    """点击入口按钮弹出真实浮层：四分组齐备且落在屏幕可用区内"""
    host.toggle_copilot_panel()
    _pump(500)
    cp = host._copilot_panel
    assert cp is not None, "浮层未创建"
    assert cp.isVisible(), "浮层未显示"
    assert [b.text() for b in cp._tab_group.buttons()] == ["应用", "防护", "通用", "工具"]
    scr = _app.primaryScreen().availableGeometry()
    assert scr.contains(cp.geometry()), f"浮层越界：{cp.geometry().getRect()}"


def test_toggle_closes_popover(host):
    """再次点击收起浮层，宿主开合状态复位"""
    if not host._copilot_open:
        host.toggle_copilot_panel()
        _pump(400)
    host.toggle_copilot_panel()
    _pump(400)
    assert not host._copilot_panel.isVisible(), "浮层未收起"
    assert host._copilot_open is False


def test_close_request_hides_popover(host):
    """浮层头部关闭按钮（close_requested 信号）→ 收起浮层"""
    host.open_copilot_panel()
    _pump(400)
    host._copilot_panel.close_requested.emit()
    _pump(400)
    assert not host._copilot_panel.isVisible()
    assert host._copilot_open is False


def _ensure_closed(host):
    if host._copilot_visible():
        host.close_copilot_panel()
        _pump(400)


def test_click_after_outside_dismiss_opens_immediately(host):
    """回归（用户报告"有时点按钮不弹出 / 要点两次"）：

    浮层被外部收起（点击别处 / Esc，走失焦守卫）后，宿主状态位必须同步；
    否则下一次点击会走"关闭"分支 —— 而浮层已不可见，关闭是空操作，
    表现为"点了没反应"，必须再点一次才能弹出。
    """
    _ensure_closed(host)
    host.open_copilot_panel()
    _pump(400)
    assert host._copilot_visible()

    # 模拟"点击浮层外部 / Esc"：正是守卫的收起路径
    f = host.__dict__.get("_copilot_dismiss")
    assert f is not None, "失焦守卫未挂载"
    f._dismiss()
    _pump(400)
    assert not host._copilot_panel.isVisible(), "外部收起失败"
    assert host._copilot_open is False, "外部收起后宿主状态未同步（旧 bug 根因）"

    # 单次点击就必须弹出
    host.toggle_copilot_panel()
    _pump(400)
    assert host._copilot_panel.isVisible(), "外部收起后首次点击未能弹出"


def test_rapid_double_click_ends_closed(host):
    """快速连点两次：一次开一次关，最终收起且状态一致"""
    _ensure_closed(host)
    host.toggle_copilot_panel()      # 开
    _pump(120)                       # 弹出动画尚未走完
    host.toggle_copilot_panel()      # 立刻再点 → 应收起
    _pump(500)
    assert not host._copilot_panel.isVisible()
    assert host._copilot_open is False


def test_minimize_collapses_popover(host):
    """面板最小化时浮层一并收起（独立顶层窗口不会自动跟随）"""
    _ensure_closed(host)
    host.open_copilot_panel()
    _pump(400)
    assert host._copilot_panel.isVisible()
    host.setWindowState(host.windowState() | Qt.WindowState.WindowMinimized)
    _pump(400)
    assert not host._copilot_panel.isVisible(), "最小化后浮层未收起"
    host.setWindowState(host.windowState() & ~Qt.WindowState.WindowMinimized)
    _pump(300)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
