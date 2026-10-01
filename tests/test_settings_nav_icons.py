# -*- coding: utf-8 -*-
"""AI 设置页左侧导航图标回归（每个导航项一个专属开源矢量图标）。

守护：
1. 覆盖：`_NAV_ITEMS` 里每个键都在 `_LUCIDE_NAV_ICONS` 有图标（新增导航项忘配会点名）；
2. 反向：图标表没有残留死配置（导航项删了图标表要跟着清）；
3. 唯一：任意两个导航项的图形不同（此前 folder 一项被四个导航项复用，靠这条守住）；
4. 对齐：导航项数量 == 右侧页面构建器数量（点错行会串页）；
5. 可绘制：每个图标在导航尺寸下真的画出可见像素（防笔误导致空白图标）；
6. 规范：无 emoji、无硬编码颜色（颜色由 _svg_pixmap 注入）、SVG 合法；
7. 多状态：常态/选中两态像素不同，且颜色不同 → 图标不同（缓存键必须含颜色，
   否则主题切换后会命中上一套主题的图标）；
8. 接线：真对话框的导航项确实用了多状态图标 + 显式 iconSize。
"""
import os
import re
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PyQt6.QtCore import QSize                                          # noqa: E402
from PyQt6.QtGui import QIcon                                           # noqa: E402
from PyQt6.QtSvg import QSvgRenderer                                    # noqa: E402
from PyQt6.QtWidgets import QApplication                                # noqa: E402

from zhuzhu_Copilot.ui import agent_panel as ap                         # noqa: E402

app = QApplication.instance() or QApplication([])

EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F]")
HEX_RE = re.compile(r"#[0-9A-Fa-f]{6}\b")

NAV_KEYS = [k for _, k in ap._NAV_ITEMS]
NAV_SVGS = [ap._LUCIDE_NAV_ICONS[k] for k in NAV_KEYS]
PROBE_COLOR = "#808080"


def _sig(svg: str, size: int, color: str = PROBE_COLOR):
    """像素指纹（逐点读色，不依赖不同 PyQt 版本的 bits() 返回类型）"""
    img = ap._svg_pixmap(svg, size, color).toImage()
    return tuple((img.pixelColor(x, y).alpha(), img.pixelColor(x, y).rgb())
                 for y in range(size) for x in range(size))


def _opaque(svg: str, size: int, color: str = PROBE_COLOR) -> int:
    img = ap._svg_pixmap(svg, size, color).toImage()
    return sum(1 for y in range(size) for x in range(size)
               if img.pixelColor(x, y).alpha() > 8)


# ---------------------------------------------------------------- 覆盖 / 唯一
def test_every_nav_item_has_an_icon():
    """扩展性守卫：新增导航项必须配图标；未配会被点名。"""
    missing = [k for k in NAV_KEYS if k not in ap._LUCIDE_NAV_ICONS]
    assert not missing, f"以下导航项没有图标（请补 _LUCIDE_NAV_ICONS）：{missing}"


def test_no_stale_icon_entries():
    """反向守卫：导航项删除后图标表也要清理，避免残留死配置。"""
    stale = [k for k in ap._LUCIDE_NAV_ICONS if k not in NAV_KEYS]
    assert not stale, f"图标表存在已不存在的导航项：{stale}"


def test_nav_labels_are_unique_and_clean():
    labels = [n for n, _ in ap._NAV_ITEMS]
    assert len(labels) == len(set(labels)), "导航项文案重复"
    bad = [n for n in labels if EMOJI_RE.search(n)]
    assert not bad, f"导航项文案含 emoji：{bad}"


def test_every_nav_icon_graphic_is_distinct():
    """任意两项的 SVG 定义都不同 —— 守住「每项一个专属图标，不复用同一图形」。"""
    dup = {s for s in NAV_SVGS if NAV_SVGS.count(s) > 1}
    assert not dup, f"有导航项复用了同一图标定义（共 {len(dup)} 组）"


def test_every_nav_icon_pixels_are_distinct():
    """进一步按渲染像素判定：即使 SVG 字符串不同，画出来也不能一模一样。"""
    sigs = {k: _sig(svg, ap._NAV_ICON_SIZE) for k, svg in zip(NAV_KEYS, NAV_SVGS)}
    seen: dict = {}
    dups = []
    for k, s in sigs.items():
        if s in seen:
            dups.append((seen[s], k))
        seen[s] = k
    assert not dups, f"导航图标渲染结果重复：{dups}"


def test_nav_count_matches_page_builders():
    """导航项与右侧页面一一对应：数量不等意味着点某行会串页或空页。"""
    dlg = ap._AgentSettingsDialog()
    assert dlg.nav.count() == len(ap._NAV_ITEMS)
    assert len(dlg._page_builders) == len(ap._NAV_ITEMS)
    assert dlg.nav.currentRow() == 0
    dlg.deleteLater()


# ------------------------------------------------------------------ 可绘制
def test_every_nav_icon_draws_visible_pixels():
    blank = [k for k, svg in zip(NAV_KEYS, NAV_SVGS)
             if _opaque(svg, ap._NAV_ICON_SIZE) < 10]
    assert not blank, f"以下导航图标画不出可见像素（疑似路径笔误）：{blank}"


def test_nav_icon_renders_at_actual_nav_size():
    """真实导航尺寸下同样可绘制（图标被缩到 16px 时不能糊成空白）。"""
    blank = [k for k, svg in zip(NAV_KEYS, NAV_SVGS) if _opaque(svg, 16) < 8]
    assert not blank, f"以下导航图标在 16px 下不可见：{blank}"


