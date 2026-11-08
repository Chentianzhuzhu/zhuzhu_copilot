# -*- coding: utf-8 -*-
from pptx import Presentation

p = Presentation(r'transformer_ppt/Transformer架构详解.pptx')
for i, s in enumerate(p.slides, 1):
    title = ''
    n_text = 0
    pics = tables = charts = 0
    for sh in s.shapes:
        if sh.shape_type is not None:
            st = str(sh.shape_type)
            if 'PICTURE' in st:
                pics += 1
            elif 'TABLE' in st:
                tables += 1
            elif 'CHART' in st:
                charts += 1
        if sh.has_text_frame and sh.text_frame.text.strip():
            n_text += 1
            t = sh.text_frame.text.strip().replace('\n', ' / ')
            if not title and len(t) < 40:
                title = t
    extra = []
    if pics: extra.append(f'图{pics}')
    if tables: extra.append(f'表{tables}')
    if charts: extra.append(f'图表{charts}')
    print(f'{i:2d}. {title[:34]:36s} 文本块={n_text:2d} {" ".join(extra)}')
