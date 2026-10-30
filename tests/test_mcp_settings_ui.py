"""MCP 设置页：复制配置只导出选中项，重启按钮的转圈/打勾反馈动画。

只测纯逻辑与控件状态，不连接真实 MCP 服务器、不发起磁盘/网络操作。
"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

import pytest
from PyQt6.QtCore import QPointF
from PyQt6.QtWidgets import QApplication, QPushButton

_app = QApplication.instance() or QApplication([])

from zhuzhu_Copilot.ui import agent_panel as ap


def _servers():
    return [{"name": "alpha", "type": "stdio", "command": "python", "args": ["a.py"]},
            {"name": "beta", "type": "sse", "url": "http://x/sse"},
            {"name": "gamma", "type": "stdio", "command": "python", "args": ["g.py"]}]


def test_export_subset_only_selected_server():
    """「复制配置」只导出点名的那一个，不允许带出全部"""
    assert ap._mcp_export_subset(_servers(), ["beta"]) == [
        {"name": "beta", "type": "sse", "url": "http://x/sse"}]


def test_export_subset_empty_never_falls_back_to_all():
    """names 为空/None 时返回空——绝不回退成「全部服务器」"""
    assert ap._mcp_export_subset(_servers(), None) == []
    assert ap._mcp_export_subset(_servers(), []) == []


def test_export_subset_unknown_name_returns_empty():
    assert ap._mcp_export_subset(_servers(), ["missing"]) == []


def test_spinner_dot_count_is_six():
    """绕圈小圆点数量：3 → 6"""
    assert ap._ActionFeedback.DOTS == 6


def test_check_stroke_grows_from_left_bottom_to_right_top():
    """打勾按路径长度绘制：从左下起笔，经底部转折，最终收笔于右上"""
    pts = [QPointF(0, 0), QPointF(5, 10), QPointF(20, -10)]
    assert ap._ActionFeedback._polyline_until(pts, 0.0) == [pts[0]]

    half = ap._ActionFeedback._polyline_until(pts, 0.5)
    assert half[0] == pts[0]
    assert len(half) >= 2
    assert half[-1].x() > 5, "进度过半时笔尖必须已经越过底部转折、向右推进"

    end = ap._ActionFeedback._polyline_until(pts, 1.0)
    assert end[0] == pts[0]
    assert end[-1].x() == pytest.approx(20) and end[-1].y() == pytest.approx(-10)


def test_action_feedback_spin_then_check_then_reset():
    """按钮反馈层：转圈态 → 打勾态（进度到 1 后停留）→ 自动复原隐藏"""
    btn = QPushButton("重启选中")
    btn.resize(120, 32)
    fx = ap._ActionFeedback(btn, "#141414", "#2F52D8")

    fx.start_spin()
    assert fx._mode == "spin"
    fx._tick_spin()
    fx.show()
    assert not fx.grab().isNull(), "转圈态必须能正常绘制"

    fx.finish(True)
    assert fx._mode == "check"
    assert fx._progress == 0.0
    for _ in range(40):
        fx._tick_check()
    assert fx._progress == 1.0
    assert fx._hold_timer.isActive(), "画完对勾后应停留一段时间再复原"
    assert not fx.grab().isNull(), "打勾态必须能正常绘制"

    fx.reset()
    assert fx._mode == ""
    assert fx.isHidden()


def test_mcp_page_builds_feedback_for_both_restart_buttons():
    """MCP 页构建后，两个重启按钮各挂一层反馈动画，复制按钮只导出选中项"""
    dlg = ap._AgentSettingsDialog()
    try:
        page = dlg._build_mcp_page()
        assert page is not None
        assert dlg._mcp_fx_one._button.text() == "重启选中"
        assert dlg._mcp_fx_all._button.text() == "全部重启"
        assert set(dlg._mcp_restart_btns) == {dlg._mcp_fx_one._button,
                                              dlg._mcp_fx_all._button}
    finally:
        dlg.close()


def test_restart_done_restores_buttons_and_plays_check():
    """重启结果回传后：按钮恢复可用，并在最短展示时长后切到打勾动画"""
    dlg = ap._AgentSettingsDialog()
    try:
        dlg._stash_page = dlg._build_mcp_page()   # 持住页面，防 GC 销毁子控件
        dlg._mcp_fx_active = dlg._mcp_fx_one
        dlg._mcp_fx_t0 = time.time() - 1.0          # 已超过最短转圈时长
        dlg._mcp_fx_one.start_spin()
        for b in dlg._mcp_restart_btns:
            b.setEnabled(False)

        dlg._on_mcp_restart_done(True, "ok")
        assert all(b.isEnabled() for b in dlg._mcp_restart_btns)
        assert dlg._mcp_fx_pending is not None

        dlg._finish_mcp_fx()
        assert dlg._mcp_fx_one._mode == "check"
        dlg._mcp_fx_one.reset()
    finally:
        dlg.close()


def test_action_feedback_failure_restores_without_check():
    """重启失败时不播放打勾，直接复原"""
    btn = QPushButton("全部重启")
    btn.resize(120, 32)
    fx = ap._ActionFeedback(btn, "#141414", "#2F52D8")
    fx.start_spin()
    fx.finish(False)
    assert fx._mode == ""
    assert fx.isHidden()
