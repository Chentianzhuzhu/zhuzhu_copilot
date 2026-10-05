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
- **命令与沙箱**：`run_command` / `check_command`；沙盒模块 `src/zhuzhu_Copilot/core/sandbox.py`（命令白名单、路径白名单、输出限制、后台轮询）。
- **浏览器**：`browser_open` 等内置工具族（`browser_control.py`），启动独立 WebView/CDP 实例；`browser_eval` 可在页面注入 JS；支持多标签页 `browser_tabs`。
- **屏幕操控**：`screen_info` / `screen_shot` / `click` / `swipe` / `type_text` 等（`screen_control.py`），支持虚拟化桌面操作。
- **快捷应用**：`find_app`（开始菜单 + 注册表模糊索引）、`uninstall_app`、`migrate_app`。
- **剪贴板**：`clipboard`（读 / 写）。
- **文档提取**：`extract_text`（txt/md/log/json/csv/docx/pptx/xlsx；PDF 需 pypdf）。
- **文档生成**：`create_docx` / `create_pptx` / `create_xlsx`；编辑：`edit_docx` / `edit_pptx` / `edit_xlsx`；美化：`beautify_*`。
- **系统管理**：`system_info` / `get_time` / `env_var` / `optimize_memory`。
- **任务编排**：`update_todo` / `list_todo`；记忆：`save_memory` / `load_memory`。
- **工作流/技能/Agent 管理**：`list_workflows` / `create_workflow` / `switch_workflow` / `inspect_workflow` / `create_skill` / `create_plugin` / `register_sub_agent` 等（`skill_manager.py` / `workflow_manager.py` / `sub_agent_manager.py`）。
- **MCP 注册**：`create_mcp` / `list_mcp` / `set_mcp`。
- **面板扩展**：`register_feature_panel`（注入浮动窗口）。
- **微信 ClawBot**：`send_file_to_wechat` / `send_files_to_wechat`（推送文件到绑定的手机微信）；入站文件读取由 `wechat_bridge.py` 处理。
- **进度反馈**：`set_generation_progress`（长耗时生成任务）。
- **子 Agent 调度**：`dispatch_sub_agents` / `explore_project` / `search_large` / `ask_user`。
- **团队通讯**：`chat_with` / `pause_agent` / `resume_agent` / `warn_agent` / `look_context` / `list_agents_network`。

### 1.3 技能系统

`src/zhuzhu_Copilot/core/skill_manager.py` 扫描 `~/.zhuzhu_Copilot/agent/skills/<name>/SKILL.md`。每个 SKILL.md 包含 description 与 instruction（可含 tool 白名单）。内置 23 个技能：brainstorming、browser-control、cmd-ops、code-review、complex-task、create-cordis、custom-ui-ux、deep-customize、doc-gen、download-skill、feature-panel、file-ops、frontend-design-pro、git-commit、image-gen、memory、plugin-create、skill-create、skill-mgmt、sub-agent、system-admin、systematic-debugging、test-driven-development、web-search、writing-plans。支持 `@skillname` 激活。

### 1.4 MCP 支持

`src/zhuzhu_Copilot/core/mcp_registry.py` 维护 `mcp_servers.json`，支持 stdio / SSE 两种传输（协议版本 `2024-11-05`）。启动时加载并暴露 `tools/list` 握手；工具名带 `<server>_` 前缀去重。示例本地服务器 `mcp_servers/local_mcp_server.py`。

### 1.5 插件系统

`src/zhuzhu_Copilot/core/plugin_manager.py` 扫描 `~/.zhuzhu_Copilot/plugins/`，加载 `plugin.json`（含 `kind` / `entry` / `dependencies`）。生成链路：AI 设计 → 落盘 → 冒烟自检（自动修正）→ 登记 MCP。类型 `mcp` / `skill` / `combined` / `web`（web 类型会起本地 HTTP UI）。

### 1.6 子 Agent 与团队协作

- 子 Agent 独立上下文 + 工具白名单；`dispatch_sub_agents` 线程池并发派发。
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
- **Copilot 浮层**：半透明覆盖层，显示当前任务状态。
- **桌宠**：透明 GIF 动画，右键菜单快捷操作。
- **内置浏览器**：WebView 多标签页，与系统浏览器隔离。
- **全屏放映**：HTML 预览全屏模式。
- **自定义面板**：`register_feature_panel` 挂载浮动窗口。
- **本地 Web 服务**：`127.0.0.1:8765`，提供预览/调试接口。

### 1.13 自动更新

每 30 秒轮询更新服务器 `/api/update/check`，首次启动立即检查。新版本下载 → 校验签名 → 替换 → 重启。旧版数据自动迁移。

---

## 二、目录结构

```
zhuzhu Copilot/
├── src/
│   ├── main.py                          # 入口：迁移 → 崩溃钩子 → QApplication → 启动动画 → AI 面板
│   └── zhuzhu_Copilot/
│       ├── core/
│       │   ├── agent_engine.py          # Agent 核心循环（观察→调用→验证）
│       │   ├── agent_llm.py             # LLM 接入（SSE / 图像输入 / 重试）
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
│       │   ├── plugin_manager.py        # 插件加载
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
├── scripts/                             # 一次性脚本（以 _ 开头）
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

## 八、开发约定

- 提交信息格式：`type(scope): 描述`（feat / fix / style / perf / refactor / chore）。
- 临时诊断脚本请加 `_` 前缀（`.gitignore` 已排除 `/_*` 与 `scripts/_*`），不要提交。
- 构建产物不入库：`build/`（中间产物、`runtime_bundle/`、`redist/`、`workflows_seed/` 中的个人化工作流）、`dist/`、`src/build/`、`src/tts_output/`、`*.spec`（除 `build/zhuzhu_Copilot.spec` 白名单外）、`*.docx` 等（以 `.gitignore` 为准）。
- 代码签名私钥 `build/certs/*.pfx` 与本地个人数据（如 `memory.md`）严禁提交。

---

> **核心循环**：`agent_engine.py` 中的「观察 → 调用工具 → 验证结果」循环，LLM 逐 token 流式回调 UI；每个工具执行前可回调确认，沙箱判定为危险的工具即使被批准也由 `agent_tools` 硬拒绝。

> **安全守卫**：`input_guard.py` 全局低级鼠标/键盘钩子，AI 操控期间检测到用户手动操作即回调通知（AI 自身模拟输入经抑制避免误判）。
