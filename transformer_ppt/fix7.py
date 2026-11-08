# -*- coding: utf-8 -*-
"""修正第 7 页行首标点问题：改写文案避开断行边界 + 字号微调 + 段落级悬挂标点"""
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN

PATH = r'transformer_ppt/Transformer架构详解.pptx'
EMU = 914400.0

NEW_TEXT = [
    '→  自注意力对所有位置加权求和，打乱词序结果不变——所以必须额外注入位置信息，否则「狗咬人」和「人咬狗」毫无区别。',
    '→  原论文用正弦余弦函数，不同频率的正弦波为每个位置生成独一无二的「指纹」，可外推至训练时未见过的长度。',
    '→  现代模型更多用可学习位置编码或 RoPE（旋转位置编码），靠旋转向量夹角表达相对位置，是当前主流。',
]

p = Presentation(PATH)
s = p.slides[6]  # 第 7 页

target = None
for sh in s.shapes:
    if sh.has_text_frame and '→' in sh.text_frame.text and sh.top / EMU > 1.4:
        if target is None or sh.width > target.width:
            target = sh

target.left = Inches(0.62)
target.top = Inches(1.55)
target.width = Inches(12.10)
target.height = Inches(5.32)
tf = target.text_frame
tf.word_wrap = True
tf.vertical_anchor = MSO_ANCHOR.MIDDLE
tf.margin_top = 0
tf.margin_bottom = 0

# 重写为三段
tf.text = NEW_TEXT[0]
for extra in NEW_TEXT[1:]:
    para = tf.add_paragraph()
    para.text = extra

for i, para in enumerate(tf.paragraphs):
    para.line_spacing = 1.8
    para.space_before = Pt(0)
    para.space_after = Pt(20) if i < len(tf.paragraphs) - 1 else Pt(0)
    para.alignment = PP_ALIGN.LEFT
    for r in para.runs:
        r.font.size = Pt(21)
        r.font.name = '微软雅黑'
    # 悬挂标点：允许标点在极端情况下悬挂而非落到行首
    pPr = para._p.get_or_add_pPr()
    pPr.set('hangingPunct', '1')

p.save(PATH)
print('第 7 页文案已重写，21pt / 行距1.8 / 悬挂标点开启')
