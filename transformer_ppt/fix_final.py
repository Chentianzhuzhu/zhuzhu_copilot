# -*- coding: utf-8 -*-
"""收尾修正：第18页要点上移防压页脚 + 第3/13页断行标点瑕疵"""
import zipfile
import os
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN

PATH = r'transformer_ppt/Transformer架构详解.pptx'
EMU = 914400.0
FONT = '微软雅黑'

p = Presentation(PATH)

# ---------- 第 18 页：卡片上移 + 要点上移缩字号 ----------
s = p.slides[17]
cards, texts = [], []
for sh in s.shapes:
    l, t = sh.left / EMU, sh.top / EMU
    w, h = sh.width / EMU, sh.height / EMU
    if str(sh.shape_type).startswith('AUTO_SHAPE') and w > 2.0 and 1.5 < h < 3.5:
        cards.append((l, sh))
    elif 0.05 < h < 0.15 and 0.3 < w < 1.0:
        pass
    elif sh.has_text_frame and w > 2.0 and h > 0.8 and t > 1.5 and '→' not in sh.text_frame.text:
        texts.append((l, sh))

CARD_TOP, CARD_H = 1.88, 2.55
for l, sh in cards:
    sh.top = Inches(CARD_TOP)
    sh.height = Inches(CARD_H)
for l, sh in texts:
    sh.top = Inches(CARD_TOP + 0.10)
    sh.height = Inches(CARD_H - 0.20)

# 装饰条
for sh in s.shapes:
    h = sh.height / EMU
    w = sh.width / EMU
    if str(sh.shape_type).startswith('AUTO_SHAPE') and 0.05 < h < 0.15 and 0.3 < w < 1.0:
        sh.top = Inches(CARD_TOP + 0.26)

NOTE_Y = 4.90
for sh in s.shapes:
    if not sh.has_text_frame:
        continue
    txt = sh.text_frame.text.strip()
    if '→' in txt and sh.top / EMU > 1.5:
        sh.top = Inches(NOTE_Y)
        sh.height = Inches(1.85)
        tf = sh.text_frame
        tf.word_wrap = True
        tf.vertical_anchor = MSO_ANCHOR.TOP
        for para in tf.paragraphs:
            para.line_spacing = 1.42
            para.space_after = Pt(5)
            para.alignment = PP_ALIGN.LEFT
            for r in para.runs:
                r.font.size = Pt(14.5)
                r.font.name = FONT
print(f'第18页：卡片 y={CARD_TOP} h={CARD_H}，要点 y={NOTE_Y} 14.5pt（防压页脚）')

# ---------- 第 3 页：消除「t 时刻」被拆分 ----------
s3 = p.slides[2]
FIX3 = '隐状态必须一个词一个词往后传，当前时刻算不完，下一秒就无法推进。GPU 再强也只能排队等。'
for sh in s3.shapes:
    if not sh.has_text_frame:
        continue
    if '隐状态必须' in sh.text_frame.text:
        for para in sh.text_frame.paragraphs:
            for r in para.runs:
                r.text = ''
            # 把正文段替换（保留标题段）
        paras = sh.text_frame.paragraphs
        # 第 1 段是标题「硬伤一 · 串行」，第 2 段是正文
        for j, para in enumerate(paras):
            if j == 1:
                for r in para.runs:
                    r.text = ''
                if para.runs:
                    para.runs[0].text = FIX3
        print('第3页：已改写卡1正文，避免英文词拆行')

# ---------- 第 13 页：消除行首逗号 ----------
s13 = p.slides[12]
FIX13 = {
    'h 个头同时运算': 'h 个头同时运算互不干扰，一次矩阵乘法就能算完，非常适合 GPU。',
    '把 h 个头的输出拼接': '把 h 个头的输出拼接回 d_model 维，再乘输出矩阵 W_O 做融合。',
}
for sh in s13.shapes:
    if not sh.has_text_frame:
        continue
    t = sh.text_frame.text
    for key, new in FIX13.items():
        if key in t:
            paras = sh.text_frame.paragraphs
            for j, para in enumerate(paras):
                if j == 1:
                    for r in para.runs:
                        r.text = ''
                    if para.runs:
                        para.runs[0].text = new
            print(f'第13页：改写「{key}...」正文，消除行首逗号')

p.save(PATH)
print('保存完成')

TMP = PATH + '.tmp'
with zipfile.ZipFile(PATH) as zin:
    with zipfile.ZipFile(TMP, 'w', zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            zout.writestr(info.filename, zin.read(info.filename))
os.replace(TMP, PATH)
print(f'已重打包 {os.path.getsize(PATH)} bytes')
