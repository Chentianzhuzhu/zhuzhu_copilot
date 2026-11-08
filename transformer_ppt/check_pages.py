# -*- coding: utf-8 -*-
import io
from pptx import Presentation

p = Presentation(r'transformer_ppt/Transformer架构详解.pptx')
for page in (3, 17):
    s = p.slides[page - 1]
    out = io.StringIO()
    out.write(f'===== 第 {page} 页 =====\n')
    for i, sh in enumerate(s.shapes):
        kind = str(sh.shape_type).split(' ')[0]
        pos = f'({sh.left/914400:.2f},{sh.top/914400:.2f}) {sh.width/914400:.2f}x{sh.height/914400:.2f}'
        txt = ''
        if sh.has_text_frame and sh.text_frame.text.strip():
            txt = sh.text_frame.text.strip().replace('\n', ' | ')[:70]
        out.write(f'[{i}] {kind:12s} {pos:36s} {txt}\n')
    with open(f'transformer_ppt/pg{page}.txt', 'w', encoding='utf-8') as f:
        f.write(out.getvalue())
print('done')
