"""静态官网（website/）SEO 产物回归校验。

website/ 是纯静态目录、上传即上线，没有服务端兜底：域名写错、关键词丢失、
产物与配置漂移都不会在运行时暴露。这里把三类约束钉住：

  1. 产物与单一配置源 `website/site.seo.json` 一致（重新生成不应产生任何改动）；
  2. 关键 SEO 元素齐备（canonical / Open Graph / Twitter / JSON-LD / robots / sitemap）；
  3. 双品牌（zhuzhu Copilot 与 WinAppMigrator）与关键词确实落地，且不出现 emoji。
"""
from __future__ import annotations

import importlib.util
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEBSITE = ROOT / "website"
INDEX = WEBSITE / "index.html"
ROBOTS = WEBSITE / "robots.txt"
SITEMAP = WEBSITE / "sitemap.xml"
CONFIG = WEBSITE / "site.seo.json"

EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200D\u20E3]")

CONFIG_DATA = json.loads(CONFIG.read_text(encoding="utf-8"))
BRAND = CONFIG_DATA["brand"]["name"]
ALIAS = CONFIG_DATA["brand"]["alternateName"]
HEAD = INDEX.read_text(encoding="utf-8")


def _load_generator():
    """scripts/ 不是包，按文件路径加载生成器模块。"""
    spec = importlib.util.spec_from_file_location("gen_site_seo", ROOT / "scripts/gen_site_seo.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GEN = _load_generator()


def _json_ld() -> dict:
    match = re.search(r'<script type="application/ld\+json">(.*?)</script>', HEAD, re.S)
    assert match, "index.html 缺少 JSON-LD 结构化数据"
    return json.loads(match.group(1))


def _meta(pattern: str) -> str:
    match = re.search(pattern, HEAD)
    assert match, f"index.html 缺少 {pattern}"
    return match.group(1)


def test_generated_files_match_config() -> None:
    """产物必须与配置一致：手改 HTML/robots/sitemap 而不改配置，会在这里被挡住。"""
    drifted = [
        path.relative_to(ROOT).as_posix()
        for path, content in GEN.build().items()
        if path.read_text(encoding="utf-8") != content
    ]
    assert drifted == [], f"SEO 产物与 site.seo.json 不一致：{drifted}，请执行 scripts/gen_site_seo.py"


def test_head_block_markers_present() -> None:
    """标记缺失会让生成器无法再次落位，必须先发现。"""
    assert GEN.BEGIN in HEAD and GEN.END in HEAD, "index.html 缺少 seo:begin / seo:end 标记"


def test_index_line_endings_are_consistent() -> None:
    """生成器必须沿用文件既有换行符风格。

    index.html 是 CRLF；若生成时按 LF 写回，行尾就会混用，整份文件显示为改动，
    真实变更被淹没在整文件 diff 里（本次就踩过一次）。
    """
    raw = INDEX.read_bytes()
    crlf, lf = raw.count(b"\r\n"), raw.count(b"\n")
    assert crlf in (0, lf), f"index.html 行尾混用了 CRLF 与 LF（CRLF {crlf} / LF {lf}）"


def test_canonical_matches_sitemap_location() -> None:
    canonical = _meta(r'<link rel="canonical" href="([^"]+)"')
    locations = [el.text for el in ET.parse(SITEMAP).getroot().iter("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")]
    assert locations == [canonical], f"canonical({canonical}) 与 sitemap loc({locations}) 不一致"


def test_robots_declares_sitemap() -> None:
    text = ROBOTS.read_text(encoding="utf-8")
    assert "User-agent: *" in text
    assert "Allow: /" in text
    assert f"Sitemap: {CONFIG_DATA['siteUrl'].rstrip('/')}/sitemap.xml" in text


def test_sitemap_is_valid_xml() -> None:
    root = ET.parse(SITEMAP).getroot()
    assert root.tag == "{http://www.sitemaps.org/schemas/sitemap/0.9}urlset"
    assert len(list(root)) == 1, "单页静态站应只有 1 条 sitemap 条目"


def test_keywords_and_description_cover_both_brands() -> None:
    keywords = _meta(r'<meta name="keywords" content="([^"]+)"')
    description = _meta(r'<meta name="description" content="([^"]+)"')
    for brand, text in ((BRAND, keywords), (ALIAS, keywords)):
        assert brand in text, f"keywords 未覆盖品牌 {brand}"
    assert BRAND in description and ALIAS in description, "description 未覆盖双品牌"


def test_structured_data_declares_both_names() -> None:
    app = _json_ld()["@graph"][0]
    assert app["name"] == BRAND
    assert app["alternateName"] == ALIAS, "结构化数据未声明第二品牌，搜索侧无法关联两个名字"
    assert app["applicationCategory"] == CONFIG_DATA["brand"]["category"]


def test_open_graph_and_twitter_complete() -> None:
    for prop in ("og:type", "og:site_name", "og:title", "og:description", "og:url",
                 "og:locale", "og:image", "og:image:alt", "og:image:width", "og:image:height"):
        assert f'property="{prop}"' in HEAD, f"缺少 {prop}"
    for name in ("twitter:card", "twitter:title", "twitter:description", "twitter:image"):
        assert f'name="{name}"' in HEAD, f"缺少 {name}"
    image = _meta(r'<meta property="og:image" content="([^"]+)"')
    assert image.startswith(("http://", "https://")), "og:image 必须是绝对地址，抓取方不会补全相对路径"
    assert (WEBSITE / image.split("/")[-1]).exists(), f"og:image 指向的本地文件不存在：{image}"


def test_generated_files_have_no_emoji() -> None:
    offenders = [f"{path.name}: {m.group(0)!r}"
                 for path in (INDEX, ROBOTS, SITEMAP, CONFIG)
                 for m in EMOJI_RE.finditer(path.read_text(encoding="utf-8"))]
    assert offenders == [], f"SEO 文案不允许出现 emoji：{offenders}"
