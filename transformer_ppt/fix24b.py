# -*- coding: utf-8 -*-
"""修复第24页第一条要点的孤行标点：缩短文案 + 字号微调，确保一行放下"""
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

NEW_FIRST = '→  可以一起深挖：FlashAttention 分块策略、RoPE 长度外推原理、MoE 稀疏激活如何放大参数量。'
KEEP_SECOND = '→  如果只能记一件事：注意力的意义不在于「注意力」这个词，而在于它把序列建模变成了一个可完全并行的矩阵运算。'


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
    if sh.has_text_frame and '如果只能记一件事' in sh.text_frame.text:
        target = sh
        break

if target is None:
    print('未找到目标文本框')
else:
    w_in = target.width / EMU
    avail = w_in * 72 - 14.4
    print(f'文本框宽 {w_in:.2f}in，可用 {avail:.0f}pt')

    # 选字号：两条都要一行放得下
    chosen = 17.0
    for size in (17.0, 16.5, 16.0, 15.5, 15.0, 14.5):
        if em(NEW_FIRST, size) <= avail and em(KEEP_SECOND, size) <= avail:
            chosen = size
            break
    print(f'选定字号 {chosen}pt  第一段={em(NEW_FIRST, chosen):.0f}pt  第二段={em(KEEP_SECOND, chosen):.0f}pt')

    tf = target.text_frame
    tf.text = NEW_FIRST
    p2 = tf.add_paragraph()
    p2.text = KEEP_SECOND

    target.top = Inches(3.60)
    target.height = Inches(2.20)
    tf.word_wrap = True
    tf.vertical_anchor = MSO_ANCHOR.TOP
    tf.margin_top = 0
    tf.margin_bottom = 0
    tf.margin_left = 0
    tf.margin_right = 0

    for para in tf.paragraphs:
        para.alignment = PP_ALIGN.LEFT
        para.line_spacing = 1.5
        para.space_before = Pt(0)
        para.space_after = Pt(14)
        for r in para.runs:
            r.font.size = Pt(chosen)
            r.font.name = FONT
            r.font.color.rgb = INK
    print('第24页要点已重写')

p.save(PATH)
print('保存完成')

TMP = PATH + '.tmp'
with zipfile.ZipFile(PATH) as zin:
    with zipfile.ZipFile(TMP, 'w', zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            zout.writestr(info.filename, zin.read(info.filename))
os.replace(TMP, PATH)
print(f'已重打包 {os.path.getsize(PATH)} bytes')
