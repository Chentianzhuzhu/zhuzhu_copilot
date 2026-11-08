# -*- coding: utf-8 -*-
"""把第 3/6 页卡片正文改写为短句分段，彻底消除行首标点（每段一行放得下）"""
import zipfile
import os
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN

PATH = r'transformer_ppt/Transformer架构详解.pptx'
EMU = 914400.0
FONT = '微软雅黑'

# 卡片文字框 x -> 改写后的分段列表（第一段为标题，其余为正文短句）
REWRITE = {
    3: {
        0.82: ['硬伤一 · 串行',
               '隐状态必须一个词一个词往后传',
               '当前词算不完，下一个词就动不了',
               'GPU 再强也只能排队等'],
        4.93: ['硬伤二 · 长依赖衰减',
               '相隔 500 步的两个词',
               '信息要穿过 500 次非线性变换',
               '梯度容易消失，前后文对不上'],
        9.03: ['硬伤三 · 难以扩展',
               '想加大模型只能加宽加深',
               '串行瓶颈依然不变',
               '训练成本随长度线性上涨'],
    },
    6: {
        0.82: ['Tokenization',
               '按子词切分，如 BPE 或 SentencePiece',
               '平衡词表大小与覆盖率',
               '中文一到两字常为一个 token'],
        4.93: ['Embedding',
               '查表得到 d_model 维稠密向量',
               '语义相近的词在空间中更近'],
        9.03: ['Scaling',
               '乘上 √d_model 匹配数值量级',
               '避免相加后被位置编码淹没'],
    },
}

SIZES = {3: (16.0, 15.0), 6: (16.0, 15.0)}

p = Presentation(PATH)

for page, mapping in REWRITE.items():
    s = p.slides[page - 1]
    t_size, b_size = SIZES[page]

    for sh in s.shapes:
        if not sh.has_text_frame:
            continue
        l = round(sh.left / EMU, 2)
        key = None
        for k in mapping:
            if abs(l - k) < 0.12:
                key = k
                break
        if key is None:
            continue

        lines = mapping[key]
        tf = sh.text_frame
        tf.word_wrap = True
        tf.vertical_anchor = MSO_ANCHOR.MIDDLE
        tf.margin_left = 0
        tf.margin_right = 0
        tf.margin_top = 0
        tf.margin_bottom = 0

        # 清空后逐段写入
        tf.text = lines[0]
        for extra in lines[1:]:
            para = tf.add_paragraph()
            para.text = extra

        for j, para in enumerate(tf.paragraphs):
            is_title = (j == 0)
            para.alignment = PP_ALIGN.LEFT
            para.line_spacing = 1.28 if is_title else 1.45
            para.space_after = Pt(9 if is_title else 5)
            para.space_before = Pt(0)
            for r in para.runs:
                r.font.size = Pt(t_size if is_title else b_size)
                r.font.bold = is_title
                r.font.name = FONT

    print(f'第{page}页：{len(mapping)} 张卡片正文已改为短句分段')

p.save(PATH)
print('保存完成')

TMP = PATH + '.tmp'
with zipfile.ZipFile(PATH) as zin:
    with zipfile.ZipFile(TMP, 'w', zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            zout.writestr(info.filename, zin.read(info.filename))
os.replace(TMP, PATH)
print(f'已重打包 {os.path.getsize(PATH)} bytes')
