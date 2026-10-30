# -*- coding: utf-8 -*-
"""生成 doc-gen 技能随附的图标资源与三个完整样式示例文件。

运行（dev）：
    python src/zhuzhu_Copilot/skills/doc-gen/scripts/gen_examples.py
产物：
    skills/doc-gen/assets/icons/*.png            扁平图标（可复用资源）
    skills/doc-gen/assets/examples/示例_完整样式_演示.pptx
    skills/doc-gen/assets/examples/示例_完整样式_报告.docx
    skills/doc-gen/assets/examples/示例_完整样式_工作簿.xlsx
"""
import math
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
SKILL = _HERE.parents[1]
_SRC = _HERE.parents[4]  # .../src
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from PIL import Image, ImageDraw  # noqa: E402

from zhuzhu_Copilot.office import build_docx, build_pptx, build_xlsx  # noqa: E402
from zhuzhu_Copilot.office import read_document  # noqa: E402
from zhuzhu_Copilot.office.preview import render_office_html  # noqa: E402

ICONS = SKILL / "assets" / "icons"
EXAMPLES = SKILL / "assets" / "examples"
ICONS.mkdir(parents=True, exist_ok=True)
EXAMPLES.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# 1) 扁平图标（透明底 PNG，256x256）
# ---------------------------------------------------------------------------
def _star(path: Path):
    s = 256
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    cx = cy = s / 2
    R, r = 104, 42
    pts = []
    for i in range(10):
        ang = -math.pi / 2 + i * math.pi / 5
        rad = R if i % 2 == 0 else r
        pts.append((cx + rad * math.cos(ang), cy + rad * math.sin(ang)))
    d.polygon(pts, fill=(224, 168, 42, 255))
    im.save(path)


def _bars(path: Path):
    s = 256
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for i, h in enumerate((70, 120, 170)):
        x = 48 + i * 60
        d.rounded_rectangle((x, s - 40 - h, x + 40, s - 40),
                            radius=8, fill=(31, 56, 100, 255))
    d.line((36, s - 40, s - 28, s - 40), fill=(80, 80, 80, 255), width=6)
    im.save(path)


