# -*- coding: utf-8 -*-
"""列出第 4 页所有形状精确坐标，定位箭头错位"""
import io
from pptx import Presentation

EMU = 914400.0
p = Presentation(r'transformer_ppt/Transformer架构详解.pptx')
s = p.slides[3]
out = io.StringIO()
items = []
for i, sh in enumerate(s.shapes):
    items.append((sh.left / EMU, i, sh))

items.sort(key=lambda x: x[0])
for l, i, sh in items:
    t = sh.top / EMU
    w = sh.width / EMU
    h = sh.height / EMU
    kind = str(sh.shape_type).split(' ')[0]
    txt = ''
    if sh.has_text_frame and sh.text_frame.text.strip():
        txt = sh.text_frame.text.strip().replace('\n', '/')[:24]
    auto = ''
    try:
        auto = sh.auto_shape_type
    except Exception:
        pass
    out.write(f'[{i:2d}] x={l:6.2f}->{l+w:6.2f} y={t:5.2f} h={h:4.2f} '
              f'{kind:12s} {str(auto) if auto else "":22s} {txt}\n')

with open('transformer_ppt/p4_geo.txt', 'w', encoding='utf-8') as f:
    f.write(out.getvalue())
print('done')
