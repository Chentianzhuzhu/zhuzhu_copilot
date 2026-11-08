# -*- coding: utf-8 -*-
"""纯重新打包（不改任何 XML 内容），验证是否为 ZIP 打包问题"""
import zipfile
import os

SRC = r'transformer_ppt/Transformer架构详解.pptx'
DST = r'transformer_ppt/repack.pptx'

with zipfile.ZipFile(SRC) as zin:
    infos = zin.infolist()
    print(f'原文件条目数: {len(infos)}')
    # 检查重复条目
    names = [i.filename for i in infos]
    dup = {n for n in names if names.count(n) > 1}
    print(f'重复条目: {dup if dup else "无"}')

    with zipfile.ZipFile(DST, 'w', zipfile.ZIP_DEFLATED) as zout:
        for info in infos:
            zout.writestr(info.filename, zin.read(info.filename))

print(f'repack.pptx 已生成 {os.path.getsize(DST)} bytes')
