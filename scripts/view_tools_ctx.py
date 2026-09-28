# -*- coding: utf-8 -*-
"""查看 agent_tools.py 头部 import + tts_speak 描述块 + threading 是否已导入"""
import sys

P = r'C:\Users\zhuzhu\Desktop\my first android app\src\zhuzhu_Copilot\core\agent_tools.py'
lines = open(P, encoding='utf-8').read().splitlines()
out = []
out.append('===== 前 40 行 =====')
for i in range(0, min(40, len(lines))):
    out.append('%d: %s' % (i + 1, lines[i].rstrip()[:110]))
out.append('===== 908~930 (tts_speak 描述) =====')
for i in range(907, min(930, len(lines))):
    out.append('%d: %s' % (i + 1, lines[i].rstrip()[:130]))
out.append('===== 1174~1188 (tts_speak 实现) =====')
for i in range(1173, min(1188, len(lines))):
    out.append('%d: %s' % (i + 1, lines[i].rstrip()[:130]))
sys.stdout.write('\n'.join(out))
sys.stdout.flush()
