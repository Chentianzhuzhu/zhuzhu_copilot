# -*- coding: utf-8 -*-
"""输出 agent_tts.py 全文 + agent_tools.py tts_speak 实现 + tts.json 内容"""
import sys, json, os

p = r'C:\Users\zhuzhu\Desktop\my first android app\src\zhuzhu_Copilot\core\agent_tts.py'
try:
    src = open(p, encoding='utf-8').read()
except Exception as e:
    src = 'ERR %s' % e
sys.stdout.write('===== agent_tts.py 全文 =====\n%s\n' % src)

t = r'C:\Users\zhuzhu\Desktop\my first android app\src\zhuzhu_Copilot\core\agent_tools.py'
lines = open(t, encoding='utf-8').read().splitlines()
sys.stdout.write('===== agent_tools.py 1150~1192 =====\n')
for i in range(1149, min(1192, len(lines))):
    sys.stdout.write('%d: %s\n' % (i + 1, lines[i].rstrip()[:150]))

cfg = os.path.expanduser(r'~\.zhuzhu_Copilot\agent\tts.json')
sys.stdout.write('===== tts.json =====\n')
try:
    d = json.load(open(cfg, encoding='utf-8'))
    sys.stdout.write(json.dumps(d, ensure_ascii=False, indent=2)[:1200] + '\n')
except Exception as e:
    sys.stdout.write('ERR %s\n' % e)
sys.stdout.flush()
