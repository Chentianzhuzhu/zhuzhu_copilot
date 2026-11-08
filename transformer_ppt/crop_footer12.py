# -*- coding: utf-8 -*-
"""放大第 12 页页脚区域，确认是否为真实渲染问题"""
import os
from PIL import Image

SRC = r'transformer_ppt/preview/slide12.png'
OUTDIR = r'transformer_ppt/preview/crop'
os.makedirs(OUTDIR, exist_ok=True)

im = Image.open(SRC)
w, h = im.size
print('原图尺寸', w, h)

# 页脚位于底部约 5%（y≈0.945h），取 88%~100% 高度，左半部分
box = (0, int(h * 0.88), int(w * 0.62), h)
crop = im.crop(box)
crop = crop.resize((crop.width * 3, crop.height * 3), Image.LANCZOS)
out = os.path.join(OUTDIR, 'footer12.png')
crop.save(out)
print(f'{out}  {crop.width}x{crop.height}')
