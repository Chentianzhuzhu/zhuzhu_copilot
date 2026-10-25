# -*- coding: utf-8 -*-
from PIL import Image
for name, p in [
    ("img1", r"C:\Users\zhuzhu\AppData\Local\Doubao\User Data\ClipboardTemp\ce9abdbe-e4cc-414a-a7c5-d73fcbcbe730.png"),
    ("img2", r"C:\Users\zhuzhu\AppData\Local\Doubao\User Data\ClipboardTemp\6dd2a6ae-b9ac-450f-8c05-4798a9c5c34f.png"),
    ("img3", r"C:\Users\zhuzhu\AppData\Local\Doubao\User Data\ClipboardTemp\738578bb-d6b6-49e9-bd44-a93327943f95.png"),
]:
    im = Image.open(p)
    print(name, im.size, im.mode)
