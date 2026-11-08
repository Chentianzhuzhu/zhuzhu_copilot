# -*- coding: utf-8 -*-
"""第 17 页精修：卡片高度收到贴合内容，文字垂直居中、悬停感更紧凑"""
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.enum.text import MSO_ANCHOR

PATH = r'transformer_ppt/Transformer架构详解.pptx'
p = Presentation(PATH)
s = p.slides[16]
EMU = 914400.0

CARD_TOP = 2.30
CARD_H = 1.95

for sh in s.shapes:
    top = sh.top / EMU
    w = sh.width / EMU
    h = sh.height / EMU
    if abs(w - 3.62) < 0.05 and abs(h - 2.55) < 0.05:        # 对比卡片
        sh.top = Inches(CARD_TOP)
        sh.height = Inches(CARD_H)
    elif abs(w - 3.02) < 0.05 and abs(h - 2.11) < 0.05:      # 卡片内文本
        sh.top = Inches(CARD_TOP + 0.16)
        sh.height = Inches(CARD_H - 0.32)
        sh.text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
    elif abs(w - 0.62) < 0.05 and abs(h - 0.62) < 0.05:      # VS 圆
        sh.top = Inches(CARD_TOP + (CARD_H - 0.62) / 2)
    elif abs(w - 0.50) < 0.05 and abs(h - 0.40) < 0.05:      # VS 文字
        sh.top = Inches(CARD_TOP + (CARD_H - 0.40) / 2)

# 底部要点随之上移，收紧版面
for sh in s.shapes:
    if sh.has_text_frame and '一句话区分' in sh.text_frame.text:
        sh.top = Inches(4.72)
        sh.height = Inches(1.75)

p.save(PATH)
print('第 17 页已精修')
