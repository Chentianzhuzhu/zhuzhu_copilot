# -*- coding: utf-8 -*-
"""补回第24页丢失的第一条要点，并调整版面"""
import zipfile
import os
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.dml.color import RGBColor

PATH = r'transformer_ppt/Transformer架构详解.pptx'
EMU = 914400.0
FONT = '微软雅黑'
INK = RGBColor(0x26, 0x33, 0x3E)

FIRST = '→  可以一起深挖的方向：FlashAttention 的分块策略、RoPE 的长度外推原理、MoE 如何用稀疏激活放大参数量。'

p = Presentation(PATH)
s = p.slides[23]

target = None
for sh in s.shapes:
    if sh.has_text_frame and '如果只能记一件事' in sh.text_frame.text:
        target = sh
        break

if target is None:
    print('未找到目标文本框')
else:
    tf = target.text_frame
    second = tf.paragraphs[0].text          # 保留原第二段内容
    tf.text = FIRST                          # 写入第一段
    p2 = tf.add_paragraph()
    p2.text = second                         # 还原第二段

    target.top = Inches(3.60)
    target.height = Inches(2.30)
    tf.word_wrap = True
    tf.vertical_anchor = MSO_ANCHOR.TOP
    tf.margin_top = 0
    tf.margin_bottom = 0

    for i, para in enumerate(tf.paragraphs):
        para.alignment = PP_ALIGN.LEFT
        para.line_spacing = 1.55
        para.space_before = Pt(0)
        para.space_after = Pt(12)
        for r in para.runs:
            r.font.size = Pt(17)
            r.font.name = FONT
            r.font.color.rgb = INK

    print(f'已补回第一条要点，当前段数 = {len([x for x in tf.paragraphs if x.text.strip()])}')

p.save(PATH)
print('保存完成')

TMP = PATH + '.tmp'
with zipfile.ZipFile(PATH) as zin:
    with zipfile.ZipFile(TMP, 'w', zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            zout.writestr(info.filename, zin.read(info.filename))
os.replace(TMP, PATH)
print(f'已重打包 {os.path.getsize(PATH)} bytes')
