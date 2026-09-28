# -*- coding: utf-8 -*-
"""提取 agent_panel.py 2250-2320 行（底部按钮区）"""
path = r'C:\Users\zhuzhu\Desktop\my first android app\src\zhuzhu_Copilot\ui\agent_panel.py'
lines = open(path, encoding='utf-8', errors='ignore').read().split('\n')
out = []
for i in range(2249, 2320):
    if i < len(lines):
        out.append(f'{i+1}: {lines[i][:160]}')
open(r'C:\Users\zhuzhu\Desktop\my first android app\scripts\agent_bottom.txt', 'w', encoding='utf-8').write('\n'.join(out))
print('done')
