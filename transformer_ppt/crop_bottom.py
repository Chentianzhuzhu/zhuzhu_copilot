# -*- coding: utf-8 -*-
"""裁剪页面底部区域放大，检查页脚是否被内容压住"""
import os, sys
from pptx import Presentation  # noqa

SRC = r'transformer_ppt/preview'
OUT = r'transformer_ppt/preview/crop'
os.makedirs(OUT, exist_ok=True)

try:
    from PIL import Image
except ImportError:
    print('NO_PIL')
    sys.exit(0)

for n in (7, 16, 22):
    f = os.path.join(SRC, f'slide{n}.png')
    if not os.path.exists(f):
        continue
    im = Image.open(f)
    w, h = im.size
    # 底部 18% 区域
    box = (0, int(h * 0.82), w, h)
    crop = im.crop(box)
    crop = crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS)
    out = os.path.join(OUT, f'bottom{n}.png')
    crop.save(out)
    print(f'{n} -> {out}  ({crop.width}x{crop.height})')
