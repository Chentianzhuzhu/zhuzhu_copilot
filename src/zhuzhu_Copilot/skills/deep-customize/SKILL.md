---
name: deep-customize
description: 深度自定义总入口（Cordis 一切皆可替换/热插拔）：盘点工作流/技能/插件/UI/UX/MCP/按钮全部可自定义维度，引导用户对任意功能做深度自定义或新增能力
---

# deep-customize：深度自定义 / 新增功能总入口（Cordis）

当用户要求"深度自定义 / 深度定制 / 全面自定义 / 自定义功能 / 新增功能 / 添加功能 / 扩展功能 / 改造 / 把 xxx 改得更好用"时使用本技能。
核心思想：Cordis —— **一切皆可替换、热插拔生效**。用户工作流/UI/UX/插件/技能覆盖内置实现，切换即生效，缺失自动回退内置，永不破坏系统。

## 0. 优先扩展、禁止滥新建（最高优先级，先于一切）
**小功能的改动，一律先扩展现有资源，禁止新建工作流。** 判定与落地顺序：
- **小功能 = 新增功能面板、新增子 agent、新增/微调工具、小 UI 调整、小钩子**，这类改动直接注入当前工作流：
  - **新增子 agent** → `list_sub_agents` 看现状 → `register_sub_agent`（name/description/goal/allowed）注册进**当前工作流**，立即可作为 `sub_<name>` 工具被主 Agent 调用，不新建工作流。
  - **新增功能面板** → 用 `register_feature_panel`（title/width/height/python）把 panel.py 写进**当前工作流**，成为可拖拽扩展浮窗。
  - **新增/覆盖工具** → `edit_agent_file(name=<当前工作流>, file="tools.py")` 写入当前工作流 tools.py，绝不为单独一个工具新建工作流。
  - **自定义代码带第三方 import** → `set_feature_deps`（op=declare deps=["xxx"]）写入当前工作流 requirements.txt，代码顶部可带第三方 import，加载时自动补齐依赖。
  - **小 UI 调整 / 钩子** → `set_app_background` 调外观（背景图 + 玻璃参数，见 app-background 技能），或 `edit_agent_file` 改 agent.py 钩子。
- **只有功能大且独立**（整套 Agent 人格、专用工具集，现有工作流完全承载不了）时才新建工作流（`create_workflow`）。
- 判断口诀：**能不能塞进现有工作流面板.py / 工具.py / 子agent 注册？能就绝不新建。**

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
- **外观（背景图 + 磨砂玻璃材质）**：`set_app_background` 设置全局背景图与五个玻璃维度
  （模糊/磨砂/边缘高光/透明度/液态感），即时生效；完整用法见 app-background 技能。
- **面板按钮（恒显示）**：`register_panel_btn` op=register 注册顶部按钮栏按钮。
- **子 agent（小功能的常用落地通道，绑定当前工作流）**：`list_sub_agents` 看现状 → `register_sub_agent`
  （name/description/goal/allowed）注册进当前工作流，立即作为 `sub_<name>` 工具可调用，不新建工作流。
  用户说「创建一个子 agent / 新增一个下属 agent」时必须走这条注册通道：注册式才同时支持
  @<名> 直接调用、主 Agent 调用 `sub_<name>`、以及由主 Agent 逐次分配是否加入共同上下文空间与
  传参 context。**不要用 `create_agent` / `register_agent_network(op=create_agent)` 顶替**——
  那只创建「主 Agent 人格」（@agent 会话级切换），主 Agent 调不到它。
- **功能面板（小 UI 面板的常用落地通道，绑定当前工作流）**：`register_feature_panel`
  （title/width/height/python，python 提供 build_panel(owner) 返回 QWidget）把 panel.py 写入当前工作流，
  扫描后成为可拖拽扩展浮窗。
- **依赖（自定义代码带第三方 import）**：`set_feature_deps` op=declare 声明（写入当前工作流 requirements.txt，
  deps=["pandas"]）；op=install 立即安装；op=list 查看。加载自定义代码时自动补齐缺失依赖，无需手动 pip。
- **MCP 服务器**：通过插件（create_plugin kind=mcp/combined）或工作流 mcp.json 声明。

## 3. 收尾
- 汇报：改了什么维度、新功能如何触发/使用、如何切回/删除（安全兜底：工作流缺失回退内置、
  UI/UX 加载失败回退默认、插件可停用/删除）。
- 若用户想先沉淀可复用能力：适合插件用 plugin-create，适合流程用 skill-create，
  适合整套 Agent 定制用 create-cordis。
