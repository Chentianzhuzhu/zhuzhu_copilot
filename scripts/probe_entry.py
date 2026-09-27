# -*- coding: utf-8 -*-
"""找 agent_tools 的执行入口函数"""
import sys
P = r'C:\Users\zhuzhu\Desktop\my first android app\src\winapp_migrator\core\agent_tools.py'
lines = open(P, encoding='utf-8').read().splitlines()
for i, l in enumerate(lines):
    if l.startswith('def '):
        sys.stdout.write('%d: %s\n' % (i + 1, l.rstrip()[:100]))
sys.stdout.flush()
