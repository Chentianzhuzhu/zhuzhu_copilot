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
- **模型接入**：`src/zhuzhu_Copilot/core/agent_llm.py`，OpenAI 兼容 `/v1/chat/completions`，支持 SSE 流式、`tools`/`tool_choice` 工具调用、图像输入（data URL / http(s)），发送前启发式估算 token、响应后累计上游 `usage`。失败按 429/5xx/网络自动重试（指数退避）。
- **HTTP 连接复用**：`src/zhuzhu_Copilot/core/agent_http.py`，按 (scheme, host, port) 复用 keep-alive 连接，配置了代理或关闭能力开关时回退 urllib。
- **上下文管理与压缩**：`src/zhuzhu_Copilot/core/agent_engine.py`。分层阈值触发摘要压缩 + 发送前硬裁剪兜底，带压缩冷却与「最近窗口保留预算」；超长工具返回文本（read_file / web_fetch / extract_text）自动压缩，LLM 不可用时回退启发式压缩。
- **Token 统计**：同一文件内维护全程累计用量 `tokens` 与最近一次请求的上游 `last_usage`，供上下文占用面板展示。
- **人设与自定义 Agent**：`src/zhuzhu_Copilot/core/agent_agents.py`。`@<agent>` 在会话级切换人格（系统提示词），可选绑定某个工作流以获得其工具/技能；`@_default` 切回内置默认人格。
- **组件装配与依赖注入**：`src/zhuzhu_Copilot/core/agent_deps.py`（自定义代码的 `requirements.txt` 自动补齐依赖）、`agent_edit.py`（同一文件写入按路径加锁）、`agent_json.py`（工具参数解析）。

### 1.2 工具集

工具注册与执行在 `src/zhuzhu_Copilot/core/agent_tools.py`（LLM function calling schema + 沙箱评估）。主要类别：

- **文件**：`read_file` / `write_file` / `edit_file` / `search_replace` / `insert_lines` / `undo_file` / `delete_file` / `list_directory` / `search_files` / `grep` / `search_code` / `find_app`（`agent_find.py`）。
- **命令与沙箱**：`run_command` / `check_command`；沙盒模块 `src/zhuzhu_Copilot/core/agent_sandbox.py` 把 Node.js / Python 便携运行时装到 `~/.zhuzhu_Copilot/runtime/`，`run_command` 透明地把沙盒 bin 前置到子进程 PATH，不写入系统 PATH。
- **查找**：`src/zhuzhu_Copilot/core/agent_find.py`（开始菜单快捷方式 + 注册表 App Paths 索引，带 1 小时缓存；用户目录并行遍历文件名模糊匹配）。
- **Git**：`src/zhuzhu_Copilot/core/agent_git.py` 只读查询分支/日志/差异（不提供写操作）；提交/推送等由 `git` 命令经沙箱白名单执行。
- **HTTP 与下载**：`web_fetch` / `web_search`；`fast_download`（`src/zhuzhu_Copilot/core/fast_download.py`）多线程分段、支持暂停/续传。
- **截图与屏幕操作**：`src/zhuzhu_Copilot/core/agent_screen.py` 截屏（QScreen.grabWindow，输出 PNG data URL 供视觉模型）、鼠标移动/点击/拖动/滚轮、键盘按键与 Unicode 文本输入（支持中文）。`src/zhuzhu_Copilot/core/agent_feedback.py` 在真实操作位置叠加点击/滑动动画（深蓝呼吸光圈，不拦截鼠标）。
- **浏览器自动化**：两条通路。
  - 内置 WebView 自动化：`src/zhuzhu_Copilot/core/agent_browser_web.py`，在应用内置的多标签 QWebEngineView 里执行 navigate/snapshot/click/type/eval/html/scroll/tabs，不干扰用户浏览器。
  - 独立外部浏览器：`src/zhuzhu_Copilot/core/agent_browser.py`，用独立 `user-data-dir` + 动态调试端口启动专用 Edge/Chrome，经 CDP 操控，与用户正在使用的浏览器完全隔离；对应工具 `browser_open/navigate/snapshot/click/type/scroll/eval/html/close/tabs/switch_tab`。
- **预览**：`src/zhuzhu_Copilot/core/agent_preview.py` 起一个仅监听回环地址的本地只读服务，把 AI 产出的页面/原型/图表的 HTML 送到用户自己的浏览器展示，并通过版本轮询自动刷新。
- **剪贴板**：`clipboard` 工具（读/写剪贴板，属需确认类）。
- **图像**：`generate_image`（Agnes Image）、`view_image`。

### 1.3 技能系统

技能的单一来源是 `SKILL.md`（市场标准 md 格式），加载逻辑在 `src/zhuzhu_Copilot/core/agent_skills.py`：配置目录 `~/.zhuzhu_Copilot/agent/`，首次运行自动生成示例；支持热改。随包分发的内置技能位于 `src/zhuzhu_Copilot/skills/`，共 23 个：

