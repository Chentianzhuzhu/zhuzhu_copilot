# -*- coding: utf-8 -*-
"""修复第 17 页 compare 版式：压缩过高的对比卡片，底部补两条要点"""
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN

PATH = r'transformer_ppt/Transformer架构详解.pptx'
p = Presentation(PATH)
s = p.slides[16]
EMU = 914400.0

CARD_H = 2.55
CARD_TOP = 2.30

for sh in s.shapes:
    top = sh.top / EMU
    w = sh.width / EMU
    h = sh.height / EMU
    # 两个对比卡片：3.62x4.10
    if abs(w - 3.62) < 0.05 and abs(h - 4.10) < 0.05:
        sh.top = Inches(CARD_TOP)
        sh.height = Inches(CARD_H)
    # 卡片内文本块：3.02x3.40
    elif abs(w - 3.02) < 0.05 and abs(h - 3.40) < 0.05:
        sh.top = Inches(CARD_TOP + 0.22)
        sh.height = Inches(CARD_H - 0.44)
    # VS 圆 0.62x0.62 —— 垂直居中到卡片
    elif abs(w - 0.62) < 0.05 and abs(h - 0.62) < 0.05:
        sh.top = Inches(CARD_TOP + (CARD_H - 0.62) / 2)
    # VS 文字
    elif abs(w - 0.50) < 0.05 and abs(h - 0.40) < 0.05:
        sh.top = Inches(CARD_TOP + (CARD_H - 0.40) / 2)

# 底部补充要点
notes = [
    '→  一句话区分：Encoder 是「把整句压缩成可查询的表示」，Decoder 是「拿着这份表示，一个词一个词往后写」。',
    '→  因果掩码是解码器的灵魂：它把「生成」这件事约束成严格的时间顺序，也正因如此训练才能并行、推理只能串行。',
]
tb = s.shapes.add_textbox(Inches(0.62), Inches(5.20), Inches(12.10), Inches(1.55))
tf = tb.text_frame
tf.word_wrap = True
tf.text = notes[0]
tf.paragraphs[0].alignment = PP_ALIGN.LEFT
for r in tf.paragraphs[0].runs:
    r.font.size = Pt(14.5)
    r.font.name = '微软雅黑'
    r.font.color.rgb = RGBColor(0x26, 0x33, 0x3E)

p2 = tf.add_paragraph()
p2.text = notes[1]
p2.alignment = PP_ALIGN.LEFT
p2.space_before = Pt(8)
for r in p2.runs:
    r.font.size = Pt(14.5)
    r.font.name = '微软雅黑'
    r.font.color.rgb = RGBColor(0x26, 0x33, 0x3E)

p.save(PATH)
print('第 17 页版式已修复')
