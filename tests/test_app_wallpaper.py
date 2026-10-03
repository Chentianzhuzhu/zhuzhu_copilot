# -*- coding: utf-8 -*-
"""背景壁纸：高斯模糊 / 压暗强度 / 容器透明化的行为与回归测试。

覆盖两段彼此独立、但必须同时成立的逻辑：
· core/app_wallpaper —— 参数夹紧、模糊重算与缓存、绘制结果；
· ui/agent_panel     —— 有壁纸时容器表面色整体改透明（让模糊壁纸透出来）。
"""

import pytest

from PyQt6.QtCore import QRect
from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication

from zhuzhu_Copilot.core import app_wallpaper as w

# QApplication 必须在**模块导入期**创建并保活（与 test_dock_layout / test_settings_nav_icons 同构）。
# 只在 module 级 fixture 里创建（return 出去）不够：setup 返回后应用对象会被回收，
# 而 PyQt 包装仍在 → 之后构造较重控件（如 CodePreviewWindow：QComboBox/QTextBrowser/
# WebEngine 分支）会访问悬挂的 C++ 对象，进程级崩溃 0xC0000409。
# 实测：仅 fixture 内创建时，本文件中构造 CodePreviewWindow 的用例必崩；改为导入期创建即通过。
_APP = QApplication.instance() or QApplication([])


