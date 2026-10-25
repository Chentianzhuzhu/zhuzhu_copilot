# -*- coding: utf-8 -*-
import sys, time
sys.path.insert(0, r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\src")
from PyQt6.QtWidgets import QApplication
app = QApplication(sys.argv)
# 找到所有可见顶层窗口
for w in app.topLevelWidgets():
    if w.isVisible() and w.windowTitle():
        print("WIN:", w.windowTitle(), w.__class__.__name__, w.size())
# 找 AgentPanel
panels = [w for w in app.topLevelWidgets() if w.__class__.__name__ == "AgentPanel"]
if panels:
    p = panels[0]
    time.sleep(1)
    p.grab().save(r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\tmp_uiux\live_panel.png")
    # 找预览窗口
    kids = p.findChildren(type(p)) if False else []
    from zhuzhu_Copilot.ui.agent_panel import CodePreviewWindow
    prevs = p.findChildren(CodePreviewWindow)
    print("previews:", len(prevs))
    if prevs:
        cw = prevs[0]
        print("preview visible:", cw.isVisible())
        cw.grab().save(r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\tmp_uiux\live_preview.png")
print("done")
