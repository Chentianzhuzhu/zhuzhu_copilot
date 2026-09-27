# -*- coding: utf-8 -*-
"""验证 agent_panel.py 第 2275-2300 行"""
path = r'C:\Users\zhuzhu\Desktop\my first android app\src\winapp_migrator\ui\agent_panel.py'
lines = open(path, encoding='utf-8', errors='ignore').read().split('\n')
out = []
for i in range(2274, 2305):
    if i < len(lines):
        out.append(f'{i+1}: {lines[i][:160]}')
open(r'C:\Users\zhuzhu\Desktop\my first android app\scripts\verify_btn.txt', 'w', encoding='utf-8').write('\n'.join(out))
print('done')
