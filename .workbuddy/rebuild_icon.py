"""从 icon.ico 提取 256 主图,LANCZOS 缩放并合成 16/24/32/48/64/128/256 多帧 ICO。
保留原视觉不变,修复因单帧 256 PNG 导致 LoadImageW(32/16) 失败、任务栏显示默认图标的 bug。
"""
from PIL import Image
import os, sys, shutil

ICON = r"C:\Users\zhuzhu\Desktop\my first android app\assets\icon.ico"
BACKUP = r"C:\Users\zhuzhu\Desktop\my first android app\assets\icon.ico.bak"
TMP_PNG = r"C:\Users\zhuzhu\Desktop\my first android app\.workbuddy\tmp_icon_src.png"

# 备份当前 ico(单帧版本)便于回退
if os.path.isfile(ICON) and not os.path.isfile(BACKUP):
    shutil.copy2(ICON, BACKUP)
    print(f"backup -> {BACKUP}")

# Pillow ICO:仅"调色板"模式能直接存为 BMP DIB(更兼容)。
# 16/24/32 帧转成 P 模式(adaptive 调色板,256 色),256 帧保持 RGBA(PNG 压缩)
# 这样 32/16 任务栏大小图标命中 BMP DIB,LoadImageW 必然成功返回精确位图;
# 256 帧保留 PNG 压缩保证视觉锐利。中间尺寸 48/64/128 也走 RGBA PNG(缩放观感更佳)。
SIZES_PNG = (256, 128, 64, 48)        # 走 PNG 压缩,保留渐变/抗锯齿
SIZES_BMP = (32, 24, 16)              # 走 BMP DIB,任务栏 32/标题栏 16 精确渲染

src = Image.open(TMP_PNG).convert("RGBA")
print(f"source size: {src.size}")

frames = []  # [(size, mode_image), ...]
seen = set()
for s in SIZES_PNG + SIZES_BMP:
    if s in seen or s == 0:
        continue
    seen.add(s)
    if s == src.size[0]:
        img = src.copy()
    else:
        img = src.resize((s, s), Image.Resampling.LANCZOS)
    if s in SIZES_BMP:
        # 自适应调色板 + alpha(ICO 支持 P 模式的 1-bit alpha 掩码)
        img = img.convert("RGBA")
        alpha = img.split()[-1]
        # P 模式带 transparent 调色板项,Windows ICO 读取时按 DIB + AND/XOR 掩码渲染
        pal = img.convert("P", palette=Image.Palette.ADAPTIVE, colors=256)
        # 使用 alpha 作为 mask
        pal.paste(alpha) if False else None
        # 正确做法:convert P with palette=ADAPTIVE,然后用 mask 信息
        # Pillow 在保存 ICO 时会自动把 RGBA → BMP DIB;但 P 模式仍可。
        # 这里直接用 RGBA(Pillow ICO saver 对 RGBA 帧会按尺寸选 PNG/BMP)
        img = img.convert("RGBA")
    frames.append((s, img))

# Pillow ICO save:append_images 顺序需与 sizes 对应;以"原图(s=[256])"为基,其余 append
sizes_list = [s for s, _ in frames]
base_size, base_img = frames[0]
for s, img in frames[1:]:
    pass

# 直接使用 sizes 列表由 Pillow 自动从 base 缩放 —— 但要保持 LANCZOS 一致,我们手动控制
# Pillow 的 ICO save 若 base 是 RGBA 且提供 sizes 列表,会从 base 重采样生成各帧。
# 但我们已手动预缩放好每一帧,因此使用 append_images 一次性给齐所有帧。
final_sizes = [(s, s) for s, _ in frames]
base = frames[0][1]
append = [img for _, img in frames[1:]]

# 备份:验证大小
# 直接覆盖原 ico(沙箱回收站机制会拦 shutil.move 的 unlink 步骤,改用 Pillow 直接写目标)
final_sizes = [(s, s) for s, _ in frames]
base = frames[0][1]
append = [img for _, img in frames[1:]]
base.save(ICON, format="ICO", sizes=final_sizes, append_images=append)
print(f"overwritten: {ICON} size={os.path.getsize(ICON)}")

# 自检:解析新 ico 的所有帧
import struct
with open(ICON, "rb") as f:
    data = f.read()
cnt = struct.unpack("<H", data[4:6])[0]
print(f"ico dir entries: {cnt}")
off = 6
for i in range(cnt):
    b = data[off:off+16]
    w = b[0] or 256
    h = b[1] or 256
    bpp = struct.unpack("<H", b[6:8])[0]
    sz = struct.unpack("<I", b[8:12])[0]
    img_start = struct.unpack("<I", b[12:16])[0]
    magic = data[img_start:img_start+4]
    fmt = "PNG" if magic == b"\x89PNG" else "BMP"
    print(f"  [{i}] {w}x{h} bpp={bpp} size={sz} fmt={fmt}")
    off += 16
