# -*- coding: utf-8 -*-
"""性能探针（离屏）：量化 导入耗时 / AgentPanel 构造耗时 / 长对话渲染耗时。
临时诊断脚本（_ 前缀，不入库）。用法：
    python scripts/_perf_probe.py [--rows N] [--bubbles N]
"""
import os, sys, time, json, pathlib, argparse

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, r'C:\Users\zhuzhu\Desktop\my first android app\src')
ROOT = pathlib.Path(r'C:\Users\zhuzhu\Desktop\my first android app')

ap = argparse.ArgumentParser()
ap.add_argument('--rows', type=int, default=30)      # 用户消息条数（轮次）
ap.add_argument('--bubbles', type=int, default=20)   # AI 气泡段落数/条
args = ap.parse_args()

def ts():
    return time.perf_counter()

t0 = ts()
from PyQt6.QtWidgets import QApplication
app = QApplication([])
t_qapp = ts()
import zhuzhu_Copilot.ui.agent_panel as M
t_import = ts()
print(f'[probe] QApplication init: {t_qapp-t0:.3f}s')
print(f'[probe] agent_panel import: {t_import-t_qapp:.3f}s  (module={M.__file__})')

# 造一段带格式的 AI 回复文本（长文本 + markdown 表格 + 代码块，模拟真实长回复）
text_seg = []
for k in range(8):
    text_seg.append(f'这是第 {k+1} 段正文内容，包含一些说明性文字与数据。\n\n')
text_seg.append('| 列A | 列B |\n| --- | --- |\n')
for k in range(20):
    text_seg.append(f'| 值{k} | {k*7} |\n')
text_seg.append('```python\ndef hello():\n    return "world"\n```\n')

big_text = ''.join(text_seg)

def make_ai_segs(nb):
    segs = []
    for j in range(nb):
        segs.append({'type': 'text', 'raw': f'### 小节 {j}\n' + big_text[:1500]})
        segs.append({'type': 'op', 'html': f'运行命令 #{j}'})
        segs.append({'type': 'result', 'html': f'结果输出 {j}: ' + 'x' * 300})
    segs.append({'type': 'split'})
    return segs

t_ctor0 = ts()
panel = M.AgentPanel()
t_ctor1 = ts()
print(f'[probe] AgentPanel.__init__ (含_ build_ui): {t_ctor1-t_ctor0:.3f}s')

# 触发 singleShot(0) 的延迟初始化（会话扫描/恢复）
app.processEvents()
time.sleep(0.05)
app.processEvents()
t_delayed = ts()
print(f'[probe] delayed startup init flushed: {t_delayed-t_ctor1:.3f}s')

# 造长会话内存态并渲染
sid = panel._session_id or 'probe'
rows = []
ums = []
hist = []
for i in range(args.rows):
    ums.append(f'用户问题 {i}: 请帮我分析一下数据')
    rows.append({'type': 'user', 'text': ums[-1]})
    rows.append({'type': 'ai', 'segs': make_ai_segs(args.bubbles)})
    for s in rows[-1]['segs']:
        hist.append(dict(s))
panel._user_msgs = list(ums)
panel._rows = list(rows)
panel._history_segments = hist
panel._seg_cache.clear()

t_render0 = ts()
panel._render_history_all()
app.processEvents()
t_render1 = ts()
total_bubbles = len(panel._bubble_widgets)
print(f'[probe] _render_history_all({args.rows}轮/{total_bubbles}气泡): {t_render1-t_render0:.3f}s')

# 会话切换重建（模拟从磁盘加载另一长会话）
t_sw0 = ts()
panel._render_history_all()
app.processEvents()
t_sw1 = ts()
print(f'[probe] re-render same history: {t_sw1-t_sw0:.3f}s')

# 模拟 resize（触发气泡宽度同步 + 防抖重建）
from PyQt6.QtWidgets import QApplication
panel.resize(1280, 800)
app.processEvents()
time.sleep(0.2)
app.processEvents()
t_rsz = ts()
print(f'[probe] resize + debounce: {t_rsz-t_sw1:.3f}s')

panel.close()
print('[probe] DONE')