@pytest.fixture(scope="module", autouse=True)
def _qt_app():
    """模糊走 QGraphicsScene、绘制走 QPixmap，都必须先有应用实例（见上方保活说明）。"""
    return _APP


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

    「翻转被识别」的前态由**直接置位**制造，不靠调用顺序：本机可能已配置壁纸，且其它
    用例可能留有存活的面板订阅者（`app_wallpaper.subscribe`），它们在 `set_fields`
    时就会把这次翻转消费掉 —— 依顺序断言会变成跨文件用例顺序相关。
    """
    from zhuzhu_Copilot.ui import agent_panel as ap

    good = _solid_png(tmp_path)
    w.set_fields(bg_image=good, persist=False)
    ap._SURFACE_TRANSPARENT = False          # 强制前态：由关到开
    assert ap.refresh_surface_mode() is True, "透明态由关到开必须被识别出来"
    for key in ap._SURFACE_KEYS:
        assert getattr(ap, key) == "transparent", f"{key} 应让位给壁纸"
    assert ap.TEXT != "transparent", "文字色不能让位，否则正文不可读"
    assert ap.USER_BG != "transparent", "用户气泡底色不能让位，否则气泡看不见"

    junk = tmp_path / "broken.png"
    junk.write_bytes(b"not an image")
    w.set_fields(bg_image=str(junk), persist=False)
    ap._SURFACE_TRANSPARENT = True           # 强制前态：由开到关
    assert ap.refresh_surface_mode() is True, "透明态由开到关必须被识别出来"
    for key in ap._SURFACE_KEYS:
        assert getattr(ap, key) != "transparent", f"{key} 坏图时必须恢复实色"


def test_refresh_surface_mode_reports_no_change_when_state_holds(tmp_path):
    """只调模糊/压暗不该触发就地重建（那是一整屏重排）→ 刷新必须回报「无变化」。"""
    from zhuzhu_Copilot.ui import agent_panel as ap

    w.set_fields(bg_image=_solid_png(tmp_path), persist=False)
    ap.refresh_surface_mode()
    assert ap._SURFACE_TRANSPARENT is True, "前置：壁纸可用时容器底应为透明态"
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


def test_border_turns_transparent_with_wallpaper_but_keeps_base_color(tmp_path):
    """壁纸下 BORDER/BORDER_SOFT 让位给壁纸，但原始描边色仍可通过 _base_color 取回，

    供需要强调可读性的局部控件（如统计浮层、输入框 scrim）兜底使用。
    """
    from zhuzhu_Copilot.ui import agent_panel as ap

    w.set_fields(bg_image=_solid_png(tmp_path), persist=False)
    ap.refresh_surface_mode()
    assert ap.BORDER == "transparent"
    assert ap.BORDER_SOFT == "transparent"
    assert ap._base_color("BORDER") != "transparent"
    assert ap._base_color("BORDER_SOFT") != "transparent"


def test_cmd_block_dots_stay_solid_on_wallpaper(tmp_path):
    """壁纸下命令块标题栏的三个圆点必须仍是实色（回归：三点整个消失）。

    根因：圆点此前直接取 `style.border`，而壁纸模式会把 BORDER 覆写为 transparent
    （描边让位给壁纸是有意设计）→ 实心圆点跟着透明掉。修法是「面板侧传入未覆写的
    原始描边色 + 组件侧一律走 `dot_of()`」；本用例钉住这条链路的两端。
    """
    from zhuzhu_Copilot.ui import agent_chat_bubbles as cb
    from zhuzhu_Copilot.ui import agent_panel as ap

    w.set_fields(bg_image=_solid_png(tmp_path), persist=False)
    ap.refresh_surface_mode()
    assert ap.BORDER == "transparent", "前置：壁纸透明覆写已生效"

    style = ap.AgentPanel.__new__(ap.AgentPanel)._chat_style()
    assert style.border == "transparent", "前置：描边确实让位给壁纸"
    assert style.dot_of() != "transparent", "面板侧必须传入未覆写的实色圆点色"
    assert QColor(style.dot_of()).isValid()

    from PyQt6.QtGui import QIcon
    _icon = lambda kind, size, color: QIcon()      # noqa: E731  折叠 chevron 需要非 None 图标
    blk = cb.CmdBlock(style, _icon)
    assert len(blk._dots_w) == 3
    for dot in blk._dots_w:
        qss = dot.styleSheet()
        assert "transparent" not in qss, f"圆点被透明掉了：{qss}"
        assert style.dot_of() in qss, f"圆点未取实色：{qss}"

    blk.restyle(ap.AgentPanel.__new__(ap.AgentPanel)._chat_style())
    for dot in blk._dots_w:
        qss = dot.styleSheet()
        assert "transparent" not in qss, f"restyle 后圆点被透明掉了：{qss}"
        assert style.dot_of() in qss, f"restyle 后圆点未取实色：{qss}"
    blk.deleteLater()
    # 收尾：透明覆写是模块级状态，必须复位，避免污染后续用例
    w.set_fields(bg_image="", persist=False)
    ap.refresh_surface_mode()


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


def test_input_bg_transparent_and_caret_boost_on_wallpaper(tmp_path):
    """壁纸下输入框底必须全透明，对比度改由「泛光 + 加宽的原生光标」兜底。

    回归场景：输入框曾用半透明 scrim 保对比度，叠在花背景上显得浑浊；
    用户要求底一律透明。光标不再自绘 —— 加宽原生光标（见 apply_input_caret）
    即可在花色背景上看清，同时保留 Qt 的原生闪烁。
    """
    from PyQt6.QtWidgets import QApplication
    from zhuzhu_Copilot.ui import agent_panel as ap
    from zhuzhu_Copilot.ui.agent_panel import _DropTextEdit

    app = QApplication.instance()
    assert app is not None
    edit = _DropTextEdit()
    try:
        w.set_fields(bg_image=_solid_png(tmp_path), persist=False)
        ap.refresh_surface_mode()
        assert ap._input_bg() == "transparent", "壁纸下输入框底必须全透明"
        # 断言：有 focus glow（确保光标区域有视觉反馈）
        blur, alpha = ap._focus_glow_params()
        assert blur > ap._FOCUS_GLOW_BLUR, "壁纸下边缘光必须加粗"
        assert alpha == 255, "壁纸下边缘光必须拉满"

        w.set_fields(bg_image="", persist=False)
        ap.refresh_surface_mode()
        assert ap._input_bg() == ap.PANEL, "无壁纸时恢复面板底色"
    finally:
        edit.deleteLater()
        w.set_fields(bg_image="", persist=False)
        ap.refresh_surface_mode()


def test_input_caret_blinks_with_native_caret(tmp_path):
    """输入光标必须「看得见 + 会闪」：加宽原生光标，禁用自绘静态光标。

    回归（用户反馈，两条）：
      ① 自定义背景模式下点进输入框看不到光标 —— 原生 1px 光标被花背景吃掉；
      ② 输入文字时「只有光标显示、不闪烁」—— 曾用 paintEvent 自绘一条静态黑色竖线
         「保证始终可见」，它盖住了原生光标，原生闪烁因此完全看不出来。
    修法：删掉自绘，改用原生闪烁光标并把宽度提到 2px（壁纸下 3px）。
    """
    from PyQt6.QtWidgets import QApplication
    from zhuzhu_Copilot.ui import agent_panel as ap
    from zhuzhu_Copilot.ui.agent_panel import _DropTextEdit

    app = QApplication.instance()
    assert app is not None

    # ① 组件不得再自绘静态光标（自绘静态线 = 光标永不消失 → 表现为不闪烁）
    assert "paintEvent" not in _DropTextEdit.__dict__, \
        "输入框重新自绘光标会再次盖住原生闪烁光标"
    assert not hasattr(ap, "_CUSTOM_CURSOR_WIDTH"), "自绘光标常量应已随实现一并删除"

    # ② 闪烁周期必须 > 0（Qt 里 0 = 不闪烁），且宽度在两种模式下都要够粗
    edit = _DropTextEdit()
    try:
        w.set_fields(bg_image=_solid_png(tmp_path), persist=False)
        ap.refresh_surface_mode()
        ap.apply_input_caret(edit)
        assert app.cursorFlashTime() > 0, "闪烁周期为 0 时光标不会闪"
        assert edit.cursorWidth() == ap._CURSOR_WIDTH_ON_WALLPAPER
        assert edit.cursorWidth() >= 2, "壁纸下光标过细会在花背景上消失"

        w.set_fields(bg_image="", persist=False)
        ap.refresh_surface_mode()
        ap.apply_input_caret(edit)
        assert edit.cursorWidth() == ap._CURSOR_WIDTH
        assert edit.cursorWidth() >= 2, "非壁纸下 1px 光标同样难看清"
    finally:
        edit.deleteLater()
        w.set_fields(bg_image="", persist=False)
        ap.refresh_surface_mode()


def _contrast_ratio(hex_a: str, hex_b: str) -> float:
    """WCAG 对比度（1:1 ~ 21:1），用于判定「压在壁纸上的前景是否看得见」。"""
    def _lum(h: str) -> float:
        c = QColor(h)
        def _lin(v: float) -> float:
            v /= 255.0
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
        return (0.2126 * _lin(c.red()) + 0.7152 * _lin(c.green())
                + 0.0722 * _lin(c.blue()))
    la, lb = _lum(hex_a), _lum(hex_b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def test_input_foreground_picks_higher_contrast_side(tmp_path):
    """输入框前景（文字/光标/描边同源）必须取与脚下壁纸**对比度更高**的一侧。

    回归（用户反馈）：自定义背景下「鼠标点击输入框没有闪烁光标」。光标本身在正常闪烁
    （原生光标 + 620ms 周期，见 test_input_caret_blinks_with_native_caret），问题是
    **配色**：此前壁纸态把前景硬编码成纯黑，而壁纸会压一层可调深度的纱 —— 深色壁纸
    （#14161B）下光标处合成底与纯黑的对比度实测只有 1.20:1，等于看不见。
    改为按壁纸实际绘制结果的相对亮度在黑白之间二选一后，四种典型底色都 ≥ 4:1。
    """
    from zhuzhu_Copilot.core import app_wallpaper as aw
    from zhuzhu_Copilot.ui import agent_panel as ap

    cases = [
        ("#F2F4F8", "#000000", "亮壁纸取黑"),     # 近白
        ("#3A7BD5", "#000000", "亮蓝取黑"),
        ("#8A8F98", "#000000", "中灰取黑"),
        ("#14161B", "#FFFFFF", "暗壁纸取白"),     # 用户实际踩到的场景
    ]
    for color, expect, why in cases:
        w.set_fields(bg_image=_solid_png(tmp_path, color), bg_blur=0, bg_dim=0,
                     persist=False)
        ap.refresh_surface_mode()
        try:
            fg = ap._input_fg()
            bd = ap._input_border()
            lum = aw.rendered_luminance(ap._base_color("BG"))
            assert lum is not None, "壁纸生效时亮度必须可测"
            assert fg == expect, f"{color}（{why}）前景应为 {expect}，实得 {fg}"
            assert bd == fg, "描边必须与前景同源（否则边界在花背景上立不住）"
            qss = ap._input_qss()
            assert f"color: {fg}" in qss and f"border: 1px solid {bd}" in qss
            # 用真实合成底验证对比度：拿亮度反推最亮/最暗两种极端底不现实，
            # 这里直接核对比度数学（黑/白对同一亮度谁更高由 switch 阈值决定）
            ref = "#000000" if lum >= aw.FOREGROUND_SWITCH_LUM else "#FFFFFF"
            assert fg == ref, f"{color} 未取对比度更高的一侧（亮度={lum:.3f}）"
        finally:
            w.set_fields(bg_image="", persist=False)
            ap.refresh_surface_mode()


def test_input_border_and_caret_follow_wallpaper_foreground(tmp_path, monkeypatch):
    """壁纸 + 任意主题下输入框「立得住」：描边与文字/光标同源，聚焦泛光为蓝色。

    回归场景：浅灰描边压在花背景上糊成「透明」，输入框边界看不出。
    修法：壁纸态描边取与文字/光标同一个高对比色（见 _wallpaper_foreground），
    不再固定纯黑 —— 纯黑在暗壁纸上同样看不见。
    主题用 monkeypatch 固定（不依赖机器上 QSettings 的真实值），light / dark 各验一遍。
    """
    from PyQt6.QtGui import QPalette
    from PyQt6.QtWidgets import QApplication, QPlainTextEdit
    from zhuzhu_Copilot.ui import agent_panel as ap

    app = QApplication.instance()
    assert app is not None

    # ── 壁纸 + 任意主题：描边 == 前景（文字/光标），且对底色有足够对比度 ──
    for theme in ("light", "dark"):
        monkeypatch.setattr(ap, "_resolve_theme", lambda t=theme: t)
        w.set_fields(bg_image=_solid_png(tmp_path), bg_blur=0, bg_dim=0, persist=False)
        ap.refresh_surface_mode()
        try:
            fg = ap._input_fg()
            qss = ap._input_qss()
            assert fg in ("#000000", "#FFFFFF"), f"壁纸下前景必须黑白二选一，实得 {fg}"
            assert ap._input_border() == fg, f"{theme}主题壁纸下描边须与前景同源"
            assert f"border: 1px solid {fg}" in qss
            assert f"color: {fg}" in qss
            assert "background: transparent" in qss, "前置：壁纸下底仍全透明"
            # 实心蓝底（#2F52D8）下应取到 ≥3:1 的对比度（可读性底线）
            assert _contrast_ratio(fg, "#2F52D8") >= 3.0, "前景对底色的对比度不足"

            # 光标色随文字色（Qt 无独立 caret-color API）：用非默认色反证
            # 「QSS color → 调色板」同步机制成立，再结合上面的 color: <fg>
            # 即可确认光标与文字同色（默认调色板本就是黑，直接断言会恒真，必须反证）。
            probe = QPlainTextEdit()
            try:
                probe.setStyleSheet("QPlainTextEdit { color: #010203; }")
                probe.show()
                probe.ensurePolished()
                app.processEvents()
                assert probe.palette().color(QPalette.ColorRole.Text).name() == "#010203", \
                    "QSS color 未同步调色板：光标色随文字色的链路失效"
            finally:
                probe.deleteLater()

            # 聚焦泛光为蓝色调（蓝分量显著高于红绿）
            holder = QPlainTextEdit()
            c = ap._focus_glow_effect(holder).color()
            holder.deleteLater()
            assert c.blue() > c.red() and c.blue() > c.green(), "聚焦泛光必须是蓝色调"
        finally:
            w.set_fields(bg_image="", persist=False)
            ap.refresh_surface_mode()

    # ── 离开壁纸：恢复主题样式（四状态全覆盖：light/dark × 壁纸/非壁纸） ──
    monkeypatch.setattr(ap, "_resolve_theme", lambda: "light")
    ap.refresh_surface_mode()
    assert ap._input_border() == ap.BORDER, "离开壁纸模式恢复主题描边"
    assert ap._input_fg() == ap.TEXT
    assert "#000000" not in ap._input_qss()


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
    """拖动中不得提交：每一格都重算一次高斯模糊会明显掉帧。

    拖动语义 = sliderPressed → valueChanged×N → sliderReleased，只有松手才落地；
    键盘方向键 / 滚轮改值不发 sliderReleased，走 valueChanged 直提（否则调节失效）。
    """
    from zhuzhu_Copilot.ui import agent_panel as ap
    dlg = ap._AgentSettingsDialog()
    try:
        dlg._stash_page = dlg._build_wallpaper_page()
        slider = dlg.wallpaper_blur
        before = w.params().bg_blur
        slider.sliderPressed.emit()            # 模拟按住开始拖动
        slider.setValue(slider.maximum())      # 拖动过程只改值
        assert w.params().bg_blur == before, "拖动中提交会让每一格都重算模糊"
        slider.sliderReleased.emit()           # 松手：一次拖动只提交一次
        assert w.params().bg_blur == float(slider.maximum())
        # 键盘/滚轮改值（无拖动）：不发 sliderReleased，必须即改即提
        slider.setValue(0)
        assert w.params().bg_blur == 0.0, "非拖动改值应直接落地，否则键盘/滚轮调节失效"
    finally:
        dlg.deleteLater()


# ══════════════ 例外面：不绘制壁纸的窗口/弹层保持不透明 ══════════════
# 透明覆写只对「能透出壁纸的主界面容器」成立；对话框 / 消息框 / 菜单 / Tooltip /
# 停靠子窗口都是独立不透明窗口，身后没有壁纸可透 —— QSS 拿到 transparent 后不填底，
# 按「未绘制」渲染成纯黑（用户反馈：浅色模式设置页整页发黑）。它们必须回落原始色板。

def _rule_of(qss: str, selector: str) -> str:
    """从 QSS 文本里取出含 selector 的规则块（到该规则结束的 '}' 为止）。"""
    for part in qss.split("}"):
        if selector in part:
            return part + "}"
    return ""


def test_settings_dialog_surfaces_stay_opaque_on_wallpaper(tmp_path):
    """设置对话框（用户反馈的「浅色模式设置页背景为黑色」）：根底必须回落实色。"""
    from PyQt6.QtWidgets import QApplication
    from zhuzhu_Copilot.ui import agent_panel as ap

    w.set_fields(bg_image=_solid_png(tmp_path), persist=False)
    ap.refresh_surface_mode()
    assert ap.BG == "transparent", "前置：壁纸模式透明覆写已生效"

    dlg = ap._AgentSettingsDialog()
    try:
        qss = dlg.styleSheet()
        root = _rule_of(qss, "QDialog#agentSettingsDlg")
        assert "transparent" not in root, f"设置对话框根底透明会渲染成纯黑：{root}"
        assert ap._base_color("BG") in root, "设置对话框根底应为原始色板（浅色下浅底）"
        inputs = _rule_of(qss, "QLineEdit, QPlainTextEdit, QComboBox")
        assert "transparent" not in inputs, "输入类控件底同样不能透明"
        # 实拍一帧：不透明底（alpha=255），而不是「未绘制」的 alpha=0
        dlg.show()
        app = QApplication.instance()
        for _ in range(3):
            app.processEvents()
        img = dlg.grab().toImage()
        px = img.pixelColor(img.width() // 2, img.height() // 2)
        assert px.alpha() == 255, "设置对话框中心像素必须是实色（不透明）"
    finally:
        dlg.close()
        dlg.deleteLater()


def test_global_dialog_qss_and_scrollbar_keep_base_colors_on_wallpaper(tmp_path):
    """全局弹层底（原生对话框 / 菜单 / Tooltip / 下拉弹层）与滚动条滑块同规则。"""
    from PyQt6.QtGui import QColor
    from zhuzhu_Copilot.ui import agent_panel as ap

    w.set_fields(bg_image=_solid_png(tmp_path), persist=False)
    ap.refresh_surface_mode()

    g = ap._global_dialog_qss()
    for sel in ("QMessageBox, QInputDialog", "QMenu {", "QToolTip {",
                "QComboBox QAbstractItemView {"):
        rule = _rule_of(g, sel)
        assert "transparent" not in rule, f"{sel} 透明底会渲染成纯黑：{rule}"
    assert ap._base_color("PANEL") in g

    expected = QColor(ap._base_color("BORDER"))
    expected.setAlphaF(ap._SCROLLBAR_OPACITY)
    assert ap._scrollbar_handle() == (
        f"rgba({expected.red()},{expected.green()},{expected.blue()},"
        f"{expected.alpha()})"), "滚动条滑块取原始边框色（不能是「黑透明」）"


def test_dialogs_and_subwindows_use_base_surfaces_on_wallpaper(tmp_path):
    """确认 / 多行输入 / 提问 / MCP / 服务商弹窗与停靠子窗口：窗口根底一律原始色板。"""
    from zhuzhu_Copilot.ui import agent_panel as ap

    w.set_fields(bg_image=_solid_png(tmp_path), persist=False)
    ap.refresh_surface_mode()

    base_panel = ap._base_color("PANEL")
    dialogs = [
        ap._MultiLineInputDialog("标题", "说明", "初始文本"),
        ap._ConfirmDialog("write_file", {"path": "C:/tmp/x.txt"}, "safe"),
        ap._AskUserDialog("问题", ["选项 A", "选项 B"], False),
        ap._McpServerDialog(),
        ap._ProviderDialog(),
    ]
    try:
        for dlg in dialogs:
            qss = dlg.styleSheet()
            name = type(dlg).__name__
            root = _rule_of(qss, "QDialog {")
            assert "transparent" not in root, f"{name} 窗口底不能透明：{root}"
            assert (base_panel in root) or (ap._base_color("BG") in root), \
                f"{name} 窗口底应回落原始色板：{root}"
        tw = ap.TodosWindow()
        try:
            # 侧栏（todos/工作树/Git/代码预览）是**壁纸透出**面板：悬浮时由
            # `_RoundedFloatWindow.paintEvent` 自绘本窗口那块壁纸，融入 dock 时透出主窗口
            # 壁纸 —— 因此根底必须透明（画成实色块会挡住用户自定义背景）。
            # 见 test_side_panels_translucent_on_wallpaper / test_float_panel_self_paints_wallpaper。
            assert tw._wallpaper_translucent is True, "侧栏应声明为壁纸透出面板"
            assert "transparent" in tw.styleSheet(), "壁纸下侧栏根底须透明（让壁纸透出）"
        finally:
            tw.close()
            tw.deleteLater()
    finally:
        for dlg in dialogs:
            dlg.close()
            dlg.deleteLater()


def test_window_surfaces_match_theme_without_wallpaper():
    """无壁纸时窗口表面色与主题完全一致（本次修复对非壁纸模式零行为变化）。"""
    from zhuzhu_Copilot.ui import agent_panel as ap

    # 本用例依赖「无壁纸」前提：同文件其它用例会设壁纸且不清除，这里显式复位
    # （透明覆写是模块级状态，跨用例泄漏会让断言读到上一个用例的透明态）。
    w.set_fields(bg_image="", persist=False)
    ap.refresh_surface_mode()
    assert ap.BG != "transparent" and ap.PANEL != "transparent"
    assert ap._panel_surface("BG") != "transparent", "无壁纸时面板表面必须是实色"
    dlg = ap._AgentSettingsDialog()
    try:
        root = _rule_of(dlg.styleSheet(), "QDialog#agentSettingsDlg")
        assert f"background: {ap.BG};" in root
    finally:
        dlg.close()
        dlg.deleteLater()


# ══════════════ 导入期安全：无 QApplication 时不因创建 QPixmap 崩溃 ══════════════

def test_import_agent_panel_with_wallpaper_before_app(tmp_path):
    """无 QApplication 时导入 agent_panel：即使已配置壁纸也不得崩（QPixmap 守卫）。

    回归（本机实测）：进程级崩溃 0xC0000409 —— 用户目录配好壁纸后，pytest
    收集阶段（先于任何 fixture）导入 agent_panel，模块顶层 apply_theme →
    _apply_surface_mode → app_wallpaper.active() → _source_pixmap 在无应用实例时
    创建 QPixmap → Qt 致命错误整进程猝死（表现为「全量测试无法收集」）。
    修法：无 QGuiApplication 时 _source_pixmap 返回 None（active() 安全返回 False），
    控件构建时 refresh_surface_mode 会在有实例后重算。
    """
    import json
    import subprocess
    import sys
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    home = tmp_path / "probe_home"
    bg_dir = home / "agent" / "backgrounds"
    bg_dir.mkdir(parents=True)
    img_dst = bg_dir / "wall.png"
    img_dst.write_bytes(Path(_solid_png(tmp_path)).read_bytes())
    (home / "agent" / "wallpaper.json").write_text(
        json.dumps({"bg_image": str(img_dst), "bg_fit": "cover",
                    "bg_blur": 0, "bg_dim": 50}), encoding="utf-8")

    code = (
        "import os, sys\n"
        "os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')\n"
        "sys.path.insert(0, " + repr(str(repo / "src")) + ")\n"
        "from zhuzhu_Copilot import app_identity\n"
        "from pathlib import Path\n"
        "app_identity._home = lambda: Path(" + repr(str(home)) + ")\n"
        "app_identity._migrated = True\n"
        "from zhuzhu_Copilot.core import app_wallpaper as w\n"
        "assert w.active() is False, '无应用实例时 active() 必须安全返回 False'\n"
        "from zhuzhu_Copilot.ui import agent_panel\n"
        "print('NO-APP-IMPORT-OK')\n"
    )
    p = subprocess.run([sys.executable, "-X", "utf8", "-c", code],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       cwd=str(repo), timeout=240)
    out = p.stdout.decode("utf-8", errors="replace")
    # Windows 进程级崩溃的退出码（0xC0000409）：此前的复现形态
    assert p.returncode == 0, \
        f"无应用实例导入崩溃（returncode={p.returncode}，0xC0000409=QPixmap 猝死）：" \
        f"{out[-800:]}"
    assert "NO-APP-IMPORT-OK" in out


# ══════════════ 侧栏面板：工作树 / Git / 代码预览（壁纸透出） ══════════════
# 用户要求：主页的这三个面板在自定义背景下不得画成实色块 —— 背景色与边框色设置
# 为透明。实现：融入 dock 时透出主窗口壁纸；悬浮时由 _RoundedFloatWindow.paintEvent
# 自绘「模糊壁纸 + 压暗纱」（否则透明窗口身后无壁纸，会渲染成纯黑——已复现过的缺陷）。

def test_side_panels_translucent_on_wallpaper(tmp_path):
    """壁纸模式：侧栏面板（含任务清单）窗口底 / 内容面 / 描边一律透明。"""
    from zhuzhu_Copilot.ui import agent_panel as ap

    w.set_fields(bg_image=_solid_png(tmp_path), bg_blur=0, bg_dim=0, persist=False)
    ap.refresh_surface_mode()
    assert ap.BG == "transparent", "前置：壁纸透明覆写已生效"

    # 开关：四个侧栏面板参与（扩展面板保持实色卡片）
    assert ap.WorktreeWindow._wallpaper_translucent is True
    assert ap.GitLogWindow._wallpaper_translucent is True
    assert ap.CodePreviewWindow._wallpaper_translucent is True
    assert ap.TodosWindow._wallpaper_translucent is True

    wt = ap.WorktreeWindow()
    git = ap.GitLogWindow()
    code = ap.CodePreviewWindow()
    todos = ap.TodosWindow()
    try:
        for win, name in ((wt, "wtWin"), (git, "gitLogWin"), (code, "codeWin"),
                          (todos, "todosWin")):
            root = _rule_of(win.styleSheet(), f"QWidget#{name}")
            assert "background: transparent" in root, f"{name} 窗口底须透明：{root}"

        tree = _rule_of(wt.tree.styleSheet(), "QTreeWidget {")
        assert "background: transparent" in tree, f"工作树内容面须透明：{tree}"
        assert "border: 1px solid transparent" in tree, f"工作树描边须透明：{tree}"

        lst = _rule_of(git.list.styleSheet(), "QListWidget {")
        assert "background: transparent" in lst, f"Git 列表须透明：{lst}"
        assert "border: 1px solid transparent" in lst, f"Git 列表描边须透明：{lst}"

        for view, sel in ((code.html_view, "QTextBrowser"),
                          (code.text, "QPlainTextEdit")):
            rule = _rule_of(view.styleSheet(), sel)
            assert "background: transparent" in rule, f"{sel} 内容面须透明：{rule}"
            assert "border: 1px solid transparent" in rule, f"{sel} 描边须透明：{rule}"

        panel_root = _rule_of(todos.panel.styleSheet(), "QWidget#todosPanel")
        assert "background: transparent" in panel_root, \
            f"任务清单面板底须透明（用户反馈：壁纸下背景仍是白色）：{panel_root}"
    finally:
        for win in (wt, git, code, todos):
            win.close()
            win.deleteLater()


def test_side_panels_fall_back_to_solid_without_wallpaper():
    """非壁纸模式：三个面板回落原始色板实色（本次改动零行为变化）。"""
    from zhuzhu_Copilot.ui import agent_panel as ap

    assert not ap._SURFACE_TRANSPARENT, "前置：当前应处于非壁纸态（用例间已复位）"
    wt = ap.WorktreeWindow()
    git = ap.GitLogWindow()
    code = ap.CodePreviewWindow()
    try:
        root = _rule_of(wt.styleSheet(), "QWidget#wtWin")
        assert ap._base_color("BG") in root and "transparent" not in root
        tree = _rule_of(wt.tree.styleSheet(), "QTreeWidget {")
        assert ap._base_color("PANEL") in tree
        assert ap._base_color("BORDER") in tree
        assert "border: 1px solid transparent" not in tree
        lst = _rule_of(git.list.styleSheet(), "QListWidget {")
        assert ap._base_color("PANEL") in lst and ap._base_color("BORDER") in lst
        html = _rule_of(code.html_view.styleSheet(), "QTextBrowser")
        assert ap._base_color("PANEL") in html
    finally:
        for win in (wt, git, code):
            win.close()
            win.deleteLater()


def test_dock_column_translucent_on_wallpaper(tmp_path):
    """dock 侧栏容器与面板同策略：壁纸下透明（栏缝同样透出壁纸），否则实色。"""
    from zhuzhu_Copilot.ui import agent_panel as ap

    w.set_fields(bg_image=_solid_png(tmp_path), bg_blur=0, bg_dim=0, persist=False)
    ap.refresh_surface_mode()
    col = ap.AgentPanel._make_dock_column(None, "left")
    try:
        assert "background: transparent" in col.styleSheet(), col.styleSheet()
    finally:
        col.deleteLater()

    w.set_fields(bg_image="", persist=False)
    ap.refresh_surface_mode()
    col2 = ap.AgentPanel._make_dock_column(None, "right")
    try:
        assert ap._base_color("BG") in col2.styleSheet(), col2.styleSheet()
        assert "transparent" not in col2.styleSheet()
    finally:
        col2.deleteLater()


def test_dropdown_popups_keep_opaque_surface_on_wallpaper(tmp_path):
    """下拉菜单 / 候选列表的弹出层：壁纸模式下也必须是实色底。

    回归（用户反馈）：浅色 + 自定义背景下，下拉菜单弹出列表仍是深色 —— 弹层是
    独立不透明层，QSS 拿到 transparent 后不填色即渲染成深色（与对话框同一成因）。
    """
    from zhuzhu_Copilot.ui import agent_panel as ap

    w.set_fields(bg_image=_solid_png(tmp_path), persist=False)
    ap.refresh_surface_mode()   # 重建派生样式常量（_QCOMBO 属其中之一）

    popup = _rule_of(ap._QCOMBO, "QComboBox QAbstractItemView")
    assert "transparent" not in popup, f"下拉弹层不能透明（会渲染成深色）：{popup}"
    assert ap._base_color("PANEL") in popup
    assert ap._base_color("BORDER") in popup

    holder = ap.AgentPanel.__new__(ap.AgentPanel)   # 只调纯拼接方法，不触发 Qt 初始化
    cand = ap.AgentPanel._cmd_list_qss(holder)
    assert "transparent" not in cand, f"命令候选弹层不能透明：{cand}"
    assert ap._base_color("PANEL") in cand


def test_float_panel_self_paints_wallpaper(tmp_path):
    """悬浮形态：窗口自绘壁纸（中心像素=壁纸色），而非透明露黑 / 实色块。"""
    from PyQt6.QtWidgets import QApplication
    from zhuzhu_Copilot.ui import agent_panel as ap

    w.set_fields(bg_image=_solid_png(tmp_path), bg_blur=0, bg_dim=0, persist=False)
    ap.refresh_surface_mode()
    app = QApplication.instance()
    wt = ap.WorktreeWindow()
    try:
        assert wt.dock_state == "float"
        wt.resize(280, 360)
        wt.show()
        for _ in range(4):
            app.processEvents()
        img = wt.grab().toImage()
        c = img.pixelColor(img.width() // 2, img.height() // 2)
        assert _close(c, QColor("#2F52D8"), 14), \
            f"悬浮面板中心应自绘壁纸色：{c.name()}"
    finally:
        wt.close()
        wt.deleteLater()
        w.set_fields(bg_image="", persist=False)
        ap.refresh_surface_mode()

    # 对照：无壁纸 → 回落实色（不再是壁纸色）
    wt2 = ap.WorktreeWindow()
    try:
        wt2.resize(280, 360)
        wt2.show()
        for _ in range(4):
            app.processEvents()
        img2 = wt2.grab().toImage()
        c2 = img2.pixelColor(img2.width() // 2, img2.height() // 2)
        assert not _close(c2, QColor("#2F52D8"), 14), "无壁纸时不应是壁纸色"
    finally:
        wt2.close()
        wt2.deleteLater()


def test_docked_panel_shows_wallpaper_through(tmp_path):
    """融入 dock（用户主用形态）：宿主自绘壁纸 + 透明 dock 栏 + 真面板 → 面板区域透出壁纸。

    合成宿主与 AgentPanel.paintEvent 同源（app_wallpaper.paint），面板经 _set_dock_ui(True)
    融入 —— 复刻用户「主页 dock 栏」链路；若把面板画成实色块，该像素将是主题底色而非壁纸色。
    """
    from PyQt6.QtGui import QPainter
    from PyQt6.QtWidgets import QApplication, QDialog, QVBoxLayout
    from zhuzhu_Copilot.core import app_wallpaper as aw
    from zhuzhu_Copilot.ui import agent_panel as ap

    w.set_fields(bg_image=_solid_png(tmp_path), bg_blur=0, bg_dim=0, persist=False)
    ap.refresh_surface_mode()

    class _Host(QDialog):
        def paintEvent(self, ev):
            painter = QPainter(self)
            done = False
            try:
                done = aw.paint(painter, self.rect(), ap._base_color("BG"))
            finally:
                painter.end()
            if not done:
                super().paintEvent(ev)

    host = _Host()
    host.resize(560, 620)
    hlay = QVBoxLayout(host)
    hlay.setContentsMargins(0, 0, 0, 0)
    col = ap.AgentPanel._make_dock_column(None, "left")
    col.setFixedWidth(280)
    hlay.addWidget(col)
    wt = ap.WorktreeWindow()
    try:
        wt.setParent(col)
        wt.dock_state = "left"
        lay = col.property("_dock_lay")
        lay.addWidget(wt)
        wt._set_dock_ui(True)
        for j in range(lay.count()):
            it = lay.itemAt(j)
            if it.spacerItem() is not None:
                lay.setStretch(j, 0)
            elif it.widget() is wt:
                lay.setStretch(j, 1)
        host.show()
        app = QApplication.instance()
        for _ in range(6):
            app.processEvents()
        img = host.grab().toImage()
        c = img.pixelColor(140, 300)   # 面板区域中心
        assert _close(c, QColor("#2F52D8"), 14), \
            f"dock 面板区域应透出壁纸：{c.name()}（仍被画成实色块？）"
    finally:
        host.close()
        host.deleteLater()
        wt.close()
        wt.deleteLater()
        w.set_fields(bg_image="", persist=False)
        ap.refresh_surface_mode()
