# -*- coding: utf-8 -*-
"""查找 agent_tools.py 中 tts_speak / tts 相关工具定义与 agent_tts 导入"""
import sys, re

p = r'C:\Users\zhuzhu\Desktop\my first android app\src\winapp_migrator\core\agent_tools.py'
lines = open(p, encoding='utf-8').read().splitlines()
out = []
for i, l in enumerate(lines):
    if 'tts' in l.lower() or 'agent_tts' in l or 'speak' in l.lower():
        out.append('%d: %s' % (i + 1, l.rstrip()[:140]))
sys.stdout.write('TOTAL=%d\n%s' % (len(out), '\n'.join(out)))
sys.stdout.flush()
