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
    """磨砂强度管「糊多厉害」，组件透明度管「透多少」——两者已解耦。"""
    _qapp()
    from zhuzhu_Copilot.ui import glass

    old_s, old_a = glass.strength(), glass.alpha()
    try:
        # 强度：只影响模糊半径与开关，不再决定填充透明度
        glass.set_strength(0, persist=False)
        assert glass.enabled() is False
        low_blur = glass.blur_radius()
        glass.set_strength(50, persist=False)
        mid_blur = glass.blur_radius()
        glass.set_strength(100, persist=False)
        assert glass.enabled() is True
        assert glass.blur_radius() > mid_blur > low_blur, "强度越大磨砂越重"

        # 透明度：独立设置，0→0.10（留一点底保可读），50→0.5，100→1.0
        glass.set_alpha(0, persist=False)
        assert glass.opacity() == pytest.approx(0.10)
        glass.set_alpha(50, persist=False)
        assert glass.opacity() == pytest.approx(0.5)
        glass.set_alpha(100, persist=False)
        assert glass.opacity() == pytest.approx(1.0)
    finally:
        glass.set_strength(old_s, persist=False)
        glass.set_alpha(old_a, persist=False)


def test_glass_backdrop_only_for_registered_host(tmp_path):
    """只有登记为宿主的窗口才采样壁纸：对话框里若也采样，会在自己窗口内画出壁纸。"""
    _qapp()
    from PyQt6.QtWidgets import QWidget
    from zhuzhu_Copilot.ui import glass

    with _wallpaper(tmp_path):
        glass.set_strength(50, persist=False)
        plain = QWidget()
        plain.resize(300, 200)
        pm, _, _ = glass._frosted_layer(plain)
        assert pm is None, "未登记的窗口不该有磨砂底"

        host = QWidget()
        host.resize(300, 200)
        glass.register_host(host)
        pm, key, scale = glass._frosted_layer(host)
        assert pm is not None and not pm.isNull(), "宿主窗口应能生成磨砂底"
        assert key is not None and scale >= 1.0
        # 同一窗口再次请求必须命中缓存（同一张位图），不能再糊一遍
        again, key2, _ = glass._frosted_layer(host)
        assert again is pm and key2 == key, "同窗口应命中磨砂层缓存"
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
        glass.reset_layers()
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



def test_capture_scene_only_in_wallpaper_mode(tmp_path):
    """场景抓取只在「自定义背景 + 玻璃开启」时发生：否则白花一次整窗渲染。"""
    from PyQt6.QtWidgets import QWidget
    from zhuzhu_Copilot.core import app_wallpaper
    from zhuzhu_Copilot.ui import glass

    _qapp()
    app_wallpaper.set_fields(bg_image="", persist=False)
    glass.set_strength(50, persist=False)
    w = QWidget()
    w.resize(120, 80)
    w.show()
    glass.capture_scene(w)
    assert w.__dict__.get("_glass_scene") is None, "无壁纸时不该抓场景"

    with _wallpaper(tmp_path):
        glass.capture_scene(w)
        snap = w.__dict__.get("_glass_scene")
        assert snap is not None and snap.size() == w.size(), "有壁纸时应抓到同尺寸快照"
    glass.drop_scene(w)
    assert w.__dict__.get("_glass_scene") is None
    glass.set_strength(glass.GLASS_DEFAULT, persist=False)


def test_glass_button_reflects_content_behind(tmp_path):
    """玻璃按钮反射的是**它压着的内容**（红色块），而不是背后的蓝色壁纸。"""
    from PyQt6.QtWidgets import QWidget, QLabel
    from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap
    from PyQt6.QtCore import Qt
    from zhuzhu_Copilot.core import app_wallpaper
    from zhuzhu_Copilot.ui import glass
    from zhuzhu_Copilot.ui.widgets import GlassButton

    _qapp()

    class _Host(QWidget):
        def paintEvent(self, _e):
            p = QPainter(self)
            if not app_wallpaper.paint(p, self.rect()):
                p.fillRect(self.rect(), QColor("#000000"))
            p.end()

    with _wallpaper(tmp_path):                       # 蓝色壁纸
        glass.set_strength(50, persist=False)
        # 填充调成全透明，剩下的像素就只能来自"反射到的内容"，断言才有意义
        glass.set_alpha(0, persist=False, mode_key="wallpaper")
        host = _Host()
        host.resize(200, 160)
        host.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        glass.register_host(host)
        red = QLabel(host)
        red.setStyleSheet("background: #FF0000;")
        red.setGeometry(60, 50, 90, 50)
        btn = GlassButton(QIcon(), 40, host)
        btn.move(80, 55)                             # 大部分压在红色块上
        host.show()
        _qapp().processEvents()

        glass.capture_scene(btn)
        assert btn.__dict__.get("_glass_scene") is not None, "应抓到按钮背后的场景"

        pm = QPixmap(btn.size())
        pm.fill(QColor("#000000"))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        glass.paint_surface(btn, p, radius=20, tint="#FFFFFF", reflect=True)
        p.end()
        c = pm.toImage().pixelColor(btn.width() // 2, btn.height() // 2)
        assert c.red() > c.blue(), f"反射应以红色内容为主，实得 {c.name()}"
        glass.set_alpha(10, persist=False, mode_key="wallpaper")
        btn.deleteLater()
        red.deleteLater()
        host.close()
    glass.set_strength(glass.GLASS_DEFAULT, persist=False)


def test_glass_alpha_is_per_mode(tmp_path):
    """组件透明度按模式各存一份：自定义背景=白底不透明度，深/浅色各管各的。"""
    _qapp()
    from zhuzhu_Copilot.ui import glass

    modes = ("wallpaper", "dark", "light")
    old = {m: glass.alpha(m) for m in modes}
    try:
        glass.set_alpha(20, persist=False, mode_key="light")
        glass.set_alpha(70, persist=False, mode_key="dark")
        assert glass.alpha("light") == 20
        assert glass.alpha("dark") == 70
        assert glass.alpha("wallpaper") == old["wallpaper"], \
            "调深/浅色不该改动自定义背景那份"

        with _wallpaper(tmp_path):
            assert glass.mode() == "wallpaper", "有壁纸时模式应为 wallpaper"
            assert glass.alpha() == glass.alpha("wallpaper"), "当前模式应取白底那份"
        assert glass.mode() in ("dark", "light")
    finally:
        for m, v in old.items():
            glass.set_alpha(v, persist=False, mode_key=m)


def test_glass_alpha_is_per_mode(tmp_path):
    """组件透明度按模式各存一份：自定义背景=白底不透明度，深/浅色各管各的。"""
    _qapp()
    from zhuzhu_Copilot.ui import glass

    modes = ("wallpaper", "dark", "light")
    old = {m: glass.alpha(m) for m in modes}
    try:
        glass.set_alpha(20, persist=False, mode_key="light")
        glass.set_alpha(70, persist=False, mode_key="dark")
        assert glass.alpha("light") == 20
        assert glass.alpha("dark") == 70
        assert glass.alpha("wallpaper") == old["wallpaper"],             "调深/浅色不该改动自定义背景那份"

        with _wallpaper(tmp_path):
            assert glass.mode() == "wallpaper", "有壁纸时模式应为 wallpaper"
            assert glass.alpha() == glass.alpha("wallpaper"), "当前模式应取白底那份"
        assert glass.mode() in ("dark", "light")
    finally:
        for m, v in old.items():
            glass.set_alpha(v, persist=False, mode_key=m)
