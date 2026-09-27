# -*- coding: utf-8 -*-
"""查看 agent_tts.py 完整接口"""
import sys

p = r'C:\Users\zhuzhu\Desktop\my first android app\src\winapp_migrator\core\agent_tts.py'
try:
    src = open(p, encoding='utf-8').read()
except Exception as e:
    sys.stdout.write('ERR %s' % e); sys.stdout.flush(); sys.exit()
lines = src.splitlines()
out = []
for i, l in enumerate(lines):
    ls = l.strip()
    if ls.startswith('def ') or ls.startswith('class '):
        out.append('%d: %s' % (i + 1, l.rstrip()))
# 同时输出配置读写部分（含 tts.json 的键）
out.append('--- 包含 tts.json / voice_id 的行 ---')
for i, l in enumerate(lines):
    if 'tts.json' in l or 'voice_id' in l or 'preferred_name' in l or 'model' in l and 'qwen' in l.lower():
        out.append('%d: %s' % (i + 1, l.rstrip()[:130]))
sys.stdout.write('\n'.join(out))
sys.stdout.flush()
