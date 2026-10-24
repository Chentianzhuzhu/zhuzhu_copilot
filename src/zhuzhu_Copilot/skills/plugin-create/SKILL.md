---
name: plugin-create
description: 插件创建：根据用户自然语言描述自动生成可运行插件（MCP server + SKILL.md 技能 + web 型本地 HTTP UI 三件套）并加载，生成后自动冒烟自检；Agent 插件仅限 Agent 能力，不涉及应用源码（改应用源码用 Copilot 插件）
---

# plugin-create：创建新插件

**核心原则：优先创建 LLM 可以直接操控的工具**，而不是生成 LLM 无法直接操控的独立 GUI 应用/完整程序。

1. 先判断是否「需要图形化/可视化交互」：
   - **是**（仪表盘/可视化面板/画布或表单类工具等，用户在界面上直观操作/观看）→ 选 **web 型**：
     一次生成**三件套** —— SKILL.md 技能 + 同名 MCP 工具 + 本地 HTTP Server 单页浏览器界面
     （浏览器与 AI 共用同一份状态，LLM 通过工具读写状态）。
     流程：create_plugin(kind="web") → 调 web_url 工具拿访问地址 → preview_open 在用户默认浏览器里打开给用户。
     质量约束（违反会被自动自检打回）：页面所有样式/脚本内联、禁外网 CDN（离线可用）；
     **必须提供 `state`（GET，读状态）端点**，其余 2-6 个为动作/查询粒度端点；
     端点实现从 args 取参、读写共享状态 `_STATE`、返回可读文本、异常自行捕获（绝不抛出）；
     页面首屏即 fetch('/api/state') 渲染状态，并在页面上显示操作结果/错误（禁止静默失败）。
   - **否** → 走「能力 → 工具」拆解：把能力拆成 1-5 个职责单一、可单独调用的 MCP 工具
     （参数 schema 清晰、返回可读文本结果），由 LLM 通过工具组合完成整件事；
     工具是「动作/查询」粒度（创建、推进、校验、回退…），不是「打开整个应用」。
2. **web 型硬性要求（必须做到，否则视为未完成）**：
   ①创建后必须立即调用 web_url 获取地址并用 preview_open 在用户默认浏览器里打开界面给用户；
   ②界面必须提供可供真人点击/输入/拖拽的交互控件，操作者必须是用户本人，
     状态变化由用户亲手操作驱动；③严禁 AI 自我演示、自问自答或与脚本/自动化程序互演
     （如 AI 自己连续调用工具推进状态）；AI 只负责读写共享状态、辅助用户（校验输入/展示状态/提示规则）。
   ④生成链路**自带冒烟自检**（语法编译 → 真起本地服务拉页面并逐个调 /api 端点 → MCP tools/list
     握手），未通过会把错误回灌给模型自动修正一轮；仍不通过会如实告知且**不登记 MCP**。
     据报错信息修正后重试即可，**不要谎报成功**；也可用
     web_fetch(url="http://127.0.0.1:<端口>/") 自行复验（本地回环/内网地址已放开）。
3. 确认插件类型：
   - web：三件套（SKILL.md 技能 + MCP 工具 + 本地 HTTP 浏览器界面，图形化交互类首选）
   - mcp：仅提供工具能力（MCP server）
   - skill：仅沉淀为标准技能（SKILL.md）
   - combined：同时提供技能与 MCP 工具（默认）
4. 用 create_plugin 工具创建，description 写清插件要做什么、提供哪些能力；
   tools/api 的 implementation 用标准库实现真实操作，异常自行捕获并返回错误说明文本。
   生成耗时较长时按阶段调用 `set_generation_progress(percent, message)` 上报进度
   （如 5 分析需求 / 70 写入文件 / 95 自检 / 100 完成），让用户在面板上看到进度；
   该工具与 create_plugin 内部进度回调同源，无需重复上报同一阶段。
   本工具**不设 40s 放弃超时**：生成 + 自检 + 自修可能耗时数分钟，由停止按钮取消。
5. 创建成功后提示：插件已统一存入插件目录，MCP 工具与技能均已自动登记即时生效
   （返回文案会写明自检结论与是否登记）；用户可在设置-插件页查看/管理/停用/删除；
   MCP 服务器需保存后自动重连。
6. 若用户描述的是可复用的独立能力，适合沉淀为插件；若只是流程，用 skill-create 即可。

## Agent 插件的能力边界（不含源码级）

Agent 插件只扩展 **Agent 自身**：MCP server 工具、SKILL.md 技能、web 型本地 HTTP 页面
（可视化交互）。**不涉及应用源码**。需要改应用源码（主题/界面/工具/语言/任何模块）时，
必须用下面的 Copilot 插件。

插件包元数据规范：名称 name / 开发者或公司 author / 更新时间 updated_at / 插件图标 icon
（包内图标文件），导入与创建时自动补齐，设置-插件页展示。
插件可携带 `config.json` 配置项（键 → 文本/数字/布尔标量）：用户在设置-插件页
「详情/配置」里编辑；MCP 型插件的配置会以环境变量（键名自动大写，如 api_key → API_KEY）
注入 server 子进程，server.py 里用 `os.environ.get("API_KEY")` 读取。PATH/HOME 等
保留环境变量名禁止作为配置键。

## Copilot 插件（应用级独立体系，与 Agent 插件分开，应用范围更广）

当需求是**定制整个应用本身**——改主题、自定义 Agent、自定义工具、自定义语言、
增删替换任何模块，或注册主窗口页面 / 后台服务 / 托盘动作 / 快捷键——用 Copilot 插件
（`create_copilot_plugin` / `apply_copilot_plugin` / `revert_copilot_plugin`，
设置页「Copilot 插件」）：

1. 与 Agent 插件共用插件校验系统，安全边界一致；变更统一写入**转译层**
   `~/.zhuzhu_Copilot/source_overlay/`（导入钩子优先加载），不改动应用源码文件，
   源码运行与安装版行为一致；启用即自动写入并热加载，停用即自动回滚。
2. 除 source_ops 外可带 `copilot.json` 应用扩展清单（pages/services/tray/shortcuts，
   `module` 指向**插件包内**模块文件，随包分发），以及可选 `agent/` 子包
   （SKILL.md + server.py，导入时登记为技能与 MCP，成为 Agent 插件的超集）。
3. 学习示例：`src/zhuzhu_Copilot/copilot_plugins_examples/`（theme-deep-blue 改主题 /
   custom-hello-tool 自定义工具 / settings-page-demo 自定义设置页 /
   tray-quick-note 应用扩展 / py-syntax-lint Python 语法高亮与纠错（「插入」示例：
   追加包装函数重绑定既有函数，原有代码行一行未改）/ prompt-optimizer 提示词优化
   （「替换」示例：replace_module 改写既有函数末行）），都是标准插件包，可导入后
   体验应用/回滚闭环。
4. 受保护核心程序（任何源码插件不得改动）：`plugin_guard.py`、`plugin_source.py`、
   `copilot_plugins.py`、`security.py`、`security_engine/`、`_copilot_overlay_hook.py`、`main.py`。
