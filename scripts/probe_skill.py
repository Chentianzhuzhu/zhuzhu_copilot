# -*- coding: utf-8 -*-
import sys
P = r'C:\Users\zhuzhu\Desktop\my first android app\src\zhuzhu_Copilot\ui\agent_panel.py'
lines = open(P, encoding='utf-8').read().splitlines()
for i, l in enumerate(lines):
    if '_build_skill_page' in l or '_build_mcp_page' in l:
        sys.stdout.write('%d: %r\n' % (i + 1, l))
sys.stdout.flush()