| 技能目录 | 用途（取自各自 SKILL.md frontmatter） |
| --- | --- |
| `brainstorming` | 头脑风暴：先理清需求，再制定计划并交用户审核 |
| `cmd-ops` | 命令执行与下载：run_command / check_command / fast_download |
| `code-review` | 系统化代码审查，检查逻辑、安全、边界条件和可维护性 |
| `complex-task` | 复杂任务：拆解为可独立验证的步骤清单，逐步执行并验证 |
| `create-cordis` | 引导创建/定制 AI Agent 工作流（Cordis 式） |
| `deep-customize` | 深度自定义总入口（工作流/技能/插件/UI/UX/MCP/按钮） |
| `doc-gen` | 办公三件套的生成、美化、读取与编辑（含随附深度提示词与示例） |
| `download-skill` | 在 GitHub 搜索 SKILL.md 技能并下载导入 |
| `evil-skill` | 技能加载/安全测试用的最小样本（frontmatter name 为 `evil`） |
| `feature-panel` | 用自然语言创建/扩展自定义功能面板（独立浮窗） |
| `file-ops` | 文件读写改删列与模糊查找 |
| `git-commit` | 按 Conventional Commits 提交、推送、拉取 |
| `image-gen` | 调用 Agnes Image 2.1 Flash 文生图/图生图 |
| `memory` | 长期记忆：save_memory / load_memory |
| `plugin-create` | 生成可运行插件（MCP server + SKILL.md + web HTTP UI 三件套） |
| `skill-create` | 根据自然语言生成 SKILL.md 技能并加载 |
| `skill-mgmt` | 技能创建/管理 |
| `sub-agent` | 子任务并行调度（dispatch_sub_agents 等） |
| `system-admin` | 系统管理：定位应用、系统信息、内存清理、卸载、迁移 |
| `systematic-debugging` | 系统化调试：复现、假设根因、逐一验证 |
| `test-driven-development` | 测试驱动开发：红-绿-重构 |
| `web-search` | 联网搜索与网页抓取定位详情 |
| `writing-plans` | 为多步骤任务制定执行计划交用户审核 |

`src/zhuzhu_Copilot/skills/doc-gen/` 另带 `references/`（word/ppt/excel/动画/调用示例说明）与 `assets/`（示例文件与图标）。

### 1.4 MCP 支持

`src/zhuzhu_Copilot/core/agent_mcp.py`：MCP 客户端，支持 **stdio**（子进程 + newline-delimited JSON-RPC，`tools/list` / `tools/call`）与 **SSE**（HTTP GET `/sse` + POST `/messages`）两种传输，协议版本 `2024-11-05`。多服务器工具按 server 前缀去重命名后聚合给 LLM。配置持久化与增删改由工具 `create_mcp` / `list_mcp` / `set_mcp` 及 `~/.zhuzhu_Copilot/agent/mcp_servers.json` 管理。仓库内提供示例本地服务器 `mcp_servers/local_mcp_server.py`（stdio，提供 system_info / get_time / env_var / read_file / list_dir）。

### 1.5 插件系统

`src/zhuzhu_Copilot/core/agent_plugins.py`：插件 = `~/.zhuzhu_Copilot/plugins/<name>/` 目录，含 `plugin.json`（元数据/类型/启用状态/自检结果）、`SKILL.md`、`server.py`（stdio MCP server，web 型兼作本地 HTTP Server + `/api/*`，内置 `--selftest` 离线自检）、可选 `deploy.py`（远程 SSE 部署）、`requirements.txt`、`examples/`。插件类型 kind：`mcp` / `skill` / `combined` / `web`（三件套）。生成链路 `create_plugin_from_nl`：AI 设计 → 落盘 → 冒烟自检（py_compile；web 打 HTTP 端点、mcp 走 stdio tools/list）→ 未过则把错误回灌模型自修 → 通过才登记 MCP。导入插件为 zip 包（含 plugin.json），成功后自动登记技能与 MCP。随包分发示例插件 `src/zhuzhu_Copilot/plugins/frontend-design-pro/`（kind=combined）。

### 1.6 子 Agent 与团队协作

- **子 Agent**：`src/zhuzhu_Copilot/core/agent_subagent.py`。`run_sub_agent`（独立上下文 + 工具白名单的独立 LLM 循环）、`dispatch_sub_agents`（线程池并发派发并汇总）、`explore_goal` / `search_goal` 指令模板。子 Agent 可读写项目文件但不暴露 `run_command`，并硬拒 risky 工具。
- **工作团（Agent Team）**：`src/zhuzhu_Copilot/core/agent_team.py`。团队定义在 `~/.zhuzhu_Copilot/team.json`（缺省用内置默认团队：leader = `product_manager`，成员 = zhuzhu_copilot / frontend_design / product_dev / backend_dev / product_debug）。用户 `@product_manager` 即激活团队模式，开启共同上下文空间，领导者可监督/讨论/纠偏成员。
- **成员后台运行**：`src/zhuzhu_Copilot/core/agent_team_run.py`，成员工作流在后台独立线程运行，消息驱动续跑。
- **控制与通信**：`src/zhuzhu_Copilot/core/agent_control.py`（stop/pause/resume/warn 控制句柄）、`agent_bus.py`（消息总线 mailbox + 上下文账本 ContextLedger）。
- **共享上下文**：`src/zhuzhu_Copilot/core/agent_context.py`，按「对话 + space id」双层隔离的双向读写空间，线程安全。

