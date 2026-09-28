"""诊断：dock→attach 切换后子面板底部透明边框 + 拖动调整大小报错。"""
import os
import sys
import traceback

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from PyQt6.QtWidgets import QApplication

app = QApplication([])

from zhuzhu_Copilot.ui import agent_panel as ap


def dump(tag, panels):
    print(f"--- {tag} ---")
    for p in panels:
        lay = p.layout()
        inner = 0
        if lay is not None:
            for i in range(lay.count()):
                it = lay.itemAt(i)
                ww = it.widget()
                if ww is not None and ww.isVisible():
                    inner = max(inner, ww.geometry().y() + ww.geometry().height())
        extra = p.height() - inner
        print(f"  {p.objectName():10s} dock={p.dock_state:6s} h={p.height():4d} "
              f"内容底={inner:4d} 底部多出={extra:4d} "
              f"minH={p.minimumHeight():4d} maxH={p.maximumHeight():6d} "
              f"minW={p.minimumWidth():4d} maxW={p.maximumWidth():6d}")


p = ap.AgentPanel()
p.resize(1500, 1000)
p.show()
for _ in range(14):
    app.processEvents()
panels = [p.wt_win, p.git_win, p.todos_win]

p._apply_panel_mode("dock")
for _ in range(16):
    app.processEvents()
dump("dock", panels)

p._apply_panel_mode("attach")
for _ in range(16):
    app.processEvents()
dump("attach（切换后）", panels)

print("--- 模拟拖动调整大小 ---")
for panel in panels:
    grip = getattr(panel, "_grip", None)
    slots = list(getattr(grip, "_slots", {}).items()) if grip is not None else []
    print(f"  {panel.objectName()}: grip={grip is not None} slots={[k for k, _ in slots]}")
    if not slots:
        continue
    key, slot = slots[-1]
    try:
        slot._panel_resize(panel, panel.width() + 30, panel.height() + 30)
        print(f"    _panel_resize({key}) OK -> h={panel.height()}")
    except Exception:
        print(f"    _panel_resize({key}) 异常:")
        traceback.print_exc()
    try:
        p._panel_size_changed(panel, panel.width(), panel.height(), persist=True)
        print(f"    _panel_size_changed(persist=True) OK -> h={panel.height()}")
    except Exception:
        print("    _panel_size_changed 异常:")
        traceback.print_exc()

# 恢复默认位置（用户报告 todos 被拉长）
print("--- 点「恢复默认位置」 ---")
fn = getattr(p, "_restore_default_positions", None) or getattr(p, "_reset_panel_positions", None)
print("  恢复方法:", fn)
if callable(fn):
    try:
        fn()
        for _ in range(12):
            app.processEvents()
        dump("恢复默认位置后", panels)
    except Exception:
        traceback.print_exc()
else:
    cands = [n for n in dir(p) if "default" in n.lower() and "pos" in n.lower()]
    print("  候选方法:", cands)
