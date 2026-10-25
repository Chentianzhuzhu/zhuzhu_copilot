# -*- coding: utf-8 -*-
from PIL import Image
p = r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\tmp_uiux\glow_test_real.png"
im = Image.open(p).convert("RGBA")
# 输入框在 y=60-104, x=60-1140。裁内部左上角
cp = im.crop((58, 58, 105, 100))
w, h = cp.size
cp.resize((w * 6, h * 6), Image.NEAREST).save(
    r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\tmp_uiux\mywid_tl.png")
print("done")
