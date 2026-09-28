# -*- coding: utf-8 -*-
"""提取 _AgentSettingsDialog 类完整结构（agent_panel.py 681 行起）"""
import sys

p = r'C:\Users\zhuzhu\Desktop\my first android app\src\zhuzhu_Copilot\ui\agent_panel.py'
lines = open(p, encoding='utf-8').read().splitlines()

# 找类开始与结束
start = None
end = None
for i, l in enumerate(lines):
    if l.startswith('class _AgentSettingsDialog'):
        start = i
    if start is not None and i > start and l.startswith('class ') and not l.startswith('class _AgentSettingsDialog'):
        end = i
        break
if end is None:
    end = len(lines)
# 找所有方法签名
out = []
out.append('=== class 区间: %d ~ %d (共 %d 行) ===' % (start + 1, end, end - start))
for i in range(start, end):
    l = lines[i]
    ls = l.strip()
    if ls.startswith('def ') or ls.startswith('class ') or ' = QComboBox' in l or 'addItem' in l or 'QComboBox' in l and 'self.' in l:
        out.append('%d: %s' % (i + 1, l.rstrip()[:120]))
sys.stdout.write('\n'.join(out))
sys.stdout.flush()
