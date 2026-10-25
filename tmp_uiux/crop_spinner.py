# -*- coding: utf-8 -*-
from PIL import Image
for tag in ("f0", "f1", "f2"):
    p = rf"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\tmp_uiux\overlay_{tag}.png"
    im = Image.open(p).convert("RGBA")
    # spinner 中心约在 (495, 320)
    cp = im.crop((460, 285, 530, 355))
    cp.resize((420, 420), Image.LANCZOS).save(
        rf"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\tmp_uiux\spin_{tag}.png")
print("done")
