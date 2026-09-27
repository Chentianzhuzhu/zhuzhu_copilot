# -*- coding: utf-8 -*-
"""找到 AgentPanel 类末尾，以便添加 _open_tts_panel 方法"""
path = r'C:\Users\zhuzhu\Desktop\my first android app\src\winapp_migrator\ui\agent_panel.py'
lines = open(path, encoding='utf-8', errors='ignore').read().split('\n')
out = []
# 找最后几个方法
for i in range(max(0, len(lines)-60), len(lines)):
    out.append(f'{i+1}: {lines[i][:160]}')
open(r'C:\Users\zhuzhu\Desktop\my first android app\scripts\agent_tail.txt', 'w', encoding='utf-8').write('\n'.join(out))
print('done, total lines:', len(lines))
