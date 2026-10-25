# -*- coding: utf-8 -*-
from PIL import Image
p = r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\tmp_uiux\live_screen.png"
im = Image.open(p).convert("RGBA")
cp = im.crop((1745, 262, 1835, 302))
cp.resize((720, 320), Image.LANCZOS).save(r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\tmp_uiux\final_arrows.png")
# 输入框：底部 / for commad 约 x 330-1050, y 745-790
ic = im.crop((325, 740, 1060, 795))
ic.resize((1100, 82), Image.LANCZOS).save(r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\tmp_uiux\final_input.png")
print("done")
