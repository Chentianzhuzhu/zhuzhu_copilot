"""skills / agents / MCP 服务器配置：JSON 文件加载（启动时读取，可热改）

技能已统一为市场标准 md 格式（~/.winapp_migrator/agent/skills/<name>/SKILL.md），
skills.json 仅保留用户自建的非内置 JSON 技能（内置旧 JSON 条目加载时自动迁移移除）。
配置目录：~/.winapp_migrator/agent/
- skills.json      用户自建技能（可选）：[{"name","description","instruction"}]
- agents.json      助手：[{"name","description","persona","rules","tool_instructions","system_prompt","skills":[],"tools":[]}]
                    persona: 人设描述；rules: 规则约束数组；tool_instructions: {工具名: {"理解": str, "执行拆分": [str]}}
- mcp_servers.json MCP：[{"name","type":"stdio|sse","command","args"|"url"}]
目录/文件不存在时使用内置默认值，首次运行自动生成示例文件。
"""

import copy
import json
import os
import re
import shutil
import sys
import threading
import time
from pathlib import Path

from winapp_migrator.core import agent_llm

CONFIG_DIR = Path.home() / ".winapp_migrator" / "agent"

# 技能体系统一为市场标准 md 格式（SKILL.md），JSON 技能已废弃：
# 原 JSON 技能（complex-task/brainstorming/writing-plans/
# test-driven-development/systematic-debugging/skill-create）已迁移至
# _BUILTIN_MD_SKILLS 内置 md 模板；code-review 由随包技能提供（见 skills/）。
# 电脑操控相关技能（screen_operate/ui-automation）已随电脑操控功能一并移除（2026-08）。
# 保留空列表仅为兼容 _ensure_samples / load_skills 的兜底逻辑。
DEFAULT_SKILLS = []

