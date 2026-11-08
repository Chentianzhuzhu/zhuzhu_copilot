# -*- coding: utf-8 -*-
"""检查 timing 结构完整性：空的 childTnLst、缺子节点的 seq、必需元素缺失"""
import io
from pptx import Presentation
from pptx.oxml.ns import qn

PATH = r'transformer_ppt/Transformer架构详解.pptx'
p = Presentation(PATH)
out = io.StringIO()
bad = []

for idx, s in enumerate(p.slides, 1):
    root = s._element
    timing = root.find(qn('p:timing'))
    if timing is None:
        continue
    # 1) 空 childTnLst
    for ctl in timing.findall('.//' + qn('p:childTnLst')):
        if len(list(ctl)) == 0:
            out.write(f'第{idx}页：存在空 childTnLst\n')
            bad.append((idx, 'empty childTnLst'))
    # 2) p:seq 缺 childTnLst
    for seq in timing.findall('.//' + qn('p:seq')):
        if seq.find(qn('p:cTn')) is None:
            out.write(f'第{idx}页：p:seq 缺 cTn\n')
            bad.append((idx, 'seq no cTn'))
    # 3) cTn 缺 stCondLst / endCondLst
    for ctn in timing.findall('.//' + qn('p:cTn')):
        if ctn.find(qn('p:stCondLst')) is None:
            out.write(f'第{idx}页：cTn id={ctn.get("id")} 缺 stCondLst\n')
            bad.append((idx, 'cTn no stCondLst'))
    # 4) par 缺 cTn
    for par in timing.findall('.//' + qn('p:par')):
        if par.find(qn('p:cTn')) is None:
            out.write(f'第{idx}页：p:par 缺 cTn\n')
            bad.append((idx, 'par no cTn'))

out.write(f'\n结构问题总数: {len(bad)}\n')
with open('transformer_ppt/structure.txt', 'w', encoding='utf-8') as f:
    f.write(out.getvalue())
print('done, bad =', len(bad))
