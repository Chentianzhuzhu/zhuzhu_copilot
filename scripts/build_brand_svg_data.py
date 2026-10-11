# -*- coding: utf-8 -*-
"""联网获取 11 家 AI 厂商的官方品牌 SVG，重新生成 src/.../ui/brand_svg_data.py。

数据来源（仅作品牌标识用途，商标归各权利人）：
- simple-icons（CC0）           : kimi / deepseek / qwen / minimax / gemini / claude / openai
- @lobehub/icons v5.23.0（MIT） : doubao(Mono) / agnes(Inner 去圆底版)
- svgl（MIT）                   : grok
- 智谱 z.ai 官网 logo.svg       : 提取其中白色 Z 字标（丢弃深色徽章底，便于主题注入）

用法：python scripts/build_brand_svg_data.py
新增品牌：在 SOURCES 登记来源 + 在 brand_icons._BRAND_RULES 加识别规则后重跑。
"""
import os
import re
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "src", "zhuzhu_Copilot", "ui", "brand_svg_data.py")

# brand → (下载地址, 来源库, 许可, 提取方式)
SOURCES = {
    "kimi": ("https://cdn.simpleicons.org/kimi", "simple-icons", "CC0", "svg"),
    "deepseek": ("https://cdn.simpleicons.org/deepseek", "simple-icons", "CC0", "svg"),
    "qwen": ("https://cdn.simpleicons.org/qwen", "simple-icons", "CC0", "svg"),
    "minimax": ("https://cdn.simpleicons.org/minimax", "simple-icons", "CC0", "svg"),
    "gemini": ("https://cdn.simpleicons.org/googlegemini", "simple-icons", "CC0", "svg"),
    "claude": ("https://cdn.simpleicons.org/claude", "simple-icons", "CC0", "svg"),
    "openai": ("https://cdn.jsdelivr.net/npm/simple-icons@latest/icons/openai.svg",
               "simple-icons", "CC0", "svg"),
    "grok": ("https://svgl.app/library/grok-light.svg", "svgl", "MIT", "svg"),
    "doubao": ("https://cdn.jsdelivr.net/npm/@lobehub/icons@5.23.0/es/"
               "Doubao/components/Mono.js", "lobe-icons", "MIT", "lobe"),
    # 智谱国际站 z.ai 官方 logo：原文件是「深色圆角徽章 + 白色 Z」，
    # 只提取白色 Z 字标（zai 提取器），底色留给主题
    "zhipu": ("https://z-cdn.chatglm.cn/z-ai/static/logo.svg", "z.ai 官网", "品牌标识", "zai"),
    # Agnes 官方 Mono 为实心圆底（注入淡灰后是大面积色块，与其它线性标不协调），
    # 取官方去底 Inner（手写 A 字标）
    "agnes": ("https://cdn.jsdelivr.net/npm/@lobehub/icons@5.23.0/es/"
              "AgnesAI/components/Inner.js", "lobe-icons", "MIT", "lobe"),
}
ORDER = ["agnes", "kimi", "deepseek", "doubao", "qwen", "zhipu", "minimax",
         "openai", "gemini", "grok", "claude"]

_ATTR_RE = re.compile(r'fill\s*=\s*"#[0-9A-Fa-f]{3,8}"')
_SIZE_RE = re.compile(r'\s(?:width|height)\s*=\s*"[^"]*"')
_TITLE_RE = re.compile(r"<title>.*?</title>", re.S)


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "brand-svg-builder/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8")


def normalize(svg: str) -> str:
    svg = _TITLE_RE.sub("", svg.strip())
    svg = _ATTR_RE.sub('fill="currentColor"', svg)   # 颜色统一占位，渲染期注入
    if not re.search(r"<svg[^>]*\bfill\s*=", svg):   # 部分源不写 fill（默认黑）
        svg = svg.replace("<svg", '<svg fill="currentColor"', 1)
    svg = _SIZE_RE.sub("", svg)
    return re.sub(r"\s+", " ", svg)


def from_lobe(js: str) -> str:
    """从 lobe-icons React 组件提取 viewBox 与全部 path（含 fillOpacity 层次）"""
    vb = re.search(r'viewBox:\s*"([^"]+)"', js).group(1)
    paths = []
    for m in re.finditer(r'd:\s*"((?:[^"\\]|\\.)*)"([^}]*)\}', js):
        d = m.group(1).encode("utf-8").decode("unicode_escape")
        op = re.search(r'fillOpacity:\s*"([^"]+)"', m.group(2))
        attr = f' fill-opacity="{op.group(1)}"' if op else ""
        paths.append(f'<path d="{d}"{attr}/>')
    return (f'<svg viewBox="{vb}" fill="currentColor" fill-rule="evenodd" '
            f'xmlns="http://www.w3.org/2000/svg">{"".join(paths)}</svg>')


def from_zai(svg: str) -> str:
    """从 z.ai 官方 logo 提取白色 Z 字标（class st23 的 path/polygon），丢弃徽章底"""
    vb = re.search(r'viewBox="[\s\d.]+\s+[\s\d.]+\s+([\d.]+)\s+([\d.]+)"', svg)
    w, h = (vb.group(1), vb.group(2)) if vb else ("30", "30")
    body = ""
    for tag, attrs in re.findall(r"<(path|polygon)([^>]*?)/?>", svg, re.S):
        if 'class="st23"' not in attrs:
            continue
        attrs = re.sub(r"\s+", " ", attrs)
        if tag == "polygon":
            pts = re.search(r'points="([^"]+)"', attrs).group(1).split()
            coords = " ".join(f"L{p}" for p in pts[1:])
            body += f'<path d="M{pts[0]} {coords} Z"/>'
        else:
            d = re.search(r'd="([^"]+)"', attrs, re.S).group(1)
            body += f'<path d="{d}"/>'
    if not body:
        raise RuntimeError("z.ai logo 结构变化，未提取到 Z 字标（st23 图层）")
    return (f'<svg viewBox="0 0 {w} {h}" fill="currentColor" '
            f'xmlns="http://www.w3.org/2000/svg">{body}</svg>')


_EXTRACTORS = {"lobe": from_lobe, "zai": from_zai}


def main():
    brands, meta = {}, {}
    for b in ORDER:
        url, lib, lic, kind = SOURCES[b]
        raw = fetch(url)
        extractor = _EXTRACTORS.get(kind)
        brands[b] = normalize(extractor(raw) if extractor else raw)
        meta[b] = (lib, lic, url)
        print(f"{b:8s} {len(brands[b]):5d}B  {lib}")

    lines = [
        '# -*- coding: utf-8 -*-',
        '"""11 家 AI 厂商的官方品牌矢量图标（SVG 源数据，单行压缩）。',
        '',
        '由 scripts/build_brand_svg_data.py 联网获取并标准化，勿手工编辑；',
        '更新或新增品牌后重跑该脚本，同时在 brand_icons._BRAND_RULES 登记识别规则。',
        '所有图形以 currentColor 为颜色占位，渲染期替换为调用方注入的主题色，',
        '深浅主题与选中反白均可复用。商标归各权利人所有，仅作品牌标识用途。',
        '"""',
        '',
        'BRAND_SVG = {',
    ]
    for b in ORDER:
        lib, lic, url = meta[b]
        lines.append(f'    # {b} —— {lib}（{lic}）: {url}')
        lines.append(f'    "{b}": {brands[b]!r},')
        lines.append('')
    lines.append('}')
    lines.append('')
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))
    print("written:", os.path.relpath(OUT, ROOT))


if __name__ == "__main__":
    sys.exit(main())