# 内置 md 技能模板：首次运行时自动生成到 skills/<name>/SKILL.md（市场标准格式，以 SKILL.md 为核心）。
# 不加入 DEFAULT_SKILLS（JSON 优先会覆盖 md 版，导致用户编辑 SKILL.md 不生效）。
_BUILTIN_MD_SKILLS = {
    "download-skill": {
        "description": "技能下载安装：在 GitHub 上搜索市场标准 SKILL.md 技能，下载并导入本地配置生效",
        "instruction": """# download-skill：GitHub 技能搜索、下载与导入

当用户要求"下载 / 安装 / 寻找某个 skill（技能）"时，按以下流程执行。

## 1. 确认需求
用 ask_user 与用户确认：技能名称/关键词、用途、是否有已知 GitHub 仓库地址。
信息不足时禁止猜测，先问清楚。

## 2. 在 GitHub 上定位技能
候选仓库判断标准：仓库根目录或 skills/ 子目录存在 SKILL.md（含 --- name / description --- frontmatter）。
- 用户提供仓库地址：先 run_command 执行 `git ls-remote <仓库地址>` 验证仓库可访问；
- 用户只给名称/关键词：依次探测常用市场仓库（git ls-remote 验证存在后浅克隆）：
  - https://github.com/anthropics/skills
  - https://github.com/anthropics/claude-code
  - 其他含 SKILL.md 的 skills 聚合仓库
  找不到时用 ask_user 请用户提供具体仓库地址，不要编造 URL。
- 用 `git clone --depth 1 <仓库> <临时目录>` 浅克隆后，用 list_directory / read_file
  在仓库内查找 SKILL.md，读取 frontmatter 确认技能 name 与 description 是否匹配用户需求。

## 3. 下载前确认（强制）
用 ask_user 向用户展示以下信息并征得同意：
- 仓库地址
- 技能名与 description
- SKILL.md 内容摘要（前若干行）
用户确认后才下载；用户拒绝则停止并说明。

## 4. 下载
- 整仓浅克隆：git clone --depth 1 <仓库> <临时目录>（临时目录建议 %TEMP% 下）
- 有 zip 或 raw 文件直链：用 fast_download 工具下载到本地
- 单文件：下载 SKILL.md 的 raw 内容

## 5. 导入本地并配置
- 目标目录：~/.winapp_migrator/agent/skills/<技能名>/SKILL.md
- 用 import_skill_file 导入（支持 SKILL.md 单文件或含 SKILL.md 的 zip），
  或把下载到的 SKILL.md 复制到目标目录
- 验证：read_file 确认 SKILL.md 已写入目标位置；技能系统实时扫描，导入后立即可用

## 6. 汇报
输出总结：技能名、来源仓库、安装路径、调用方式（/技能名 或自然语言描述）。""",
    },
    "doc-gen": {
        "description": "办公三件套全流程（生成/读取/编辑/美化）：create_* 生成、read_* 读真实结构与锚点、edit_* 精确修改、beautify_* 一键美化、extract_text/read_* 验证；含 PDF 读取",
        "instruction": """# doc-gen：Word / PPT / Excel 生成·读取·编辑·美化 全流程规范

当用户要求「做一份文档 / 生成 PPT / 整理成 Excel / 改一下这份 PPT 的标题 / 读这份 PDF 的要点」时使用本技能。
本技能覆盖四类能力：**生成(create_*)** → **读取(read_*)** → **编辑(edit_*)** → **美化(beautify_*)**，并含 PDF 读取(read_pdf)。

## 0. 能力选择矩阵（先判断用户要的是哪一类）
| 用户意图 | 用哪个工具 | 关键点 |
|---|---|---|
| 从零做文档 | create_docx / create_pptx / create_xlsx | 见「§2 生成」 |
| 改已有文档的文字/样式/结构 | read_* → edit_* | **必须先读后改**（§3） |
| 看文档里有什么（给模型读） | read_docx / read_pptx / read_xlsx / read_pdf | 返回结构化 Markdown（§3.1） |
| 读 PDF 内容 | read_pdf（只读，不支持编辑） | 缺 pypdf 会提示安装 |
| 只嫌排版丑、内容不动 | beautify_* | 比重写快、保留内容（§4） |
| 快速取纯文本比对 | extract_text | 轻量，适合校验 |

> 工具可用性：read_*/edit_*/*_pdf 需应用版本支持「办公读写」能力。若调用返回
> 「未知工具/工具不存在」，说明当前运行版本尚未包含该能力，请如实告知用户并建议
> 升级（或改用手头可用的工具组合），不要伪造读取/编辑结果。

## 1. 确认需求
先问清：文档主题与内容要点、保存路径、格式（docx/pptx/xlsx）。
内容信息不足时用 ask_user 补齐，**禁止编造数据**（数字、报表、预算类必须由用户提供）。

## 2. 生成（create_docx / create_pptx / create_xlsx）

### 2.1 大胆设计，拒绝千篇一律（核心原则）
所有文档都默认商务深蓝风会让你看起来像模板机器。请根据内容主题主动选择配色、字体与布局：
- 主题换色（style.theme）：科技/产品→tech(蓝)、环保/自然→green(墨绿)、党政/年节→red(朱红)、
  教育/学术→business(深蓝)或pastel(浅蓝)、高端/商务晚宴→black-gold(黑金)、创意/发布会→vivid(橙红)、
  暗色炫酷→dark、温馨/文艺→warm(橙棕)。也可直接传色值（base_color/heading_color/text_color/theme_color/header_fill）。
- 字体（font_name）：正式文书"宋体/仿宋"，文艺"楷体"，默认微软雅黑。
- 布局：Word 调行距/对齐/纸张方向；PPT 选封面布局与要点符号；Excel 自定义表头配色/隔行/边框/冻结筛选。
- 每份文档至少换一个配色或布局参数，让作品符合内容气质。

### 2.2 传参硬性要求（先读，否则会生成空/残缺文档）
- path 必须给出保存路径。
- 必须用方括号 [] 传数组：paragraphs=[字符串...]、slides=[{...},...]、sheets=[{...},...]。
  绝不要把单个对象传成 slides={...} 或把字符串传成 paragraphs="..."（工具会报「类型应为数组」并拒绝生成）。
- slides 每项必须是 {title,...} 对象；sheets 每项必须是 {name,rows} 对象；wordart 每项必须是 {text,...} 对象。

### 2.3 各格式要点
- **Word（create_docx）**：path、title、paragraphs=[...]、style、images=[...]、wordart=[...]。
  段落轻标记：'# '一级、'## '二级、'### '三级、'- '项目符号、'1. '编号、'> '引用、
  连续 '| a | b |' 自动成表（首行表头）、'[toc]' 目录、'[pagebreak]' 分页。
  style.cover={subtitle,author,date,image} 生成独立封面；style.header_text 页眉，页脚自动页码。
- **PPT（create_pptx）**：slides=[{title, bullets, cards, table, chart, diagram, image, wordart,
  bg_color, title_color, layout, stats, columns, highlight, anim}]。
  **页数要够、内容要详**：主题类 PPT ≥8 页（建议 12-25 页），每页要点写成完整句子，不要只写短语。
  **排版要多元**：用 cards 分区、table 呈现数据、chart 画图、diagram 画思维导图/流程/对比/循环、
  bg_color 换页底色、layout 换版式；style.transition 加页面切换，动画默认开启（style.animation / anim_trigger /
  anim_stagger 调节奏，整份关闭用 style.animation=false）。
- **Excel（create_xlsx）**：sheets=[{name, rows, charts, image, wordart, format}]。
  首行自动表头（深蓝底白字并冻结）；纯数字字符串自动转数值；末行 '~sum' 自动求和；
  charts=[{type: bar/column/line/pie, title, labels, values}] 生成原生可编辑图表；
  format={data_bars:[列], highlight:{max:[列],min:[列]}, color_scale:[列]} 加条件格式。

### 2.4 配图（强制）
内容适合配图（汇报/产品介绍/感言/总结/宣传等）时，**必须先用 generate_image**（真实 AI 文生图）
生成素材，再把返回的本地路径作为 image 参数插入。不要跳过配图直接生成无图文档。

## 3. 读取与编辑（改已有文档的正确姿势：先读后改）

### 3.1 读取：read_docx / read_pptx / read_xlsx / read_pdf
返回**结构化 Markdown**，专门为「定位锚点 → 精确修改」设计：
- read_docx：标题层级、粗斜体下划线、每段 `<!-- size=12pt align=justify -->` 标注、
  表格（| 列 | 列 |）、图片占位 `[图片 #n media/xxx.png]`、页眉页脚、可用样式名清单。
- read_pptx：逐页 `=== 第 N 页 ===`，每个形状 `[z序] 类型 name= 位置(英寸) 尺寸(英寸) 字体/字号/颜色`、
  文本行、表格、图表(类型+分类+数值)、备注、动画条目(shape_id/效果名/trigger/delay)。
- read_xlsx：每表 `=== 工作表 '名称' R行 x C列 ===`、列宽、合并、图表、条件格式，
  以及 `| 行 | A | B |` 数据表（公式原样 `=SUM(...)`，样式以注释标注）。
- read_pdf：页数、元数据、逐页文本（多空白分隔行线性化为表格）。PDF 只读。

### 3.2 编辑：edit_docx / edit_pptx / edit_xlsx
统一形态：`edit_xxx(path=..., ops=[{op: 名称, ...参数}, ...])`。逐条真实执行，
**单条失败不中断**并返回逐条报告（OK/FAILED + 原因）；未知 op 会提示该类型全部可用 op。
- edit_docx 可用 op：replace_text / append_paragraph / insert_paragraph / delete_paragraph /
  set_paragraph_style / set_table_cell / add_table / add_image / set_header / set_footer
- edit_pptx 可用 op：replace_text / add_slide / delete_slide / duplicate_slide / move_slide /
  set_text / set_notes / set_bg_color / add_textbox / add_image / set_transition / set_animations
  （坐标单位英寸；set_animations 的 effect 支持 fade/wipe_*/fly_*/zoom/float_*/dissolve/box/circle/
  diamond/plus/blinds/wheel/split/wedge/checkerboard/grow_turn/pinwheel/randombar_*/strips_upleft/appear）
- edit_xlsx 可用 op：set_cell / set_formula / append_row / insert_row / delete_row / set_style /
  merge_cells / unmerge_cells / set_column_width / set_row_height / add_sheet / rename_sheet /
  delete_sheet / add_chart / add_condition

### 3.3 编辑闭环（推荐流程）
1. `read_xxx` 读出结构，确认要改的**原文/索引/单元格**；
2. 用该原文作 find/anchor（宁可多读一次，不要猜文字）；
3. `edit_xxx` 执行 ops；
4. 再 `read_xxx` 或 extract_text 复核改动生效；
5. 预览面板会自动刷新保真预览（含 PPT 动画、Word/Excel 样式与图片）。

## 3.5 美化已有三件套（beautify_*）
内容不动、只精修排版时用：beautify_docx(path, style) / beautify_pptx(path, style) /
beautify_xlsx(path, style)。style 可传 {theme, font_name, theme_color}；beautify_docx 额外支持
align/line_spacing，beautify_pptx 额外支持 bg_color。建议先复制副本再美化以保留原件。

## 4. 验证（必做）
生成/编辑后用 read_xxx（或 extract_text）读回文件，确认标题、正文、中文、表格数据、图片、
艺术字、动画均正确；若工具返回「复核失败/未解析标记」必须修正参数重做，不得交付残件。

## 5. 资源与示例文件（可复用模板）
技能自带资源目录（与本 SKILL.md 同级）：
- `assets/theme_guide.md`：配色方案选择速查（10 套主题的适用场景与色值）。
- `assets/style_cheatsheet.md`：三类文档 style 参数全字段速查表。
- `examples/`：真实生成的样例文件，可直接复制改造：
  `sample_report.docx`（带封面/目录/表格的报告）、`sample_deck.pptx`（含卡片/图表/动画的演示）、
  `sample_book.xlsx`（含表头样式/条件格式/图表的表格）。
  用法：先用 read_xxx 读样例结构，再照其 style 仿写自己的内容；或直接 edit_xxx 改样例副本。

## 6. 汇报
输出：文件路径、章节/工作表、页数、采用的配色主题与排版风格（卡片/表格/图表/思维导图/流程图）、
动画与切换效果、图片/艺术字/背景处理、做了什么编辑（逐条 op 摘要）、内容摘要。""",
    },
    "web-search": {
        "description": "联网搜索：web_search 关键词检索（Bing/回退 DuckDuckGo），web_fetch 抓取网页/调用 API 并按关键词定位详情",
        "instruction": """# web-search：联网搜索与网页抓取

当需要查询实时信息、查找资料、调用网络接口时使用本技能。

## 1. 搜索
web_search(query=搜索关键词, max_results=返回条数, must_include=过滤词可选)：
- 适合：新闻、文档、教程、代码示例、产品信息等实时内容
- 返回标题+URL+摘要，先读摘要判断相关性，再决定是否抓详情
- 需要精确检索时传 must_include：只保留标题/摘要包含该词的结果

## 2. 抓取详情
web_fetch(url=地址, keyword=页面内关键词可选)，默认 GET；
- 抓取后定位：keyword 传目标词，只返回页面内包含该词的段落（长页/文档精确提取）
- 调用 API：method=POST/PUT/DELETE，headers 传鉴权头（如 {"Authorization": "Bearer xxx"}），
  body 传请求体（JSON 字符串或原始文本）
- 响应过长会截断，可指定 max_chars 调整

## 3. 信息不足时
换关键词重搜或用 ask_user 向用户确认方向，禁止编造内容与链接。

## 4. 汇报
给出结论并附来源 URL。""",
    },
    "file-ops": {
        "description": "文件操作：read_file/write_file/edit_file/delete_file/list_directory 读写改删列文件，search_files 模糊查找",
        "instruction": """# file-ops：文件读写改删与查找

当需要读取、创建、修改、删除文件或查找文件时使用本技能。

## 1. 定位
- 知道路径：直接用 read_file / write_file / edit_file / delete_file / list_directory
  （相对路径基于工作目录，未设工作目录则基于用户目录）
- 不知道路径：search_files(query=文件名关键字, folder=限定目录可选) 模糊查找

## 2. 操作要点
- 读：read_file(path)，大文件分段读取
- 写：write_file(path, content)，会覆盖已存在文件，写前先确认目标
- 改：edit_file(path, old_text, new_text)，只替换首次匹配，old_text 需在文件中唯一
- 删：delete_file(path)
- 列目录：list_directory(path)

## 3. 注意事项
- 修改前先 read_file 了解原文，避免改错
- 系统关键目录（Windows、Program Files 等）的删除会被沙盒拒绝
- 操作后建议 read_file 验证结果""",
    },
    "system-admin": {
        "description": "系统管理：find_app 定位应用、system_info/get_time/env_var 查系统信息、optimize_memory 清理内存、uninstall_app 卸载、migrate_app 迁移应用",
        "instruction": """# system-admin：系统信息查询与应用管理

当需要查询系统信息、定位应用、清理内存、卸载或迁移应用时使用本技能。

## 1. 查询类（无副作用）
- system_info：主机名/系统版本/CPU 核心数/物理内存
- get_time：当前时间
- env_var(name)：读取环境变量

## 2. 定位应用
find_app(query=应用名)：秒查已安装应用的可启动路径，比逐层截图找图标高效。

## 3. 重量级操作（ask/edit 模式工具内部会弹用户确认；YOLO 模式直接执行）
- optimize_memory：清理内存（终止可安全退出的后台进程、压缩工作集）
- uninstall_app(name)：卸载应用（先调自带卸载器再清理残留）
- migrate_app(name, target)：把应用迁移到其他盘
ask/edit 模式下先向用户说明影响，确认后再执行；YOLO 模式下直接执行即可。
若用 run_command 辅助卸载（如 taskkill /f /im 应用进程、rd /s /q 应用目录），
目标是非系统进程/非系统目录时可正常执行；系统关键目录与系统进程仍会被拒绝。""",
    },
    "cmd-ops": {
        "description": "命令执行与下载：run_command 执行命令（含白名单/沙盒约束）、check_command 轮询后台命令、fast_download 高速下载文件",
        "instruction": """# cmd-ops：命令执行、轮询与下载

当需要在终端执行命令、下载文件时使用本技能。

## 1. 执行命令
run_command(command=命令, wait=等待秒数默认5, force_quit=是否超时强杀)：
- 危险命令（删除/格式化/关机等）会被沙盒拒绝，先想清楚再执行
- 短命令：默认 wait=5 等输出；启动 GUI 应用建议 wait=1
- 长任务：wait 秒内未完成且 force_quit=false 会转入后台，返回命令 ID
- 预计长时间挂起/无输出的命令：force_quit=true 防阻塞

## 2. 轮询后台命令
check_command(cmd_id=命令ID) 查询进度与最新输出；不传 cmd_id 列出全部后台命令。

## 3. 下载文件
fast_download(url=下载地址, dest_dir=保存目录留空用工作目录)：多段并发高速下载，自动探测文件名，支持断点续传。

## 4. 汇报
输出：命令执行结果/退出码、下载的文件路径。""",
    },
    "memory": {
        "description": "长期记忆：save_memory 追加保存用户偏好/重要结论/约定/路径到本地记忆，load_memory 读取全部记忆",
        "instruction": """# memory：本地长期记忆

当需要记住跨任务信息或回忆过往内容时使用本技能。

## 1. 保存（主动）
遇到值得长期记住的信息时调用 save_memory(content=内容)：
- 用户偏好、习惯、语言要求
- 重要结论、决策、约定
- 常用路径、工作目录、文件位置
每条自动带时间戳追加，单条 ≤8000 字符，不会覆盖旧记录。

## 2. 读取
load_memory() 读取本地记忆文件 memory.md 全部内容。
新任务开始、或需要回忆过往信息时自行决定是否调用。

## 3. 原则
- 只存真正长期有价值的信息，临时性内容不必存
- 每次任务结束前回顾是否有值得保存的内容""",
    },
    "sub-agent": {
        "description": "子任务调度：dispatch_sub_agents 并行派发互不依赖的子任务、explore_project 快速了解新项目、search_large 大规模跨目录搜索",
        "instruction": """# sub-agent：子任务并行调度与大规模搜索

当任务包含多个互不依赖的子任务、需要快速了解项目、或搜索范围很大时使用本技能。

## 1. 并行派发
dispatch_sub_agents(tasks=[{title: 标题, goal: 目标与要求, context: 上下文}]):
- 适合：大规模读取/搜索/探索、多文件并行处理
- 子任务数量不限（一次派发多少就跑多少），每个 goal 写清"要做什么、输出什么"
- ★每个子任务必须带 context：把主 Agent 已读取的文件内容、关键代码片段、
  搜索结果、约束要求等完成任务所必需的信息随任务传给子 Agent——子 Agent 是
  独立上下文，看不到主对话与工具结果；缺 context 会盲猜或重复读取，降低质量。
- 派发前对尚未读取而子任务需要的内容，先用 read_file 等工具读取后再打包进 context
- 子 Agent 可读写文件但不能执行命令；执行类工作留在主 Agent

## 2. 了解新项目
explore_project(directory=项目目录, context=可选上下文, shared_context=可选, space=可选)：
生成目录结构、读 README 与关键入口，输出项目概览（用途/技术栈/模块结构/入口/构建方式）。
接手新项目先用它。

## 3. 大规模搜索
search_large(query=关键词, directories=目录列表可选, max_results=条数,
context=可选上下文, shared_context=可选, space=可选)：
跨目录多轮搜索并汇总命中，适合范围大、文件多的场景；
小范围/单文件查找用 search_files 更轻量。

## 4. 注册式子 Agent（sub_<name>）与「共享上下文 / 传参上下文」分配
用户要求「创建一个子 agent / 新增一个能干活的下属 agent」时，一律用 register_sub_agent
注册进当前工作流（不要用 create_agent——那只是切换主 Agent 人格，主 Agent 调不到它）。
注册式子 Agent 的三种用法：① 用户 @<名> 直接调用；② 主 Agent 调用 sub_<name>；③ 参与共享上下文。
主 Agent 对每个子 Agent（注册式与临时子任务均可）逐次分配：
- shared_context=true/false：本次是否加入共同上下文空间（先 shared_context 工具 op=open 开空间；
  注册默认值可被逐次覆盖）；
- context=<上下文>：把已读到的文件内容 / 已得结论交给该子 Agent，免其重复读取搜索；
- space=<空间 id>：指定目标空间（缺省用活跃空间）。

注册时可选权限（团队协作场景按需开启，缺省关闭）：
- allow_chat=true/false：是否允许该子 Agent 参与 Agent 间聊天（chat_with 可发给它并接收）；
- share_context=true/false：是否允许领导者（如产品经理）用 look_context 查看其上下文轨迹
  （消息/命令/文件/工具/skill/mcp/plugin）。

## 5. 工作团（Agent Team）与监督
默认团队：领导者 product_manager（产品经理）+ 成员 zhuzhu_copilot（默认工作流）、
frontend_design、product_dev、backend_dev、product_debug。用户 @product_manager 即激活团队模式。
产品经理/领导者可：
- look_context(agent=<成员名>)：查看成员上下文轨迹（开启 share_context 的子 Agent 可见）；
- chat_with(to=<成员名>, text=...)：与成员讨论（开启 allow_chat 的子 Agent 可接收）；
- pause_agent(agent=<成员名>) / resume_agent(agent=<成员名>)：暂停/恢复成员执行；
- warn_agent(agent=<成员名>, text=...)：向成员发警告提醒（下一检查点注入其上下文）。
跨工作流派发：dispatch_sub_agents(tasks=[{agent: <成员工作流名>, goal, context, shared_context}])
把任务交给成员工作流的主 Agent 接管，原主 Agent 监督；@工作流 切换继承原工作流的共享空间。

## 6. 汇报
汇总各子任务结果、项目概览或搜索命中清单。""",
    },
    "skill-mgmt": {
        "description": "技能创建：create_skill 根据用户自然语言描述自动生成市场标准 SKILL.md 技能并立即加载生效",
        "instruction": """# skill-mgmt：创建新技能

当用户要求"创建一个技能 / 把某个能力做成 skill"时使用本技能。

## 1. 确认需求
用 ask_user 问清：技能要做什么（触发场景）、执行流程、注意事项。
信息不足禁止猜测。

## 2. 创建
create_skill(name=技能名, description=用途简介, instruction=执行流程正文)：
- name：仅字母/数字/下划线/连字符，≤50 字符（如 doc-gen）
- instruction：markdown 正文，写清触发条件、分步流程、注意事项
- 生成到 skills/<name>/SKILL.md，创建后立即生效

## 3. 验证与汇报
创建后说明：技能名、调用方式（/技能名 或自然语言描述）、是否已生效。
市场已有同名技能时，建议先询问用户是否仍要创建（避免覆盖）。""",
    },
    # ---- 原 JSON 技能迁移（统一为市场标准 md）----
    "complex-task": {
        "description": "复杂任务：拆解为可独立验证的步骤清单，逐步执行并验证",
        "instruction": """# complex-task：复杂任务拆解执行

1. 把复杂任务拆解为可独立验证的步骤清单，按依赖顺序排列，先输出计划再动手。
2. 每步执行前说明意图；执行后确认结果正确（不确定时截图验证），成功才进入下一步。
3. 步骤失败时先自查原因（截图差异、坐标偏移、路径错误、名称不符），
   换方案重试（最多 2 次），仍失败用 ask_user 向用户求助，禁止盲目重复。
4. 需要定位应用/文件时先用 find_app / search_files；信息不足用 ask_user 澄清。
5. 步骤多时每完成一个阶段用 save_memory 保存进度，上下文被压缩后先 load_memory 恢复。
6. 全部完成后输出总结：完成项、最终结果、关键产出位置。""",
    },
    "brainstorming": {
        "description": "头脑风暴：先理清需求，再制定计划并交用户审核",
        "instruction": """# brainstorming：需求澄清与方案计划

当任务目标、范围或验收标准不明确时使用本技能，先想清楚再动手。

1. 先用 ask_user 一次性澄清需求：目标、范围、约束、输入/输出与验收标准
   （合并为一次提问，避免逐项追问）；仍有疑问再做少量补充，禁止编造需求内容。
2. 需求明确后进入方案设计：提出 2-3 个可行方案并对比优缺点、成本与风险，
   用 ask_user 征询用户选择。
3. 方案确认后，按 writing-plans 技能的流程制定详细执行计划：
   把任务拆解为可独立验证的小步骤，每步写明目标与验证方式。
4. 将完整计划作为文档输出（文字/列表，或 write_file 写入计划文件），
   并请用户审核：确认计划、或提出修改与补充意见。
5. 用户审核通过后才开始执行；执行中每步完成后验证再继续，任何变更先与用户确认。""",
    },
    "writing-plans": {
        "description": "为多步骤任务制定详细执行计划，交用户审核后再执行",
        "instruction": """# writing-plans：制定执行计划

1. 把任务拆解为可独立执行的小步骤，每步写明目标、动作与验证方式，按依赖顺序排列。
2. 将完整计划作为文档输出（文字/列表，或 write_file 写入计划文件），
   并请用户审核确认后再执行。
3. 按计划逐步执行，每步完成后确认结果验证再继续；计划变更先与用户确认。""",
    },
    "test-driven-development": {
        "description": "测试驱动开发：先写测试再实现，红-绿-重构循环",
        "instruction": """# test-driven-development：测试驱动开发

1. 先用 write_file 编写针对目标行为的测试用例。
2. 运行测试确认失败（红）。
3. 实现最小可用代码使测试通过（绿），必要时重构（重构）。
4. 重复直到所有用例通过并汇报结果。""",
    },
    "systematic-debugging": {
        "description": "系统化调试：复现问题、假设根因、逐一验证，不靠猜测",
        "instruction": """# systematic-debugging：系统化调试

1. 复现问题并读取相关日志/输出（run_command 或 read_file）。
2. 提出最可能的 2-3 个根因假设，按可能性排序。
3. 逐个用最小实验验证假设，排除一个再验证下一个。
4. 定位根因后修复，再复现验证已解决。
5. 修复阶段少汇报多动手：需求与根因已明确时直接改代码并验证，不要反复向用户
   复述方案、请求确认；只有改动会波及生产数据/不可逆操作时才先确认。
6. 单点 bug 自己定位自己改，不要为一个小问题派发子 Agent 编队。""",
    },
    "skill-create": {
        "description": "技能创建：根据用户自然语言描述自动生成市场标准 SKILL.md 技能并加载",
        "instruction": """# skill-create：创建新技能

1. 倾听用户对技能的描述，提炼出：技能名（英文，字母/数字/下划线/连字符，≤50 字符）、
   一句话用途简介、执行流程正文。
2. 设计原则：技能描述的是「用可调用的工具按步骤完成目标」的流程（工具驱动），
   而不是生成 LLM 无法直接操控的独立程序；涉及交互式能力（如实时状态、可视化操作）时，
   流程中明确写明调用哪些工具、每步后如何向用户呈现进度（如输出状态文本/状态图）。
3. **硬性规则：涉及图形化操作界面/可视化交互（仪表盘、可视化面板、画布表单类工具等）时，
   必须按 web 型插件方案处理（见 plugin-create）——创建本地 web 服务器 + 浏览器界面，
   并明确要求 AI 打开浏览器界面给用户；操作者必须是用户本人，严禁 AI 自我演示或与脚本
   自动化程序互演。** 若只是文本/状态展示流程，用工具组合输出即可。
4. 用 create_skill 工具创建：instruction 写清触发条件、执行步骤与规则（markdown）。
5. 创建成功后提示：已可通过 /技能名 或对话描述调用；若用户描述的是可复用的流程，适合沉淀为技能。""",
    },
    "plugin-create": {
        "description": "插件创建：根据用户自然语言描述自动生成可运行插件（MCP server + SKILL.md 技能 + 脚本/资源/示例）并加载",
        "instruction": """# plugin-create：创建新插件

**核心原则：优先创建 LLM 可以直接操控的工具**，而不是生成 LLM 无法直接操控的独立 GUI 应用/完整程序。

1. 先判断是否「需要图形化/可视化交互」：
   - **是**（仪表盘/可视化面板/画布或表单类工具等，用户在界面上直观操作/观看）→ 选 **web 型**：
     生成本地 HTTP Server + 单页浏览器界面（画布/图表/表单用 HTML/JS 内联实现，禁止外网 CDN）+
     同名 MCP 工具（浏览器与 AI 共用同一份状态，LLM 通过工具读写状态）。
     流程：create_plugin(kind="web") → 调 web_url 工具拿访问地址 → browser_open 打开给用户。
   - **否** → 走「能力 → 工具」拆解：把能力拆成 1-5 个职责单一、可单独调用的 MCP 工具
     （参数 schema 清晰、返回可读文本结果），由 LLM 通过工具组合完成整件事；
     工具是「动作/查询」粒度（创建、推进、校验、回退…），不是「打开整个应用」。
2. **web 型硬性要求（必须做到，否则视为未完成）**：
   ①创建后必须立即调用 web_url 获取地址并用 browser_open 打开浏览器界面给用户；
   ②界面必须提供可供真人点击/输入/拖拽的交互控件，操作者必须是用户本人，
     状态变化由用户亲手操作驱动；③严禁 AI 自我演示、自问自答或与脚本/自动化程序互演
     （如 AI 自己连续调用工具推进状态）；AI 只负责读写共享状态、辅助用户（校验输入/展示状态/提示规则）。
3. 确认插件类型：
   - web：本地 Server + 浏览器界面 + MCP 工具（图形化交互类首选）
   - mcp：仅提供工具能力（MCP server）
   - skill：仅沉淀为标准技能（SKILL.md）
   - combined：同时提供技能与 MCP 工具（默认）
4. 用 create_plugin 工具创建，description 写清插件要做什么、提供哪些能力；
   tools/api 的 implementation 用标准库实现真实操作，异常自行捕获并返回错误说明文本。
5. 创建成功后提示：插件已统一存入插件目录，MCP 工具与技能均已自动登记即时生效；
   用户可在设置-插件页查看/管理/停用/删除；MCP 服务器需保存后自动重连。
6. 若用户描述的是可复用的独立能力，适合沉淀为插件；若只是流程，用 skill-create 即可。""",
    },
    "browser-control": {
        "description": "浏览器操控：用独立浏览器实例（CDP）打开网页、截图、按元素编号/文字点击输入、执行JS解析HTML/CSS、读取页面内容，完全不影响用户其他操作",
        "instruction": """# browser-control：AI 操控浏览器（独立实例，不影响用户）

当用户要求"打开浏览器/打开某网站/在网页上登录/填写表单/刷视频/看视频/抓取网页数据/自动操作网页"时，
**优先用浏览器操控工具**（browser_open/browser_navigate/browser_snapshot/browser_click/browser_type/
browser_scroll/browser_eval/browser_html/browser_close），而不是用鼠标键盘去点用户正在用的浏览器。

## 1. 启动（第一步）
- `browser_open`：启动**独立浏览器实例**（独立持久用户目录+调试端口，与用户正在用的浏览器完全隔离，
  不碰鼠标键盘、不影响用户其他操作）。留空 engine 自动找 Edge/Chrome。
- **登录态持久保留**：登录的 cookie/token 保存在持久目录，`browser_close` 后仍保留，
  下次 `browser_open` 自动恢复、无需用户重复登录。

## 2. 打开网页
- `browser_navigate(url=网址)` 打开目标网页（自动补全 http/https）。

## 3. 观察（每步操作前后）
- `browser_snapshot`：返回页面截图 + **可交互元素编号清单 [id] (标签) 文字**。
- 分析网页内容用 `browser_html(selector=可选CSS选择器)` 读取 HTML/文本摘要。
- 需要自定义分析/抓取时用 `browser_eval(js=JS代码)` 直接执行 JavaScript（读取/修改 DOM、调用页面函数）。

## 4. 操作（精确点击/输入/滑动）
- **点击**：`browser_click(id=编号)` 或 `browser_click(text=按钮/链接文字)`，或 `browser_click(selector=CSS选择器)`
  精确定位（如 #submit / .btn-primary / form button）。系统解析 DOM 坐标派发点击，兼容 React/Vue 框架事件。
- **输入**：`browser_type(text=内容, id=输入框编号)`、`browser_type(text=内容, target=占位符/标签)` 或
  `browser_type(text=内容, selector=CSS选择器)`。
- **滑动**：页面内容超出屏幕（列表/长文/评论区/视频流）需查看更多时，用
  `browser_scroll(direction=down/up/top/bottom, amount=像素步长, id/selector=滚动容器)` 滚动后再 snapshot。
- 操作后通常自动返回最新截图确认结果；不确认时可再 `browser_snapshot` 或 `browser_html` 验证。

## 5. 弹窗/iframe/影子DOM/新窗口（关闭按钮点不到时的处理）
- 元素清单由系统**穿透解析**：跨域 iframe（广告/客服弹窗）与 shadow DOM 里的可交互元素（含关闭按钮）
  也已在 browser_snapshot 清单中（编号同样可用），直接按 [id] 或文字点击即可，无需估算坐标。
- 关闭弹窗：优先 `browser_click(text=关闭)` 或按清单找到"关闭/×"图标按钮的编号点击；
  点不到/没反应时先 `browser_snapshot` 看当前状态再操作，不要无脑重复点击。
- 若当前页面里的关闭按钮始终找不到，可能是**新窗口/新标签弹窗**：
  1. `browser_tabs` 列出所有页面标签（含弹窗）；
  2. `browser_switch_tab(id=弹窗编号)` 切到弹窗标签；
  3. 再 `browser_snapshot` 找到关闭按钮后 `browser_click`。
- 确认弹窗是否真的关闭：关闭后再 `browser_snapshot`，弹窗消失即成功。

## 6. 登录页必须等待用户登录完成（强制）
- 打开需要登录的页面（检测到"登录/注册/输入密码/验证码"等登录表单，或访问受限需登录跳转）时：
  1. 先用 `browser_snapshot` / `browser_html` 确认当前确实是登录页。
  2. **不要盲目操作登录表单**：不猜测账号密码、不重复点击登录按钮。
  3. **通知用户登录**：用 ask_user 告知用户"请在已打开的浏览器窗口中完成登录（账号/密码/验证码）"，
     并说明登录完成后会继续任务。
  4. **等待登录完成**：登录期间停止一切网页操作，循环用 `browser_snapshot` / `browser_html`
     检查是否已登录（登录页消失、出现用户头像/主页内容/跳转回目标页、URL 变化等）。
  5. 确认已登录后才继续后续操作；登录超时（用户仍未登录）时再次提示用户，不要跳过登录直接操作。

## 7. 收尾
- 任务完成后 `browser_close` 关闭独立浏览器实例（不影响用户正在用的浏览器）。
- **登录态/token 保留**：关闭后 cookie/token 仍在持久目录，下次打开浏览器自动恢复，无需重复登录。

## 8. 原则
- 每步先想清楚目标再操作；操作结果不确定时 snapshot/html 验证，失败先自查再换方案。
- 涉及账号密码/验证码/手机验证等敏感登录信息时，一律交给用户手动完成，AI 不代为填写、不猜测。""",
    },
    "custom-ui-ux": {
        "description": "自定义 AI 面板 UI/UX（Cordis 热插拔）：AI 完整自定义 PyQt6 界面，支持深色/浅色双主题与自定义主题色板，创建/编辑/切换/删除 UI/UX 包",
        "instruction": """# custom-ui-ux：自定义 AI 面板界面（热插拔 UI/UX）

当用户要求"自定义界面 / 换个风格 / 改 UI / 换个主题皮肤 / 重新设计面板"时使用本技能。

## 1. 理解 UI/UX 包
每个 UI/UX 包是一个目录：~/.winapp_migrator/ui_ux/<name>/，包含：
- ui_ux.json        元数据（name/description/is_builtin/theme 自定义主题色板）
- build_ui.py       必须：def build_ui(self): 构建面板
- build_welcome.py  可选：def build_welcome(self): 返回欢迎页 QWidget
- panel.qss         可选：QSS 样式表，深度定制全部组件外观（按钮/滑块/输入框/对话框等）
- resources/        可选：图标/字体/图片等资源文件
内置 default 包不可删除；自定义包加载失败会自动回退默认，无需担心破坏面板。
包可导出为 zip 资源包（manage_uiux op=export）分享给其他开发者，对方 op=import 即装即用。

## 2. 生成流程
1. 先问清用户想要的风格/布局/配色（信息不足用 ask_user，禁止瞎猜）。
2. 用 manage_uiux 工具列出已有包：参数 op=list。
3. 生成**完整自定义** build_ui.py 代码（见下方契约与示例），用 manage_uiux op=create 创建：
   {"op":"create","name":"包名(英文)","description":"一句话风格","build_ui":"完整代码",
    "build_welcome":"可选","theme":"{\\"dark\\":{...},\\"light\\":{...}} 可选自定义主题色板",
    "qss":"可选 QSS 样式表深度定制组件外观"}
4. 可选 op=activate 立即切换（热插拔生效）；若未激活，让用户在 设置→UI/UX 自定义 里切换预览。

## 3. 契约（务必遵守，否则面板出错自动回退默认）
- def build_ui(self): 的 self 是 AgentPanel 实例。
- **默认必须完整自定义**：自建全部契约控件属性与侧边窗口，包括 self._root_lay /
  self.title / self.session_combo / self.new_btn / self.settings_btn / self.token_label /
  self.wf_label / self.msg_area / self.msg_lay / self._welcome_page / self.msg_stack /
  self.cmd_list / self.input / self.attach_btn / self.optimize_btn / self.model_combo /
  self.action_btn / self.todos_win / self.git_win / self.wt_win / self.code_win；
  且正确连接 self._new_session / self._open_settings / self._on_session_selected /
  self._send / self._on_action_clicked / self._pick_attachments / self._on_optimize_clicked /
  self._on_model_combo / self._on_cmd_selected / self._clear_chat 等已有方法。
  可选复用：self.toggle_token_stats() / self.open_token_stats() 弹出 token/上下文统计浮层
  （上游真实用量 + 占用进度条 + 阈值刻度 + 缓存命中率），无需自建统计界面。
- 仅当用户明确说"在默认基础上微调"时才允许 build_ui 先调用 self.build_default_ui() 再改样式。
- **PyQt6 环境（禁止 import 与 PyQt5 API）**：本项目基于 PyQt6，build_ui 的命名空间已注入全部
  所需组件/常量/函数，**禁止 import/from**（导入会加载失败回退默认）。禁止 PyQt5 专属写法：
  QFont.Bold → QFont.Weight.Bold、Qt.AlignLeft → Qt.AlignmentFlag.AlignLeft、
  Qt.Checked → Qt.CheckState.Checked、QFontMetrics.width → horizontalAdvance。
- **必须更改全部 UI/UX 组件（硬性要求，禁止任何组件保留默认外观）**：按钮（形状/圆角/渐变/
  内边距/悬停/禁用）、输入框（边框/圆角/聚焦）、下拉框（箭头/列表项高亮）、滚动条（轨道/滑块）、
  菜单 / Tooltip / 对话框、图标尺寸与颜色、字体大小粗细、间距留白、边框阴影、列表选中态，
  都要深度自定义样式。
- **必须提供 panel.qss（qss）**：用 QSS 统一覆盖按钮/输入框/下拉框/滚动条/菜单/Tooltip/
  对话框/列表等全部组件类型；颜色可用主题变量名（BG/TEXT/ACCENT 等）。
- **必须提供 theme 的 dark/light 两套色板**：ui_ux.json 的 theme 字段为 {"dark":{常量名:色值,...},
  "light":{常量名:色值,...}}，键必须是上面的主题常量名。提供后用户可在当前 UI/UX 下独立换肤。
- **4 个子面板完全自定义（布局/背景/位置）**：主面板之外有 4 个独立悬浮子窗口——任务清单
  self.todos_win、Git 面板 self.git_win、工作树 self.wt_win、代码预览 self.code_win。可用注入的类
  （TodosWindow/GitLogWindow/WorktreeWindow/CodePreviewWindow）或自定义 QWidget 子类替换，
  布局/背景（setStyleSheet/QSS）/控件全部自定义。位置默认由守卫自动停靠（todos/git/wt 堆叠主面板
  左侧、code 贴右侧）；对窗口置 `win._ai_managed = True` 后完全接管：守卫不再重定位/改大小/按设置
  开关隐藏，由你自行 move()/resize()/show()/hide() 自由摆放。数据接口（面板会自动调用）：
  todos_win.update_todos(list)；git_win.refresh() 与 git_win.list（QListWidget）；
  wt_win.refresh()（双击文件可触发 self._open_code_preview(path)，需窗口内保存主面板引用）；
  code_win.show_file(path)。4 个子面板同样必须深度自定义，颜色用主题常量，深/浅色两套适配。
- **按钮注册接口（与 UI/UX 解耦，恒可用）**：self.register_btn(btn_id, text=..., callback=...,
  tooltip=..., order=0) / self.unregister_btn(btn_id) / self.list_registered_btns()。
  注册的按钮恒渲染在面板顶部按钮栏——不管当前/以后切换任何 UI/UX 包、甚至完全重构界面，
  按钮都保持可见且点击回调可用。代码里可直接绑定任意回调；AI 对话运行中可用工具
  register_panel_btn op=register 注册（action 限内置功能：send/new_session/settings/
  attach/optimize/clear_chat），op=unregister 注销。
- **深度自定义组件**：build_ui 命名空间覆盖 Qt 全组件——布局（QVBoxLayout/QHBoxLayout/
  QGridLayout/QFormLayout）、控件（QLabel/QPushButton/QToolButton/QComboBox/QScrollArea/
  QStackedWidget/QListWidget/QWidget/QSlider/QCheckBox/QRadioButton/QLineEdit/QFrame/QMenu/
  QTabWidget/QSplitter/QProgressBar/QDialog/QScrollBar/QMessageBox/QButtonGroup）、绘图
  （QPixmap/QFont/QIcon/QColor/QPalette/QPainter/QPen/QBrush/QLinearGradient/
  QRadialGradient/QTextOption）、动画线程（QTimer/QPropertyAnimation/QVariantAnimation/
  QEasingCurve/QThread）、阴影特效（QGraphicsDropShadowEffect）等，可任意组合自定义组件。
- **panel.qss 深度样式**：包根目录 panel.qss（QSS）可定制全部组件外观——按钮形状/圆角/渐变、
  字体样式、图标大小、滑动条轨道/滑块、输入框、对话框、菜单、滚动条、Tooltip 等；
  用 manage_uiux op=create 的 qss 参数或 op=set_qss 写入。
- build_welcome(self) 返回一个 QWidget（无对话时显示）。
- 可用命名空间：上面全部 Qt 组件 + 主题常量 + 样式常量（_BTN_GHOST/_BTN_COMPACT/
  _BTN_GHOST_ACCENT/_BTN_PRIMARY/_BTN_DIM/_QCOMBO/_BTN_ICON/_BTN_DANGER）+ 工具函数
  （_line_icon/_svg_icon/_scrollbar_css/_app_icon_path/_std_icon/_file_icon）。
- **预览能力（内置，用户/你均可用来搭 Markdown/HTML/Web 渲染预览面板）**：
  - `pv = create_preview_widget(kind='markdown'|'html'|'text'|'web')` 或 `pv = PreviewPanel(kind=..., parent=self)`
    一键创建预览控件，加入任意布局即可用：
    pv.render_markdown(text)（Markdown→当前主题配色）、pv.set_html(html)、pv.set_text(text)、
    pv.set_url(url)。kind='web' 需已安装 QtWebEngine（未装自动回退静态渲染）。
  - `render_markdown_html(md, fragment=False, css_extra='')` 取完整 HTML 文档串（可嵌入他人文档）；
    `web_engine_available()` / `new_web_view()` 判断/创建浏览器内核。
- 禁止：import/from、os/subprocess/eval/exec/open/__import__、访问 self 之外的全局状态。
  只能操作 self 与以上命名空间。

## 3.5 液体玻璃/玻璃拟态（Liquid Glass / Glassmorphism）设计要点
当用户要求"液体玻璃/毛玻璃/玻璃拟态/磨砂玻璃"风格时，必须做出以下效果，禁止只给普通半透明矩形：

0. **真毛玻璃 = 背景模糊透出（核心）**：Qt 没有 CSS 的 `backdrop-filter: blur()`，光靠半透明只是"雾面塑料"，不是毛玻璃。已注入两个辅助函数（可直接调用，无需自己写模糊）：
   - `_box_blur(pixmap, radius)`：对 QPixmap 做高斯模糊，返回模糊后的 QPixmap。
   - `_glass_fill(painter, rect, radius=18, tint=None, bg_pixmap=None, accent=None, phase=0.0)`：在 painter 上画一块真正的磨砂玻璃——自动完成「模糊背景透出 → 纯透明底色 → 顶部高光 → 对角液光 → 底部微光 → **边缘反光描边**」。
   **用法**：先把面板背景（渐变+光斑）画到一张 QPixmap 快照，再把它作为 `bg_pixmap` 传给 `_glass_fill`，即可得到"透出并模糊背后内容"的真毛玻璃。
1. **纯透明无色（默认，勿染色）**：`tint=None`、`accent=None`（均默认）。玻璃只透出模糊背景原色 + 白色高光/反光，**不混入 PANEL 蓝、ACCENT 紫等任何彩色**。用户要"纯透明/无色"时必须这样传，禁止再 `tint=QColor(PANEL)` 染色——那是"玻璃太实"的根因。
2. **必须先有可见的背景变化**：在主题色板里让 `BG` 与 `BG_BOTTOM` 取不同色值（深色如 #0B1226 → #1B2A4A，浅色如 #EAF1FB → #D6E6FA）。玻璃透出的是这个渐变；BG 与 BG_BOTTOM 相同就无透明感。
3. **液态动感动画（必须）**：玻璃要"活起来"。在 `_GlassContainer` 里加一个 `QTimer`（interval 40ms）循环推进 `self._phase`（0→1 循环），`_advance()` 里 `self._phase += 0.02` 后 `self.update()`；paintEvent 把 `phase=self._phase` 传给 `_glass_fill`，高光/液光/微光随 phase 流动。多个容器各配一个 QTimer 即可。
4. **边缘反光（玻璃厚度感）**：玻璃边缘要有 1px 亮白描边（外圈），`_glass_fill` 已内置；手绘时务必补上纯白描边，否则边缘"糊"在背景里。
5. **高光/光斑/流光都用白色**：顶部白色高光、白色对角液光、白色径向光斑（`QColor(255,255,255,alpha)`）。不要用 ACCENT 彩色光斑。
6. **投影用中性灰**：`QGraphicsDropShadowEffect` 的 `setColor` 用 `QColor(0,0,0,120)`（深色）/ `QColor(0,0,0,60)`（浅色），blurRadius 20-40，offset 0-3px。不要用 ACCENT 彩色投影。
7. **避免扁平**：按钮/输入框/卡片必须有圆角（12-20px）、白色渐变高光；悬停态明显变亮。
8. **子面板也要玻璃化**：todos_win / git_win / wt_win / code_win 至少设置圆角、纯白透明背景、玻璃边框和阴影。**注意子面板宽度会影响定位**——不要随意把 `setFixedWidth` 改成比默认 280 大太多，否则左锚点按实际宽度计算后仍可能挤压主面板。
9. **⚠️ 必须用 QPointF**：`QRadialGradient(QPointF(x, y), radius)` / `QLinearGradient(QPointF, QPointF)`。传 `QPoint`/`QRect.topLeft()`（返回 QPoint）会在 paintEvent 抛 TypeError → 面板闪退！

首选模板（纯透明无色 + 液态动感动画，用注入的 `_glass_fill`）：
```python
class _GlassContainer(QWidget):
    def __init__(self, parent=None, radius=18):
        super().__init__(parent)
        self._radius = radius
        self._phase = 0.0
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        eff = QGraphicsDropShadowEffect(self)
        eff.setBlurRadius(34)
        eff.setColor(QColor(0, 0, 0, 120))   # 中性灰阴影（无色）
        eff.setOffset(0, 3)
        self.setGraphicsEffect(eff)
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._advance)
        self._timer.start()

    def _advance(self):
        self._phase += 0.02
        if self._phase > 1.0:
            self._phase -= 1.0
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        rect = self.rect()
        # 1) 背景快照（面板根渐变 + 白色光斑，无色），供玻璃"透出并模糊"
        bg = QPixmap(rect.size())
        bg.fill(Qt.GlobalColor.transparent)
        bp = QPainter(bg)
        g = QLinearGradient(0, 0, rect.width(), rect.height())
        g.setColorAt(0, QColor(BG))
        g.setColorAt(1, QColor(BG_BOTTOM))
        bp.fillRect(rect, g)
        for fx, fy in ((0.15, 0.12), (0.85, 0.9)):
            gp = QRadialGradient(QPointF(rect.width() * fx, rect.height() * fy), rect.width() * 0.55)
            c = QColor(255, 255, 255, 40)
            gp.setColorAt(0, c)
            gp.setColorAt(1, QColor(255, 255, 255, 0))
            bp.fillRect(rect, QBrush(gp))
        bp.end()
        # 2) 纯透明液态玻璃（无色，随 phase 流动；不染色、不混入彩色）
        _glass_fill(p, rect, radius=self._radius, tint=None,
                    bg_pixmap=bg, accent=None, phase=self._phase)
        p.end()
```
注意：`paintEvent` 里只做绘制，不要创建子控件；自定义类必须定义在 `build_ui` 内部，所有名称来自注入的命名空间（含 `_glass_fill` / `_box_blur` / `QTimer`）。

## 4. 完整自定义示例（默认必须此风格，颜色用主题常量）
```python
def build_ui(self):
    root = QVBoxLayout(self)
    root.setContentsMargins(16, 14, 16, 14)
    self._root_lay = root
    # 侧边窗口必须创建
    self.todos_win = TodosWindow(self)
    self.git_win = GitLogWindow(self)
    self.wt_win = WorktreeWindow(self)
    self.wt_win.file_open_requested.connect(self._open_code_preview)
    self.code_win = CodePreviewWindow(self)
    # 顶栏（主题常量配色，深色浅色自适应）
    top = QHBoxLayout()
    self.title = QLabel("CUSTOM")
    self.title.setStyleSheet(f"color: {ACCENT}; font-size: 18px; font-weight: 800;")
    top.addWidget(self.title)
    self.session_combo = QComboBox()
    self.session_combo.setMinimumWidth(150)
    self.session_combo.setItemDelegate(_SessionStatusDelegate(self))
    self.session_combo.currentIndexChanged.connect(self._on_session_selected)
    top.addWidget(self.session_combo)
    self.new_btn = QPushButton("+")
    self.new_btn.clicked.connect(self._new_session)
    self.new_btn.setStyleSheet(_BTN_ICON)
    top.addWidget(self.new_btn)
    self.settings_btn = QPushButton("设置")
    self.settings_btn.clicked.connect(self._open_settings)
    self.settings_btn.setStyleSheet(_BTN_GHOST)
    top.addWidget(self.settings_btn)
    top.addStretch(1)
    self.token_label = QLabel("0 tk")
    self.token_label.setStyleSheet(f"color: {TEXT_DIM}; font-size: 12px;")
    top.addWidget(self.token_label)
    self.wf_label = QLabel("")
    self.wf_label.setStyleSheet(f"color: {TEXT_DIM}; font-size: 12px;")
    top.addWidget(self.wf_label)
    root.addLayout(top)
    # 聊天区
    self.msg_area = QScrollArea()
    self.msg_area.setWidgetResizable(True)
    self.msg_area.setStyleSheet(
        "QScrollArea { background: transparent; border: none; }" + _scrollbar_css(8, 4, both=True))
    container = QWidget()
    container.setStyleSheet("background: transparent;")
    self.msg_lay = QVBoxLayout(container)
    self.msg_lay.addStretch(1)
    self.msg_area.setWidget(container)
    self._welcome_page = self._build_welcome()
    self.msg_stack = QStackedWidget()
    self.msg_stack.addWidget(self.msg_area)
    self.msg_stack.addWidget(self._welcome_page)
    root.addWidget(self.msg_stack, 1)
    self.cmd_list = QListWidget()
    self.cmd_list.hide()
    root.addWidget(self.cmd_list)
    # 输入行
    row = QHBoxLayout()
    self.input = _DropTextEdit()
    self.input.setPlaceholderText("给我描述你的任务吧…")
    self.input.submit.connect(self._send)
    self.input.setStyleSheet(
        f"QPlainTextEdit {{ background: {PANEL}; color: {TEXT}; border: 1px solid {BORDER};"
        "border-radius: 10px; padding: 5px 10px; font-size: 14px; }}"
        f"QPlainTextEdit:focus {{ border: 1px solid {ACCENT}; }}")
    row.addWidget(self.input, 1)
    self.attach_btn = QPushButton("+")
    self.attach_btn.clicked.connect(self._pick_attachments)
    self.attach_btn.setStyleSheet(_BTN_ICON)
    row.addWidget(self.attach_btn)
    self.optimize_btn = QPushButton("优化")
    self.optimize_btn.clicked.connect(self._on_optimize_clicked)
    self.optimize_btn.setStyleSheet(_BTN_GHOST)
    row.addWidget(self.optimize_btn)
    self.model_combo = _ArrowComboBox()
    self.model_combo.setMinimumWidth(150)
    self.model_combo.setStyleSheet(_QCOMBO)
    self.model_combo.currentIndexChanged.connect(self._on_model_combo)
    row.addWidget(self.model_combo)
    self.action_btn = QPushButton("发送")
    self.action_btn.setStyleSheet(_BTN_PRIMARY)
    self.action_btn.clicked.connect(self._on_action_clicked)
    row.addWidget(self.action_btn)
    root.addLayout(row)
    return self
```
子面板完全自定义示例（放在 build_ui 内；_ai_managed=True 自由摆放，默认则自动停靠）：
```python
# 方案 A：继承内置类深度改版（数据接口自动保留）
class _MyGit(GitLogWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("myGitWin")
        self.setStyleSheet(f"QWidget#myGitWin {{ background: {CARD}; "
                           f"border: 2px solid {ACCENT}; border-radius: 10px; }}")
        self.setFixedWidth(320)          # 自定义宽度
        self._ai_managed = True          # 完全接管：守卫不再重定位/改大小
        self.move(30, 80)                # 自由摆放位置
        self.show()
self.git_win = _MyGit(self)
# 方案 B：全新自定义窗口（实现所需接口即可）
class _MyTodos(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self.setObjectName("myTodos")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"QWidget#myTodos {{ background: {CARD}; }}")
        self.setFixedWidth(240)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        self._list = QListWidget()
        self._list.setStyleSheet("QListWidget { border: none; }")
        lay.addWidget(self._list)
    def update_todos(self, todos: list):
        self._list.clear()
        for t in todos:
            self._list.addItem(str(t))
self.todos_win = _MyTodos(self)
self.todos_win._ai_managed = True        # 自由摆放，守卫不干预
self.todos_win.move(60, 150)
self.todos_win.show()
```
按钮注册接口示例（放在 build_ui 内；与 UI/UX 解耦，任何界面都渲染在顶部按钮栏）：
```python
self.register_btn("quick_send", text="快速发送", callback=self._on_action_clicked,
                  tooltip="发送/停止当前任务")
self.register_btn("clear", text="清空", callback=self._clear_chat, order=1)
```
主题色板示例（随 create 的 theme 参数输出，两套都要）：
```json
{"dark": {"BG": "#0B0F1A", "PANEL": "#12182B", "CARD": "#1A2138", "TEXT": "#E8EAF6",
          "TEXT_DIM": "#9AA3C0", "ACCENT": "#6D5DF6", "ACCENT_HOVER": "#8B7CF8",
          "HOVER": "#232C48", "USER_BG": "#6D5DF6"},
 "light": {"BG": "#F6F5FF", "PANEL": "#FFFFFF", "CARD": "#FFFFFF", "TEXT": "#1E1B36",
           "TEXT_DIM": "#6B6790", "ACCENT": "#6D5DF6", "ACCENT_HOVER": "#5A4BE0",
           "HOVER": "#EDEBFC", "USER_BG": "#6D5DF6"}}
```
panel.qss 样式表示例（随 create 的 qss 参数或 op=set_qss 写入，深度定制组件外观）：
```css
QPushButton { border-radius: 16px; padding: 8px 20px; }
QPushButton#sendBtn { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
    stop:0 #6D5DF6, stop:1 #9A7BFF); border-radius: 20px; font-weight: 800; }
QSlider::groove:horizontal { height: 6px; border-radius: 3px; background: #2A3350; }
QSlider::handle:horizontal { width: 18px; height: 18px; margin: -6px 0;
    border-radius: 9px; background: #6D5DF6; }
QLineEdit { border-radius: 12px; padding: 8px 14px; }
QScrollBar::handle:vertical { border-radius: 4px; }
```

## 5. 编辑、样式、主题与共享
- 读取包内容：manage_uiux op=read，传 name + part（build_ui/build_welcome/theme/qss/meta）。
- 修改 build_ui：manage_uiux op=update（name + 新代码），或 read_file/edit_file 直接改。
- 深度定制组件样式：manage_uiux op=set_qss（name + qss QSS 样式表），定制按钮形状/滑动条/
  输入框/对话框/图标/字体等全部组件外观。
- 修改主题色板：manage_uiux op=update_theme（name + theme JSON），一键换肤。
- 依赖打包（插件/MCP server）：包可在元数据声明依赖（op=create 传 plugins=["插件名"]、
  mcp=["MCP名"]，或 op=deps name+plugins+mcp 更新）。导出 zip 时对应插件目录与 MCP 配置
  会一并打包，对方导入自动安装依赖，实现"即装即用"。
- 复制包：manage_uiux op=duplicate（name + new_name），便于基于现有包派生新风格。
- 导出 zip 共享：manage_uiux op=export（name + dest 目标目录），生成 zip 资源包分享给开发者
  （若声明了依赖会含 plugins/ 与 mcp_servers.json）；对方导入：manage_uiux op=import
  （zip_path），自动校验安全（防路径穿越/文件白名单）并安装依赖，同名覆盖。
- 切换：manage_uiux op=activate，name 传包名（"default" 切回默认）；面板热插拔立即重建。
- 删除用户包：manage_uiux op=delete（内置包会被拒绝）。
- 列出：manage_uiux op=list；查看当前：op=get。

## 6. 收尾
- 汇报：创建的包名、是否带自定义主题、保存位置、如何切换/删除。
- 强调安全：出问题会自动回退默认界面，不会破坏面板功能。""",
    },
    "deep-customize": {
        "description": "深度自定义总入口（Cordis 一切皆可替换/热插拔）：盘点工作流/技能/插件/UI/UX/MCP/按钮全部可自定义维度，引导用户对任意功能做深度自定义或新增能力",
        "instruction": """# deep-customize：深度自定义 / 新增功能总入口（Cordis）

当用户要求"深度自定义 / 深度定制 / 全面自定义 / 自定义功能 / 新增功能 / 添加功能 / 扩展功能 / 改造 / 把 xxx 改得更好用"时使用本技能。
核心思想：Cordis —— **一切皆可替换、热插拔生效**。用户工作流/UI/UX/插件/技能覆盖内置实现，切换即生效，缺失自动回退内置，永不破坏系统。

## 0. 优先扩展、禁止滥新建（最高优先级，先于一切）
**小功能的改动，一律先扩展现有资源，禁止新建工作流 / UI/UX 资源包。** 判定与落地顺序：
- **小功能 = 新增功能面板、新增子 agent、新增/微调工具、小 UI 调整、小钩子**，这类改动直接注入当前工作流或现有 UI/UX：
  - **新增子 agent** → `list_sub_agents` 看现状 → `register_sub_agent`（name/description/goal/allowed）注册进**当前工作流**，立即可作为 `sub_<name>` 工具被主 Agent 调用，不新建工作流。
  - **新增功能面板** → `manage_uiux` op=list 看现有包 → **优先**用 `register_feature_panel`（title/width/height/python）把 panel.py 写进**当前工作流**，成为可拖拽扩展浮窗；仅当你确认面板属于某个独立 UI/UX 包的整体主题时才用 `manage_uiux` op=update 挂到现有包。
  - **新增/覆盖工具** → `edit_agent_file(name=<当前工作流>, file="tools.py")` 写入当前工作流 tools.py，绝不为单独一个工具新建工作流。
  - **自定义代码带第三方 import** → `set_feature_deps`（op=declare deps=["xxx"]）写入当前工作流 requirements.txt，代码顶部可带第三方 import，加载时自动补齐依赖。
  - **小 UI 调整 / 钩子** → `manage_uiux` op=read + set_qss/update_theme/update 现有包，或 `edit_agent_file` 改 agent.py 钩子。
- **只有功能大且独立**（整套 Agent 人格、独立界面体系、专用工具集，现有工作流/UI/UX 完全承载不了）时才新建工作流（`create_workflow`）或 UI/UX 包（`manage_uiux` op=create）。
- 判断口诀：**能不能塞进现有工作流面板.py / 工具.py / 子agent 注册？能就绝不新建资源包。**

## 1. 先盘点再动手
1. 信息不足先 `ask_user` 问清：想自定义/新增什么功能、目标效果、使用场景（禁止瞎猜）。
2. 调用 `inspect_customization` 盘点当前全部可自定义维度现状（工作流/技能/插件/UI/UX/MCP/按钮/子agent/扩展面板），
   把结果与用户需求对齐，确认要改哪个维度。
3. 先按「第 0 节」判断是小功能还是大而独立的功能：**小功能直接走扩展通道注入现有工作流/UI/UX，不新建。**

## 2. 按维度落地（扩展通道，先扩展后新建，任选/组合）
- **工作流（Agent 人格/LLM/工具/技能/插件/MCP 配置）**：
  `list_workflows` 看现状 → `create_workflow`（files 留空建全部核心文件，或只传单文件定制某一能力）
  → `inspect_workflow` 查看 → `edit_agent_file` 编辑 agent.py（人格/钩子）/llm.py（客户端）/
  tools.py（自定义工具）→ `switch_workflow` 热插拔生效。
- **技能（SKILL.md 市场标准）**：`create_skill` 新建；或 skill-mgmt 导入/启停/删除。
  新技能描述写清触发条件与执行流程，创建后 /技能名 即可调用。
- **插件（mcp/skill/combined/web 四型）**：`create_plugin` AI 生成可运行插件（含 MCP server +
  SKILL.md），web 型本地 HTTP Server + 浏览器界面（创建后必须 web_url + browser_open 打开给用户，
  操作者必须是用户本人，严禁 AI 与脚本自动对战）。
- **UI/UX 包（面板深度自定义）**：`manage_uiux` op=list 看现状 → op=create（build_ui 完整自定义 +
  theme 双主题色板 + qss 样式表）→ op=activate 热插拔切换；或 op=update/set_qss/update_theme 迭代。
- **面板按钮（与 UI/UX 解耦恒显示）**：`register_panel_btn` op=register 注册顶部按钮栏按钮。
- **子 agent（小功能的常用落地通道，绑定当前工作流）**：`list_sub_agents` 看现状 → `register_sub_agent`
  （name/description/goal/allowed）注册进当前工作流，立即作为 `sub_<name>` 工具可调用，不新建工作流。
  用户说「创建一个子 agent / 新增一个下属 agent」时必须走这条注册通道：注册式才同时支持
  @<名> 直接调用、主 Agent 调用 `sub_<name>`、以及由主 Agent 逐次分配是否加入共同上下文空间与
  传参 context。**不要用 `create_agent` / `register_agent_network(op=create_agent)` 顶替**——
  那只创建「主 Agent 人格」（@agent 会话级切换），主 Agent 调不到它。
- **功能面板（小 UI 面板的常用落地通道，绑定当前工作流）**：`register_feature_panel`
  （title/width/height/python，python 提供 build_panel(owner) 返回 QWidget）把 panel.py 写入当前工作流，
  扫描后成为可拖拽扩展浮窗，不新建 UI/UX 包。
- **依赖（自定义代码带第三方 import）**：`set_feature_deps` op=declare 声明（写入当前工作流 requirements.txt，
  deps=["pandas"]）；op=install 立即安装；op=list 查看。加载自定义代码时自动补齐缺失依赖，无需手动 pip。
- **MCP 服务器**：通过插件（create_plugin kind=mcp/combined）或工作流 mcp.json 声明。

## 3. 收尾
- 汇报：改了什么维度、新功能如何触发/使用、如何切回/删除（安全兜底：工作流缺失回退内置、
  UI/UX 加载失败回退默认、插件可停用/删除）。
- 若用户想先沉淀可复用能力：适合插件用 plugin-create，适合流程用 skill-create，
  适合整套 Agent 定制用 create-cordis。""",
    },
}

