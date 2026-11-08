# -*- coding: utf-8 -*-
"""修正第 23 页要点溢出：上移文本框 + 缩小字号 + 收紧行距"""
from pptx import Presentation
from pptx.util import Inches, Pt

PATH = r'transformer_ppt/Transformer架构详解.pptx'
p = Presentation(PATH)
slide = p.slides[22]  # 第 23 页

fixed = False
for sh in slide.shapes:
    if sh.has_text_frame and 'Transformer = 注意力' in sh.text_frame.text:
        sh.top = Inches(5.58)
        sh.height = Inches(1.75)
        for para in sh.text_frame.paragraphs:
            para.line_spacing = 1.15
            para.space_after = Pt(4)
            para.space_before = Pt(0)
            for r in para.runs:
                r.font.size = Pt(12)
        fixed = True
        print('已修正第 23 页要点文本框')

p.save(PATH)
print('保存完成' if fixed else '未找到目标文本框')
