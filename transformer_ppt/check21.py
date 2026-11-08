# -*- coding: utf-8 -*-
"""检查第21页卡片几何与文字"""
import io
from pptx import Presentation

EMU = 914400.0
p = Presentation(r'transformer_ppt/Transformer架构详解.pptx')
s = p.slides[20]
out = io.StringIO()
for i, sh in enumerate(s.shapes):
    kind = str(sh.shape_type).split(' ')[0]
    l, t = sh.left / EMU, sh.top / EMU
    w, h = sh.width / EMU, sh.height / EMU
    size = ''
    txt = ''
    if sh.has_text_frame and sh.text_frame.text.strip():
        txt = sh.text_frame.text.strip().replace('\n', ' | ')
        for para in sh.text_frame.paragraphs:
            for r in para.runs:
                if r.font.size:
                    size = f'{r.font.size.pt}pt'
                    break
            if size:
                break
    out.write(f'[{i:2d}] {kind:11s} x={l:5.2f} y={t:5.2f} {w:5.2f}x{h:5.2f} {size:6s} {txt[:70]}\n')
with open('transformer_ppt/p21.txt', 'w', encoding='utf-8') as f:
    f.write(out.getvalue())
print('done')
