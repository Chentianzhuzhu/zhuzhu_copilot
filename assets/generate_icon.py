from PIL import Image, ImageDraw

sizes = [16, 24, 32, 48, 64, 128, 256]
images = []

for size in sizes:
    img = Image.new("RGBA", (size, size), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)

    # 蓝色菱形背景
    margin = int(size * 0.12)
    diamond = [
        (size // 2, margin),
        (size - margin, size // 2),
        (size // 2, size - margin),
        (margin, size // 2),
    ]
    draw.polygon(diamond, fill=(37, 99, 235, 255))

    # 白色箭头/迁移符号
    line_width = max(2, size // 16)
    arrow_color = (255, 255, 255, 255)
    cx, cy = size // 2, size // 2
    offset = size // 8

    # 向右箭头
    draw.line([(cx - offset, cy - 1), (cx + offset, cy - 1)], fill=arrow_color, width=line_width)
    draw.polygon([
        (cx + offset + 2, cy),
        (cx + offset - 3, cy - 4),
        (cx + offset - 3, cy + 4),
    ], fill=arrow_color)

    # 向左箭头（下方）
    draw.line([(cx + offset, cy + 3), (cx - offset, cy + 3)], fill=arrow_color, width=line_width)
    draw.polygon([
        (cx - offset - 2, cy + 2),
        (cx - offset + 3, cy - 2),
        (cx - offset + 3, cy + 6),
    ], fill=arrow_color)

    images.append(img)

images[0].save("icon.ico", format="ICO", sizes=[(s, s) for s in sizes], append_images=images[1:])
print("icon.ico generated")
