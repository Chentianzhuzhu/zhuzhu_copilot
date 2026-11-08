# -*- coding: utf-8 -*-
"""第24页要点再收紧：两条都留足余量，确保绝不折行"""
import zipfile
import os
import unicodedata
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.dml.color import RGBColor

PATH = r'transformer_ppt/Transformer架构详解.pptx'
EMU = 914400.0
FONT = '微软雅黑'
INK = RGBColor(0x26, 0x33, 0x3E)

LINES = [
    '→  可以一起深挖：FlashAttention 分块策略、RoPE 长度外推、MoE 稀疏激活。',
    '→  只能记一件事：注意力的价值不在名字，而在于它把序列建模变成完全可并行的矩阵运算。',
]


def em(txt, size):
    t = 0.0
    for ch in txt:
        if unicodedata.east_asian_width(ch) in ('W', 'F'):
            t += size * 1.0
        elif ch in ' \t':
            t += size * 0.30
        else:
            t += size * 0.55
    return t


p = Presentation(PATH)
s = p.slides[23]

target = None
for sh in s.shapes:
    if sh.has_text_frame and '只能记一件事' in sh.text_frame.text:
        target = sh
        break

if target is None:
    print('未找到目标文本框')
else:
    avail = (target.width / EMU) * 72 - 14.4
    chosen = 17.0
    for size in (17.0, 16.5, 16.0, 15.5, 15.0):
        if all(em(t, size) <= avail * 0.96 for t in LINES):
            chosen = size
            break
    for t in LINES:
        print(f'  {em(t, chosen):.0f}pt / 可用 {avail:.0f}pt  {t[:30]}')
    print(f'选定字号 {chosen}pt')

    tf = target.text_frame
    tf.text = LINES[0]
    p2 = tf.add_paragraph()
    p2.text = LINES[1]

    target.top = Inches(3.70)
    target.height = Inches(2.10)
    tf.word_wrap = True
    tf.vertical_anchor = MSO_ANCHOR.TOP
    for m in ('margin_top', 'margin_bottom', 'margin_left', 'margin_right'):
        setattr(tf, m, 0)

    for para in tf.paragraphs:
        para.alignment = PP_ALIGN.LEFT
        para.line_spacing = 1.5
        para.space_before = Pt(0)
        para.space_after = Pt(16)
        for r in para.runs:
            r.font.size = Pt(chosen)
            r.font.name = FONT
            r.font.color.rgb = INK

p.save(PATH)
print('保存完成')

TMP = PATH + '.tmp'
with zipfile.ZipFile(PATH) as zin:
    with zipfile.ZipFile(TMP, 'w', zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            zout.writestr(info.filename, zin.read(info.filename))
os.replace(TMP, PATH)
print(f'已重打包 {os.path.getsize(PATH)} bytes')
