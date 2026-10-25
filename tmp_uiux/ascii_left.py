# -*- coding: utf-8 -*-
from PIL import Image
p = r"C:\Users\zhuzhu\AppData\Local\Doubao\User Data\ClipboardTemp\ce9abdbe-e4cc-414a-a7c5-d73fcbcbe730.png"
im = Image.open(p).convert("RGBA")
px = im.load()

def classify(x, y):
    r, g, b, a = px[x, y]
    r = r * a // 255; g = g * a // 255; b = b * a // 255
    lum = (r + g + b) // 3
    blue = b - (r + g) // 2
    if lum < 14:
        return "."
    if blue > 18:
        return "B"
    if lum < 45:
        return "#"
    return "o"

# 打印 x=0..80, 全部 y，每像素
for y in range(87):
    row = "".join(classify(x, y) for x in range(0, 80, 1))
    print(f"{y:2d} {row}")
