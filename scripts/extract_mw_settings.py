# -*- coding: utf-8 -*-
"""提取 main_window.py 设置相关代码行 + 输出到 stdout"""
import re, sys

p = r'C:\Users\zhuzhu\Desktop\my first android app\src\winapp_migrator\ui\main_window.py'
lines = open(p, encoding='utf-8').read().splitlines()
hits = []
for i, l in enumerate(lines):
    if re.search(r'settings|Settings|设置|config|Config', l):
        hits.append((i + 1, l.rstrip()[:120]))
out = '\n'.join(f'{n}: {t}' for n, t in hits)
sys.stdout.write('TOTAL=%d\n%s\n' % (len(hits), out))
sys.stdout.flush()
