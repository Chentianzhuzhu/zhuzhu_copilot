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
- **跑 pytest**：托管 venv（`binaries/python/envs/default`）装不了 pytest（pip 无网），**系统 Python313（`C:/Users/zhuzhu/AppData/Local/Programs/Python/Python313/python.exe`）自带 pytest 9.1.1**，核心模块（agent_plugins/guard/source/skills，无 PyQt6 依赖）可正常导入。命令：`PYTHONPATH=src <该python> -m pytest -c <临时ini> tests/...`（`pytest.ini` 带 BOM 必须 `-c` 指临时 ini）。PyQt6 未装，agent_engine/UI 相关测试在此环境不可跑。
- **插件体系（2026-10-22 扩展，2026-10-23 双体系化）**：`plugin_guard.py` 插件校验系统内置唯一不可替换（导入/AI 生成/源码应用前强制过：静态规则 + LLM 评审，危险强制删除 + `plugins/.guard_log.jsonl`；PROTECTED_CORE_FILES 含 plugin_guard/plugin_source/copilot_plugins/security/security_engine/_copilot_overlay_hook.py/main.py）；`plugin_source.py` 源码级插件引擎（source_ops 五类 op：create_file/append/insert_after/replace_module/replace_file，apply 前 guard、逐文件备份、编译失败整体回滚、applied.json 支持 revert）。插件包元数据规范：name/author/updated_at/icon。agent_plugins 对 plugin_guard/plugin_source 一律**函数内惰性导入**（避免循环依赖）；测试 LLM 替身注入 `plugin_guard._llm_review`。
- **两套插件体系（2026-10-23）**：Agent 插件（`agent_plugins.py`，`~/.zhuzhu_Copilot/plugins/`，只扩展 AI Agent：MCP/技能/web）与 **Copilot 插件**（`copilot_plugins.py`，`~/.zhuzhu_Copilot/copilot_plugins/`，应用级、范围更广：源码级定制任何模块 + copilot.json 注册页面/服务/托盘/快捷键 + 可选 agent/ 子包）。共用 plugin_source + plugin_guard。工具：create/apply/revert_copilot_plugin（agent_engine._NO_WAIT_TIMEOUT_TOOLS 同加入）。设置页导航「Agent 插件」/「Copilot 插件」两项。
- **打包态源码覆盖层（2026-10-23，关键机制）**：安装目录无源码，源码级插件写 `~/.zhuzhu_Copilot/source_overlay/`（镜像源码相对路径），由 `src/_copilot_overlay_hook.py`（meta_path 最前置 Finder，`main.py` 导入包前 install）让 `zhuzhu_Copilot.*` 优先命中覆盖层；`__file__` 指回原版（src 或随包快照 `_copilot_src/`，spec datas）保证资源解析不回退。revert 在还原内容==原版时删覆盖层副本。无覆盖层时 install() 返回 False 零侵入。
- **示例插件（教学用）**：`src/zhuzhu_Copilot/copilot_plugins_examples/`（theme-deep-blue / custom-hello-tool / settings-page-demo / tray-quick-note），锚点都取自真实文件且唯一；改动目标文件后示例的锚点可能失效（测试 `tests/test_plugin_copilot.py` 会抓到）。

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
