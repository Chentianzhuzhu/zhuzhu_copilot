"""dock 左栏二维像素结构分析：找出「可见缝隙」的确切位置与成因。

输出：每 4 行做一次横向扫描，列出与栏底色不同的颜色区段（x 范围 + 颜色），
从而看清白色卡片区域的上下边界（缝隙 = 卡片之间的底色带）。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, "src")

from PyQt6.QtWidgets import QApplication

app = QApplication([])
from winapp_migrator.ui import agent_panel as ap


def pump(n=10):
    for _ in range(n):
        app.processEvents()


def main():
    p = ap.AgentPanel()
    p.resize(1500, 1000)
    p.show()
    pump(16)
    p._todos_enabled = lambda: True
    p._git_enabled = lambda: True
    p._apply_panel_mode("dock")
    pump(20)
    p.todos_win.update_todos([{"title": f"任务{i}", "status": "pending"}
                              for i in range(1, 4)])
    pump(10)

    col = p._dock_left
    img = col.grab().toImage()
    h, w = img.height(), img.width()
    bg = img.pixelColor(w - 3, 3).name()          # 栏底色（取右上角空处）
    print(f"左栏 {w}x{h}  栏底色={bg}", flush=True)
    for wd in p._dock_panel_order("left"):
        print(f"  {wd.objectName():10s} y={wd.y():4d}..{wd.y() + wd.height():4d}"
              f" (h={wd.height()})", flush=True)

    print("\n每 4 行的「非底色」区段（x 范围:颜色）：", flush=True)
    for y in range(0, h, 4):
        runs = []
        cur = None
        for x in range(w):
            c = img.pixelColor(x, y).name()
            if c == bg:
                if cur:
                    runs.append(cur)
                    cur = None
                continue
            if cur is None:
                cur = [x, x, c]
            elif cur[2] == c:
                cur[1] = x
            else:
                runs.append(cur)
                cur = [x, x, c]
        if cur:
            runs.append(cur)
        if not runs:
            continue
        desc = "  ".join(f"{a}-{b}:{c}" for a, b, c in runs)
        print(f"  y={y:4d} {desc[:150]}", flush=True)
    os._exit(0)


if __name__ == "__main__":
    main()
