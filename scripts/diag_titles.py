# -*- coding: utf-8 -*-
"""诊断1：窗口/菜单标题设置位置 + tts.json 当前内容"""
import sys, re

base = r'C:\Users\zhuzhu\Desktop\my first android app\src\winapp_migrator'
for f in ('ui/main_window.py', 'ui/agent_panel.py'):
    p = base + '\\' + f
    try:
        lines = open(p, encoding='utf-8').read().splitlines()
    except Exception as e:
        sys.stdout.write('ERR %s: %s\n' % (f, e)); continue
    for i, l in enumerate(lines):
        if re.search(r'setWindowTitle|WindowTitle|windowTitle|setTitle|setWindowIconText', l):
            sys.stdout.write('%s:%d: %s\n' % (f, i + 1, l.rstrip()[:130]))

import json, pathlib
cfg = pathlib.Path.home() / '.winapp_migrator' / 'agent' / 'tts.json'
sys.stdout.write('\n--- tts.json ---\n')
try:
    d = json.loads(cfg.read_text(encoding='utf-8'))
    for k, v in d.items():
        sys.stdout.write('%s=%r\n' % (k, v))
except Exception as e:
    sys.stdout.write('ERR %s\n' % e)
sys.stdout.flush()
