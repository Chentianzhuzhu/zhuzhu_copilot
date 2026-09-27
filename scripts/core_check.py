# -*- coding: utf-8 -*-
"""检查 core 目录加载机制：.py / .c / .pyd 共存时实际加载哪个"""
import os, sys
core_dir = r'C:\Users\zhuzhu\Desktop\my first android app\src\winapp_migrator\core'
out = []
out.append('=== core 目录文件样本 ===')
for f in sorted(os.listdir(core_dir)):
    if f.startswith(('agent_', '__init__')) or f.endswith('.pyd'):
        out.append(f)
out.append('')
out.append('=== __init__.py 是否存在 ===')
init_py = os.path.join(core_dir, '__init__.py')
out.append(str(os.path.exists(init_py)))
if os.path.exists(init_py):
    out.append('--- __init__.py 内容 ---')
    out.append(open(init_py, encoding='utf-8', errors='ignore').read()[:1500])
open(r'C:\Users\zhuzhu\Desktop\my first android app\scripts\core_load_check.txt', 'w', encoding='utf-8').write('\n'.join(out))
print('done')
