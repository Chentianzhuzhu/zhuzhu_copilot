# -*- coding: utf-8 -*-
"""模拟真实场景：对话框打开一段时间（缓存热了）后再点保存，测各阶段耗时。"""
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
end = time.time() + 0.5
while time.time() < end:
    _app.processEvents()

# 预热：先把所有页构建一遍（模拟用户已浏览过所有页，缓存全热）
print("== 预热（首次构建全部页，含冷启动 I/O） ==")
t0 = time.monotonic()
while d.stack.count() < len(d._page_builders):
    d._build_pages_step()
    end2 = time.time() + 0.05
    while time.time() < end2:
        _app.processEvents()
print(f"预热总耗时: {(time.monotonic()-t0)*1000:.0f}ms\n")

# 正式测量：再来一次完整保存流程（热缓存）
marks = []
orig_step = ap._AgentSettingsDialog._build_pages_step
def rec_step(self):
    t = time.monotonic()
    orig_step(self)
    marks.append(("page", (time.monotonic()-t)*1000))
ap._AgentSettingsDialog._build_pages_step = rec_step

orig_blur = ap._LoadingOverlay._blur_async
def rec_blur(self):
    t = time.monotonic()
    orig_blur(self)
    marks.append(("blur", (time.monotonic()-t)*1000))
ap._LoadingOverlay._blur_async = rec_blur

orig_finish = ap._AgentSettingsDialog._finish_save
def rec_finish(self):
    t = time.monotonic()
    orig_finish(self)
    marks.append(("finish_save", (time.monotonic()-t)*1000))
ap._AgentSettingsDialog._finish_save = rec_finish

ticks = []
orig_tick = ap._LoadingOverlay._tick
def rec_tick(self):
    ticks.append(time.monotonic())
    orig_tick(self)
ap._LoadingOverlay._tick = rec_tick

print("== 热缓存保存流程 ==")
t0 = time.monotonic()
d._save()
while time.monotonic() - t0 < 6.0:
    _app.processEvents()
    if not getattr(d, "_overlay", None) or not d._overlay.isVisible():
        break

pages = [x for k, x in marks if k == "page"]
print(f"页面构建: {len(pages)} 次, 总 {sum(pages):.0f}ms, 最大 {max(pages):.0f}ms")
for k, x in marks:
    if k != "page":
        print(f"  {k}: {x:.0f}ms")
iv = [(ticks[i+1]-ticks[i])*1000 for i in range(len(ticks)-1)]
if iv:
    iv_s = sorted(iv)
    print(f"tick: {len(ticks)} 个, 中位 {iv_s[len(iv_s)//2]:.1f}ms, P90 {iv_s[int(len(iv_s)*0.9)]:.1f}ms, 最大 {iv_s[-1]:.1f}ms")
    print(f">32ms 帧: {sum(1 for x in iv if x>32)}/{len(iv)}, >100ms 帧: {sum(1 for x in iv if x>100)}")
