# -*- coding: utf-8 -*-
"""验证：官网「关于我们 / 联系我们」页的 GitHub 联系方式使用官方 GitHub 图标。

检查项：
  1) 模板结构：每个联系方式按类型选图标（github / mail / link 三分支），不再共用信封；
  2) 图标本体：GitHub 分支的 path 与 GitHub 官方 Octicons「mark-github」路径逐字一致；
  3) 语义模拟：对真实数据（邮箱 mailto:… / GitHub https://github.com/zhuzhu）跑一遍
     模板里的取值表达式，确认分别落到 mail 与 github 分支；
  4) 渲染：用 QSvgRenderer 把三个分支的 SVG 渲染成 PNG，确认能正常画出来（非空、非全透明），
     并输出预览图供人工核对。
"""
import io
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

FAILS, PASSES, SKIPS = [], [], []


def check(name, cond, detail=""):
    if cond:
        PASSES.append(name)
        print(f"[PASS] {name}" + (f" — {detail}" if detail else ""))
    else:
        FAILS.append(name)
        print(f"[FAIL] {name}" + (f" — {detail}" if detail else ""))


HERE = os.path.dirname(os.path.abspath(__file__))
TPL = os.path.join(HERE, "..", "update-server", "src", "main", "resources",
                   "templates", "about.html")
tpl = io.open(TPL, encoding="utf-8").read()

# GitHub 官方图标（Octicons v19 mark-github, 24×24）——必须逐字一致，避免"近似图标"
OFFICIAL_GITHUB = (
    "M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258."
    "82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 "
    "3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 "
    "1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-"
    "5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 "
    "0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 "
    "2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 "
    "1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 "
    "2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-"
    "5.373-12-12-12")

print("--- 1. 模板结构 ---")
check("联系方式图标改为按类型分支（th:switch）",
      'th:switch="${ck}"' in tpl and "th:case=\"'github'\"" in tpl)
check("保留邮件分支（信封图标）", "th:case=\"'mail'\"" in tpl
      and 'd="M4 6h16v12H4z"' in tpl)
check("提供兜底分支（通用链接图标）", 'th:case="*"' in tpl)
check("类型判定同时看 href 与 label（GitHub 写法多样也能命中）",
      "#strings.contains(#strings.toLowerCase(c.href), 'github')" in tpl
      and "#strings.contains(#strings.toLowerCase(c.label), 'github')" in tpl)
check("旧的『所有联系方式共用信封图标』已移除",
      tpl.count('d="M4 6h16v12H4z"') == 1, f"信封出现 {tpl.count('d=\"M4 6h16v12H4z\"')} 次")

print()
print("--- 2. 图标本体（GitHub 官方 Octicon） ---")
check("GitHub 分支使用官方 mark-github 路径（逐字一致）",
      OFFICIAL_GITHUB in tpl, f"路径长度 {len(OFFICIAL_GITHUB)}")
check("品牌图形按实心绘制（fill=currentColor，不描边）",
      re.search(r'th:case="\'github\'"[^>]*fill="currentColor"', tpl) is not None)
check("仍用 currentColor 继承主题色（深/浅色主题自适应）",
      'fill="currentColor"' in tpl)

print()
print("--- 3. 语义模拟（用真实联系方式数据跑模板表达式） ---")


def _thymeleaf_key(href: str, label: str) -> str:
    """复刻模板里的取值表达式（SpEL 等价逻辑），用于断言分支选择正确。"""
    h, l = (href or "").lower(), (label or "").lower()
    if "github" in h or "github" in l:
        return "github"
    if h.startswith("mailto:") or "邮箱" in (label or ""):
        return "mail"
    return "link"


CASES = [
    ("mailto:admin@winappmigrator.com", "邮箱", "mail"),
    ("https://github.com/zhuzhu", "GitHub", "github"),
    ("https://github.com/zhuzhu", "代码仓库", "github"),        # label 不含 github 也能命中
    ("mailto:a@b.com", "GitHub", "github"),                    # label 优先于 mailto 的判断
    ("https://weibo.com/x", "微博", "link"),
]
for href, label, want in CASES:
    got = _thymeleaf_key(href, label)
    check(f"分支判定 {label} / {href[:28]} → {want}", got == want, f"得到 {got}")

print()
print("--- 4. SVG 渲染检查 ---")
try:
    from PyQt6.QtCore import QByteArray, QRectF  # noqa: E402
    from PyQt6.QtGui import QColor, QImage, QPainter  # noqa: E402
    from PyQt6.QtSvg import QSvgRenderer  # noqa: E402
    from PyQt6.QtWidgets import QApplication  # noqa: E402

    app = QApplication.instance() or QApplication([])
    svgs = re.findall(r'<svg th:case="([^"]+)"(.*?)</svg>', tpl, re.S)
    check("模板内解析出 3 个图标分支", len(svgs) == 3, f"{[s[0] for s in svgs]}")
    shots = {}
    for case, body in svgs:
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" ' + body.strip() + "</svg>")
        svg = re.sub(r'th:[a-z]+="[^"]*"', "", svg)      # 去掉 Thymeleaf 属性再渲染
        # currentColor 在裸 SVG 渲染器里解析为黑色（实际网页里由 CSS color 继承主题色），
        # 故渲染前替换为浅色，才能在纯黑画布上统计出图标像素。
        svg = svg.replace("currentColor", "#F5F5F5")
        r = QSvgRenderer(QByteArray(svg.encode("utf-8")))
        img = QImage(96, 96, QImage.Format.Format_ARGB32)
        img.fill(QColor("#000000"))
        p = QPainter(img)
        r.render(p, QRectF(0, 0, 96, 96))
        p.end()
        # 统计"浅色像素"（图标以 currentColor=#F5F5F5 画在纯黑底上）
        lit = sum(1 for y in range(0, 96, 2) for x in range(0, 96, 2)
                  if QColor(img.pixel(x, y)).lightness() > 60)
        shots[case] = (img, lit)
        _safe = {"'github'": "github", "'mail'": "mail", "*": "link"}.get(case, "icon")
        out = os.path.join(HERE, f"_verify_icon_{_safe}.png")
        img.save(out)
        check(f"图标分支 {case} 可正常渲染（有内容像素）", lit > 20,
              f"lit={lit} → {os.path.basename(out)}")
    gimg, _l = shots["'github'"]
    # GitHub mark 的特征：左下/右下有"腿"，整体上下都应有内容 → 检查四象限均非空
    quads = []
    for qx, qy in ((0, 0), (1, 0), (0, 1), (1, 1)):
        n = sum(1 for y in range(qy * 48, qy * 48 + 48, 2)
                for x in range(qx * 48, qx * 48 + 48, 2)
                if QColor(gimg.pixel(x, y)).lightness() > 60)
        quads.append(n)
    check("GitHub mark 四象限均有笔画（非残缺/非空图形）", all(q > 4 for q in quads),
          f"象限像素={quads}")
except ImportError as e:   # noqa: BLE001
    SKIPS.append("SVG 渲染检查")
    print(f"[SKIP] SVG 渲染检查 — {e}")

print()
print(f"[SUMMARY] PASS={len(PASSES)} FAIL={len(FAILS)} SKIP={len(SKIPS)}")
if FAILS:
    print("[FAILURES]")
    for f in FAILS:
        print("  -", f)
    print("HAS FAILURES")
    print("EXIT=1")
    sys.exit(1)
print("ALL PASS")
print("EXIT=0")
