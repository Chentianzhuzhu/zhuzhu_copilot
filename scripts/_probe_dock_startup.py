"""复现：启动即 dock（panel_pref_mode=dock）时，左栏 todos 窗口与内容的高度是否自洽。

模拟真实启动顺序：构造 AgentPanel（_build_ui 内即应用 dock 偏好，此时窗口几何尚未落定）
→ show → 事件循环推进 → 检查 todos 窗口高 / 内层面板高 / 空白带。
"""
from zhuzhu_Copilot import app_identity
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, "src")

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication

app = QApplication([])
from zhuzhu_Copilot.ui import agent_panel as ap

print("panel_pref_mode =",
      app_identity.qsettings().value("panel_pref_mode"), flush=True)


def pump(n=10):
    for _ in range(n):
        app.processEvents()


def report(p, tag):
    print(f"--- {tag}", flush=True)
    col = p._dock_left
    print(f"  左栏 {col.width()}x{col.height()} 可见={col.isVisible()}", flush=True)
    order = p._dock_panel_order("left")
    lay = col.property("_dock_lay")
    spacing = lay.spacing() * max(0, len(order) - 1)
    for w in order:
        inner = getattr(w, "panel", None)
        g = inner.geometry() if inner is not None else None
        print(f"  {w.objectName():10s} 窗口 {w.width()}x{w.height()}"
              f" minH={w.minimumHeight()} maxH={w.maximumHeight()}"
              f" y={w.y()} | 内层 geo=({g.x()},{g.y()}) {g.width()}x{g.height()}"
              if g is not None else f"  {w.objectName()} {w.width()}x{w.height()}", flush=True)
        if inner is not None:
            blank = w.height() - (g.y() + g.height())
            print(f"      内层底边下方空白={blank}px"
                  f" {'← 窗口比内容高，多出来的条带' if abs(blank) > 2 else ''}", flush=True)
    left = col.height() - sum(w.height() for w in order) - spacing
    print(f"  栏内空白 = {left}px", flush=True)


p = ap.AgentPanel()
print(f"构造完成，_applied_panel_mode={getattr(p, '_applied_panel_mode', None)}", flush=True)
p.resize(1500, 1000)
p.show()
pump(16)
p.todos_win.update_todos([{"title": f"任务 {i}", "status": "pending"} for i in range(1, 4)])
pump(8)
report(p, "启动即 dock（show + 首次内容更新后）")

# 再走一次显式切换：attach → dock，对比是否自洽
p._apply_panel_mode("attach")
pump(12)
report(p, "切到 attach")
p._apply_panel_mode("dock")
pump(16)
report(p, "再切回 dock")
os._exit(0)
