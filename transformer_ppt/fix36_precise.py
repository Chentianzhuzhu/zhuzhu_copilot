# -*- coding: utf-8 -*-
"""精确修复：恢复被误改的标题框、清空装饰色条文字、优化第6页卡片文案避免折行"""
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

# 标题原文
TITLES = {
    3: '前 Transformer 时代：RNN 的三个硬伤',
    6: '第一步：把词变成向量',
}

# 卡片正文（每段确保一行放得下：3.49in ≈ 237pt，15pt 中文约 15.7 字）
CARDS = {
    3: {
        0.82: ['硬伤一 · 串行',
               '隐状态必须一词一词往后传',
               '当前词算不完，下一个就卡住',
               'GPU 再强也只能排队等'],
        4.93: ['硬伤二 · 长依赖衰减',
               '相隔 500 步的两个词',
               '信息要穿过 500 次变换',
               '梯度易消失，前后文对不上'],
        9.03: ['硬伤三 · 难以扩展',
               '想加大模型只能加宽加深',
               '串行瓶颈依然不变',
               '训练成本随长度线性上涨'],
    },
    6: {
        0.82: ['Tokenization',
               '按子词切分（BPE 等）',
               '平衡词表大小与覆盖率',
               '中文一到两字常为一个 token'],
        4.93: ['Embedding',
               '查表得到 d_model 维向量',
               '语义相近的词在空间中更近'],
        9.03: ['Scaling',
               '乘上 √d_model 匹配量级',
               '避免相加后被位置编码淹没'],
    },
}

p = Presentation(PATH)

for page in (3, 6):
    s = p.slides[page - 1]

    # 1) 恢复标题框（x≈0.92, y≈0.50, w≈11.00）
    for sh in s.shapes:
        l, t = sh.left / EMU, sh.top / EMU
        w, h = sh.width / EMU, sh.height / EMU
        if sh.has_text_frame and abs(l - 0.92) < 0.05 and t < 0.8 and w > 10:
            tf = sh.text_frame
            tf.text = TITLES[page]
            para = tf.paragraphs[0]
            para.alignment = PP_ALIGN.LEFT
            for r in para.runs:
                r.font.size = Pt(25)
                r.font.bold = True
                r.font.name = FONT
                r.font.color.rgb = INK
            print(f'第{page}页：标题已恢复「{TITLES[page]}」')

    # 2) 清空装饰色条文字（宽 0.62 的细条）
    cleared = 0
    for sh in s.shapes:
        w, h = sh.width / EMU, sh.height / EMU
        if str(sh.shape_type).startswith('AUTO_SHAPE') and abs(w - 0.62) < 0.05 and abs(h - 0.09) < 0.03:
            if sh.has_text_frame and sh.text_frame.text.strip():
                sh.text_frame.text = ''
                cleared += 1
    if cleared:
        print(f'第{page}页：清空 {cleared} 个装饰色条上的残留文字')

    # 3) 重设卡片正文（x≈0.82/4.93/9.03, y>2.0）
    for sh in s.shapes:
        if not sh.has_text_frame:
            continue
        l, t = round(sh.left / EMU, 2), sh.top / EMU
        if t < 2.0 or t > 5.0:
            continue
        key = None
        for k in CARDS[page]:
            if abs(l - k) < 0.06:
                key = k
                break
        if key is None:
            continue

        lines = CARDS[page][key]
        tf = sh.text_frame
        tf.word_wrap = True
        tf.vertical_anchor = MSO_ANCHOR.MIDDLE
        tf.margin_left = 0
        tf.margin_right = 0
        tf.margin_top = 0
        tf.margin_bottom = 0

        tf.text = lines[0]
        for extra in lines[1:]:
            para = tf.add_paragraph()
            para.text = extra

        for j, para in enumerate(tf.paragraphs):
            is_title = (j == 0)
            para.alignment = PP_ALIGN.LEFT
            para.line_spacing = 1.30 if is_title else 1.55
            para.space_after = Pt(10 if is_title else 6)
            para.space_before = Pt(0)
            for r in para.runs:
                r.font.size = Pt(16 if is_title else 15)
                r.font.bold = is_title
                r.font.name = FONT

p.save(PATH)
print('\n保存完成')

TMP = PATH + '.tmp'
with zipfile.ZipFile(PATH) as zin:
    with zipfile.ZipFile(TMP, 'w', zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            zout.writestr(info.filename, zin.read(info.filename))
os.replace(TMP, PATH)
print(f'已重打包 {os.path.getsize(PATH)} bytes')