### 1.7 工作流

`src/zhuzhu_Copilot/core/agent_workflow.py`（Cordis 式「一切皆可替换」）。工作流目录 `~/.zhuzhu_Copilot/workflows/`，`.active` 记录当前激活工作流，`_default/` 为只读的内置映射。用户工作流可含核心文件，缺失部分回退内置：

| 文件 | 作用 |
| --- | --- |
| `agent.py` | 人格/系统提示/生命周期（`AGENT_NAME` / `SYSTEM_PROMPT` / `build_system_prompt` / `on_task_start` / `on_task_end`） |
| `llm.py` | 自定义 LLM 客户端（`LLMClient` 类或 `make_client(config)`） |
| `tools.py` | 自定义工具集（`TOOLS` + `execute_tool(name,args)`） |
| `skills/` | 工作流专属技能目录 |
| `plugins/` | 工作流专属插件目录 |
| `mcp.json` | 工作流 MCP 服务器配置 |
| `panel.py`（可选） | 工作流专属 UI 功能面板 |
| `workflow.json` | 元数据 |

内置模板与预设位于 `src/zhuzhu_Copilot/core/workflow_templates/presets/`：`backend_dev`、`frontend_design`、`product_debug`、`product_dev`、`product_manager`、`sansheng_liubu`、`zhuzhu_copilot`。构建期由 `scripts/prepare_workflow_seed.py` 把本机现成工作流与团队配置快照到 `build/workflows_seed/`，随安装包分发，新机首启自动补齐。

### 1.8 安全能力

**统一安全引擎**（`src/zhuzhu_Copilot/core/security_engine/`）由 `engine.py` 编排，`tick()` 一轮巡检聚合各模块，`analyze_file/process/script` 提供深度分析入口；模块开关由配置 `enabled_modules` 控制：

| 模块 | 文件 | 能力 |
| --- | --- | --- |
| 配置中心 | `config.py` | 加载 `config/security.json`（内置默认 + 用户覆盖），路径支持 `%VAR%`/`~` 展开 |
| 规则引擎 | `rules.py` | 加载外置规则库 `config/security_rules.json`，注册式匹配器，返回命中规则与最高置信度 |
| YARA | `yara_engine.py` | 编译 `config/yara/*.yar` 做恶意特征匹配；yara-python 不可用时降级为禁用 |
| 深度分析 | `deep_analyze.py` | 规则 + YARA + 可选 LLM 的流水线，verdict 归一化为 malicious / suspicious / clean |
| 文件静态分析 | `file_intel.py` | SHA256 / PE 节区与导入表 / 信息熵 / 可打印字符串（纯标准库） |
| 签名验证 | `signature.py` | Authenticode 签名验证与签发者提取（crypt32，纯 ctypes） |
| LLM 分析 | `llm_analyzer.py` | 低置信样本二次判定；未配置/失败则回退规则判定 |
| 隔离区 | `quarantine.py` | 恶意文件移动隔离（记录 sha256 与原路径），支持恢复 |
| 下载防护 | `download_guard.py` | 增量监控下载目录（SHGetKnownFolderPath），规则 + YARA + LLM 流水线，可 notify 或自动隔离 |
| 勒索防护 | `ransomware_guard.py` | 哨兵诱饵文件 + 勒索后缀爆发检测 + VSS 卷影副本 |
| 自启动监控 | `startup_guard.py` | 注册表 Run/RunOnce + 启动文件夹基线对比，发现新增项并判定可疑性 |
| DNS 防护 | `dns_guard.py` | hosts 劫持条目检测与 hosts 篡改（哈希基线）监控 |
| 弹窗拦截 | `popup_guard.py` | WinEventHook 监控窗口创建，按标题/进程特征自动关闭广告窗口 |

**执行守卫**：`src/zhuzhu_Copilot/core/execution_guard.py`，纯 Win32 监控新进程，检测申请管理员权限的程序、拦截格机命令与无文件攻击（PowerShell 编码执行 / IEX 远程加载）。

**工具沙箱与权限**：`src/zhuzhu_Copilot/core/agent_sandbox.py`（命令白名单/黑名单、路径限制，评估结果为 safe / risky / dangerous 三级）；`permissions.py`（提权与夺取文件所有权）。

**网络防御**：`src/zhuzhu_Copilot/core/network_defense.py`（纯 Win32）：ARP 欺骗、SYN 洪泛、TCP/UDP 洪泛、端口扫描检测，命中后经 `HNetCfg.FwPolicy2` 下发防火墙封禁规则（带上限自动清理）。