# -------------------------------------------------------------------- 规范
def test_nav_icons_have_no_hardcoded_color():
    bad = [k for k, svg in ap._LUCIDE_NAV_ICONS.items() if HEX_RE.search(svg)]
    assert not bad, f"导航图标含硬编码颜色（须用 {{color}} 占位）：{bad}"


def test_nav_icons_use_color_placeholder():
    missing = [k for k, svg in ap._LUCIDE_NAV_ICONS.items() if "{color}" not in svg]
    assert not missing, f"导航图标缺少 {{color}} 占位（主题切换无法换色）：{missing}"


def test_nav_icons_are_valid_svg():
    invalid = [k for k, svg in ap._LUCIDE_NAV_ICONS.items()
               if not QSvgRenderer(svg.replace("{color}", PROBE_COLOR)
                                   .encode("utf-8")).isValid()]
    assert not invalid, f"以下导航图标 SVG 非法：{invalid}"


def test_nav_icons_share_lucide_geometry():
    """与顶栏齿轮同源：统一 24×24 viewBox + stroke-width 2，线条粗细一致。"""
    for k, svg in ap._LUCIDE_NAV_ICONS.items():
        assert 'viewBox="0 0 24 24"' in svg, f"{k} viewBox 不是 Lucide 标准的 24×24"
        assert 'stroke-width="2"' in svg, f"{k} stroke-width 与 Lucide 原图不一致"
        assert 'stroke-linecap="round"' in svg and 'stroke-linejoin="round"' in svg


def test_nav_icon_size_is_legible():
    assert ap._NAV_ICON_SIZE >= 16, "导航图标过小，细密图形笔画会糊"


# ------------------------------------------------------------------ 多状态
def test_nav_icon_has_four_states():
    ic = ap._nav_icon(NAV_SVGS[0], ap._NAV_ICON_SIZE,
                      "#111111", "#222222", "#333333", "#444444")
    sz = QSize(ap._NAV_ICON_SIZE, ap._NAV_ICON_SIZE)
    modes = {name: ic.pixmap(sz, mode, QIcon.State.Off)
             for name, mode in (("normal", QIcon.Mode.Normal),
                                ("selected", QIcon.Mode.Selected),
                                ("active", QIcon.Mode.Active),
                                ("disabled", QIcon.Mode.Disabled))}
    assert all(not p.isNull() for p in modes.values()), "存在缺失的图标状态"


def test_selected_state_differs_from_normal():
    """选中态必须与常态不同色，否则选中行图标不会跟着文字一起变强调色。"""
    ic = ap._nav_icon(NAV_SVGS[0], ap._NAV_ICON_SIZE,
                      "#111111", "#222222", "#333333", "#444444")
    sz = QSize(ap._NAV_ICON_SIZE, ap._NAV_ICON_SIZE)
    normal = ic.pixmap(sz, QIcon.Mode.Normal, QIcon.State.Off).toImage()
    selected = ic.pixmap(sz, QIcon.Mode.Selected, QIcon.State.Off).toImage()
    assert normal != selected


def test_icon_cache_key_includes_color():
    """同一图形换色必须产出不同图标 —— 否则主题切换会命中旧主题色（默认参数陷阱）。"""
    a = ap._nav_icon(NAV_SVGS[0], ap._NAV_ICON_SIZE,
                     "#101010", "#202020", "#303030", "#404040")
    b = ap._nav_icon(NAV_SVGS[0], ap._NAV_ICON_SIZE,
                     "#A0A0A0", "#B0B0B0", "#C0C0C0", "#D0D0D0")
    sz = QSize(ap._NAV_ICON_SIZE, ap._NAV_ICON_SIZE)
    assert a.pixmap(sz).toImage() != b.pixmap(sz).toImage()


# -------------------------------------------------------------------- 接线
def test_dialog_wires_multistate_icons_and_icon_size():
    dlg = ap._AgentSettingsDialog()
    assert dlg.nav.iconSize() == QSize(ap._NAV_ICON_SIZE, ap._NAV_ICON_SIZE)
    for row, (name, key) in enumerate(ap._NAV_ITEMS):
        item = dlg.nav.item(row)
        assert item.text() == name
        assert not item.icon().isNull(), f"第 {row} 项 {name} 没挂上图标"
        expected = ap._nav_icon(ap._LUCIDE_NAV_ICONS[key], ap._NAV_ICON_SIZE,
                                dlg._DIM, dlg._TEXT, dlg._ACCENT_HOVER, dlg._DIM)
        sz = QSize(ap._NAV_ICON_SIZE, ap._NAV_ICON_SIZE)
        assert item.icon().pixmap(sz).toImage() == expected.pixmap(sz).toImage()
    dlg.deleteLater()


def test_nav_no_longer_reuses_line_icon():
    """导航项改走开源 SVG 后，不应再回退到 _line_icon（防改动被回滚）。

    直接读磁盘源码而不是 `inspect.getsource`：后者的行号来自模块 `__file__`，
    全量跑时若 sys.modules 被其他用例换成另一份副本，会取到错位的代码块，
    让本断言随机失败（本项目已有先例，见 test_tool_icons.py 的 MODULE 同法）。
    """
    src = (Path(__file__).resolve().parents[1] / "src" / "zhuzhu_Copilot"
           / "ui" / "agent_panel.py").read_text(encoding="utf-8")
    init_src = src.split("class _AgentSettingsDialog", 1)[1]
    init_src = init_src.split("def __init__", 1)[1].split("\n    # ---------- 各分组页面", 1)[0]
    assert "_nav_icon(" in init_src
    assert "_line_icon(kind, 16)" not in init_src
    assert "_NAV_ITEMS" in init_src
