# -*- coding: utf-8 -*-
"""真实场景性能验证（离屏）：
1) 模块 import（含后台预热模拟——已提前 import 则命中）
2) AgentPanel 构造
3) 恢复本机最大真实会话 f56ddc08604d.ui.json（684KB/740段/20轮）
4) 会话间切换（长对话加载日常场景）
"""
import os, sys, time, json, pathlib, threading

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, r'C:\Users\zhuzhu\Desktop\my first android app\src')
SESS = pathlib.Path.home() / '.winapp_migrator' / 'agent' / 'sessions'
BIG = SESS / 'f56ddc08604d.ui.json'

def ts(): return time.perf_counter()

t0 = ts()
from PyQt6.QtWidgets import QApplication
app = QApplication([])
t1 = ts()
import winapp_migrator.ui.agent_panel as M
t2 = ts()
print(f'[real] QApp={t1-t0:.2f}s import={t2-t1:.2f}s')

# 预热解密（模拟 main.py 后台预热线程效果）
from winapp_migrator.core import agent_llm, agent_skills
agent_skills.load_settings(); agent_llm.load_model_config()

t3 = ts()
panel = M.AgentPanel()
app.processEvents(); time.sleep(0.05); app.processEvents()
t4 = ts()
print(f'[real] ctor+flush={t4-t3:.2f}s')

# 读取真实大会话
data = json.loads(BIG.read_text(encoding='utf-8'))
segs = data.get('segments') or []
ums = data.get('user_msgs') or []
rows = data.get('rows') or []
print(f'[real] big session: {len(segs)} segs / {len(rows)} rows')

# 第一次渲染（模拟恢复会话）
t5 = ts()
panel._user_msgs = list(ums)
panel._rows = [dict(r) for r in rows]
panel._history_segments = [dict(s) for s in segs]
panel._seg_cache.clear()
panel._render_history_all()
app.processEvents()
t6 = ts()
print(f'[real] render big history: {t6-t5:.2f}s bubbles={len(panel._bubble_widgets)}')

# 切换到另一有内容的会话（3922af 81KB / 8afe56 64KB），模拟日常来回切
for name in ('3922af7508b9', '8afe560b3581'):
    p = SESS / f'{name}.ui.json'
    if not p.exists():
        continue
    d2 = json.loads(p.read_text(encoding='utf-8'))
    t7 = ts()
    panel._user_msgs = list(d2.get('user_msgs') or [])
    panel._rows = [dict(r) for r in (d2.get('rows') or [])]
    panel._history_segments = [dict(s) for s in (d2.get('segments') or [])]
    panel._seg_cache.clear()
    panel._render_history_all()
    app.processEvents()
    t8 = ts()
    print(f'[real] switch to {name}: {t8-t7:.2f}s rows={len(panel._rows)}')

# 从大会话切到空新会话
t9 = ts()
panel._rows = []; panel._user_msgs = []; panel._history_segments = []
panel._seg_cache.clear()
panel._render_history_all()
app.processEvents()
t10 = ts()
print(f'[real] switch to empty: {t10-t9:.2f}s')
panel.close()
print('[real] DONE')
