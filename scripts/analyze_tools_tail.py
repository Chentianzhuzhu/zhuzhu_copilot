# -*- coding: utf-8 -*-
"""定位 TOOLS 列表结尾与 create_skill schema 位置 + main_window imports"""
path = r'C:\Users\zhuzhu\Desktop\my first android app\src\winapp_migrator\core\agent_tools.py'
lines = open(path, encoding='utf-8', errors='ignore').read().split('\n')
out = []
# 找 create_skill / web_search / fast_download 等 schema 在 TOOLS 中的位置
for i, l in enumerate(lines):
    if '"name": "' in l and i < 900:
        out.append('%4d %s' % (i + 1, l.strip()[:80]))
out.append('---- TOOLS 列表结束与 tool_schemas 之间的代码 ----')
for i, l in enumerate(lines):
    if l.strip() == ']' and i > 800 and i < 920:
        out.append('%4d %s' % (i + 1, l.rstrip()[:80]))
        # 输出后续 30 行
        out.extend(['%4d %s' % (j + 1, lines[j].rstrip()[:100]) for j in range(i + 1, min(i + 30, 920))])
        break
with open(r'C:\Users\zhuzhu\Desktop\my first android app\scripts\tools_tail.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(out))
print('done')
