# -*- coding: utf-8 -*-
"""检查流程图页：卡片内文字是否超出卡片宽度，箭头是否与卡片重叠"""
import io
import unicodedata
from pptx import Presentation

EMU = 914400.0
p = Presentation(r'transformer_ppt/Transformer架构详解.pptx')
out = io.StringIO()


def em_width(txt, size):
    total = 0.0
    for ch in txt:
        if unicodedata.east_asian_width(ch) in ('W', 'F'):
            total += 1.0
        elif ch in ' \t':
            total += 0.3
        else:
            total += 0.55
    return total * size


for page in (4, 10, 14):
    s = p.slides[page - 1]
    cards, arrows, inners = [], [], []
    for sh in s.shapes:
        t, l = sh.top / EMU, sh.left / EMU
        w, h = sh.width / EMU, sh.height / EMU
        if 1.6 < t < 2.1 and w > 1.0 and h > 1.5:
            cards.append((l, w, h, sh))
        elif 2.4 < t < 2.9 and w < 0.6 and h < 0.6:
            arrows.append((l, w, h, sh))
        elif sh.has_text_frame and 1.9 < t < 2.6 and w < 2.5:
            inners.append((l, w, h, sh))

    out.write(f'===== 第 {page} 页 =====\n')
    cards.sort()
    inners.sort()
    for l, w, h, sh in cards:
        out.write(f'  卡片 x={l:.2f} w={w:.2f} 右={l+w:.2f}\n')
    for l, w, h, sh in inners:
        raw = sh.text_frame.text.strip()
        avail_pt = (w) * 72 - 7.2
        worst = 0.0
        worst_txt = ''
        for para in sh.text_frame.paragraphs:
            txt = ''.join(r.text for r in para.runs) or para.text
            if not txt.strip():
                continue
            size = None
            for r in para.runs:
                if r.font.size:
                    size = r.font.size.pt
                    break
            size = size or 14
            em = em_width(txt, size)
            if em > worst:
                worst, worst_txt = em, txt
        ratio = worst / avail_pt if avail_pt > 0 else 99
        flag = '  <== 文字超宽' if ratio > 1.0 else ''
        out.write(f'  文字 x={l:.2f} w={w:.2f} 可用={avail_pt:.0f}pt 实际={worst:.0f}pt '
                  f'比={ratio:.2f}{flag}  文本={worst_txt!r}\n')
    out.write('\n')

with open('transformer_ppt/flow_check.txt', 'w', encoding='utf-8') as f:
    f.write(out.getvalue())
print('done')
