# -*- coding: utf-8 -*-
"""背景壁纸：高斯模糊 / 压暗强度 / 容器透明化的行为与回归测试。

覆盖两段彼此独立、但必须同时成立的逻辑：
· core/app_wallpaper —— 参数夹紧、模糊重算与缓存、绘制结果；
· ui/agent_panel     —— 有壁纸时容器表面色整体改透明（让模糊壁纸透出来）。
"""

import pytest

from PyQt6.QtCore import QRect
from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap

from zhuzhu_Copilot.core import app_wallpaper as w


@pytest.fixture(scope="module", autouse=True)
def _qt_app():
    """模糊走 QGraphicsScene、绘制走 QPixmap，都必须先有应用实例。"""
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    return app


def _striped_pixmap(size: int = 64, step: int = 4) -> QPixmap:
    """黑白竖条纹：高对比度图案，模糊后锐度必须显著下降。"""
    img = QImage(size, size, QImage.Format.Format_RGB32)
    for y in range(size):
        for x in range(size):
            img.setPixelColor(x, y,
                              QColor(255, 255, 255) if (x // step) % 2 else QColor(0, 0, 0))
    return QPixmap.fromImage(img)


def _sharpness(pm: QPixmap) -> int:
    """中间一行的「相邻像素亮度差之和」：越大越锐利，模糊后必然变小。"""
    im = pm.toImage()
    y = im.height() // 2
    total, prev = 0, im.pixelColor(0, y).red()
    for x in range(1, im.width()):
        cur = im.pixelColor(x, y).red()
        total += abs(cur - prev)
        prev = cur
    return total


def _close(a: QColor, b: QColor, tol: int = 8) -> bool:
    """颜色近似相等：模糊对 alpha 通道的加权平均有取整损失（实测 255 → 249），
    叠到底色上会带来几个 /255 的偏差，肉眼不可辨，但逐通道比较必须留容差。"""
    return all(abs(getattr(a, k)() - getattr(b, k)()) <= tol
               for k in ("red", "green", "blue"))


def _solid_png(tmp_path, color: str = "#2F52D8", size: int = 64) -> str:
    pm = QPixmap(size, size)
    pm.fill(QColor(color))
    path = tmp_path / "wall.png"
    assert pm.save(str(path), "PNG")
    return str(path)


# ══════════════ 高斯模糊 ══════════════

def test_blur_softens_the_image():
    """真实模糊的判据：条纹图的高频对比必须被抹掉（不是只改个参数值）。"""
    src = _striped_pixmap()
    sharp_before = _sharpness(src)
    blurred = w._blurred(src, 10)
    sharp_after = _sharpness(blurred)
    assert sharp_before > 0, "探针图本身必须有对比度，否则断言无意义"
    assert sharp_after < sharp_before * 0.5, (
        f"模糊没有真正生效：锐度 {sharp_before} → {sharp_after}")


def test_blur_zero_returns_source_unchanged():
    """半径 0 = 不糊：直接返回原图，不做任何逐像素开销。"""
    src = _striped_pixmap()
    assert w._blurred(src, 0) is src


def test_blur_keeps_original_size():
    """模糊不能改变尺寸：可见区四周的过渡带必须被裁掉，否则壁纸会错位/缩小。"""
    src = _striped_pixmap(48)
    out = w._blurred(src, 12)
    assert (out.width(), out.height()) == (src.width(), src.height())


def test_blur_result_is_cached_and_invalidated_on_change():
    """模糊是逐像素重活：同参数必须命中缓存，换半径必须重算（不能拿旧图糊新半径）。"""
    src = _striped_pixmap()
    first = w._blurred(src, 8)
    assert w._blurred(src, 8) is first, "同参数重复请求应命中缓存"
    second = w._blurred(src, 20)
    assert second is not first, "换了半径必须重算"
    assert _sharpness(second) < _sharpness(first), "半径越大应该越糊"


def test_blur_survives_broken_pixmap():
    """空位图不能让绘制链崩（异常路径一律回退原图）。"""
    empty = QPixmap()
    assert w._blurred(empty, 10) is empty


# ══════════════ 参数层 ══════════════

def test_params_clamp_blur_and_dim():
    """超范围一律夹紧到上下限：滑杆/agent 工具传什么都画得出来。"""
    p = w.WallpaperParams(bg_blur=999, bg_dim=-50).clamped()
    assert p.bg_blur == w.BLUR_MAX
    assert p.bg_dim == w.DIM_MIN


def test_params_reject_nan_and_non_numeric():
    """NaN 会绕过 min/max（比较恒假），非数字来自被手改坏的 json —— 都回落默认值。"""
    p = w.WallpaperParams(bg_blur=float("nan"), bg_dim="很大").clamped()
    assert p.bg_blur == w.BLUR_DEFAULT
    assert p.bg_dim == w.DIM_DEFAULT


def test_params_roundtrip_keeps_blur_and_dim():
    p = w.WallpaperParams(bg_image="/x/a.png", bg_fit="tile", bg_blur=12.0, bg_dim=30.0)
    assert w.WallpaperParams.from_dict(p.to_dict()) == p


def test_legacy_config_without_blur_gets_defaults():
    """老配置（只有 bg_image/bg_fit）读进来必须补上默认值，不能变成 0 而看不出壁纸。"""
    p = w.WallpaperParams.from_dict({"bg_image": "/x/a.png", "bg_fit": "cover"})
    assert p.bg_blur == w.BLUR_DEFAULT
    assert p.bg_dim == w.DIM_DEFAULT


# ══════════════ 绘制：模糊 + 压暗 ══════════════

def _paint_to(path, size, blur, dim, scrim="#101216") -> QImage:
    w.set_fields(bg_image=path, bg_blur=blur, bg_dim=dim, persist=False)
    out = QPixmap(size[0], size[1])
    out.fill(QColor("#000000"))
    painter = QPainter(out)
    try:
        assert w.paint(painter, out.rect(), scrim) is True
    finally:
        painter.end()
    return out.toImage()


def test_paint_dim_full_covers_wallpaper(tmp_path):
    """压暗 100% = 只剩主题底色：这是「正文压在照片上看不清」的兜底。"""
    img = _paint_to(_solid_png(tmp_path), (80, 60), blur=0, dim=100)
    assert img.pixelColor(40, 30) == QColor("#101216")


def test_paint_dim_zero_shows_wallpaper(tmp_path):
    """压暗 0% = 壁纸原色透出（容器透明后正文之下必须真的是这张图）。"""
    img = _paint_to(_solid_png(tmp_path, "#2F52D8"), (80, 60), blur=0, dim=0)
    assert img.pixelColor(40, 30) == QColor("#2F52D8")


def test_paint_default_dim_is_readable_floor(tmp_path):
    """默认压暗必须落在 0 与 100 之间：既看得见壁纸，又留得住正文可读性。"""
    img = _paint_to(_solid_png(tmp_path, "#2F52D8"), (80, 60), blur=0, dim=w.DIM_DEFAULT)
    px = img.pixelColor(40, 30)
    assert px != QColor("#2F52D8"), "默认压暗没有生效"
    assert px != QColor("#101216"), "默认压暗过重，壁纸完全看不见"


def test_paint_with_blur_still_covers_the_whole_rect(tmp_path):
    """模糊不能把画面画小/画偏、也不能把透明混进来：整个矩形（含四角）都必须是壁纸色。

    反例（实测）：模糊核会取到画布外的透明，可见区中心 alpha 被压到 243、四角只剩 68
    → 壁纸整体发暗并与下层黑底混色。修法是摆版后先垫一圈由边缘延伸出的边再糊。
    """
    img = _paint_to(_solid_png(tmp_path), (120, 90), blur=16, dim=0)
    for x, y in ((0, 0), (119, 0), (0, 89), (119, 89), (60, 45)):
        px = img.pixelColor(x, y)
        assert _close(px, QColor("#2F52D8")), f"({x},{y}) 不是壁纸色：{px.name()}"
        assert px.alpha() >= 250, f"({x},{y}) 被混入了透明：alpha={px.alpha()}"


# ══════════════ 容器透明化（主页 UI/UX 背景让位给壁纸） ══════════════

def test_surfaces_turn_transparent_only_with_usable_wallpaper(tmp_path):
    """设了可用壁纸 → 容器底改透明；坏图 / 清除 → 立刻恢复不透明。

    坏图必须回落：把界面变透明却没有壁纸可画，用户看到的就是一块黑屏。
    """
    from zhuzhu_Copilot.ui import agent_panel as ap

    good = _solid_png(tmp_path)
    w.set_fields(bg_image=good, persist=False)
    assert ap.refresh_surface_mode() is True, "透明态由关到开必须被识别出来"
    for key in ap._SURFACE_KEYS:
        assert getattr(ap, key) == "transparent", f"{key} 应让位给壁纸"
    assert ap.TEXT != "transparent", "文字色不能让位，否则正文不可读"
    assert ap.USER_BG != "transparent", "用户气泡底色不能让位，否则气泡看不见"

    junk = tmp_path / "broken.png"
    junk.write_bytes(b"not an image")
    w.set_fields(bg_image=str(junk), persist=False)
    assert ap.refresh_surface_mode() is True, "透明态由开到关必须被识别出来"
    for key in ap._SURFACE_KEYS:
        assert getattr(ap, key) != "transparent", f"{key} 坏图时必须恢复实色"


def test_refresh_surface_mode_reports_no_change_when_state_holds(tmp_path):
    """只调模糊/压暗不该触发就地重建（那是一整屏重排）→ 刷新必须回报「无变化」。"""
    from zhuzhu_Copilot.ui import agent_panel as ap

    w.set_fields(bg_image=_solid_png(tmp_path), persist=False)
    assert ap.refresh_surface_mode() is True
    w.set_fields(bg_blur=18, bg_dim=40, persist=False)
    assert ap.refresh_surface_mode() is False, "同状态下重复刷新不该触发重建"
    assert w.params().bg_blur == 18
    assert w.params().bg_dim == 40


def test_base_color_unmasks_the_scrim_color(tmp_path):
    """压暗纱必须取原始底色：透明模式下 BG 已是 transparent，拿它压纱等于没压。"""
    from zhuzhu_Copilot.ui import agent_panel as ap

    w.set_fields(bg_image=_solid_png(tmp_path), persist=False)
    ap.refresh_surface_mode()
    assert ap.BG == "transparent"
    assert ap._base_color("BG") != "transparent"
    assert QColor(ap._base_color("BG")).isValid()


def test_focus_glow_and_cursor_flash_boost_on_wallpaper(tmp_path):
    """壁纸下必须加强焦点反馈：背景花 + 容器透明会吃掉默认的边缘光与光标。

    回归场景：设了自定义背景后「聚焦时看不出输入框在哪、光标几乎看不见」。
    """
    from PyQt6.QtWidgets import QApplication
    from zhuzhu_Copilot.ui import agent_panel as ap

    app = QApplication.instance()
    assert app is not None
    try:
        w.set_fields(bg_image=_solid_png(tmp_path), persist=False)
        ap.refresh_surface_mode()
        blur, alpha = ap._focus_glow_params()
        assert blur > ap._FOCUS_GLOW_BLUR, "壁纸下边缘光必须加粗"
        assert alpha > ap._FOCUS_GLOW_ALPHA, "壁纸下边缘光必须加亮"
        assert app.cursorFlashTime() == ap._CURSOR_FLASH_MS_ON_WALLPAPER

        w.set_fields(bg_image="", persist=False)
        ap.refresh_surface_mode()
        assert ap._focus_glow_params() == (ap._FOCUS_GLOW_BLUR, ap._FOCUS_GLOW_ALPHA)
        assert app.cursorFlashTime() == ap._CURSOR_FLASH_MS
    finally:
        w.set_fields(bg_image="", persist=False)
        ap.refresh_surface_mode()


def test_token_stats_popover_keeps_opaque_surface(tmp_path):
    """统计浮层是「数字可读性」的关键面：容器整体透明时它必须保持不透明底。

    用户实测反馈：设了自定义背景后上下文 token 消耗看不清 —— 卡片底变透明、
    进度条轨道跟着消失，数字直接压在深浅不定的照片上。
    """
    from zhuzhu_Copilot.ui import agent_panel as ap

    w.set_fields(bg_image=_solid_png(tmp_path), persist=False)
    ap.refresh_surface_mode()
    assert ap.PANEL == "transparent", "前置条件：容器底确实已让位给壁纸"

    assert ap._usage_palette()["track"].alpha() == 255, "进度条轨道不能透明"

    pop = ap._TokenStatsPopover()
    try:
        qss = pop.styleSheet()
        assert "background: transparent" not in qss, "统计卡片底不能透明"
        assert ap._base_color("PANEL") in qss, "统计卡片底应回落到全局默认面板色"
        assert ap._base_color("HOVER") in qss, "chip 底应回落到全局默认悬停色"
    finally:
        pop.deleteLater()


def test_mix_hex_on_transparent_base_stays_transparent():
    """气泡层次色是「某色压在容器底色上」：底色透明时结果必须还是透明。

    不处理的话 QColor("transparent") 会被当成 rgba(0,0,0,0)，
    混色得到纯黑 —— 正是「设了壁纸后聊天气泡变黑块」的成因。
    """
    from zhuzhu_Copilot.ui import agent_panel as ap
    assert ap._mix_hex("#2F52D8", "transparent", 0.3) == "transparent"
    assert ap._mix_hex("#2F52D8", "#1F232C", 0.0) == "#1f232c"


def test_wallpaper_page_exposes_blur_and_dim_controls():
    """背景页必须同时给出模糊与压暗两个可调项（用户明确要求可调模糊程度）。"""
    from zhuzhu_Copilot.ui import agent_panel as ap
    dlg = ap._AgentSettingsDialog()
    try:
        dlg._stash_page = dlg._build_wallpaper_page()
        blur = getattr(dlg, "wallpaper_blur", None)
        dim = getattr(dlg, "wallpaper_dim", None)
        assert blur is not None and dim is not None, "背景页缺少模糊/压暗滑杆"
        assert blur.maximum() == int(w.BLUR_MAX)
        assert dim.maximum() == int(w.DIM_MAX)
        assert blur.value() == int(round(w.params().bg_blur))
        assert dim.value() == int(round(w.params().bg_dim))
    finally:
        dlg.deleteLater()


def test_slider_commit_only_on_release():
    """拖动中不得提交：每一格都重算一次高斯模糊会明显掉帧。"""
    from zhuzhu_Copilot.ui import agent_panel as ap
    dlg = ap._AgentSettingsDialog()
    try:
        dlg._stash_page = dlg._build_wallpaper_page()
        slider = dlg.wallpaper_blur
        before = w.params().bg_blur
        slider.setValue(slider.maximum())      # 只改值，不松手
        assert w.params().bg_blur == before, "拖动中提交会让每一格都重算模糊"
        slider.sliderReleased.emit()
        assert w.params().bg_blur == float(slider.maximum())
    finally:
        dlg.deleteLater()
