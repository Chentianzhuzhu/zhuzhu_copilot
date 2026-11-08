# -*- coding: utf-8 -*-
"""修复流程图卡片文字溢出：按可用宽度精确计算字号 + 拆分过长文案"""
import unicodedata
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN

PATH = r'transformer_ppt/Transformer架构详解.pptx'
EMU = 914400.0
FONT = '微软雅黑'

# 卡片正文改写（拆短，保证一行放得下）
REWRITE = {
    4: {
        0: ['输入序列', '词元 + 位置'],
        1: ['Encoder × N', '自注意力 + FFN'],
        2: ['上下文表示', '融合全句信息'],
        3: ['Decoder × N', '掩码自注意力', '交叉注意力'],
        4: ['线性层 + Softmax', '输出词概率'],
    },
    14: {
        0: ['输入 x', ''],
        1: ['子层', '注意力 / FFN'],
        2: ['与 x 相加', '（残差）'],
        3: ['LayerNorm', ''],
        4: ['输出', '给下一层'],
    },
}


def em_width(txt, size):
    t = 0.0
    for ch in txt:
        if unicodedata.east_asian_width(ch) in ('W', 'F'):
            t += 1.0
        elif ch in ' \t':
            t += 0.3
        else:
            t += 0.55
    return t * size


def fit_size(txt, avail_pt, cap):
    """求能放下的最大字号（不超过 cap）"""
    if not txt.strip():
        return cap
    per_em = em_width(txt, 1.0)
    if per_em <= 0:
        return cap
    s = avail_pt / per_em
    return max(9.0, min(cap, round(s * 2) / 2 - 0.5))


p = Presentation(PATH)

for page, mapping in REWRITE.items():
    s = p.slides[page - 1]

    cards = []
    inner = []
    for sh in s.shapes:
        t, l = sh.top / EMU, sh.left / EMU
        w, h = sh.width / EMU, sh.height / EMU
        if 1.6 < t < 2.1 and w > 1.0 and h > 1.5:
            cards.append((l, w, sh))
        elif sh.has_text_frame and 1.9 < t < 2.7 and w < 2.5 and sh.text_frame.text.strip():
            inner.append((l, w, sh))

    cards.sort(key=lambda x: x[0])
    inner.sort(key=lambda x: x[0])

    for i, (l, w, sh) in enumerate(inner):
        if i not in mapping:
            continue
        lines = [x for x in mapping[i] if x]
        if not lines:
            continue

        # 卡片内可用宽度
        avail = (w - 0.18) * 72 - 7.2

        tf = sh.text_frame
        tf.word_wrap = True
        tf.vertical_anchor = MSO_ANCHOR.MIDDLE
        tf.margin_top = 0
        tf.margin_bottom = 0
        tf.margin_left = 0
        tf.margin_right = 0

        # 重写段落
        tf.text = lines[0]
        for extra in lines[1:]:
            para = tf.add_paragraph()
            para.text = extra

        # 逐段定字号：标题 13.5pt 上限，正文按宽度自适应
        for j, para in enumerate(tf.paragraphs):
            txt = para.text
            cap = 13.5 if j == 0 else 12.0
            size = fit_size(txt, avail, cap)
            para.alignment = PP_ALIGN.CENTER
            para.line_spacing = 1.3
            para.space_before = Pt(2)
            para.space_after = Pt(2)
            for r in para.runs:
                r.font.size = Pt(size)
                r.font.name = FONT

        # 文本框严格限制在卡片内
        sh.width = Inches(w - 0.16)

    print(f'第{page}页：{len(inner)} 个卡片文字已按宽度自适应缩小并拆分')

p.save(PATH)
print('保存完成')
