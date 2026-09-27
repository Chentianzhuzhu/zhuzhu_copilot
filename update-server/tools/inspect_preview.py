#!/usr/bin/env python3
"""检查本地预览产物的关键渲染结果（开发辅助，方便快速核对页面结构）。"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PAGES = Path(__file__).resolve().parent.parent / "target/preview/pages"

CHECKS = [
    (r"<title>(.*?)</title>", "title"),
    (r'name="description" content="(.*?)"', "description"),
    (r'rel="canonical" href="(.*?)"', "canonical"),
    (r'property="og:image" content="(.*?)"', "og:image"),
    (r'class="hero-title[^"]*"[^>]*>(.*?)</h1>', "hero h1"),
    (r'class="hero-slogan[^"]*"[^>]*>(.*?)</p>', "slogan"),
]

COUNTS = [
    ("gallery-item", "gallery-item"),
    ("video-card", 'class="video-card"'),
    ("feature-card", 'class="feature-card"'),
    ("adv-card", 'class="adv-card"'),
    ("faq-item", '<details class="faq-item"'),
    ("stat", 'class="stat"'),
]


def main() -> int:
    page = sys.argv[1] if len(sys.argv) > 1 else "index.html"
    html = (PAGES / page).read_text(encoding="utf-8")
    print(f"== {page}（{len(html)} 字节）==")

    for pattern, label in CHECKS:
        m = re.search(pattern, html, re.S)
        value = m.group(1).strip()[:120] if m else "*** 缺失 ***"
        print(f"{label:12}: {value}")

    print("-- 结构 --")
    print("  sections    :", ", ".join(re.findall(r'<section[^>]*id="([^"]+)"', html)) or "无")
    for label, token in COUNTS:
        print(f"  {label:12}: {html.count(token)}")

    leftovers = {
        "th: 指令": html.count("th:"),
        "未解析表达式": html.count("${"),
        "结构不完整": 0 if html.rstrip().endswith("</html>") else 1,
    }
    print("-- 健康检查 --")
    for label, count in leftovers.items():
        print(f"  {label:12}: {count}")
    print("  JSON-LD     :", "application/ld+json" in html)
    print("  favicon     :", "/favicon.svg" in html or "/favicon.ico" in html)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
