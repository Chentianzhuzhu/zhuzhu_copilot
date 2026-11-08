# -*- coding: utf-8 -*-
"""扫描每页内容实际占用的垂直范围，找出下部大片留白的页面"""
import io
from pptx import Presentation

EMU = 914400.0
PAGE_H = 7.5
CONTENT_BOTTOM_LIMIT = 6.95   # 内容区底界（页脚 7.12 之上）

p = Presentation(r'transformer_ppt/Transformer架构详解.pptx')
out = io.StringIO()

for idx, s in enumerate(p.slides, 1):
    max_bottom = 0.0
    body_shape = None
    kinds = set()
    for sh in s.shapes:
        top = sh.top / EMU
        h = sh.height / EMU
        bottom = top + h
        txt = sh.text_frame.text.strip() if sh.has_text_frame else ''
        # 排除页眉标题/页脚/页码/左上色条/分隔线
        if top < 1.3 or top > 6.95:
            continue
        if txt and txt.isdigit():
            continue
        if len(txt) < 6 and not txt:
            pass
        st = str(sh.shape_type)
        if 'PICTURE' in st: kinds.add('图')
        elif 'TABLE' in st: kinds.add('表')
        elif 'CHART' in st: kinds.add('图表')
        if bottom > max_bottom:
            max_bottom = bottom
    gap = CONTENT_BOTTOM_LIMIT - max_bottom
    flag = '  <== 留白偏大' if gap > 1.6 else ''
    out.write(f'第{idx:2d}页  内容底={max_bottom:.2f}in  剩余空白={gap:.2f}in  '
              f'{"".join(sorted(kinds))}{flag}\n')

with open('transformer_ppt/whitespace.txt', 'w', encoding='utf-8') as f:
    f.write(out.getvalue())
print('done')