**静默安全核心**：`src/zhuzhu_Copilot/core/security.py`，纯 ctypes 直接调用 Win32 API（进程枚举/终止、启动项、网络监听、防火墙状态），不依赖 PowerShell / WMI / 系统安全工具。

**输入守卫**：`src/zhuzhu_Copilot/core/input_guard.py`，全局低级鼠标/键盘钩子，AI 操控期间检测到用户手动操作即回调通知（AI 自身模拟输入经抑制避免误判）。

### 1.9 系统工具（应用管理）

- **应用扫描**：`src/zhuzhu_Copilot/core/app_scanner.py`（注册表 + UWP 清单扫描，识别已安装应用）。
- **应用迁移**：`src/zhuzhu_Copilot/core/migration.py` + `orchestrator.py`，支持 junction / move 两种模式，联动更新注册表路径（`registry.py`）、快捷方式（`shortcut.py`）、UWP 包（`uwp.py`）。
- **内存优化**：`src/zhuzhu_Copilot/core/memory_optimizer.py`（工作集 trim、系统级 trim、standby list 清理）。
- **应用卸载**：`src/zhuzhu_Copilot/core/uninstaller.py`（先调应用自带卸载器，再清理根目录、注册表关联项、快捷方式、数据目录）。
- **数据目录探测**：`src/zhuzhu_Copilot/core/data_dirs.py`。
- **壁纸/背景**：`src/zhuzhu_Copilot/core/app_wallpaper.py`（适配方式 cover/contain/stretch/tile + 可选高斯模糊 + 压暗纱）。
- **高速下载**：`fast_download.py`（见 1.2）。

### 1.10 办公文档

`src/zhuzhu_Copilot/office/`：

- **读取**：`reader.py`，统一入口 `read_document` 按扩展名分派 `read_docx` / `read_pptx` / `read_xlsx` / `read_pdf`，输出结构化 Markdown（保留标题层级、粗斜体、表格、公式、数字格式、形状类型与位置尺寸、动画条目、图表系列）；图片只标注占位不内联 base64。支持扩展名：docx/docm/pptx/pptm/xlsx/xlsm/pdf。
- **生成**：`docx_builder.py`（封面、目录域、表格、引用块、分页符、页眉页脚页码）、`pptx_builder.py`（16:9，统一排版系统；**元素动画与幻灯片切换**真实 XML 实现）、`xlsx_builder.py`（主题统一、数字格式、`~sum` 合计公式、原生图表）。
- **编辑**：`editor.py`，`edit_document` 按扩展名分派，操作数组逐条执行、单条失败不中断，返回逐条报告（读写闭环：read → 定位锚点 → edit → 复核）。
- **美化**：`beautify.py`，就地统一已有文档的字体/配色/行距/底纹，不改内容。
- **保真预览**：`preview.py`，把 Word/PPT/Excel 渲染为自包含 HTML（图片内联 data URI），提供 `extract_slides` 与放映接口（`__deck*`），供预览面板与全屏放映使用。
- **主题与兼容**：`theme.py`（三件套统一色板）、`compat.py`、`utils.py`。

### 1.11 媒体

- **音乐播放**：`src/zhuzhu_Copilot/core/music_player.py`（pygame.mixer），歌曲存放 `~/.zhuzhu_Copilot/music/`，播放状态持久化到同目录 `player.json`，支持顺序/随机/单曲循环。
- **歌词**：`src/zhuzhu_Copilot/core/lyrics_engine.py`（LRC 解析、BPM/拍号元数据、变速换算、休止识别、逐字插值）；桌面歌词窗口 `src/zhuzhu_Copilot/ui/desktop_lyrics.py`（无边框置顶，纯白按节奏填充当前句、淡灰显示下一句，滚轮调字号）。
- **语音合成与音色克隆**：`src/zhuzhu_Copilot/core/agent_tts.py`（阿里云 DashScope 真实 API：`create_voice` 声音复刻、`query_voice`、`delete_voice`、`synthesize`）。API Key 读取顺序：环境变量 `DASHSCOPE_API_KEY` → 独立配置文件 `~/.zhuzhu_Copilot/agent/tts.json`。对应工具 `tts_create_voice` / `tts_query_voice` / `tts_delete_voice` / `tts_speak`。

### 1.12 界面

