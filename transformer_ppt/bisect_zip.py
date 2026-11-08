# -*- coding: utf-8 -*-
"""ZIP 级二分：生成只含前 N 页的测试文件，用于定位损坏页"""
import zipfile
import shutil
import os
import re

SRC = r'transformer_ppt/Transformer架构详解.pptx'
OUTDIR = r'transformer_ppt/bisect'

os.makedirs(OUTDIR, exist_ok=True)


def make_truncated(keep, dst):
    """复制 pptx，只保留前 keep 个 sldId"""
    with zipfile.ZipFile(SRC) as zin:
        names = zin.namelist()
        data = {n: zin.read(n) for n in names}

    pres = data['ppt/presentation.xml'].decode('utf-8')
    m = re.search(r'<p:sldIdLst>(.*?)</p:sldIdLst>', pres, re.S)
    if not m:
        print('未找到 sldIdLst')
        return False
    block = m.group(1)
    ids = re.findall(r'<p:sldId[^>]*/>', block)
    kept = ids[:keep]
    new_block = '<p:sldIdLst>' + ''.join(kept) + '</p:sldIdLst>'
    pres = pres.replace(m.group(0), new_block)
    data['ppt/presentation.xml'] = pres.encode('utf-8')

    with zipfile.ZipFile(dst, 'w', zipfile.ZIP_DEFLATED) as zout:
        for n in names:
            zout.writestr(n, data[n])
    return True


for keep in (1, 4, 7, 10, 13, 16, 19, 22, 24):
    dst = os.path.join(OUTDIR, f'keep{keep}.pptx')
    ok = make_truncated(keep, dst)
    print(f'keep{keep}.pptx {"生成" if ok else "失败"}  {os.path.getsize(dst) if ok else 0} bytes')
