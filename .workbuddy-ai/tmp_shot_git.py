import os, sys, time
sys.path.insert(0, r"c:\Users\zhuzhu\Desktop\zhuzhu Copilot\src")
os.chdir(r"c:\Users\zhuzhu\Desktop\zhuzhu Copilot")
from PyQt6.QtWidgets import QApplication
app = QApplication([])
from zhuzhu_Copilot.ui import agent_panel


def pump(s):
    end = time.time() + s
    while time.time() < end:
        app.processEvents(); time.sleep(0.02)


gw = agent_panel.GitLogWindow()
gw.resize(300, 560)
gw.show()
pump(2.0)                      # 等后台读取提交
gw.grab().save(r"c:\Users\zhuzhu\Desktop\zhuzhu Copilot\.workbuddy-ai\git_commit.png")
print("commit items", gw.list.count(), "chip", gw.branch_chip.text())
gw._set_view("branch")
pump(2.0)
gw.grab().save(r"c:\Users\zhuzhu\Desktop\zhuzhu Copilot\.workbuddy-ai\git_branch.png")
print("branch items", gw.list.count())

# 暂存弹窗截图（模拟真实改动清单）
dlg = agent_panel._StageFilesDialog([
    {"code": " M", "path": "src/zhuzhu_Copilot/ui/agent_panel.py"},
    {"code": "??", "path": ".workbuddy-ai/tmp_shot.py"},
    {"code": " M", "path": "src/zhuzhu_Copilot/core/agent_git.py"},
])
dlg.show()
pump(0.5)
dlg.grab().save(r"c:\Users\zhuzhu\Desktop\zhuzhu Copilot\.workbuddy-ai\git_stage.png")
print("stage dlg ok")
