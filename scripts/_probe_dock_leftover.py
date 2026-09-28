"""左栏（dock）余量扫描：不同窗口高度 × 任务条数下，检查栏内是否残留空白区。

判据：栏高 - Σ面板高 - 间距和 > 0 → 存在无人吸收的空白（用户看到的空条）。
同时打印 todos 面板高度构成（内容所需 / 固定默认上限 / 面板-窗口空档）与 Git 高度。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, "src")

from PyQt6.QtWidgets import QApplication

app = QApplication([])
from zhuzhu_Copilot.ui import agent_panel as ap


def pump(n=8):
    for _ in range(n):
        app.processEvents()


def main():
    p = ap.AgentPanel()
    p.resize(1600, 1000)
    p.show()
    pump(14)
    p._todos_enabled = lambda: True
    p._git_enabled = lambda: True
    p._apply_panel_mode("dock")
    pump(16)
    print(f"{'窗口高':>6s} {'待办':>4s} | {'栏高':>5s} {'工作树':>6s} {'Git':>6s}"
          f" {'清单':>6s} {'栏隙':>5s} {'窗隙':>5s} | 清单 内容/上限", flush=True)
    for win_h in (760, 900, 1000, 1200):
        for n in (0, 1, 3, 5, 8, 14):
            p.resize(1500, win_h)
            p.todos_win.update_todos(
                [{"title": f"任务 {i}", "status": "pending"} for i in range(1, n + 1)])
            pump(10)
            col = p._dock_left
            order = p._dock_panel_order("left")
            by = {w.objectName(): w for w in order}
            lay = col.property("_dock_lay")
            spacing = lay.spacing() * max(0, len(order) - 1)
            total_h = sum(w.height() for w in order)
            left = col.height() - total_h - spacing
            tw = p.todos_win
            inner = tw.panel
            geo = inner.geometry()
            win_gap = tw.height() - (geo.y() + geo.height())
            print(f"{win_h:6d} {n:4d} | {col.height():5d}"
                  f" {by['wtWin'].height():6d} {by['gitLogWin'].height():6d}"
                  f" {by['todosWin'].height():6d} {left:5d} {win_gap:5d} |"
                  f" {inner._content_height()}/{tw._fixed_height()}"
                  f" room={tw._dock_room} manual={tw._manual_h}"
                  f" req={inner.required_height()}"
                  f" wtcap={by['wtWin'].maximumHeight()}", flush=True)
    os._exit(0)


if __name__ == "__main__":
    main()
