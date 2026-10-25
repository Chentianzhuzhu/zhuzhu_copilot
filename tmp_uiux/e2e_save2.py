# -*- coding: utf-8 -*-
import sys, os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
SRC = r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\src"
sys.path.insert(0, SRC)
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer, QElapsedTimer
OUT = r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\tmp_uiux"
app = QApplication(sys.argv)
import zhuzhu_Copilot.ui.agent_panel as ap
panel = ap.AgentPanel()
panel.show()
for _ in range(30): app.processEvents()
dlg = ap._AgentSettingsDialog(panel)
dlg.show()
for _ in range(20): app.processEvents()
clock = QElapsedTimer(); clock.start()
dlg._save()
# 记录转圈角度是否在前进（证明动画流畅）
angles = []
def poll():
    ov = dlg._overlay
    if ov is not None and ov.isVisible():
        angles.append((clock.elapsed(), ov._angle))
    if clock.elapsed() < 1400:
        QTimer.singleShot(40, poll)
    else:
        finish()
def grab(tag, when):
    QTimer.singleShot(when, lambda: (dlg.grab().save(os.path.join(OUT, f"e2e2_{tag}.png")),))
grab("early", 150)
grab("mid", 500)
grab("late", 800)
def finish():
    print("samples (ms, angle):")
    for ms, a in angles:
        print(f"  {ms}ms angle={a:.2f}")
    # 检查角度是否前进（动画在动）
    if len(angles) >= 3:
        progressed = angles[-1][1] != angles[0][1]
        print("angle progressed:", progressed)
    print("dialog result:", dlg.result(), "visible:", dlg.isVisible())
    app.quit()
QTimer.singleShot(0, poll)
sys.exit(app.exec())
