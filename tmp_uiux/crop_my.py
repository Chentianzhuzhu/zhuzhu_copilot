# -*- coding: utf-8 -*-
from PIL import Image
p = r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\tmp_uiux\glow_test_real.png"
im = Image.open(p).convert("RGB")
w, h = im.size
print("size", im.size)
# 输入框大约在 y=60 附近，左右 margin 60。裁右下角区域
# 整图 1200x160，输入框 x:60..1140, y:60..104
c = 80
crops = {
    "tl": (55, 55, 55 + c, 55 + c),
    "br": (1140 - c, 104 - c, 1145, 110),
}
for k, box in crops.items():
    cp = im.crop(box).resize((c * 4, c * 4), Image.NEAREST)
    cp.save(rf"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\tmp_uiux\mycorner_{k}.png")
print("done")
