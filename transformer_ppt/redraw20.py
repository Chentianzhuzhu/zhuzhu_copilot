# -*- coding: utf-8 -*-
"""重做第 20 页柱状图：删除错乱形状，重绘精确摆放的横向条形图
   关键：删除形状后必须清理 bldLst / timing 悬空引用，否则 PowerPoint 拒绝打开
"""
import math
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.dml.color import RGBColor
from pptx.oxml.ns import qn

PATH = r'transformer_ppt/Transformer架构详解.pptx'
EMU = 914400.0
FONT = '微软雅黑'

NAVY = RGBColor(0x0E, 0x5A, 0x8A)
CYAN = RGBColor(0x12, 0xA8, 0xC0)
GREEN = RGBColor(0x10, 0xB9, 0x81)
GREY = RGBColor(0x8C, 0x9B, 0xAD)
INK = RGBColor(0x26, 0x33, 0x3E)

p = Presentation(PATH)
s = p.slides[19]

# ---------- 1) 找出并删除旧图表相关形状（标题框/参考线/柱条/标签） ----------
OLD_NAMES = set()
for sh in s.shapes:
    t, l = sh.top / EMU, sh.left / EMU
    w, h = sh.width / EMU, sh.height / EMU
    txt = sh.text_frame.text.strip() if sh.has_text_frame else ''
    kill = False
    # 图表标题框
    if txt.startswith('n = 512 时任意两位置'):
        kill = True
    # 三条标签框（宽 7.70）
    elif abs(w - 7.70) < 0.05 and ('RNN' in txt or 'CNN' in txt or 'Self-Attention' in txt):
        kill = True
    # RNN 数值框
    elif txt.strip() == 'RNN  512':
        kill = True
    # 参考细线（高 <= 0.03，宽 > 8）
    elif 'AUTO_SHAPE' in str(sh.shape_type) and h <= 0.03 and w > 8:
        kill = True
    # 短柱条（高 0.50，y > 3.0）
    elif 'AUTO_SHAPE' in str(sh.shape_type) and abs(h - 0.50) < 0.05 and t > 3.0:
        kill = True
    if kill:
        OLD_NAMES.add(sh.name)
        sh._element.getparent().remove(sh._element)

print(f'删除旧图表形状 {len(OLD_NAMES)} 个: {sorted(OLD_NAMES)}')

# ---------- 2) 重绘横向条形图 ----------
TITLE_Y = 3.52
# (标签, 数值, 颜色)
ROWS = [
    ('RNN / LSTM', 512, NAVY),
    ('CNN (k=3)', 6, CYAN),
    ('Self-Attention', 1, GREEN),
]
BAR_X0 = 2.30          # 条形起点
BAR_MAX_W = 4.55       # 满宽（RNN=512）
LABEL_X = 0.62
LABEL_W = 1.58
VAL_X = BAR_X0 + BAR_MAX_W + 0.12
VAL_W = 1.25
ROW_H = 0.56
ROW_GAP = 0.30
Y0 = 3.98
MAXV = 512.0


def add_label(text, x, y, w, h, size, color, bold=False, align=PP_ALIGN.LEFT):
    tb = s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.margin_left = 0
    tf.margin_right = 0
    tf.margin_top = 0
    tf.margin_bottom = 0
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    tf.text = text
    para = tf.paragraphs[0]
    para.alignment = align
    for r in para.runs:
        r.font.size = Pt(size)
        r.font.name = FONT
        r.font.color.rgb = color
        r.font.bold = bold
    return tb


def add_bar(x, y, w, h, color):
    bar = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
    bar.fill.solid()
    bar.fill.fore_color.rgb = color
    bar.line.fill.background()
    bar.shadow.inherit = False
    try:
        bar.adjustments[0] = 0.18
    except Exception:
        pass
    return bar


# 图表标题
add_label('n = 512 时，任意两位置的最大交互步数（越小越好）',
          LABEL_X, TITLE_Y, 8.55, 0.38, 13.5, GREY)

MIN_W = 0.10
for i, (label, val, color) in enumerate(ROWS):
    y = Y0 + i * (ROW_H + ROW_GAP)
    # 左侧标签（右对齐贴近条形）
    add_label(label, LABEL_X, y, LABEL_W, ROW_H, 13.5, INK, align=PP_ALIGN.RIGHT)
    # 条形（线性比例，极小值给最小可见宽度）
    w = max(MIN_W, BAR_MAX_W * val / MAXV)
    add_bar(BAR_X0, y + 0.06, w, ROW_H - 0.12, color)
    # 数值标注
    add_label(str(val), VAL_X, y, VAL_W, ROW_H, 15, color, bold=True)

# 底部注释
add_label('线性刻度下，CNN 与 Self-Attention 的柱长几乎看不见 —— 这正是差距的分量。',
          LABEL_X, Y0 + 3 * (ROW_H + ROW_GAP) + 0.02, 8.55, 0.52, 12, GREY)

print('横向条形图已重绘：3 行条形 + 标签 + 数值 + 注释')

# ---------- 3) 清理 bldLst / timing 中的悬空引用 ----------
def collect_ids(slide):
    ids = set()
    for sh in slide.shapes:
        for tag in ('p:nvSpPr', 'p:nvPicPr', 'p:nvGraphicFramePr', 'p:nvGrpSpPr'):
            nv = sh._element.find(qn(tag))
            if nv is not None:
                c = nv.find(qn('p:cNvPr'))
                if c is not None and c.get('id'):
                    ids.add(c.get('id'))
    return ids


valid = collect_ids(s)
root = s._element
removed_bld = 0
for bldP in root.findall('.//' + qn('p:bldP')):
    spid = bldP.get('spid')
    if spid and spid not in valid:
        bldP.getparent().remove(bldP)
        removed_bld += 1

removed_par = 0
timing = root.find(qn('p:timing'))
if timing is not None:
    for spTgt in list(timing.findall('.//' + qn('p:spTgt'))):
        spid = spTgt.get('spid')
        if spid is None or spid in valid:
            continue
        node, target = spTgt, None
        while node is not None and node is not timing:
            if node.tag == qn('p:par'):
                target = node
            node = node.getparent()
        if target is not None and target.getparent() is not None:
            target.getparent().remove(target)
            removed_par += 1

print(f'清理悬空引用：bldP {removed_bld} 个，动画节点 {removed_par} 个')

p.save(PATH)
print('保存完成')
