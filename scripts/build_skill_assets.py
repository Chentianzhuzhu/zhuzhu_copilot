# -*- coding: utf-8 -*-
"""生成 doc-gen 技能的资源与示例文件（真实产出，供技能复用与回归测试）。

产物目录：~/.zhuzhu_Copilot/agent/skills/doc-gen/
- assets/theme_guide.md        配色方案选择速查
- assets/style_cheatsheet.md   三类文档 style 参数速查
- examples/sample_report.docx  带封面/目录/表格/页眉的报告
- examples/sample_deck.pptx    含卡片/图表/表格/动画的演示
- examples/sample_book.xlsx    含表头样式/条件格式/图表的表格
- examples/sample_readme.pdf   最小可读 PDF（需 pypdf，缺失则跳过）

幂等：重复运行会覆盖产物；示例文件同时作为 office 能力的真实回归样本。
"""
from zhuzhu_Copilot import app_identity
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from zhuzhu_Copilot.office import docx_builder, pptx_builder, xlsx_builder  # noqa: E402

SKILL_DIR = os.path.join(str(app_identity.data_root()), "agent", "skills", "doc-gen")
ASSETS = os.path.join(SKILL_DIR, "assets")
EXAMPLES = os.path.join(SKILL_DIR, "examples")

THEME_GUIDE = """# 配色方案速查（style.theme）

| 主题 | 主色 | 适用场景 |
|---|---|---|
| business | #1F3864 深蓝 | 教育、学术、通用正式报告（默认） |
| black-gold | #1A1A1A + #C9A227 | 高端商务、晚会、颁奖、精品册 |
| green | #2E5E4E 墨绿 | 环保、自然、健康、农业 |
| warm | #B0561A 橙棕 | 温馨、文艺、餐饮、人文 |
| tech | #0E5A8A 科技蓝 | 产品发布、技术方案、互联网 |
| vivid | #E4572E 橙红 | 创意、营销、活动、发布会 |
| purple | #5B2D8F 紫 | 品牌、时尚、设计 |
| pastel | #3D6A99 浅蓝 | 清新、培训、轻量汇报 |
| dark | #23272E 暗色 | 暗色炫酷、影音、科技演示 |
| red | #8C2F39 朱红 | 党政、年节、庆典、表彰 |

也可直接传色值：base_color / heading_color / text_color / theme_color / header_fill / accent。
选定主题后，建议同时定 font_name（宋体/仿宋=正式文书，楷体=文艺，默认微软雅黑）。
"""

STYLE_CHEATSHEET = """# style 参数速查

## 通用
theme（配色名）、font_name（字体）、base_color/heading_color/text_color（自由色值）

## create_docx
align（left/center/right/justify）、line_spacing（1.0-2.0）、title_size、body_size、
page（portrait/landscape）、header_text（页眉）、watermark={text,color,size}、
cover={subtitle,author,date,image,style: centered|band}

## create_pptx
base_color、heading_color、text_color、accent、bg_color、title_size、body_size、
bullet_style（dot/number/arrow/check）、transition（fade/push/wipe/split/cover/pull/zoom/
dissolve/circle/diamond/blinds/checker/wheel/comb/plus/newsflash/cut/wedge/random）、
animation（默认 true）、animation_effect（全局统一效果）、anim_trigger（click/with/after）、
anim_stagger（级联毫秒，默认 160）、exit_effect（整页退场效果）、footer（页脚）、
cover={subtitle,author,date,image}
每页可覆盖：bg_color、title_color、layout（left_image/right_image/two_col/center_highlight/
hero_stats）、stats、columns、highlight、anim

## create_xlsx
theme_color、header_fill、header_color、font_name、banded、band_fill、border_color、
freeze_header、auto_filter、header_size、body_size、header_bold、bg_color

## beautify_*
docx：theme/theme_color/font_name/body_size/dark_color/align/line_spacing
pptx：theme/theme_color/font_name/body_size/bg_color
xlsx：theme/theme_color/header_fill/header_color/font_name/banded/freeze_header/auto_filter/border_color
"""


