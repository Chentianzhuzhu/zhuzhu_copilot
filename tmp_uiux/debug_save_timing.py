# -*- coding: utf-8 -*-
import sys, os, time
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\src")
from PyQt6.QtWidgets import QApplication
app = QApplication(sys.argv)
import zhuzhu_Copilot.ui.agent_panel as ap
# 给 _finish_save 打桩，观察它何时被调用以及 save_settings 结果
orig_finish = ap._AgentSettingsDialog._finish_save
def traced_finish(self):
    print(">>> _finish_save CALLED, pending=", self._pending_data is not None)
    orig_finish(self)
    ov = getattr(self, "_overlay", None)
    print("    after finish, overlay visible:", ov.isVisible() if ov else None)
ap._AgentSettingsDialog._finish_save = traced_finish

panel = ap.AgentPanel()
panel.show()
for _ in range(20): app.processEvents()
dlg = ap._AgentSettingsDialog(panel)
dlg.show()
for _ in range(15): app.processEvents()
t0 = time.monotonic()
dlg._save()
print("immediately after _save, overlay visible:", dlg._overlay.isVisible(), "elapsed 0")
for i in range(40):
    app.processEvents()
    ov = dlg._overlay
    el = (time.monotonic()-t0)*1000
    if i < 6 or not ov.isVisible():
        print(f"step {i} elapsed {el:.0f}ms overlay visible: {ov.isVisible()}")
    if not ov.isVisible():
        break
