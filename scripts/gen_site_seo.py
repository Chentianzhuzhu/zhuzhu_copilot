#!/usr/bin/env python3
"""静态官网 SEO 产物生成器。

单一配置源是 `website/site.seo.json`，本脚本据此产出三样东西（均落在 `website/`，可直接上传服务器）：

  * `index.html` 里 `<!-- seo:begin -->` 与 `<!-- seo:end -->` 之间的 head 区块
    （title / description / keywords / robots / canonical / hreflang / Open Graph / Twitter / JSON-LD）
  * `robots.txt`
  * `sitemap.xml`

站点地址、品牌双名与关键词只写在配置里，页面与抓取文件由脚本推导，避免同一域名散落在多处。

用法：
  python scripts/gen_site_seo.py           # 生成并写盘
  python scripts/gen_site_seo.py --check   # 只校验产物与配置是否一致（CI 用），有漂移则退出码 1
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE_DIR = ROOT / "website"
CONFIG_PATH = SITE_DIR / "site.seo.json"
INDEX_PATH = SITE_DIR / "index.html"

BEGIN = "<!-- seo:begin -->"
END = "<!-- seo:end -->"
GENERATED = "<!-- 由 scripts/gen_site_seo.py 依据 website/site.seo.json 生成，请勿手工编辑 -->"


def load_config() -> dict:
    with CONFIG_PATH.open(encoding="utf-8") as fp:
        return json.load(fp)


def page_url(cfg: dict, path: str = "") -> str:
    """站点内页面的绝对地址；根路径保留结尾斜杠。"""
    base = str(cfg["siteUrl"]).rstrip("/")
    tail = path.strip("/")
    return f"{base}/{tail}" if tail else f"{base}/"


def absolute(cfg: dict, url: str) -> str:
    """站内相对地址补全为绝对地址，已是绝对地址则原样返回。"""
    if url.startswith(("http://", "https://", "//")):
        return url
    return page_url(cfg, url)


def esc(text: object) -> str:
    """HTML 属性值转义。"""
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def xml_esc(text: object) -> str:
    """XML 文本转义。"""
    return esc(text).replace("'", "&apos;")


def png_size(path: Path) -> tuple[int, int] | None:
    """读取 PNG 像素尺寸；用于生成 og:image:width/height，避免把尺寸写死在脚本里。"""
    if not path.is_file():
        return None
    head = path.read_bytes()[:24]
    if len(head) < 24 or head[12:16] != b"IHDR":
        return None
    return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")


def json_ld(cfg: dict) -> str:
    """结构化数据：软件信息 + 站点信息，双品牌通过 alternateName 一并声明。"""
    brand = cfg["brand"]
    url = page_url(cfg)
    graph = [
        {
            "@type": "SoftwareApplication",
            "name": brand["name"],
            "alternateName": brand["alternateName"],
            "url": url,
            "description": cfg["description"],
            "applicationCategory": brand["category"],
            "operatingSystem": brand["operatingSystem"],
            "inLanguage": cfg["lang"],
            "publisher": {"@type": "Organization", "name": brand["name"], "url": url},
        },
        {
            "@type": "WebSite",
            "name": f'{brand["name"]}（{brand["alternateName"]}）',
            "url": url,
            "inLanguage": cfg["lang"],
        },
    ]
    payload = json.dumps({"@context": "https://schema.org", "@graph": graph},
                         ensure_ascii=False, separators=(",", ":"))
    # 防止内容里的尖括号提前闭合 script 标签
    return payload.replace("<", "\\u003C").replace(">", "\\u003E")


def render_head(cfg: dict) -> str:
    url = page_url(cfg)
    brand = cfg["brand"]
    lines = [
        GENERATED,
        f"<title>{esc(cfg['title'])}</title>",
        f'<meta name="description" content="{esc(cfg["description"])}">',
        f'<meta name="keywords" content="{esc(", ".join(cfg["keywords"]))}">',
        f'<meta name="author" content="{esc(cfg["author"])}">',
        '<meta name="robots" content="index, follow, max-image-preview:large, max-snippet:-1">',
        f'<meta name="theme-color" content="{esc(cfg["themeColor"])}">',
        '<meta name="color-scheme" content="dark">',
        f'<link rel="canonical" href="{esc(url)}">',
        f'<link rel="alternate" hreflang="{esc(cfg["lang"])}" href="{esc(url)}">',
        f'<link rel="sitemap" type="application/xml" href="{esc(page_url(cfg, "sitemap.xml"))}">',
        "",
        "<!-- Open Graph -->",
        '<meta property="og:type" content="website">',
        f'<meta property="og:site_name" content="{esc(brand["name"])}">',
        f'<meta property="og:title" content="{esc(cfg["title"])}">',
        f'<meta property="og:description" content="{esc(cfg["description"])}">',
        f'<meta property="og:url" content="{esc(url)}">',
        f'<meta property="og:locale" content="{esc(cfg["locale"])}">',
    ]

    og_image = str(cfg.get("ogImage", "")).strip()
    if og_image:
        og_url = absolute(cfg, og_image)
        lines.append(f'<meta property="og:image" content="{esc(og_url)}">')
        lines.append(f'<meta property="og:image:alt" content="{esc(cfg["title"])}">')
        size = png_size(SITE_DIR / og_image)
        if size:
            lines.append(f'<meta property="og:image:width" content="{size[0]}">')
            lines.append(f'<meta property="og:image:height" content="{size[1]}">')

    lines += [
        "",
        "<!-- Twitter Card -->",
        '<meta name="twitter:card" content="summary_large_image">',
        f'<meta name="twitter:title" content="{esc(cfg["title"])}">',
        f'<meta name="twitter:description" content="{esc(cfg["description"])}">',
    ]
    if og_image:
        lines.append(f'<meta name="twitter:image" content="{esc(absolute(cfg, og_image))}">')

    lines += [
        "",
        "<!-- 结构化数据 -->",
        f'<script type="application/ld+json">{json_ld(cfg)}</script>',
    ]
    return "\n".join(lines)


def render_robots(cfg: dict) -> str:
    lines = ["User-agent: *", "Allow: /"]
    disallow = [str(item).strip() for item in cfg.get("robots", {}).get("disallow", []) if str(item).strip()]
    if disallow:
        lines.append("")
        lines.append("# 不参与收录的路径")
        lines.extend(f"Disallow: {item}" for item in disallow)
    lines += ["", f"Sitemap: {page_url(cfg, 'sitemap.xml')}", ""]
    return "\n".join(lines)


def render_sitemap(cfg: dict) -> str:
    spec = cfg.get("sitemap", {})
    # 刻意不输出 lastmod：静态站的产物需可复现，用构建时间/文件时间会让 CI 校验永远漂移
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
        "  <url>",
        f"    <loc>{xml_esc(page_url(cfg))}</loc>",
    ]
    if spec.get("changefreq"):
        lines.append(f"    <changefreq>{xml_esc(spec['changefreq'])}</changefreq>")
    if spec.get("priority"):
        lines.append(f"    <priority>{xml_esc(spec['priority'])}</priority>")
    lines += ["  </url>", "</urlset>", ""]
    return "\n".join(lines)


def splice_head(html: str, block: str) -> str:
    """只替换标记之间的内容，页面其余部分（含内联样式与正文）保持原样。"""
    missing = [mark for mark in (BEGIN, END) if mark not in html]
    if missing:
        raise SystemExit(f"[FAIL] {INDEX_PATH.name} 缺少 SEO 标记：{missing}")
    start = html.index(BEGIN) + len(BEGIN)
    end = html.index(END)
    return f"{html[:start]}\n{block}\n{html[end:]}"


def build() -> dict[Path, str]:
    """产出全部 SEO 文件的目标内容。"""
    cfg = load_config()
    if not str(cfg.get("siteUrl", "")).strip():
        raise SystemExit("[FAIL] 配置缺少 siteUrl：canonical / sitemap 必须使用绝对地址")
    html = INDEX_PATH.read_text(encoding="utf-8")
    return {
        INDEX_PATH: splice_head(html, render_head(cfg)),
        SITE_DIR / "robots.txt": render_robots(cfg),
        SITE_DIR / "sitemap.xml": render_sitemap(cfg),
    }


def newline_of(path: Path) -> str:
    """沿用目标文件既有的换行符风格。

    index.html 是 CRLF 文件；若生成时统一按 LF 写回，整份文件都会显示为改动，
    真实变更会被淹没在整文件 diff 里，评审时无法看清。新文件用 LF。
    """
    if not path.is_file():
        return "\n"
    return "\r\n" if b"\r\n" in path.read_bytes() else "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="生成静态官网 SEO 产物")
    parser.add_argument("--check", action="store_true",
                        help="只校验产物与配置是否一致（CI 用），有漂移则退出码 1")
    args = parser.parse_args()

    outputs = build()
    drifted = []
    for path, content in outputs.items():
        # read_text 会把 CRLF 归一成 LF，因此这里的比较不受换行符风格影响
        current = path.read_text(encoding="utf-8") if path.is_file() else None
        if args.check:
            if current != content:
                drifted.append(path.relative_to(ROOT).as_posix())
            continue
        if current == content:
            continue
        with path.open("w", encoding="utf-8", newline=newline_of(path)) as fp:
            fp.write(content)
        print(f"  写入 {path.relative_to(ROOT).as_posix()}")

    if args.check and drifted:
        print("[FAIL] 以下 SEO 产物与 website/site.seo.json 不一致，请执行 "
              "python scripts/gen_site_seo.py：", file=sys.stderr)
        for name in drifted:
            print(f"  - {name}", file=sys.stderr)
        return 1
    if args.check:
        print("[OK] 静态官网 SEO 产物与配置一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
