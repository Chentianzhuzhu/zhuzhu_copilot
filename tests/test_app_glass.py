# -*- coding: utf-8 -*-
"""全局磨砂玻璃内核（`core/app_glass.py`）的回归测试。

守护四类契约：
1. **参数是唯一事实来源**：设置页滑杆、校验夹紧、持久化、agent 工具都从
   `PARAM_SPECS` 派生 —— 加一个可调维度不需要改任何渲染代码；
2. **脏数据不能变成坏外观**：损坏的 glass.json 必须回落 *默认值*，
   回落成 0 会把「模糊关掉、磨砂全无」伪装成正常；
3. **模糊真的发生且只算一次**：模糊是 Pillow 真高斯（不是空转），
   且同一参数签名下 `GlassBackground` 复用同一份位图（面板级缓存的落点）；
4. **绘制受参数驱动**：透明度 / 总开关能真正改变或抑制像素输出。
"""
import json
import os
import re
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest                                                        # noqa: E402
from PyQt6.QtCore import QSize, Qt                                   # noqa: E402
from PyQt6.QtGui import QColor, QPainter, QPixmap                    # noqa: E402
from PyQt6.QtWidgets import QApplication, QWidget                    # noqa: E402

from zhuzhu_Copilot.core import app_glass as g                       # noqa: E402

app = QApplication.instance() or QApplication([])

MODULE_PATH = (Path(__file__).resolve().parents[1] / "src" / "zhuzhu_Copilot"
               / "core" / "app_glass.py")
COLOR_RE = re.compile(r"#[0-9A-Fa-f]{6}\b")

# 内核允许出现的颜色字面量：只有磨砂底色（浅色白 / 深色石墨黑）。
# 高光与描边一律用 QColor(255,255,255,alpha) 现算 —— 出现别的彩色即违反四色规范。
ALLOWED_COLORS = {"#FFFFFF", "#101216"}


@pytest.fixture(autouse=True)
def _fresh_glass():
    """每个用例从干净的默认参数开始（内核是进程级单例，必须显式复位）。"""
    g.invalidate()
    g.set_params(g.GlassParams(), persist=False)
    yield
    g.set_params(g.GlassParams(), persist=False)
    g.set_theme_probe(None)
    g.invalidate()


def _solid(size=(64, 64), color="#FFFFFF") -> QPixmap:
    pm = QPixmap(*size)
    pm.fill(QColor(color))
    return pm


# 亮度口径：QPixmap.toImage() 在「无 alpha 通道」时给出 Format_RGB32，此时
# pixelColor() 的值是**相对黑底预乘**的（白@alpha32 读出来是 (32,32,32,255)），
# alpha() 恒为 255。所以断言一律看「光量」= r+g+b：它在「预乘」与「非预乘」
# 两种格式下都随实际绘制量单调变化，不依赖 alpha 通道是否被保留。
def _light(pm: QPixmap) -> int:
    """整图光量总和 —— 反映「画了多少」，与格式无关。"""
    img = pm.toImage()
    total = 0
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            total += c.red() + c.green() + c.blue()
    return total


def _max_light(pm: QPixmap) -> int:
    """最亮像素的光量 —— 描边压在圆角边界上，按固定坐标取样会落到圆角外的透明区。"""
    img = pm.toImage()
    best = 0
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            best = max(best, c.red() + c.green() + c.blue())
    return best


# ─────────────────────────── 1) 参数层 ───────────────────────────

def test_param_specs_cover_the_five_documented_knobs():
    """用户要求可调的五个维度必须在规格表里，且 key 稳定（工具与 UI 都按 key 寻址）。"""
    assert g.param_keys() == ("blur", "frost", "edge", "opacity", "liquid")
    for s in g.PARAM_SPECS:
        assert s.low < s.high
        assert s.low <= s.default <= s.high
        assert s.label and s.hint


def test_format_value_is_derived_from_spec():
    """显示格式由规格派生（百分比 / 带单位），避免 UI 再写一套格式化。"""
    p = g.params()
    assert g.format_value(g.spec("blur"), p.blur) == "18px"
    assert g.format_value(g.spec("frost"), p.frost) == "42%"


def test_dirty_values_fall_back_to_default_not_zero():
    """损坏的配置必须回落默认值 —— 回落 0 会静默变成「无模糊无磨砂」的坏外观。"""
    p = g.GlassParams.from_dict({"blur": "abc", "frost": None, "edge": [], "opacity": {}})
    assert p.blur == g.spec("blur").default
    assert p.frost == g.spec("frost").default
    assert p.edge == g.spec("edge").default
    assert p.opacity == g.spec("opacity").default


