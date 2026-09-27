# Agent 工作流（Cordis 式自定义）

本目录是你的自定义 AI Agent 工作流。每个工作流是一套"核心文件"，激活后
热插拔替换内置 Agent 的对应能力；缺失的文件自动回退到内置默认实现。

## 核心文件

| 文件 | 作用 | 暴露接口 |
|------|------|----------|
| agent.py | 自定义 Agent 人格/系统提示/生命周期 | AGENT_NAME / SYSTEM_PROMPT / build_system_prompt / on_task_start / on_task_end |
| llm.py | 自定义 LLM 客户端（整体替换） | LLMClient 类 或 make_client(config) |
| tools.py | 自定义工具集（覆盖/新增） | TOOLS + execute_tool(name,args) |
| skills/ | 工作流专属技能目录 | SKILL.md（frontmatter 格式） |
| plugins/ | 工作流专属插件目录 | plugin.json + SKILL.md / server.py |
| mcp.json | 工作流 MCP 服务器配置 | {"servers":[...]} |
| panel.py（可选） | 工作流专属 UI 功能面板 | TITLE / WIDTH / HEIGHT / build_panel(owner)->QWidget |

## 使用方法

1. 用 create_workflow 工具创建（或复制本模板）：
   - 整套工作流：`create_workflow(name="myagent")`
   - 仅单个核心文件（加入默认工作流）：`create_workflow(name="myagent", files=["tools.py"])`
2. 用 edit_agent_file 工具读取/修改核心文件，或直接在文件管理器里编辑
3. 用 switch_workflow 切换激活（立即重建引擎生效，无需重启应用）
4. 用 list_workflows / inspect_workflow 查看状态

## 规则

- 默认工作流 `_default` 映射内置模块，只读，不可删除/覆盖。
- 用户工作流可随时创建/修改/删除；删除单个核心文件后该模块回退内置。
- 每次编辑核心文件后需要 switch_workflow 重新激活（或切走再切回）以生效。