- **Agent 面板**：`src/zhuzhu_Copilot/ui/agent_panel.py`（应用主界面）。
- **事件流气泡**：`src/zhuzhu_Copilot/ui/agent_chat_bubbles.py`（思考气泡、工具调用行内条目、命令块、耗时徽章、过程区收起等；按段内容签名增量渲染）。
- **Copilot 浮层宿主**：`src/zhuzhu_Copilot/ui/main_window.py`。
- **桌宠**：`src/zhuzhu_Copilot/ui/desktop_pet.py`（透明 GIF 常驻桌面右下角，悬停循环播放）。
- **内置浏览器面板**：`src/zhuzhu_Copilot/ui/webview_panel.py`（QWebEngineView 多标签、地址栏、前进后退、下载确认）。
- **全屏放映**：`src/zhuzhu_Copilot/ui/slideshow.py`（PPT 放映态与文档放大查看，键盘/鼠标/滚轮翻页）。
- **主题与令牌**：`src/zhuzhu_Copilot/ui/styles.py`、`tokens.py`、`tool_icons.py`（淡灰色矢量图标）、`widgets.py`；壁纸绘制见 `core/app_wallpaper.py`。
- **下载对话框**：`src/zhuzhu_Copilot/ui/download_dialog.py`。
- **自定义功能面板**：`src/zhuzhu_Copilot/core/agent_panels.py` 注册表，支持来自 code / 工作流 `panel.py` / 插件 `panel.py` 的面板自动发现与挂载。
- **本地 Web 前端数据服务**：`src/zhuzhu_Copilot/web/server.py`，把桌面端真实数据以 JSON 暴露给单文档 Web 前端（仅标准库，默认 `127.0.0.1:8765`，可用 `python -m zhuzhu_Copilot.web.server` 启动）。

### 1.13 自动更新

`src/zhuzhu_Copilot/update_check.py`：`UpdateChecker` 每 30 秒轮询更新服务器（`{server}/api/update/check?version=<APP_VERSION>&platform=windows`），发现新版本发信号提示；首次启动立即检查一次。当前客户端版本常量 `APP_VERSION` 与更新服务器地址解析见第六节。安装包由 `installer/` 下的 Inno Setup 脚本产出。

---

## 二、目录结构

| 目录 / 文件 | 说明 |
| --- | --- |
| `src/main.py` | 程序入口：迁移 → 崩溃钩子 → QApplication → 启动动画 → AI 面板 |
| `src/zhuzhu_Copilot/app_identity.py` | 应用身份单一数据源（包名/显示名/注册表作用域/数据目录）与旧版一次性迁移 |
| `src/zhuzhu_Copilot/update_check.py` | 客户端自动更新检查（`APP_VERSION`、轮询） |
| `src/zhuzhu_Copilot/core/` | Agent 引擎、工具、技能、工作流、安全、系统工具等核心逻辑 |
| `src/zhuzhu_Copilot/core/security_engine/` | 统一安全引擎与各防护模块 |
| `src/zhuzhu_Copilot/office/` | Word / Excel / PPT / PDF 的读写、编辑、美化与预览 |
| `src/zhuzhu_Copilot/ui/` | PyQt6 界面（面板、气泡、桌宠、歌词、WebView、放映等） |
| `src/zhuzhu_Copilot/skills/` | 内置技能（各含 `SKILL.md`） |
| `src/zhuzhu_Copilot/plugins/` | 随包分发插件 |
| `src/zhuzhu_Copilot/core/workflow_templates/` | 工作流模板与内置预设 |
| `src/zhuzhu_Copilot/utils/` | 通用工具函数（日志、异常钩子等） |
| `src/zhuzhu_Copilot/web/server.py` | 本地 Web 前端数据服务 |
| `src/requirements.txt` | Python 运行时依赖 |
| `mcp_servers/local_mcp_server.py` | 示例本地 MCP 服务器（stdio） |
| `config/` | `security.json`（引擎配置）、`security_rules.json`（规则库）、`yara/*.yar` |
| `installer/` | Inno Setup 7 安装脚本与语言文件 |
| `update-server/` | Spring Boot 更新服务器（详见 `update-server/README.md`） |
| `scripts/` | 构建 / 打包 / 部署辅助脚本 |
| `assets/` | 图标、桌宠 GIF、清单等静态资源 |
| `build/` | 构建中间产物、运行时与工作流种子、签名证书目录、PyInstaller spec |
| `tests/` | pytest 单元测试 |
| `docs/` | 设计与评审文档 |
| `.github/workflows/ci.yml` | 持续集成流水线 |
| `build_release.ps1` | 客户端发布流水线（单一入口） |
| `pytest.ini` | pytest 配置 |

---

## 三、环境搭建与运行

```powershell
# Windows，Python 3.13
pip install -r src/requirements.txt
python src/main.py
```