def test_values_are_clamped_and_snapped():
    p = g.params()
    assert p.with_param("blur", 999).blur == g.spec("blur").high
    assert p.with_param("blur", -999).blur == g.spec("blur").low
    assert p.with_param("frost", 5).frost == 1.0
    assert p.with_param("blur", 17.4).blur == 17.0        # 吸附到步长 1


def test_unknown_keys_are_ignored_for_forward_compat():
    """老版本读到新版本的参数文件不能崩，新版本读老文件也不能丢默认。"""
    p = g.params()
    assert p.with_param("no_such_knob", 1) == p
    assert g.GlassParams.from_dict({"future_knob": 3}).blur == g.spec("blur").default
    assert g.GlassParams.from_dict({"bg_fit": "乱写"}).bg_fit in g.BG_FITS


def test_roundtrip_preserves_everything():
    p = g.params().with_fields(bg_image="C:/x.png", bg_fit="contain",
                               liquid_anim=True).with_param("blur", 30)
    assert g.GlassParams.from_dict(p.to_dict()) == p


def test_persistence_writes_real_json_under_data_root():
    ok = g.set_param("blur", 24)
    assert ok.blur == 24
    path = g._config_path()
    assert path.is_file(), "参数必须真的落盘，否则重启就丢外观"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["blur"] == 24
    assert data["enabled"] is True
    # 清掉内存缓存后必须能从盘上读回来
    g.invalidate()
    assert g.params().blur == 24


def test_corrupt_config_file_does_not_break_startup():
    path = g._config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ 这不是 json", encoding="utf-8")
    g.invalidate()
    p = g.params()
    assert p.blur == g.spec("blur").default


def test_subscribe_accepts_plain_functions_and_fires():
    """订阅要能吃普通函数（曾只支持绑定方法 → 传 lambda 直接 TypeError）。"""
    hits = []

    def cb():
        hits.append(1)

    g.subscribe(cb)
    try:
        g.set_param("blur", 30, persist=False)
        assert hits, "参数变化必须广播给订阅者"
    finally:
        g._listeners[:] = [r for r in g._listeners if r() is not cb]


def test_one_subscriber_raising_does_not_block_the_rest():
    """单个订阅者抛异常不能中断广播链（否则后面所有窗口都不再刷新）。"""
    hits = []

    def bad():
        raise RuntimeError("boom")

    def good():
        hits.append(1)

    g.subscribe(bad)
    g.subscribe(good)
    try:
        g.set_param("frost", 0.8, persist=False)
        assert hits == [1]
    finally:
        g._listeners[:] = [r for r in g._listeners if r() in (bad, good)]


# ─────────────────────────── 2) 背景资源层 ───────────────────────────

def test_gaussian_blur_actually_blurs_and_is_safe():
    """必须是真模糊：棋盘格模糊后相邻像素差异显著下降。"""
    pm = QPixmap(32, 32)
    pm.fill(QColor("#000000"))
    painter = QPainter(pm)
    for x in range(0, 32, 2):          # 竖条纹 = 高频
        painter.fillRect(x, 0, 1, 32, QColor("#FFFFFF"))
    painter.end()

    sharp = pm.toImage()
    blurred = g.gaussian_blur(pm, 8).toImage()
    sharp_delta = sum(abs(sharp.pixelColor(x, 5).red() - sharp.pixelColor(x + 1, 5).red())
                      for x in range(0, 30))
    blur_delta = sum(abs(blurred.pixelColor(x, 5).red() - blurred.pixelColor(x + 1, 5).red())
                     for x in range(0, 30))
    assert blur_delta < sharp_delta / 4, "模糊没有生效"
    # 半径为 0 / None / 空图都必须原样返回，绝不抛异常
    assert g.gaussian_blur(pm, 0) is pm
    assert g.gaussian_blur(None, 10) is None
    assert g.gaussian_blur(QPixmap(), 10).isNull()


