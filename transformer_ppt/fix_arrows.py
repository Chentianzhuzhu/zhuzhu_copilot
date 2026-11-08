# -*- coding: utf-8 -*-
"""清除流程图页中落在卡片内部的杂散箭头（第4/14页）"""
from pptx import Presentation

PATH = r'transformer_ppt/Transformer架构详解.pptx'
EMU = 914400.0

p = Presentation(PATH)

for page in (4, 14):
    s = p.slides[page - 1]
    cards, arrows = [], []
    for sh in s.shapes:
        t, l = sh.top / EMU, sh.left / EMU
        w, h = sh.width / EMU, sh.height / EMU
        if 1.6 < t < 2.1 and w > 1.0 and h > 1.5:
            cards.append((l, l + w))
        elif 2.3 < t < 2.8 and 0.25 < w < 0.6 and h < 0.6:
            arrows.append(sh)

    cards.sort()
    removed = 0
    kept = []
    for a in arrows:
        al = a.left / EMU
        ar = al + a.width / EMU
        inside = False
        for cl, cr in cards:
            # 箭头中心落在卡片内部区间（留 0.25in 容差给边缘)
            center = (al + ar) / 2
            if cl + 0.25 < center < cr - 0.25:
                inside = True
                break
        if inside:
            a._element.getparent().remove(a._element)
            removed += 1
        else:
            kept.append((al, ar))

    kept.sort()
    print(f'第{page}页：删除 {removed} 个杂散箭头，保留 {len(kept)} 个')
    for al, ar in kept:
        print(f'    箭头 {al:.2f} -> {ar:.2f}')

p.save(PATH)
print('保存完成')
