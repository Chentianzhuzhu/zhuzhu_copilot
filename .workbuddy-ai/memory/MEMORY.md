# 项目长期记忆

## 服务器资产
- **Redis 缓存节点**：`122.228.216.138:6379`，Debian 12/宝塔，Redis 7.0.15。ACL 账号 api_order/api_points/api_auth（key 前缀隔离）；6 个 Lua 在 `/etc/redis/scripts/`；ACL 持久化在 `/var/lib/redis/users.acl`——**ACL SAVE 后必查 default 密码还在**（aclfile 会覆盖 requirepass）。6379 白名单 = 应用服务器 IP + 127.0.0.1。
- **应用服务器**：`39.104.28.191`（阿里云 ECS/宝塔），auth-server(/www/auth-server) + Java + MySQL + 本机 Redis。L1/L2 已上线：积分走 L2 远程节点（Lua 原子扣减），**RTT 39ms 热路径严禁逐条循环**（pipeline）。SFTP 未启用，传文件用 base64+exec；连接脚本 `tmp_redis/app_ssh.py`（app）与 `sshrun.py`（redis）**不要混用**。异地备份：app 03:30 → RainYun，恢复见 `outputs/offsite_backup_setup.md`。
- **Git Bash 坑**：参数里的 `/root/xxx` 被 MSYS 转成 `C:/Program Files/Git/root/xxx`——命令前加 `MSYS_NO_PATHCONV=1` 或路径写死在脚本内。
- `chentian.dpdns.org` 未备案被阿里云网关抽样拦截（nginx access.log 记 499、响应 403 HTML 含 `beian-block`）；客户端已内置识别重试，偶发失败先 `grep -c ' 499 ' access.log`。

## 测试与运行约定（必守）
- **跑 pytest**：托管 venv 装不了 pytest，用**系统 Python313**（`C:/Users/zhuzhu/AppData/Local/Programs/Python/Python313/python.exe`，自带 pytest 9.1.1 + PyQt6，可 `QT_QPA_PLATFORM=offscreen` 离屏冒烟）。命令：`PYTHONPATH=src <py> -m pytest -c <临时ini> tests/...`——**`pytest.ini` 带 BOM 必须 `-c` 指临时 ini**。
- **改 `src/zhuzhu_Copilot/ui/agent_panel.py` 后必须同步到 `~/.zhuzhu_Copilot/source_overlay/zhuzhu_Copilot/ui/agent_panel.py` 并清该目录 `__pycache__`**（overlay hook 优先命中覆盖层，不同步则运行源码版不生效；安装版 .pyd 不含 hook 不受影响）。改动前先备份 `.bak_<ts>`。
- 改动前就失败的测试（勿误判为回归）：`test_context_settings_ui.py::test_settings_hint_follows_priority_chain`（ed3ebf7 把模型上下文表改 262,144 未同步测试期望 131,072）、`test_ui_longtask_fluency.py::test_op_status_text_covers_every_registered_tool`（新工具缺状态文案）。
- 跑源码版用根目录「运行源码版.bat」（开始菜单快捷方式指向 10/7 安装版 .pyd，不读源码）。

## UI/UX 约定（2026-10-25 五招美化 + 计时器/遮罩重构，详见根 `UIUX五招改动说明.md`）
- 色板三层：`styles.PALETTE`（bg/card/elevated/field，8bit 步进 5~12）与 `agent_panel._THEMES` 双轨；**全站不画 border:1px**，分层靠底色、聚焦靠背景加深/柔光。字重档位 `tokens.FONT_WEIGHT`（暗色降一档）；等宽数字栈 `tokens.NUMERIC_FONT_STACK`；动效注册表 `ui/motion.py`（hover/snap/settle/fade_out 四性格）。
- **AI 回合计时器 = 左上角纯文本行**（`fmt_elapsed`："已处理 X小时X分钟X秒"零档省略，等宽数字防抖），下方虚线、再是输出内容。无数据占位「已处理 --」。
- **Qt 父子合成陷阱（踩过）**：`WA_TranslucentBackground` 父控件的 paintEvent 内容落在子控件矩形内会被**清除**——顶部虚线必须由 `_TurnBox`（内容盒子类）自己画，改回父控件画会只剩右缘 9px。回归：`test_timer_row_sits_above_dash_line`。
- **保存遮罩时机**：`_save()` 必须先建遮罩→`start()`(grab 底图)→**`repaint()` 当帧上屏**→再做重活；高斯模糊 `singleShot(0)` 异步 + 底图降采样 1/4。`show()` 不入队绘制，不补 repaint 会被后续重活推迟。首帧实测 37ms（须 <500ms）。回归：`tests/test_settings_save_overlay.py`。
- 底图采集用 `grab()`（冷 20ms）不用 painter 缩放 `render()`（冷 96ms）。
- 设置页卡片布局：`_CardBoard` 卡片在上、按钮行在下；`_fit_height` 依赖 `_below_space()` 扣减。`_AgentSettingsDialog._section_label()` 类内禁止重名。

## 插件体系
- 两套：Agent 插件（`~/.zhuzhu_Copilot/plugins/`，扩展 AI Agent）与 Copilot 插件（`~/.zhuzhu_Copilot/copilot_plugins/`，源码级定制任何模块）。共用 `plugin_source.py`（五类 op）+ `plugin_guard.py`（导入/AI 生成前强制校验，内置唯一）。
- 源码级插件落 `~/.zhuzhu_Copilot/source_overlay/`，由 `_copilot_overlay_hook.py`（meta_path 最前置）优先命中；**停用插件后若覆盖层 .py 仍遮住源码，需手动删 overlay 副本再启用**。
- 开发约定：图标必须是包根方形 PNG 且 plugin.json 只写文件名；页面/托盘/快捷键需重启生效（services 即时）；改插件 bump `revision`；`source_ops` 改 agent_panel/main_window/styles 必须重启。
- 插件目录下不放备份（`list_plugins` 会把含 plugin.json 的子目录当插件）。

## 窗口 / 焦点 / 动画约定
- `window_fx.py` 顺序铁律：精灵先盖窗再 `showMinimized()`；动画中 `can_conceal/reveal` 返回 True；还原必须先 `unmaximize(win, QRect)`；`WA_TranslucentBackground` 背面缓冲不自动擦除，paintEvent 先 Clear 再画。
- 浮层关闭焦点归属：`session_combo` 用 ClickFocus + `_restore_focus_after_popover()` 交还焦点给输入框（回归 `test_input_row_affordances.py`）。
- `glass_window.py` 液体玻璃窗：ctypes 直连 opengl32，GLSL 1.12；抓屏前 `SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)` 排除自己。

## 用户偏好
- 中文沟通、交付物用中文；部署类任务倾向"直接做"（Agent 模式），仅网络暴露范围等安全分叉需要确认。
