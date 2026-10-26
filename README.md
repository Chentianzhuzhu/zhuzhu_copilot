# zhuzhu Copilot

一款基于 PyQt6 的 **Windows 桌面 AI Agent 应用**。它把「会调用工具的对话式 AI」做成了一个常驻桌面的助手：可以对话、读写文件、执行命令、操控网页、生成办公文档、播放音乐、朗读语音，并带有一套本机安全防护与自动更新能力。面向希望在 Windows 上用自然语言直接驱动本机操作的普通用户与开发者；对开发者而言，它的工作流、技能、插件、MCP、功能面板均可自定义与热插拔。

- 运行环境：Windows 10/11，Python 3.13（源码运行），或直接使用安装包
- 技术栈：Python 3.13 + PyQt6 + QtWebEngine；更新服务端为 Spring Boot 3
- 入口文件：`src/main.py`
- 下载安装：[GitHub Releases](https://github.com/Chentianzhuzhu/zhuzhu_copilot/releases) 直链获取安装包，或走官网下载页 / 更新服务器（见第六节 6.1）

---

## 一、功能详解

### 1.1 AI Agent 引擎

- **多轮工具调用循环**：`src/zhuzhu_Copilot/core/agent_engine.py`。循环「观察 → 调用工具 → 验证结果」，LLM 逐 token 流式回调给 UI；每个工具执行前可回调确认，沙箱判定为危险的工具即使被批准也由 `agent_tools` 硬拒绝。
- **并发工具执行**：同一轮返回多个可并发工具时会批量并行（写类按解析后路径加锁串行、只读类直接并行，线程数上限 4）；结果回调仍按原始调用顺序触发，保证对话展示与上下文落库顺序不乱。可通过设置项 `concurrent_edits=false` 整体关闭。
- **后台并发执行**：`src/zhuzhu_Copilot/core/agent_background.py`。复杂任务（多步骤、长时间运行、多工具调用）自动判定并转入后台线程池执行，不阻塞当前对话，用户可继续发送其他消息。支持后台并发执行命令、后台并发派发注册式子 Agent 与临时子 Agent、跨工作流主 Agent 协调；后台任务状态（运行中/完成/失败）、进度、输出实时回传 UI，可取消。工具描述标记 `background_capable`，新增 `background_task_status` / `cancel_background_task` 工具。
- **模型接入**：`src/zhuzhu_Copilot/core/agent_llm.py`，OpenAI 兼容 `/v1/chat/completions`，支持 SSE 流式、`tools`/`tool_choice` 工具调用、图像输入（data URL / http(s)），发送前启发式估算 token、响应后累计上游 `usage`。失败按 429/5xx/网络自动重试（指数退避）。默认上下文窗口 256k tokens（输入+输出），开启 1M 模式后为 1048576 tokens。
- **HTTP 连接复用**：`src/zhuzhu_Copilot/core/agent_http.py`，按 (scheme, host, port) 复用 keep-alive 连接，配置了代理或关闭能力开关时回退 urllib。
- **上下文管理与压缩**：`src/zhuzhu_Copilot/core/agent_engine.py`。分层阈值触发摘要压缩 + 发送前硬裁剪兜底，带压缩冷却与「最近窗口保留预算」；超长工具返回文本（read_file / web_fetch / extract_text）自动压缩，LLM 不可用时回退启发式压缩。
- **Token 统计**：同一文件内维护全程累计用量 `tokens` 与最近一次请求的上游 `last_usage`，供上下文占用面板展示。
- **人设与自定义 Agent**：`src/zhuzhu_Copilot/core/agent_agents.py`。`@<agent>` 在会话级切换人格（系统提示词），可选绑定某个工作流以获得其工具/技能；`@_default` 切回内置默认人格。
- **组件装配与依赖注入**：`src/zhuzhu_Copilot/core/agent_deps.py`（自定义代码的 `requirements.txt` 自动补齐依赖）、`agent_edit.py`（同一文件写入按路径加锁）、`agent_json.py`（工具参数解析）。

### 1.2 工具集

工具注册与执行在 `src/zhuzhu_Copilot/core/agent_tools.py`（LLM function calling schema + 沙箱评估）。主要类别：

- **文件**：`read_file` / `write_file` / `edit_file` / `search_replace` / `insert_lines` / `undo_file` / `delete_file` / `list_directory` / `search_files` / `grep` / `search_code` / `find_app`（`agent_find.py`）。
- **命令与沙箱**：`run_command` / `check_command`；沙盒模块 `src/zhuzhu_Copilot/core/sandbox.py`（命令白名单、路径白名单、输出限制、后台轮询）。
- **浏览器**：`browser_open` 等内置工具族（`browser_control.py`），启动独立 WebView/CDP 实例；`browser_eval` 可在页面注入 JS；支持多标签页 `browser_tabs`。
- **屏幕操控**：`screen_info` / `screen_shot` / `click` / `swipe` / `type_text` 等（`screen_control.py`），支持虚拟化桌面操作。
- **快捷应用**：`find_app`（开始菜单 + 注册表模糊索引）、`uninstall_app`、`migrate_app`。
- **剪贴板**：`clipboard`（读 / 写）。
- **文档提取**：`extract_text`（txt/md/log/json/csv/docx/pptx/xlsx；PDF 需 pypdf）。
- **文档生成**：`create_docx` / `create_pptx` / `create_xlsx`；编辑：`edit_docx` / `edit_pptx` / `edit_xlsx`；美化：`beautify_*`。
- **系统管理**：`system_info` / `get_time` / `env_var` / `optimize_memory`。
- **任务编排**：`update_todo` / `list_todo`；记忆：`save_memory` / `load_memory`。
- **工作流/技能/Agent 管理**：`list_workflows` / `create_workflow` / `switch_workflow` / `inspect_workflow` / `use_skill`（对话内按名调用技能：读取 SKILL.md 规范并入本任务）/ `create_skill` / `create_plugin`（Agent 插件：MCP server + SKILL.md + web 型本地 HTTP 页面）/ `create_copilot_plugin`（Copilot 应用级插件：AI 生成源码变更清单 + 应用扩展清单）/ `apply_copilot_plugin` / `revert_copilot_plugin` / `register_sub_agent` 等（`skill_manager.py` / `workflow_manager.py` / `sub_agent_manager.py`）。
- **MCP 注册**：`create_mcp` / `list_mcp` / `set_mcp`。
- **微信 ClawBot**：`send_file_to_wechat` / `send_files_to_wechat`（推送文件到绑定的手机微信）；入站文件读取由 `wechat_bridge.py` 处理。
- **进度反馈**：`set_generation_progress`（长耗时生成任务）。
- **子 Agent 调度**：`dispatch_sub_agents` / `explore_project` / `search_large` / `ask_user`。
- **团队通讯**：`chat_with` / `pause_agent` / `resume_agent` / `warn_agent` / `look_context` / `list_agents_network`。

### 1.3 技能系统

`src/zhuzhu_Copilot/core/skill_manager.py` 扫描 `~/.zhuzhu_Copilot/agent/skills/<name>/SKILL.md`。每个 SKILL.md 包含 description 与 instruction（可含 tool 白名单）。内置 23 个技能：brainstorming、browser-control、cmd-ops、code-review、complex-task、create-cordis、deep-customize、doc-gen、download-skill、file-ops、frontend-design-pro、git-commit、image-gen、memory、plugin-create、skill-create、skill-mgmt、sub-agent、system-admin、systematic-debugging、test-driven-development、web-search、writing-plans。支持 `@skillname` 激活。

技能进入上下文有两条路：**关键词自动匹配**（按任务输入命中 `_SKILL_KEYWORDS`，自动注入规范）与**对话内点名调用**——`use_skill` 工具按名读取技能的「说明 + 规范正文」直接返回给模型并纳入本任务（后续轮次持续生效、路由硬拦截随之覆盖），`name` 留空则返回可用技能清单。用户说「用 plugin-create 技能帮我生成一个 copilot 插件」时，即使措辞不在关键词表里，Agent 也能点名调到规范再动手。

### 1.4 MCP 支持

`src/zhuzhu_Copilot/core/mcp_registry.py` 维护 `mcp_servers.json`，支持 stdio / SSE 两种传输（协议版本 `2024-11-05`）。启动时加载并暴露 `tools/list` 握手；工具名带 `<server>_` 前缀去重。示例本地服务器 `mcp_servers/local_mcp_server.py`。

### 1.5 插件系统（两套体系：Agent 插件 与 Copilot 插件）

应用有**两套分开的插件体系**，目录、设置页入口、能力范围都不同：

| | **Agent 插件** | **Copilot 插件** |
| --- | --- | --- |
| 目录 | `~/.zhuzhu_Copilot/plugins/` | `~/.zhuzhu_Copilot/copilot_plugins/` |
| 设置页 | 设置 → **Agent 插件** | 设置 → **Copilot 插件** |
| 代码 | `core/agent_plugins.py` | `core/copilot_plugins.py` |
| 扩展对象 | 只扩展 **AI Agent** 的能力，**不涉及应用源码** | 扩展**整个 Copilot 应用**，可改应用全部源码（安全层除外） |
| 能力 | MCP server 工具、SKILL.md 技能、web 型本地 HTTP 可视化展示页面 | 源码级定制任何模块 + 应用级扩展（页面/服务/托盘/快捷键）+ 可附带 Agent 能力 |
| 类型 kind | `mcp` / `skill` / `combined` / `web` | `copilot`（含 source_ops + 可选 copilot.json + 可选 agent/ 子包） |

两套体系**共用同一个插件校验系统（`core/plugin_guard.py`）**，安全边界完全一致；源码变更引擎（`core/plugin_source.py`）**只服务于 Copilot 插件**。

**插件包规范**：每个插件包包含 `plugin.json` 元数据——插件名称（name）、开发者/公司（author）、更新时间（updated_at）、插件图标（icon，**包内方形 PNG，建议 128~256px，只写文件名如 `icon.png`，不能带目录**），以及 description / kind / enabled 等；导入与创建时自动补齐，插件页列表展示开发者、更新时间与图标（卡片 44px 等比缩放，缺失时显示占位图标）。Copilot 插件还可带 `config.json`（用户可编辑的键值配置）与可选 `agent/` 子包。

> **想直接动手做插件？** 跳到 **第八章「插件开发指南（手把手）」**——从零到可导入的完整流程、三种源码级写法、图标配置、exe 环境验证与排错表都在那里。

**源码级定制（仅 Copilot 插件，改应用全部源码、安全层除外）**：插件包内 `source_ops.json`（或 plugin.json 的 `source_ops` 字段）描述对应用源码的变更清单（`path` 相对源码根 `src/`，但变更**一律写入转译层**，不改动应用源码文件）——可把功能源码**插入到任何源代码文件**、**新建源码文件**、**一次修改多个源码文件并集成功能**。改主题、自定义 Agent、自定义工具、自定义语言、增删替换任何模块，都是这条路：

| op | 作用 |
| --- | --- |
| `create_file` | 新建功能模块（创建新模块） |
| `append` | 追加到现有代码模块之后（可给 `after` 锚点，缺省追加到文件末尾） |
| `insert_after` | 在锚点（`anchor`，须唯一）或指定行（`line`）之后插入 |
| `insert_at` | **按行号插入**：在第 `line` 行之前插入（1 起；`line=行数+1` 即追加到末尾） |
| `replace_lines` | **按行号替换**：替换第 `start_line`..`end_line` 行（含边界） |
| `replace_module` | 替换模块（`start`..`end` 锚点，或 `start_line`..`end_line` 行号；`end` 缺省到文件尾） |
| `replace_file` | 整体替换文件 |

**统一写入转译层（Copilot 插件，源码运行与安装版行为一致）**：变更不落应用源码目录，而是写入用户数据目录的**转译层**（`~/.zhuzhu_Copilot/source_overlay/`，目录结构镜像源码相对路径），由 `src/_copilot_overlay_hook.py` 的导入钩子让 `zhuzhu_Copilot.*` 的导入**优先命中转译层**（没有的模块回落原版；`__file__` 指回原版路径，保证包内资源解析不回退）。随包分发源码快照 `_copilot_src/`（spec datas）作为「原版内容」基准，供插入/替换类 op 取当前生效版本。**启用插件时自动写入并热加载，停用时自动回滚**（**导入 / AI 创建后若为启用状态，立即自动写入、热加载并刷新插件列表**，无需重开设置页或手动停用再启用）；只有包根与持有进程级状态的模块（`agent_panel` / `main_window` / `styles`）会在下次启动时生效。

应用链路（启用插件自动执行 / `apply_copilot_plugin` 工具）：**插件校验系统**（危险即整体删除插件包并提示）→ 逐文件备份到插件目录 `backups/` → 逐条应用 → 全部 `py_compile`，任一失败**整体回滚**（源码一行不动）→ 写 `applied.json` 清单。停用或 `revert_copilot_plugin` 据此卸载回滚：新建的文件删除、修改的文件还原备份（还原内容与原版一致时直接删掉转译层副本，让模块回落原版）；应用后被用户手动改过的文件跳过不覆盖。路径仅限源码根内的相对路径（禁绝对路径/`..` 穿越）。

**应用级扩展（copilot.json，仅 Copilot 插件）**：声明式注册应用能力，`module` 指向**插件包内**模块文件（随包分发、不走源码树），由主窗口启动时按文件路径动态导入并调用：

```json
{
  "pages":      [{"id": "my-page", "title": "我的页面", "icon": "icon.png",
                  "module": "pages/my_page.py", "widget": "MyPage"}],
  "services":   [{"id": "bg-job", "title": "后台服务", "module": "services/job.py",
                  "entry": "start", "stop": "stop"}],
  "tray":       [{"id": "quick-do", "title": "托盘动作", "module": "pages/my_page.py",
                  "action": "quick_do"}],
  "shortcuts":  [{"id": "open-page", "title": "打开页面", "hotkey": "ctrl+alt+m",
                  "module": "pages/my_page.py", "action": "on_hotkey"}]
}
```

页面以新页签追加到 Copilot 浮层（应用/防护/通用/工具）之后；服务随进程常驻、退出时统一停；托盘动作追加到托盘菜单；快捷键为 `QShortcut`（形如 `ctrl+alt+m`）。`id` 全局唯一（重复拒绝）。Copilot 插件还可带可选 `agent/` 子包（`SKILL.md` + `server.py`），导入时登记为技能与 MCP 服务器，成为 Agent 插件的超集。

**热插拔（停用即摘、启用即挂，不必重启）**：页面页签、玻璃窗口、托盘动作、快捷键都是可撤销登记——停用/删除插件时立刻从界面上摘掉（`main_window.unregister_copilot_plugin`），启用时当场挂上；后台服务也是即时启停。**只有源码级变更落到被热加载跳过的模块（`agent_panel` / `main_window` / `styles`）时才需要重启应用**，见 8.5 的对照表。

页面条目还可以加 `"window": "glass"`，把页面从「浮层页签」改成**独立无框液体玻璃窗口**（GPU 实时折射/反射/边缘高光，详见 8.9）。

**插件校验系统（`src/zhuzhu_Copilot/core/plugin_guard.py`，内置唯一、不可替换的程序）**：所有插件（两套体系）在导入 / AI 生成 / 源码应用之前都必须通过校验——① 静态危险规则扫描（动态执行 `eval/exec`、`os.system`/`shell=True`、注册表写、持久化、凭据窃取、大规模删除、关闭防护、混淆载荷、篡改校验程序等）；② LLM 源码评审（送当前配置的模型判定 safe/dangerous，LLM 不可用时降级为纯规则判定并在结论中注明）。判定危险的代码**直接强制删除**并明确提示用户（哪个插件、哪些文件、哪条规则、LLM 结论），源码级插件危险则整体移除插件包；删除记录写入 `plugins/.guard_log.jsonl`。受保护清单 `PROTECTED_CORE_FILES`（`plugin_guard.py` / `plugin_source.py` / `copilot_plugins.py` / `security.py` / `security_engine/` / `_copilot_overlay_hook.py` / `main.py`）内的程序任何源码插件不得改动，plugin.json 也没有任何字段可以跳过校验。

**配置与管理（设置页，两套体系都有）**：双击插件或点「详情/配置」打开插件详情对话框——① **基本信息**：名称/类型/开发者/更新时间/描述/构成/校验结论；② **插件配置**：键值表编辑，保存到插件目录 `config.json`（仅文本/数字/布尔标量，禁配 `PATH`/`HOME` 等保留环境变量名）；对 MCP 型插件，配置以环境变量（键名自动大写，如 `api_key` → `API_KEY`）经 stdio 传输注入 server 子进程（继承当前进程环境后覆盖，`agent_mcp._StdioTransport`），保存即更新 `mcp_servers.json` 对应服务器 env 并自动重连生效；③ **源码变更**：`source_ops` 清单与应用状态预览（Copilot 插件另有**应用扩展**页签展示 copilot.json 注册的页面/服务/托盘/快捷键）；④ **校验记录**：该校验系统对该插件的拦截/通过日志。对话框内可**导出插件包**为 zip（含 plugin.json/配置/图标/源码，排除 `__pycache__`、`backups/`、`applied.json` 等本机数据），导出包可直接再导入，配置不丢。Copilot 插件页顶部显示当前**生效位置**（转译层路径与已加载模块数），卡片状态显示「服务运行中 / 服务未运行 / 服务加载失败 / 已写入转译层」，用于快速判断插件是否真的生效。

**开发者上手（示例插件）**：`src/zhuzhu_Copilot/copilot_plugins_examples/` 随包提供**九个**可导入学习的示例——`theme-deep-blue`（改主题）、`custom-hello-tool`（自定义工具：新模块 + TOOLS 登记 + 分发器接线）、`settings-page-demo`（自定义设置页：四条 op 跨两文件集成）、`tray-quick-note`（应用扩展：托盘动作 + 后台服务，模块随包分发）、`py-syntax-lint`（**Python 语法高亮与纠错**：源码级「插入」——新模块给代码块着色 + 新增 `check_python_syntax` 工具定位语法错误，原有代码行一行未改）、`prompt-optimizer`（**提示词优化**：源码级「替换」——新增 `optimize_prompt` 工具把粗略提示词改写成六要素结构化提示词，并替换 `_base_prompt_parts` 末行把「提示词优化纪律」追加进系统提示词）、`agent-scorecard`（**Agent 能力评分**：纯运行时服务，任务结束弹评分窗展示耗时 / 工具调用 / token，评分回传 Agent 提示自我反省）、`perf-turbo`（**性能优化**：新建性能内核 + 替换 `_md_to_html` 加跨会话渲染缓存 + 流式节拍随实测帧延迟自适应 + `perf_report` 工具 + 实时性能看板与三档调速）、`code-map`（**工作区代码地图**：新建扫描内核 + `code_map` 工具 + 看板页 + 预热服务 + Agent 技能 + **自定义图标**，综合性插件的标准范本）。每个示例都是标准插件包（plugin.json + source_ops.json / copilot.json），在设置-Copilot 插件页「导入插件包」后**启用即自动写入并热加载**；`copilot_plugins_examples/README.md` 是示例区的开发者指南，根目录 **第八章** 是手把手教程（含三种源码级开发方法、图标配置、op 语法、生命周期、exe 验证与排错表）。

### 1.6 子 Agent 与团队协作

- 子 Agent 独立上下文 + 工具白名单；`dispatch_sub_agents` 线程池并发派发。
- **后台并发派发**：注册式子 Agent 与临时子 Agent 均支持 `background` 参数，转入后台线程池执行，不阻塞当前对话；输出流式回传，完成后汇总。
- **跨工作流主 Agent**：主 Agent 可在后台协调多个工作流中的注册子 Agent，统一调度与结果汇总。
- 团队模式 `@product_manager`，共同上下文空间（`agent_bus.py`），领导者可监督/纠偏/派活。
- 注册子 Agent：`register_sub_agent`，支持 `persona` / `allowed` / `shared_context`。
- 通讯：`chat_with` / `look_context` / `pause_agent` / `resume_agent` / `warn_agent`。
- 网络总览：`list_agents_network`。

### 1.7 工作流

Cordis 式「一切皆可替换」，目录 `~/.zhuzhu_Copilot/workflows/`。核心文件：`agent.py` / `llm.py` / `tools.py` / `skills/` / `plugins/` / `mcp.json` / `panel.py`。内置预设：backend_dev、frontend_design、product_debug 等 7 个。热切换 `switch_workflow`。

### 1.8 安全能力

`src/zhuzhu_Copilot/core/security_engine.py` 每轮 tick() 巡检一次，模块开关由 `config/security.json` 控制：
- **YARA 恶意特征匹配**（`yara_engine.py`，缺失时降级禁用）。
- **深度分析流水线**：规则 + YARA + LLM 三段判罚归一化。
- **PE 静态分析**：导入表、节表、特征码。
- **Authenticode 签名验证**：验证链完整、未篡改。
- **隔离区**：可疑文件移至隔离区，白名单放行。
- **下载防护**：URL 检查、文件大小上限、防重定向链。
- **勒索防护**：哨兵诱饵文件、后缀爆发检测、VSS 快照监控。
- **DNS 防护**：hosts 劫持检测、哈希基线。
- **弹窗拦截**：WinEventHook 监控广告窗口，自动关闭。
- **网络防御**：ARP/SYN/端口扫描检测，命中后防火墙封禁。
- **执行守卫**：纯 Win32 监控新进程，拦截格机命令与无文件攻击。
- **输入守卫**：全局低级钩子，AI 操控期间检测用户手动操作。
- **插件校验**：`plugin_guard.py` 内置唯一、不可替换——所有插件（Agent 插件与 Copilot 插件）导入/生成/应用前过静态危险规则 + LLM 源码评审，危险代码强制删除并提示用户（详见 1.5）。
- **转译层（源码覆盖层）**：`_copilot_overlay_hook.py` 导入钩子让 `zhuzhu_Copilot.*` 的导入优先命中用户数据目录的转译层，Copilot 插件的源码级变更因此在**源码运行与打包版**上行为一致；该钩子与应用入口（`main.py`）同列受保护清单，插件不得拆除。

### 1.9 系统工具

- **应用扫描**：注册表 + UWP 清单，列出已安装软件。
- **迁移**：junction/move 迁移到目标盘，更新注册表/快捷方式。
- **内存优化**：终止可安全退出的后台进程，compact working sets。
- **卸载**：优先调用卸载程序，再清理数据目录 / 注册表 / 快捷方式。
- **壁纸适配**：按屏幕分辨率自适应背景图（`set_app_background`）。

### 1.10 办公文档

- **读取**：`src/zhuzhu_Copilot/office/docx_reader.py` / `pptx_reader.py` / `xlsx_reader.py` / `pdf_reader.py`，统一入口 `read_document`，输出结构化 Markdown（保留表格、标题层级、图片占位、动画条目）。
- **生成**：`docx_builder`（封面/目录/表格/动画 XML）、`pptx_builder`（真实 XML 动画序列）、`xlsx_builder`（多 sheet、图表、条件格式）。
- **编辑**：`edit_docx` / `edit_pptx` / `edit_xlsx`，逐条 ops 执行，单条失败不中断。
- **美化**：`beautify_*` 一键统一字体/颜色/行距/对齐。
- **保真预览**：渲染为自包含 HTML，调用 `preview_open` 展示。

### 1.11 媒体

- **音乐播放**：`pygame.mixer`，支持 MP3/WAV，状态持久化（`music_state.json`）。
- **歌词引擎**：LRC 解析 + 逐字时间戳插值，桌面歌词窗口置顶。
- **TTS**：阿里云 DashScope，支持声音复刻（`tts_create_voice` / `tts_speak`）。

### 1.12 界面

- **Agent 面板**：对话气泡、工具调用日志、上下文占用、记忆管理。
- **事件流**：流式渲染 LLM 输出，支持 Markdown 高亮。
- **流式自由滚动**：Agent 执行任务时用户可随意用鼠标滚轮滑动历史会话，不再强制回底；输入框下方居中悬浮圆形一键返回底部按钮（仅当内容可滚动且不在底部时显示）。
- **后台任务面板**：后台执行的命令/子 Agent 以任务卡片形式展示，含状态灯（运行中/完成/失败）、可展开输出、取消按钮；启动时提示「已在后台执行，你可以继续发送其他消息」，完成时自动通知。
- **Copilot 浮层**：半透明覆盖层，显示当前任务状态。
- **桌宠**：透明 GIF 动画，右键菜单快捷操作。
- **内置浏览器**：WebView 多标签页，与系统浏览器隔离。
- **全屏放映**：HTML 预览全屏模式。
- **本地 Web 服务**：`127.0.0.1:8765`，提供预览/调试接口。
- **多语言**：简体中文 / English 双语界面，安装时选择，运行中可在设置切换；English 模式无中文硬编码残留。

### 1.13 自动更新

每 30 秒轮询更新服务器 `/api/update/check`，首次启动立即检查。新版本下载 → 校验签名 → 替换 → 重启。旧版数据自动迁移。

- **下载 URL 加密**：更新接口不再暴露明文版本 ID（如 `/api/update/24`），改为 HMAC-SHA256 签名的临时 token URL（`/api/download/{token}`），token 内含 version_id + 过期时间（1 小时），篡改或过期返回 404，防止枚举与爬取。

### 1.14 用户反馈

- 客户端「关于」页与官网均提供反馈入口，提交后同步至更新服务器与管理后台。
- **IP 限流**：每个 IP 每天最多提交 3 次反馈，以服务器端时间为准，超限返回 429。
- 管理后台可查看反馈列表、回复、标记已解决；反馈内容含 IP、时间、内容、联系方式、状态、回复。

### 1.15 账号系统（登录 / 积分 / 会员）

后端 `auth-server/`（FastAPI + MySQL + Redis），已部署至 `https://chentian.dpdns.org/authapp/`。

- **服务端地址固定**：客户端统一使用内置常量 `DEFAULT_AUTH_SERVER`
  （`https://chentian.dpdns.org/authapp`），**已取消自定义服务端地址**功能；
  历史遗留的 `QSettings(auth_server_url)` 在启动时自动清理。
- **登录方式**：桌面端点击「登录」→ 系统浏览器打开登录页 → 登录/注册成功 → 回调本地
  `127.0.0.1:18765/callback` 传 token（失败时页面提供手动复制 Token 兜底）。
- **登录 URL 格式**：
  `{server}/static/login/index.html?platform=zhuzhu-copilot&state=<64位hex>&version=<APP_VERSION>&callback_port=18765`
- **登录握手 state（防 login CSRF / 重放）**：客户端登录前调 `POST /api/auth/state` 申请一次性
  state（Redis 存 600s、单次消费），拼进登录页 URL 并在登录/注册体中回传；服务端校验后
  随回调原样返回，客户端用 `secrets.compare_digest` 做常量时间比对，不一致即拒绝 token。
  生产已开启 `REQUIRE_LOGIN_STATE=true`（无 state 的登录/注册直接 400）。
- **登录态健壮性**：JWT 携带 jti，服务端 Redis 会话 + 滑动续期；客户端 30 分钟巡检，token
  临近过期（<6h）自动 `/api/auth/refresh` 换新并重连 WS；积分上报遇 401 自动续期重试一次，
  续期失败清凭证并弹窗引导重新登录。登出/被禁用/被删除立即撤销服务端会话。
- **实时推送**：WebSocket `wss://chentian.dpdns.org/authapp/ws?token=...`，30s 心跳、指数退避
  自动重连；推送积分变动、强制下线、订单到账。
- **积分与会员**：内置模型（agnes）需登录后使用，按 token 实时扣积分（每 1000 token ≈ 30 积分）；
  积分耗尽立即截断输出并追加「您的积分不足，请接入其他AI服务」。
- **安全加固**：bcrypt cost=12（每次独立随机盐，盐值随哈希串存储，不单独保存）；JWT HS256
  ≥32 字符强随机密钥（生产 `REQUIRE_STRONG_JWT_SECRET=true` 拒绝弱密钥启动）；每 IP 限注册
  一个账号；登录失败账号级 + IP 级双向锁定；CORS 收敛为具体域名。

---

## 二、目录结构

```
zhuzhu Copilot/
├── src/
│   ├── main.py                          # 入口：迁移 → 崩溃钩子 → **覆盖层导入钩子安装** → QApplication → 启动动画 → AI 面板
│   ├── _copilot_overlay_hook.py         # 源码覆盖层导入钩子（Copilot 插件源码级定制的地基，受保护）
│   └── zhuzhu_Copilot/
│       ├── copilot_plugins_examples/    # Copilot 插件开发指南 + 九个示例（改主题/自定义工具/设置页/应用扩展/语法高亮/提示词/评分/性能优化/代码地图）
│       ├── core/
│       │   ├── agent_engine.py          # Agent 核心循环（观察→调用→验证）
│       │   ├── agent_background.py      # 后台并发任务管理器（命令/子Agent/跨工作流）
│       │   ├── agent_llm.py             # LLM 接入（SSE / 图像输入 / 重试 / 256k上下文）
│       │   ├── agent_http.py            # HTTP 连接复用
│       │   ├── agent_tools.py           # 工具注册与执行
│       │   ├── agent_agents.py          # 人设与 Agent 切换
│       │   ├── agent_deps.py            # 依赖注入
│       │   ├── agent_edit.py            # 文件写入（路径加锁）
│       │   ├── agent_json.py            # 参数解析
│       │   ├── sandbox.py               # 命令沙箱
│       │   ├── security_engine.py       # 安全巡检
│       │   ├── yara_engine.py           # YARA 规则
│       │   ├── input_guard.py           # 输入守卫（低级钩子）
│       │   ├── browser_control.py       # 浏览器自动化
│       │   ├── screen_control.py        # 屏幕操控
│       │   ├── skill_manager.py         # 技能加载
│       │   ├── agent_plugins.py         # Agent 插件系统（mcp/skill/combined/web 四型，不涉及应用源码）
│       │   ├── copilot_plugins.py       # Copilot 插件系统（应用级独立体系：源码级定制 + 页面/服务/托盘/快捷键扩展）
│       │   ├── plugin_guard.py          # 插件校验系统（内置唯一不可替换，危险强制删除）
│       │   ├── plugin_source.py         # 源码变更引擎（仅 Copilot 插件：source_ops 写入转译层 / 自动回滚）
│       │   ├── hot_reload.py            # 转译层热加载（启用/停用后重载受影响模块）
│       │   ├── workflow_manager.py      # 工作流管理
│       │   ├── sub_agent_manager.py     # 子 Agent 管理
│       │   ├── mcp_registry.py          # MCP 注册
│       │   ├── agent_bus.py             # 团队消息总线
│       │   ├── wechat_bridge.py         # 微信 ClawBot 桥接
│       │   └── ...
│       ├── ui/
│       │   ├── agent_panel.py           # Agent 主面板
│       │   ├── copilot_overlay.py       # 浮层
│       │   ├── pet_widget.py            # 桌宠
│       │   └── ...
│       ├── office/
│       │   ├── docx_builder.py
│       │   ├── pptx_builder.py
│       │   ├── xlsx_builder.py
│       │   └── ...
│       ├── skills/                      # 内置 23 个技能
│       └── config/                      # 安全规则、YARA 规则
├── update-server/                       # Spring Boot 更新服务器
├── installer/                           # Inno Setup 安装脚本
├── scripts/                             # 一次性脚本（以 _ 开头）与运维工具
│   └── verify_overlay.py                # 转译层自检：打包态模拟 + 安装目录体检（第八章 8.5）
├── tests/                               # pytest 单元测试
├── build/                               # 构建产物（.gitignore）
├── dist/                                # 发布包（.gitignore）
├── build_release.ps1                    # 构建脚本
└── README.md
```

---

## 三、环境搭建

```powershell
# 1. 克隆仓库
git clone https://github.com/Chentianzhuzhu/zhuzhu_copilot.git
cd zhuzhu_copilot

# 2. 安装依赖（Python 3.13）
pip install -r src/requirements.txt

# 3. 运行
python src/main.py
```

首次运行自动创建 `%USERPROFILE%\.zhuzhu_Copilot\` 数据目录，迁移旧版 `WinAppMigrator` 数据。**仅支持 Windows**。

---

## 四、关键依赖

| 依赖 | 版本 | 用途 |
|------|------|------|
| PyQt6 | >=6.7 | 桌面 GUI 框架 |
| PyQt6-WebEngine | >=6.7 | 内置浏览器 |
| python-docx | - | Word 文档读写 |
| openpyxl | - | Excel 文档读写 |
| python-pptx | - | PPT 文档读写 |
| pypdf | >=4.0 | PDF 读取 |
| pygame | >=2.5 | 音乐播放 / TTS 回放 |
| pywin32 | >=306 | Windows API |
| yara-python | >=4.5 | 恶意特征匹配 |
| lxml | - | XML 解析（office 模块直接用） |
| requests | - | HTTP 请求 |
| flask | - | 本地 Web 服务 |

构建期可选：Cython（`.pyd` 加固）、PyInstaller（打包）。

---

## 五、构建与打包

```powershell
# 完整打包 + 签名
.\build_release.ps1

# 指定版本号（同步三处版本号并校验）
.\build_release.ps1 -Version 5.1.2

# 只体检环境，不构建
.\build_release.ps1 -DryRun

# 不签名（调试用）
.\build_release.ps1 -NoSign
```

- **版本号单一来源**：`update_check.py` 的 `APP_VERSION`。
- **PyInstaller spec**：实时生成，唯一事实来源 `build/zhuzhu_Copilot.spec`。
- **签名**：查找 Subject 含 `zhutianliang` 的证书，私钥不留存 `.pfx`。
- **卸载器签名必须在编译期完成**，否则卸载时会转读不存在的 `unins000.msg`。

---

## 六、部署

### 6.1 客户端分发

- GitHub Releases 直链 + 官网 / 更新服务器双入口。
- 推送到 Release：`scripts/publish_release.py`（流式上传，幂等）。
- 更新服务器地址优先级：QSettings → 环境变量 `UPDATE_SERVER_URL` → 内置常量。

### 6.2 更新服务器（Spring Boot 3）

- 技术栈：Spring Boot 3 + MySQL 8 + Redis 6 + nginx。
- 一键部署：`python update-server/tools/deploy.py`。
- 公开接口 `/api/site` 返回站点文案、最新版本、时间戳。
- **用户反馈 API**：`POST /api/feedback` 公开提交（每 IP 每天 3 次限流）；`GET /api/admin/feedbacks` 管理端查看；`POST /api/admin/feedback/{id}/reply` 回复。
- **下载 URL 加密**：`/api/download/{token}`，HMAC-SHA256 签名 + 1 小时过期，不再暴露明文版本 ID。
- **官网视觉自定义**：管理后台「视觉样式」配置页支持主题色、背景图、Logo、标题、副标题、页脚、字体、圆角、导航样式、社交链接等 15 项配置，保存后官网 `:root` CSS 变量动态注入即时生效。
- **官网美化**：CSS 变量驱动全站配色，Hero 区动态渐变光斑动画，卡片 hover 上浮，毛玻璃导航栏，滚动揭示动画，countUp 数字动画，完整响应式。
- nginx 反代，后台仅 SSH 隧道访问。

### 6.3 部署脚本

```powershell
python update-server/tools/deploy.py --dry-run          # 只列出将上传的文件
python update-server/tools/deploy.py --verify-only      # 只做健康检查
python update-server/tools/deploy.py --skip-build       # 只上传（排障用）
python update-server/tools/deploy.py --skip-restart     # 不重启服务
```

脚本支持 `--host` / `--user` / `--remote-dir` / `--service` / `--env-file` / `--base-url`。

**nginx 配置要点**（`update-server/deploy/nginx.conf`）：
- 80 → 301 跳 HTTPS
- `/admin` 对外返回 404（管理后台仅 SSH 隧道访问）
- 旧静态官网 `/website/` 已 301 归并到根路径 `/`
- `client_max_body_size 600m`
- 安装包下载路径关闭代理缓冲直接透传
- `/uploads/` 反代上传资源

**健康检查覆盖端点**：`/`、`/gallery`、`/download`、`/faq`、`/about`、`/sitemap`、`/robots.txt`、`/sitemap.xml`、`/site.webmanifest`、`/favicon.svg`、`/css/site.css`、`/js/site.js`、`/api/site`、`/api/version/latest`、`/uploads/`（期望 404），以及后台 `/admin`、`/admin/`、`/admin/index.html`、`/admin/admin.css`、`/admin/admin.js`；并逐个真实请求页面引用的站内资源。

---

## 七、持续集成

`.github/workflows/ci.yml`（push 到 `main` 与 pull request 触发，`PYTHONIOENCODING=utf-8`）三个 job：

1. **python-check**（windows-latest，Python 3.13）：安装 `src/requirements.txt` 与 `ruff`，执行 `python -m compileall -q src mcp_servers` 语法编译检查，并运行 `ruff check src mcp_servers --statistics`（`|| true`，不阻断）。
2. **security-engine-tests**（windows-latest，Python 3.13）：安装依赖与 `pytest`，执行 `python -m pytest tests -v`（安全引擎 / 网络防御 / Git 数据层 / 静态官网 SEO 等单元测试）。
3. **update-server**（ubuntu-latest，Java 21）：在 `update-server/` 下执行 `mvn -B -q test`（内容合并 / SEO 推导 / 上传安全 / 模板渲染），随后以 Python 3.13 执行 `python -m unittest discover -s tests -v` 做官网静态回归校验（模板配平 / 元素 id 一致性 / 四色规范 / SEO 资源 / 编码）。

---

## 八、插件开发指南（手把手）

这一章假设你**从没写过插件**。跟着做，30 分钟内能做出一个能装、能跑、能回滚的插件；
每个小节都给出可直接复制的文件内容与验证方法。

### 8.0 三十秒决策：该做哪套插件

| 你想做的事 | 用哪套 | 关键文件 |
| --- | --- | --- |
| 给 AI 加**工具/技能**（不碰界面） | Agent 插件 | `plugin.json` + `server.py` / `SKILL.md` |
| 给 AI 加**可视化网页**（浏览器里操作） | Agent 插件（`kind=web`） | `server.py` + `plugin.json` |
| 改**主题/界面/语言**、改任何模块的行为 | **Copilot 插件**（源码级） | `source_ops.json` |
| 加一个**浮层面板页**、托盘项、快捷键、后台服务 | **Copilot 插件**（应用扩展） | `copilot.json` |
| 既要新页面，又要新工具，还要改既有行为 | **Copilot 插件**（综合） | 三者都要 |

判断口诀：**「AI 能不能用它」→ Agent 插件；「用户能不能看到界面变化」→ Copilot 插件。**
Copilot 插件是超集：它可以带 `agent/` 子包同时提供技能与 MCP 工具。

### 8.1 第一个插件：五分钟（纯运行时，不碰源码）

最安全的插件不写一行源码变更，只注册一个页面。三步：

**第 1 步**：建目录 `my-first-plugin/`，写 `plugin.json`：

```json
{
  "name": "my-first-plugin",
  "kind": "copilot",
  "author": "你的名字",
  "description": "我的第一个 Copilot 插件：加一个显示问候语的页面",
  "enabled": true,
  "created_at": "2026-10-25 10:00:00",
  "updated_at": "2026-10-25 10:00:00",
  "icon": ""
}
```

**第 2 步**：写 `pages/hello_page.py`（页面就是普通 `QWidget`，父控件由应用传入）：

```python
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget


class HelloPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.addWidget(QLabel("你好，这是插件加的页面。"))
        lay.addStretch(1)
```

**第 3 步**：写 `copilot.json` 把它挂到浮层页签上：

```json
{
  "pages": [
    {"id": "hello-page", "title": "问候", "module": "pages/hello_page.py",
     "widget": "HelloPage"}
  ]
}
```

打包（zip 根目录必须直接是 `plugin.json`）：

```powershell
python -c "import zipfile,pathlib;z=zipfile.ZipFile('my-first-plugin.zip','w');[z.write(p,pathlib.Path(p).relative_to('my-first-plugin')) for p in pathlib.Path('my-first-plugin').rglob('*') if p.is_file()];z.close()"
```

导入：设置 → Copilot 插件 → 导入插件包（或把 zip 拖进对话输入框）。**启用 → 重启应用**，
就能在浮层里看到「问候」页签。纯运行时插件不改任何源码，所以停用即彻底消失。

> 为什么纯运行时也要重启：**页面 / 托盘 / 快捷键是在应用启动时登记的**
> （`MainWindow._load_copilot_extensions`），运行期启用只重启 `services`。
> 记住这条，能省掉一半「启用了却没反应」的困惑。

### 8.2 第二个插件：十分钟（源码级，三种写法）

源码级插件用 `source_ops.json` 描述「改哪个文件、怎么改」。三种写法，按风险从低到高：

#### 写法一：新建模块（`create_file`）——优先用这个

```json
[
  {"op": "create_file",
   "path": "zhuzhu_Copilot/core/my_tool.py",
   "content": "\"\"\"我的工具模块\"\"\"\n\n\ndef run(args: dict) -> dict:\n    return {\"text\": \"你好 \" + str((args or {}).get(\"name\") or \"世界\"), \"images\": []}\n"}
]
```

要点：`path` 相对**源码根 `src/`**；目标文件已存在会被拒绝（要整体覆盖用 `replace_file`）；
新模块建好后可以直接被 `import`。

#### 写法二：插入到现有文件（`insert_after` / `insert_at` / `append`）

**不改动任何已有代码行**，风险最低。典型用途：登记工具 schema、接线分发器、在模块末尾
追加包装函数。

```json
[
  {"op": "insert_after", "path": "zhuzhu_Copilot/core/agent_tools.py",
   "anchor": "TOOLS = [",
   "content": "    {\n        \"type\": \"function\",\n        \"function\": {\n            \"name\": \"my_tool\",\n            \"description\": \"向指定名字问好\",\n            \"parameters\": {\"type\": \"object\", \"properties\": {\"name\": {\"type\": \"string\"}}, \"required\": []},\n        },\n    },"},
  {"op": "insert_after", "path": "zhuzhu_Copilot/core/agent_tools.py",
   "anchor": "            return _revert_copilot_plugin(str(args.get(\"name\", \"\")))",
   "content": "        if name == \"my_tool\":\n            from zhuzhu_Copilot.core import my_tool\n            return my_tool.run(args)"}
]
```

四个「加代码」类 op 的差别（前三个只加不改，`replace_lines` 是按行替换）：

| op | 落点 | 必需字段 |
| --- | --- | --- |
| `insert_after` | 锚点行（或第 `line` 行）**之后**，自动补一个空行 | `path` `content` + `anchor` 或 `line` |
| `insert_at` | 第 `line` 行**之前**（`line = 行数+1` 等于追加到末尾） | `path` `line` `content` |
| `append` | 文件末尾；给 `after` 则插到该锚点行之后 | `path` `content`（+ 可选 `after`） |
| `replace_lines` | 替换第 `start_line..end_line` 行（含边界） | `path` `start_line` `content` |

**包装既有函数而不改原实现**（最推荐的「改造」姿势）：在模块末尾追加一段，用同名变量
重绑定。例如给渲染函数加缓存（`perf-turbo` 用的就是这个思路的变体）：

```python
_orig_render = _render_text


def _render_text(raw: str) -> str:      # noqa: F811  插件包装
    try:
        return _orig_render(raw).replace("foo", "bar")
    except Exception:
        return _orig_render(raw)
```

因为 Python 在**调用时**才查模块全局名，模块级重绑定对所有调用方立即生效。

#### 写法三：替换既有实现（`replace_module` / `replace_file`）

只有必须改既有行为时才用。**替换后的代码必须完整可编译**（写入后逐文件 `py_compile`，
失败整体回滚）。

```json
[
  {"op": "replace_module", "path": "zhuzhu_Copilot/core/agent_skills.py",
   "start": "    return parts", "end": "    return parts",
   "content": "    from zhuzhu_Copilot.core.my_rules import RULES\n    if RULES not in parts:\n        parts.append(RULES)\n    return parts"}
]
```

`start` / `end` 是**行内文本锚点**（都要求唯一），`end` 省略则替换到文件末尾；
也可以用 `start_line` / `end_line` 按行号替换。

#### 定位方式二选一：文本锚点 vs 行号

| | 文本锚点（`anchor` / `start`） | 行号（`line` / `start_line`） |
| --- | --- | --- |
| 稳定性 | 对版本差异鲁棒，改动上方代码不影响 | 上方插一行就整体错位 |
| 要求 | 该串在文件里**真实存在且唯一** | 1 起、含边界，且必须准确 |
| 适合 | 大多数情况（推荐） | 你拿得到完整源码且要精确改某几行 |

**取锚点的正确姿势**：先 `read_file` 打开目标文件（或直接用编辑器看），复制**一整行**
有辨识度的代码（带缩进），并确认它只出现一次。锚点太短（如 `return`）必然「不唯一」。

同一文件有多条 op 时，**行号会随前一条插入而偏移** → 全程用锚点，或从后往前写行号。

#### 组合起来：一个真实插件的完整清单

`code-map` 插件的 `source_ops.json` 只有 3 条，却做了「新模块 + 登记工具 + 接线」三件事：

```json
[
  {"op": "create_file", "path": "zhuzhu_Copilot/core/code_map.py", "content": "…扫描内核…"},
  {"op": "insert_after", "path": "zhuzhu_Copilot/core/agent_tools.py",
   "anchor": "TOOLS = [", "content": "    # 工具 schema\n    { … },"},
  {"op": "insert_after", "path": "zhuzhu_Copilot/core/agent_tools.py",
   "anchor": "            return _revert_copilot_plugin(str(args.get(\"name\", \"\")))",
   "content": "        if name == \"code_map\":\n            from zhuzhu_Copilot.core import code_map\n            return code_map.tool_report(args)"}
]
```

> **别和已装插件抢同一锚点**。两个插件都往 `TOOLS = [` 后面插没问题（锚点行本身没被改动），
> 但如果两个插件都去 `replace_module` 同一行，后装的会基于前一个的结果再替换，容易互相覆盖。
> 动手前先看已装插件的 `source_ops.json`。

### 8.3 配图标（icon）

三件事，缺一就会「图标不显示」：

1. **文件放包里**：`my-plugin/icon.png`（与 `plugin.json` 同目录，**不能放子目录**）。
2. **plugin.json 写文件名**：`"icon": "icon.png"`——只写文件名，**不能带 `pages/` 这类目录前缀**，
   也不能以 `.` 开头（安全校验会直接判为非法并清空该字段）。
3. **格式在白名单内**：`.png` / `.jpg` / `.jpeg` / `.svg` / `.ico` / `.gif` / `.webp`。
   **推荐 PNG**：`exe` 安装版对 PNG 的支持最稳（SVG 依赖 Qt 的 svg 图像插件，虽然通常随包
   分发，但没必要冒这个险）。建议方形 128~256px，卡片按 44px 等比缩放显示。

自己生成一个（Pillow 最省事；`code-map` 的图标连 Pillow 都没用——脚本直接光栅化出
256×256 PNG，见 `tmp_code_map/make_icon.py` 的做法）：

```python
# 用 Pillow（若已装）
from PIL import Image, ImageDraw
img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle((6, 6, 250, 250), 52, fill=(30, 64, 175, 255))
d.rounded_rectangle((38, 84, 218, 200), 18, fill=(147, 197, 253, 255))
d.rounded_rectangle((62, 134, 94, 178), 8, fill=(15, 23, 42, 255))
img.save("icon.png")
```

**AI 生成的插件也能带图标**：`create_copilot_plugin` 支持模型返回 `icon_svg`（一段独立
SVG 源码），应用会校验后落盘为 `icon.svg` 并写进 `plugin.json`；带脚本/事件属性/外链
资源的 SVG 会被拒绝（图标只是静态图形，不需要这些）。

显示位置：设置 → Copilot 插件页的**卡片头像**（44px）、详情对话框、导出包。图标缺失时
显示占位图标（Copilot 插件为 🧊，Agent 插件为 🧩），不影响功能。

### 8.4 应用级扩展：`copilot.json` 全字段

```json
{
  "pages":     [{"id": "唯一id", "title": "页签标题",
                 "module": "pages/x.py", "widget": "XxxPage"}],
  "services":  [{"id": "唯一id", "title": "服务说明",
                 "module": "service/x.py", "entry": "start", "stop": "stop"}],
  "tray":      [{"id": "唯一id", "title": "托盘菜单文字",
                 "module": "pages/x.py", "action": "quick_do"}],
  "shortcuts": [{"id": "唯一id", "title": "快捷键说明", "hotkey": "ctrl+alt+m",
                 "module": "pages/x.py", "action": "on_hotkey"}]
}
```

`pages` 条目额外支持四个可选项（要独立窗口时才用）：

| 字段 | 取值 | 说明 |
| --- | --- | --- |
| `window` | `tab`（默认）/ `glass` | `glass` = 独立无框液体玻璃窗口，不占浮层页签 |
| `auto_show` | `true`（默认）/ `false` | 是否随应用启动直接浮现（常驻） |
| `width` / `height` | 240~4000 | 窗口尺寸（默认 760×520） |
| `position` | `[x, y]` | 固定位置；不给则屏幕居中，多个窗口自动错位 |

约定（违反会静默失效，务必逐条核对）：

- `id` **全局唯一**（跨插件重复会被拒绝），只能用字母/数字/`_`/`-`，≤50 字符；
- `module` 是**插件包内相对路径**（随包分发，不经转译层），文件必须真实存在；
- `pages.widget` 是模块里的 `QWidget` 子类名，实例化时父控件传入浮层容器；
- `services.entry` 在 **GUI 线程**调用，可返回句柄（含 `stop`/`close` 方法）；`stop` 字段
  指向模块里的清理函数，停用插件或退出应用时调用。**服务里别做阻塞操作**，长任务自己开线程，
  异常必须自己吞掉——一个插件不能把应用启动搞挂；
- `shortcuts.hotkey` 形如 `ctrl+alt+m`（只允许字母/数字/`+`，≤40 字符）。

`config.json`（可选）：用户可在「详情/配置」里编辑的键值（只支持文本/数字/布尔标量），
键名限 `字母数字 _ - .`（≤64 字符），且不能占用 `PATH`/`HOME` 等系统变量名。
MCP 型插件会把它以**大写环境变量**注入 server 子进程；页面/服务模块自己读的话，
直接按 `Path(__file__).parent.parent / "config.json"` 定位最省事（`code-map` 就是这么做的）。

### 8.5 生命周期、转译层与 **exe 环境验证**

```
导入 zip ──▶ 插件校验系统（静态危险规则 + LLM 源码评审，危险即删除并提示）
        ──▶ 落盘 ~/.zhuzhu_Copilot/copilot_plugins/<name>/
启用    ──▶ 逐文件备份 backups/ ──▶ 逐条写入转译层 ──▶ 全部 py_compile（失败整体回滚）
        ──▶ 写 applied.json ──▶ 热加载受影响模块 ──▶ 启动 copilot.json 声明的服务
停用    ──▶ 停服务 ──▶ 按 applied.json 回滚（新建的删、改过的还原）──▶ 热加载回落原版
```

**转译层**是这套机制的核心：变更**不写应用源码文件**，而是写进
`~/.zhuzhu_Copilot/source_overlay/zhuzhu_Copilot/<相对路径>`，由 `src/_copilot_overlay_hook.py`
（`main.py` 在导入包之前安装）让 `zhuzhu_Copilot.*` 的导入**优先命中转译层**；覆盖层里没有的
模块自动回落原版。因此**源码运行与 exe 安装版行为一致**，回滚只需清理转译层。

| | 源码运行 | exe 安装版 |
| --- | --- | --- |
| 「原版内容」从哪来 | `src/zhuzhu_Copilot/`（真实源码） | 随包快照 `_copilot_src/zhuzhu_Copilot/` |
| 插件变更写到哪 | `~/.zhuzhu_Copilot/source_overlay/` | 同左（**不是**安装目录） |
| 生效时机 | 重启应用（`agent_panel` / `main_window` / `styles` 等进程级模块必须重启；其余模块热加载） | 同左 |

**「什么时候需要重启」这张表记牢，能省掉大量排错时间**：

| 插件构成 | 启用后 | 停用后 |
| --- | --- | --- |
| `copilot.json` 的页面 / 托盘 / 快捷键 | **热插拔**：立即挂上（页签/玻璃窗/菜单/快捷键当场出现） | **热插拔**：立即摘掉 |
| `copilot.json` 的 `services` | 立即启动 | 立即停止 |
| `source_ops` 改了 `agent_panel` / `main_window` / `styles` | 需重启（热加载跳过进程级模块） | 需重启 |
| `source_ops` 改了其它模块（`agent_tools` / 新建模块等） | 热加载即时生效 | 热加载即时回落 |

#### 在 exe 里实验「转译层是否生效」

**第 0 步（必做，否则一定失败）**：确认这个 exe 是**含覆盖层机制**的构建——
安装目录里必须有 `_copilot_src/zhuzhu_Copilot/`。早期构建（例如仓库里 2026-10-19 那版
`dist/`）**不含**该机制，源码级插件在它上面一定不生效，必须重新打包：

```powershell
.\build_release.ps1            # spec 的 datas 已包含源码快照，重建即带上
```

**第 1 步**：跑自检脚本（不依赖任何插件，随时可跑）：

```powershell
python scripts/verify_overlay.py                 # 打包态模拟 + 体检 dist/zhuzhu Copilot
python scripts/verify_overlay.py --simulate      # 只做打包态模拟（隔离临时目录，不碰你的数据）
python scripts/verify_overlay.py --inspect "D:\Program Files\zhuzhu Copilot"   # 只体检某个安装目录
```

`--simulate` 会在临时目录里造一个「假安装目录 + 假用户目录」，把 `sys.frozen` 置为真，
然后验证三件事：① 覆盖层有同名模块时导入到覆盖层版本；② 覆盖层没有时回落 `_copilot_src`；
③ 没有转译层时 `install()` 返回 `False`、行为与未装插件完全一致。这三条通过，说明
exe 里的代码路径没问题。

**第 2 步**：在 exe 里装一个**看得见**的插件（推荐 `code-map`：多一个页签 + 托盘项 + 工具）：

1. 打开 exe → 设置 → Copilot 插件 → 导入 `code-map.zip` → **启用** → 重启 exe；
2. 看三个信号：
   - Copilot 浮层多出「代码地图」页签（**应用扩展生效**）；
   - 让 AI 调一次 `code_map` 工具（**源码级插入生效**：工具被登记进 TOOLS）；
   - 设置 → Copilot 插件页顶部「生效位置」显示转译层路径与已加载模块数，卡片显示
     「已写入转译层」（**机制自报状态**）。
3. 反证：**停用**插件 → 重启 → 页签消失、`code_map` 工具不可用、转译层模块数归零。

任何一步不符，按下表排查。

### 8.6 排错表（按症状查）

| 症状 | 原因 | 处理 |
| --- | --- | --- |
| 启用失败：**找不到锚点** | 目标文件版本与你写清单时不一致 | `read_file` 重新取锚点，或改用行号 |
| 启用失败：**锚点不唯一** | 锚点串在文件里出现多次 | 加长锚点，带缩进与更多上下文 |
| 启用失败：**编译检查未通过** | 插入/替换后的代码不完整、缩进错、括号没闭合 | 看报错里的行号；`replace_module` 的 `end` 是否漏了 |
| 启用失败：**文件已存在** | `create_file` 撞了已有文件 | 换路径，或改用 `replace_file` |
| 插件在，**页面/工具没出现** | ① `source_ops` 落在热加载跳过的模块（`agent_panel` / `main_window` / `styles`）；② 页面条目带 `window: glass` 时窗口可能被别处挡住；③ 忘了点启用 | ① **重启应用**；② 看任务栏/托盘；③ 看卡片状态 |
| 插件在，**什么都没变** | ① 忘了启用；② 转译层没写入；③ exe 构建不含覆盖层机制；④ 页面被旧页签/窗口遮住 | 看卡片状态与「生效位置」；跑 `scripts/verify_overlay.py`；重启一次再判 |
| 卡片显示**服务加载失败** | `services.entry` 抛异常或函数名不对 | 看 `copilot.json` 的 `entry` 名字；服务里包 try/except |
| 停用后**页签还在** | 该页签不是插件登记的（同名/手工加），或登记记录被改过 | 重启一次；仍在此就检查是否别的插件也注册了同一个 `id` |
| **图标不显示** | `icon` 写了目录前缀 / 文件不在包里 / 扩展名不在白名单 | 改成 `"icon": "icon.png"`，文件放包根 |
| 插件**被自动删除** | 校验系统判定危险代码 | 查 `copilot_plugins/.guard_log.jsonl`，改掉危险写法（`eval`/`exec`/`os.system`/`shutil.rmtree` 等） |
| 改了源码**却没生效** | 转译层里有该文件的旧快照，遮住了源码改动 | 先**停用**插件（回滚转译层）再改源码，或删掉转译层对应文件 |
| 两个插件**互相打架** | 都改了同一行/同一锚点 | 停用其一；改清单换一处特征行 |

### 8.7 让 AI 帮你写插件

应用内置了三条「AI 写插件」的链路，都是**生成 → 校验 → 写入**闭环，不用手写 JSON：

| 你说 | 触发 | 产物 |
| --- | --- | --- |
| 「帮我做个插件，给 AI 加个 XX 工具」 | `create_plugin` + `plugin-create` 技能 | Agent 插件（MCP server + SKILL.md，web 型带浏览器界面） |
| 「帮我做个插件，在浮层加一个 XX 页面 / 改一下界面」 | `create_copilot_plugin` | Copilot 插件（source_ops + copilot.json） |
| 「我要深度自定义 XX」 | `deep-customize` 技能 | 先盘点可自定义维度，再选最小落地通道 |

**对话内调用技能**：用户点名「用 XX 技能 / 按 XX 规范来」时，Agent 调 `use_skill(name=...)` 读取该技能规范正文并纳入本任务（`name` 留空返回可用技能清单）；生成插件前也可主动 `use_skill("plugin-create")` 取到完整生成规范（含 web 型三件套质量约束、冒烟自检流程、何时改用 Copilot 插件）。

**直接描述即可生成**：「生成一个 copilot 插件，在设置页加一个每日一句页面」这类自然语言会命中 cordis 任务组与 `plugin-create` 技能关键词，Agent 按规范调用 `create_plugin` / `create_copilot_plugin` 生成，`apply_copilot_plugin` 应用后由 `reload_extensions` + `hot_reload` **即时热插拔**（技能/MCP/源码覆盖层当轮生效，无需重启）；生成或应用失败可用 `revert_copilot_plugin` 一键回滚。

提示词模板（越具体越准）：

```
帮我做一个 Copilot 插件，名字叫 xxx：
1) 新建模块 zhuzhu_Copilot/core/xxx.py，实现 <具体功能>；
2) 在 agent_tools.py 登记工具 xxx（参数 a/b）并接线；
3) copilot.json 加一个页面「xxx」，显示 <什么内容>，配 icon.png。
```

生成后**一定要审**：设置 → Copilot 插件 → 该插件「详情/配置」→「源码变更」页签逐条看
`source_ops`（哪条 op、改哪个文件、内容是什么），「应用扩展」页签看注册了哪些页面/服务。
不满意就停用（自动回滚）再重生成，不会在源码里留下痕迹。

### 8.8 液体玻璃窗口（GPU 实时折射 / 反射 / 边缘高光）

`src/zhuzhu_Copilot/ui/glass_window.py` 提供无框自制窗口 `GlassWindow`，插件页面加
`"window": "glass"` 即用它托管（应用启动直接常驻浮现，可拖拽、可缩放、可关闭）。
玻璃本体**不是贴图、不是预渲染滤镜**，是每帧在 GPU 上逐像素算出来的：

| 效果 | 怎么算的 |
| --- | --- |
| 折射 | 窗口形状的 SDF（超椭圆 squircle）求法线，按「边缘厚、中心薄」的透镜模型偏移采样背景贴图——边缘处桌面被真实弯折 |
| 色散 | R/G/B 三个通道用不同折射率（不同偏移量 + 不同模糊半径）分别采样，形成彩色边缘 |
| 反射 / 镜面高光 | 法线抬成 3D 法线做 Blinn-Phong，高光条随光角移动 |
| 边缘高光 | 菲涅尔（离边缘越近越亮）× N·L 的物理亮边 + 内侧一圈极薄暗线做厚度感 |
| 液态感的角度 | 法线叠加随时间缓慢呼吸的正弦扰动，高光与折射随之游走 |

关键实现（都是踩过的坑，改之前先看）：

- **PyQt6 没有 `QOpenGLFunctions`**，Qt6 的 `ShaderEffect` 又要求预编译 `.qsb`（本仓库
  不带 qsb 工具链），所以着色器用 **ctypes 直连 opengl32 / wglGetProcAddress**，
  GLSL 1.20 + 固定管线提交四边形——兼容性最好，exe 里同样可用；
- **背景贴图必须比窗口大一圈**（`pad`，默认 22px）。否则边缘折射采样到的是窗口内部
  内容，边缘会发脏、出色斑；
- **单四边形绘制时关闭混合**：开了 `SRC_ALPHA` 混合会让透明 FBO 变成预乘结果，读回时
  二次乘算 → 亮部泛黄绿；输出前必须 `clamp(col)`；
- **抓屏前排除自己**：`SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)` 把玻璃窗口
  排除在截屏之外，否则背景贴图会拍到玻璃自己，反馈糊成一团；
- **磨砂放 CPU**（抓图后缩小再放大），着色器只做 3×3 小半径精修——比在着色器里堆几十
  个 taps 便宜得多；
- 默认 60fps，窗口隐藏时停定时器（零开销）。

排障开关（环境变量）：

| 变量 | 作用 |
| --- | --- |
| `GLASS_MODE=painter` | 强制降级为 QPainter 半透明圆角面板（驱动异常/远程桌面时用） |
| `GLASS_KEEP_CAPTURABLE=1` | 允许本窗口被截屏（自测/演示用；默认排除，否则背景会拍到自己） |

窗口自带 `diagnostics()`：`gpu`（GPU 是否就绪）、`surface_error`（着色器失败原因）、
`grab`（抓屏是否成功）、`excluded_from_capture`。**远程桌面 / 虚拟机 / 无 GL 环境会自动
降级**（功能不受影响，只是没有 GPU 光学效果）。

验收：`GLASS_TEST_GL=1 python -m pytest tests/test_glass_shader.py` 会真编译着色器并
逐像素断言——形状外全透明、边缘比中心亮、改变折射强度后边缘带像素发生变化（证明折射
真的偏移了采样）。无 GL 环境下该用例自动 skip。

### 8.9 一个「综合插件」该长什么样

以 `code-map` 为范本（`src/zhuzhu_Copilot/copilot_plugins_examples/code-map/`）；
仓库里的 `perf-turbo`（性能看板）与 `code-map`（代码地图）现在都用 `"window": "glass"`
以独立液体玻璃窗口常驻浮现，可作为观感参考：

```
code-map/
├── plugin.json        name / author / updated_at / icon=icon.png / description
├── source_ops.json    3 条 op：create_file 内核 + 2×insert_after 登记与接线工具
├── copilot.json       页面 + 服务 + 托盘 + 快捷键（四项齐全）
├── config.json        refresh_minutes / max_depth（用户可改）
├── icon.png           256×256 方形图标
├── source/code_map.py 内核的可读副本（与 source_ops 内容一致，便于 review）
├── pages/             看板页（QWidget，含后台扫描线程）
├── service/           启动预热 + 定时刷新缓存
├── agent/SKILL.md     教 AI 什么时候用 code_map 工具
└── README.md          这个插件改了什么、怎么验证、怎么回滚
```

**源码副本（`source/`）不是必需的**，但它让 review 变容易；注意 `source_ops.json` 里
`content` 才是真正执行的内容，两者要保持一致（改完副本记得同步，或用脚本生成清单）。

---

## 九、开发约定

- 提交信息格式：`type(scope): 描述`（feat / fix / style / perf / refactor / chore）。
- 临时诊断脚本请加 `_` 前缀（`.gitignore` 已排除 `/_*` 与 `scripts/_*`），不要提交。
- 构建产物不入库：`build/`（中间产物、`runtime_bundle/`、`redist/`、`workflows_seed/` 中的个人化工作流）、`dist/`、`src/build/`、`src/tts_output/`、`*.spec`（除 `build/zhuzhu_Copilot.spec` 白名单外）、`*.docx` 等（以 `.gitignore` 为准）。
- 代码签名私钥 `build/certs/*.pfx` 与本地个人数据（如 `memory.md`）严禁提交。

---

> **核心循环**：`agent_engine.py` 中的「观察 → 调用工具 → 验证结果」循环，LLM 逐 token 流式回调 UI；每个工具执行前可回调确认，沙箱判定为危险的工具即使被批准也由 `agent_tools` 硬拒绝。

> **安全守卫**：`input_guard.py` 全局低级鼠标/键盘钩子，AI 操控期间检测到用户手动操作即回调通知（AI 自身模拟输入经抑制避免误判）。