def test_background_is_cached_per_signature(tmp_path):
    """面板级缓存的落点：同一参数签名下必须复用同一份位图，不能每层各模糊一次。"""
    img = tmp_path / "wall.png"
    src = _solid((200, 120), "#2F52D8")
    assert src.save(str(img), "PNG")

    p = g.params().with_fields(bg_image=str(img))
    bg = g.GlassBackground(p, QSize(300, 200))
    first = bg.blurred()
    assert first is not None and not first.isNull()
    assert bg.blurred() is first, "签名未变时应命中缓存"

    bg.set_params(p.with_param("blur", 40))
    second = bg.blurred()
    assert second is not None
    assert second is not first, "模糊半径变化必须让缓存失效"


def test_background_missing_file_returns_none(tmp_path):
    p = g.params().with_fields(bg_image=str(tmp_path / "nope.png"))
    bg = g.GlassBackground(p, QSize(100, 100))
    assert bg.blurred() is None
    assert bg.has_wallpaper() is False


@pytest.mark.parametrize("fit", g.BG_FITS)
def test_every_fit_mode_produces_window_sized_output(fit, tmp_path):
    """四种适配方式都必须正好产出窗口尺寸的位图（否则玻璃层采样会错位）。"""
    img = tmp_path / "w.png"
    _solid((300, 100), "#101216").save(str(img), "PNG")
    p = g.params().with_fields(bg_image=str(img), bg_fit=fit)
    out = g.GlassBackground(p, QSize(160, 90)).blurred()
    assert out is not None
    assert (out.width(), out.height()) == (160, 90)


def test_import_background_copies_into_managed_dir(tmp_path):
    """必须把用户选的图收编进数据目录：直接引用原路径会「重启后背景丢失」。"""
    src = tmp_path / "wall.png"
    _solid((40, 40)).save(str(src), "PNG")
    ok, msg, stored = g.import_background(src)
    assert ok, msg
    assert Path(stored).is_file()
    assert g.backgrounds_dir() in Path(stored).parents
    assert g.params().bg_image == stored

    ok2, msg2, _ = g.import_background(tmp_path / "missing.png")
    assert not ok2 and "不存在" in msg2

    bad = tmp_path / "evil.exe"
    bad.write_bytes(b"MZ")
    ok3, msg3, _ = g.import_background(bad)
    assert not ok3 and "格式" in msg3

    g.clear_background(purge_files=False)
    assert not g.params().bg_image


# ─────────────────────────── 3) 绘制层 ───────────────────────────

def _paint(p, size=(80, 60), origin=(0, 0), bg=None) -> QPixmap:
    pm = QPixmap(*size)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    g.paint_glass_surface(painter, pm.rect(), p,
                          frost_color=p.frost_color(),
                          bg_pixmap=bg, bg_origin=origin, radius=12)
    painter.end()
    return pm


def test_glass_paints_something_when_enabled():
    assert _light(_paint(g.params())) > 0, "开启玻璃却什么都没画出来"


def test_opacity_zero_paints_nothing():
    assert _light(_paint(g.params().with_param("opacity", 0))) == 0, \
        "透明度为 0 时必须完全透出背景"


def test_disabled_paints_nothing():
    assert _light(_paint(g.params().with_fields(enabled=False))) == 0, \
        "关闭玻璃后不应残留任何涂装"


def test_more_frost_and_opacity_actually_obscures_the_backdrop():
    """磨砂程度 / 透明度必须真的改变「遮蔽背景」的程度。

    度量口径说明：不能看亮度。深色主题的磨砂底色是石墨黑，磨砂越强像素越**暗**，
    用亮度会得到完全相反的结论。真正的语义是「背板被遮住多少」，所以把玻璃画在
    一块与磨砂底色相反的背板上，量它偏离背板多远。
    """
    g.set_theme_probe(lambda: False)          # 固定深色：磨砂底 = 石墨黑
    backdrop = QColor("#FFFFFF")
    frost = g.params().frost_color()
    assert frost.name() == g._FROST_DARK.lower()

    def paint_on_backdrop(p):
        pm = QPixmap(80, 60)
        pm.fill(backdrop)
        painter = QPainter(pm)
        g.paint_glass_surface(painter, pm.rect(), p, frost_color=p.frost_color(),
                              radius=0)
        painter.end()
        return pm

    def deviation(pm):
        img = pm.toImage()
        total = 0
        for y in range(img.height()):
            for x in range(img.width()):
                c = img.pixelColor(x, y)
                total += (abs(c.red() - backdrop.red())
                          + abs(c.green() - backdrop.green())
                          + abs(c.blue() - backdrop.blue()))
        return total

    weak = deviation(paint_on_backdrop(
        g.params().with_param("frost", 0.05).with_param("opacity", 0.2)))
    strong = deviation(paint_on_backdrop(
        g.params().with_param("frost", 1.0).with_param("opacity", 1.0)))
    assert strong > weak * 3, f"磨砂/透明度没有拉开差距：{weak} vs {strong}"


