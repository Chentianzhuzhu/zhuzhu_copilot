# -*- coding: utf-8 -*-
"""启动弹窗（SplashWindow）形状回归：矩形 + 圆角（尺寸保持不变）。

需求：应用启动时的启动弹窗原本是矩形，只要求把四角改为圆角 —— **不改变窗口大小**。

契约：
1) 尺寸严格保持 SPLASH_W x SPLASH_H（359x200，宽高不等，仍为矩形）；
2) 四角套圆角，且复用应用统一圆角机制（agent_ui_ux.apply_rounded_window），
   不另起一套绘制分支；
3) 形状参数集中为模块常量（SPLASH_W / SPLASH_H / SPLASH_RADIUS），改一处即整体生效。

注意：本用例在 offscreen 平台下运行，仅断言几何与调用契约（不比对像素），
故对驱动/系统圆角实现差异（Win11 DWM / Win10 SetWindowRgn）保持中立。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])


def _splash_mod():
    """惰性导入启动入口模块（模块顶层有环境变量/faulthandler 等启动副作用）"""
    import main
    return main


def test_splash_size_unchanged_rectangle():
    """尺寸必须是 359x200 矩形：只加圆角，不改变窗口大小。"""
    m = _splash_mod()
    w = m.SplashWindow()
    try:
        assert (w.width(), w.height()) == (m.SPLASH_W, m.SPLASH_H), \
            f"启动弹窗尺寸应为 {m.SPLASH_W}x{m.SPLASH_H}，实际 {w.width()}x{w.height()}"
        assert (w.width(), w.height()) == (359, 200), "尺寸应为 359x200"
        assert w.width() != w.height(), "仍为矩形（宽高不等），不应变成正方形"
        # 固定尺寸：不可被布局/用户拉伸
        assert w.minimumSize() == w.maximumSize(), "启动弹窗应固定尺寸"
    finally:
        w.close()


def test_splash_rounding_reuses_shared_helper(monkeypatch):
    """圆角走应用统一机制：apply_rounded_window 必须被按 SPLASH_RADIUS 调用。"""
    m = _splash_mod()
    from winapp_migrator.core import agent_ui_ux

    calls = []
    real = agent_ui_ux.apply_rounded_window

    def _spy(widget, radius=18):
        calls.append((widget, radius))
        return real(widget, radius)

    monkeypatch.setattr(agent_ui_ux, "apply_rounded_window", _spy)
    w = m.SplashWindow()
    try:
        w.show()
        QTest.qWait(60)                 # 让 showEvent 与其后延时补套各跑一次
        assert calls, "显示时应调用 apply_rounded_window 套圆角"
        assert all(r == m.SPLASH_RADIUS for _, r in calls), \
            f"圆角半径应统一为 SPLASH_RADIUS={m.SPLASH_RADIUS}，实际 {[r for _, r in calls]}"
        assert all(widget is w for widget, _ in calls), "应作用于启动窗自身"
    finally:
        w.close()


def test_splash_show_and_close_are_safe():
    """显示/淡出关闭流程不抛异常，且关闭兜底确保窗口不残留挡住主面板。"""
    m = _splash_mod()
    w = m.SplashWindow()
    w.start()
    QTest.qWait(60)
    assert w.isVisible()
    w.finish_and_close()
    w.finish_and_close()               # 重复调用必须安全（引擎/托盘多路径可能触发）
    w._ensure_closed()
    assert not w.isVisible(), "关闭兜底后启动窗不应仍可见"


def test_splash_shape_is_single_source():
    """形状常量存在且为矩形语义（防回退到旧硬编码尺寸或误改成正方形）。"""
    m = _splash_mod()
    src = open(m.__file__, encoding="utf-8").read()
    assert "SPLASH_W" in src and "SPLASH_H" in src and "SPLASH_RADIUS" in src
    assert "setFixedSize(SPLASH_W, SPLASH_H)" in src, "启动窗必须按 SPLASH_W x SPLASH_H 设为矩形"
    assert "setFixedSize(366, 218)" not in src, "旧的扁矩形硬编码尺寸应已移除"
    assert "SPLASH_SIDE" not in src, "不应再保留等边（正方形）语义的常量"
