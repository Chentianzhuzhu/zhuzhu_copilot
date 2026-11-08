# -*- coding: utf-8 -*-
import io
from pptx import Presentation

EMU = 914400.0
p = Presentation(r'transformer_ppt/Transformer架构详解.pptx')
out = io.StringIO()
for page in (7, 16, 22):
    s = p.slides[page - 1]
    out.write(f'===== 第 {page} 页 =====\n')
    for i, sh in enumerate(s.shapes):
        kind = str(sh.shape_type).split(' ')[0]
        pos = f'({sh.left/EMU:.2f},{sh.top/EMU:.2f}) {sh.width/EMU:.2f}x{sh.height/EMU:.2f}'
        txt = ''
        size = ''
        if sh.has_text_frame and sh.text_frame.text.strip():
            txt = sh.text_frame.text.strip().replace('\n', ' | ')[:58]
            for para in sh.text_frame.paragraphs:
                for r in para.runs:
                    if r.font.size:
                        size = f'{r.font.size.pt}pt'
                        break
                if size:
                    break
        out.write(f'[{i}] {kind:11s} {pos:32s} {size:6s} {txt}\n')
    out.write('\n')
with open('transformer_ppt/pg71622.txt', 'w', encoding='utf-8') as f:
    f.write(out.getvalue())
print('done')