首次运行会在用户家目录创建数据目录 **`%USERPROFILE%\.zhuzhu_Copilot\`**（配置、工作流、技能、插件、会话、音乐、运行时等），并在临时目录创建 **`%TEMP%\zhuzhu_Copilot\`**（日志与崩溃转储）。若检测到旧版（`WinAppMigrator`）的 `.winapp_migrator` 目录与注册表作用域，会在启动最早时机自动一次性迁移，保证主题、执行模式、工作流、Agent、会话与密钥不丢失（见 `app_identity.ensure_migrated`）。

> Linux / macOS 不可运行：本项目大量依赖 Win32 API 与 Windows 路径语义。

---

## 四、Python 依赖库

以 `src/requirements.txt` 为准（含直接 import 但未在其中声明的 `lxml`，见下方说明）：

| 依赖 | 版本约束 | 用途 | 缺失时的行为 |
| --- | --- | --- | --- |
| `PyQt6` | `>=6.7` | 桌面 GUI 框架（全部界面，含 QScreen 截图与媒体播放） | 核心依赖，缺失即无法启动 |
| `PyQt6-WebEngine` | `>=6.7` | 内置多标签浏览器与 HTML 预览（QWebEngineView） | 内置 WebView 不可用，相关功能降级（`agent_ui_ux.web_engine_available` 探测） |
| `Pillow` | `>=10.0` | 图像处理（桌宠 GIF 帧解码、截图缩放、办公图片） | 相关功能降级/不可用 |
| `pywin32` | `>=306` | Windows API：`win32gui`（图标提取）、`win32com`（防火墙规则、WScript.Shell 快捷方式） | 图标提取、防火墙封禁、快捷方式更新等功能降级 |
| `python-docx` | `>=1.1` | Word 读写 | `office.reader` 惰性导入，缺失时给出明确安装提示 |
| `openpyxl` | `>=3.1` | Excel 读写 | 同上 |
| `python-pptx` | `>=0.6` | PPT 读写 | 同上 |
| `pypdf` | `>=4.0` | PDF 阅读（read_pdf / PDF 预览） | 缺失时优雅降级并提示安装（requirements.txt 注释） |
| `Cython` | `>=3.0` | 构建期可选：把 core 模块编译为 `.pyd` 加固 | 仅构建期需要；源码运行不需要 |
| `yara-python` | `>=4.5` | 安全引擎 YARA 规则匹配 | 缺失时安全引擎自动降级为禁用 YARA，不影响其他防护（requirements.txt 注释） |
| `pygame` | `>=2.5` | 音乐播放与 TTS 朗读回放（`pygame.mixer`） | 缺失时音乐/朗读静默降级，不影响其余功能（requirements.txt 注释） |
| `lxml` | 未声明 | Word/PPT 底层 XML 操作：`office/docx_builder.py`、`office/pptx_builder.py` 直接 `from lxml import etree` | 见下方说明 |

**关于 `lxml` 的缺口**：`lxml` 被 `src/zhuzhu_Copilot/office/docx_builder.py` 与 `src/zhuzhu_Copilot/office/pptx_builder.py` **直接 import**（用于底层 XML 操作），但 `src/requirements.txt` 里**没有显式声明**，目前仅靠 `python-docx` / `python-pptx` 间接带入。若上游依赖调整、或在精简环境里单独安装 office 依赖，这两处代码路径会因 `ModuleNotFoundError` 失败。此处已在表内标注，**建议后续把 `lxml` 补进 `src/requirements.txt`**。

**构建期可选依赖**：`Cython`（`scripts/cython_build.py`、`build/cythonize_build.py` 用于把 `core/` 编译为 `.pyd` 加固）与 `PyInstaller`（打包主程序）。二者都**不在 `src/requirements.txt`**：`build_release.ps1` 在体检阶段自动探测一个同时具备 `PyInstaller + PyQt6` 的 Python 解释器，并校验 `PyInstaller,PyQt6,PIL,docx,pptx,openpyxl,pygame,win32api` 是否齐全，缺失则构建失败并给出安装指引。

---

## 五、构建与打包（客户端）

发布流水线是单一入口 `build_release.ps1`：

```powershell
.\build_release.ps1                 # 完整打包 + 签名
.\build_release.ps1 -Version 5.1.2  # 同时把版本号同步到三处并回读校验
.\build_release.ps1 -DryRun         # 只做环境体检 + 版本同步，不构建
.\build_release.ps1 -NoSign         # 生成未签名产物（本地验证用）
.\build_release.ps1 -SkipRuntime -NoSign   # 复用已有运行时、不签名，快速出包
```

真实存在的开关（见脚本 `param` 块）：`-Version`、`-ProjectDir`、`-Python`、`-CertSubject`、`-TimestampServer`、`-Iscc`、`-NoSign`、`-SkipRuntime`、`-SkipSeed`、`-SkipApp`、`-SkipInstaller`、`-KeepDist`、`-DryRun`、`-NoLog`。

- **`installer\build_setup.bat`** 是薄壳入口，仅以 `powershell.exe -File build_release.ps1 %*` 转发参数，本身不再维护独立流程。
- **版本号一处生效**：`-Version x.y.z` 会同步 `installer\zhuzhu_Copilot.iss`（`MyAppVersion`）、`src\zhuzhu_Copilot\update_check.py`（`APP_VERSION`）、`build\version_info.txt`（exe 文件属性版本）并回读校验。
- **流程阶段**：环境体检 → 版本号同步 → 沙盒运行时打包（Node/Python 便携版）→ 依赖组件准备（VC++ 运行库 / d3dcompiler_47）→ 工作流种子快照 → 清理旧产物 → 生成 spec + 打包主程序 → 签名主程序 → 编译安装包（安装包与卸载器在编译期一并签名）→ 校验安装包签名 → 产物自检 + 汇总。
- **产物位置**：`dist\zhuzhu Copilot\`（PyInstaller onedir 目录，主程序 `zhuzhu Copilot.exe`）与安装包 `dist\zhuzhu Copilot Setup.exe`。自检会核对 exe 文件属性版本号与关键模块是否随包（`scripts/verify_release_artifact.py`），任一不合格即失败。
- **PyInstaller spec**：`build\zhuzhu_Copilot.spec` 由 `scripts/generate_spec.py` 在打包前按仓库配置**实时重新生成**（单一事实来源，写与读之间无竞争窗口）；该文件被 `.gitignore` 显式白名单保留入库（`!build/zhuzhu_Copilot.spec`）。
- **签名证书约定**：脚本从 `Cert:\CurrentUser\My` 中查找 Subject 含 `zhutianliang` 且带私钥的证书进行 Authenticode 签名（含时间戳）。**私钥只留在证书库里，构建过程不导出任何 `.pfx` 文件**；公开的 `build/certs/zhutianliang.cer` 可入库。签名口令不写入仓库。
- **卸载器签名必须在编译期完成**：`build_release.ps1` 以 `ISCC -s<名称>=<命令> /DSIGNTOOL=<名称>` 把 `scripts\sign_file.ps1` 注册为 SignTool，`.iss` 内 `SignedUninstaller=yes` 随之启用。Inno 只在卸载器 EXE 自带签名时才把语言文本外置为 `unins000.msg`（已签名的 EXE 不能再改），因此**绝不能改成「安装后用脚本补签 `unins000.exe`」**：那样卸载器运行时会转去读并不存在的 `unins000.msg`，卸载当场中止（退出码 0、什么都不删、连日志都写不出）。

---

## 六、部署方式

### 6.1 客户端

**分发渠道**（同一份产物 `zhuzhu Copilot Setup.exe`，两个入口）：

| 渠道 | 地址 | 适用场景 |
| --- | --- | --- |
| GitHub Releases | `https://github.com/Chentianzhuzhu/zhuzhu_copilot/releases` | 对外直链下载，版本归档、可追溯 |
| 官网 / 更新服务器 | 官网「下载安装」页；客户端自动更新走 `/api/update/check` | 站内下载与客户端自动升级 |

