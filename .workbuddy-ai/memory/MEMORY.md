# 项目长期记忆

## 服务器资产
- **Redis 缓存节点**：`122.228.216.138:6379`，Debian 12 / 宝塔面板，Redis 7.0.15，2026-10-19 部署。密码与配置见 `outputs/redis_deployment_report.md`。用于官网 + 客户端 API 高并发缓存。
- **业务域 ACL 账号**：api_order / api_points / api_auth（订单、积分、登录三域，key 前缀隔离 + 独立密码，见 `outputs/redis_api_integration.md`）；6 个原子 Lua 脚本在服务器 `/etc/redis/scripts/`；健康检查 `/root/redis_healthcheck.sh`。FLUSHALL/FLUSHDB 已改名秘密命令，DEBUG 禁用。
- **ACL 持久化位置**：`/var/lib/redis/users.acl`（/etc/redis 只读放不了）。**ACL SAVE 后必查 default 用户密码还在**（aclfile 会覆盖 requirepass，曾导致 nopass 裸奔）。
- **应用服务器**：`39.104.28.191`（Alibaba Cloud Linux 3 / 宝塔），跑 FastAPI `auth-server`（/www/auth-server，redis-py 5.2.1）+ Java + MySQL + **本机 Redis(127.0.0.1，已有业务在用)**。Redis 节点 6379 防火墙只放行此 IP + 127.0.0.1。**L1/L2 已上线（2026-10-19）**：积分余额缓存+原子扣减走 L2 远程节点（`points:cache:{uid}` + Lua），会话/限流/state/pubsub 留本机；`.env` 删 5 行 REDIS_L2_* 即回滚。**RTT 39ms，热路径严禁逐条循环读写远程节点**（实测逐条 GET×50 = 2473ms vs pipeline 50ms）。auth-server 双向同步以本地仓库为准，服务器备份在 `/www/auth-server/.bak_l2_<ts>/`。
- 该机器是宝塔面板环境（nginx + PHP 8.2 + MySQL），**SFTP 子系统未启用**，传文件需用 base64 + exec 通道；远程操作 SSH 用 paramiko 系统 Python（自带 paramiko 5.0.0）。应用服务器连接脚本 `tmp_redis/app_ssh.py`，Redis 节点是 `tmp_redis/sshrun.py`——**两台机器的连接脚本不要混用**。
- **异地备份**：app 03:30 推送 → RainYun `/home/bkpush/backups/`（rrsync 沙箱锁定，仅能写该目录）；RainYun 04:30 校验+清 7 天前。恢复流程见 `outputs/offsite_backup_setup.md`。注意：**app 服务器的 nginx 是系统级 /etc/nginx**（非宝塔路径）。
- **Git Bash 坑**：命令行参数中的 `/root/xxx` 会被 MSYS 转成 `C:/Program Files/Git/root/xxx` 传给 python，再拼进远程命令就会报 `C:/Program: No such file or directory`——命令前加 `MSYS_NO_PATHCONV=1`，或路径写死在脚本内。

