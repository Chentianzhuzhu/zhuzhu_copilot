# -*- coding: utf-8 -*-
"""AI 厂商官方品牌矢量图标（模型选择下拉 / 服务商接入页共用）。

- 图标本体为各厂商**官方品牌 SVG**（来源与许可见 ``brand_svg_data``），
  非自绘图形；统一以 ``currentColor`` 占位，颜色由调用方注入，深浅主题与
  选中反白（蓝底白图标）都直接复用；
- 「识别哪家厂商」不靠调用方硬编码，由本表的关键字规则推断：新增品牌只需在
  ``_BRAND_RULES`` 登记一行 + 在 ``brand_svg_data`` 补一个官方 SVG，调用方零改动；
- 未命中任何品牌时画中性通用占位图标（圆角方块+圆点，不冒充任何品牌），绝不留白。

推断三类线索，优先级 **模型名 > 服务商名 > 接口地址**：聚合平台（火山方舟 /
硅基流动 …）同一服务商会挂多家模型，下拉逐行展示时以模型名识别最准；服务商
卡片则走 ``brand_for_provider``（显式 brand 字段 > 名称 > URL > 模型名）。
"""
from __future__ import annotations

from functools import lru_cache

from PyQt6.QtCore import QByteArray, QRectF, Qt
from PyQt6.QtGui import QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer

from zhuzhu_Copilot.ui import brand_svg_data

# ---------------------------------------------------------------------------
# 品牌识别规则：(品牌键, 服务商名关键字, 接口地址关键字, 模型名关键字)
# 全部按小写子串匹配；同一线索内按本表顺序裁决，故特化品牌放在通用品牌之前。
# ---------------------------------------------------------------------------
_BRAND_RULES = (
    ("agnes",
     ("agnes",),
     ("agnes-ai.cn", "agnes"),
     ("agnes",)),
    ("deepseek",
     ("deepseek",),
     ("deepseek.com", "deepseek"),
     ("deepseek",)),
    # 火山方舟 = 字节跳动豆包旗下云平台，平台卡片身份归豆包；其挂售的第三方
    # 模型（deepseek/glm/minimax…）在下拉里仍按模型名各自识别（模型线索优先）。
    ("doubao",
     ("豆包", "doubao", "火山", "volc", "volcano", "ark", "字节", "bytedance"),
     ("volces.com", "volcengine", "bytedance"),
     ("doubao", "ark-code")),
    ("qwen",
     ("千问", "通义", "qwen", "dashscope", "阿里", "aliyun"),
     ("dashscope", "aliyuncs.com"),
     ("qwen",)),
    ("zhipu",
     ("智谱", "清言", "zhipu", "bigmodel", "glm", "chatglm"),
     ("bigmodel.cn", "bigmodel"),
     ("glm", "chatglm")),
    ("kimi",
     ("kimi", "月之暗面", "moonshot"),
     ("moonshot.cn", "moonshot"),
     ("kimi",)),
    ("minimax",
     ("minimax", "mini-max", "海螺"),
     ("minimax.io", "minimaxi.com", "minimax"),
     ("minimax", "abab")),
    ("grok",
     ("grok", "xai", "x.ai"),
     ("x.ai", "xai"),
     ("grok",)),
    ("gemini",
     ("gemini", "谷歌", "google"),
     ("generativelanguage", "googleapis"),
     ("gemini",)),
    ("openai",
     ("openai", "chatgpt", "gpt"),
     ("openai.com", "openai"),
     ("gpt", "chatgpt")),
    ("claude",
     ("anthropic", "claude", "克劳德"),
     ("anthropic.com", "anthropic"),
     ("claude",)),
)

KNOWN_BRANDS = frozenset(_row[0] for _row in _BRAND_RULES)

# 未识别品牌的中性占位：圆角方块 + 中心圆点（UI 控件，不指向任何厂商）
_GENERIC_SVG = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="1.8" xmlns="http://www.w3.org/2000/svg">'
    '<rect x="4.2" y="4.2" width="15.6" height="15.6" rx="4.4"/>'
    '<circle cx="12" cy="12" r="2.6" fill="currentColor" stroke="none"/></svg>')


def _hit(keywords: tuple, text: str) -> bool:
    return bool(text) and any(kw in text for kw in keywords)


def brand_for(model: str = "", provider_name: str = "", base_url: str = "") -> str:
    """按 模型名 → 服务商名 → 接口地址 的优先级推断品牌键；全部未命中返回 ""。

    输入大小写不敏感（内部统一小写）。供模型下拉逐行使用：同一聚合服务商下的
    deepseek / glm / doubao 等模型能各归各家。
    """
    # 线索优先级：模型名 > 服务商名 > 接口地址（每项内层再按规则表顺序裁决）
    cues = (
        ((str(model or "")).lower(), 3),
        ((str(provider_name or "")).lower(), 1),
        ((str(base_url or "")).lower(), 2),
    )
    for cue, col in cues:
        if not cue:
            continue
        for row in _BRAND_RULES:
            if _hit(row[col], cue):
                return row[0]
    return ""


def brand_for_provider(p: dict) -> str:
    """推断一张服务商卡片/一条预设的品牌。

    优先级：数据里显式声明的 ``brand``（预设可钉死身份，且需为已知品牌）
    > 服务商名 > 接口地址 > 名下任一模型名。全未命中返回 ""（UI 画通用占位）。
    """
    if not isinstance(p, dict):
        return ""
    explicit = str(p.get("brand") or "").strip().lower()
    if explicit in KNOWN_BRANDS:
        return explicit
    name = str(p.get("name") or "")
    url = str(p.get("base_url") or "")
    brand = brand_for("", name, url)
    if brand:
        return brand
    for m in (p.get("models") or []):
        brand = brand_for(str(m or ""), name, url)
        if brand:
            return brand
    return ""


# ---------------------------------------------------------------------------
# 官方 SVG 渲染：颜色占位替换 → QSvgRenderer 栅格化（按 品牌/尺寸/颜色 缓存）
# ---------------------------------------------------------------------------
@lru_cache(maxsize=512)
def brand_icon(brand: str, size: int = 16, color: str = "#9BA3B0") -> QIcon:
    """官方品牌矢量图标（按 品牌/尺寸/颜色 缓存；换主题颜色自动得到新图）。

    仅可在 GUI 线程调用（内部构造 QPixmap/QIcon）。未知品牌走中性通用占位，
    保证调用方永远拿到非空图标。
    """
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    # SVG 内统一以 currentColor 占位，这里替换为调用方注入的当前主题色
    r = QSvgRenderer(QByteArray(
        (brand_svg_data.BRAND_SVG.get(brand) or _GENERIC_SVG)
        .replace("currentColor", color).encode("utf-8")))
    if not r.isValid():
        r = QSvgRenderer(QByteArray(_GENERIC_SVG.replace(
            "currentColor", color).encode("utf-8")))
    r.render(p, QRectF(0, 0, size, size))
    p.end()
    return QIcon(pm)
