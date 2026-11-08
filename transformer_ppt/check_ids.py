# -*- coding: utf-8 -*-
"""检查每页 shape id 是否重复（PowerPoint 拒绝打开的头号原因）"""
import io
from pptx import Presentation
from pptx.oxml.ns import qn

PATH = r'transformer_ppt/Transformer架构详解.pptx'
p = Presentation(PATH)
out = io.StringIO()
problems = []

for idx, s in enumerate(p.slides, 1):
    ids = []
    for sh in s.shapes:
        nv = sh._element.find(qn('p:nvSpPr'))
        if nv is None:
            nv = sh._element.find(qn('p:nvPicPr'))
        if nv is None:
            nv = sh._element.find(qn('p:nvGraphicFramePr'))
        if nv is None:
            nv = sh._element.find(qn('p:nvGrpSpPr'))
        cNvPr = nv.find(qn('p:cNvPr')) if nv is not None else None
        if cNvPr is not None:
            ids.append((int(cNvPr.get('id')), cNvPr.get('name')))
    seen = {}
    for i, name in ids:
        seen.setdefault(i, []).append(name)
    dup = {i: n for i, n in seen.items() if len(n) > 1}
    if dup:
        problems.append(idx)
        out.write(f'第{idx}页 id 重复: {dup}\n')

out.write(f'\n有问题的页: {problems}\n')

# 同时检查动画引用（p:timing 里的 spid）是否指向不存在的形状
out.write('\n--- 动画 spid 引用检查 ---\n')
for idx, s in enumerate(p.slides, 1):
    xml = s._element.xml
    if '<p:timing>' not in xml:
        continue
    import re
    spids = set(re.findall(r'spid="(\d+)"', xml))
    shape_ids = set()
    for sh in s.shapes:
        for tag in ('p:nvSpPr', 'p:nvPicPr', 'p:nvGraphicFramePr'):
            nv = sh._element.find(qn(tag))
            if nv is not None:
                c = nv.find(qn('p:cNvPr'))
                if c is not None:
                    shape_ids.add(c.get('id'))
    missing = spids - shape_ids
    if missing:
        out.write(f'第{idx}页 动画引用了不存在的形状 id: {sorted(missing)}\n')

with open('transformer_ppt/idcheck.txt', 'w', encoding='utf-8') as f:
    f.write(out.getvalue())
print('done')
