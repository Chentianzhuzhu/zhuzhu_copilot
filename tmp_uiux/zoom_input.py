# -*- coding: utf-8 -*-
from PIL import Image
p = r"C:\Users\zhuzhu\AppData\Local\Doubao\User Data\ClipboardTemp\ce9abdbe-e4cc-414a-a7c5-d73fcbcbe730.png"
im = Image.open(p).convert("RGB")
print("size", im.size)
# 放大左上角
w, h = im.size
crop = im.crop((0, 0, min(120, w), h)).resize((min(120, w) * 4, h * 4), Image.NEAREST)
crop.save(r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\tmp_uiux\input_tl_zoom.png")
# 放大右上角
crop2 = im.crop((w - 120, 0, w, h)).resize((120 * 4, h * 4), Image.NEAREST)
crop2.save(r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\tmp_uiux\input_tr_zoom.png")
