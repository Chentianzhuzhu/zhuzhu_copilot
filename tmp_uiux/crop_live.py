# -*- coding: utf-8 -*-
from PIL import Image
p = r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\tmp_uiux\live_screen.png"
im = Image.open(p).convert("RGBA")
# Open 按钮右侧的两个箭头：x 约 1775-1870, y 265-300
cp = im.crop((1770, 262, 1880, 302))
cp.resize((660, 240), Image.LANCZOS).save(r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\tmp_uiux\live_arrows.png")
print("done")
