# -*- coding: utf-8 -*-
from PIL import Image
p = r"C:\Users\zhuzhu\AppData\Local\Doubao\User Data\ClipboardTemp\ce9abdbe-e4cc-414a-a7c5-d73fcbcbe730.png"
im = Image.open(p).convert("RGB")
print("size", im.size)
w, h = im.size
# 裁四角，各放大
c = 70
crops = {
    "tl": (0, 0, c, c),
    "tr": (w - c, 0, w, c),
    "bl": (0, h - c, c, h),
    "br": (w - c, h - c, w, h),
}
for k, box in crops.items():
    cp = im.crop(box).resize((c * 4, c * 4), Image.NEAREST)
    cp.save(rf"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\tmp_uiux\usercorner_{k}.png")
print("done")
