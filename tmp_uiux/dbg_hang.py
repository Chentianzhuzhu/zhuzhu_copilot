# -*- coding: utf-8 -*-
import os, sys, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, "src")
from PyQt6.QtWidgets import QApplication
print("T0 import Qt", flush=True)
app = QApplication.instance() or QApplication([])
print("T1 app", flush=True)
from zhuzhu_Copilot.ui import main_window as mw
print("T2 mw", flush=True)
from zhuzhu_Copilot.ui.agent_panel import AgentPanel
print("T3 ap", flush=True)
import pytest
mp = pytest.MonkeyPatch()
mp.setattr(mw.CopilotPanel, "_start_scan", lambda self, *a: None)
mp.setattr(mw.CopilotPanel, "_init_update_check", lambda self: None)
mp.setattr(mw.CopilotPanel, "_check_admin", lambda self: None)
mp.setattr(mw.CopilotPanel, "_setup_tray", lambda self: None)
try:
    mp.setattr("zhuzhu_Copilot.ui.desktop_pet.ensure_pet", lambda w: None)
except Exception:
    pass
print("T4 monkeypatched", flush=True)
t0 = time.time()
p = AgentPanel(None)
print("T5 constructed %.2fs" % (time.time() - t0), flush=True)
p.resize(1000, 900)
print("T6 resized", flush=True)
p.show()
print("T7 shown", flush=True)
end = time.time() + 0.12
while time.time() < end:
    app.processEvents()
print("T8 pumped", flush=True)
p.hide()
print("T9 hidden", flush=True)
end = time.time() + 0.04
while time.time() < end:
    app.processEvents()
print("T10 done", flush=True)
