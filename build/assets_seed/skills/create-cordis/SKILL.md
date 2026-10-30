---
name: create-cordis
description: 引导用户创建/定制自己的 AI Agent 工作流（Cordis 式）。当用户想自定义 AI 行为、创建 agent 核心文件、切换工作流、或提到"工作流/agent 核心文件/cordis"时使用。提供模板生成、单核心文件定制、热插拔切换的完整流程。
---

# create-cordis — 用户自定义 Agent 工作流

模仿 Cordis"一切皆可替换"理念：用户可在 `~/.zhuzhu_Copilot/workflows/` 下创建自己的
Agent 工作流，覆盖 AI 的核心文件（agent.py / llm.py / tools.py / skills / plugins / mcp.json），
切换即热插拔生效；内置默认工作流（`_default`）保留且只读不可删除。

## 一、必须使用的工具

- `list_workflows` — 先列出已有工作流与当前激活项，避免重复创建
- `create_workflow` — 创建工作流（name 必填；files 留空=整套，指定单文件=单独定制）
- `inspect_workflow` — 查看工作流结构/核心文件现状
- `edit_agent_file` — 读取/写入工作流核心文件（action=read|write）
- `switch_workflow` — 切换激活工作流（热插拔，立即重建引擎生效）
- `delete_workflow` — 删除用户工作流（默认工作流不可删）

## 二、标准流程

1. **先了解现状**：调用 `list_workflows`，向用户说明已有工作流与激活项。
2. **明确用户意图**（问清后再动手）：
   - 整套自定义 → `create_workflow(name=<名>, description=<说明>)` 生成全部核心文件；
   - 单核心文件定制 → `create_workflow(name=<名>, files=["tools.py"|"llm.py"|"agent.py"])`
     只生成该文件，其余模块回退内置默认（即"加入默认工作流"）。
3. **生成与定制**：
   - 生成后先用 `edit_agent_file(action=read)` 读取模板，理解其接口契约；
   - 按用户需求改写核心文件（`edit_agent_file(action=write, content=<完整文件>)`），
     写入前先规划，写入后自检语法与契约。
4. **热插拔生效**：调用 `switch_workflow(name=<名>)` 切换激活。工具会自动重建引擎，
   新的 LLM/工具/技能/系统提示立即生效，无需用户重启应用。
5. **验证**：`list_workflows` 确认已激活；如需让用户单独测试，提示其在 AI 面板直接
   发送一条测试任务即可。

## 三、核心文件契约（必须遵守）

| 文件 | 提供的接口 | 说明 |
|------|-----------|------|
| agent.py | AGENT_NAME / SYSTEM_PROMPT / build_system_prompt(agent_name, extra_skills) / on_task_start(engine) / on_task_end(engine) | 定义后整体替换内置系统提示/人格与生命周期钩子 |
| llm.py | LLMClient 类（__init__(base_url,api_key,model,timeout,protocol) + chat_stream + chat）或 make_client(config) 工厂 | 整体替换 LLM 客户端（自定义厂商/协议） |
| tools.py | TOOLS（OpenAI function schema 列表）+ execute_tool(name,args,allow_dangerous) -> {"text","images"} | 同名覆盖内置工具、新增自定义工具 |
| skills/ | 每个子目录含 SKILL.md（frontmatter: name/description） | 工作流专属技能，激活后合并进技能库 |
| plugins/ | 每个子目录含 plugin.json + 可选 SKILL.md / server.py | 工作流专属插件 |
| mcp.json | {"servers":[...]} | 工作流 MCP 服务器配置 |

## 四、规则与红线

- **默认工作流 `_default` 只读**：任何修改/删除其核心文件的请求都必须拒绝，并说明
  内置核心（agent_engine/agent_llm/agent_tools 等）由应用自带、不可删除。
- 工作流名仅允许字母/数字/下划线/中划线，且不能为 `_default`。
- 用户自定义代码写入前必须自审（语法、缩进、接口契约、无 mock、真实实现），
  写入后确认文件已保存并提醒用户可 `switch_workflow` 生效。
- 删除单个核心文件 = 该模块回退内置默认（可用 delete_workflow 或询问用户）。

## 五、工作团（Agent Team）规范

应用内置「工作团」概念：多个工作流 + 一个领导者（产品经理）组成默认专家团队。

- **默认团队**：领导者 `product_manager`（产品经理）+ 成员 `zhuzhu_copilot`（默认工作流，
  含 explorer_project_agent / sub_coding_agent）、`frontend_design`（前端设计）、
  `product_dev`（产品开发）、`backend_dev`（后端开发）、`product_debug`（产品调试）。
  团队配置存 `~/.zhuzhu_Copilot/team.json`，缺省用内置默认。
- **激活制**：用户 `@product_manager` 切到产品经理工作流即激活团队模式——自动开启共同
  上下文空间；产品经理作为领导者可 `look_context` 监督成员、`chat_with` 讨论、
  `pause_agent/resume_agent/warn_agent` 纠偏成员，并 `dispatch_sub_agents(agent=成员工作流)`
  总派发设计任务。未激活时各工作流独立运行。
- **专属能力**：每个工作流可在 `tools.py` 声明 `SUB_AGENT_ALLOWED`（该工作流子 Agent
  可额外调用的工具名单）；工作流自定义 tools/skills/plugins/mcp.json 供本工作流 Agent
  独自调用；内置 tools/skills/MCP/plugins 为所有工作流共享默认能力。
- **子 Agent 权限**：`register_sub_agent` 支持 `allow_chat`（是否可参与 Agent 间聊天）与
  `share_context`（是否允许领导者 look_context 查看其上下文轨迹），缺省关闭。
- **上下文延续**：`@工作流` 切换时继承原工作流的活跃共享上下文空间——新工作流主 Agent
  能基于既有协作上下文继续回答；未读团队消息在其下一轮任务开始时注入。

## 六、创建子 Agent 规范（增强）

用户要求「创建子 agent / 新增一个能干活的下属 agent」时，必须用 `register_sub_agent`
注册为注册式子 Agent（唯一同时支持 @直呼、主 Agent 调用 sub_<名>、加入共同上下文空间
三种用法的形态），**禁止**用 `create_agent` / `register_agent_network(op=create_agent)`
顶替（那是会话级人格切换，主 Agent 调不到它）。

流程：先 `list_sub_agents` 看现状 → 与用户确认 name / description / goal（任务指令模板）
/ allowed（工具白名单，可选）/ persona（可选人格）/ shared_context（是否默认共享）/
allow_chat（是否可聊天）/ share_context（是否可被监督）→ `register_sub_agent` 注册 →
提示三种调用方式。默认工作流 `zhuzhu_copilot` 已预置 `explorer_project_agent` 与
`sub_coding_agent` 两个注册式子 Agent 作为示例。