## 技术约定
- 宝塔机器部署 Redis 的 4 个坑及修复见 `memory/2026-10-19.md`（符号链接 ExecPaths、/usr/local/lib jemalloc 白名单、unix socket、日志权限）。
- `pkill -f <name>` 会匹配 SSH 命令自身导致会话中断，改用 `pkill -x`。
- Redis 节点 6379 白名单 = 应用服务器 IP；若应用服务器 IP 变更需同步 `ufw`。
- **跑 pytest**：托管 venv（`binaries/python/envs/default`）装不了 pytest（pip 无网），**系统 Python313（`C:/Users/zhuzhu/AppData/Local/Programs/Python/Python313/python.exe`）自带 pytest 9.1.1 与 PyQt6**（2026-10-24 实测，此前"PyQt6 未装"结论过时），UI 模块可离屏冒烟（`QT_QPA_PLATFORM=offscreen`）。命令：`PYTHONPATH=src <该python> -m pytest -c <临时ini> tests/...`（`pytest.ini` 带 BOM 必须 `-c` 指临时 ini）。
- **设置页卡片布局约定（2026-10-24 定稿）**：`_CardBoard` 卡片看板在**上**、交互按钮行在**下**（先浏览内容再操作；插件/Copilot插件/工作流含预设行/Agent 子 Agent 四页均已如此）。`_CardBoard._fit_height` 依赖 `_below_space()` 为下方按钮行预留高度——**若再改回按钮在上，必须同步去掉该扣减**，否则卡片区留下大片空白。`_AgentSettingsDialog._section_label()` 是分组小节标题唯一实现（DIM 12px 加粗 + margin-top 8px），**类内禁止再定义同名方法**（后定义覆盖前定义）。
- **插件体系（2026-10-22 扩展，2026-10-23 双体系化）**：`plugin_guard.py` 插件校验系统内置唯一不可替换（导入/AI 生成/源码应用前强制过：静态规则 + LLM 评审，危险强制删除 + `plugins/.guard_log.jsonl`；PROTECTED_CORE_FILES 含 plugin_guard/plugin_source/copilot_plugins/security/security_engine/_copilot_overlay_hook.py/main.py）；`plugin_source.py` 源码级插件引擎（source_ops 五类 op：create_file/append/insert_after/replace_module/replace_file，apply 前 guard、逐文件备份、编译失败整体回滚、applied.json 支持 revert）。插件包元数据规范：name/author/updated_at/icon。agent_plugins 对 plugin_guard/plugin_source 一律**函数内惰性导入**（避免循环依赖）；测试 LLM 替身注入 `plugin_guard._llm_review`。
- **两套插件体系（2026-10-23）**：Agent 插件（`agent_plugins.py`，`~/.zhuzhu_Copilot/plugins/`，只扩展 AI Agent：MCP/技能/web）与 **Copilot 插件**（`copilot_plugins.py`，`~/.zhuzhu_Copilot/copilot_plugins/`，应用级、范围更广：源码级定制任何模块 + copilot.json 注册页面/服务/托盘/快捷键 + 可选 agent/ 子包）。共用 plugin_source + plugin_guard。工具：create/apply/revert_copilot_plugin（agent_engine._NO_WAIT_TIMEOUT_TOOLS 同加入）。设置页导航「Agent 插件」/「Copilot 插件」两项。
- **打包态源码覆盖层（2026-10-23，关键机制）**：安装目录无源码，源码级插件写 `~/.zhuzhu_Copilot/source_overlay/`（镜像源码相对路径），由 `src/_copilot_overlay_hook.py`（meta_path 最前置 Finder，`main.py` 导入包前 install）让 `zhuzhu_Copilot.*` 优先命中覆盖层；`__file__` 指回原版（src 或随包快照 `_copilot_src/`，spec datas）保证资源解析不回退。revert 在还原内容==原版时删覆盖层副本。无覆盖层时 install() 返回 False 零侵入。
- **示例插件（教学用，9 个）**：`src/zhuzhu_Copilot/copilot_plugins_examples/`（theme-deep-blue / custom-hello-tool / settings-page-demo / tray-quick-note / py-syntax-lint / prompt-optimizer / agent-scorecard / perf-turbo / code-map），锚点都取自真实文件且唯一；改动目标文件后示例的锚点可能失效（测试 `tests/test_plugin_copilot.py` 会抓到）。
- **插件开发约定（2026-10-25 定稿，详见根 README 第八章）**：图标 `icon` 必须是**包根**的方形 PNG（128~256px），`plugin.json` 里**只写文件名**（带目录前缀或 `.` 开头会被清空）；**页面/托盘/快捷键在应用启动时登记**（`MainWindow._load_copilot_extensions`），启用后必须重启才看得到，只有 `services` 会在启用时即时重启；`source_ops` 改 `agent_panel`/`main_window`/`styles` 必须重启（热加载跳过）。
- **转译层快照陷阱（踩过）**：插件启用后若又改了被覆盖的源码文件，停用时 `revert` 因「备份 ≠ 当前源码」会把**旧备份写回覆盖层**（而不是删副本）→ 覆盖层继续遮住源码改动。正确恢复顺序：停用插件 → **手动删除 `~/.zhuzhu_Copilot/source_overlay/` 下的 .py** → 重新启用（以当前源码为基准重新插入；直接重复启用会插重复）。
- **exe 转译层验证**：`scripts/verify_overlay.py`（`--simulate` 隔离模拟 frozen：覆盖层优先 / `__file__` 指回 `_copilot_src` / 无覆盖层零侵入；`--inspect DIR` 体检安装目录）。**仓库 `dist/zhuzhu Copilot`（2026-10-19 构建）无 `_copilot_src/`，不含覆盖层机制，源码级插件在它上面一定不生效**，需 `.\build_release.ps1` 重建。
- **插件开发构建脚本**：`tmp_perf/build_ops.py`（perf-turbo）、`tmp_code_map/build_ops.py`（code-map）从 `source/*.py` 生成 `source_ops.json` 并在 src 临时副本上真跑 op + py_compile + 落位检查 + 静态规则扫描 + 子进程内核自测；**改了 source 副本必须重跑，否则清单与副本不一致**。

## 技术约定
- 宝塔机器部署 Redis 的 4 个坑及修复见 `memory/2026-10-19.md`（符号链接 ExecPaths、/usr/local/lib jemalloc 白名单、unix socket、日志权限）。
- `pkill -f <name>` 会匹配 SSH 命令自身导致会话中断，改用 `pkill -x`。
- 该节点当前 6379 对全网开放，待用户提供应用服务器 IP 后应改为 UFW 白名单。

