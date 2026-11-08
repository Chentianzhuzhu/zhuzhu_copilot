# -*- coding: utf-8 -*-
"""清理 bldLst 中引用不存在形状的 bldP 条目（PowerPoint 拒开的主因）"""
from pptx import Presentation
from pptx.oxml.ns import qn

PATH = r'transformer_ppt/Transformer架构详解.pptx'
p = Presentation(PATH)


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


total = 0
for idx, s in enumerate(p.slides, 1):
    valid = collect_ids(s)
    root = s._element

    # 1) 清理 bldLst 里的悬空 bldP
    removed_bld = 0
    for bldP in root.findall('.//' + qn('p:bldP')):
        spid = bldP.get('spid')
        if spid and spid not in valid:
            bldP.getparent().remove(bldP)
            removed_bld += 1

    # 2) 清理 timing 里剩余 spTgt 悬空引用（逐个 spTgt 处理，向上找最内层 p:par）
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

    if removed_bld or removed_par:
        print(f'第{idx}页：清理 bldP {removed_bld} 个，动画节点 {removed_par} 个')
        total += removed_bld + removed_par

print(f'\n共清理 {total} 处')
p.save(PATH)
print('保存完成')
