# -*- coding: utf-8 -*-
"""列出第10/14页形状的 z 序与坐标，确认箭头是否被卡片遮挡"""
import io
from pptx import Presentation

EMU = 914400.0
p = Presentation(r'transformer_ppt/Transformer架构详解.pptx')
out = io.StringIO()

for page in (10, 14):
    s = p.slides[page - 1]
    out.write(f'===== 第 {page} 页（按 z 序，从底到顶）=====\n')
    for z, sh in enumerate(s.shapes):
        l, t = sh.left / EMU, sh.top / EMU
        w, h = sh.width / EMU, sh.height / EMU
        kind = str(sh.shape_type).split(' ')[0]
        auto = ''
        try:
            auto = str(sh.auto_shape_type).split(' ')[0]
        except Exception:
            pass
        txt = ''
        if sh.has_text_frame and sh.text_frame.text.strip():
            txt = sh.text_frame.text.strip().replace('\n', '/')[:26]
        out.write(f'z={z:2d} x={l:6.2f}->{l+w:6.2f} y={t:5.2f}->{t+h:5.2f} '
                  f'{kind:11s} {auto:16s} {txt}\n')
    out.write('\n')

with open('transformer_ppt/z_order.txt', 'w', encoding='utf-8') as f:
    f.write(out.getvalue())
print('done')
