# -*- coding: utf-8 -*-
"""全面扫描：按实际宽度模拟换行，检测每行行首是否为中文标点（行首标点禁则）"""
import io
import unicodedata
from pptx import Presentation

EMU = 914400.0
PUNCT_HEADS = set('，。、；：？！）】》」』%,.;:?!)]}')

p = Presentation(r'transformer_ppt/Transformer架构详解.pptx')
out = io.StringIO()
hits = []

for idx, s in enumerate(p.slides, 1):
    for sh in s.shapes:
        if not sh.has_text_frame:
            continue
        txt_all = sh.text_frame.text.strip()
        if not txt_all:
            continue
        w_in = sh.width / EMU
        if w_in < 1.0:
            continue
        tf = sh.text_frame
        # 左右内边距粗略扣除
        avail = w_in * 72 - 14.4

        for pi, para in enumerate(tf.paragraphs):
            raw = ''.join(r.text for r in para.runs) or para.text
            if not raw.strip():
                continue
            size = None
            for r in para.runs:
                if r.font.size:
                    size = r.font.size.pt
                    break
            if size is None:
                size = 16.0

            # 逐字符模拟换行
            line_w = 0.0
            line_head = None
            prev_head = None
            for ch in raw:
                if unicodedata.east_asian_width(ch) in ('W', 'F'):
                    cw = size * 1.0
                elif ch in ' \t':
                    cw = size * 0.30
                else:
                    cw = size * 0.55

                if line_w + cw > avail and line_w > 0:
                    # 换行：当前字符成为新行行首
                    if line_head is not None and line_head in PUNCT_HEADS:
                        hits.append((idx, sh.name, pi, line_head, raw))
                    line_head = ch
                    line_w = cw
                else:
                    if line_head is None:
                        line_head = ch
                    line_w += cw

            if line_head is not None and line_head in PUNCT_HEADS and len(raw) > 6:
                # 首行行首为标点（可能是段落本身的写法）
                hits.append((idx, sh.name, pi, line_head, raw))

# 去重
seen = set()
unique = []
for h in hits:
    key = (h[0], h[1], h[2], h[4][:20])
    if key in seen:
        continue
    seen.add(key)
    unique.append(h)

for idx, name, pi, ch, raw in unique:
    out.write(f'第{idx:2d}页 {name:16s} 段{pi} 行首标点「{ch}」  {raw[:60]}\n')

out.write(f'\n共 {len(unique)} 处潜在行首标点\n')
with open('transformer_ppt/punct_report.txt', 'w', encoding='utf-8') as f:
    f.write(out.getvalue())
print(f'检测完成，{len(unique)} 处')
