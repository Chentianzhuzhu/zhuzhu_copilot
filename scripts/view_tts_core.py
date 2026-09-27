# -*- coding: utf-8 -*-
"""查看 agent_tts.py 的 synthesize / list_voices / 音色解析相关代码"""
import sys
p = r'C:\Users\zhuzhu\Desktop\my first android app\src\winapp_migrator\core\agent_tts.py'
src = open(p, encoding='utf-8').read()
lines = src.splitlines()

import re
# 找关键函数定义位置
for i, l in enumerate(lines):
    if re.search(r'^def (synthesize|synthesize_stream|list_voices|_resolve|save_config|load_config|_strip_wav_header)', l):
        sys.stdout.write('%d: %s\n' % (i + 1, l.rstrip()[:120]))

# 打印 synthesize 定义到 synthesize_stream 之前（约 20 行窗口）
def show_range(name, span=25):
    for i, l in enumerate(lines):
        if l.startswith('def ' + name + '('):
            for j in range(i, min(i + span, len(lines))):
                sys.stdout.write('%d: %s\n' % (j + 1, lines[j].rstrip()[:130]))
            sys.stdout.write('---\n')
            return

show_range('list_voices', 30)
show_range('synthesize', 30)
sys.stdout.flush()
