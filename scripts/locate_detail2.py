# -*- coding: utf-8 -*-
"""看 _line_icon 剩余 kind + tts_btn 完整块 + 4339 上下文"""
import sys

p = r'C:\Users\zhuzhu\Desktop\my first android app\src\zhuzhu_Copilot\ui\agent_panel.py'
lines = open(p, encoding='utf-8').read().splitlines()

out = ['===== _line_icon 234~330 =====']
for i in range(233, min(330, len(lines))):
    out.append('%d: %s' % (i + 1, lines[i].rstrip()[:120]))

out.append('===== tts_btn 块 2276~2300 =====')
for i in range(2275, min(2300, len(lines))):
    out.append('%d: %s' % (i + 1, lines[i].rstrip()[:120]))

out.append('===== _open_tts_panel 4335~4355 =====')
for i in range(4334, min(4355, len(lines))):
    out.append('%d: %s' % (i + 1, lines[i].rstrip()[:120]))

out.append('===== _build_model_page 918~930（新页面参照）=====')
for i in range(917, min(930, len(lines))):
    out.append('%d: %s' % (i + 1, lines[i].rstrip()[:120]))
sys.stdout.write('\n'.join(out))
sys.stdout.flush()
