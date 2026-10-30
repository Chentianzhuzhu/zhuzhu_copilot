# doc-gen 工具调用示例速查（few-shot）

> 生成前先读本文件：照格式传参，杜绝类型错误与漏字段。
> 所有 `path` 必须给保存路径；`slides/sheets/paragraphs` 必须用数组，绝不可传对象。

## 1. create_pptx（演示文稿）

```json
{
  "path": "out/产品发布.pptx",
  "title": "2026 新品发布会",
  "style": {
    "theme": "black-gold",
    "transition": "push",
    "cover": {"subtitle": "AI 驱动增长新引擎", "author": "市场部", "date": "2026-09-08"}
  },
  "slides": [
    {"title": "目录", "bullets": ["一、市场机遇", "二、产品亮点", "三、成长计划"], "anim": false},
    {"title": "核心指标", "layout": "hero_stats", "stats": [
      {"value": "42%", "label": "营收增长"},
      {"value": "327万", "label": "活跃用户"}]},
    {"title": "能力雷达", "chart": {"type": "radar",
      "labels": ["性能", "成本", "体验", "安全"], "values": [92, 78, 88, 95]}},
    {"title": "营收与增速", "chart": {"type": "combo",
      "labels": ["Q1", "Q2", "Q3", "Q4"], "values": [120, 180, 240, 320],
      "values2": [10, 22, 18, 30]}},
    {"title": "发展历程", "diagram": {"type": "timeline", "items": [
      {"date": "2023", "text": "公司成立，组建核心研发团队"},
      {"date": "2024", "text": "V1 发布，完成技术验证"}]}},
    {"title": "方案对比", "diagram": {"type": "compare", "items": [
      {"title": "自研方案", "left": "成本可控", "right": "定制灵活"},
      {"title": "采购方案", "left": "上线快", "right": "依赖厂商"}]},
     "anim": {"text": "wipe", "exit": "fade"}},
    {"title": "产品演进", "diagram": {"type": "flow", "items": ["立项", "研发", "发布", "迭代"]}},
    {"title": "份额数据", "table": {"title": "2025 全年份额",
      "header": ["厂商", "份额", "增速"], "rows": [["A", "27%", "+42%"], ["B", "18%", "+12%"]]}},
    {"title": "双主线", "layout": "two_col", "columns": [
      {"title": "产品线", "bullets": ["AI 平台持续迭代", "智能体生态扩张"]},
      {"title": "商业化", "bullets": ["订阅制渗透率翻倍", "政企大单落地"]}]},
    {"title": "行动号召", "layout": "center_highlight", "highlight": "以 AI 重塑增长曲线"}
  ]
}
```

## 2. create_docx（Word 文档）

```json
{
  "path": "out/项目方案.docx",
  "title": "数字化转型项目方案",
  "style": {
    "theme": "business",
    "cover": {"style": "band", "subtitle": "一期建设规划", "author": "战略部",
              "date": "2026-09-08"},
    "watermark": {"text": "内部资料", "color": "C9CFD8"},
    "header_text": "数字化转型项目"
  },
  "paragraphs": [
    "# 第一章 项目概述",
    "本方案围绕数据平台建设与智能分析展开……",
    "> 核心目标：一年内完成三大系统上线。",
    "| 指标 | 目标值 |",
    "| 数据接入 | 60 个系统 |",
    "| 报表自动化 | 达 80% |",
    "# 第二章 实施计划",
    "- 阶段一：基础平台搭建",
    "- 阶段二：数据治理",
    "[pagebreak]",
    "## 2.1 里程碑",
    "1. 立项评审（第 1 月）",
    "2. 平台上线（第 6 月）"
  ]
}
```

## 3. create_xlsx（Excel 工作簿）

```json
{
  "path": "out/经营分析.xlsx",
  "style": {"theme": "business", "theme_color": "1F3864"},
  "sheets": [
    {
      "name": "季度经营",
      "wordart": {"text": "2026 季度经营分析", "size": 16},
      "rows": [
        ["门店", "营收(万)", "增速%", "评分"],
        ["华东", 320, 42, 92],
        ["华南", 260, 22, 88],
        ["华北", 180, 58, 79],
        ["合计", "~sum", "", ""]
      ],
      "format": {"data_bars": [2], "highlight": {"max": [2], "min": [3]},
                 "color_scale": [4]},
      "charts": [{"type": "bar", "title": "门店营收对比",
                  "labels": ["华东", "华南", "华北"], "values": [320, 260, 180]}]
    },
    {
      "name": "预算明细",
      "rows": [["项目", "预算"], ["研发", 500], ["市场", 300]]
    }
  ]
}
```

## 4. 美化既有文件

```json
{"path": "out/草案.docx", "style": {"theme": "warm", "align": "justify",
  "line_spacing": 1.5, "title_color": "B0561A"}}
{"path": "out/旧版.pptx", "style": {"theme": "dark", "title_color": "FFFFFF",
  "bg_color": "16233E"}}
```

## 参数硬性要求（违反即报错或残件）

- `path` 必填；`slides`/`sheets`/`paragraphs` 必须是数组（`slides={...}` 会报类型错误）。
- 文本类字段（bullets/cards.title/cards.desc/diagram.items/chart.labels/table 单元格）
  一律传**字符串**或字符串数组，禁止传对象，防止出现未解析字面量。
- 数据类内容缺失时必须 ask_user 补齐，禁止编造。
- 生成成功后会自动复核内容（工具返回含"已自动复核"字样）；若提示校验异常需修正参数重生成。