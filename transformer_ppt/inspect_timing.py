# -*- coding: utf-8 -*-
"""查看第10页 timing XML 中所有 spid 出现位置的上下文"""
import re
import io
from pptx import Presentation

PATH = r'transformer_ppt/Transformer架构详解.pptx'
p = Presentation(PATH)
s = p.slides[9]
xml = s._element.xml
out = io.StringIO()

for m in re.finditer(r'spid="(\d+)"', xml):
    start = max(0, m.start() - 320)
    end = min(len(xml), m.end() + 160)
    out.write(f'>>> spid={m.group(1)}\n')
    out.write(xml[start:end].replace('><', '>\n<') + '\n\n')

# 顺便看 bldLst 部分
i = xml.find('bldLst')
if i >= 0:
    out.write('=== bldLst 片段 ===\n')
    out.write(xml[i - 60:i + 900].replace('><', '>\n<'))

with open('transformer_ppt/timing_ctx.txt', 'w', encoding='utf-8') as f:
    f.write(out.getvalue())
print('done')
