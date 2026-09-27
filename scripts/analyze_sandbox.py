# -*- coding: utf-8 -*-
"""分析 agent_sandbox.assess_tool 与配置机制"""
path = r'C:\Users\zhuzhu\Desktop\my first android app\src\winapp_migrator\core\agent_sandbox.py'
lines = open(path, encoding='utf-8', errors='ignore').read().split('\n')
out = []
out.append('===== assess_tool 相关 =====')
for i, l in enumerate(lines):
    if 'assess_tool' in l or 'WHITELIST' in l or 'ALLOW' in l or 'dangerous' in l.lower():
        out.append('%4d %s' % (i + 1, l.rstrip()[:120]))
out.append('')
out.append('===== 工具名白名单/评估函数体 =====')
# 找到 def assess_tool 附近
for i, l in enumerate(lines):
    if l.strip().startswith('def assess_tool'):
        out.extend(['%4d %s' % (j + 1, lines[j].rstrip()[:130]) for j in range(i, min(i + 80, len(lines)))])
        break
with open(r'C:\Users\zhuzhu\Desktop\my first android app\scripts\sandbox_index.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(out))
print('done')
