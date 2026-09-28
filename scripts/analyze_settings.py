# -*- coding: utf-8 -*-
"""查找 settings.json 的读写位置与格式"""
import os, re

# 1. 在 agent_panel.py 找 _save / settings 读写
p1 = r'C:\Users\zhuzhu\Desktop\my first android app\src\zhuzhu_Copilot\ui\agent_panel.py'
src1 = open(p1, encoding='utf-8', errors='ignore').read().split('\n')
out = []
out.append('===== agent_panel.py settings 读写 =====')
for i, l in enumerate(src1):
    if ('settings' in l.lower() and ('json' in l.lower() or 'load' in l.lower() or 'save' in l.lower() or 'write' in l.lower())) or 'def _save' in l or 'SETTINGS' in l:
        out.append('%4d %s' % (i + 1, l.rstrip()[:150]))

# 2. agent_llm.py 读 settings
p2 = r'C:\Users\zhuzhu\Desktop\my first android app\src\zhuzhu_Copilot\core\agent_llm.py'
src2 = open(p2, encoding='utf-8', errors='ignore').read().split('\n')
out.append('')
out.append('===== agent_llm.py settings 读写 =====')
for i, l in enumerate(src2):
    if 'settings' in l.lower() or 'providers' in l or 'api_key' in l.lower():
        out.append('%4d %s' % (i + 1, l.rstrip()[:150]))

with open(r'C:\Users\zhuzhu\Desktop\my first android app\scripts\settings_index.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(out[:120]))
print('done')
