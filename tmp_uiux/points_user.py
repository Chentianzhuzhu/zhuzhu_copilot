# -*- coding: utf-8 -*-
from PIL import Image
p = r"C:\Users\zhuzhu\AppData\Local\Doubao\User Data\ClipboardTemp\ce9abdbe-e4cc-414a-a7c5-d73fcbcbe730.png"
im = Image.open(p).convert("RGBA")
px = im.load()
for name, (x, y) in {
    "top_field(40,5)": (40, 5),
    "mid_field(40,40)": (40, 40),
    "bot_field(40,70)": (40, 70),
    "top_glow(40,17)": (40, 17),
    "bot_glow(40,63)": (40, 63),
    "left_glow(5,40)": (5, 40),
    "bg(40,82)": (40, 82),
}.items():
    print(name, px[x, y])