def sync_skill_md():
    """把内置 doc-gen 技能模板写入用户技能目录（单一数据源：agent_skills._BUILTIN_MD_SKILLS）。

    内置模板只在「首次运行」生成 SKILL.md，已存在的不会自动更新；本步骤保证
    技能规范升级后用户目录立即生效（技能系统实时扫描，改完即用）。
    """
    from zhuzhu_Copilot.core import agent_skills
    cfg = agent_skills._BUILTIN_MD_SKILLS["doc-gen"]
    md = (f"---\nname: doc-gen\ndescription: {cfg['description']}\n---\n\n"
          f"{cfg['instruction'].strip()}\n")
    os.makedirs(SKILL_DIR, exist_ok=True)
    path = os.path.join(SKILL_DIR, "SKILL.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"[ok] {path}  {len(md)} 字符")


def build_examples():
    os.makedirs(ASSETS, exist_ok=True)
    os.makedirs(EXAMPLES, exist_ok=True)
    with open(os.path.join(ASSETS, "theme_guide.md"), "w", encoding="utf-8") as f:
        f.write(THEME_GUIDE)
    with open(os.path.join(ASSETS, "style_cheatsheet.md"), "w", encoding="utf-8") as f:
        f.write(STYLE_CHEATSHEET)

    docx_builder.build_docx(
        os.path.join(EXAMPLES, "sample_report.docx"),
        "季度经营分析报告",
        ["# 一、总体概况",
         "本季度整体经营保持稳健增长，收入与利润均实现同比增长。",
         "## 1.1 核心指标",
         "- 营业收入：1,286 万元，同比增长 12.4%",
         "- 净利润：214 万元，同比增长 8.9%",
         "| 指标 | 本季度 | 上季度 | 环比 |",
         "| 营业收入 | 1286 | 1143 | +12.5% |",
         "| 净利润 | 214 | 196 | +9.2% |",
         "## 1.2 下一步举措",
         "1. 优化渠道结构，提升高毛利产品占比",
         "2. 强化费用管控，压降非必要开支",
         "> 说明：以上数据为示例，实际使用时请替换为真实业务数据。"],
        style={"theme": "business", "header_text": "季度经营分析报告",
               "cover": {"subtitle": "经营数据与举措", "author": "经营分析组", "date": "2026-09"}})

    pptx_builder.build_pptx(
        os.path.join(EXAMPLES, "sample_deck.pptx"),
        "产品方案汇报",
        [{"title": "项目背景", "bullets": ["市场需求快速增长，用户规模持续扩大。",
                                        "现有系统在并发与体验上已接近瓶颈。",
                                        "需要一套可扩展的新架构方案支撑未来三年。"]},
         {"title": "核心指标", "cards": [{"title": "响应时间", "desc": "P95 < 200ms"},
                                      {"title": "可用性", "desc": "99.95%"},
                                      {"title": "成本", "desc": "下降 30%"}],
          "chart": {"type": "column", "labels": ["Q1", "Q2", "Q3", "Q4"],
                    "values": [120, 180, 260, 340], "title": "季度处理量（万次）"}},
         {"title": "实施路径", "diagram": {"type": "flow",
                                        "items": ["需求梳理", "架构设计", "开发联调", "灰度发布", "全量上线"]}},
         {"title": "对比分析", "table": {"header": ["方案", "优点", "风险"],
                                      "rows": [["A 渐进式", "风险可控", "周期较长"],
                                               ["B 重构式", "彻底解决", "投入较高"]]}}],
        style={"theme": "tech", "transition": "fade",
               "cover": {"subtitle": "技术方案与实施路径", "author": "产品技术组", "date": "2026-09"}})

    xlsx_builder.build_xlsx(
        os.path.join(EXAMPLES, "sample_book.xlsx"),
        [{"name": "月度收支",
          "rows": [["月份", "收入", "支出", "结余"],
                   ["1月", 8200, 5300, 2900],
                   ["2月", 8600, 6100, 2500],
                   ["3月", 9100, 5400, 3700],
                   ["合计", "~sum", "~sum", "~sum"]],
          "charts": [{"type": "column", "title": "月度收支对比",
                      "labels": ["1月", "2月", "3月"], "values": [8200, 8600, 9100]}],
          "format": {"data_bars": [2, 3], "highlight": {"max": [2]}}}],
        style={"theme_color": "1F3864", "banded": True, "freeze_header": True})

    pdf_path = os.path.join(EXAMPLES, "sample_readme.pdf")
    try:
        from pypdf import PdfWriter
        w = PdfWriter()
        w.add_blank_page(width=595, height=842)
        w.add_metadata({"/Title": "doc-gen 示例 PDF", "/Author": "zhuzhu Copilot"})
        with open(pdf_path, "wb") as f:
            w.write(f)
    except ImportError:
        print("[skip] pypdf 未安装，跳过 PDF 示例（read_pdf 会给出安装提示）")

    for p in sorted(os.listdir(EXAMPLES)):
        f = os.path.join(EXAMPLES, p)
        print(f"[ok] {f}  {os.path.getsize(f)} bytes")


if __name__ == "__main__":
    sync_skill_md()
    build_examples()
