# 项目长期约定（zhuzhu Copilot / winapp_migrator）

## 架构（2026-09-25 起）
- **不存在独立主窗口**：唯一界面是 `AgentPanel`（AI 面板）。
  zhuzhu Copilot 的迁移器功能收在 `CopilotPanel`（文件 `ui/main_window.py`，类名已改）
  浮层内 —— 由 AgentPanel 顶栏 `copilot_btn` 懒创建并托管，`main.py` 启动即进 AI 面板。
- 主界面文案/图标改动时注意：`main_window.py` 里保留 Worker 类与样式常量，
  只有窗口外壳换成了 `QFrame` 浮层。

## 环境
- 跑 PyQt6 脚本 / 测试必须用系统 Python313：
  `C:\Users\zhuzhu\AppData\Local\Programs\Python\Python313\python.exe`
  （managed python 无 PyQt6，直接跑会 ModuleNotFoundError）。
- 离屏测试用 `QT_QPA_PLATFORM=offscreen`；offscreen 下 CJK 渲染成方块，
  因此**几何断言优于截图肉眼判断**。

## UI 规范（用户硬性要求）
- 禁止 emoji；图标一律淡灰线条矢量（`_line_icon(kind,…)` / `svg_icon(...)`）。
- 配色只用：纯黑 + 淡灰 + 白 + 深蓝；禁止把红/绿/橙当主色。
- 页面内通知优先于原生 alert。

## 事件流聊天气泡（2026-09-27 起）
- 界面基准是 `ui_style_demo/index.html`（用户指定"完美 1:1 复原"的唯一风格来源）。
- 气泡外形全部收在 `ui/agent_chat_bubbles.py`：
  `ChatTurn`（AI 回合，无填充气泡 + 虚线分区 + 耗时徽章 + 过程区收起）、
  `ThinkBubble` / `ToolCallRow` / `CmdBlock` / `RichBlock` / `StreamBlock` / `UserBubble`。
  该模块**禁止出现 #RRGGBB 字面量**（颜色一律经 `ChatStyle` 注入，
  由面板 `AgentPanel._chat_style()` 从当前主题色板生成），已有回归测试守着。
- 段 → 区块映射的唯一入口是 `AgentPanel._seg_block()`（新增段类型只改这里）。
  每段各自是一个独立控件，因此**没有整泡 HTML 预算护栏**，靠单段截断
  （`_RESULT_TRUNCATE` / `_SUB_*`）保证单块体积有界。
- 回合耗时/时间行随会话落盘在 `rows` 的 `cost` / `meta` 字段（老会话无此键 → 徽章隐藏）。
- 性能约定：`ChatTurn.render()` 按段内容签名增量更新，签名未变的块不重建、不重排。
- 布局断言用 `isHidden()` 而不是 `isVisible()`：面板未显示时 `isVisible()` 恒为 False，
  会误判"已按期望隐藏"。

## 环境
- 跑 PyQt6 脚本 / 测试必须用系统 Python313：
  `C:\Users\zhuzhu\AppData\Local\Programs\Python\Python313\python.exe`
  （managed python 无 PyQt6，直接跑会 ModuleNotFoundError）。
- 离屏测试用 `QT_QPA_PLATFORM=offscreen`；offscreen 下 CJK 渲染成方块，
  因此**几何断言优于截图肉眼判断**。
- **`agent_panel.py` 是 CRLF 行尾**（`tokens.py` 等其它文件是 LF）：
  改它只能用 Edit/Write 工具；用裸 Python 写文件必须显式还原 `\r\n`，
  否则会产生整文件级虚假 diff（已踩坑一次）。
- `AgentPanel.__new__` 轻代理可跑渲染链路，但**不能 `show()`**：面板的
  `resizeEvent` 等回调会访问未初始化属性 → PyQt6 未捕获异常 → 进程 abort
  （表现为"无输出 + 退出码 127"）。集成测试用真 `QDialog` 基类初始化 +
  手工接线最小属性，且不 show。

## ⚠️ 仓库 git 状态（2026-09-27）
- 本机 `.git` 对象库损坏：`refs/` 目录与 `objects/pack/*.pack` 丢失（15:51 左右，
  疑为 `git stash` 触发 auto-gc 后清理）。已手工重建 refs：
  `main` = `6816631abfdb64b7c7a94ddd59d56e30500019f6`，
  `origin/main` = `486256b953daee8851bbebaa44ebbd5746712681`，但对象本体已不存在。
- **工作区文件完好**，无需从 git 恢复内容；恢复历史需在沙箱外
  `git fetch`/重 clone 远端 `https://github.com/Chentianzhuzhu/zhuzhu_copilot.git`，
  再把工作区覆盖过去重新提交。
- **在该仓库恢复前，避免任何 git 写操作**（commit/stash/gc/checkout），防止二次清理。

## update-server（官网 + 后台 + 更新 API，2026-09-25 起）
- 位置 `update-server/`，Spring Boot 3.2.5 + MySQL + Redis；线上 `/www/update-server`，
  域名 `https://chentian.dpdns.org`，服务 `update-server.service`，`/admin` 仅 SSH 隧道可达。
- 官网是**服务端渲染**（Thymeleaf `templates/`），内容全部来自 `site_content` 单行 JSON；
  新增字段必须同时改 `ContentService.defaults()` 与 `admin.js` 的 `SCHEMAS`，否则后台编辑不了。
- 内容读取走深合并：后台清空列表 = 真的空（不会被默认值复填），老库数据自动补新键。
- 域名/站点地址禁止硬编码：用 `SITE_BASE_URL`（缺省按 `X-Forwarded-*` 推导，
  非法 Host 回落 localhost）。
- 改 Java 后本机可用 `python tools/run_tests.py` 跑真实单测（含模板渲染），
  `python tools/deploy.py --base-url https://chentian.dpdns.org` 一条命令上线；
  线上健康检查须先等就绪（Spring Boot 启动约 17 秒）。
- 前端测试红线：四色体系、禁止 emoji、禁止原生 alert/confirm/prompt、全项目文件必须 UTF-8。
