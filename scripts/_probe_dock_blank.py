"""dock 左栏几何明细：定位 todos 面板上方空白区域（304x34）的来源。

输出：左栏每个面板的窗口几何 / 内层面板几何 / 拖拽把手可见性与几何 /
todos 窗口布局各项的实际位置（含被隐藏但仍被计算进高度的项）。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, "src")

from PyQt6.QtWidgets import QApplication

app = QApplication([])
from winapp_migrator.ui import agent_panel as ap


def pump(n=8):
    for _ in range(n):
        app.processEvents()


def dump(panel, w, tag):
    print(f"--- {tag}", flush=True)
    print(f"  窗口 {w.objectName()}: geo=({w.x()},{w.y()}) {w.width()}x{w.height()}"
          f" minH={w.minimumHeight()} maxH={w.maximumHeight()}"
          f" dock={w.dock_state} 可见={w.isVisible()}")
    hd = getattr(w, "_drag_handle", None)
    if hd is not None:
        print(f"    把手: 可见={hd.isVisible()} 几何=({hd.x()},{hd.y()}) "
              f"{hd.width()}x{hd.height()} 隐藏占位={hd.sizePolicy().retainSizeWhenHidden()}")
    lay = w.layout()
    if lay is not None:
        for i in range(lay.count()):
            it = lay.itemAt(i)
            g = it.geometry()
            wd = it.widget()
            nm = getattr(wd, "objectName", lambda: "")() if wd is not None else "?"
            print(f"    布局[{i}] {type(wd).__name__ if wd else 'spacer'} obj={nm!r}"
                  f" 可见={wd.isVisible() if wd else '-'} geo=({g.x()},{g.y()}) {g.width()}x{g.height()}")
    inner = getattr(w, "panel", None)
    if inner is not None:
        g = inner.geometry()
        print(f"    内层面板: geo=({g.x()},{g.y()}) {g.width()}x{g.height()}"
              f" fixedH={inner.minimumHeight()}/{inner.maximumHeight()}")
        lay2 = inner.layout()
        if lay2 is not None:
            m = lay2.contentsMargins()
            print(f"    内层面板边距={{l:{m.left()}, t:{m.top()}, r:{m.right()}, b:{m.bottom()}}}"
                  f" spacing={lay2.spacing()}")


def main():
    p = ap.AgentPanel()
    p.resize(1600, 1000)
    p.show()
    pump(14)
    p._todos_enabled = lambda: True
    p._git_enabled = lambda: True
    p.todos_win.update_todos([{"title": f"任务 {i}", "status": "pending"} for i in range(1, 4)])
    pump(6)
    p._apply_panel_mode("dock")
    pump(20)

    col = p._dock_left
    print(f"左栏 {col.objectName()}: {col.width()}x{col.height()} 可见={col.isVisible()}",
          flush=True)
    for w in p._dock_panel_order("left"):
        dump(p, w, f"{w.objectName()} in dock")
    print(f"\n左栏 sum(panel h) = "
          f"{sum(w.height() for w in p._dock_panel_order('left'))} / 栏高 {col.height()}",
          flush=True)
    os._exit(0)


if __name__ == "__main__":
    main()
