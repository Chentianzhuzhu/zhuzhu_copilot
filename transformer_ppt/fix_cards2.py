# -*- coding: utf-8 -*-
"""卡片页二次修正：设定合理最小高度 + 放大字号 + 内容垂直居中 + 整块页面居中"""
import zipfile
import os
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN

PATH = r'transformer_ppt/Transformer架构详解.pptx'
EMU = 914400.0
FONT = '微软雅黑'

# 页号 -> (目标卡片高度, 标题字号, 正文字号)
PLAN = {
    3:  (3.05, 16.0, 15.0),
    6:  (3.05, 16.0, 15.0),
    13: (2.95, 16.5, 14.5),
    18: (2.55, 16.5, 16.0),
}

CONTENT_TOP = 1.55     # 标题分隔线下方
CONTENT_BOTTOM = 6.92  # 页脚上方
NOTE_H = 0.46          # 底部要点高度
GAP = 0.52             # 卡片与要点间距

p = Presentation(PATH)

for page, (target_h, title_size, body_size) in PLAN.items():
    s = p.slides[page - 1]

    cards, texts, decos = [], [], []
    for sh in s.shapes:
        l, t = sh.left / EMU, sh.top / EMU
        w, h = sh.width / EMU, sh.height / EMU
        if str(sh.shape_type).startswith('AUTO_SHAPE'):
            if w > 2.0 and 1.5 < h < 3.5:
                cards.append((l, sh))
            elif 0.05 < h < 0.15 and 0.3 < w < 1.0:
                decos.append((l, sh))
        elif sh.has_text_frame and w > 2.0 and h > 0.8 and t > 1.5:
            if '→' not in sh.text_frame.text:
                texts.append((l, sh))

    if not cards or not texts:
        print(f'第{page}页：结构不符，跳过')
        continue

    cards.sort(key=lambda x: x[0])
    texts.sort(key=lambda x: x[0])

    # 整块垂直居中
    block_h = target_h + GAP + NOTE_H
    card_top = CONTENT_TOP + (CONTENT_BOTTOM - CONTENT_TOP - block_h) / 2
    card_top = round(card_top, 2)

    for cl, csh in cards:
        csh.top = Inches(card_top)
        csh.height = Inches(target_h)

    for l, sh in texts:
        sh.top = Inches(card_top + 0.10)
        sh.height = Inches(target_h - 0.20)
        tf = sh.text_frame
        tf.word_wrap = True
        tf.vertical_anchor = MSO_ANCHOR.MIDDLE
        tf.margin_left = 0
        tf.margin_right = 0
        tf.margin_top = 0
        tf.margin_bottom = 0
        for j, para in enumerate(tf.paragraphs):
            para.alignment = PP_ALIGN.LEFT
            is_title = (j == 0)
            para.line_spacing = 1.3 if is_title else 1.55
            para.space_after = Pt(9 if is_title else 4)
            para.space_before = Pt(0)
            for r in para.runs:
                r.font.size = Pt(title_size if is_title else body_size)
                r.font.bold = is_title
                r.font.name = FONT

    for l, sh in decos:
        sh.top = Inches(card_top + 0.26)

    note_y = round(card_top + target_h + GAP, 2)
    for sh in s.shapes:
        if not sh.has_text_frame:
            continue
        txt = sh.text_frame.text.strip()
        t = sh.top / EMU
        if '→' in txt and t > card_top + 0.2:
            sh.top = Inches(note_y)
            sh.height = Inches(NOTE_H)
            for para in sh.text_frame.paragraphs:
                for r in para.runs:
                    r.font.size = Pt(15.5)
            print(f'第{page}页：卡片 y={card_top:.2f} h={target_h} 要点 y={note_y:.2f} '
                  f'（标题 {title_size}pt / 正文 {body_size}pt）')

p.save(PATH)
print('\n保存完成')

TMP = PATH + '.tmp'
with zipfile.ZipFile(PATH) as zin:
    with zipfile.ZipFile(TMP, 'w', zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            zout.writestr(info.filename, zin.read(info.filename))
os.replace(TMP, PATH)
print(f'已重打包 {os.path.getsize(PATH)} bytes')
