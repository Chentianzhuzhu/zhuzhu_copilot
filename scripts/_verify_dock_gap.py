"""回归验证：融入主面板(dock)模式下左侧栏不得留空白（git 面板下方 273x44 空白）。

测量左侧 dock 栏（工作树/Git/任务清单）各面板几何与栏内剩余空隙：
- 面板之间 / 末尾到栏底的空白应约为 0（布局间距 8 除外）。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from PyQt6.QtWidgets import QApplication

app = QApplication([])

from zhuzhu_Copilot.ui import agent_panel as ap

panel = ap.AgentPanel()
panel.resize(1500, 900)
panel.show()
for _ in range(12):
    app.processEvents()

panel._apply_panel_mode("dock")
for _ in range(12):
    app.processEvents()

col = panel._dock_left
print(f"dock_left: visible={col.isVisible()} geo=({col.x()},{col.y()},{col.width()}x{col.height()})")
order = panel._dock_panel_order("left")
prev_bottom = None
reported = []
for p in order:
    name = p.objectName()
    g = p.geometry()
    reported.append((name, g.y(), g.height(), p.minimumHeight(), p.maximumHeight(),
                     p.sizePolicy().verticalPolicy().name, p.isVisible()))
    if prev_bottom is not None:
        print(f"  gap between previous and {name}: {g.y() - prev_bottom}px")
    prev_bottom = g.y() + g.height()
for name, y, h, minh, maxh, pol, vis in reported:
    print(f"panel {name}: y={y} h={h} minH={minh} maxH={maxh} vpolicy={pol} visible={vis}")
if prev_bottom is not None:
    print(f"leftover below last panel: {col.height() - prev_bottom}px "
          f"(col h={col.height()})")

# 逐面板检查其内部内容是否填满自身高度（子控件底边与面板底边的差）
for p in order:
    lay = p.layout()
    if lay is None:
        continue
    items_bottom = 0
    for i in range(lay.count()):
        it = lay.itemAt(i)
        w = it.widget()
        if w is not None and w.isVisible():
            items_bottom = max(items_bottom, w.geometry().y() + w.geometry().height())
    print(f"  inner content bottom {p.objectName()}: {items_bottom} / panel h={p.height()}"
          f" -> 底部空白 {p.height() - items_bottom}px")
