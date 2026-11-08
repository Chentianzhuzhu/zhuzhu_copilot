# -*- coding: utf-8 -*-
import io
from pptx import Presentation

p = Presentation(r'transformer_ppt/Transformer架构详解.pptx')
s = p.slides[18]  # 第 19 页
out = io.StringIO()
out.write('===== 第 19 页 全部形状 =====\n')
for sh in s.shapes:
    kind = str(sh.shape_type).split(' ')[0]
    pos = f'({sh.left/914400:.2f},{sh.top/914400:.2f}) {sh.width/914400:.2f}x{sh.height/914400:.2f}'
    txt = ''
    if sh.has_text_frame and sh.text_frame.text.strip():
        txt = sh.text_frame.text.strip().replace('\n', ' | ')[:100]
    out.write(f'{kind:14s} {pos:34s} {txt}\n')
with open('transformer_ppt/p19.txt', 'w', encoding='utf-8') as f:
    f.write(out.getvalue())
print('done')
