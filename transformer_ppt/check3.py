# -*- coding: utf-8 -*-
import io
from pptx import Presentation

p = Presentation(r'transformer_ppt/Transformer架构详解.pptx')
out = io.StringIO()
for idx in (11, 20, 23):
    s = p.slides[idx - 1]
    out.write(f'===== 第 {idx} 页 =====\n')
    for sh in s.shapes:
        if sh.has_text_frame and sh.text_frame.text.strip():
            out.write('  ' + sh.text_frame.text.strip().replace('\n', ' | ')[:160] + '\n')
        elif str(sh.shape_type).startswith('TABLE'):
            out.write('  [表格]\n')
        elif str(sh.shape_type).startswith('PICTURE'):
            out.write('  [图片]\n')
    out.write('\n')
with open('transformer_ppt/pages.txt', 'w', encoding='utf-8') as f:
    f.write(out.getvalue())
print('done')
