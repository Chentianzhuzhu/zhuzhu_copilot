# -*- coding: utf-8 -*-
"""修正第 7/16/22 页明显下部留白：放大正文字号 + 加行距 + 内容区垂直居中"""
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN

PATH = r'transformer_ppt/Transformer架构详解.pptx'
EMU = 914400.0

# 页号 -> (字号, 行距, 段后间距pt)
PLAN = {
    7:  (23, 1.85, 22),
    16: (21, 1.70, 16),
    22: (19, 1.75, 13),
}

CONTENT_TOP = 1.55
CONTENT_H = 5.32          # 1.55 -> 6.87

p = Presentation(PATH)

for page, (size, spacing, after) in PLAN.items():
    s = p.slides[page - 1]
    target = None
    for sh in s.shapes:
        if not sh.has_text_frame:
            continue
        txt = sh.text_frame.text
        if '→' in txt and sh.top / EMU > 1.4 and sh.top / EMU < 3.0:
            if target is None or sh.width > target.width:
                target = sh
    if target is None:
        print(f'第{page}页：未找到正文文本框，跳过')
        continue

    target.left = Inches(0.62)
    target.top = Inches(CONTENT_TOP)
    target.width = Inches(12.10)
    target.height = Inches(CONTENT_H)
    tf = target.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    tf.margin_top = 0
    tf.margin_bottom = 0

    for para in tf.paragraphs:
        para.line_spacing = spacing
        para.space_after = Pt(after)
        para.space_before = Pt(0)
        para.alignment = PP_ALIGN.LEFT
        for r in para.runs:
            r.font.size = Pt(size)

    print(f'第{page}页：字号 {size}pt，行距 {spacing}，垂直居中完成')

p.save(PATH)
print('全部保存')
