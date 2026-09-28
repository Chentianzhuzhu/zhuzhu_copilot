"""把 dock 左栏渲染成像素后逐行扫描，定位「缝隙」到底在哪（颜色带分析）。

做法：QWidget.grab() 截图左栏 → 按行取样本色（面板内 x 与栏边缘 x）→ 输出颜色分段，
凡是与栏底色相同且高度 > 1px 的连续行即为可见缝隙。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, "src")

from PyQt6.QtWidgets import QApplication

app = QApplication([])
from zhuzhu_Copilot.ui import agent_panel as ap


def pump(n=10):
    for _ in range(n):
        app.processEvents()


def scan(p, tag):
    col = p._dock_left
    img = col.grab().toImage()
    h, w = img.height(), img.width()
    print(f"--- {tag}  左栏 {w}x{h}", flush=True)
    # 取两列样本：x=6（面板内左侧留白）与 x=w//2（内容中轴）
    for xs, label in ((6, "左内缘 x=6"), (w // 2, "中轴 x=w/2")):
        if xs >= w:
            continue
        bands = []
        cur = None
        for y in range(h):
            c = img.pixelColor(xs, y).name()
            if cur is None or cur[0] != c:
                if cur is not None:
                    bands.append(cur)
                cur = [c, y, y]
            else:
                cur[2] = y
        bands.append(cur)
        print(f"  {label}:", flush=True)
        for c, y0, y1 in bands:
            n = y1 - y0 + 1
            flag = "  ← 纯色带" if n > 1 else ""
            print(f"    y {y0:4d}-{y1:4d} ({n:4d}px)  {c}{flag}", flush=True)


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
    scan(p, "融入模式 · 3 条待办")
    # 打印各面板窗口几何，便于与颜色带对照
    for wd in p._dock_panel_order("left"):
        print(f"  面板 {wd.objectName():10s} y={wd.y():4d} h={wd.height():4d}"
              f" 底={wd.y() + wd.height()}", flush=True)
    os._exit(0)


if __name__ == "__main__":
    main()
