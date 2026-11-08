# -*- coding: utf-8 -*-
"""诊断第24页结尾页：确认要点条数是否丢失"""
import io
from pptx import Presentation

EMU = 914400.0
p = Presentation(r'transformer_ppt/Transformer架构详解.pptx')
s = p.slides[23]
out = io.StringIO()
out.write(f'第 24 页共 {len(s.shapes)} 个形状\n')
for i, sh in enumerate(s.shapes):
    kind = str(sh.shape_type).split(' ')[0]
    l, t = sh.left / EMU, sh.top / EMU
    w, h = sh.width / EMU, sh.height / EMU
    txt = ''
    paras = 0
    size = ''
    if sh.has_text_frame and sh.text_frame.text.strip():
        paras = len([x for x in sh.text_frame.paragraphs if x.text.strip()])
        txt = sh.text_frame.text.strip().replace('\n', ' ⏎ ')[:90]
        for para in sh.text_frame.paragraphs:
            for r in para.runs:
                if r.font.size:
                    size = f'{r.font.size.pt}pt'
                    break
            if size:
                break
    out.write(f'[{i:2d}] {kind:11s} x={l:5.2f} y={t:5.2f} {w:5.2f}x{h:5.2f} {size:6s} 段数={paras} {txt}\n')
with open('transformer_ppt/p24.txt', 'w', encoding='utf-8') as f:
    f.write(out.getvalue())
print('done')
