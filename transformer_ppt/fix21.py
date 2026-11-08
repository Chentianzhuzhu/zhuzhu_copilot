# -*- coding: utf-8 -*-
"""修复第21页：压缩卡片高度、标题/正文分级字号、按短句分段彻底消除行首标点"""
import zipfile
import os
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.dml.color import RGBColor

PATH = r'transformer_ppt/Transformer架构详解.pptx'
EMU = 914400.0
FONT = '微软雅黑'

# 每段控制在一行内（12pt 时约 14 字），杜绝行首标点
CONTENT = {
    0: ('Encoder-only', ['代表 BERT', '只保留编码器', '双向理解全句', '擅长分类、抽取', '检索与句向量']),
    1: ('Decoder-only', ['代表 GPT、Llama、Qwen', '只保留解码器', '因果生成', '当前大模型主流形态']),
    2: ('Encoder-Decoder', ['代表 T5、BART', '擅长翻译、摘要', '结构化生成', '输入输出都长的任务']),
    3: ('跨模态扩展', ['代表 ViT、CLIP', '把图像切成 patch', '当作 token 处理', '架构照搬不改', '视觉领域同样通吃']),
}

CARD_TOP = 1.62
CARD_H = 3.50
PAD = 0.20

p = Presentation(PATH)
s = p.slides[20]

# 1) 收集卡片与内部文字框
cards, texts, decos = [], [], []
for sh in s.shapes:
    t, l = sh.top / EMU, sh.left / EMU
    w, h = sh.width / EMU, sh.height / EMU
    if 'AUTO_SHAPE' in str(sh.shape_type):
        if abs(h - 4.40) < 0.05:
            cards.append((l, sh))
        elif abs(h - 0.09) < 0.03:
            decos.append((l, sh))
    elif sh.has_text_frame and abs(h - 3.70) < 0.05:
        texts.append((l, sh))

cards.sort(key=lambda x: x[0])
texts.sort(key=lambda x: x[0])
decos.sort(key=lambda x: x[0])

print(f'卡片 {len(cards)} 个，文字框 {len(texts)} 个，装饰条 {len(decos)} 个')

# 2) 卡片压缩高度
for l, sh in cards:
    sh.top = Inches(CARD_TOP)
    sh.height = Inches(CARD_H)

# 3) 装饰条跟随
for l, sh in decos:
    sh.top = Inches(CARD_TOP + 0.24)

# 4) 文字重写
for i, (l, sh) in enumerate(texts):
    if i not in CONTENT:
        continue
    title, lines = CONTENT[i]
    card_w = 2.86

    sh.left = Inches(l + 0.06)
    sh.top = Inches(CARD_TOP + 0.46)
    sh.width = Inches(card_w - 0.20)
    sh.height = Inches(CARD_H - PAD * 2 - 0.50)

    tf = sh.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = MSO_ANCHOR.TOP
    tf.margin_left = 0
    tf.margin_right = 0
    tf.margin_top = 0
    tf.margin_bottom = 0

    # 标题
    tf.text = title
    p0 = tf.paragraphs[0]
    p0.alignment = PP_ALIGN.LEFT
    p0.space_after = Pt(10)
    p0.line_spacing = 1.15
    for r in p0.runs:
        r.font.size = Pt(15)
        r.font.bold = True
        r.font.name = FONT

    # 正文（短句分段）
    for line in lines:
        para = tf.add_paragraph()
        para.text = line
        para.alignment = PP_ALIGN.LEFT
        para.space_before = Pt(0)
        para.space_after = Pt(3)
        para.line_spacing = 1.35
        for r in para.runs:
            r.font.size = Pt(12)
            r.font.bold = False
            r.font.name = FONT

# 5) 底部要点随卡片下移对齐
for sh in s.shapes:
    if sh.has_text_frame and '架构没变' in sh.text_frame.text:
        sh.top = Inches(5.55)
        sh.height = Inches(0.42)
        for para in sh.text_frame.paragraphs:
            for r in para.runs:
                r.font.size = Pt(15)

p.save(PATH)
print('第21页已修复：卡片 3.50in 高、标题 15pt、正文 12pt 短句分段')

# 6) 重打包 ZIP（确保 PowerPoint 可正常打开）
TMP = PATH + '.tmp'
with zipfile.ZipFile(PATH) as zin:
    with zipfile.ZipFile(TMP, 'w', zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            zout.writestr(info.filename, zin.read(info.filename))
os.replace(TMP, PATH)
print(f'已重打包，大小 {os.path.getsize(PATH)} bytes')
