# -*- coding: utf-8 -*-
from PIL import Image
p = r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\tmp_uiux\live_screen.png"
im = Image.open(p).convert("RGBA")
# 预览区 Open + 箭头：x 约 1690-1850, y 265-300
cp = im.crop((1690, 262, 1860, 302))
cp.resize((1020, 240), Image.LANCZOS).save(r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\tmp_uiux\live_arrows2.png")
print("done")
