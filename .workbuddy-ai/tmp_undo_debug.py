import os, sys, tempfile, time
sys.path.insert(0, r"c:\Users\zhuzhu\Desktop\zhuzhu Copilot\src")
os.chdir(r"c:\Users\zhuzhu\Desktop\zhuzhu Copilot")
from PyQt6.QtWidgets import QApplication
app = QApplication([])
from zhuzhu_Copilot.ui import agent_panel

tmp = tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8", newline="")
tmp.write("a = 1\nb = 2\n")
tmp.close()
p = tmp.name

def pump(s):
    end = time.time() + s
    while time.time() < end:
        app.processEvents(); time.sleep(0.02)

w = agent_panel.CodePreviewWindow()
w.show_text(p, "py")
pump(0.1)
c = w.text.textCursor(); c.movePosition(c.MoveOperation.End); w.text.setTextCursor(c)
w.text.insertPlainText("c = 3\n")
pump(1.0)
print("after insert:", repr(open(p, encoding="utf-8").read()), "dirty", w._dirty)
w._undo_code()
print("after undo text:", repr(w.text.toPlainText()), "redoAvail", w.text.document().isRedoAvailable())
pump(1.0)
print("after undo saved:", repr(open(p, encoding="utf-8").read()), "dirty", w._dirty)
w._redo_code()
print("after redo text:", repr(w.text.toPlainText()), "dirty", w._dirty, "redoAvail", w.text.document().isRedoAvailable())
pump(1.0)
print("after redo saved:", repr(open(p, encoding="utf-8").read()), "dirty", w._dirty)
