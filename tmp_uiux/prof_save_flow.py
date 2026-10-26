# -*- coding: utf-8 -*-
"""cProfile：整个设置对话框 _save() 流程（转圈期间主线程都在干什么）。"""
import cProfile
import io
import os
import pstats
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PyQt6.QtWidgets import QApplication          # noqa: E402

_app = QApplication.instance() or QApplication([])

from zhuzhu_Copilot.ui import agent_panel as ap   # noqa: E402

ap.agent_skills.save_settings = lambda data: True
ap.agent_skills.save_mcp_servers = lambda servers: True
ap.agent_tts.save_config = lambda **kw: None
try:
    import zhuzhu_Copilot.core.agent_proxy_service as _aps
    _aps.apply = lambda: None
    _aps.status = lambda: {}
except Exception:
    pass

d = ap._AgentSettingsDialog()
d.resize(1040, 860)
d.show()
end = time.time() + 0.3
while time.time() < end:
    _app.processEvents()

pr = cProfile.Profile()
pr.enable()
d._save()
# 泵事件直到遮罩关闭（_close_after_save → accept）
t0 = time.monotonic()
while time.monotonic() - t0 < 12.0:
    _app.processEvents()
    if not getattr(d, "_overlay", None) or not d._overlay.isVisible():
        break
pr.disable()

s = io.StringIO()
ps = pstats.Stats(pr, stream=s).sort_stats("cumulative")
ps.print_stats(32)
print(s.getvalue())
