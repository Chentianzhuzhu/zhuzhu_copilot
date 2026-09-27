# -*- coding: utf-8 -*-
"""提取 agent_panel.py 关键位置：__init__ 结尾、按钮布局区、_build_ui 结构"""
import sys, io
sys.path.insert(0, r'C:\Users\zhuzhu\Desktop\my first android app\src')
out = io.StringIO()

with open(r'C:\Users\zhuzhu\Desktop\my first android app\src\winapp_migrator\ui\agent_panel.py', encoding='utf-8') as f:
    lines = f.readlines()

# 找 __init__ 函数结束位置
in_init = False
init_start = None
for i, l in enumerate(lines):
    if 'def __init__(self' in l and 'def ' in l:
        init_start = i
        in_init = True
        out.write(f'===== __init__ starts at line {i+1} =====\n')
    elif in_init and l.strip().startswith('def ') and i > init_start:
        out.write(f'===== __init__ ends before line {i+1} (next func: {l.strip()[:80]}) =====\n')
        # 打印 __init__ 最后 30 行
        for j in range(max(init_start, i-40), i):
            out.write(f'{j+1}: {lines[j].rstrip()[:140]}\n')
        break

# 找 agent_btn 附近（已知在第 542 行左右，在 main_window）
# 在 agent_panel 里找发送按钮或底部操作区
out.write('\n===== 查找发送/底部按钮 =====\n')
for i, l in enumerate(lines):
    if 'send_btn' in l or '发送' in l or 'input_edit' in l or 'bottom' in l.lower():
        out.write(f'{i+1}: {l.rstrip()[:140]}\n')

open(r'C:\Users\zhuzhu\Desktop\my first android app\scripts\agent_panel_analysis.txt', 'w', encoding='utf-8').write(out.getvalue())
print('done')