## 用户偏好
- 用户以中文沟通，交付物用中文。
- 部署类任务倾向"直接做"（Agent 模式），仅网络暴露范围等安全/架构关键分叉需要确认。

## zhuzhu Copilot 客户端（本仓库）约定
- **技能真源 = 插件 SKILL.md**：`~/.zhuzhu_Copilot/agent/skills/<名>/SKILL.md` 只是插件 SKILL.md 的投影。`ensure_md_skills()` 里任何"清理旧技能"的名单都不得包含插件正在提供的名字（`computer-control` 曾因 2026-08 的遗留清理被每次启动 rmtree，导致模型拿不到操控纪律、乱猜坐标）。
- **插件目录下不要放备份/临时目录**：`list_plugins()` 会把 `plugins/` 下任何含 plugin.json 的子目录当成插件，同名会顶掉真插件（备份请放 `~/.zhuzhu_Copilot/backups/`）。
- **改插件要 bump `plugin.json` 的 `revision`**：`ensure_shipped_plugins()` 只在随包 revision 更大时才同步到用户目录并重登记 MCP/技能，否则改了 src 也不生效。改完 server.py 需重启 App（MCP 进程常驻）。
- **任务路由双份表**：`agent_engine._TASK_GROUPS`（工具裁剪）+ `agent_skills._SKILL_KEYWORDS`（技能注入）。桌面/网页消歧词表统一在 agent_skills（`DESKTOP_STRONG_KWS`/`WEB_STRONG_KWS`/`gui_automation_intent`），两边都引用同一份，别再各写一套。
- **`pytest.ini` 带 BOM 会导致 pytest 启动报错**：临时跑测试用 `-c <临时ini>` + `PYTHONPATH=src`。`tests/test_prompt_i18n.py` 与 `tests/test_agent_plugin_context.py` 有 4 个改动前就失败的用例（`efficient_mode` 属性缺失）。

## auth-server 边缘网络（2026-10-22 起）
- `chentian.dpdns.org` 未备案 + 大陆阿里云 ECS(39.104.28.191) + CF 前置：阿里云网关**抽样拦截**
  未备案域名——请求会到 nginx（access.log 记 **499 / 0 字节**），响应被换成 403 +
  `Non-compliance ICP Filing` 页。浏览器走 HTTP/3(UDP) 不受影响，客户端 TCP 请求约一半中招。
- 判据：403 响应体是 HTML 且含 `beian-block` / `aliyun.com/beian`；无 `cf-mitigated` 头（不是 CF WAF）；
  应用自身的 403 一律是 JSON。详见 `memory/2026-10-22.md`。
- 客户端已内置识别+重试（auth_client._request_json / http_get_bytes，8 次退避重试），
  扣积分接口带 request_id 幂等键（Redis `points:idem:{uid}:{rid}`，TTL 600s）。
- 根治：CF Tunnel 或源站换 8443 端口或备案；在未根治前，任何"客户端 API 偶发失败"先查
  `grep -c ' 499 ' /var/log/nginx/access.log` 是否在涨。

## Cloudflare Turnstile 人机验证（2026-10-25 上线）
- **口径**：只用于**应用账户登录/注册**（auth-server 登录页），市场登录**不启用**。
  密钥：site `0x4AAAAAAFkPdJ6msoFqH2a4`，secret `0x4AAAAAAFkPdLOg8IrlZXj8ZlDbT0s6YNU`（已实测有效）。
- **auth-server**：`app/services/turnstile.py`（urllib + to_thread，零新依赖，fail-closed）；
  `GET /api/auth/captcha` 下发 `{enabled, sitekey}`；register/login 校验 `turnstile_token`；
  `.env` 需 `TURNSTILE_ENABLED/SITE_KEY/SECRET_KEY`。登录页 `static/login/index.html`
  在 tabs 下方共用一块验证组件，切 tab 与提交失败都 `cfReset()`（token 一次性）。
- **站点密钥只对 CF 控制台绑定的域名生效**：域名 `chentian.dpdns.org`，登录页对外地址
  `https://chentian.dpdns.org/authapp/static/login/index.html`（nginx 反代 → 127.0.0.1:8000）。
  **用 IP 直连会拒绝渲染**，所以市场服务（IP:8081）不能直接上验证。
- **market-server**：Turnstile 代码保留但 `market.turnstile.enabled` 默认 false（verify 直接放行），
  客户端 `plugin_market.register/login` 的 `turnstile_token` 改为可选。市场登录后隐藏
  「登录/注册」按钮，页面不再显示 api_base。
- 限流必须排在 Turnstile 校验**之前**（每次 verify 都要外呼 Cloudflare）。
- auth-server 部署：`systemctl restart auth-server`（venv python3.11，uvicorn 2 workers，
  监听 127.0.0.1:8000）；文件用 paramiko exec + base64 stdin 通道上传，备份在
  `/www/auth-server/.bak_turnstile_<ts>/`。