**推送到 GitHub Release**：`scripts/publish_release.py`（仅标准库，令牌只读环境变量）：

```powershell
$env:GITHUB_TOKEN = "<personal-access-token>"   # 需要 repo 权限，禁止写入仓库
python scripts/publish_release.py --dry-run     # 只打印计划
python scripts/publish_release.py               # 建/复用 Release 并上传安装包
```

- tag 默认取 `v<APP_VERSION>`（版本号单一来源为 `src/zhuzhu_Copilot/update_check.py`，与 `build_release.ps1 -Version` 同步），因此 tag 不会与客户端声明版本漂移。
- 幂等：同名 tag 的 Release 已存在则复用，同名资产先删后传，重跑不会堆出重复资产。
- 流式上传（显式 `Content-Length` + 分块），400MB+ 安装包不进内存。
- 可用 `--draft` / `--prerelease` / `--notes-file` / `--asset` 覆盖默认行为。

**安装与卸载**：Inno Setup 7 安装包（`installer\zhuzhu_Copilot.iss`），安装时写入注册表、创建桌面快捷方式，卸载走自带 `unins000.exe`。
- **自动更新**：客户端每 30 秒轮询更新服务器。服务器地址解析优先级（`update_check.py`）：
  1. QSettings 键 `update_server`
  2. 环境变量 `UPDATE_SERVER_URL`
  3. 源码内置默认服务器常量 `DEFAULT_SERVER`（保证首次启动必检）
- **版本号机制**：客户端版本来自 `update_check.py` 的 `APP_VERSION`，构建时由 `build_release.ps1 -Version` 同步；服务器据此判断是否有新版本。

### 6.2 云服务：更新服务器（update-server，Spring Boot）

技术栈：**Spring Boot 3 + MySQL 8 + Redis 6+ + nginx（HTTPS/域名）**，应用只监听 `127.0.0.1:8080`，由 nginx 反代对外。

**环境变量**（读 `update-server/src/main/resources/application.yml`，示例值仅为占位，禁止把真实凭据写入仓库）：

| 变量 | 用途 | 示例 |
| --- | --- | --- |
| `ADMIN_PASSWORD` | 管理后台登录密码，空则后台拒绝登录 | `example-password` |
| `DB_HOST` / `DB_PORT` / `DB_NAME` | MySQL 连接 | `127.0.0.1` / `3306` / `winapp_update` |
| `DB_USER` / `DB_PASSWORD` | MySQL 账号 | `root` / `example-password` |
| `REDIS_HOST` / `REDIS_PORT` | Redis 连接（`REDIS_PASSWORD` 默认注释） | `127.0.0.1` / `6379` |
| `SITE_BASE_URL` | 官网绝对地址（canonical / sitemap / 结构化数据），留空按请求头推导 | `https://example.com` |
| `UPLOAD_DIR` | 上传目录（安装包 + 官网图片 + 视频） | `./uploads` |

