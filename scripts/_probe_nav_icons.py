# -*- coding: utf-8 -*-
"""设置页导航图标探针：验证多状态图标真的被 QListView 取用 + 输出预览图。

用法（离屏）：
    QT_QPA_PLATFORM=offscreen python scripts/_probe_nav_icons.py [输出png]

两件事：
1. 抓取真实 QListWidget 的渲染结果，逐行比对「图标区域」像素更接近常态色还是选中色
   —— 用来证明 `_nav_icon` 的 Selected 帧确实被 Qt 取用（不是只构造出来放着）。
2. 输出 14 个图标 × 常态/选中 的对照预览图，供肉眼检查图形是否清晰可辨。
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PyQt6.QtCore import QRectF, QSize, Qt                              # noqa: E402
from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap               # noqa: E402
from PyQt6.QtSvg import QSvgRenderer                                  # noqa: E402
from PyQt6.QtWidgets import QApplication                              # noqa: E402

from zhuzhu_Copilot.ui import agent_panel as ap                       # noqa: E402

app = QApplication.instance() or QApplication([])


def _dist(c: QColor, ref: QColor) -> float:
    return ((c.red() - ref.red()) ** 2 + (c.green() - ref.green()) ** 2
            + (c.blue() - ref.blue()) ** 2) ** 0.5


def probe_live_nav() -> None:
    dlg = ap._AgentSettingsDialog()
    dlg.nav.resize(176, 687)
    dlg.nav.setCurrentRow(4)                       # 第 5 项选中，其余常态
    app.processEvents()
    img = dlg.nav.grab().toImage()
    dim, acc = QColor(dlg._DIM), QColor(dlg._ACCENT_HOVER)
    # 只统计"墨迹"像素：底色（常态行底 / 选中行底）本身距强调色可能比距淡灰更近，
    # 若把底色计入会得到「整行都是强调色」的假象。
    bgs = (QColor(dlg._PANEL), QColor(dlg._PANEL2))
    print(f"主题色：常态 {dim.name()} / 选中 {acc.name()}")
    print(f"{'行':>2} {'导航项':<12} {'更接近常态':>9} {'更接近选中':>9}  判定")
    ok = True
    for row in range(dlg.nav.count()):
        rect = dlg.nav.visualItemRect(dlg.nav.item(row))
        d_cnt = a_cnt = 0
        # 只扫图标区（跳过最左 3px 的选中竖条与右侧文字）
        for x in range(rect.x() + 8, min(rect.x() + 36, rect.x() + rect.width())):
            for y in range(rect.y(), rect.y() + rect.height()):
                c = img.pixelColor(x, y)
                if c.alpha() < 40:
                    continue
                if min(_dist(c, b) for b in bgs) < 48:
                    continue                     # 底色，不计入
                if _dist(c, dim) < _dist(c, acc):
                    d_cnt += 1
                else:
                    a_cnt += 1
        selected = row == dlg.nav.currentRow()
        hit = (a_cnt > d_cnt) == selected
        ok = ok and hit
        print(f"{row:>2} {ap._NAV_ITEMS[row][0]:<12} {d_cnt:>9} {a_cnt:>9}  "
              f"{'选中' if selected else '常态'} → {'OK' if hit else '不符'}")
    print("\n结论：" + ("Selected 帧已被 Qt 取用（选中行图标为强调色）"
                        if ok else "选中行图标未体现强调色，多状态未被取用"))
    dlg.deleteLater()


def render_sheet(out: Path) -> None:
    """输出验收图：每行一个导航项，左侧语义键，中间四态 3 倍放大（看细节），
    右侧原始 18px（看真实尺寸下的可读性）。

    说明：offscreen 平台没有任何字体（QFontDatabase.families() 为空），中文标签必然
    渲染成方块，故此处用 ASCII 语义键代替文案。
    """
    zoom, pad, key_w = 3, 8, 104
    cell = ap._NAV_ICON_SIZE * zoom + 8          # 放大后的图标 + 内边距
    native_w = 40
    states = (("常态", "#5F6B7E"), ("悬停", "#1B2433"),
              ("选中", "#2B51D1"), ("深色", "#9BA3B0"))
    rows = len(ap._NAV_ITEMS)
    w = key_w + cell * len(states) + native_w + pad * 2
    h = 34 + rows * (cell + pad) + pad
    sheet = QImage(w, h, QImage.Format.Format_RGB32)
    sheet.fill(QColor("#FFFFFF"))
    p = QPainter(sheet)
    f = p.font()
    f.setPointSize(9)
    p.setFont(f)
    p.setPen(QColor("#1B2433"))
    p.drawText(pad, 22, "设置页导航图标 · Lucide 开源线条矢量图（ISC）· 3× 放大 + 实际尺寸")
    for i, (name, key) in enumerate(ap._NAV_ITEMS):
        y = 34 + i * (cell + pad)
        p.setPen(QColor("#1B2433"))
        p.drawText(pad, y + cell // 2 + 4, key)
        p.setPen(QColor("#8A93A3"))
        p.drawText(pad, y + cell // 2 + 18, f"# {i + 1}")
        svg = ap._LUCIDE_NAV_ICONS[key]
        for j, (label, color) in enumerate(states):
            x = key_w + j * cell
            side = ap._NAV_ICON_SIZE * zoom
            pm = QPixmap(side, side)
            pm.fill(Qt.GlobalColor.transparent)
            pp = QPainter(pm)
            pp.setRenderHint(QPainter.RenderHint.Antialiasing)
            QSvgRenderer(svg.replace("{color}", color).encode("utf-8")).render(
                pp, QRectF(0, 0, side, side))
            pp.end()
            p.drawPixmap(x + 4, y + 4, pm)
            p.setPen(QColor("#D5DCE8"))
            p.drawRect(x + 4, y + 4, side - 1, side - 1)
            p.setPen(QColor("#8A93A3"))
            p.drawText(x + 6, y + cell + 6, label)
        # 实际尺寸（18px）直出：看真实尺寸下笔画是否还分得开
        nx = key_w + cell * len(states) + 8
        p.drawPixmap(nx, y + (cell - ap._NAV_ICON_SIZE) // 2,
                     ap._svg_pixmap(svg, ap._NAV_ICON_SIZE, states[0][1]))
        p.drawPixmap(nx + ap._NAV_ICON_SIZE + 4, y + (cell - ap._NAV_ICON_SIZE) // 2,
                     ap._svg_pixmap(svg, ap._NAV_ICON_SIZE, states[2][1]))
        p.setPen(QColor("#8A93A3"))
        p.drawText(nx, y + cell + 6, "18px")
    p.end()
    sheet.save(str(out), "PNG")
    print(f"预览图：{out}  ({w}×{h})")


if __name__ == "__main__":
    probe_live_nav()
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "logs" / "nav_icons_preview.png"
    render_sheet(target)
