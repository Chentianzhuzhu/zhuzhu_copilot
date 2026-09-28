# -*- coding: utf-8 -*-
"""搜索全项目中的设置面板/设置对话框/设置按钮类"""
import os, re, sys

base = r'C:\Users\zhuzhu\Desktop\my first android app\src\zhuzhu_Copilot'
pat = re.compile(r'class\s+(\w*[Ss]etting\w*)|\b(\w*[Ss]etting\w*Panel\w*)\b|设置面板|设置按钮|settings_btn|SettingsDialog|SettingsPanel')
hits = []
for root, dirs, files in os.walk(base):
    if '__pycache__' in root:
        continue
    for f in files:
        if not f.endswith('.py'):
            continue
        p = os.path.join(root, f)
        try:
            lines = open(p, encoding='utf-8').read().splitlines()
        except Exception:
            continue
        for i, l in enumerate(lines):
            if pat.search(l):
                hits.append((os.path.relpath(p, base), i + 1, l.rstrip()[:130]))
out = []
for h in hits:
    out.append('%s:%d: %s' % h)
sys.stdout.write('TOTAL=%d\n%s\n' % (len(hits), '\n'.join(out)))
sys.stdout.flush()