DEFAULT_AGENTS = [
    {"name": "zhuzhu Copilot", "description": "zhuzhu Copilot：浏览器操控 + 文件/命令自动化高质量完成任务",
     "persona": "你是 zhuzhu Copilot，运行在 Windows 上的桌面 AI 助手，性格谨慎可靠、注重安全，"
                "擅长把复杂任务拆解为可验证的小步骤，每步先想清楚后果再动手，"
                "需要操作网页时使用独立浏览器操控工具（不影响用户正在用的浏览器），"
                "失败时先自查再换方案，直到任务高质量完成。",
     "rules": [
         "1. 复杂任务先拆解为步骤清单，按依赖顺序执行，每步完成后确认结果正确再进入下一步"
         "（网页操作结果用 browser_snapshot 验证）。",
         "2. 每次操作前用一句话说明意图（会弹窗由用户确认）。",
         "3. 操作结果不确定时验证：网页操作 browser_snapshot/browser_html 确认；"
         "失败则分析原因并换方案重试，禁止盲目重复。",
         "4. **浏览器任务一律用独立浏览器操控工具**：browser_open 启动（独立持久用户目录+调试端口，"
         "与用户正在用的浏览器完全隔离，不碰鼠标键盘、不影响用户其他操作），"
         "browser_navigate 打开网页，browser_snapshot 拿页面元素清单，"
         "browser_click/browser_type 按编号/文字操作，browser_eval/browser_html 解析页面，"
         "完成后 browser_close 关闭（登录态自动保留）。",
         "5. 打开应用前先用 find_app 精确定位可执行路径，避免猜错名称；找不到时用 search_files 兜底。",
         "6. 文件操作（查找/创建/修改/删除/读取）优先在工作目录内执行：用户设置了工作目录时，"
         "未指定完整路径默认在工作目录内执行，相对路径也基于工作目录解析；同时避开系统关键目录"
         "（Windows、Program Files 等），删除系统关键目录内容会被沙盒拒绝。",
         "7. 用户需求不明确、缺少关键信息时，优先基于上下文合理推断并自主推进；"
         "仅在推断会明显做错方向（目标文件/对象/期望结果不明）、或涉及不可逆/危险操作时，"
         "用 ask_user 一次问清，严禁编造关键信息。",
         "8. 完成任务后总结：做了什么、结果如何、关键输出在哪，不要做多余操作。",
         "9. 登录相关：涉及账号密码/验证码/手机验证等敏感登录信息时，一律交给用户手动完成，"
         "AI 不代为填写、不猜测；登录期间停止网页操作并等待用户完成。",
         "10. 长/复杂任务管理：任务步骤多时，先输出执行计划再动手；"
         "每完成一个阶段用 save_memory 保存进度与关键状态（已完成/下一步）；"
         "上下文被自动压缩后，先用 load_memory 恢复任务目标与进度，避免遗忘开头。",
         "11. 失败纠错：工具调用失败（超时/未找到/被拒绝）时，分析原因（URL 错误/元素未加载/"
         "选择器不匹配/参数错误），再换方案重试（换 text/selector 定位、先 browser_snapshot 刷新清单、"
         "browser_scroll 滚动后重试）；同一操作最多重试 2 次，之后必须换方案或问用户，禁止无脑循环。",
     ],
     "tool_instructions": {
         "browser_open": {
             "理解": "启动独立浏览器实例（Edge/Chrome，独立持久用户目录+调试端口，与用户正在用的浏览器完全隔离）。"
                    "浏览器任务（打开网页/登录/填表/抓取/自动操作网页）第一步先 browser_open，"
                    "之后用 browser_navigate/browser_snapshot/browser_click/browser_type/browser_eval/"
                    "browser_html 完成操作，最后 browser_close 关闭。"
                    "**登录态/cookie/token 持久保存，关闭后再打开自动恢复，用户无需重复登录。**",
             "执行拆分": ["浏览器相关任务第一步先 browser_open 启动独立实例",
                          "browser_open 后 browser_navigate 打开目标网址",
                          "browser_snapshot 看页面元素清单，browser_click/browser_type 按编号/文字操作",
                          "browser_html/browser_eval 读取或解析页面内容",
                          "完成后 browser_close 关闭实例（登录态自动保留）"],
         },
         "browser_navigate": {
             "理解": "在独立浏览器中打开网页（自动补全 http/https）。需先 browser_open。",
             "执行拆分": ["确认已 browser_open",
                          "传入完整网址或可补全的域名，打开后自动截图确认"],
         },
         "browser_snapshot": {
             "理解": "截取独立浏览器当前页面并返回可交互元素编号清单 [id]（截图+语义清单）。"
                    "每步操作前后先 snapshot 看页面状态与元素。"
                    "**若页面是登录页（出现登录/密码/验证码等表单），必须立即通知用户登录并等待其完成，禁止直接操作登录表单。**",
             "执行拆分": ["用 browser_snapshot 观察页面，先判断是否登录页",
                          "若检测到登录页：停止操作，用 ask_user 请用户在打开的浏览器窗口完成登录，"
                          "之后循环 snapshot/html 等待登录完成（登录页消失/出现用户信息）",
                          "确认已登录后再按清单 [id] 或文字用 browser_click/browser_type 继续操作"],
         },
         "browser_click": {
             "理解": "在独立浏览器中点击元素：id/text/selector（CSS 选择器）三选一定位，系统解析 DOM 坐标派发点击"
                    "（原生 click+合成事件兜底，兼容 React/Vue）。"
                    "**点击前先确认页面非登录页且已登录；登录表单上的按钮（登录/注册/验证码）不代为操作。**",
             "执行拆分": ["先 browser_snapshot 拿元素清单并确认页面状态（是否已登录）",
                          "若未登录/是登录页：先通知用户登录并等待完成，不要点登录按钮",
                          "定位：优先 id 或 text；元素不易用文字描述时用 selector 精确指定",
                          "已登录后 browser_click 点击，返回最新截图确认结果"],
         },
         "browser_type": {
             "理解": "在独立浏览器的输入框中输入文本：id/target/selector 三选一定位，先点击聚焦再输入。"
                    "**账号/密码/验证码输入框不代为填写，一律由用户手动输入。**",
             "执行拆分": ["先 browser_snapshot 找输入框并确认页面状态",
                          "若是登录表单（账号/密码/验证码）：不填写，通知用户手动登录并等待完成",
                          "定位输入框用 id/target，或用 selector 精确定位",
                          "普通输入框用 browser_type 输入，必要时再点击提交/回车"],
         },
         "browser_scroll": {
             "理解": "滚动独立浏览器页面或指定容器：direction=up/down/left/right/top/bottom，amount 像素步长，"
                    "id/selector 指定滚动容器（留空滚动整页）。页面内容超屏（列表/长文/评论区/视频流）时使用。",
             "执行拆分": ["页面内容超出屏幕时用 browser_scroll 滚动（默认向下滚一屏）",
                          "找长列表/评论区等特定容器时用 id/selector 指定滚动容器",
                          "滚动后 browser_snapshot 看新内容"],
         },
         "browser_eval": {
             "理解": "在独立浏览器页面执行 JavaScript 并返回结果（读取/修改 DOM、调用页面函数、抓取数据）。",
             "执行拆分": ["直接执行 JS，返回文本结果",
                          "用于解析 HTML/CSS、模拟操作、抓取数据"],
         },
         "browser_html": {
             "理解": "读取独立浏览器当前页面 HTML/文本内容，可传 CSS 选择器只读指定区域。用于分析网页与确认结果。"
                    "**若内容显示需登录/登录页，须通知用户登录并等待完成后再继续。**",
             "执行拆分": ["读取页面内容摘要或指定区域 HTML，判断是否需登录",
                          "需登录时先通知用户登录并等待完成",
                          "登录确认后用于确认操作结果或抓取数据"],
         },
         "browser_close": {
             "理解": "关闭独立浏览器实例（仅关闭 AI 启动的专用实例，不影响用户浏览器）。"
                    "登录态/cookie/token 保留在持久目录，下次打开自动恢复。任务完成后调用清理资源。",
             "执行拆分": ["浏览器任务完成后调用 browser_close 清理",
                          "登录态自动保留，用户下次无需重复登录"],
         },
         "browser_tabs": {
             "理解": "列出独立浏览器内所有页面标签（含 window.open 弹出的新窗口/新标签）。"
                    "页面弹窗/新窗口里的元素（如关闭按钮）在 browser_snapshot 中找不到时，"
                    "先 browser_tabs 查看弹窗标签，再用 browser_switch_tab 切过去操作。",
             "执行拆分": ["browser_snapshot 找不到弹窗元素时，先 browser_tabs 列出全部标签",
                          "用 browser_switch_tab(id) 切到弹窗/新窗口标签再 snapshot/操作"],
         },
         "browser_switch_tab": {
             "理解": "切换到指定编号的页面标签（弹窗/新窗口）。切换后 browser_snapshot/browser_click 等"
                    "操作都针对该标签。弹窗里的关闭按钮：切到弹窗标签后 browser_snapshot 找到关闭按钮再点击。",
             "执行拆分": ["browser_tabs 拿到标签编号后，用 browser_switch_tab(id) 切换",
                          "切换后 browser_snapshot 确认目标页面/元素"],
         },
         "find_app": {
             "理解": "秒查已安装应用路径（开始菜单/桌面/注册表，带缓存），返回可启动的完整路径候选。",
             "执行拆分": ["打开应用前先精确查找可执行路径",
                          "候选多时选择最匹配用户意图的一个"],
         },
         "run_command": {
             "理解": "在系统终端执行命令（受沙盒约束）。默认等待 wait 秒（默认5）；超时未结束且 force_quit=true 则强制结束，否则转入后台运行，用 check_command 轮询进度。",
             "执行拆分": ["分析命令安全性（删除/格式化/关机等一律拒绝）",
                          "长任务自主决定 wait/force_quit：预计挂起或无输出则 force_quit=true，需要看进度则 false+check_command 轮询",
                          "说明意图并等待确认",
                          "执行并读取输出"],
         },
         "check_command": {
             "理解": "轮询后台运行命令（run_command 转入后台的）的进度与最新输出。",
             "执行拆分": ["上一步 run_command 返回了后台命令 ID 时，用 check_command 持续轮询直到结束",
                          "不传 cmd_id 可先列出全部后台命令"],
         },
         "write_file": {
             "理解": "创建或覆盖写入文本文件（相对路径基于工作目录；删除系统关键目录仍被沙盒拒绝）。",
             "执行拆分": ["确认目标路径合法（禁止删除系统关键目录）",
                          "说明写入目标文件并等待确认",
                          "执行写入",
                          "读取文件确认结果"],
         },
         "read_file": {
             "理解": "读取文本文件内容（相对路径基于工作目录）。",
             "执行拆分": ["确认目标路径合法",
                          "读取文件",
                          "向用户摘要关键内容"],
         },
         "delete_file": {
             "理解": "删除文件或空目录（相对路径基于工作目录；删除系统关键目录内容被沙盒拒绝，非空目录用 run_command）。",
             "执行拆分": ["确认要删除的对象与路径无误",
                          "删除文件/空目录",
                          "确认删除结果"],
         },
         "web_fetch": {
             "理解": "联网请求指定 URL（网页 HTML / JSON 接口 / raw 文件 / REST API），默认 GET，可 POST/PUT/DELETE 调接口。",
             "执行拆分": ["需要访问网络页面/接口时先用 web_fetch 获取文本内容",
                          "HTML 自动提取正文，JSON/文本原样返回"],
         },
         "web_search": {
             "理解": "联网搜索（Bing）：实时信息/新闻/文档/知识范围外内容，返回标题+URL+摘要。",
             "执行拆分": ["需要实时或知识范围外信息时先 web_search",
                          "必要时再 web_fetch 打开具体结果页"],
         },
         "tts_speak": {
             "理解": "把文本合成为语音并自动播放（DashScope 真实 API，流式边生成边播放，同时保存 wav）。"
                    "用户要求朗读/读出来/语音回复/播报时，用它对要朗读的文本调用；"
                    "voice_id 留空自动使用设置面板选中的音色；play 默认 true 自动播放。",
             "执行拆分": ["判断用户是否要求朗读/语音输出：是则把回复正文或用户指定文本交给 tts_speak",
                          "voice_id 留空（使用设置面板音色），output_path 留空（自动保存工作目录 tts_output/）",
                          "返回语音合成完成与文件路径，确认已朗读"],
         },
     },
     "system_prompt": ("你是 zhuzhu Copilot，桌面自动化助手。通过独立浏览器操控 + 命令/文件工具"
                       "帮用户完成任务。\n"
                       "高质量完成任务的方法论：\n"
                       "1. 浏览器任务（打开网页/登录/填表/抓取/自动操作网页）第一步先 browser_open，"
                       "全程用独立浏览器实例，不碰用户正在用的浏览器。\n"
                       "2. 复杂任务拆解为步骤清单，逐步执行、逐步验证（browser_snapshot/browser_html 确认结果）。\n"
                       "3. 每步操作后确认结果正确再继续；失败先分析原因再换方案重试。\n"
                       "4. 打开应用先 find_app 定位路径；文件操作（查找/创建/修改/删除/读取）"
                       "优先在工作目录内执行：设置了工作目录时，未指定完整路径默认在工作目录内，"
                       "相对路径基于工作目录解析。\n"
                       "5. 能用 run_command/文件操作/web_fetch 完成的（启动应用、执行命令、写文件、下载、调接口），"
                       "优先用命令完成。\n"
                       "6. 登录相关：账号密码/验证码/手机验证一律交用户手动完成，AI 不代为填写；"
                       "登录期间停止网页操作并等待用户完成。\n"
                       "7. 全部完成后向用户总结结果。"),
     "skills": ["complex-task"], "tools": []},
]


