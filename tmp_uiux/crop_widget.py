# -*- coding: utf-8 -*-
from PIL import Image
p = r"C:\Users\zhuzhu\AppData\Local\Doubao\User Data\ClipboardTemp\ce9abdbe-e4cc-414a-a7c5-d73fcbcbe730.png"
im = Image.open(p).convert("RGBA")
# 输入框主体（含光晕）y=16-66。裁左上角 x=0-45, y=14-60
crops = {
    "wid_tl": (0, 14, 45, 60),
    "wid_br": (1156, 14, 1201, 60),
}
for k, box in crops.items():
    cp = im.crop(box)
    w, h = cp.size
    cp = cp.resize((w * 6, h * 6), Image.NEAREST)
    cp.save(rf"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\tmp_uiux\{k}.png")
print("done")
