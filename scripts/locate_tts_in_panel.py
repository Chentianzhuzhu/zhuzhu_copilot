# -*- coding: utf-8 -*-
"""1) _line_icon 支持的 kind；2) agent_panel 中 tts 相关行位置；3) agent_panel import 区"""
import sys, re

p = r'C:\Users\zhuzhu\Desktop\my first android app\src\zhuzhu_Copilot\ui\agent_panel.py'
lines = open(p, encoding='utf-8').read().splitlines()
out = []

# 1) _line_icon 实现
out.append('===== _line_icon 实现 =====')
for i, l in enumerate(lines):
    if 'def _line_icon' in l:
        for j in range(i, min(i + 60, len(lines))):
            out.append('%d: %s' % (j + 1, lines[j].rstrip()[:120]))
        break

# 2) tts 相关行
out.append('===== tts 相关行 =====')
for i, l in enumerate(lines):
    if re.search(r'tts|TtsPanel|_open_tts', l):
        out.append('%d: %s' % (i + 1, l.rstrip()[:120]))

# 3) import 区前 60 行
out.append('===== import 区 =====')
for i in range(0, min(60, len(lines))):
    if 'import' in lines[i] or 'from' in lines[i]:
        out.append('%d: %s' % (i + 1, lines[i].rstrip()[:120]))
sys.stdout.write('\n'.join(out))
sys.stdout.flush()
