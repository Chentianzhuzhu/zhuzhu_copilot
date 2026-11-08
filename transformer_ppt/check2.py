# -*- coding: utf-8 -*-
from pptx import Presentation

p = Presentation(r'transformer_ppt/Transformer架构详解.pptx')
for idx in (2, 11):
    s = p.slides[idx - 1]
    print(f'===== 第 {idx} 页 =====')
    for sh in s.shapes:
        if sh.has_text_frame and sh.text_frame.text.strip():
            t = sh.text_frame.text.strip()
            print(f'  [{sh.shape_type}] {t[:120]}')
    print()
