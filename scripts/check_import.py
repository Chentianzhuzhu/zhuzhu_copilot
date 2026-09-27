# -*- coding: utf-8 -*-
"""验证 import 区是否有 tts_panel"""
path = r'C:\Users\zhuzhu\Desktop\my first android app\src\winapp_migrator\ui\agent_panel.py'
lines = open(path, encoding='utf-8').read().split('\n')
out = []
for i, l in enumerate(lines[:60]):
    if 'tts' in l.lower():
        out.append(f'{i+1}: {l[:160]}')
open(r'C:\Users\zhuzhu\Desktop\my first android app\scripts\import_check.txt', 'w', encoding='utf-8').write('\n'.join(out))
print('done')
