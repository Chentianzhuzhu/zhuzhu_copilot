# -*- coding: utf-8 -*-
"""诊断第3/6页：列出所有形状，找出被误改的标题框与重叠文本框"""
import io
from pptx import Presentation

EMU = 914400.0
p = Presentation(r'transformer_ppt/Transformer架构详解.pptx')
out = io.StringIO()

for page in (3, 6):
    s = p.slides[page - 1]
    out.write(f'===== 第 {page} 页（共 {len(s.shapes)} 个形状）=====\n')
    boxes = []
    for i, sh in enumerate(s.shapes):
        kind = str(sh.shape_type).split(' ')[0]
        l, t = sh.left / EMU, sh.top / EMU
        w, h = sh.width / EMU, sh.height / EMU
        size = ''
        color = ''
        txt = ''
        if sh.has_text_frame and sh.text_frame.text.strip():
            tf = sh.text_frame
            txt = tf.text.strip().replace('\n', ' ⏎ ')[:60]
            for para in tf.paragraphs:
                for r in para.runs:
                    if r.font.size:
                        size = f'{r.font.size.pt}pt'
                        break
                if size:
                    break
            for para in tf.paragraphs:
                for r in para.runs:
                    try:
                        if r.font.color and r.font.color.rgb:
                            color = str(r.font.color.rgb)
                            break
                    except Exception:
                        pass
                if color:
                    break
            boxes.append((l, t, i))
        out.write(f'[{i:2d}] {kind:11s} x={l:5.2f} y={t:5.2f} {w:5.2f}x{h:5.2f} '
                  f'{size:6s} {color:7s} {txt}\n')
    out.write('\n')

with open('transformer_ppt/diag36.txt', 'w', encoding='utf-8') as f:
    f.write(out.getvalue())
print('done')