def _target(path: Path):
    s = 256
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    c = (s // 2, s // 2)
    d.ellipse((28, 28, s - 28, s - 28), fill=(31, 56, 100, 255))
    d.ellipse((62, 62, s - 62, s - 62), fill=(255, 255, 255, 255))
    d.ellipse((96, 96, s - 96, s - 96), fill=(198, 60, 60, 255))
    im.save(path)


def _check(path: Path):
    s = 256
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((24, 24, s - 24, s - 24), radius=40,
                        fill=(46, 125, 78, 255))
    d.line((72, 132, 112, 176), fill=(255, 255, 255, 255), width=20)
    d.line((112, 176, 184, 88), fill=(255, 255, 255, 255), width=20)
    im.save(path)


IC = {"star": _star, "bars": _bars, "target": _target, "check": _check}
for name, fn in IC.items():
    fn(ICONS / f"{name}.png")
print("icons ->", ICONS)


# ---------------------------------------------------------------------------
# 2) PPT 示例（封面自动；目录 + 要点 + 图表 + 卡片 + 表格 + 图文页；动画/切换）
# ---------------------------------------------------------------------------
ppt = EXAMPLES / "示例_完整样式_演示.pptx"
slides = [
    {"title": "目录", "bullets": ["一、项目背景与目标", "二、关键数据表现",
        "三、方案与指标", "四、结论与建议"]},
    {"title": "一、项目背景与目标",
     "bullets": ["业务规模扩大，传统流程效率成为瓶颈。",
                 "目标：核心流程线上化、自动化，提升效率 30% 以上。",
                 "同时降低运营成本、提高用户满意度。"],
     "cards": [{"title": "+30%", "desc": "效率提升目标"},
               {"title": "-15%", "desc": "成本下降目标"},
               {"title": "94%", "desc": "满意度目标"}]},
    {"title": "二、关键数据表现",
     "chart": {"type": "column", "title": "四季度业务量",
               "labels": ["Q1", "Q2", "Q3", "Q4"], "values": [120, 165, 210, 268]},
     "bullets": ["四个季度持续增长，Q4 业务量达到 268。"]},
    {"title": "三、关键指标对照",
     "table": {"header": ["指标", "目标", "实际", "结论"],
               "rows": [["效率提升", "+20%", "+33%", "超额"],
                        ["成本下降", "-10%", "-14%", "达成"],
                        ["满意度", "90%", "94%", "超额"]]}},
    {"title": "四、结论与建议",
     "layout": "left_image", "image": {"path": str(ICONS / "check.png"), "width": 4.2},
     "bullets": ["各项关键指标均达成或超额完成。",
                 "建议扩大推广范围，并持续优化自动化流程。",
                 "下阶段聚焦数据看板与智能预警建设。"]},
]
build_pptx(str(ppt), "办公增强能力演示", slides,
           {"theme": "tech", "transition": "fade", "footer": "zhuzhu Copilot · doc-gen"})
print("pptx ->", ppt)


# ---------------------------------------------------------------------------
# 3) Word 示例（封面 + 目录 + 标题/段落/列表/表格/引用/图片 + 页眉页脚）
# ---------------------------------------------------------------------------
doc = EXAMPLES / "示例_完整样式_报告.docx"
paras = [
    "[toc]",
    "# 一、项目概述",
    "本报告说明项目的背景、进展、关键指标与下步计划，供管理层决策参考。",
    "## 1.1 背景",
    "- 业务规模持续扩大，传统人工流程效率偏低。",
    "- 数据分散，缺少统一的可视化看板。",
    "## 1.2 目标",
    "1. 核心流程线上化、自动化，效率提升 30% 以上。",
    "2. 运营成本下降约 15%。",
    "3. 用户满意度提升至 94%。",
    "# 二、进展与指标",
    "| 指标 | 目标 | 实际 | 结论 |",
    "| --- | --- | --- | --- |",
    "| 效率提升 | +20% | +33% | 超额 |",
    "| 成本下降 | -10% | -14% | 达成 |",
    "| 满意度 | 90% | 94% | 超额 |",
    "> 整体进度快于计划约一个月，各项关键指标均达成或超额完成。",
    "# 三、结论与建议",
    "建议扩大推广范围，持续优化自动化流程，并建设统一数据看板与智能预警能力。",
]
build_docx(str(doc), "项目阶段报告", paras,
           images=[str(ICONS / "target.png")],
           style={"theme": "business", "header_text": "项目阶段报告",
                  "cover": {"subtitle": "办公增强能力示例", "author": "zhuzhu Copilot",
                            "date": "2026-09"}})
print("docx ->", doc)


# ---------------------------------------------------------------------------
# 4) Excel 示例（参数 + 明细[公式/数据条/色阶/合计] + 汇总[图表]）
# ---------------------------------------------------------------------------
xls = EXAMPLES / "示例_完整样式_工作簿.xlsx"
sheets = [
    {"name": "参数",
     "rows": [["参数", "取值", "说明"],
              ["目标增长率", 0.2, "年度目标"],
              ["基准业务量", 100, "Q1 基准"]],
     "column_widths": [14, 12, 20]},
    {"name": "明细",
     "rows": [["季度", "目标", "实际", "达成率"],
              ["Q1", 100, 103, "=C2/B2"],
              ["Q2", 140, 148, "=C3/B3"],
              ["Q3", 180, 182, "=C4/B4"],
              ["Q4", 220, 240, "=C5/B5"],
              ["合计", "~sum", "~sum", "=C6/B6"]],
     "format": {"data_bars": [3], "color_scale": [4],
                "highlight": {"max": [3], "min": [3]}},
     "column_widths": [10, 12, 12, 12]},
    {"name": "汇总",
     "rows": [["指标", "数值"],
              ["平均达成率", "=AVERAGE(明细!D2:D5)"],
              ["最大业务量", "=MAX(明细!C2:C5)"]],
     "charts": [{"type": "column", "title": "目标 vs 实际",
                 "labels": ["Q1", "Q2", "Q3", "Q4"],
                 "values": [103, 148, 182, 240]}],
     "column_widths": [14, 14]},
]
build_xlsx(str(xls), sheets, {"theme": "business"})
print("xlsx ->", xls)


# ---------------------------------------------------------------------------
# 5) 校验：读取 + 保真渲染
# ---------------------------------------------------------------------------
for f, marker in ((ppt, "__deckStep"), (doc, "项目阶段报告"), (xls, "目标 vs 实际")):
    res = read_document(str(f))
    html = render_office_html(str(f))
    print(f"[verify] {f.name}: read_chars={len(res.get('text',''))} "
          f"html={len(html or '')} marker_ok={marker in (html or '')}")
print("DONE")
