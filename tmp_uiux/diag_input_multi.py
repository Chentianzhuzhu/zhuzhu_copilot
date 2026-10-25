# -*- coding: utf-8 -*-
import sys, os
SRC = r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\src"
sys.path.insert(0, SRC)
os.chdir(SRC)
from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout
from PyQt6.QtCore import Qt
from zhuzhu_Copilot.ui import agent_panel as ap
from PIL import Image

app = QApplication(sys.argv)


def make(theme, focus, name):
    ap._apply_colors(ap._THEMES[theme], force=True)
    host = QWidget()
    host.setStyleSheet(f"background: {ap.BG};")
    lay = QVBoxLayout(host)
    lay.setContentsMargins(20, 20, 20, 20)
    inp = ap._DropTextEdit()
    ap._install_focus_glow(inp)
    ap.apply_input_caret(inp)
    inp.setStyleSheet(ap._input_qss())
    inp.setPlaceholderText("/ for commad @ for agent")
    inp.setPlainText("第一行内容\n第二行内容\n第三行内容\n第四行内容\n第五行内容\n第六行内容")
    lay.addWidget(inp)
    host.resize(700, 260)
    host.show()
    if focus:
        inp.setFocus()
    for _ in range(8):
        app.processEvents()
    # 滚动到底部测试残影
    bar = inp.verticalScrollBar()
    bar.setValue(bar.maximum())
    for _ in range(5):
        app.processEvents()
    host.grab().save(rf"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\tmp_uiux\{name}.png")
    host.close()
    for _ in range(3):
        app.processEvents()


make("dark", True, "multi_dark_focus")
make("dark", False, "multi_dark_blur")
make("light", True, "multi_light_focus")
print("done")
app.quit()
