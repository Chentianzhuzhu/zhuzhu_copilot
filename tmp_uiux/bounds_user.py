# -*- coding: utf-8 -*-
from PIL import Image
p = r"C:\Users\zhuzhu\AppData\Local\Doubao\User Data\ClipboardTemp\ce9abdbe-e4cc-414a-a7c5-d73fcbcbe730.png"
im = Image.open(p).convert("RGBA")
w, h = im.size
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

# 找每一行第一个非背景像素
print("=== 每行第一个/最后一个非背景 ===")
for y in range(0, h, 2):
    first = None; last = None
    for x in range(w):
        if classify(x, y) != ".":
            if first is None:
                first = x
            last = x
    print(f"y={y:3d} first={first} last={last}")
