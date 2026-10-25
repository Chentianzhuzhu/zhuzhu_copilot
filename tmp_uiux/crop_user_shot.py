# -*- coding: utf-8 -*-
from PIL import Image
p = r"C:\Users\zhuzhu\AppData\Local\Doubao\User Data\ClipboardTemp\92ba46d2-da51-4cb1-a298-f25e71dbc290.png"
im = Image.open(p).convert("RGBA")
# 两个箭头区域放大
cp = im.crop((1700, 130, 1845, 185))
cp.resize((580, 220), Image.LANCZOS).save(r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\tmp_uiux\usershot_arrows.png")
print("done")
