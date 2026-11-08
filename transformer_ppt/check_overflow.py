# -*- coding: utf-8 -*-
"""检测每页文本框是否可能溢出（估算文字高度 vs 页面高度）"""
import io
from pptx import Presentation
from pptx.util import Emu

EMU_IN = 914400.0
PAGE_H = 7.5

p = Presentation(r'transformer_ppt/Transformer架构详解.pptx')
out = io.StringIO()

for idx, s in enumerate(p.slides, 1):
    for sh in s.shapes:
        if not sh.has_text_frame:
            continue
        tf = sh.text_frame
        raw = tf.text.strip()
        if not raw:
            continue
        top = sh.top / EMU_IN
        # 估算所需高度：逐段落 行数 × 字号 × 1.35
        need = 0.0
        for para in tf.paragraphs:
            txt = ''.join(r.text for r in para.runs) or para.text
            if not txt.strip():
                continue
            size = None
            for r in para.runs:
                if r.font.size:
                    size = r.font.size.pt
                    break
            if size is None:
                size = 16.0
            # 中文字符按 1 字宽，按文本框宽度估算每行字数
            w_in = sh.width / EMU_IN
            chars_per_line = max(8.0, w_in * 72 / size * 0.95)
            lines = max(1, int(len(txt) / chars_per_line) + (1 if len(txt) % chars_per_line else 0))
            need += lines * size * 1.38 / 72.0 + 0.04
        bottom = top + need
        if bottom > PAGE_H - 0.08:
            out.write(f'第{idx}页  溢出风险  底部估算={bottom:.2f}in  top={top:.2f}  '
                      f'需高={need:.2f}  字号估算  文本={raw[:40]!r}\n')
            out.write(f'    形状: name={sh.name} pos=({sh.left/EMU_IN:.2f},{top:.2f}) '
                      f'size=({sh.width/EMU_IN:.2f}x{sh.height/EMU_IN:.2f})in\n')

with open('transformer_ppt/overflow.txt', 'w', encoding='utf-8') as f:
    f.write(out.getvalue() or '未检测到溢出')
print('done')
