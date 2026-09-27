---
name: doc-gen
description: 高质量生成与美化办公三件套（Word/PPT/Excel）：用 create_docx/create_pptx/create_xlsx 生成（支持封面/目录/表格/图表/两栏布局等），用 beautify_docx/beautify_pptx/beautify_xlsx 精修已有文档，extract_text 验证内容。生成要遵循「规划→配图→生成→自检→精修」闭环，杜绝千篇一律的模板感。
---

# doc-gen：Word / PPT / Excel 高质量生成与美化

当用户要求"做一份文档 / 生成 PPT / 整理成 Excel 表格 / 美化这份文档 / 做一份正式报告"时使用本技能。

## 0. 先判断用哪个工具
- 新建文档 → create_docx / create_pptx / create_xlsx
- 美化已有文件（或新生成后二次精修）→ beautify_docx / beautify_pptx / beautify_xlsx
- 读取/校验内容 → extract_text
- 需要配图素材 → 先 generate_image（绝对路径随文档用）

## 1. 确认需求（内容不齐必须问，禁止编造数据）
文档主题、受众与用途、内容要点/章节、保存路径、格式（docx/pptx/xlsx）。
数据类内容（数字/报表/预算）若用户未提供具体数据，**必须 ask_user 补齐**，绝不允许凭印象编造。

## 2. 先规划后生成（高质量的核心，不要拿起就写）
1. **定结构**：先在心里排好大纲再一次性传参。
   - PPT：封面(1 页) + 目录/背景(1-2 页) + 分主题内容页。主题类 **12-25 页**（重要主题可更多），
     每个分论点拆成独立一页，整体做到"页数够、信息密度足"。封面、目录、章节开篇、数据页、
     小结页/致谢页缺一不可——一份完整 PPT 至少要有封面、目录、8+ 页正文、结尾页。
   - Word：封面 + 目录([toc]) + 各章节（'# '标题分层）+ 必要时表格/引用。
   - Excel：按维度拆多个 sheet（如 明细/汇总/图表数据），每 sheet 首行表头。
2. **内容要详实**：PPT 每页要点 3-6 条、写成完整句子（每点 15-40 字），"产品介绍/方案对比/总结"
   不要只给短语；同一主题下尽量交替使用 要点页 / 卡片页 / 图表页 / 表格页 / 图形排版页，
   避免连续多页纯文字。配图（generate_image）也要跟上：封面、开篇、收尾各至少一张。
3. **同一页用一种主视觉**：要点页 / 卡片页 / 图表页 / 表格页不要堆叠混排，一页讲透一件事。

## 3. 视觉规范（摆脱"模板机器"感，每份都必须定制）
- **选主题**：style.theme 按内容气质选（business 商务/tech 科技/green 环保/warm 文艺/
  red 党政年节/black-gold 高端/暗色主题用 dark……），同时换一两个布局/封面参数，
  严禁全部默认同一商务蓝。
- **文档骨架要全**：
  - Word：正式报告给 style.cover={subtitle,author,date} 生成封面；长文档放 [toc]；
    数据用连续 '| 列 | 列 |' 行成表；强调句用 '> '。
  - PPT：title 一定配 cover 页；页面底色别都是白的，挑 3-5 页用 bg_color 浅色/深色；
    对比或流程用 diagram，数据用 chart，不要全部写成 bullets 页。
  - Excel：多 sheet 拆表 + 表尾 '~sum' 合计行；关键指标页加 charts=[{type,title,labels,values}]。
- **配图（汇报/产品/宣传/总结必须）**：generate_image 生成与主题一致、留白足够的素材图，
  插入 create_* 的 image/cover.image，图注可用 Word caption。
- 中文字体默认微软雅黑，正式文书可换 宋体/仿宋。

## 4. 传参硬性要求（先读，否则生成残缺/空文档）
- `path` 必须给保存路径。
- **结构化参数必须用数组**：paragraphs=[...]、slides=[...]、sheets=[...]。
  绝不能把单个对象传成 slides={...}、也不能把字符串传成 paragraphs="..."（工具会报类型错误）。
- slides 每项是 {title, ...} 对象；sheets 每项是 {name, rows, ...} 对象；
  wordart/image/chart/diagram/table/cards 是对象或数组，按 schema 传。
- style、cover、image 等对象参数按 dict 传。
- PPT layout=two_col 用 columns=[{title,bullets}]；center_highlight 用 highlight；
  hero_stats 用 stats=[{value,label}]；新图表 radar/scatter/combo、新图形 timeline 见
  `skills/doc-gen/references/call-examples.md`（打包版同目录），先读示例再传参。
- 文本类字段（bullets/cards.title/desc/diagram.items/chart.labels/表格单元格）一律传字符串，
  禁止传对象，否则会生成未解析字面量。生成成功后工具会自动复核内容（返回含"已自动复核"）。

## 5. 生成 → 自检 → 精修（闭环必做）
1. 生成后**必须 extract_text(path)** 验证：标题/正文/表格/中文都正确、无空页/残页。
2. 如果用户说"不好看/太模板/再精致点"：优先用对应 beautify_* 一键换主题重排，
   或**只改样式/布局参数重新生成**，不要整份推倒重写。
3. 校验通过后向用户汇报：文件路径 + 结构（章节/页数/工作表）+ 主题配色与版式选择。

## 3.5 动画与切换（PPT 专属，用得好才有"高级感"）
- **默认已开启创意动画**：每页按页序自动轮换入场效果（fade/wipe/box/circle/diamond/dissolve/plus/
  checkerboard/randombar/blinds/wedge/wheel/appear），并按 标题→图形→正文 分层逐元素播放；
  不需要动画的页（如目录页/致谢页）用 slide.anim=false 或 anim.enabled=false 关掉。
- **按页定制**：slide.anim = {"title": "fade", "graphics": "circle", "text": "wipe",
  "exit": "fade"}。效果名中英别名均可（"淡入"=fade）。退场（exit）放在关键页收尾（本章小结、
  观点页），全篇少用避免廉价感。
- **切换动画**：style.transition 支持 fade/push/wipe/split/cover/pull/zoom/dissolve/circle/
  diamond/blinds/checker/wheel/comb/plus/newsflash/cut/wedge/random。章节开篇用 push/zoom
  制造"翻篇感"，内容页统一 fade/wipe，不要每页花哨。
- **节奏感原则**：标题先出、图形跟上、正文最后；重要数据页给图形加 effect 让其"逐柱/逐卡"
  链式出现；演讲型 PPT 动画宁少勿滥，全自动放映可 style.animation=false 或各页全部 appear。
- 需要精确参数表/JSON 示例时，用 read_file 读取技能随附速查文件
  `skills/doc-gen/references/animation-guide.md`（打包版为程序目录下同名文件；
  找不到时按上方 §3.5 内联示例即可），禁止凭空猜测字段。

## 6. 常见错误自查
- slides 里某页忘了 title → 必加。
- 图表/数据与正文脱节 → 图表数据必须与要点论述一致。
- 配图与内容无关 → generate_image 的 prompt 要含主题关键词与风格（如"扁平商务插画"）。
- 美化会覆盖原文件 → 想保留原稿先复制一份再 beautify。
