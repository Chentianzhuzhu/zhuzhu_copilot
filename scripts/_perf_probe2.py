# -*- coding: utf-8 -*-
"""剖析 AgentPanel 延迟初始化内部耗时（离屏）。临时脚本不入库。"""
import os, sys, time, pathlib
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, r'C:\Users\zhuzhu\Desktop\my first android app\src')

def ts(): return time.perf_counter()

from PyQt6.QtWidgets import QApplication
app = QApplication([])
import zhuzhu_Copilot.ui.agent_panel as M

# 阻止 _init_sessions 真正的会话恢复? 不——我们需要看全貌。先原样构造，但把
# _finish_startup_init 拆开逐段计时：构造后用信号/单发不可行，直接手动调用各步骤。
panel = M.AgentPanel.__new__(M.AgentPanel)
# 仅用于探测 import/构造期间静态开销

# 完整构造，但先停掉 singleShot 延迟任务（_finish_startup_init 通过 singleShot(0) 注册，
# 在 show/processEvents 前我们手动清空? 单发计时器不可取消——用 processEvents 触发并计时）
t0 = ts()
panel = M.AgentPanel()
app.processEvents()   # flush singleShot(0)
t1 = ts()
print(f'ctor+flush = {t1-t0:.3f}s')

# 现在逐项手动计时 —— 需要访问已执行完的初始化，检查已初始化属性
print('has sessions_dir:', hasattr(panel, '_sessions_dir'))

# 分解 _init_sessions
def t_call(label, fn, *a, **kw):
    t0 = ts()
    r = fn(*a, **kw)
    print(f'{label}: {ts()-t0:.3f}s')
    return r

if hasattr(panel, '_load_session_list'):
    t_call('_load_session_list', panel._load_session_list)
sess_dir = panel._sessions_dir()
files = list(sess_dir.glob('*.ui.json')) if sess_dir.exists() else []
print(f'session ui files: {len(files)}')
sizes = [(f.stat().st_size) for f in files]
print('total ui bytes:', sum(sizes), 'max:', max(sizes) if sizes else 0)

# 模拟最坏情况：逐个会话文件读取耗时
for f in files[:5]:
    t0 = ts()
    try:
        json.loads(f.read_text(encoding='utf-8'))
    except Exception:
        pass
    print(f'  read {f.name}: {ts()-t0:.3f}s  ({f.stat().st_size} B)')
panel.close()
print('DONE')
