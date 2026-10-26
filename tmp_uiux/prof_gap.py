# -*- coding: utf-8 -*-
"""定位剩余的最大帧间隔缺口出现在哪个阶段。"""
import os
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

events = []          # (t, kind, extra)

def wrap(cls, name, kind):
    orig = getattr(cls, name)
    def rec(self, *a, **kw):
        t0 = time.monotonic()
        r = orig(self, *a, **kw)
        events.append((time.monotonic(), kind, f"{(time.monotonic()-t0)*1000:.0f}ms"))
        return r
    setattr(cls, name, rec)

wrap(ap._LoadingOverlay, "_tick", "tick")
wrap(ap._LoadingOverlay, "_blur_async", "blur")
wrap(ap._AgentSettingsDialog, "_build_pages_step", "page_build")
wrap(ap._AgentSettingsDialog, "_collect_and_save", "collect")
wrap(ap._AgentSettingsDialog, "_finish_save", "finish_save")
wrap(ap._AgentSettingsDialog, "_close_after_save", "close")
wrap(ap._AgentSettingsDialog, "_save", "save")

t0 = time.monotonic()
d._save()
while time.monotonic() - t0 < 4.0:
    _app.processEvents()

# 相邻事件间的时间差（含 tick）→ 找最大缺口
prev_t, prev_k = t0, "click"
max_gap = (0, "")
for t, k, extra in events:
    gap = (t - prev_t) * 1000
    if gap > max_gap[0]:
        max_gap = (gap, f"{prev_k} → {k} ({extra})")
    prev_t, prev_k = t, k
print(f"最大事件间隔: {max_gap[0]:.0f}ms  {max_gap[1]}")
print()
for t, k, extra in events:
    print(f"  +{(t - t0)*1000:7.0f}ms  {k:12s} {extra}")
