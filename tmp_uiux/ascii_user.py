# -*- coding: utf-8 -*-
from PIL import Image
p = r"C:\Users\zhuzhu\AppData\Local\Doubao\User Data\ClipboardTemp\ce9abdbe-e4cc-414a-a7c5-d73fcbcbe730.png"
im = Image.open(p).convert("RGBA")
w, h = im.size
px = im.load()

# 合成到纯黑背景，取亮度
def cell(x, y):
    r, g, b, a = px[x, y]
    # 按 alpha 合成到黑
    r = r * a // 255; g = g * a // 255; b = b * a // 255
    lum = (r + g + b) // 3
    # 蓝色程度
    blue = b - (r + g) // 2
    return lum, blue, (r, g, b)

# 打印左上角 72x72，每2像素一个字符
for y in range(0, 72, 2):
    row = ""
    for x in range(0, 72, 2):
        lum, blue, rgb = cell(x, y)
        if lum < 14:
            row += "."   # 背景（黑）
        elif blue > 18:
            row += "B"   # 蓝色光晕
        elif lum < 45:
            row += "#"   # 内部填充 FIELD
        else:
            row += "o"   # 亮（文字/光标）
    print(row)
