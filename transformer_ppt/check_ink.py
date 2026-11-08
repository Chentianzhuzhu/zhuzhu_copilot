# -*- coding: utf-8 -*-
"""精确版留白扫描：按真实文字高度估算内容占用，而非形状包围盒"""
import io, unicodedata
from pptx import Presentation
from pptx.enum.text import MSO_ANCHOR

EMU = 914400.0
PAGE_H = 7.5
BODY_TOP = 1.35      # 标题区下方
BODY_BOTTOM = 6.95   # 页脚上方


def char_em(ch):
    """粗略字符宽度（以 em 为单位）"""
    if unicodedata.east_asian_width(ch) in ('W', 'F'):
        return 1.0
    if ch in ' \t':
        return 0.32
    return 0.55


def text_ink_height(sh):
    """估算文本框真实文字高度（英寸）"""
    tf = sh.text_frame
    w_pt = (sh.width / EMU) * 72.0 - 7.2   # 扣掉左右内边距
    if w_pt <= 10:
        return 0.0
    total = 0.0
    has_text = False
    for para in tf.paragraphs:
        txt = ''.join(r.text for r in para.runs) or para.text
        if not txt.strip():
            continue
        has_text = True
        size = None
        for r in para.runs:
            if r.font.size:
                size = r.font.size.pt
                break
        if size is None:
            size = 16.0
        em = sum(char_em(c) for c in txt) * size
        lines = max(1, int(em / w_pt) + (1 if em % w_pt else 0))
        ls = para.line_spacing if isinstance(para.line_spacing, float) else 1.0
        total += lines * size * 1.22 * max(ls, 1.0) / 72.0
        sa = para.space_after
        if sa is not None:
            total += sa.pt / 72.0
    return total if has_text else 0.0


def shape_ink_bottom(sh):
    """返回该形状内容真实底边（英寸）；非正文返回 None"""
    top = sh.top / EMU
    h = sh.height / EMU
    st = str(sh.shape_type)

    if 'PICTURE' in st or 'CHART' in st:
        return top + h

    if 'TABLE' in st:
        # 表格按行高累加
        try:
            heights = [r.height / EMU for r in sh.table.rows]
            return top + sum(heights)
        except Exception:
            return top + h

    if sh.has_text_frame and sh.text_frame.text.strip():
        need = text_ink_height(sh)
        if need <= 0:
            return None
        anchor = sh.text_frame.vertical_anchor
        if anchor == MSO_ANCHOR.MIDDLE:
            return top + h / 2 + need / 2
        return top + need          # TOP / None 默认
    return None


p = Presentation(r'transformer_ppt/Transformer架构详解.pptx')
out = io.StringIO()
issues = []

for idx, s in enumerate(p.slides, 1):
    max_bottom = 0.0
    for sh in s.shapes:
        top = sh.top / EMU
        h = sh.height / EMU
        txt = sh.text_frame.text.strip() if sh.has_text_frame else ''
        # 跳过标题/页脚/页码/装饰色条与圆
        if top < BODY_TOP or top > 6.95:
            continue
        if txt.isdigit():
            continue
        if not txt and 'AUTO_SHAPE' in str(sh.shape_type) and (h < 0.15 or (abs(sh.width/EMU - h) < 0.05)):
            continue   # 装饰细条 / 正圆
        b = shape_ink_bottom(sh)
        if b is None:
            continue
        if b > max_bottom:
            max_bottom = b
    gap = BODY_BOTTOM - max_bottom
    if gap > 1.3 or gap < -0.15:
        flag = '留白偏大' if gap > 1.3 else '可能溢出'
        issues.append((idx, max_bottom, gap, flag))
    out.write(f'第{idx:2d}页  真实内容底={max_bottom:.2f}in  剩余={gap:+.2f}in'
              f'{"   <== " + flag if (gap > 1.3 or gap < -0.15) else ""}\n')

out.write(f'\n共 {len(issues)} 页需处理: {[i[0] for i in issues]}\n')
with open('transformer_ppt/ink_report.txt', 'w', encoding='utf-8') as f:
    f.write(out.getvalue())
print('done', [i[0] for i in issues])
