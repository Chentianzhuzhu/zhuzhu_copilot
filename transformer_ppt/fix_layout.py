# -*- coding: utf-8 -*-
"""统一修复版面留白：流程图撑满全宽、表格放大、图文页放大配图"""
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN

PATH = r'transformer_ppt/Transformer架构详解.pptx'
EMU = 914400.0
FONT = '微软雅黑'
INK = RGBColor(0x26, 0x33, 0x3E)

p = Presentation(PATH)

# ============ 通用：把流程图 + 右栏要点重排为「全宽流程 + 下方全宽要点」 ============
EXTRA = {
    4: ['在代码实现里，Encoder Block 与 Decoder Block 共用同一套模板，区别只在于解码器多了一个交叉注意力子层。'],
    10: ['工程实现上，所有头的 Q/K/V 可以由一次大矩阵乘法同时算出，所以多头并不会显著增加耗时。'],
    14: ['Pre-LN 与 Post-LN 的选择直接决定训练稳定性：当代大模型几乎清一色改用 Pre-LN。'],
}


def refit_flow(slide, page_no):
    boxes, arrows, inner, bullet_tb = [], [], [], []
    for sh in slide.shapes:
        st = str(sh.shape_type)
        t, l = sh.top / EMU, sh.left / EMU
        w, h = sh.width / EMU, sh.height / EMU
        if 'AUTO_SHAPE' in st:
            if abs(h - 1.15) < 0.12 and w > 1.0:
                boxes.append(sh)
            elif abs(h - 0.42) < 0.12 and w < 0.6:
                arrows.append(sh)
        elif sh.has_text_frame and sh.text_frame.text.strip():
            if h < 0.75 and 1.9 < t < 2.7 and w < 2.2:
                inner.append(sh)
            elif l > 9.0 and w > 3.0:
                bullet_tb.append(sh)

    if not boxes or not bullet_tb:
        print(f'第{page_no}页：结构不符，跳过')
        return

    boxes.sort(key=lambda s: s.left)
    arrows.sort(key=lambda s: s.left)
    inner.sort(key=lambda s: s.left)
    n = len(boxes)

    TOTAL, AW = 12.10, 0.42
    bw = (TOTAL - (n - 1) * AW) / n
    Y0, BH = 1.72, 2.00

    xs = []
    x = 0.62
    for i in range(n):
        xs.append(x)
        x += bw + (AW if i < n - 1 else 0)

    for i, b in enumerate(boxes):
        b.left, b.top = Inches(xs[i]), Inches(Y0)
        b.width, b.height = Inches(bw), Inches(BH)

    for i, tb in enumerate(inner):
        if i >= n:
            break
        tb.left, tb.top = Inches(xs[i] + 0.08), Inches(Y0 + 0.30)
        tb.width, tb.height = Inches(bw - 0.16), Inches(BH - 0.55)
        tb.text_frame.word_wrap = True
        tb.text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
        for para in tb.text_frame.paragraphs:
            para.alignment = PP_ALIGN.CENTER
            for r in para.runs:
                r.font.size = Pt(14)

    for i, a in enumerate(arrows):
        if i >= n - 1:
            break
        a.left = Inches(xs[i] + bw)
        a.top = Inches(Y0 + BH / 2 - 0.21)
        a.width, a.height = Inches(AW), Inches(0.42)

    bt = bullet_tb[0]
    bt.left, bt.top = Inches(0.62), Inches(3.98)
    bt.width, bt.height = Inches(12.10), Inches(2.75)
    tf = bt.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    tf.margin_top = 0
    tf.margin_bottom = 0

    for extra in EXTRA.get(page_no, []):
        para = tf.add_paragraph()
        para.text = '→  ' + extra

    for para in tf.paragraphs:
        para.line_spacing = 1.62
        para.space_after = Pt(9)
        para.space_before = Pt(0)
        para.alignment = PP_ALIGN.LEFT
        for r in para.runs:
            r.font.size = Pt(16)
            r.font.name = FONT

    print(f'第{page_no}页：流程图 {n} 个已撑满全宽，要点移至下方（{len(tf.paragraphs)} 条）')


