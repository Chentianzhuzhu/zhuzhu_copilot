# -*- coding: utf-8 -*-
"""设计令牌：主题色板与通用数值/颜色工具。

设计目标（对应"生成结果模板感强"问题）：
1. 单一数据源：所有配色、默认字号、安全边距集中在此，逻辑无散落硬编码。
2. 主题结构统一：primary/deep/accent/ink/soft/bg/band/chart，各生成器按统一语义取色，
   使 Word/PPT/Excel 观感一致（品牌感）而不再各写一套默认色。
3. 兼容历史 style 参数：base_color/theme_color/heading_color/text_color 等旧字段仍有效，
   映射到新 token，避免破坏已训练好的 agent 调用习惯。
"""

DEFAULT_FONT = "微软雅黑"

# 每主题字段含义：
#   primary 主色（表头/标题/封面主块）   deep 主色加深（封面层次/底部色带）
#   accent  强调色（装饰竖条/分隔线/高亮，与 primary 成对出现）
#   ink     正文文字色                    soft  弱化文字/辅助色
#   bg      页面浅底色（卡片底/页面）     band  表格隔行底/浅块
#   chart   图表/卡片轮换色板
THEMES = {
    "business":   {"primary": "1F3864", "deep": "131F3A", "accent": "D6A23C",
                   "ink": "2F3642", "soft": "718096", "bg": "F6F8FB", "band": "EDF1F8",
                   "chart": ["1F3864", "D6A23C", "2E7D8A", "6B7FD7", "C65D4B", "4C9F70"]},
    "black-gold": {"primary": "1A1A1A", "deep": "000000", "accent": "C9A227",
                   "ink": "333333", "soft": "8C8C8C", "bg": "FAFAF7", "band": "F2EDDE",
                   "chart": ["C9A227", "1A1A1A", "8C6D1F", "5B4636", "7A7A7A", "3E5C58"]},
    "green":      {"primary": "2E5E4E", "deep": "1B3A30", "accent": "B98A4A",
                   "ink": "38413C", "soft": "7A8B83", "bg": "F5F8F6", "band": "E6EFEA",
                   "chart": ["2E5E4E", "B98A4A", "4E8D7E", "9A7FB5", "D27D56", "6F7D58"]},
    "warm":       {"primary": "B0561A", "deep": "7A3A0E", "accent": "E0892F",
                   "ink": "4A3A2E", "soft": "9A8268", "bg": "FDF7F1", "band": "F6EBDD",
                   "chart": ["B0561A", "E0892F", "C46A34", "8A6B4A", "D9A05B", "5E8C8A"]},
    "tech":       {"primary": "0E5A8A", "deep": "083A5E", "accent": "12A8C0",
                   "ink": "26333E", "soft": "5C7A8E", "bg": "F4F9FC", "band": "E3F0F8",
                   "chart": ["0E5A8A", "12A8C0", "6C5CE7", "1E88E5", "0FAF8B", "F2A541"]},
    "vivid":      {"primary": "E4572E", "deep": "B03612", "accent": "F2A104",
                   "ink": "45332C", "soft": "937B72", "bg": "FFF8F3", "band": "FCE9DC",
                   "chart": ["E4572E", "F2A104", "C0392B", "8E5B3A", "E07B39", "2F9E8F"]},
    "purple":     {"primary": "5B2D8F", "deep": "3A1B5E", "accent": "D9C16B",
                   "ink": "3A3352", "soft": "7E74A0", "bg": "F8F6FC", "band": "EFE9F8",
                   "chart": ["5B2D8F", "D9C16B", "7B4FBF", "A65BD9", "4E7FA6", "C9648A"]},
    "pastel":     {"primary": "3D6A99", "deep": "27486B", "accent": "88B7E8",
                   "ink": "34404C", "soft": "7790A6", "bg": "F9FBFD", "band": "EAF2FA",
                   "chart": ["3D6A99", "88B7E8", "6FA287", "D8A657", "A57FB8", "D07B6A"]},
    "dark":       {"primary": "23272E", "deep": "14161B", "accent": "5D8BB5",
                   "ink": "E8EAED", "soft": "A9B2BD", "bg": "1E2228", "band": "272C35",
                   "chart": ["5D8BB5", "E0A458", "69C2A6", "C77FB4", "E06C5B", "A9B2BD"]},
    "red":        {"primary": "8C2F39", "deep": "5E1A22", "accent": "C9A227",
                   "ink": "40333A", "soft": "8A767D", "bg": "FCF8F7", "band": "F6E7E5",
                   "chart": ["8C2F39", "C9A227", "A63D4A", "6E7B5C", "9A7FB0", "D98A4A"]},
}

# style.theme 取色名（不传默认 business）
DEFAULT_THEME = "business"


def pick_theme(theme: str) -> dict:
    """按主题名取色板；未知/空回退默认。"""
    name = str(theme or "").strip().lower()
    return THEMES.get(name) or THEMES[DEFAULT_THEME]


def hex_color(value, default: str = "1F3864") -> str:
    """清洗十六进制颜色：容错 # 前缀/大写/空值/畸形 → 返回 6 位大写去 # 形式。"""
    s = str(value or "").strip().lstrip("#")
    if len(s) != 6:
        return str(default).lstrip("#").upper()
    try:
        int(s, 16)
    except ValueError:
        return str(default).lstrip("#").upper()
    return s.upper()


def mix(c: str, target: str, ratio: float) -> str:
    """颜色混合：ratio=0 保持 c，=1 变为 target。用于浅化底/深化的色阶。"""
    def _rgb(h):
        h = hex_color(h)
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    r1, g1, b1 = _rgb(c)
    r2, g2, b2 = _rgb(target)
    k = max(0.0, min(1.0, float(ratio)))
    def _f(a, b):
        return int(round(a + (b - a) * k))
    return "%02X%02X%02X" % (_f(r1, r2), _f(g1, g2), _f(b1, b2))


def lighten(c: str, ratio: float) -> str:
    """向白色混合，生成浅一档色（卡片底/表格 band）。ratio≈0.85 即 15% 原色。"""
    return mix(c, "FFFFFF", ratio)


def darken(c: str, ratio: float) -> str:
    """向黑色混合，生成深一档色。"""
    return mix(c, "000000", ratio)


def to_int(v, default: int, lo: int, hi: int) -> int:
    try:
        x = int(v)
    except (TypeError, ValueError):
        return default
    return x if lo <= x <= hi else default


def to_float(v, default: float, lo: float, hi: float) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    return x if lo <= x <= hi else default


def to_flag(v, default: bool = False) -> bool:
    if v is None:
        return default
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on", "是", "开")
    return bool(v)
