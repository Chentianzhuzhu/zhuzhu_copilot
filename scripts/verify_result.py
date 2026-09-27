# -*- coding: utf-8 -*-
"""验证修改结果"""
import sys

path = r'C:\Users\zhuzhu\Desktop\my first android app\src\winapp_migrator\ui\agent_panel.py'
lines = open(path, encoding='utf-8').read().split('\n')

out = []
# 验证 import
for i, l in enumerate(lines[:60]):
    if 'tts_panel' in l:
        out.append(f'{i+1}: {l}')

# 验证按钮
for i, l in enumerate(lines):
    if 'self.tts_btn' in l:
        out.append(f'{i+1}: {l}')

# 验证方法
for i, l in enumerate(lines):
    if '_open_tts_panel' in l:
        out.append(f'{i+1}: {l}')

open(r'C:\Users\zhuzhu\Desktop\my first android app\scripts\verify_result.txt', 'w', encoding='utf-8').write('\n'.join(out))
print('done')
