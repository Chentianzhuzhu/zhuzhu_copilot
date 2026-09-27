# -*- coding: utf-8 -*-
"""在 agent_panel.py 添加 TTS 按钮和 _open_tts_panel 方法"""
import sys, io

path = r'C:\Users\zhuzhu\Desktop\my first android app\src\winapp_migrator\ui\agent_panel.py'
lines = open(path, encoding='utf-8').read().split('\n')

# 找 import agent_tools 附近
for i, l in enumerate(lines[:80]):
    if 'agent_tools' in l or 'agent_engine' in l:
        print(f'{i+1}: {l}')

# 找 bottom.addWidget(self.input, 1)
for i, l in enumerate(lines):
    if 'bottom.addWidget(self.input, 1)' in l:
        print(f'\n--- input addWidget at line {i+1} ---')
        for j in range(max(0,i-3), min(len(lines), i+5)):
            print(f'{j+1}: {lines[j]}')
        break
