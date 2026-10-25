# -*- coding: utf-8 -*-
"""预览精致化后的评分对话框（自动截图并退出）。"""
import sys, os
SRC = r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\src"
sys.path.insert(0, SRC)
os.chdir(SRC)
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer

app = QApplication(sys.argv)
import importlib.util
_sc_path = r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\src\zhuzhu_Copilot\copilot_plugins_examples\agent-scorecard\service\scorecard_service.py"
_spec = importlib.util.spec_from_file_location("scorecard_service", _sc_path)
sc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sc)

snap = {
    "seconds": 11.3, "calls": 2,
    "tokens": {"prompt": 0, "completion": 0, "total": 0},
    "groups": {"tool": [("read_file", 1), ("run_command", 1)], "mcp": [], "skill": [], "agent": []},
}
dlg = sc.build_dialog(snap)
dlg.show()

def shots():
    dlg.grab().save(r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\tmp_uiux\score_dlg.png")
    # 模拟点第 4 颗
    dlg._pick(4)
    QTimer.singleShot(300, shot2)

def shot2():
    dlg.grab().save(r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\tmp_uiux\score_dlg4.png")
    app.quit()

QTimer.singleShot(400, shots)
sys.exit(app.exec())
