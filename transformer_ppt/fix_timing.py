# -*- coding: utf-8 -*-
"""修复悬空动画引用：删除第10页 timing 中引用已删除形状的动画节点"""
from pptx import Presentation
from pptx.oxml.ns import qn

PATH = r'transformer_ppt/Transformer架构详解.pptx'
P_NS = 'http://schemas.openxmlformats.org/presentationml/2006/main'
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


total_removed = 0
for idx, s in enumerate(p.slides, 1):
    timing = s._element.find(qn('p:timing'))
    if timing is None:
        continue
    valid = collect_ids(s)

    # 逐层向上找：含 spTgt 的最内层 <p:par>
    removed_here = 0
    for spTgt in timing.findall('.//' + qn('p:spTgt')):
        spid = spTgt.get('spid')
        if spid is None or spid in valid:
            continue
        # 向上找到可删除的动画单元：<p:par>
        node = spTgt
        target = None
        while node is not None and node is not timing:
            if node.tag == qn('p:par'):
                target = node
                break
            node = node.getparent()
        if target is not None:
            parent = target.getparent()
            if parent is not None:
                parent.remove(target)
                removed_here += 1

    if removed_here:
        print(f'第{idx}页：移除 {removed_here} 个悬空动画节点')
        total_removed += removed_here

    # 如果移除后 timing 已无任何动画内容，整块删除
    if removed_here and timing.find('.//' + qn('p:childTnLst')) is None:
        timing.getparent().remove(timing)
        print(f'第{idx}页：timing 已空，整块移除')

print(f'\n共移除 {total_removed} 个悬空动画节点')
p.save(PATH)
print('保存完成')