def test_theme_probe_switches_the_frost_color():
    """浅色主题用白磨砂、深色主题用石墨黑磨砂 —— 否则深色界面会被白色糊住。"""
    g.set_theme_probe(lambda: True)
    assert g.params().frost_color().name() == g._FROST_LIGHT.lower()
    g.set_theme_probe(lambda: False)
    assert g.params().frost_color().name() == g._FROST_DARK.lower()
    g.set_theme_probe(None)
    assert g.theme_is_light() is False        # 未注入时保守按深色处理


def test_broken_theme_probe_does_not_break_rendering():
    def boom():
        raise RuntimeError("probe down")

    g.set_theme_probe(boom)
    assert g.theme_is_light() is False
    assert not _paint(g.params()).isNull()


def test_edge_highlight_adds_bright_border_pixels():
    """边缘高光是唯一能把画面推到最亮的来源：为 0 时整层只有均匀磨砂底。"""
    dim = _max_light(_paint(g.params().with_param("edge", 0.0)))
    bright = _max_light(_paint(g.params().with_param("edge", 1.0)))
    assert bright > dim, f"边缘高光没有产生更亮的描边：{dim} vs {bright}"


def test_liquid_changes_output_without_running_the_animation():
    """液态感的静态表达必须可见（动效默认关闭时滑杆也不能是死键）。"""
    none_ = _light(_paint(g.params().with_param("liquid", 0.0)))
    full = _light(_paint(g.params().with_param("liquid", 1.0)))
    assert full > none_, f"液态感没有产生任何可见差别：{none_} vs {full}"


def test_phase_changes_the_liquid_highlight():
    """相位必须真的改变画面，否则动效定时器就是空转。

    两侧除相位外参数完全相同（含圆角），确保差异只来自相位。
    """
    def render(phase):
        pm = QPixmap(200, 120)
        pm.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pm)
        g.paint_glass_surface(painter, pm.rect(), g.params(),
                              frost_color=QColor("#FFFFFF"), radius=0, phase=phase)
        painter.end()
        return pm

    assert render(0.0).toImage() != render(0.5).toImage()


def test_background_is_sampled_at_the_surface_offset():
    """相邻玻璃层按各自在窗口中的位置取样 —— 接缝处背景才不会错位。"""
    bg = QPixmap(200, 100)
    bg.fill(QColor("#000000"))
    painter = QPainter(bg)
    painter.fillRect(100, 0, 100, 100, QColor("#FFFFFF"))   # 右半白
    painter.end()

    left = _paint(g.params().with_param("frost", 0.0), size=(40, 40), origin=(0, 0), bg=bg)
    right = _paint(g.params().with_param("frost", 0.0), size=(40, 40), origin=(100, 0), bg=bg)
    assert _light(right) > _light(left), "偏移取样没有生效"


def test_paint_never_raises_on_degenerate_input():
    """绘制层在任何输入下都不许抛异常（它会跑在每个控件的 paintEvent 里）。"""
    pm = QPixmap(10, 10)
    painter = QPainter(pm)
    g.paint_glass_surface(painter, pm.rect(), g.params().with_fields(enabled=False))
    g.paint_glass_surface(None, pm.rect(), g.params())
    g.paint_glass_surface(painter, None, g.params())
    g.paint_glass_surface(painter, pm.rect(), g.params(), radius=0, phase=99)
    painter.end()


# ─────────────────────────── 3.5) 不透明表面的磨砂底 ───────────────────────────

def test_wallpaper_tint_is_none_without_wallpaper():
    """无壁纸时不得给出平均色（调用方才能正确回落主题色）。"""
    assert g.wallpaper_tint() is None


def test_wallpaper_tint_comes_from_the_image(tmp_path):
    """平均色必须真的来自图片内容，而不是某个固定值。"""
    img = tmp_path / "w.png"
    _solid((32, 32), "#2F52D8").save(str(img), "PNG")
    g.set_fields(bg_image=str(img), persist=False)
    c = g.wallpaper_tint()
    assert c is not None
    # 蓝色图平均出来应偏蓝：B 通道显著高于 R
    assert c.blue() > c.red() + 40, f"平均色不像来自蓝色图：{c.name()}"
    # 换图后缓存必须失效
    img2 = tmp_path / "w2.png"
    _solid((32, 32), "#E11D48").save(str(img2), "PNG")
    g.set_fields(bg_image=str(img2), persist=False)
    c2 = g.wallpaper_tint()
    assert c2.red() > c2.blue() + 40


