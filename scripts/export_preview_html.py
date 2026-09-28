# -*- coding: utf-8 -*-
"""把 Office 文件的「保真预览 HTML」导出为独立文件（可双击用浏览器打开）。

用途：
- 不依赖应用与 QtWebEngine，直接用系统浏览器评估预览保真度（样式/图片/动画/放映）；
- 也便于把渲染问题单独发给他人复现。

用法：
    python scripts/export_preview_html.py <文档路径> [输出路径]
    python scripts/export_preview_html.py            # 导出 doc-gen 全部示例文件
"""
from zhuzhu_Copilot import app_identity
import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from zhuzhu_Copilot.office import preview  # noqa: E402

EX = os.path.join(str(app_identity.data_root()), "agent", "skills",
                  "doc-gen", "examples")
OUT = os.path.join(os.path.expanduser("~"), "Desktop", "office_preview")
THEMES = {
    "dark": {"bg": "#000000", "card": "#1E1E1E", "text": "#F5F5F5",
             "dim": "#9A9A9A", "border": "#2A2A2A", "accent": "#1F3A5F"},
    "light": {"bg": "#FFFFFF", "card": "#F7F8FA", "text": "#1A1A1A",
              "dim": "#6B7280", "border": "#DDE1E6", "accent": "#1F3A5F"},
}


def export(path: str, out_dir: str, theme_name: str = "light") -> str:
    html = preview.render_office_html(path, theme=THEMES[theme_name])
    if not html:
        return ""
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(path))[0]
    out = os.path.join(out_dir, f"{base}.{theme_name}.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    return out


if __name__ == "__main__":
    args = sys.argv[1:]
    theme = "light"
    if args and args[0] in THEMES:
        theme = args.pop(0)
    if args:
        path = args[0]
        out = args[1] if len(args) > 1 else export(path, OUT, theme)
        print(out or "无法渲染（不支持的类型或文件不存在）")
        sys.exit(0 if out else 1)
    names = sorted(os.listdir(EX)) if os.path.isdir(EX) else []
    for n in names:
        p = os.path.join(EX, n)
        out = export(p, OUT, theme)
        print(f"[ok] {out}" if out else f"[skip] {n}")