# 技能加载缓存：输入框 textChanged 等高频路径避免每次全量扫盘。
# 失效依据：skills.json 的 mtime + skills 目录内所有 SKILL.md 的 (目录名, mtime, size) 指纹，
# 任何技能导入/删除/编辑（含用户手改 SKILL.md）都会导致指纹变化而自动失效，保证热改即时生效。
_SKILLS_CACHE = {"all_key": None, "all_list": None, "md_key": None, "md_list": None,
                 "stamp": 0.0}
# skills 目录指纹缓存：{目录路径: (计算时刻, 指纹)}，避免每轮重复 scandir/stat
_MD_KEY_CACHE: dict = {}
_MIGRATE_STATE = {"mtime": 0, "clean": False}
_SKILLS_TTL = 2.0   # 快速路径有效期（秒）：期间直接返回缓存，零磁盘 IO

# 工作流额外技能目录（set_extra_skill_dirs 注册，同名时工作流技能优先）：
# {工作流名: [Path,...]} —— 技能按工作流隔离，激活/@切换的工作流只合并其专属技能目录。
_EXTRA_WF_DIRS: dict = {}

# 线程局部当前工作流：引擎任务线程在 run() 开头设置，技能加载按会话工作流隔离
# （@工作流 切换后的会话走各自工作流，互不干扰并发会话）。
_WF_LOCAL = threading.local()
# 工作流隔离技能缓存：{工作流名: (key, [技能])}（技能启停/导入删除时随 _invalidate 清空）
_WF_SKILLS_CACHE: dict = {}

