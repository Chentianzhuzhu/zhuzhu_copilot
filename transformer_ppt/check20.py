# -*- coding: utf-8 -*-
"""检查第 20 页全部形状与图表结构"""
import io
from pptx import Presentation
from pptx.oxml.ns import qn

EMU = 914400.0
p = Presentation(r'transformer_ppt/Transformer架构详解.pptx')
s = p.slides[19]
out = io.StringIO()
out.write('===== 第 20 页形状（按 z 序）=====\n')
for i, sh in enumerate(s.shapes):
    kind = str(sh.shape_type).split(' ')[0]
    l, t = sh.left / EMU, sh.top / EMU
    w, h = sh.width / EMU, sh.height / EMU
    txt = ''
    if sh.has_text_frame and sh.text_frame.text.strip():
        txt = sh.text_frame.text.strip().replace('\n', ' | ')[:50]
    name = sh.name
    extra = ''
    if sh.has_chart:
        ch = sh.chart
        extra = f' CHART type={ch.chart_type} series={len(ch.plots[0].series) if ch.plots else 0}'
        try:
            extra += f' cats={list(ch.plots[0].categories)}'
        except Exception:
            pass
        try:
            extra += f' vals={list(ch.plots[0].series[0].values)}'
        except Exception:
            pass
    out.write(f'[{i:2d}] {kind:12s} name={name:22s} x={l:6.2f} y={t:5.2f} {w:5.2f}x{h:5.2f}{extra}\n      {txt}\n')

with open('transformer_ppt/p20.txt', 'w', encoding='utf-8') as f:
    f.write(out.getvalue())
print('done')