def test_frost_surface_color_is_opaque_and_wallpaper_derived(tmp_path):
    """不透明表面（QToolTip 等）的磨砂底必须是**实色**——真半透明会与系统窗口
    默认底色叠成黑色。有壁纸时颜色应来自壁纸而不是主题面板色。"""
    img = tmp_path / "w.png"
    _solid((32, 32), "#101216").save(str(img), "PNG")
    g.set_fields(bg_image=str(img), persist=False)
    s = g.frost_surface_color("#FFFFFF")
    assert len(s) == 9 and s[1:3] == "FF", f"必须是不透明实色：{s}"
    assert s[3:] != "FFFFFF", "有深色壁纸时不应仍等于主题面板色"
    # 玻璃关闭 → 回落主题底色本身
    g.set_fields(enabled=False, persist=False)
    assert g.frost_surface_color("#FFFFFF") == "#FFFFFFFF"


def test_frost_surface_color_returns_fallback_when_glass_off():
    """玻璃关闭时必须原样返回底色（这是「无壁纸用户外观零变化」的保证）。

    有壁纸时颜色来自壁纸平均色的契约由
    `test_frost_surface_color_is_opaque_and_wallpaper_derived` 覆盖。
    """
    g.set_fields(bg_image="", enabled=False, persist=False)
    assert g.frost_surface_color("#FFFFFF") == "#FFFFFFFF"
    assert g.frost_surface_color("#101216") == "#FF101216"


def test_control_fill_tracks_the_glass_params():
    """小控件走 QSS 半透明填充，必须与大面积玻璃同源（否则两种材质对不上）。"""
    assert "rgba" in g.control_fill("#FFFFFF")
    concentrated = g.control_fill("#FFFFFF", g.params().with_param("opacity", 1.0)
                                  .with_param("frost", 1.0))
    assert "1.000" in concentrated
    assert g.control_fill("#FFFFFF", g.params().with_fields(enabled=False)) == "#ffffff"


def test_module_has_no_stray_colors():
    """四色规范：内核里只允许出现两个磨砂底色字面量，其余颜色必须现算。"""
    found = set(COLOR_RE.findall(MODULE_PATH.read_text(encoding="utf-8")))
    assert found <= ALLOWED_COLORS, f"出现规范外的颜色字面量：{found - ALLOWED_COLORS}"


def test_module_has_no_emoji():
    src = MODULE_PATH.read_text(encoding="utf-8")
    emoji = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F]")
    assert not emoji.findall(src)


# ─────────────────────────── 5) 窗口装配 ───────────────────────────

def test_install_is_idempotent_and_registers_surfaces():
    w = QWidget()
    w.resize(300, 200)
    skin = g.install(w, 18)
    assert g.install(w) is skin, "重复 install 必须返回同一皮肤（否则会漏一层订阅）"
    assert g.skin_of(w) is skin

    host = QWidget(w)
    host.setGeometry(10, 10, 100, 60)
    surf = skin.add_surface(host, 12)
    assert surf.parent() is host
    assert surf.geometry() == host.rect(), "玻璃层必须贴合宿主"
    assert surf.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents), \
        "玻璃层绝不能吃掉鼠标事件"

    same = skin.add_surface(host, 12)
    assert same is not surf, "重复挂载应替换旧层"
    assert len(skin._surfaces) == 1, "同一宿主不应累积多层玻璃"

    skin.remove_surface(host)
    assert not skin._surfaces
    skin.dispose()
    w.deleteLater()


def test_liquid_timer_only_runs_when_animation_is_on():
    """液态动效默认关闭 —— 常驻定时器是最主要的性能开销来源。"""
    w = QWidget()
    skin = g.install(w)
    assert skin._timer is None, "默认不应有相位定时器"
    g.set_fields(liquid_anim=True, persist=False)
    assert skin._timer is not None, "开启动效后必须推进相位"
    g.set_fields(liquid_anim=False, persist=False)
    assert skin._timer is None, "关闭动效后必须停掉定时器"
    assert skin.phase == 0.0, "停表后相位应复位，避免下次开启跳变"
    skin.dispose()
    w.deleteLater()