for pg in (4, 10, 14):
    refit_flow(p.slides[pg - 1], pg)

# ============ 第 5 页：放大右侧配图 + 左栏要点垂直居中放大 ============
s5 = p.slides[4]
for sh in s5.shapes:
    st = str(sh.shape_type)
    w, h = sh.width / EMU, sh.height / EMU
    if 'PICTURE' in st:
        sh.left, sh.top = Inches(6.62), Inches(2.42)
        sh.width, sh.height = Inches(6.40), Inches(3.59)
    elif sh.has_text_frame and '第一步' in sh.text_frame.text:
        sh.left, sh.top = Inches(0.62), Inches(1.62)
        sh.width, sh.height = Inches(5.72), Inches(5.20)
        tf = sh.text_frame
        tf.word_wrap = True
        tf.vertical_anchor = MSO_ANCHOR.MIDDLE
        for para in tf.paragraphs:
            para.line_spacing = 1.55
            para.space_after = Pt(10)
            for r in para.runs:
                r.font.size = Pt(17)
print('第5页：配图放大至 6.4×3.59，要点 17pt 垂直居中')

# ============ 第 8 页：表格放大到全宽，说明移到页底 ============
s8 = p.slides[7]
for sh in s8.shapes:
    st = str(sh.shape_type)
    l, t = sh.left / EMU, sh.top / EMU
    w, h = sh.width / EMU, sh.height / EMU
    if 'TABLE' in st:
        old_w = w
        sh.left, sh.top = Inches(0.62), Inches(1.50)
        sh.width = Inches(12.10)
        scale = 12.10 / old_w
        for i, col in enumerate(sh.table.columns):
            col.width = int(col.width * scale)
        heights = [0.74] + [0.79] * (len(sh.table.rows) - 1)
        for row, hh in zip(sh.table.rows, heights):
            row.height = Inches(hh)
        for ri, row in enumerate(sh.table.rows):
            for cell in row.cells:
                cell.vertical_anchor = MSO_ANCHOR.MIDDLE
                for para in cell.text_frame.paragraphs:
                    for r in para.runs:
                        r.font.size = Pt(13.5 if ri == 0 else 12)
                        r.font.name = FONT
    elif 'AUTO_SHAPE' in st and abs(w - 8.39) < 0.3 and abs(h - 2.68) < 0.4:
        sh.left, sh.top = Inches(0.50), Inches(1.38)
        sh.width, sh.height = Inches(12.35), Inches(4.92)
    elif sh.has_text_frame and '选型经验' in sh.text_frame.text:
        sh.left, sh.top = Inches(0.62), Inches(6.34)
        sh.width, sh.height = Inches(12.10), Inches(0.55)
        sh.text_frame.word_wrap = True
        for para in sh.text_frame.paragraphs:
            for r in para.runs:
                r.font.size = Pt(14.5)
                r.font.name = FONT
print('第8页：表格放大至 12.10 英寸全宽，字号上调，说明移至页底')

# ============ 第 20 页：右栏补充要点，填满右侧空白 ============
s20 = p.slides[19]
for sh in s20.shapes:
    if sh.has_text_frame and '代价很明确' in sh.text_frame.text:
        tf = sh.text_frame
        tf.word_wrap = True
        sh.top = Inches(1.82)
        sh.height = Inches(5.05)
        for txt in [
            '序列长度翻倍，注意力矩阵元素数变为 4 倍，这是长上下文最直接的显存成本。',
            '正因为 n² 这一项，FlashAttention 这类分块算法才有价值：不落地完整矩阵，把显存从 O(n²) 降到 O(n)。',
        ]:
            para = tf.add_paragraph()
            para.text = '→  ' + txt
        for para in tf.paragraphs:
            para.line_spacing = 1.55
            para.space_after = Pt(10)
            for r in para.runs:
                r.font.size = Pt(14.5)
                r.font.name = FONT
print('第20页：右栏补充 2 条要点，填满右侧空间')

p.save(PATH)
print('\n全部保存完成')
