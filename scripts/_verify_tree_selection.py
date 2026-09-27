"""工作树选中胶囊宽度自适应的离屏验证探针。

目的：证明 _ContentSelectionDelegate 绘制的选中/悬停胶囊宽度确实随「目录名长度」
变化，而不是所有行等宽（历史 bug：QSS 的 item:hover 整行铺蓝，观感上"统一长度"）。

用法：python scripts/_verify_tree_selection.py
退出码 0 = 通过；非 0 附带失败原因。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "src"))

from PyQt6.QtCore import QRect                          # noqa: E402
from PyQt6.QtGui import QColor, QPainter, QPixmap        # noqa: E402
from PyQt6.QtWidgets import (                            # noqa: E402
    QApplication, QStyle, QStyleOptionViewItem, QTreeWidget, QTreeWidgetItem)

app = QApplication([])

from winapp_migrator.ui import agent_panel as ap         # noqa: E402
from winapp_migrator.ui.agent_panel import _ContentSelectionDelegate  # noqa: E402

ROW_W, ROW_H = 640, 26
NAMES = ("a", "docs", "a_medium_directory_name", "a_very_long_directory_name_example_here")


def capsule_width(tree, delegate, item, selected: bool) -> int:
    """把单项画到离屏 pixmap，返回「深蓝实色」像素的最右边界（即胶囊宽度）"""
    idx = tree.indexFromItem(item)
    opt = QStyleOptionViewItem()
    opt.rect = QRect(0, 0, ROW_W, ROW_H)
    opt.state = (QStyle.StateFlag.State_Enabled
                 | (QStyle.StateFlag.State_Selected if selected
                    else QStyle.StateFlag.State_MouseOver))
    pix = QPixmap(ROW_W, ROW_H)
    pix.fill(QColor("#000000"))
    p = QPainter(pix)
    delegate.paint(p, opt, idx)
    p.end()
    last = 0
    img = pix.toImage()
    for x in range(ROW_W):
        for y in range(ROW_H):
            c = QColor(img.pixel(x, y))
            # 胶囊 = 偏蓝像素（实色蓝选中 / 半透明蓝悬停均满足 b 明显大于 r、g）
            if c.blue() > c.red() + 8 and c.blue() > c.green() + 8:
                last = x
                break
    return last + 1


def hover_alpha(tree, delegate, item) -> float:
    """反算悬停胶囊的实际不透明度：px = bg*(1-a) + accent*a。

    背景填纯黑，故 a 可由来 alpha 通道直接反解（a = px.blue() / accent.blue()）。
    取点位于胶囊右侧内边距、垂直居中处：避开文字、图标与圆角弧。"""
    idx = tree.indexFromItem(item)
    opt = QStyleOptionViewItem()
    opt.rect = QRect(0, 0, ROW_W, ROW_H)
    opt.state = QStyle.StateFlag.State_Enabled | QStyle.StateFlag.State_MouseOver
    pix = QPixmap(ROW_W, ROW_H)
    pix.fill(QColor("#000000"))
    p = QPainter(pix)
    delegate.paint(p, opt, idx)
    p.end()
    w = capsule_width(tree, delegate, item, False)
    img = pix.toImage()
    x = max(1, w - 4)
    y = ROW_H // 2
    c = QColor(img.pixel(x, y))
    accent = QColor(ap.ACCENT)
    if accent.blue() <= 0:
        return -1.0
    return c.blue() / float(accent.blue())


def main() -> int:
    tree = QTreeWidget()
    tree.setHeaderHidden(True)
    tree.setColumnCount(1)
    tree.setIconSize(tree.iconSize())
    delegate = _ContentSelectionDelegate(tree)
    tree.setItemDelegate(delegate)
    for name in NAMES:
        it = QTreeWidgetItem([name])
        it.setIcon(0, ap._line_icon("folder", 18, ap.TEXT_DIM))
        tree.addTopLevelItem(it)
    tree.resize(ROW_W + 40, 200)
    tree.show()
    for _ in range(8):
        app.processEvents()

    failures = []
    for state_name, selected in (("selected", True), ("hover", False)):
        widths = [capsule_width(tree, delegate, tree.topLevelItem(i), selected)
                  for i in range(len(NAMES))]
        print(f"[{state_name}] " + ", ".join(
            f"{n}={w}" for n, w in zip(NAMES, widths)))
        # 断言 1：宽度随文本长度单调不减（真正自适应，而非统一长度）
        for a, b in zip(widths, widths[1:]):
            if b < a:
                failures.append(f"{state_name}: 宽度未随文本增长（{a} -> {b}）")
        # 断言 2：最短与最长差异明显（> 30px），排除"统一长度"
        if widths[-1] - widths[0] < 30:
            failures.append(
                f"{state_name}: 最长与最短宽度差仅 {widths[-1] - widths[0]}px，疑似统一长度")
        # 断言 3：任何一行都不铺满整行（留出右侧空白）
        for n, w in zip(NAMES, widths):
            if w >= ROW_W - 4:
                failures.append(f"{state_name}: {n} 胶囊铺满整行（{w}px），未自适应")

    # 断言 4：悬停胶囊不透明度 = 40%（反算自混合像素，锁定需求给定的数值）
    alpha = hover_alpha(tree, delegate, tree.topLevelItem(2))
    print(f"[hover alpha] 实测不透明度 = {alpha:.3f}（目标 0.40）")
    if abs(alpha - 0.40) > 0.06:
        failures.append(f"悬停不透明度 {alpha:.3f} 偏离目标 0.40")

    if failures:
        print("\nFAIL:")
        for f in failures:
            print("  -", f)
        return 1
    print("\nPASS: 选中/悬停胶囊宽度均随目录名长度自适应，且不铺满整行。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