- 本地构建市场 jar：无 mvn，临时下载 apache-maven 到 `/tmp/mvnhome`，`~/.m2` 有依赖缓存；
  **Java 源文件不能带 UTF-8 BOM**（javac 报 illegal character）。

## 浮层 / 下拉的焦点归属（2026-10-25）
- **症状**：点顶栏用户胶囊打开个人信息面板、再关掉，历史对话下拉会亮起一圈蓝色
  focus 描边（用户没碰过它）。
- **根因**：`QComboBox` 默认 `WheelFocus`；浮层隐藏时 Qt 按焦点链把焦点丢给下一个可
  聚焦控件，正好是顶栏的 `session_combo`，而 `_HOME_COMBO` 里有
  `QComboBox:focus { border-color: ACCENT_HOVER }`。
- **修法（两条一起，缺一不可）**：
  1) `session_combo.setFocusPolicy(ClickFocus)` —— 焦点链 / Tab 都跳过它，只有鼠标
     点击才聚焦（点开列表仍有正常高亮）；
  2) `close_profile_panel` 末尾调 `_restore_focus_after_popover()` —— 仅当焦点悬空或
     还留在正在消失的浮层内时，把焦点交回聊天输入框 `self.input`；焦点已在用户刚点
     的别的控件上就不动（不抢焦点）。
- 回归用例：`tests/test_input_row_affordances.py::test_profile_panel_close_keeps_focus_off_session_combo`。

## 窗口折叠动画（ui/window_fx.py）不可破的约定
- **顺序铁律：精灵先盖住窗口，再执行 `showMinimized()`**（`_play(before=...)` 回调）。
  反序就是「卡顿一下→没播动画→最小化」+ 全尺寸快照「重播」一次。
- **动画中（`_busy`）`can_conceal()/can_reveal()` 必须返回 True** 吞掉重复系统命令；
  返回 False 会让消息下发给原生窗口，把正在跑的折叠直接掐断。
- `setWindowState(WindowNoState)` **不恢复几何**；还原大小必须先 `unmaximize(win, QRect(target))`
  （setWindowState → showNormal 兜底 → setGeometry），否则会停在「全屏尺寸但未 zoomed」的坏状态，
  之后最大化按钮彻底失效。
- `_toggle_max_restore` 在 `_fx.busy()` 时直接 return：否则额外点击穿透到原生 showMaximized/
  showNormal 与形变互斗，叠加主线程 137~672ms 重排 = 假死。
- 分层窗口（`WA_TranslucentBackground`）**背面缓冲不自动擦除**：`FoldSprite.paintEvent` 必须
  先 `CompositionMode_Clear` + `fillRect(e.rect())` 再画，否则折叠收缩时腾空区残留上一帧 → 拖影。
- `_restore_window` 末尾必须 `w.repaint()`，防止精灵退场瞬间露出桌面。
- 探测/验证坑：跨进程 `SendMessage(WM_SYSCOMMAND)` 到不了 Qt native filter（用 `PostMessage`）；
  屏幕 BitBlt 读回 ~70ms/帧追不上 230ms 动画，逐帧验证要在进程内用 `render()`。
- **Copilot 插件热插拔（2026-10-25）**：`main_window.unregister_copilot_plugin()` 摘页签/玻璃窗/托盘/快捷键，`start_copilot_plugin()` 面板缺省回落 `_COPILOT_EXT_STATE["host"]`；`stop_copilot_plugin` **先摘 UI 再停服务**。页签移除必须 `setParent(None)` 再 `deleteLater()`（否则子控件还挂在父级上）。回归 `tests/test_copilot_hotplug.py`。
- **液体玻璃窗口 `ui/glass_window.py`**：无框置顶 GPU 玻璃窗（SDF 法线→透镜折射→RGB 色散→菲涅尔亮边→Blinn 高光→正弦液态扰动）。PyQt6 无 QOpenGLFunctions、Qt6 ShaderEffect 需 .qsb → **ctypes 直连 opengl32/wglGetProcAddress**，GLSL 1.20 + 固定管线。三坑：背景贴图要比窗口大 pad 圈；单四边形关 GL_BLEND 且输出前 clamp(col)；抓屏前 `SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)` 排除自己。`grabWindow()` 返回 QPixmap 要 `.toImage()`。`GLASS_MODE=painter` 强制降级。验收 `GLASS_TEST_GL=1 pytest tests/test_glass_shader.py`（conftest 默认离屏无 GL，故默认 skip）。
- **同步插件包别覆盖 plugin.json**：会冲掉 source_applied 等运行时字段；只同步 README/copilot.json/图标，plugin.json 要合并。
