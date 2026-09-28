# -*- coding: utf-8 -*-
"""提取 _AgentSettingsDialog __init__ 导航构建 + _save + _build_general_page 示例 + tts_speak 工具"""
import sys

p = r'C:\Users\zhuzhu\Desktop\my first android app\src\zhuzhu_Copilot\ui\agent_panel.py'
lines = open(p, encoding='utf-8').read().splitlines()

def show(a, b, label):
    out = ['===== %s (%d~%d) =====' % (label, a + 1, b)]
    for i in range(a, b):
        out.append('%d: %s' % (i + 1, lines[i].rstrip()[:140]))
    return '\n'.join(out)

# __init__ 696 ~ 787（含导航添加）
print(show(695, 786, '__init__'))
# _build_general_page 800~858
print(show(799, 858, '_build_general_page'))
# _save 1291~1335
print(show(1290, 1335, '_save'))