# settings.json 解析缓存：model 解密的 PBKDF2 派生耗时数百毫秒，load_settings 被
# cap_enabled/load_skills/load_model_config 高频调用，重复解密阻塞 UI。save_settings 后失效。
_SETTINGS_CACHE: dict = {"data": None}
_SETTINGS_LOCK = threading.Lock()

# mcp_servers.json 读取缓存：打开 AI 设置/应用设置/引擎连接高频读取，避免重复磁盘 IO。
_MCP_CACHE: dict = {"data": None}


def set_current_workflow(name: str) -> None:
    """设置当前线程的工作流（引擎任务线程调用；空串回退全局/无隔离）"""
    _WF_LOCAL.wf = name or ""


def _current_workflow() -> str:
    return getattr(_WF_LOCAL, "wf", "") or ""


def set_extra_skill_dirs(dirs: list, workflow: str = "") -> None:
    """注册额外技能目录（工作流 skills/），随后失效缓存强制重扫。
    workflow 记录目录所属工作流名，供按工作流隔离的技能加载使用。"""
    global _EXTRA_WF_DIRS
    _EXTRA_WF_DIRS = {workflow or "": [Path(d) for d in (dirs or [])]}
    _invalidate_skills_cache()


def invalidate_skills_cache() -> None:
    """公共别名：修改技能/技能状态后显式失效缓存，下次调用重新扫盘即时生效"""
    _invalidate_skills_cache()


def _skills_fresh() -> bool:
    """TTL 快速路径：高频调用（输入框 textChanged）期间零 IO 直接命中缓存。
    修改类操作（导入/删除/创建/技能启停）会显式失效缓存，保证即时生效。"""
    return time.time() - _SKILLS_CACHE["stamp"] < _SKILLS_TTL


def _invalidate_skills_cache() -> None:
    """修改技能后显式失效缓存（导入/删除/创建/启停），下次调用重新扫盘"""
    _SKILLS_CACHE.update(all_key=None, all_list=None, md_key=None, md_list=None, stamp=0.0)
    _WF_SKILLS_CACHE.clear()
    _MD_KEY_CACHE.clear()
    _COVER_CACHE.clear()


def _file_mtime(path: Path):
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


def _md_key(root: Path) -> str:
    """skills 目录指纹：所有 SKILL.md 的 (目录名, mtime, size) 摘要。

    性能：引擎按工作流隔离加载技能时**每轮**都要算指纹（长任务里是每轮热点），
    故加 TTL 记忆 —— 有效期内零磁盘扫描。技能增删/启停/导入等修改操作都会走
    _invalidate_skills_cache（清空本缓存），保证即时生效。"""
    rk = str(root)
    hit = _MD_KEY_CACHE.get(rk)
    if hit is not None and time.time() - hit[0] < _SKILLS_TTL:
        return hit[1]
    parts = []
    try:
        with os.scandir(root) as it:
            entries = sorted(it, key=lambda e: e.name)
    except OSError:
        entries = []
    for e in entries:
        try:
            if not e.is_dir():
                continue
            f = Path(e.path) / "SKILL.md"
            if not f.is_file():
                continue
            st = f.stat()
        except OSError:
            continue
        parts.append(f"{e.name}:{st.st_mtime_ns}:{st.st_size}")
    key = "|".join(parts)
    _MD_KEY_CACHE[rk] = (time.time(), key)
    return key


def _load(name: str, default: list) -> list:
    path = CONFIG_DIR / name
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else default
    except Exception:
        return default


