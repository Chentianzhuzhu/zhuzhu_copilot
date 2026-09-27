#!/usr/bin/env python3
"""生成官网品牌资源（站点图标 + 社交分享图）。

四色体系：纯黑 #000000 / 淡灰 #F5F5F5 / 深蓝 #1E40AF，无其他彩色。
输出（写入 static/ 与 static/og/）：
  favicon.ico（16/24/32/48/64）、apple-touch-icon.png、icon-192.png、icon-512.png、og/og-cover.png

分享图上的产品名由 --name 指定（与线上站名保持一致）；站名后续若在后台改动，
可在「SEO → 社交分享图」直接替换为自定义图片，本图仅作默认兜底。

用法：
  python tools/gen_brand_assets.py --name "zhuzhu Copilot"
  python tools/gen_brand_assets.py --out DIR
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont

BLACK = (0, 0, 0, 255)
INK = (10, 10, 10, 255)
WHITE = (245, 245, 245, 255)
BLUE = (30, 64, 175, 255)
BLUE_BRIGHT = (91, 123, 232, 255)

SS = 8  # 超采样倍数，保证小尺寸图标边缘平滑

# 本机字体（生成动作在开发机执行，产物为 PNG 资源，服务器无需字体）
FONT_BOLD = r"C:\Windows\Fonts\arialbd.ttf"
FONT_MONO = r"C:\Windows\Fonts\consolab.ttf"
FONT_CJK = r"C:\Windows\Fonts\msyhbd.ttc"


def _font(path: str, size: int, index: int = 0) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(path, size, index=index)
    except OSError:
        return ImageFont.load_default(size)


def draw_mark(size: int, with_cross: bool = True) -> Image.Image:
    """绘制品牌标记：外框 + 十字辅助线 + 深蓝方块。"""
    s = size * SS
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img, "RGBA")  # RGBA 模式才会按 alpha 混合，避免辅助线变成实心灰条

    pad = round(s * 0.06)
    stroke = max(1, round(s * 0.062))
    radius = round(s * 0.12)

    if with_cross:
        thin = max(1, round(stroke * 0.45))
        mid = s / 2
        d.line([(mid, pad), (mid, s - pad)], fill=WHITE[:3] + (80,), width=thin)
        d.line([(pad, mid), (s - pad, mid)], fill=WHITE[:3] + (80,), width=thin)

    d.rounded_rectangle([pad, pad, s - pad, s - pad], radius=radius, outline=WHITE, width=stroke)

    # 左上象限填充深蓝方块
    box_pad = round(s * 0.19)
    box = round(s * 0.29)
    d.rounded_rectangle(
        [box_pad, box_pad, box_pad + box, box_pad + box],
        radius=round(s * 0.05),
        fill=BLUE,
    )
    return img.resize((size, size), Image.LANCZOS)


def radial_glow(w: int, h: int, cx: float, cy: float, radius: float,
                color: tuple[int, int, int], strength: float) -> Image.Image:
    """低成本径向辉光：小尺寸算完再放大，避免逐像素开销。"""
    sw, sh = 240, max(1, round(240 * h / w))
    small = Image.new("RGB", (sw, sh), (0, 0, 0))
    px = small.load()
    scx, scy, sr = cx * sw, cy * sh, radius * sw
    for y in range(sh):
        dy = y - scy
        for x in range(sw):
            dx = x - scx
            dist = math.hypot(dx, dy) / sr
            if dist >= 1:
                continue
            fade = (1 - dist) ** 2 * strength
            px[x, y] = (
                int(color[0] * fade),
                int(color[1] * fade),
                int(color[2] * fade),
            )
    return small.resize((w, h), Image.BICUBIC)


def draw_tracked_text(d, xy, text, font, fill, tracking_px):
    """字距tracking的文本绘制（PIL 无原生字距支持）。"""
    x, y = xy
    for ch in text:
        d.text((x, y), ch, font=font, fill=fill)
        x += d.textlength(ch, font=font) + tracking_px
    return x


def make_og_cover(name: str, w: int = 1200, h: int = 630) -> Image.Image:
    img = Image.new("RGB", (w, h), BLACK)

    # 深蓝辉光（右上 + 左下），仍属四色体系
    glow = Image.new("RGB", (w, h), (0, 0, 0))
    for cx, cy, r, strength in ((0.82, -0.05, 0.62, 0.55), (-0.05, 1.05, 0.50, 0.32)):
        glow = ImageChops.add(glow, radial_glow(w, h, cx, cy, r, BLUE, strength))
    img = ImageChops.add(img, glow)

    d = ImageDraw.Draw(img)

    # 64px 网格（极淡）
    for x in range(0, w, 64):
        d.line([(x, 0), (x, h)], fill=(255, 255, 255, 10), width=1)
    for y in range(0, h, 64):
        d.line([(0, y), (w, y)], fill=(255, 255, 255, 10), width=1)

    # 右侧大号标记水印（低透明度，纯装饰）
    wm_size = int(h * 0.78)
    wm = draw_mark(wm_size)
    alpha = wm.getchannel("A").point(lambda v: int(v * 0.13))
    wm.putalpha(alpha)
    img.paste(wm, (w - wm_size - 34, (h - wm_size) // 2), wm)

    mark = draw_mark(112)
    img.paste(mark, (86, 78), mark)

    d.text((86, 236), name, font=_font(FONT_BOLD, 76), fill=WHITE)
    d.line([(88, 350), (196, 350)], fill=BLUE_BRIGHT, width=4)
    draw_tracked_text(d, (86, 382), "WINDOWS MIGRATION · AI COPILOT", _font(FONT_MONO, 24),
                      BLUE_BRIGHT, 3.2)
    d.text((86, 440), "迁移 · 优化 · 自动化，把电脑交给 AI", font=_font(FONT_CJK, 28), fill=(206, 206, 210))
    d.text((86, 512), "Windows 10 / 11", font=_font(FONT_MONO, 22), fill=(150, 150, 155))
    return img


def main() -> None:
    here = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(here / "src/main/resources/static"))
    ap.add_argument("--name", default="WinAppMigrator", help="分享图上展示的产品名")
    args = ap.parse_args()

    out = Path(args.out)
    og_dir = out / "og"
    og_dir.mkdir(parents=True, exist_ok=True)

    # 站点图标：小尺寸省略十字线，保证在 16px 下依然清晰
    icon_sizes = [16, 24, 32, 48, 64]
    frames = []
    for s in icon_sizes:
        base = Image.new("RGBA", (s, s), BLACK)
        mark = draw_mark(s, with_cross=s >= 32)
        base.alpha_composite(mark)
        frames.append(base.convert("RGB"))
    frames[-1].save(out / "favicon.ico", format="ICO",
                    sizes=[(s, s) for s in icon_sizes])
    print("written:", out / "favicon.ico")

    for s in (180, 192, 512):
        base = Image.new("RGBA", (s, s), BLACK)
        base.alpha_composite(draw_mark(s))
        name = "apple-touch-icon.png" if s == 180 else f"icon-{s}.png"
        base.convert("RGB").save(out / name, format="PNG", optimize=True)
        print("written:", out / name)

    cover = make_og_cover(args.name)
    cover.save(og_dir / "og-cover.png", format="PNG", optimize=True)
    print("written:", og_dir / "og-cover.png")


if __name__ == "__main__":
    main()
