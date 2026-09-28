"""验证：面板偏好 attach⇄dock 往返后，子面板与主窗口尺寸必须回到「原有大小」。

背景 bug：从融入主面板切回贴附时，子面板停在 dock 栏分配的高度、主窗口停在 dock 放宽后的
尺寸（需手动拖拽或点「重置全部面板位置」才恢复）。
用法：python scripts/_probe_panel_mode_size.py
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, "src")

from PyQt6.QtWidgets import QApplication

app = QApplication([])
from zhuzhu_Copilot.ui import agent_panel as ap

NAMES = ("wt_win", "git_win", "todos_win", "code_win")


def pump(n=10):
    for _ in range(n):
        app.processEvents()


def snap(p):
    return {n: (getattr(p, n).width(), getattr(p, n).height())
            for n in NAMES if getattr(p, n, None) is not None}


def main():
    p = ap.AgentPanel()
    p.resize(1400, 950)
    p.show()
    pump(16)
    p._todos_enabled = lambda: True
    p._git_enabled = lambda: True
    p.todos_win.update_todos([{"title": f"任务{i}", "status": "pending"}
                              for i in range(1, 6)])
    pump(8)

    # 先切到贴附，构造「原有大小」（模拟用户当前处在贴附态时切到融入）
    p._apply_panel_mode("attach")
    pump(16)
    before_panels, before_win = snap(p), (p.width(), p.height())
    print("贴附（原有）: 窗口", before_win, flush=True)
    for n, wh in before_panels.items():
        print(f"   {n:10s} {wh[0]}x{wh[1]}", flush=True)

    p._apply_panel_mode("dock")
    pump(20)
    print("融入后: 窗口", (p.width(), p.height()), flush=True)
    for n, wh in snap(p).items():
        print(f"   {n:10s} {wh[0]}x{wh[1]}", flush=True)

    p._apply_panel_mode("attach")
    pump(20)
    after_panels, after_win = snap(p), (p.width(), p.height())
    print("切回贴附: 窗口", after_win, flush=True)
    for n, wh in after_panels.items():
        print(f"   {n:10s} {wh[0]}x{wh[1]}", flush=True)

    bad = []
    for n in before_panels:
        if before_panels[n] != after_panels.get(n):
            bad.append(f"{n}: {before_panels[n]} -> {after_panels.get(n)}")
    if before_win != after_win:
        bad.append(f"主窗口: {before_win} -> {after_win}")
    print("\n=== 结论:", "尺寸已恢复原有大小" if not bad else "FAIL 未恢复 " + "; ".join(bad),
          flush=True)
    os._exit(1 if bad else 0)


if __name__ == "__main__":
    main()
