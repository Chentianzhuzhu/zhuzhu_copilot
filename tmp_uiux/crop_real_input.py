# -*- coding: utf-8 -*-
from PIL import Image
p = r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\tmp_uiux\real_input_grab.png"
im = Image.open(p).convert("RGBA")
w, h = im.size
print("size", im.size)
c = 40
for k, box in {"tl": (0, 0, c, c), "br": (w - c, h - c, w, h)}.items():
    cp = im.crop(box).resize((c * 6, c * 6), Image.NEAREST)
    # 合成到中灰背景，清楚区分透明
    bg = Image.new("RGBA", cp.size, (128, 128, 128, 255))
    bg.alpha_composite(cp)
    bg.save(rf"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\tmp_uiux\real_input_{k}.png")
print("done")
