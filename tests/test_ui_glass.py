"""玻璃皮肤（ui/glass.py）的契约测试：强度持久化、派生量映射、磨砂底与贴图缓存。

这些断言针对「设置-背景里的磨砂强度滑杆」背后的行为契约，而不是像素外观：
外观由 scripts/_verify_glass_look.py 出图目视确认。
"""
import os
import sys

import pytest

_app_mod = None


def _qapp():
    from PyQt6.QtWidgets import QApplication
    global _app_mod
    _app_mod = QApplication.instance() or QApplication([])
    return _app_mod


def _png(tmp_path, name="w.png") -> str:
    from PyQt6.QtCore import QRect
    from PyQt6.QtGui import QColor, QPainter, QPixmap
    pm = QPixmap(240, 160)
    p = QPainter(pm)
    p.fillRect(QRect(0, 0, 240, 160), QColor("#2563EB"))
    for x in range(0, 240, 12):
        p.fillRect(QRect(x, 0, 4, 160), QColor("#F79009"))
    p.end()
    path = str(tmp_path / name)
    pm.save(path, "PNG")
    return path


def _wallpaper(tmp_path):
    """临时换上壁纸并把原参数还回去（全局单例，必须成对）。"""
    from zhuzhu_Copilot.core import app_wallpaper
    from contextlib import contextmanager

    @contextmanager
    def _ctx():
        old = app_wallpaper.params()
        app_wallpaper.set_fields(bg_image=_png(tmp_path), bg_blur=0.0, bg_dim=0.0,
                                 persist=False)
        try:
            yield
        finally:
            app_wallpaper.set_params(old, persist=False)
    return _ctx()


def test_glass_strength_defaults_persists_and_clamps():
    _qapp()
    from zhuzhu_Copilot import app_identity
    from zhuzhu_Copilot.ui import glass

    qs = app_identity.qsettings()
    old = qs.value("ui_glass", None)
    try:
        qs.remove("ui_glass")
        glass._strength = None                       # 强制回读 QSettings
        assert glass.strength() == glass.GLASS_DEFAULT == 50, "默认磨砂强度应为 50"

        assert glass.set_strength(120) == 100, "越界必须夹到上限"
        assert str(qs.value("ui_glass")) == "100", "强度必须落库"
        assert glass.set_strength(-5) == 0
        glass._strength = None
        assert glass.strength() == 0, "重启后应读回落库值"
    finally:
        glass.set_strength(glass.GLASS_DEFAULT, persist=False)
        if old is None:
            qs.remove("ui_glass")
        else:
            qs.setValue("ui_glass", old)


def test_glass_strength_maps_to_opacity_and_blur():
    _qapp()
    from zhuzhu_Copilot.ui import glass

    try:
        glass.set_strength(0, persist=False)
        assert glass.opacity() == pytest.approx(1.0), "0 = 关闭玻璃：实色"
        assert glass.enabled() is False
        glass.set_strength(50, persist=False)
        assert glass.opacity() == pytest.approx(0.5), "50 = 半透明填充（用户指定的基准）"
        assert glass.enabled() is True
        mid_blur = glass.blur_radius()
        glass.set_strength(100, persist=False)
        assert glass.opacity() == pytest.approx(0.12), "100 = 最通透"
        assert glass.blur_radius() > mid_blur, "强度越大磨砂越重"
    finally:
        glass.set_strength(glass.GLASS_DEFAULT, persist=False)


def test_glass_backdrop_only_for_registered_host(tmp_path):
    """只有登记为宿主的窗口才采样壁纸：对话框里若也采样，会在自己窗口内画出壁纸。"""
    _qapp()
    from PyQt6.QtWidgets import QWidget
    from zhuzhu_Copilot.ui import glass

    with _wallpaper(tmp_path):
        glass.set_strength(50, persist=False)
        plain = QWidget()
        plain.resize(300, 200)
        assert glass._frosted_layer(plain) is None, "未登记的窗口不该有磨砂底"

        host = QWidget()
        host.resize(300, 200)
        glass.register_host(host)
        layer = glass._frosted_layer(host)
        assert layer is not None and not layer.isNull(), "宿主窗口应能生成磨砂底"
        assert layer.size() == host.size()
    glass.set_strength(glass.GLASS_DEFAULT, persist=False)


def test_glass_block_is_cached_and_invalidated_by_strength(tmp_path):
    _qapp()
    from PyQt6.QtWidgets import QWidget, QVBoxLayout
    from zhuzhu_Copilot.ui import glass
    from zhuzhu_Copilot.ui.widgets import Card

    with _wallpaper(tmp_path):
        glass.set_strength(50, persist=False)
        host = QWidget()
        host.resize(420, 320)
        glass.register_host(host)
        lay = QVBoxLayout(host)
        card = Card("t")
        lay.addWidget(card)
        host.show()
        _qapp().processEvents()

        first = glass._glass_block(card, card.rect(), 12, "#FFFFFF", 0.0)
        second = glass._glass_block(card, card.rect(), 12, "#FFFFFF", 0.0)
        assert first is second, "同一状态必须命中缓存（否则每帧都在重建整块贴图）"

        glass.set_strength(80, persist=False)
        glass._layer["key"] = None
        third = glass._glass_block(card, card.rect(), 12, "#FFFFFF", 0.0)
        assert third is not first, "强度变化后必须重建"
        host.close()
    glass.set_strength(glass.GLASS_DEFAULT, persist=False)


def test_wallpaper_page_glass_row_reuses_capsule_slider():
    """设置-背景的磨砂强度行复用现有胶囊滑杆，且初始值取当前强度。"""
    from zhuzhu_Copilot.ui import agent_panel as ap
    from zhuzhu_Copilot.ui import glass
    from zhuzhu_Copilot.ui import styles as st

    _qapp()
    d = ap._AgentSettingsDialog.__new__(ap._AgentSettingsDialog)
    d._TEXT = st.LIGHT_PALETTE["text"]
    d._ACCENT = st.LIGHT_PALETTE["primary"]
    seen = []
    row, slider, label = d._wallpaper_slider_row(
        "磨砂强度", glass.strength(), glass.GLASS_MAX, "%", "tip",
        lambda v: seen.append(int(v)), live=True)
    try:
        assert isinstance(slider, ap._CapsuleSlider), "必须复用设置页胶囊滑杆"
        assert (slider.minimum(), slider.maximum()) == (0, glass.GLASS_MAX)
        assert slider.value() == glass.strength(), "初始值应取当前强度"
        assert label.text() == f"{glass.strength()}%"
        slider.setValue(73)                      # 非拖动改值：应立即提交
        assert seen == [73], f"live 行必须即时提交，实得 {seen}"
    finally:
        slider.deleteLater()
