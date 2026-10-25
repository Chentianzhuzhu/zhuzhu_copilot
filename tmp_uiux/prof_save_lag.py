# -*- coding: utf-8 -*-
"""剖析设置页保存流程：转圈定时器帧间隔 + 各阶段主线程阻塞时长。"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PyQt6.QtWidgets import QApplication          # noqa: E402

_app = QApplication.instance() or QApplication([])

from zhuzhu_Copilot.ui import agent_panel as ap   # noqa: E402

# 屏蔽落盘副作用
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

# ---- 打点：转圈 tick / 页面构建 / 模糊 / 持久化 ----
events = []          # (t, kind, extra)

orig_tick = ap._LoadingOverlay._tick
def rec_tick(self):
    events.append((time.monotonic(), "tick", ""))
    orig_tick(self)
ap._LoadingOverlay._tick = rec_tick

orig_step = ap._AgentSettingsDialog._build_pages_step
def rec_step(self):
    t0 = time.monotonic()
    orig_step(self)
    events.append((time.monotonic(), "page_build", f"{(time.monotonic()-t0)*1000:.0f}ms"))
ap._AgentSettingsDialog._build_pages_step = rec_step

orig_blur = ap._LoadingOverlay._blur_async
def rec_blur(self):
    t0 = time.monotonic()
    orig_blur(self)
    events.append((time.monotonic(), "blur", f"{(time.monotonic()-t0)*1000:.0f}ms"))
ap._LoadingOverlay._blur_async = rec_blur

orig_finish = ap._AgentSettingsDialog._finish_save
def rec_finish(self):
    t0 = time.monotonic()
    orig_finish(self)
    events.append((time.monotonic(), "finish_save", f"{(time.monotonic()-t0)*1000:.0f}ms"))
ap._AgentSettingsDialog._finish_save = rec_finish

orig_close = ap._AgentSettingsDialog._close_after_save
def rec_close(self):
    t0 = time.monotonic()
    orig_close(self)
    events.append((time.monotonic(), "close", f"{(time.monotonic()-t0)*1000:.0f}ms"))
ap._AgentSettingsDialog._close_after_save = rec_close

t0 = time.monotonic()
d._save()
while time.monotonic() - t0 < 4.0:
    _app.processEvents()

# ---- 分析 tick 间隔 ----
ticks = [t for t, k, _ in events if k == "tick"]
iv = [(ticks[i + 1] - ticks[i]) * 1000.0 for i in range(len(ticks) - 1)]
print(f"总 tick 数: {len(ticks)}  (4 秒窗口)")
if iv:
    iv_s = sorted(iv)
    print(f"帧间隔 ms: 中位 {iv_s[len(iv_s)//2]:.1f}  "
          f"P90 {iv_s[int(len(iv_s)*0.9)]:.1f}  最大 {iv_s[-1]:.1f}")
    print(f">32ms(掉到30fps以下) 的帧数: {sum(1 for x in iv if x > 32)}/{len(iv)}")
    print(f">100ms(明显定格) 的帧数: {sum(1 for x in iv if x > 100)}")
print()
for t, k, extra in events:
    if k != "tick":
        print(f"  +{(t - t0)*1000:7.0f}ms  {k:12s} {extra}")
print()
print("遮罩生命周期(ms):", f"{(events[-1][0] - ticks[0])*1000:.0f}" if ticks else "n/a")
