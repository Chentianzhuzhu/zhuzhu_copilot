# -*- coding: utf-8 -*-
from PIL import Image
p = r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\assets\pet.gif"
im = Image.open(p)
print("size", im.size, "frames", getattr(im, "n_frames", 1), "mode", im.mode)
print("transparency", im.info.get("transparency"), "background", im.info.get("background"))
im.seek(0)
fr = im.convert("RGBA")
a = fr.getchannel("A")
hist = a.histogram()
print("alpha=0 count", hist[0], "alpha=255 count", hist[255], "partial", sum(hist[1:255]))
px = fr.load()
w, h = fr.size
dark = 0
for y in range(h):
    for x in range(w):
        r, g, b, al = px[x, y]
        if al > 0 and r < 60 and g < 60 and b < 60:
            dark += 1
print("dark opaque pixels", dark)
# 保存首帧 PNG 观察
fr.save(r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\tmp_uiux\pet_frame0.png")

# 试做 alpha 平滑：中值去孤立点 + 高斯羽化
from PIL import ImageFilter
r, g, b, a = fr.split()
a2 = a.filter(ImageFilter.MedianFilter(3))
a2 = a2.filter(ImageFilter.GaussianBlur(1.1))
sm = Image.merge("RGBA", (r, g, b, a2))
sm.save(r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\tmp_uiux\pet_frame0_smooth.png")
# 只高斯、不中值
a3 = a.filter(ImageFilter.GaussianBlur(1.0))
sm2 = Image.merge("RGBA", (r, g, b, a3))
sm2.save(r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\tmp_uiux\pet_frame0_blur.png")
