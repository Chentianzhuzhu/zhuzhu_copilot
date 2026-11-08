# -*- coding: utf-8 -*-
"""修复第 19 页：压缩两栏卡片高度，底部补回总结要点"""
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN

PATH = r'transformer_ppt/Transformer架构详解.pptx'
p = Presentation(PATH)
s = p.slides[18]

EMU = 914400.0
for sh in s.shapes:
    top = sh.top / EMU
    h = sh.height / EMU
    # 两个卡片底色块：1.67 起、高 5.00 → 压到 3.55
    if abs(top - 1.67) < 0.05 and abs(h - 5.00) < 0.05:
        sh.height = Inches(3.55)
    # 两个正文文本块：高 4.38 → 压到 2.95
    if abs(h - 4.38) < 0.05:
        sh.height = Inches(2.95)

# 底部补回总结要点
tb = s.shapes.add_textbox(Inches(0.62), Inches(5.62), Inches(12.10), Inches(0.62))
tf = tb.text_frame
tf.word_wrap = True
tf.text = '→  这个不对称是 Transformer 部署成本高的根源：训练吃算力，推理吃显存带宽和延迟。'
para = tf.paragraphs[0]
para.alignment = PP_ALIGN.LEFT
for r in para.runs:
    r.font.size = Pt(15)
    r.font.name = '微软雅黑'
    r.font.color.rgb = RGBColor(0x26, 0x33, 0x3E)
    r.font.bold = False

p.save(PATH)
print('第 19 页已修复')
