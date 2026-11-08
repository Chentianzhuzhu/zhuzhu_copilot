# -*- coding: utf-8 -*-
"""收尾精修：第5页防溢出 + 第2/11/17/24页版式平衡"""
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN

PATH = r'transformer_ppt/Transformer架构详解.pptx'
EMU = 914400.0
FONT = '微软雅黑'

p = Presentation(PATH)


def style_block(tb, size, spacing, after, anchor_middle=True):
    tf = tb.text_frame
    tf.word_wrap = True
    if anchor_middle:
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
            r.font.name = FONT


# ---- 第 5 页：加宽要点栏、收紧字号，避免溢出 ----
s5 = p.slides[4]
for sh in s5.shapes:
    w, h = sh.width / EMU, sh.height / EMU
    if 'PICTURE' in str(sh.shape_type):
        sh.left, sh.top = Inches(6.88), Inches(2.62)
        sh.width, sh.height = Inches(5.98), Inches(3.36)
    elif sh.has_text_frame and '第一步' in sh.text_frame.text:
        sh.left, sh.top = Inches(0.62), Inches(1.62)
        sh.width, sh.height = Inches(6.06), Inches(5.18)
        style_block(sh, 15, 1.45, 11)
print('第5页：要点栏 15pt/1.45，配图 5.98×3.36')

# ---- 第 2 页：开场强调页，要点放大 ----
s2 = p.slides[1]
for sh in s2.shapes:
    t = sh.top / EMU
    w = sh.width / EMU
    if sh.has_text_frame and t > 3.8 and w > 10 and '→' in sh.text_frame.text:
        sh.top, sh.height = Inches(3.92), Inches(2.92)
        style_block(sh, 18, 1.62, 12)
        print('第2页：要点 18pt/1.62 垂直居中')

# ---- 第 11 页：公式页，要点放大 ----
s11 = p.slides[10]
for sh in s11.shapes:
    t = sh.top / EMU
    w = sh.width / EMU
    if sh.has_text_frame and t > 3.8 and w > 10 and '→' in sh.text_frame.text:
        sh.top, sh.height = Inches(3.95), Inches(2.90)
        style_block(sh, 17, 1.55, 11)
        print('第11页：要点 17pt/1.55 垂直居中')

# ---- 第 17 页：底部要点上移并放大 ----
s17 = p.slides[16]
for sh in s17.shapes:
    if sh.has_text_frame and '一句话区分' in sh.text_frame.text:
        sh.top, sh.height = Inches(4.46), Inches(2.44)
        style_block(sh, 15.5, 1.6, 12)
        print('第17页：底部要点 15.5pt/1.6 垂直居中')

# ---- 第 24 页：结尾页，要点放大 ----
s24 = p.slides[23]
for sh in s24.shapes:
    t = sh.top / EMU
    w = sh.width / EMU
    if sh.has_text_frame and t > 3.5 and w > 10 and '→' in sh.text_frame.text:
        sh.top, sh.height = Inches(3.75), Inches(3.05)
        style_block(sh, 18, 1.7, 14)
        print('第24页：要点 18pt/1.7 垂直居中')

p.save(PATH)
print('\n保存完成')
