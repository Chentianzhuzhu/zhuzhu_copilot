# -*- coding: utf-8 -*-
"""全面诊断：id 冲突 + 空 childTnLst + 悬空 spTgt + 空 seq"""
import io
from pptx import Presentation
from pptx.oxml.ns import qn

PATH = r'transformer_ppt/Transformer架构详解.pptx'
p = Presentation(PATH)
out = io.StringIO()
issues = 0


def collect_ids(slide):
    ids = []
    for sh in slide.shapes:
        for tag in ('p:nvSpPr', 'p:nvPicPr', 'p:nvGraphicFramePr', 'p:nvGrpSpPr'):
            nv = sh._element.find(qn(tag))
            if nv is not None:
                c = nv.find(qn('p:cNvPr'))
                if c is not None and c.get('id'):
                    ids.append(c.get('id'))
    return ids


for idx, s in enumerate(p.slides, 1):
    ids = collect_ids(s)
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        out.write(f'第{idx}页 id 重复: {dup}\n')
        issues += 1

    valid = set(ids)
    root = s._element
    timing = root.find(qn('p:timing'))
    if timing is None:
        continue

    # 悬空 spTgt
    dang = {sp.get('spid') for sp in timing.findall('.//' + qn('p:spTgt'))
            if sp.get('spid') and sp.get('spid') not in valid}
    if dang:
        out.write(f'第{idx}页 悬空 spTgt: {dang}\n')
        issues += 1

    # 悬空 bldP
    dangb = {b.get('spid') for b in root.findall('.//' + qn('p:bldP'))
             if b.get('spid') and b.get('spid') not in valid}
    if dangb:
        out.write(f'第{idx}页 悬空 bldP: {dangb}\n')
        issues += 1

    # 空 childTnLst
    for ctl in timing.findall('.//' + qn('p:childTnLst')):
        if len(list(ctl)) == 0:
            out.write(f'第{idx}页 空 childTnLst\n')
            issues += 1

    # seq 缺 cTn / 空 seq
    for seq in timing.findall('.//' + qn('p:seq')):
        ctn = seq.find(qn('p:cTn'))
        if ctn is None:
            out.write(f'第{idx}页 seq 缺 cTn\n')
            issues += 1
            continue
        ctl = ctn.find(qn('p:childTnLst'))
        if ctl is None or len(list(ctl)) == 0:
            out.write(f'第{idx}页 seq 的 childTnLst 为空\n')
            issues += 1

out.write(f'\n问题总数: {issues}\n')
with open('transformer_ppt/diag2.txt', 'w', encoding='utf-8') as f:
    f.write(out.getvalue())
print('done, issues =', issues)
