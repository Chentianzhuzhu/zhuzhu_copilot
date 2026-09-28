# -*- coding: utf-8 -*-
"""分析 agent_tools.py 工具注册机制"""
import re

path = r'C:\Users\zhuzhu\Desktop\my first android app\src\zhuzhu_Copilot\core\agent_tools.py'
src = open(path, encoding='utf-8', errors='ignore').read()
lines = src.split('\n')
out = []
out.append('总行数: %d' % len(lines))
out.append('')

# 找 def/class/Tool 相关
for i, l in enumerate(lines):
    s = l.rstrip()
    if re.search(r'^(def |class |    def )', s):
        out.append('%4d %s' % (i + 1, s[:130]))

with open(r'C:\Users\zhuzhu\Desktop\my first android app\scripts\tools_index.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(out))
