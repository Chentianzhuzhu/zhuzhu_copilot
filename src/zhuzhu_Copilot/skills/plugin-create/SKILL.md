---
name: plugin-create
description: 插件创建：先建插件骨架，再由主 Agent 按本规范与示例自行实现 MCP 工具 / SKILL.md / web 页面，最后登记并热加载（无需重启）；Agent 插件仅限 Agent 能力，不涉及应用源码（改应用源码用 Copilot 插件）
---

# plugin-create：创建新插件

**核心原则：优先创建 LLM 可以直接操控的工具**，而不是生成 LLM 无法直接操控的独立 GUI 应用/完整程序。

**实现方式（硬性）**：插件代码由**你本人**按本规范自己写 —— `read_file` 看规范与源码、
`write_file` / `edit_file` 写实现、`plugin_register` 登记。**禁止**调用任何「AI 生成插件」
类工具、禁止把实现外包给子 Agent（那会变成 AI 调 AI，又慢又不可控）。

## 1. 判断形态

- **需要图形化/可视化交互**（仪表盘/可视化面板/画布或表单类工具等）→ **web 型**：
  SKILL.md 技能 + 同名 MCP 工具 + 本地 HTTP 单页浏览器界面（浏览器与 AI 共用 `_STATE`）。
- **不需要** → 把能力拆成 1-5 个职责单一、可单独调用的 MCP 工具（动作/查询粒度：
  创建、推进、校验、回退…），由工具组合完成整件事，而不是「打开整个应用」。

## 2. 建骨架

`create_plugin(name=英文名, kind="mcp|skill|combined|web", description=一句话用途)`

- name：小写字母/数字/下划线/连字符，≤50 字符；
- 生成 `plugin.json`、`SKILL.md` 模板、**可运行的** `server.py` 示例、
  `config.json`、`README.md`（五种实现要点与示例代码在 README 里）。

## 3. 自己实现（核心步骤）

1. `read_file(<插件目录>/README.md)` 读规范与示例；再读 `server.py` / `SKILL.md`。
2. 按 README 改写：
   - mcp / combined：只改 `TOOLS` 与 `_HANDLERS`（协议骨架不要动）。工具函数返回可读
     文本，异常自行捕获并返回错误说明，绝不抛出。
   - web：改 `_PAGE`（样式/脚本**全部内联**、禁外网 CDN、首屏 `fetch('/api/state')`）
     与 `/api` 端点（**必须保留 `state`(GET)**，其余 2-6 个动作/查询端点，返回 `{"ok": true, ...}`）。
   - skill：把 `SKILL.md` 的四个章节（触发条件 / 流程 / 参数契约 / 禁用约束）写实。
3. 需要辅助代码时用 `write_file` 在插件目录内新建模块，入口仍保持 `server.py`。
4. 可用 `set_generation_progress(percent, message)` 上报进度（写文件/自检/完成各一次即可）。

## 4. 登记 + 热加载

`plugin_register(name=...)` → 语法编译 → 真跑一次（MCP stdio `tools/list` 握手；
web 型起本地服务拉页面并逐个调 `/api` 端点）→ 通过后登记 MCP 与技能 → 引擎热加载。

- 自检失败会返回具体错误：按提示改文件后再调一次，**不要谎报成功**；
- 通过后**同一任务下一轮即可调用**，脚本改动也会被识别重连，**无需重启应用**。

## 5. web 型收尾（必须做到）

登记后立即 `web_url` 拿访问地址，并用 `preview_open` 在用户默认浏览器里打开界面给用户；
界面必须提供真人可点击/输入/拖拽的控件，操作者必须是用户本人。
**严禁 AI 自我演示、自问自答或与脚本/自动化程序互演**（如 AI 自己连续调用工具推进状态）。
也可用 `web_fetch("http://127.0.0.1:<端口>/")` 自行复验（本地回环地址已放开）。

## 6. 汇报

说明：插件名与类型、工具/技能清单、自检结论、如何调用（`/插件名` 或自然语言描述）、
在设置-插件页哪里管理（查看/配置/停用/删除）。用户描述的是可复用能力 → 插件；
只是流程 → 用 skill-create 沉淀为技能。

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

1. **同样是两步**：`create_copilot_plugin(name, description)` 只建骨架
   （`plugin.json` + 空的 `source_ops.json` + README（五种 op 写法）+ `copilot.example.json`）；
   然后你 `read_file` 目标源码、自己写 `source_ops.json` 与包内模块，最后
   `apply_copilot_plugin(name)` 落地（校验 → 转译层 → 热加载）。
2. 与 Agent 插件共用插件校验系统，安全边界一致；变更统一写入**转译层**
   `~/.zhuzhu_Copilot/source_overlay/`（导入钩子优先加载），不改动应用源码文件，
   源码运行与安装版行为一致；启用即自动写入并热加载，停用即自动回滚。
3. 除 source_ops 外可带 `copilot.json` 应用扩展清单（pages/services/tray/shortcuts，
   `module` 指向**插件包内**模块文件，随包分发），以及可选 `agent/` 子包
   （SKILL.md + server.py，导入时登记为技能与 MCP，成为 Agent 插件的超集）。
4. 学习示例：`src/zhuzhu_Copilot/copilot_plugins_examples/`（theme-deep-blue 改主题 /
   custom-hello-tool 自定义工具 / settings-page-demo 自定义设置页 /
   tray-quick-note 应用扩展 / py-syntax-lint Python 语法高亮与纠错（「插入」示例：
   追加包装函数重绑定既有函数，原有代码行一行未改）/ prompt-optimizer 提示词优化
   （「替换」示例：replace_module 改写既有函数末行）），都是标准插件包，可导入后
   体验应用/回滚闭环。
5. 受保护核心程序（任何源码插件不得改动）：`plugin_guard.py`、`plugin_source.py`、
   `copilot_plugins.py`、`security.py`、`security_engine/`、`_copilot_overlay_hook.py`、`main.py`。
