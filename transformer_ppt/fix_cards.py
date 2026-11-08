# -*- coding: utf-8 -*-
"""统一修复卡片留白：按文字实际高度压缩卡片，自适应字号，底部要点跟随上移"""
import unicodedata
import zipfile
import os
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN

PATH = r'transformer_ppt/Transformer架构详解.pptx'
EMU = 914400.0
FONT = '微软雅黑'

# 页号 -> (标题字号上限, 正文字号上限, 卡片底 padding)
PLAN = {
    3:  (15.0, 14.0, 0.62),
    6:  (15.0, 14.0, 0.62),
    13: (15.5, 13.0, 0.60),
    18: (15.5, 15.0, 0.58),
}


def char_em(ch):
    if unicodedata.east_asian_width(ch) in ('W', 'F'):
        return 1.0
    if ch in ' \t':
        return 0.30
    return 0.55


def text_h(paragraphs, width_in, size_pt, spacing=1.42):
    """计算给定宽度/字号下文字所需高度（英寸）"""
    avail = width_in * 72 - 7.2
    total = 0.0
    for txt in paragraphs:
        if not txt.strip():
            continue
        em = sum(char_em(c) for c in txt) * size_pt
        lines = max(1, int(em / avail) + (1 if em % avail else 0))
        total += lines * size_pt * spacing / 72.0
    return total


p = Presentation(PATH)

for page, (title_cap, body_cap, pad) in PLAN.items():
    s = p.slides[page - 1]

    cards, texts, decos = [], [], []
    for sh in s.shapes:
        l, t = sh.left / EMU, sh.top / EMU
        w, h = sh.width / EMU, sh.height / EMU
        if str(sh.shape_type).startswith('AUTO_SHAPE'):
            if h > 3.0 and w > 2.0:
                cards.append((l, sh))
            elif 0.05 < h < 0.15 and 0.3 < w < 1.0:
                decos.append((l, sh))
        elif sh.has_text_frame and h > 2.0 and w > 2.0 and t > 1.5:
            texts.append((l, sh))

    if not cards or not texts:
        print(f'第{page}页：结构不符，跳过')
        continue

    cards.sort(key=lambda x: x[0])
    texts.sort(key=lambda x: x[0])

    card_top = cards[0][1].top / EMU
    needed_max = 0.0

    for i, (l, sh) in enumerate(texts):
        tf = sh.text_frame
        paras = [para.text for para in tf.paragraphs]
        if not paras:
            continue

        # 文字框宽度固定为 卡片宽 - 0.40
        card_w = None
        for cl, csh in cards:
            if abs(cl - l) < 0.35 or (cl <= l < cl + csh.width / EMU):
                card_w = csh.width / EMU
                break
        card_w = card_w or 3.89
        txt_w = card_w - 0.40

        title_txt = paras[0]
        body_paras = paras[1:] if len(paras) > 1 else []

        # 标题：优先用上限，若单行放不下则缩
        t_size = title_cap
        while t_size > 11 and text_h([title_txt], txt_w, t_size, 1.2) * 72 > t_size * 1.6:
            t_size -= 0.5

        # 正文：在不超过上限的前提下，选能让内容吃饱卡片的字号
        b_size = body_cap
        title_h = text_h([title_txt], txt_w, t_size, 1.2)
        body_h = text_h(body_paras, txt_w, b_size) if body_paras else 0.0
        total_h = title_h + 0.16 + body_h          # 0.16 = 标题与正文间距
        needed_max = max(needed_max, total_h)

        # 应用字号
        for j, para in enumerate(tf.paragraphs):
            para.alignment = PP_ALIGN.LEFT
            if j == 0:
                para.space_after = Pt(7)
                para.line_spacing = 1.2
                for r in para.runs:
                    r.font.size = Pt(t_size)
                    r.font.bold = True
                    r.font.name = FONT
            else:
                para.space_before = Pt(0)
                para.space_after = Pt(2)
                para.line_spacing = 1.42
                for r in para.runs:
                    r.font.size = Pt(b_size)
                    r.font.bold = False
                    r.font.name = FONT

        tf.word_wrap = True
        tf.vertical_anchor = MSO_ANCHOR.TOP
        tf.margin_left = 0
        tf.margin_right = 0
        tf.margin_top = 0
        tf.margin_bottom = 0

    new_h = round(needed_max + pad, 2)
    new_h = max(1.6, min(new_h, 4.40))
    print(f'第{page}页：内容高 {needed_max:.2f}in -> 卡片高 {new_h:.2f}in（原 4.40）')

    # 卡片压缩
    for cl, csh in cards:
        csh.top = Inches(card_top)
        csh.height = Inches(new_h)

    # 文字框跟随
    for l, sh in texts:
        sh.top = Inches(card_top + 0.44)
        sh.height = Inches(new_h - 0.62)

    # 装饰条位置（卡片内顶部）
    for l, sh in decos:
        sh.top = Inches(card_top + 0.24)

    # 底部要点上移，填补空间
    bottom_y = card_top + new_h + 0.58
    for sh in s.shapes:
        if not sh.has_text_frame:
            continue
        t = sh.top / EMU
        txt = sh.text_frame.text.strip()
        if t > card_top + new_h + 0.1 and txt and not txt.isdigit() and 'Transformer 架构详解' not in txt:
            if txt.startswith('→') or '→' in txt:
                sh.top = Inches(bottom_y)
                print(f'        底部要点移至 y={bottom_y:.2f}')

p.save(PATH)
print('\n保存完成')

# 重打包 ZIP（确保 PowerPoint 正常打开）
TMP = PATH + '.tmp'
with zipfile.ZipFile(PATH) as zin:
    with zipfile.ZipFile(TMP, 'w', zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            zout.writestr(info.filename, zin.read(info.filename))
os.replace(TMP, PATH)
print(f'已重打包 {os.path.getsize(PATH)} bytes')
