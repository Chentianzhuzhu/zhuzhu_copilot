"""回归验证：子面板拖动调整大小不再崩溃 + dock↔attach 切换后尺寸自洽。

修复前：
1. `_PanelResizeSlot.mouseMoveEvent` 用 `globalPosition()`（float）直接喂 QRect.set* →
   TypeError "argument 1 has unexpected type 'float'" → 每次拖动都弹崩溃框。
2. dock→attach 后 todos 面板残留 dock 的 min/max（限行内容高度），贴附窗口
   比内容矮（内容被裁）或高（底部透明/空白带）。
3. 「重置全部面板位置」把 todos 固定成 280x320 → 清单很短时面板被拉长。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication

app = QApplication([])

from winapp_migrator.ui import agent_panel as ap

TODO6 = [{"title": f"任务 {i}", "status": "pending"} for i in range(1, 7)]


def drag(slot, panel, gdx, gdy):
    """模拟真实拖拽：press → move（走 mouseMoveEvent 的 float 计算路径）→ release"""
    start = QPointF(100.0, 100.0)
    press = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(1.0, 1.0), start,
                        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                        Qt.KeyboardModifier.NoModifier)
    slot.mousePressEvent(press)
    move = QMouseEvent(QEvent.Type.MouseMove, QPointF(1.0, 1.0),
                       QPointF(100.0 + gdx, 100.0 + gdy),
                       Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
                       Qt.KeyboardModifier.NoModifier)
    slot.mouseMoveEvent(move)
    rel = QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(1.0, 1.0),
                      QPointF(100.0 + gdx, 100.0 + gdy),
                      Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
                      Qt.KeyboardModifier.NoModifier)
    slot.mouseReleaseEvent(rel)


def main() -> int:
    p = ap.AgentPanel()
    p.resize(1500, 1000)
    p.show()
    for _ in range(14):
        app.processEvents()
    panels = [p.wt_win, p.git_win, p.todos_win]
    p.todos_win.update_todos(TODO6)

    fail = 0
    for panel in panels:
        grip = getattr(panel, "_grip", None)
        slots = getattr(grip, "_slots", {}) if grip is not None else {}
        for key in ("R", "BR", "B", "TL"):
            slot = slots.get(key)
            if slot is None:
                continue
            try:
                drag(slot, panel, 24, 18)          # 修复前：TypeError
                print(f"OK  {panel.objectName():10s} 拖动 {key} -> {panel.width()}x{panel.height()}")
            except Exception as e:   # noqa: BLE001 - 验证脚本：任何异常都算失败项
                fail += 1
                print(f"ERR {panel.objectName():10s} 拖动 {key}: {type(e).__name__}: {e}")

    # dock → attach：todos 必须按内容自洽（既不裁内容也不留透明带）
    p._apply_panel_mode("dock")
    for _ in range(16):
        app.processEvents()
    dock_h = p.todos_win.height()
    p._apply_panel_mode("attach")
    for _ in range(16):
        app.processEvents()
    tw = p.todos_win
    content = tw.panel.geometry().bottom() + 1 + tw.layout().contentsMargins().bottom()
    print(f"dock 下 todos h={dock_h} → attach 后 h={tw.height()} 内容底={content} "
          f"minH={tw.minimumHeight()} maxH={tw.maximumHeight()}")
    if tw.height() < content:
        fail += 1
        print("ERR attach 后 todos 比内容矮（内容被裁）")

    # 重置全部面板位置：todos 不得被固定成 320 拉长
    ap._reset_panel_poses(p)
    for _ in range(16):
        app.processEvents()
    content2 = tw.panel.geometry().bottom() + 1 + tw.layout().contentsMargins().bottom()
    print(f"重置位置后 todos h={tw.height()} 内容底={content2}")
    if tw.height() > content2 + 40:
        fail += 1
        print("ERR 重置位置后 todos 被拉长（底部留空）")

    for panel in panels:
        panel.close()
    p.close()
    print("失败项:", fail)
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
