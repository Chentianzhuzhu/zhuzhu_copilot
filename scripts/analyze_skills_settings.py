# -*- coding: utf-8 -*-
"""提取 agent_skills.py 的 load_settings / save_settings"""
path = r'C:\Users\zhuzhu\Desktop\my first android app\src\winapp_migrator\core\agent_skills.py'
lines = open(path, encoding='utf-8', errors='ignore').read().split('\n')
out = []
out.append('总行数: %d' % len(lines))
for i, l in enumerate(lines):
    if 'def load_settings' in l or 'def save_settings' in l or 'SETTINGS' in l or 'settings.json' in l:
        out.append('%4d %s' % (i + 1, l.rstrip()[:130]))
with open(r'C:\Users\zhuzhu\Desktop\my first android app\scripts\skills_settings.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(out))
print('done')
