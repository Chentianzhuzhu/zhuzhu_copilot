# -*- coding: utf-8 -*-
"""查看 agent_panel.py:3660-3680 上下文（engine.start 参数含义）"""
import sys
p = r'C:\Users\zhuzhu\Desktop\my first android app\src\winapp_migrator\ui\agent_panel.py'
lines = open(p, encoding='utf-8').read().splitlines()
for i in range(3655, 3680):
    if i < len(lines):
        sys.stdout.write('%d: %s\n' % (i + 1, lines[i].rstrip()[:140]))
sys.stdout.flush()
