"""复现：dock（融入主窗口）模式下最小化 → 恢复后，左右侧栏子面板消失。

探针流程（offscreen）：
1. 构造 AgentPanel，切到 dock 模式；
2. 记录各面板可见性 / 尺寸；
3. showMinimized() → showNormal() 模拟「最小化再返回」；
4. 复查可见性——若为 False 即复现。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, "src")

from PyQt6.QtWidgets import QApplication   # noqa: E402

app = QApplication([])
from winapp_migrator.ui import agent_panel as ap   # noqa: E402

NAMES = ("wt_win", "git_win", "todos_win", "code_win")


def snap(p, tag):
    print(f"--- {tag}")
    print(f"    dock 栏可见: left={p._dock_left.isVisible()} right={p._dock_right.isVisible()}"
          f" | 面板最小化标志={p._panel_minimized} | 主窗口最小化={p.isMinimized()}")
    for n in NAMES:
        w = getattr(p, n, None)
        if w is None:
            continue
        print(f"    {n:10s} state={w.dock_state:6s} visible={w.isVisible()!s:5s} "
              f"size={w.width()}x{w.height()}")


p = ap.AgentPanel()
p.resize(1600, 1000)
p.show()
for _ in range(14):
    app.processEvents()
p._apply_panel_mode("dock")
for _ in range(16):
    app.processEvents()

snap(p, "dock 就绪")

# 最小化 → 恢复（真实窗口状态切换）
p.showMinimized()
for _ in range(16):
    app.processEvents()
snap(p, "最小化后")

p.showNormal()
for _ in range(24):
    app.processEvents()
snap(p, "恢复后（期望左右侧栏面板全部回归）")

missing = [n for n in NAMES
           if (getattr(p, n, None) is not None
               and getattr(p, n).dock_state in ("left", "right")
               and not getattr(p, n).isVisible())]
print("=== 结论:", "BUG 复现，丢失面板 " + str(missing) if missing else "未复现（面板均在）")