**数据库初始化**：执行 `update-server/src/main/resources/db/schema.sql`（可选，JPA `ddl-auto: update` 也会自动建表）。

**公开数据接口 `GET /api/site`**：官网首页所需的全部数据一次返回 —— `content`（页面文案）、`totalDownloads`、`latest`（最新版本）与 `timestamps`（站点时间戳）：

| `timestamps` 字段 | 含义 | 数据来源 | 为空的条件 |
| --- | --- | --- | --- |
| `siteContentUpdatedAt` | 站点内容更新时间（ISO-8601） | `site_content.updated_at`，后台每保存一次内容即刷新 | 库中尚无内容记录 |
| `latestReleaseDate` | 最新版本发布日期（`yyyy-MM-dd`） | 最新一条 `app_version.created_at` | 尚无发布记录 |
| `mediaLibraryUpdatedAt` | 媒体库更新时间（ISO-8601） | `uploads/img` 与 `uploads/video` 中最新的文件修改时间（**不含安装包**） | 媒体库为空 |

三个口径统一由 `SiteTimestampService`（`update-server/src/main/java/com/zhuzhu/update/service/SiteTimestampService.java`）取值，**页脚展示与 API 共用同一份数据**，避免两处各算各的导致口径漂移。全站页脚以「站点内容更新 / 版本发布日期 / 媒体库更新」三个日期输出，任一来源缺失时该条整条隐藏、不留空占位。

**一键部署**：`update-server/tools/deploy.py`（SSH/SFTP + 远端 Maven 构建 + systemd 重启 + 健康检查），凭据只从环境变量读取、不写入仓库也不打印：

```bash
export REMOTE_HOST=<服务器地址> REMOTE_USER=<用户名> REMOTE_PASS=<密码>
python update-server/tools/deploy.py                    # 上传 + 构建 + 重启 + 健康检查
python update-server/tools/deploy.py --dry-run          # 只列出将上传的文件
python update-server/tools/deploy.py --verify-only      # 只做健康检查，不上传不构建不重启
python update-server/tools/deploy.py --skip-build       # 只上传（排障用）
python update-server/tools/deploy.py --skip-restart     # 不重启服务
```

脚本另支持 `--host` / `--user` / `--remote-dir` / `--service` / `--env-file` / `--base-url`。

**nginx 配置**（`update-server/deploy/nginx.conf`）要点：80 → 301 跳 HTTPS；`/admin` 对外返回 404（管理后台**仅可通过 SSH 隧道**访问 `127.0.0.1:8080/admin`）；旧静态官网 `/website/` 已 301 归并到根路径 `/`；`client_max_body_size 600m`；安装包下载路径关闭代理缓冲直接透传；`/uploads/` 反代上传资源。

**健康检查覆盖的端点**（`deploy.py` 的 `checks` 列表）：`/`、`/gallery`、`/download`、`/faq`、`/about`、`/sitemap`、`/robots.txt`、`/sitemap.xml`、`/site.webmanifest`、`/favicon.svg`、`/css/site.css`、`/js/site.js`、`/api/site`、`/api/version/latest`、`/uploads/`（期望 404），以及后台 `/admin`、`/admin/`、`/admin/index.html`、`/admin/admin.css`、`/admin/admin.js`；并逐个真实请求页面引用的站内资源。

---

## 七、持续集成

`.github/workflows/ci.yml`（push 到 `main` 与 pull request 触发，`PYTHONIOENCODING=utf-8`）三个 job：

1. **python-check**（windows-latest，Python 3.13）：安装 `src/requirements.txt` 与 `ruff`，执行 `python -m compileall -q src mcp_servers` 语法编译检查，并运行 `ruff check src mcp_servers --statistics`（`|| true`，不阻断）。
2. **security-engine-tests**（windows-latest，Python 3.13）：安装依赖与 `pytest`，执行 `python -m pytest tests -v`（安全引擎 / 网络防御 / Git 数据层 / 静态官网 SEO 等单元测试）。
3. **update-server**（ubuntu-latest，Java 21）：在 `update-server/` 下执行 `mvn -B -q test`（内容合并 / SEO 推导 / 上传安全 / 模板渲染），随后以 Python 3.13 执行 `python -m unittest discover -s tests -v` 做官网静态回归校验（模板配平 / 元素 id 一致性 / 四色规范 / SEO 资源 / 编码）。

---

## 八、开发约定

- 提交信息格式：`type(scope): 描述`（feat / fix / style / perf / refactor / chore）。
- 临时诊断脚本请加 `_` 前缀（`.gitignore` 已排除 `/_*` 与 `scripts/_*`），不要提交。
- 构建产物不入库：`build/`（中间产物、`runtime_bundle/`、`redist/`、`workflows_seed/` 中的个人化工作流）、`dist/`、`src/build/`、`src/tts_output/`、`*.spec`（除 `build/zhuzhu_Copilot.spec` 白名单外）、`*.docx` 等（以 `.gitignore` 为准）。
- 代码签名私钥 `build/certs/*.pfx` 与本地个人数据（如 `memory.md`）严禁提交。
