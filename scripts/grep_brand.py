# -*- coding: utf-8 -*-
"""全局搜索 "zhuzhu Copilot" 出现的所有文件与行"""
import sys, os, re

root = r'C:\Users\zhuzhu\Desktop\my first android app\src'
hits = []
for dirpath, dirnames, filenames in os.walk(root):
    for fn in filenames:
        if not fn.endswith('.py'):
            continue
        p = os.path.join(dirpath, fn)
        try:
            lines = open(p, encoding='utf-8').read().splitlines()
        except Exception:
            continue
        for i, l in enumerate(lines):
            if 'zhuzhu Copilot' in l:
                hits.append((p.replace(root, 'src'), i + 1, l.rstrip()[:130]))
for h in hits:
    sys.stdout.write('%s:%d: %s\n' % h)
sys.stdout.write('total=%d\n' % len(hits))
sys.stdout.flush()
