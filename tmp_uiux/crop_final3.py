# -*- coding: utf-8 -*-
from PIL import Image
p = r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\tmp_uiux\live_screen.png"
im = Image.open(p).convert("RGBA")
print("size", im.size)
# 扫描行，找到包含 "/ for commad" 文字的行（非纯黑的行）
import numpy as np
a = np.array(im.convert("L"))
rows = a.mean(axis=1)
for i, v in enumerate(rows):
    if v > 14:
        # 找输入框区域（y 700-900）
        if 700 < i < 920:
            print(i, round(float(v), 1))
