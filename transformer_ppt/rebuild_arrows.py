# -*- coding: utf-8 -*-
"""重建流程图页（第4/10/14页）的卡片间箭头：删除杂散形状，统一生成标准箭头并置于顶层"""
from pptx import Presentation
from pptx.util import Inches, Emu
from pptx.enum.shapes import MSO_SHAPE
from pptx.dml.color import RGBColor

PATH = r'transformer_ppt/Transformer架构详解.pptx'
EMU = 914400.0
ARROW_COLOR = RGBColor(0x0E, 0x5A, 0x8A)

p = Presentation(PATH)

for page in (4, 10, 14):
    s = p.slides[page - 1]

    # 1) 收集卡片（y≈1.72, h≈2.00 的圆角矩形）
    cards = []
    for sh in s.shapes:
        t, l = sh.top / EMU, sh.left / EMU
        w, h = sh.width / EMU, sh.height / EMU
        try:
            auto = str(sh.auto_shape_type)
        except Exception:
            continue
        if 'ROUNDED_RECTANGLE' in auto and 1.6 < t < 1.85 and h > 1.8:
            cards.append((l, l + w))
    if len(cards) < 2:
        print(f'第{page}页：卡片不足，跳过')
        continue
    cards.sort()

    # 2) 删除：落在卡片内部的杂散形状 + 缝隙间的旧箭头/矩形
    card_lo, card_hi = cards[0][0], cards[-1][1]
    gaps = [(cards[i][1], cards[i + 1][0]) for i in range(len(cards) - 1)]
    removed = 0
    for sh in list(s.shapes):
        t, l = sh.top / EMU, sh.left / EMU
        w, h = sh.width / EMU, sh.height / EMU
        r = l + w
        if not (2.10 < t < 3.00 and 0.2 < w < 0.7 and 0.2 < h < 0.7):
            continue
        center = (l + r) / 2
        in_card = any(cl + 0.20 < center < cr - 0.20 for cl, cr in cards)
        in_gap = any(gl - 0.06 < center < gr + 0.06 for gl, gr in gaps)
        if in_card or in_gap:
            sh._element.getparent().remove(sh._element)
            removed += 1

    # 3) 在每个缝隙中生成标准箭头
    mid_y = 1.72 + 2.00 / 2          # 卡片垂直中线
    AH = 0.40
    for gl, gr in gaps:
        gw = gr - gl
        aw = min(gw - 0.02, 0.40)
        left = gl + (gw - aw) / 2
        arrow = s.shapes.add_shape(
            MSO_SHAPE.RIGHT_ARROW,
            Inches(left), Inches(mid_y - AH / 2), Inches(aw), Inches(AH)
        )
        arrow.fill.solid()
        arrow.fill.fore_color.rgb = ARROW_COLOR
        arrow.line.fill.background()
        arrow.shadow.inherit = False

    print(f'第{page}页：删除 {removed} 个旧形状，重建 {len(gaps)} 个箭头（居中、顶层）')

p.save(PATH)
print('\n保存完成')
