# -*- coding: utf-8 -*-
from PIL import Image
p = r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\tmp_uiux\live_screen.png"
im = Image.open(p).convert("RGBA")
print("size", im.size)
# 整个底部横条（含输入框与右侧按钮）
im.crop((300, 730, 1450, 800)).save(r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\tmp_uiux\final_bottom.png")
print("done")
