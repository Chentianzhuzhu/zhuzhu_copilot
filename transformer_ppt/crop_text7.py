# -*- coding: utf-8 -*-
"""裁剪第 7 页正文区域，检查行首标点问题"""
import os
from PIL import Image

SRC = r'transformer_ppt/preview/slide7.png'
OUT = r'transformer_ppt/preview/crop/text7.png'
os.makedirs(os.path.dirname(OUT), exist_ok=True)

im = Image.open(SRC)
w, h = im.size
# 正文大致在 15%~92% 高度区间
crop = im.crop((0, int(h * 0.16), w, int(h * 0.93)))
crop = crop.resize((int(crop.width * 1.05), int(crop.height * 1.05)), Image.LANCZOS)
crop.save(OUT)
print(f'{OUT}  {crop.width}x{crop.height}')
