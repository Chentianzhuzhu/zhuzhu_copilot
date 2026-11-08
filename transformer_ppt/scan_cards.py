# -*- coding: utf-8 -*-
"""扫描所有页面：找出「卡片过高、文字只占上部」的页面"""
import io
from pptx import Presentation
from pptx.util import Emu

EMU = 914400.0
p = Presentation(r'transformer_ppt/Transformer架构详解.pptx')
out = io.StringIO()

for idx, s in enumerate(p.slides, 1):
    cards = []
    texts = []
    for sh in s.shapes:
        l, t = sh.left / EMU, sh.top / EMU
        w, h = sh.width / EMU, sh.height / EMU
        if str(sh.shape_type).startswith('AUTO_SHAPE') and h > 3.0 and w > 2.0:
            cards.append((l, t, w, h))
        elif sh.has_text_frame and h > 2.0 and w > 2.0 and t > 1.5:
            texts.append((l, t, w, h, len(sh.text_frame.text.strip())))

    if not cards:
        continue

    # 估算文字实际高度：按 21pt/2.46in 宽估算
    out.write(f'第{idx:2d}页  卡片 {len(cards)} 个: ')
    for l, t, w, h in cards:
        out.write(f'[{w:.2f}x{h:.2f}@y{t:.2f}] ')
    out.write('\n')
    for l, t, w, h, n in texts:
        # 每行约 2.46in/21pt ≈ 8.4 字
        chars_per_line = max(6, int(w * 72 / 21 * 0.95))
        lines = max(1, -(-n // chars_per_line))
        ink_h = lines * 21 * 1.35 / 72
        gap = h - ink_h
        flag = '  <== 留白大' if gap > 1.3 else ''
        out.write(f'        文字 x={l:.2f} {w:.2f}x{h:.2f} 字数={n} 估算行数={lines} '
                  f'文字高≈{ink_h:.2f}in 空白≈{gap:.2f}in{flag}\n')

with open('transformer_ppt/cardgaps.txt', 'w', encoding='utf-8') as f:
    f.write(out.getvalue())
print('done')
