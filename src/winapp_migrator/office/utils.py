# -*- coding: utf-8 -*-
"""office 公共工具：style 合并解析、路径解析、图片尺寸适配。

约定：各 builder 接收 path 与 style（dict），resolve_style 负责把
theme 色板 + 旧字段(base_color/theme_color/text_color/...)映射 + 类型钳制 合并为统一
tokens，生成逻辑只读 tokens，不直接读 style，保证字段覆盖行为一致、无硬编码散落。
"""

from pathlib import Path

from . import theme

# 兼容转发：布尔解析工具统一收口在 theme.to_flag，这里提供别名便于调用方阅读。
to_flag = theme.to_flag


def resolve_style(style, font_default=theme.DEFAULT_FONT) -> dict:
    """把用户 style 合并成稳定 tokens（含旧字段兼容映射）。

    返回键：theme_name, primary, deep, accent, ink, soft, bg, band, chart,
            font, align, line_spacing, title_size, body_size。
    数值字段畸形一律回退默认并钳制。
    """
    s = dict(style or {})
    pal = theme.pick_theme(s.get("theme"))
    primary = theme.hex_color(
        s.get("base_color") or s.get("theme_color") or s.get("heading_color")
        or pal["primary"], pal["primary"])
    ink = theme.hex_color(s.get("text_color") or s.get("dark_color")
                          or s.get("ink") or pal["ink"], pal["ink"])
    return {
        "theme_name": str(s.get("theme") or theme.DEFAULT_THEME),
        "primary": primary,
        "deep": theme.hex_color(s.get("deep") or pal["deep"], pal["deep"]),
        "accent": theme.hex_color(s.get("accent") or pal["accent"], pal["accent"]),
        "ink": ink,
        "soft": theme.hex_color(s.get("soft") or pal["soft"], pal["soft"]),
        "bg": theme.hex_color(s.get("bg_color") or s.get("bg") or pal["bg"], pal["bg"]),
        "band": theme.hex_color(s.get("band_fill") or s.get("band") or pal["band"], pal["band"]),
        "chart": [theme.hex_color(c, pal["chart"][i % len(pal["chart"])])
                  for i, c in enumerate(pal["chart"])],
        "font": str(s.get("font_name") or font_default),
        "align": str(s.get("align") or "left"),
        "line_spacing": theme.to_float(s.get("line_spacing"), 1.5, 1.0, 3.0),
        "title_size": theme.to_int(s.get("title_size"), 22, 8, 72),
        "body_size": theme.to_int(s.get("body_size"), 11, 6, 48),
        "page": str(s.get("page") or "portrait").lower(),
    }


def resolve_path(path: str, workdir: str = "") -> Path:
    """把 path 解析为绝对路径：已是绝对路径直接用；否则基于 workdir（缺省当前目录）。"""
    p = str(path or "").strip()
    if not p:
        raise ValueError("缺少保存路径 path")
    pth = Path(p).expanduser()
    if pth.is_absolute():
        return pth
    base = Path(workdir).expanduser() if str(workdir or "").strip() else Path.cwd()
    return base / pth


def _pil_size(img_path: str):
    """读图片像素尺寸；PIL 缺失/解析失败返回 (1.0, 1.0)（按近似 16:9 保底）。"""
    try:
        from PIL import Image
        with Image.open(img_path) as im:
            w, h = im.size
            return float(max(w, 1)), float(max(h, 1))
    except Exception:
        return 960.0, 540.0


def fit_scale(img_path: str, max_w: float, max_h: float):
    """等比缩放：返回 (scale, w, h)，scale=缩放系数，w/h=原像素宽高。"""
    w, h = _pil_size(img_path)
    scale = min(1.0, max_w / w if w else 1.0, max_h / h if h else 1.0)
    if max_w <= 0 or max_h <= 0:
        scale = 1.0
    return scale, w, h


def cell_value(v):
    """单元格值规范化：纯数字字符串转数值，支持 dict {v, format} 携带数字格式。

    返回 (value, number_format)；number_format 为空表示不特殊处理。
    """
    if isinstance(v, dict):
        inner = v.get("v", v.get("value", ""))
        fmt = str(v.get("format") or v.get("fmt") or "")
        return cell_value(inner)[0], fmt
    if isinstance(v, bool) or isinstance(v, (int, float)):
        return v, ""
    s = str(v).strip() if v is not None else ""
    if s == "":
        return None, ""
    try:
        if s.isdigit() or (s.replace(".", "", 1).isdigit() and s.count(".") <= 1):
            return float(s) if "." in s else int(s), ""
    except (ValueError, AttributeError):
        pass
    return s, ""
