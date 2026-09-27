# -*- coding: utf-8 -*-
"""统计项目规模：文件数与代码行数按扩展名分布"""
import os
from collections import defaultdict

root = r'C:\Users\zhuzhu\Desktop\my first android app'
skip_dirs = {'.git', '.github', 'node_modules', '__pycache__', 'build', 'dist',
             '.workbuddy', 'downloads', 'output', 'mydir', '_probe_test',
             'cordis_test', 'images', 'tts_output', '.venv', 'venv'}
exts = defaultdict(lambda: {'files': 0, 'lines': 0})
total_lines = 0
total_files = 0

for dirpath, dirnames, filenames in os.walk(root):
    dirnames[:] = [d for d in dirnames if d not in skip_dirs and not d.startswith('.')]
    for f in filenames:
        ext = os.path.splitext(f)[1].lower()
        if ext not in ('.py', '.java', '.js', '.ts', '.html', '.css', '.md',
                       '.kt', '.json', '.xml', '.gradle', '.vue'):
            continue
        p = os.path.join(dirpath, f)
        try:
            with open(p, 'r', encoding='utf-8', errors='ignore') as fh:
                lines = sum(1 for _ in fh)
        except Exception:
            lines = 0
        exts[ext]['files'] += 1
        exts[ext]['lines'] += lines
        total_lines += lines
        total_files += 1

print('总文件数: %d, 总代码行数: %d' % (total_files, total_lines))
print()
for ext in sorted(exts, key=lambda e: -exts[e]['lines']):
    print('%-8s 文件数 %4d  行数 %7d' % (ext, exts[ext]['files'], exts[ext]['lines']))
