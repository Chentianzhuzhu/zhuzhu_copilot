import os, sys, tempfile, time
sys.path.insert(0, r"c:\Users\zhuzhu\Desktop\zhuzhu Copilot\src")
os.chdir(r"c:\Users\zhuzhu\Desktop\zhuzhu Copilot")
from PyQt6.QtWidgets import QApplication
app = QApplication([])
from zhuzhu_Copilot.ui import agent_panel

tmp = tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8",
                                  newline="")
for i in range(1, 16):
    tmp.write(f"print('line {i}')\n")
tmp.close()

w = agent_panel.CodePreviewWindow()
w.resize(760, 420)
w.show()
end = time.time() + 0.8
while time.time() < end:
    app.processEvents(); time.sleep(0.02)
w.show_text(tmp.name, "py")
end = time.time() + 0.6
while time.time() < end:
    app.processEvents(); time.sleep(0.02)
vp = w.text.viewport().geometry()
la = w.text._line_area.geometry()
print("viewport", vp, "linearea", la,
      "right==vp.left", la.right() + 1 == vp.left(), "top==vp.top", la.top() == vp.top())
w.grab().save(r"c:\Users\zhuzhu\Desktop\zhuzhu Copilot\.workbuddy-ai\preview_lineno.png")
print("saved")
