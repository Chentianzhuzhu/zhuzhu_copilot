# -*- coding: utf-8 -*-
"""用重新打包后的文件替换正式文件（内容不变，仅修正 ZIP 结构）"""
import shutil
import os
import zipfile

DIR = r'transformer_ppt'
MAIN = os.path.join(DIR, 'Transformer架构详解.pptx')

# 1) 备份当前版本
bak = os.path.join(DIR, 'Transformer架构详解.bak_before_repack.pptx')
shutil.copy2(MAIN, bak)
print(f'已备份 -> {bak}')

# 2) 用 repack 版本覆盖正式文件（再次重打包，确保干净）
repack = os.path.join(DIR, 'repack.pptx')
tmp_out = os.path.join(DIR, '_final.pptx')
with zipfile.ZipFile(repack) as zin:
    with zipfile.ZipFile(tmp_out, 'w', zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            zout.writestr(info.filename, zin.read(info.filename))
shutil.move(tmp_out, MAIN)
print(f'正式文件已更新 {os.path.getsize(MAIN)} bytes')
