# 项目长期约定（zhuzhu Copilot）

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
- **`agent_panel.py` 是 CRLF 行尾**（`tokens.py` 等其它文件是 LF），且**碰它的每个
  环节都会把行尾规范化成 LF**：Edit/Write 工具会，裸 Python 写入也会被外部进程在后
  续改回 LF（写完立刻读是 CRLF，稍后再读又变 LF）。对策：改动全部做完后**统一转换
  一次并立即 `git add`**，再用 `git ls-files --eol` 复核（2026-10-01 二次踩坑：
  裸转换 + 稍后检查 → `w/lf`，diff 4.2 万行）。
- `AgentPanel.__new__` 轻代理可跑渲染链路，但**不能 `show()`**：面板的
  `resizeEvent` 等回调会访问未初始化属性 → PyQt6 未捕获异常 → 进程 abort
  （表现为"无输出 + 退出码 127"）。集成测试用真 `QDialog` 基类初始化 +
  手工接线最小属性，且不 show。

## ⚠️ 仓库 git 状态（2026-09-27 重建）
- 旧 `.git` 对象库曾损坏（`refs/` 与 `objects/pack/*.pack` 丢失，工作区文件未受影响）。
  已按用户要求重建本地仓库：**新 `main` 首版提交 `136430e`**（`chore(repo): 重建本地仓库…`），
  跟踪 696 文件，仓库约 16 MiB，`origin` 已配回
  `https://github.com/Chentianzhuzhu/zhuzhu_copilot.git`，**尚未推送**。
- **历史不再连续**：原 9 条提交（旧 tip `6816631a`）的对象已丢失，新仓库是全新历史。
  提交标题清单已存 `memory/2026-09-27.md`，可按需人工重建说明。
- 旧 `.git` 备份在仓库外 `C:\Users\zhuzhu\Desktop\.git_broken_20260927_myfirstandroidapp`

### 可用的历史快照
- 项目根 `.git_broken_backup/`（581MB）= **2026-09-26 的完整有效仓库**，
  tip `498ba64`，含真实 pack 对象（不含今天的提交）。想接回 9-26 之前的历史可
  `git fetch C:/Users/zhuzhu/Desktop/my\ first\ android\ app/.git_broken_backup main`。
  该目录已加入 `.gitignore`（防止 `git add -A` 误提交 581MB）。
- 远端 `origin/main` 停在 `486256b9`（比损坏前的本地少 3 个提交），
  网络恢复后 `git fetch origin` 可取回该提交及其祖先。

### 教训（重要，避免重犯）
- **不要在这个仓库上随意跑 `git stash` / `git gc` 等写操作**：refs 一旦异常，
  auto-gc 会把所有对象判定为不可达并删除 pack。


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
