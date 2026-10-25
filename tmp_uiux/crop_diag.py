# -*- coding: utf-8 -*-
from PIL import Image
p = r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\tmp_uiux\diag_frame.png"
im = Image.open(p).convert("RGBA")
print("size", im.size)
w, h = im.size
c = 42
crops = {
    "tl": (0, 0, c, c),
    "br": (w - c, h - c, w, h),
}
for k, box in crops.items():
    cp = im.crop(box).resize((c * 6, c * 6), Image.NEAREST)
    # 合成到深背景上以便看清透明
    bg = Image.new("RGBA", cp.size, (16, 18, 22, 255))
    bg.alpha_composite(cp)
    bg.save(rf"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\tmp_uiux\diagcorner_{k}.png")
print("done")