def _ensure_samples():
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        for name, default in (("skills.json", DEFAULT_SKILLS),
                              ("agents.json", DEFAULT_AGENTS),
                              ("mcp_servers.json", [])):
            path = CONFIG_DIR / name
            if not path.exists():
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(default, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def _skills_dir() -> Path:
    """市场标准技能目录：~/.winapp_migrator/agent/skills/<name>/SKILL.md"""
    return CONFIG_DIR / "skills"


def _parse_skill_md(text: str) -> dict:
    """解析 SKILL.md：--- frontmatter（name/description）--- + 正文 instruction

    市场标准格式（Claude Skills / Trae Skill）。无 frontmatter 时仅返回正文。
    """
    s = (text or "").strip()
    if not s:
        return {}
    name = desc = ""
    body = s
    if s.startswith("---"):
        end = s.find("\n---", 3)
        if end > 0:
            fm = s[3:end].strip()
            body = s[end + 4:].strip()
            for line in fm.splitlines():
                line = line.strip()
                if line.startswith("name:"):
                    name = line[len("name:"):].strip().strip("\"'")
                elif line.startswith("description:"):
                    desc = line[len("description:"):].strip().strip("\"'")
    if not body:
        return {}
    return {"name": name, "description": desc, "instruction": body}


# SKILL.md 必需约束章节：每类提供等价关键词，任中一条即视为具备（软校验，缺失仅告警不阻断）
_SKILL_REQUIRED_SECTIONS = (
    ("触发场景", ("触发", "当用户", "何时", "适用", "使用本", "场景")),
    ("执行流程", ("流程", "步骤", "How To", "Procedure", "操作", "1.", "做法")),
    ("参数契约", ("参数", "传参", "字段", "schema", "示例", "JSON", "输入")),
    ("禁用约束", ("禁止", "不要", "避免", "严禁", "不得", "不允许", "禁用", "硬性")),
)


def _skill_structure_warning(instruction: str) -> list:
    """校验 SKILL.md 正文是否覆盖必需约束章节；返回缺失清单（空=齐全）。"""
    body = (instruction or "").lower()
    missing = []
    for label, kws in _SKILL_REQUIRED_SECTIONS:
        if not any(k.lower() in body for k in kws):
            missing.append(label)
    return missing


def _shipped_skills_dir() -> Path:
    """随应用分发的内置技能资源目录：
    - 开发模式：src/winapp_migrator/skills
    - 打包（onedir/onefile）：依次尝试 _MEIPASS/skills、<exe>/_internal/skills、<exe>/skills"""
    if getattr(sys, "frozen", False):
        for base in (getattr(sys, "_MEIPASS", None),
                     str(Path(sys.executable).parent / "_internal"),
                     str(Path(sys.executable).parent)):
            if not base:
                continue
            cand = Path(base) / "skills"
            if cand.is_dir():
                return cand
        return Path()
    return Path(__file__).resolve().parent.parent / "skills"


def ensure_md_skills() -> None:
    """确保内置 md 技能存在并同步到最新模板：内置模板（_BUILTIN_MD_SKILLS）+ 随包分发的技能资源。
    内置模板技能启动时与最新模板比对，内容不一致则更新（保证规则/流程改动生效）；
    用户自建技能与随包分发技能仅缺失时生成，不覆盖。"""
    root = _skills_dir()
    # 2026-08 电脑操控功能移除：清理旧版残留的电脑操控技能目录（computer-control/screen_operate/ui-automation）
    for name in ("computer-control", "screen_operate", "ui-automation"):
        d = root / name
        try:
            if d.is_dir():
                shutil.rmtree(d)
        except OSError:
            pass
    for name, cfg in _BUILTIN_MD_SKILLS.items():
        f = root / name / "SKILL.md"
        template = f"---\nname: {name}\ndescription: {cfg['description']}\n---\n\n{cfg['instruction'].strip()}\n"
        try:
            if f.is_file():
                if f.read_text(encoding="utf-8", errors="replace") == template:
                    continue   # 与模板一致，无需更新
            else:
                f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(template, encoding="utf-8")
        except OSError:
            pass
    # 随包分发技能：用户目录缺失同名技能时整体复制（含 resources 附属文件）
    shipped = _shipped_skills_dir()
    if shipped.is_dir():
        for d in shipped.iterdir():
            if not d.is_dir() or not (d / "SKILL.md").is_file():
                continue
            target = root / d.name
            if (target / "SKILL.md").is_file():
                continue
            try:
                shutil.copytree(d, target)
            except OSError:
                pass


def _md_roots(workflow: str = None) -> list:
    """(工作流名, 目录) 对列表：主技能目录 + 各工作流技能目录。
    workflow 非空（含线程局部）→ 只合并该工作流的技能目录（隔离）；
    为空 → 合并所有已注册工作流技能目录（管理视图）。"""
    wf = workflow if workflow is not None else _current_workflow()
    roots = [("", _skills_dir())]
    if wf:
        try:
            from winapp_migrator.core import agent_workflow
            for d in agent_workflow.extra_skill_dirs(wf):
                roots.append((wf, d))
        except Exception:
            pass
    else:
        for w, ds in _EXTRA_WF_DIRS.items():
            for d in ds:
                roots.append((w, d))
    return roots


def _scan_md(pairs: list, wf: str = "") -> list:
    """按 (工作流名, 目录) 对扫描 SKILL.md；工作流专属技能带 workflow 标记并覆盖同名全局技能"""
    out = []
    try:
        for w, root in pairs:
            if not root.is_dir():
                continue
            for d in sorted(root.iterdir()):
                f = d / "SKILL.md"
                if not d.is_dir() or not f.is_file():
                    continue
                s = _parse_skill_md(f.read_text(encoding="utf-8", errors="replace"))
                if not s:
                    continue
                s["source"] = "md"
                if w:
                    s["workflow"] = w      # 工作流专属技能标记
                if w and s.get("name"):
                    # 工作流技能覆盖同名（内置/用户）技能
                    out = [x for x in out if x.get("name") != s["name"]]
                out.append(s)
    except Exception:
        pass
    return out


def load_md_skills(workflow: str = None) -> list:
    """扫描市场标准技能目录 + Cordis 工作流技能目录，返回 [{name,description,instruction,source:'md'}]。
    TTL 快速路径：有效期内的重复调用零 IO 直接返回缓存；修改操作显式失效。
    同名技能：工作流（额外目录）技能优先于内置/用户。
    workflow 非空时按该工作流隔离（只合并其专属技能目录）。"""
    wf = workflow if workflow is not None else _current_workflow()
    if not wf:
        if _skills_fresh() and _SKILLS_CACHE["md_list"] is not None:
            return _SKILLS_CACHE["md_list"]
        ensure_md_skills()
        pairs = _md_roots("")
        key = "|".join(f"{w}:{_md_key(d)}" for w, d in pairs)
        if _SKILLS_CACHE["md_key"] == key and _SKILLS_CACHE["md_list"] is not None:
            _SKILLS_CACHE["stamp"] = time.time()
            return _SKILLS_CACHE["md_list"]
        out = _scan_md(pairs, "")
        _SKILLS_CACHE["md_key"] = key
        _SKILLS_CACHE["md_list"] = out
        _SKILLS_CACHE["stamp"] = time.time()
        return out
    # 工作流路径的缓存自带时间戳（不共用全局 stamp）：各工作流的快路径互不干扰，
    # 命中即零 IO；技能修改经 _invalidate_skills_cache 立即失效。
    cached = _WF_SKILLS_CACHE.get("md:" + wf)
    if cached is not None and time.time() - cached[2] < _SKILLS_TTL:
        return cached[1]                     # TTL 快速路径：跳过目录指纹计算（零 IO）
    ensure_md_skills()
    pairs = _md_roots(wf)
    key = "|".join(f"{w}:{_md_key(d)}" for w, d in pairs)
    if cached and cached[0] == key:
        _WF_SKILLS_CACHE["md:" + wf] = (key, cached[1], time.time())
        return cached[1]
    out = _scan_md(pairs, wf)
    _WF_SKILLS_CACHE["md:" + wf] = (key, out, time.time())
    return out


def delete_skill(name: str) -> tuple:
    """删除用户技能：md 技能（skills/<name> 目录含 resources）与 skills.json 中的条目；
    内置技能（DEFAULT_SKILLS JSON 与 _BUILTIN_MD_SKILLS md 模板）拒绝删除。返回 (ok, message)。"""
    import shutil
    name = (name or "").strip()
    if not name:
        return False, "技能名不能为空"
    builtin = {s.get("name") for s in DEFAULT_SKILLS} | set(_BUILTIN_MD_SKILLS)
    if name in builtin:
        return False, f"「{name}」是内置技能，不可删除"
    removed = False
    # 1. md 技能目录（SKILL.md + resources 等附属文件）
    d = _skills_dir() / name
    if d.is_dir():
        try:
            shutil.rmtree(d)
            removed = True
        except OSError as e:
            return False, f"删除技能目录失败: {e}"
    # 2. skills.json 中的同名条目
    try:
        data = _load("skills.json", DEFAULT_SKILLS)
        if any(s.get("name") == name for s in data):
            data = [s for s in data if s.get("name") != name]
            with open(CONFIG_DIR / "skills.json", "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            removed = True
    except OSError:
        pass
    if not removed:
        return False, f"未找到技能「{name}」"
    remove_workflow_binding("skill", name)
    _invalidate_skills_cache()
    return True, f"已删除技能「{name}」并即时生效"


def create_md_skill(name: str, description: str, instruction: str,
                    workflow: str = "") -> tuple:
    """以用户自然语言描述为基础，生成市场标准 SKILL.md 技能并注册。

    workflow 非空时，创建后自动把该技能绑定到指定工作流（仅该工作流加载），
    实现"对话中描述即可一键生成并配置到当前工作流"。返回 (ok, message)。
    生成后 load_skills 立即加载生效。
    """
    import re
    name = (name or "").strip()
    if not name:
        return False, "技能名不能为空"
    if not re.match(r"^[A-Za-z0-9_-]{1,50}$", name):
        return False, "技能名仅支持字母/数字/下划线/连字符（≤50 字符）"
    description = (description or "").strip() or name
    instruction = (instruction or "").strip()
    if not instruction:
        return False, "技能说明（instruction）不能为空"
    try:
        d = _skills_dir() / name
        d.mkdir(parents=True, exist_ok=True)
        md = (f"---\nname: {name}\ndescription: {description}\n---\n\n"
              f"{instruction}\n")
        (d / "SKILL.md").write_text(md, encoding="utf-8")
        bind_msg = ""
        if str(workflow).strip():
            ok, bmsg = set_workflow_binding("skill", name, [workflow])
            bind_msg = f"；已绑定工作流「{workflow}」" if ok else f"；工作流绑定失败: {bmsg}"
        _invalidate_skills_cache()
        from winapp_migrator.core import agent_workflow
        if str(workflow).strip():
            agent_workflow.set_skill_state(workflow, name, True)
        msg = f"已创建技能「{name}」（{d / 'SKILL.md'}），已加载生效{bind_msg}"
        warn = _skill_structure_warning(instruction)
        if warn:
            msg += "；结构提示：缺少" + "、".join(warn) + "章节（建议补充触发词/流程/参数契约/禁用约束）"
        return True, msg
    except OSError as e:
        return False, f"创建技能失败: {e}"


def import_skill_file(path: str) -> tuple:
    """导入市场标准技能：SKILL.md 单文件 或 含 SKILL.md 的 zip 包。

    单文件 → 复制为 skills/<name>/SKILL.md（name 取 frontmatter 或文件名）；
    zip → 按包内目录解压到 skills/（含 resources 等附属文件），
    防止路径穿越。返回 (ok, message)；导入后 load_skills 立即生效。
    """
    import re as _re
    import zipfile
    p = Path(path or "")
    if not p.is_file():
        return False, "文件不存在"
    try:
        if p.suffix.lower() == ".zip":
            with zipfile.ZipFile(p) as z:
                md_entries = [i for i in z.infolist()
                              if not i.is_dir()
                              and i.filename.replace("\\", "/").endswith("SKILL.md")]
                if not md_entries:
                    return False, "压缩包内未找到 SKILL.md"
                root = _skills_dir()
                root.mkdir(parents=True, exist_ok=True)
                for info in z.infolist():
                    if info.is_dir():
                        continue
                    rel = info.filename.replace("\\", "/")
                    if rel.startswith("/") or ".." in rel.split("/"):
                        return False, "压缩包内含非法路径（拒绝导入）"
                    target = (root / rel).resolve()
                    if not str(target).startswith(str(root.resolve())):
                        return False, "压缩包内含越界路径（拒绝导入）"
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(z.read(info))
            md_path = root / md_entries[0].filename.replace("\\", "/")
        else:
            text = p.read_text(encoding="utf-8", errors="replace")
            name = (_parse_skill_md(text).get("name") or "").strip() or p.stem.strip()
            if not _re.match(r"^[A-Za-z0-9_-]{1,50}$", name):
                return False, "技能名仅支持字母/数字/下划线/连字符（≤50 字符）"
            md_path = _skills_dir() / name / "SKILL.md"
            md_path.parent.mkdir(parents=True, exist_ok=True)
            md_path.write_text(text, encoding="utf-8")
        s = _parse_skill_md(md_path.read_text(encoding="utf-8", errors="replace"))
        if not s:
            return False, "SKILL.md 内容为空或格式不正确"
        warn = _skill_structure_warning(s.get("instruction") or "")
        _invalidate_skills_cache()
        msg = f"已导入技能「{s.get('name') or md_path.parent.name}」并即时生效"
        if warn:
            msg += "；结构提示：缺少" + "、".join(warn) + "章节（建议补充触发词/流程/参数契约/禁用约束）"
        return True, msg
    except OSError as e:
        return False, f"导入失败: {e}"


def _migrate_legacy_json_skills() -> None:
    """迁移旧 JSON 技能：技能已统一为市场标准 md（SKILL.md），
    若 skills.json 中存在已内置化的旧 JSON 条目则自动移除（保留用户自建技能），
    避免 JSON 优先覆盖 md 版本导致 SKILL.md 编辑不生效。
    带 mtime 状态缓存：文件未变化且已清理干净时跳过重复读取。"""
    global _MIGRATE_STATE
    try:
        path = CONFIG_DIR / "skills.json"
        if not path.is_file():
            return
        mt = _file_mtime(path)
        if _MIGRATE_STATE["clean"] and _MIGRATE_STATE["mtime"] == mt:
            return
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return
        builtin = set(_BUILTIN_MD_SKILLS) | {"code-review"}
        leftover = [s for s in data
                    if isinstance(s, dict) and s.get("name") not in builtin]
        _MIGRATE_STATE["mtime"] = mt
        _MIGRATE_STATE["clean"] = len(leftover) == len(data)
        if not _MIGRATE_STATE["clean"]:
            path.write_text(json.dumps(leftover, ensure_ascii=False, indent=2),
                            encoding="utf-8")
    except Exception:
        pass


def _load_skills_all() -> list:
    """全部技能（无工作流隔离）：内置 skills.json + 内置核心技能兜底 + 市场标准 md 技能。
    md 技能与 JSON 同名时以 JSON 为准（JSON 优先）。每次调用实时扫描，新增 md 技能即时生效。
    TTL 快速路径：有效期内的重复调用零 IO 直接返回缓存；修改操作显式失效。"""
    if _skills_fresh() and _SKILLS_CACHE["all_list"] is not None:
        return _SKILLS_CACHE["all_list"]
    _migrate_legacy_json_skills()
    key = (_file_mtime(CONFIG_DIR / "skills.json"), _md_key(_skills_dir()))
    if _SKILLS_CACHE["all_key"] == key and _SKILLS_CACHE["all_list"] is not None:
        _SKILLS_CACHE["stamp"] = time.time()
        return _SKILLS_CACHE["all_list"]
    merged = list(_load("skills.json", DEFAULT_SKILLS))
    seen = {s.get("name") for s in merged if s.get("name")}
    # 内置核心技能兜底：缺失时补充（保证 skill-create 等始终可用）
    for s in DEFAULT_SKILLS:
        if s.get("name") and s.get("name") not in seen:
            merged.append(s)
            seen.add(s.get("name"))
    for s in load_md_skills(workflow=""):
        if s.get("name") and s.get("name") not in seen:
            merged.append(s)
            seen.add(s.get("name"))
    _SKILLS_CACHE["all_key"] = key
    _SKILLS_CACHE["all_list"] = merged
    _SKILLS_CACHE["stamp"] = time.time()
    return merged


def _load_workflow_skills(wf: str) -> list:
    """按工作流隔离的技能列表：工作流专属技能（workflow==wf）恒启用 +
    全局技能（内置/用户）按该工作流 skill_states 过滤（缺省启用）；
    显式绑定到其他工作流的全局技能对该工作流不可见（工作流绑定隔离）。
    带 per-工作流 缓存（技能启停/绑定/导入删除时随 _invalidate 清空）。"""
    cached = _WF_SKILLS_CACHE.get("all:" + wf)
    if cached is not None and time.time() - cached[2] < _SKILLS_TTL:
        return cached[1]     # TTL 快速路径：跳过目录指纹/绑定快照计算（零 IO，长任务热点）
    try:
        from winapp_migrator.core import agent_workflow
        states = agent_workflow.skill_states(wf)
    except Exception:
        states = {}
    bindings = workflow_bindings("skill")
    key = (_file_mtime(CONFIG_DIR / "skills.json"), _md_key(_skills_dir()),
           tuple(sorted(states.items())),
           tuple(sorted((k, tuple(sorted(v))) for k, v in bindings.items())))
    if cached and cached[0] == key:
        _WF_SKILLS_CACHE["all:" + wf] = (key, cached[1], time.time())
        return cached[1]
    merged = []
    seen = set()
    for s in _load("skills.json", DEFAULT_SKILLS):
        if s.get("name") and s.get("name") not in seen:
            merged.append(s)
            seen.add(s.get("name"))
    for s in load_md_skills(workflow=wf):
        if s.get("name") and s.get("name") not in seen:
            merged.append(s)
            seen.add(s.get("name"))
    out = []
    for s in merged:
        if s.get("workflow") == wf:      # 工作流专属技能恒启用
            out.append(s)
            continue
        name = s.get("name") or ""
        b = bindings.get(name)
        if b and wf not in b:            # 显式绑定到其他工作流 → 本工作流不可见
            continue
        if states.get(name, True):       # 未绑定全局 / 绑定本工作流 → 按启停过滤
            out.append(s)
    _WF_SKILLS_CACHE["all:" + wf] = (key, out, time.time())
    return out


def load_skills(workflow: str = None) -> list:
    """全部技能；workflow 非空（含引擎任务线程的当前工作流）时按该工作流隔离：
    只返回工作流专属技能 + 该工作流启用的全局技能（内置/用户私有可逐项启停）。
    技能能力关闭（cap_skill=false）→ 返回空；插件能力关闭 → 剔除插件登记的技能。"""
    if not cap_enabled("skill"):
        return []
    wf = workflow if workflow is not None else _current_workflow()
    skills = _load_workflow_skills(wf) if wf else _load_skills_all()
    if not cap_enabled("plugin"):
        from winapp_migrator.core import agent_plugins
        banned = agent_plugins.plugin_skill_names()
        if banned:
            skills = [s for s in skills if s.get("name") not in banned]
    return skills


def load_agents() -> list:
    agents = _load("agents.json", DEFAULT_AGENTS)
    # 2026-08 电脑操控功能移除：旧版默认配置的 tool_instructions 仍含已删除的电脑操控工具
    # （screenshot/click_text/control_ui/list_windows/capture_window）时，重置为最新默认配置，
    # 避免把已不存在的工具指令注入系统提示词。
    removed_tools = ("screenshot", "click_text", "control_ui", "list_windows",
                     "capture_window", "get_screen_size", "type_text")
    for a in agents:
        ti = a.get("tool_instructions") or {}
        if any(k in ti for k in removed_tools):
            return DEFAULT_AGENTS
    return agents


def load_mcp_servers() -> list:
    """加载 MCP 服务器配置（带缓存：打开设置/应用设置/引擎连接高频读取，避免重复磁盘 IO）。
    返回深拷贝，避免调用方就地修改污染缓存；save_mcp_servers 写成功后失效。"""
    cached = _MCP_CACHE.get("data")
    if cached is not None:
        return copy.deepcopy(cached)
    data = _load("mcp_servers.json", [])
    _MCP_CACHE["data"] = data
    return copy.deepcopy(data)


def save_mcp_servers(servers: list) -> bool:
    """把 MCP 服务器配置写入 mcp_servers.json"""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_DIR / "mcp_servers.json", "w", encoding="utf-8") as f:
            json.dump(servers, f, ensure_ascii=False, indent=2)
        _MCP_CACHE["data"] = None   # 保存后失效缓存，下次读取重新读盘
        return True
    except OSError:
        return False


# ------------------------------------------------------------
# 工作流绑定注册表：skill / plugin / mcp 资源可指定一个或多个工作流使用。
# 未绑定 = 全局（所有工作流可用）；绑定 = 仅绑定的工作流可用（隔离）。
# 集中存放在 workflow_bindings.json，避免污染市场标准 SKILL.md / plugin.json。
# ------------------------------------------------------------
_BINDING_KINDS = ("skill", "plugin", "mcp")


def _bindings_path() -> Path:
    return CONFIG_DIR / "workflow_bindings.json"


def _load_bindings() -> dict:
    try:
        f = _bindings_path()
        if f.is_file():
            data = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _save_bindings(bindings: dict) -> bool:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        _bindings_path().write_text(
            json.dumps(bindings, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except OSError:
        return False


def workflow_bindings(kind: str = "") -> dict:
    """返回 {资源名: [工作流名, ...]}；kind 为空返回 {kind: {...}} 全量"""
    data = _load_bindings()
    if kind:
        b = data.get(kind)
        return b if isinstance(b, dict) else {}
    return data


def get_workflow_binding(kind: str, resource: str) -> list:
    """某资源绑定的工作流列表（空 = 未绑定/全局）"""
    b = workflow_bindings(kind)
    ws = b.get(resource or "")
    return list(ws) if isinstance(ws, list) else []


def set_workflow_binding(kind: str, resource: str, workflows: list) -> tuple:
    """设置资源绑定的工作流列表（空列表/空串 = 解除绑定回全局）。
    返回 (ok, message)；skill 绑定变更即时失效技能缓存。"""
    if kind not in _BINDING_KINDS:
        return False, f"未知资源类型: {kind}（应为 {'/'.join(_BINDING_KINDS)}）"
    resource = (resource or "").strip()
    if not resource:
        return False, "资源名不能为空"
    ws = [str(w).strip() for w in (workflows or []) if str(w).strip()]
    # 去重并校验工作流存在
    seen, names = set(), []
    from winapp_migrator.core import agent_workflow
    known = {w["name"] for w in agent_workflow.list_workflows()}
    for w in ws:
        if w in seen:
            continue
        seen.add(w)
        names.append(w)
    unknown = [w for w in names if w not in known]
    if unknown:
        return False, f"未知工作流: {', '.join(unknown)}"
    data = _load_bindings()
    data.setdefault(kind, {})
    if names:
        data[kind][resource] = names
    else:
        data[kind].pop(resource, None)
    _save_bindings(data)
    if kind == "skill":
        _invalidate_skills_cache()
    return True, (f"已绑定资源「{resource}」到工作流: {', '.join(names)}"
                  if names else f"已解除「{resource}」工作流绑定（恢复全局）")


def remove_workflow_binding(kind: str, resource: str) -> None:
    """删除资源时清理其工作流绑定"""
    data = _load_bindings()
    if kind in data:
        data[kind].pop(resource, None)
    _save_bindings(data)


def resource_allowed(kind: str, resource: str, workflow: str) -> bool:
    """资源是否允许在指定工作流使用：未绑定 → 全局允许；绑定 → 仅绑定工作流允许"""
    ws = get_workflow_binding(kind, resource)
    if not ws:
        return True
    return (workflow or "") in ws


def load_settings() -> dict:
    """加载用户设置 settings.json：
    custom_rules(规则数组) / custom_system_prompt(提示词补充) /
    custom_safe_commands(bash 白名单) / memory_enabled(记忆开关) /
    model({base_url, api_key, model}) — 模型配置自动解密

    兼容旧版明文 model 配置：若未加密则原样返回。

    性能：model 加密解密的 PBKDF2 派生耗时数百毫秒，而 load_settings 被
    cap_enabled / load_skills / load_model_config 等高频重复调用，每次重复解密
    会阻塞 UI。故对解析结果做缓存：save_settings 写成功后显式失效，保证用户
    保存的修改下一轮立即生效。返回深拷贝，避免调用方就地修改污染缓存。
    """
    cached = _SETTINGS_CACHE.get("data")
    if cached is not None:
        return copy.deepcopy(cached)
    path = CONFIG_DIR / "settings.json"
    data = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        data = data if isinstance(data, dict) else {}
        if "model" in data:
            data["model"] = agent_llm.decrypt_model_config(data["model"])
    except Exception:
        data = {}
    with _SETTINGS_LOCK:
        if _SETTINGS_CACHE.get("data") is None:   # 并发下只填一次缓存
            _SETTINGS_CACHE["data"] = data
    return copy.deepcopy(data)


def invalidate_settings_cache() -> None:
    """失效 settings.json 缓存：下次 load_settings 重新读盘并解密（保存设置后调用）"""
    with _SETTINGS_LOCK:
        _SETTINGS_CACHE["data"] = None


def cap_enabled(key: str) -> bool:
    """能力开关：settings.json 中 cap_<key>，默认开启；存 "false"/"0"/"off" 即禁用。
    key ∈ {"mcp","skill","plugin"}。供引擎按能力裁剪工具/技能。"""
    v = str(load_settings().get("cap_" + key, "1")).strip().lower()
    return v not in ("0", "false", "no", "off")


def save_settings(s: dict) -> bool:
    """写入用户设置 settings.json，model 配置自动加密存储"""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        data = dict(s)
        if "model" in data:
            data["model"] = agent_llm.encrypt_model_config(data["model"])
        with open(CONFIG_DIR / "settings.json", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        invalidate_settings_cache()
        return True
    except OSError:
        return False


def skill_instructions(skill_names: list) -> str:
    """把所选技能的 instruction 拼装成 system prompt 附加段落"""
    by_name = {s.get("name"): s for s in load_skills()}
    parts = []
    for n in skill_names or []:
        s = by_name.get(n)
        if s and s.get("instruction"):
            parts.append(s["instruction"])
    return "\n".join(parts)


def skill_md_path(name: str) -> str:
    """技能 SKILL.md 绝对路径（引擎技能路由拦截的提示用）"""
    return str(_skills_dir() / name / "SKILL.md")


def is_builtin_skill(name: str) -> bool:
    """技能是否内置（内置 md 模板 / 默认 JSON / 随包分发技能）；否则为用户私有技能"""
    name = (name or "").strip()
    if not name:
        return False
    if name in _BUILTIN_MD_SKILLS or name in {s.get("name") for s in DEFAULT_SKILLS}:
        return True
    try:
        shipped = _shipped_skills_dir()
        if shipped.is_dir():
            for d in shipped.iterdir():
                if d.is_dir() and d.name == name and (d / "SKILL.md").is_file():
                    return True
    except OSError:
        pass
    return False


def list_skills_for_workflow(workflow: str = "") -> list:
    """管理界面用：工作流视图的技能清单 [{name, description, builtin, workflow_skill, enabled, workflows}]。
    workflow 为空 → 展示全部技能（enabled 按该线程无隔离全启用）；非空 → enabled 按该工作流启停；
    workflows 为技能绑定的工作流列表（空 = 未绑定/全局）。"""
    states = {}
    if workflow:
        try:
            from winapp_migrator.core import agent_workflow
            states = agent_workflow.skill_states(workflow)
        except Exception:
            states = {}
    bindings = workflow_bindings("skill")
    out, seen = [], set()
    for s in load_skills(workflow=""):
        name = s.get("name") or ""
        if not name or name in seen:
            continue
        seen.add(name)
        out.append({
            "name": name,
            "description": s.get("description", "") or "",
            "builtin": is_builtin_skill(name),
            "workflow_skill": bool(s.get("workflow")),
            "enabled": states.get(name, True),
            "workflows": list(bindings.get(name, [])),
        })
    out.sort(key=lambda x: (not x["workflow_skill"], x["name"]))
    return out


_WORD_CHAR_NAME = re.compile(r"[A-Za-z0-9_]+\Z")   # 纯标识符工具名（可走分词快路径）
_NON_WORD = re.compile(r"[^A-Za-z0-9_]")
# 技能覆盖工具查询缓存 {(工作流, 工具名集合): (计算时刻, {工具名: [技能名]})}：同一任务内
# 每轮工具集固定 → 结果不变；随技能缓存一起失效（技能变更/启停后立即重算）。
_COVER_CACHE: dict = {}
_COVER_CACHE_MAX = 32


def skills_covering_tools(tool_names) -> dict:
    """返回 {工具名: [技能名, ...]}：instruction 文本中明确提及该底层工具的技能。
    引擎硬拦截用：被技能覆盖的工具，调用前须先 read_file 对应 SKILL.md 获取规范流程。
    工具名按词边界匹配（前后非字母数字下划线），避免子串误匹配。

    性能：原实现是「技能 × 工具」逐个正则 search（91 工具 × 30 技能 ≈ 2700 次全文扫描，
    实测 240ms+，是长任务每轮的最大热点之一）。改为：把 instruction 里的非
    [A-Za-z0-9_] 字符归一为空格后分词，与工具名集合直接比对 —— 与词边界语义等价
    （工具名两侧必为非词字符或串首尾，归一后恰好成为独立词元），每技能只扫一遍文本。
    含非词字符的罕见工具名（如带 '-' 的 MCP 名）仍走原正则兜底，语义零变化。"""
    names = {str(t) for t in (tool_names or []) if t}
    if not names:
        return {}
    ckey = (_current_workflow(), frozenset(names))
    hit = _COVER_CACHE.get(ckey)
    if hit is not None and time.time() - hit[0] < _SKILLS_TTL:
        return {k: list(v) for k, v in hit[1].items()}      # 命中：同一任务内零重复扫描
    word_names = {n for n in names if _WORD_CHAR_NAME.match(n)}
    odd_names = names - word_names
    out = {}
    for s in load_skills():
        inst = s.get("instruction", "") or ""
        name = s.get("name", "") or ""
        if not inst or not name:
            continue
        if word_names:
            for tok in set(_NON_WORD.sub(" ", inst).split()):
                if tok in word_names:
                    out.setdefault(tok, []).append(name)
        for t in odd_names:
            if re.search(rf"(?<![A-Za-z0-9_]){re.escape(t)}(?![A-Za-z0-9_])", inst):
                out.setdefault(t, []).append(name)
    if len(_COVER_CACHE) >= _COVER_CACHE_MAX:
        _COVER_CACHE.clear()
    _COVER_CACHE[ckey] = (time.time(), {k: list(v) for k, v in out.items()})
    return out


# 按用户自然语言提示词自动匹配技能的触发词（命中即注入该技能流程）
# 电脑操控类技能（computer-control/ui-automation/screen_operate）已移除（2026-08），
# 网页类触发词统一归 browser-control。
_SKILL_KEYWORDS = {
    "doc-gen": ["ppt", "pptx", "powerpoint", "演示文稿", "word", "docx", "文档",
                "excel", "xlsx", "表格", "报告", "简历", "计划书", "感言", "总结",
                "方案", "毕业论文", "宣传单", "邀请函", "收款记录", "清单"],
    "browser-control": ["浏览器", "打开网页", "打开网站", "浏览网页", "网页操作",
                        "刷视频", "看视频", "刷网页", "网页抓取", "抓取网页", "自动操作网页",
                        "上网站", "进网站", "网页登录", "网页填表", "打开浏览器",
                        "帮我打开", "打卡", "登录", "点赞", "点视频", "点一下", "帮我点"],
    "web-search": ["搜索", "查询", "新闻", "最新", "实时", "查一下", "网页", "联网"],
    "file-ops": ["读取文件", "创建文件", "删除文件", "查找文件", "修改文件", "重命名文件", "整理文件"],
    "cmd-ops": ["命令行", "终端", "安装软件", "运行程序", "pip 安装", "下载文件", "执行命令"],
    "system-admin": ["系统信息", "查看进程", "服务状态", "系统体检"],
    "memory": ["记住这个", "记住"],
    "sub-agent": ["并行", "并发", "大规模搜索", "分布式", "了解", "探索", "理解",
                  "熟悉", "上手", "接手", "读懂", "摸清", "搞清楚", "项目结构",
                  "看项目", "是什么项目"],
    "download-skill": ["安装技能", "下载技能", "找技能"],
    "skill-create": ["创建技能", "新技能", "自定义技能"],
    "plugin-create": ["创建插件", "新插件", "自定义插件", "做一个插件"],
    "deep-customize": ["深度自定义", "深度定制", "全面自定义", "自定义功能", "新增功能",
                       "添加功能", "扩展功能", "改造", "深度定制 agent", "深度定制agent",
                       "自定义整个", "全功能自定义", "把 ai 改", "把ai改", "改得更", "更好用"],
    "test-driven-development": ["写测试", "测试用例", "tdd"],
    "systematic-debugging": ["调试", "排查问题", "修复bug"],
    "brainstorming": ["头脑风暴", "想创意", "方案点子"],
    "writing-plans": ["制定计划", "规划方案", "执行计划"],
    "complex-task": ["复杂任务", "多步骤任务"],
}


def auto_skill_names(text: str) -> list:
    """根据用户自然语言提示词匹配命中的技能名（按触发词命中，返回技能名列表）。
    用于任务开始时把命中技能的规范流程注入系统提示词，让 AI 先走 skill 再动手。
    技能能力关闭时返回空。"""
    if not cap_enabled("skill"):
        return []
    t = (text or "").lower()
    if not t:
        return []
    return [name for name, kws in _SKILL_KEYWORDS.items() if any(k in t for k in kws)]


# 子 Agent 技能名（其 instruction 通篇教怎么派发子 Agent）
SUBAGENT_SKILL = "sub-agent"


def filter_subagent_skills(names, allowed: bool) -> list:
    """按子 Agent 准入过滤技能名：能力被禁用（简单/中等任务）时剔除该技能。

    该技能触发词包含"了解/探索/理解/接手/项目结构"等高频词，几乎任意任务都会被自动
    命中；留着它会让模型在工具已被禁用的情况下仍反复尝试派发。"""
    if allowed:
        return list(names or [])
    return [n for n in (names or []) if n != SUBAGENT_SKILL]


def filter_enabled_skills(names) -> list:
    """按当前线程工作流过滤技能名：被该工作流禁用的技能剔除（无隔离时原样返回）。
    引擎任务线程内调用，保证自动匹配/手动指定的技能也遵循工作流技能隔离。
    技能能力关闭时返回空。"""
    names = list(names or [])
    if not names:
        return names
    if not cap_enabled("skill"):
        return []
    wf = _current_workflow()
    if not wf:
        return names
    try:
        from winapp_migrator.core import agent_workflow
        states = agent_workflow.skill_states(wf)
    except Exception:
        states = {}
    return [n for n in names if states.get(n, True)]


def _skill_router_block() -> str:
    """技能建议段：列出全部可用技能（name：description）。
    不强制读取——由 AI 判断任务是否命中某技能且读取能提升效果时，再用 read_file
    读取对应 SKILL.md 作为规范流程参考；简单/不匹配任务可直接调用底层工具。"""
    skills = [s for s in load_skills()
              if s.get("name") and s.get("description")]
    if not skills:
        return ""
    lines = [f"- {s['name']}：{s['description']}" for s in skills]
    return ("\n\n技能手册（可选参考）：下方技能收录了对应任务的规范流程。"
            "当你的任务正好命中某个技能、且读取其流程能显著提升效果时，"
            "可先 read_file 读取技能目录 "
            "~/.winapp_migrator/agent/skills/<技能名>/SKILL.md（找不到时用 "
            "list_directory/search_files 在 skills 目录定位）作为参考；"
            "不匹配或简单任务无需读取，直接调用底层工具即可：\n"
            + "\n".join(lines))


def _extend_dont_create_rule() -> str:
    """功能扩展硬性规则：小功能的改动（新增功能面板/子 agent/工具/小 UI/小钩子）一律
    优先扩展现有工作流或现有 UI/UX，禁止新建工作流 / UI/UX 资源包。仅功能大且独立时才新建。
    该块固定无动态内容，保持 system 前缀缓存稳定。"""
    return ("功能扩展优先级（必须遵守）：小功能的改动——新增功能面板、新增子 agent、"
            "新增/覆盖工具、小 UI 调整、小钩子——一律注入当前工作流或现有 UI/UX，禁止新建工作流或 UI/UX 资源包：\n"
            "  新增子 agent → register_sub_agent（注册进当前工作流，作为 sub_<name> 工具可用）；"
            "子 agent 一律走注册式（register_sub_agent），其三重用法：用户 @<名> 直接调用、"
            "主 Agent 调用 sub_<name>、由主 Agent 逐次分配是否加入共同上下文空间（shared_context）；\n"
            "  注意 register_agent_network(op=create_agent) / create_agent 只创建「主 Agent 人格」"
            "（@agent 会话级切换，主 Agent 无法调用它），用户要求「创建子 agent」时禁止用它顶替；\n"
            "  新增/覆盖工具 → edit_agent_file 写当前工作流 tools.py；\n"
            "  新增功能面板 → register_feature_panel（写入当前工作流 panel.py，成为扩展浮窗）；\n"
            "  自定义代码需第三方库 → set_feature_deps 声明依赖（requirements.txt），代码顶部可带第三方 import，加载时自动安装；\n"
            "  小 UI 调整 → manage_uiux op=read + set_qss/update_theme/update 扩展现有包。\n"
            "派发子 agent 时（sub_<name> / dispatch_sub_agents / explore_project / search_large）："
            "每个子任务都要传 context（已读取的文件内容、关键结论等，子 Agent 上下文独立于主对话）；"
            "并逐次分配 shared_context=true/false 决定其是否加入共同上下文空间（注册式与临时子任务均支持）。\n"
            "仅当功能大且独立、现有工作流/UI/UX 完全承载不了时，才新建工作流（create_workflow）"
            "或 UI/UX 包（manage_uiux op=create）。")


def _tool_call_hard_rules() -> str:
    """工具调用硬约束（固定内容，无动态时间戳，保持 system 前缀缓存稳定）：
    禁止 mock、真实 API、返回格式与参数准确性、生成后复核。"""
    return ("工具调用硬约束（必须遵守）：\n"
            "  1. 严禁 mock/占位/假数据：所有工具必须真实执行（真实 API/真实文件/真实命令），"
            "     工具返回即为真实结果，禁止编造工具输出；\n"
            "  2. 数据类内容（数字/报表/预算）用户未提供时先 ask_user 补齐，禁止凭印象编造；\n"
            "  3. 调用工具前按 schema 传全必填参数：path、结构化数组（slides/sheets/paragraphs）、"
            "     文本字段一律字符串，禁止把对象塞进文本字段，避免未解析字面量；\n"
            "  4. create_docx/create_pptx/create_xlsx 生成本身即含自动复核，若结果提示"
            "     \"复核失败/未解析标记\"，必须先修正参数重生成，不得直接交付残件；\n"
            "  5. 工具执行规范：先按 schema 校验参数类型与必填，再执行；返回含有效结果描述与文件路径。")


def _subagent_policy_block(allowed: bool) -> str:
    """子 Agent / 跨工作流派发的分级准入（内容随任务复杂度档变化，故必须在 system 里声明）。

    准入档位由 agent_llm.subagent_allowed 统一判定（任务力度分档 × 面板策略 × 用户显式要求）；
    引擎已按同一结论在 schema 层剔除工具、在执行层硬拦截，此处只负责把"规则"讲清楚，
    避免模型反复尝试被拒绝的工具而空转。"""
    if allowed:
        return ("\n\n子 Agent 派发（本任务已放行，但仍须克制）：只有当任务确实"
                "非常复杂——跨多模块重构、大规模并发读写、大范围跨目录搜索——才调用 "
                "dispatch_sub_agents 派发多个子 Agent 并发协作；简单与中等任务一律自己直接完成，"
                "禁止为图省事把工作转派出去（组队有固定开销，小事组队只会更慢更贵）。"
                "执行后必须汇总各子任务结果并对照计划自查完整性，不要只停留在计划上。")
    return ("\n\n子 Agent 派发（本任务禁止）：本任务被判定为简单/中等复杂度，"
            "禁止调用 dispatch_sub_agents / explore_project / search_large / sub_<name> / "
            "use_workflow_agent，也禁止以任何方式调用其他工作流的主 Agent"
            "（这些工具已被系统禁用，调用会被直接拒绝）。"
            "请自己动手：用 read_file / grep / search_files 读取定位，"
            "用 write_file / edit_file / search_replace 修改，用 run_command 验证，"
            "一次性把任务做完做干净。")


def _ask_policy_block() -> str:
    """提问与确认策略（不随模式变化的稳定文本）：一次问清 + 需求明确时禁止冗余确认。

    对应用户两条硬性要求：①需求已明确就直接改/直接执行，不要反复请示；
    ②确实要问时合并为一次提问，并给出可点选的建议选项。"""
    return ("\n\n提问与确认策略：尽量自主完成，减少打扰。"
            "需求或项目内容**已经明确**时（用户说清了目标、范围、对象），"
            "禁止再向用户复述确认、禁止重复询问此前已告知或已答过的信息，"
            "直接动手修改/执行，做完再汇报结果。"
            "需求不明确时优先基于上下文与已有信息合理推断，先推进低风险步骤；"
            "仅当关键信息缺失且推断会明显做错方向（目标文件/对象/预期结果不明）、"
            "或涉及不可逆/危险操作/需要用户拍板时，才调用 ask_user 提问，"
            "并把多个待确认项合并为一次询问（给出 options 建议选项方便点选，"
            "多选场景设置 multi_select=true），避免逐项追问；严禁编造关键信息冒充真实内容。")


def _direct_mode_block() -> str:
    """YOLO（无确认直行）模式补充说明。

    直行的语义是"不设操作约束"，不等于"不许澄清需求"——关键信息缺失时提问比盲猜
    更符合用户利益，故此处放开 ask_user（此前禁止，AskUserDialog 通道本就已就绪）。"""
    return ("\n\n当前为「直接工作模式」（用户已开启无确认直行）：直接调用完成任务所需的"
            "必要技能与命令，无需逐步询问或请求确认；不要做多余的确认、验证或演示步骤，"
            "禁止就需求细节反复请示。"
            "例外：仅当关键信息缺失、推断会明显做错方向（目标文件/对象/预期结果不明）时，"
            "可用 ask_user 一次性问清后继续，不要在信息不足时硬猜。")


def build_system_prompt(agent_name: str = "", extra_skills: list = None,
                        text_only: bool = False, memory_enabled: bool = True,
                        direct: bool = False, subagents_allowed: bool = True) -> str:
    """构造 system prompt：人设 persona + 基础提示 + 严格规则 + 工具执行规范 + 技能说明 + 工具列表

    text_only: 纯文本模型（无视觉输入），追加禁用截图/视觉引导。
    memory_enabled: 记忆开关，关闭时追加禁用记忆工具引导。
    direct: 直接工作模式（无确认直行），追加减少询问/确认的引导。
    subagents_allowed: 是否允许派发子 Agent / 其他工作流主 Agent（由任务复杂度分档决定，
        见 agent_llm.subagent_allowed）。False 时明确禁止组队，要求主 Agent 自己动手。
    自定义规则 / 自定义系统提示词从 settings.json 读取并追加。

    注意：自动匹配/手动指定的任务技能（extra_skills）不再注入 system —— 由引擎以
    对话末尾独立消息注入（_sync_skill_msg），避免每轮任务 system 变化导致
    服务端前缀缓存整段 miss、全量重计费。
    """
    agent = next((a for a in load_agents() if a.get("name") == agent_name), None) \
        or DEFAULT_AGENTS[0]
    parts = []
    persona = agent.get("persona")
    if persona:
        parts.append(persona)
    system_prompt = agent.get("system_prompt", "")
    if system_prompt:
        parts.append(system_prompt)
    rules = agent.get("rules", [])
    if rules:
        parts.append("严格规则（必须遵守）：\n" + "\n".join(str(r) for r in rules))
    tool_ins = agent.get("tool_instructions") or {}
    if tool_ins:
        block = ["工具执行规范（每个工具先理解再按固定流程拆分执行）："]
        for tname, cfg in tool_ins.items():
            if not isinstance(cfg, dict):
                continue
            u = cfg.get("理解")
            steps = cfg.get("执行拆分")
            if u:
                block.append(f"- {tname}（{u}）")
            if steps:
                for i, st in enumerate(steps, 1):
                    block.append(f"  步骤{i}. {st}")
        parts.append("\n".join(block))
    parts.append(_extend_dont_create_rule())
    parts.append(_tool_call_hard_rules())
    prompt = "\n\n".join(parts)

    # 仅注入 agent 自带固定技能（配置稳定，不破坏 system 前缀缓存）。
    # 自动匹配/手动指定的任务技能 instruction 由引擎以对话末尾独立消息注入
    # （_sync_skill_msg），内容变化只 miss 末尾几十 token 的短消息。
    skills = list(agent.get("skills", []))
    if skills:
        inst = skill_instructions(skills)
        if inst:
            prompt += "\n\n" + inst
    prompt += _skill_router_block()
    _mem_desc = ("save_memory/load_memory(本地长期记忆)。" if memory_enabled
                 else "内存记忆工具(save_memory/load_memory)已在设置中禁用，不可调用。")
    _mcp_desc = ("若连接了 MCP 服务器，其工具同样可用。" if cap_enabled("mcp")
                 else "MCP 服务器工具已在设置中禁用，不可调用。")
    prompt += ("\n\n可用内置工具："
               "browser_open(启动独立浏览器实例，不影响用户浏览器；浏览器任务第一步用它)、"
               "browser_navigate(在独立浏览器打开网页)、browser_snapshot(页面截图+元素清单)、"
               "browser_click(按编号/文字/CSS选择器点击网页元素)、browser_type(向网页输入框输入)、"
               "browser_scroll(滚动网页/容器)、"
               "browser_eval(执行JS解析/操作网页)、browser_html(读取页面HTML/文本)、"
               "browser_close(关闭独立浏览器实例)、"
               "run_command(执行命令，可设 wait/force_quit，长任务用 check_command 轮询进度)、"
               "ask_user(需求不明确时向用户提问)、"
               "find_app(秒查已安装应用路径)、search_files(工作目录内快速查找文件，未设工作目录则搜用户常用目录)、"
               "web_search(联网搜索，实时信息/新闻/文档，返回标题+URL+摘要)、"
               "web_fetch(联网请求 URL：GET/POST/PUT/DELETE 抓网页、调 API、读接口)、"
               "clipboard(读写剪贴板)、"
               "extract_text(提取文档文本：txt/csv/docx/pptx/xlsx，pdf 需装 pypdf)、"
               "create_docx(生成 Word 文档：标题+段落+可传 style 自定义配色字体排版)、"
               "create_pptx(生成 PPT：标题+每页要点+可传 style 自定义配色封面布局)、"
               "create_xlsx(生成 Excel：多工作表二维数据+可传 style 自定义表头隔行边框)、"
        "read_docx/read_pptx/read_xlsx(读取 Word/PPT/Excel 真实结构：样式/图片/表格/图表/动画，"
        "供定位锚点后精确修改)、read_pdf(读 PDF 页数/元数据/逐页文本，需 pypdf)、"
        "edit_docx/edit_pptx/edit_xlsx(按 ops 列表真实编辑 Word/PPT/Excel：文字/段落/表格/形状/"
        "备注/背景/动画/单元格/样式/图表/条件格式，单条失败不中断并返回逐条报告)、"
        "beautify_docx/beautify_pptx/beautify_xlsx(一键美化已有三件套)、"
               "tts_speak(把文本合成为语音并自动播放，适合朗读回复/语音输出；"
               "用户要求朗读/读出来/语音回复时，回复正文后用它对需要朗读的文本调用，"
               "voice_id 留空使用设置面板选中的音色)、"
               "read_file/write_file/edit_file/delete_file/list_directory(文件读写改删列，相对路径基于工作目录)、"
               "update_todo(维护任务清单：复杂/多步骤任务开始必须用它创建并逐步更新每步状态，全量提交含已完成项)、"
               "list_todo(查看当前任务清单)、"
               + _mem_desc + _mcp_desc)
    # 手动禁用工具：提示词层面明示（schema 与执行层已硬性剔除，此提示避免模型反复尝试）
    try:
        from winapp_migrator.core import agent_sandbox as _sb
        _banned = _sb.disabled_tools()
        if _banned:
            prompt += ("\n\n注意：以下工具已被你在设置中禁用，禁止调用它们"
                       "（调用会被系统直接拒绝）：" + "、".join(sorted(_banned)))
        if _sb.tools_disabled_all():
            prompt += ("\n\n注意：你已禁用全部工具调用（仅可对话回复），"
                       "不要调用任何工具，只以文本回答问题。")
    except Exception:
        pass
    prompt += _ask_policy_block()
    if memory_enabled:
        prompt += ("\n\n记忆：你有本地长期记忆文件 memory.md。遇到用户偏好、重要结论、"
                   "约定、常用路径等值得长期记住的信息时，调用 save_memory 保存；"
                   "新任务开始或需要回忆过往信息时，自行决定是否调用 load_memory 查看。")
    else:
        # 记忆关闭（系统层面）：不引导记忆工具，schema 与执行层已双重禁用
        prompt += "\n\n当前未开启记忆功能：不要调用 save_memory / load_memory。"
    if text_only:
        prompt += ("\n\n当前为纯文本模型（不支持图像输入）：browser_snapshot 等工具返回的页面截图"
                   "不会提供给你，本环境不提供图像。"
                   "请通过文本工具（read_file、run_command、web_fetch、browser_html 等）完成用户请求。")
    if direct:
        prompt += _direct_mode_block()
    settings = load_settings()
    rules = settings.get("custom_rules") or []
    valid = [str(r).strip() for r in rules if str(r).strip()]
    if valid:
        rules_path = CONFIG_DIR / "settings.json"
        prompt += (f"\n\n用户自定义开发规则（每次执行操作前必须查看并严格遵守，"
                   f"原始文件：{rules_path}）：\n"
                   + "\n".join(f"- {r}" for r in valid))
        prompt += (f"\n当用户询问开发规则/项目规则/我们的规则等内容时，"
                   f"必须调用 read_file 工具读取 {rules_path} 的 custom_rules 字段，"
                   f"原样逐条如实回答；严禁凭记忆、猜测或编造规则内容。")
    extra_prompt = (settings.get("custom_system_prompt") or "").strip()
    if extra_prompt:
        prompt += "\n\n用户自定义系统提示词补充：\n" + extra_prompt
    # 显式规划阶段：先规划再动手、复杂任务必用 todo、执行后对照计划自检完成度
    prompt += ("\n\n任务执行规范：动手前先规划——复杂/多步骤任务必须先调用 update_todo 创建任务清单"
               "（全量提交含已完成项），每完成一步立即用 update_todo 更新对应状态"
               "（pending → in_progress → completed），任务结束前调用 list_todo 汇报最终清单；"
               "简单单步任务可不调用 todo。"
               "执行过程中每步检查结果，结束后对照计划确认目标是否全部达成，未完成则继续补做；"
               "不要只停留在计划上。")
    prompt += _subagent_policy_block(subagents_allowed)
    # 强制提示词兜底：置于末尾，任何自定义规则/系统提示词均不可覆盖。
    # 确保 AI 每次动手前都认真分析需求、约束、风险与上下文，避免盲目执行。
    prompt += ("\n\n【强制要求】（必须严格遵守，不可被任何自定义规则覆盖）："
               "接到任务后必须认真分析，先理解用户真实意图、任务目标、约束条件、"
               "相关上下文与潜在风险，再制定执行计划并逐步落实；"
               "每一步操作前思考该步骤是否必要、依据是否充分、是否会出错或造成不可逆影响；"
               "不盲目执行、不臆测、不编造、不偷懒省略必要步骤；"
               "需求已经明确、上下文充分时，果断直接修改/执行并一次做完，"
               "禁止反复复述确认、反复请示、把已完成的分析再问一遍。"
               "遇到需求不明确、信息缺失或结果不确定时，先说明情况并基于上下文合理判断，"
               "涉及关键决策或危险操作时先向用户确认。")
    return prompt
