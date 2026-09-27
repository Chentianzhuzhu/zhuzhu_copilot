"""实证：TodosWindow 的 22px 拖拽把手是否导致 panel 底部 22px 溢出窗口被裁切"""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "windows")
sys.path.insert(0, os.path.abspath("src"))
from PyQt6.QtCore import QPoint
from PyQt6.QtWidgets import QApplication, QLabel
app = QApplication([])
from winapp_migrator.ui import agent_panel as ap

todos = [
    {"title": "分析用户需求，制定学习计划框架，需要注意各个阶段的衔接与节奏把控", "status": "in_progress"},
    {"title": "生成C语言入门学习文档（Word），包含全部示例代码、注释说明", "status": "pending"},
]
win = ap.TodosWindow()
win.show()
win.update_todos(todos)
for _ in range(8):
    app.processEvents()

panel = win.panel
dh = getattr(win, "_drag_handle", None)
print("window height:", win.height())
print("panel height:", panel.height())
print("drag handle height:", dh.height() if dh else "无")
print("layout min total(预期):", (dh.height() if dh else 0) + panel.height())
print("布局实际需求(win.layout().minimumSize().height()):", win.layout().minimumSize().height())
print("panel 底缘(y + h):", panel.y() + panel.height(), "窗口高:", win.height())
# panel 底部是否超出窗口（被裁切）
overflow = panel.y() + panel.height() - win.height()
print("panel 底部溢出窗口:", overflow, "px")
# 每条任务行必须完整落在窗口可视区域内（相对窗口坐标）
rows_vis = []
for i in range(panel._list_lay.count() - 1):
    w = panel._list_lay.itemAt(i).widget()
    lbl = w.findChild(QLabel, "todoTitle")
    if lbl:
        tl = w.mapTo(win, QPoint(0, 0))
        bottom = tl.y() + w.height()
        need = lbl.heightForWidth(lbl.width())
        rows_vis.append((i, tl.y(), bottom, lbl.height(), need,
                         need <= lbl.height() + 2 and bottom <= win.height()))
print("各行(相对窗口 top/bottom/labelH/need/可见):", rows_vis, "窗口底:", win.height())

dh = getattr(win, "_drag_handle", None)
extra = dh.height() if dh else 0
assert win.height() == panel.height() + extra, "窗口高度未包含拖拽把手"
assert overflow <= 0, f"panel 底部溢出窗口 {overflow}px（底部文字被裁切）"
for i, top, bottom, lh, need, vis in rows_vis:
    assert vis, f"row{i} 文字被挤压或裁切 (need={need} h={lh} bottom={bottom} win={win.height()})"
# 空状态占位文字同样不被裁切
win.update_todos([])
for _ in range(4):
    app.processEvents()
empty_w = win.panel._list_lay.itemAt(0).widget()
bottom = empty_w.mapTo(win, QPoint(0, 0)).y() + empty_w.height()
assert bottom <= win.height(), f"空占位文字被裁切 bottom={bottom} win={win.height()}"
print("empty 占位底缘:", bottom, "窗口底:", win.height())
print("RESULT: PASS")