# zhuzhu Copilot Mobile · 设计文档

日期：2026-11-01
状态：已与用户逐节确认，待评审

> 范围：Android 手机端 Agent 首个版本（主力版）。复用桌面端 Agent 能力模型（对话 + 工具循环 + 风险确认），以无障碍服务实现手机 GUI 自动化，界面采用 iOS 风格液态玻璃材质。交付物为可直接在 Android Studio 打开的 `mobile/` 工程与操作手册，自签名 release 打包由用户在 Android Studio 内完成。

## 1. 设计决策记录（已确认）

| 决策点 | 结论 |
| --- | --- |
| 信息架构 | B 骨架：对话 / 任务 / 设置三标签常驻 |
| 视觉材质 | iOS 风格液态玻璃：边缘高光 + 液态光带边框 + 内部磨砂，标准档为主档 |
| 模型接入 | 双通道：内置账号（复用桌面端 agnes 与积分）+ 用户自填 OpenAI 兼容 Key |
| GUI 自动化 | 节点优先（AccessibilityNodeInfo），节点失败才降级截图 + 视觉模型 |
| 首版范围 | 主力版：对话、无障碍工具、任务页、权限引导、设置、语音输入/可选播报、定时任务、快捷指令、历史与本地记忆 |
| 执行可视化 | 玻璃 HUD 胶囊 + 目标节点高亮框，均可随时中止 |
| 权限深度 | 全系统深度：通知内容、短信验证码、剪贴板、应用列表 |
| 确认策略 | 默认仅危险/敏感操作确认，可在设置改为每步确认或全自动 |
| 语音 | 按住说话 + 系统语音识别；TTS 播报默认关闭可在设置开启；不做常驻唤醒词 |
| 语言 | 中英双语，全部文本走 strings.xml |
| 工程路线 | 模块化重架构：多 Gradle 模块 + Hilt + Repository/UseCase |
| 构建目标 | minSdk 26，compileSdk/targetSdk 36，自签名 release（侧载，不上架商店） |

## 2. 总体架构

### 2.1 Gradle 模块与依赖

共 12 个 Gradle 模块：`app` 1 个、`core/*` 6 个、`feature/*` 3 个、`service/*` 2 个。

```
mobile/
├── app/                       Application、MainActivity、导航、Hilt 聚合
├── core/
│   ├── model/                 纯 Kotlin 模型，零 Android 依赖
│   ├── common/                Result、Dispatcher、时间、脱敏日志
│   ├── llm/                   OpenAI 兼容 SSE 客户端、工具 schema、错误映射
│   ├── agent/                 Agent 循环、工具注册表、风险策略、确认网关
│   ├── data/                  Room、DataStore、加密存储、Repository
│   └── ui/                    主题、色板、GlassSurface、图标、通用组件
├── feature/
│   ├── chat/                  对话页 + ViewModel
│   ├── tasks/                 任务页 + ViewModel
│   └── settings/              设置页、权限引导、模型配置、账号登录
└── service/
    ├── accessibility/         无障碍服务、节点快照、动作执行、HUD/高亮 overlay
    └── automation/            前台服务、通知监听、MediaProjection、WorkManager
```

依赖方向（禁止反向依赖）：

```
app → feature/* → core/ui, core/agent, core/data
app → service/automation → core/agent, core/data, service/accessibility
service/accessibility → core/agent, core/ui
core/agent → core/llm, core/model
core/llm → core/common, core/model
core/data → core/model, core/common
core/ui → core/model
```

`core/agent` 只定义 `AgentTool` 接口、`ToolRegistry`、`RiskPolicy`、`ConfirmationGateway`，不依赖任何具体工具实现。各模块通过 Hilt `@IntoSet` 贡献 `AgentTool`，由 `service/automation` 注入 `Set<AgentTool>` 构造注册表。新增工具只改对应模块，不动引擎。

### 2.2 技术选型

- Kotlin + Jetpack Compose + Material 3（只取基础组件，视觉全部自绘）
- Hilt 依赖注入；Repository + UseCase 分层
- Room 持久化；DataStore 存 UI 设置；EncryptedSharedPreferences（Keystore）存密钥
- OkHttp + 自实现 SSE 流式解析；`kotlinx.serialization` 解析工具调用
- 玻璃背景模糊采用 Haze（跨版本 `Modifier.hazeEffect`），高光/光带/颗粒由自绘 `GlassSurface` 叠加
- 语音：系统 `SpeechRecognizer` + 系统 `TextToSpeech`
- 定时任务：WorkManager
- 图标：Material Symbols（Apache 2.0），24dp 网格、1.4dp 描边

依赖版本在初始化时由 Android Studio 模板生成后锁定到 `gradle/libs.versions.toml`，不在设计文档中硬编码具体小版本；Haze 锁定单个稳定版本，升级需单独验证模糊性能。

## 3. Agent 核心

### 3.1 循环

每轮四拍：观察 → 决策 → 执行 → 校验。

1. **上下文组装**：系统提示（设备信息、已授权能力、工具目录、风险规则、防注入规则）+ 最近 N 轮消息 + 压缩摘要 + 当前屏快照（按需以 `snapshot_screen` 工具输出，而非每轮自动塞入）
2. **决策**：流式请求 LLM（function calling）。工具 schema 按当前授权过滤，未授权能力不下发给模型
3. **执行**：按风险分级串行执行；只读工具可并发；每个工具可被取消
4. **校验**：动作后重新取快照比对窗口指纹，判定是否生效；失败如实上报并重试（最多 2 次，退避 300ms / 800ms）

终止条件：模型给出最终文本回答、达到步数上限（默认 30，可配置）、总时长上限（默认 10 分钟，可配置）、用户中止、不可恢复错误。

### 3.2 事件流

`Flow<AgentEvent>` 单向输出，UI 只消费不回调：

`Thinking` / `TokenDelta` / `ToolStarted` / `ToolProgress` / `NeedConfirm` / `ToolResult` / `TaskUpdated` / `Done` / `Error`

取消：引擎持有 `Job`，`cancel()` 后每个工具在执行前与执行中检查取消标志；HUD 中止按钮直接触发同一取消入口。

### 3.3 上下文管理

- token 估算：中文按 1.5 token/字、英文按 4 字符/token 的折中口径
- 压缩阈值按模型上下文窗口的 75% 触发，保留最近 6 轮原文 + 结构化摘要
- 压缩后插入摘要消息并标注 `compressed=true`，UI 显示可展开的“已压缩历史”
- 上下文超限兜底：发送前裁剪最旧的非系统消息，绝不发出超窗请求

### 3.4 风险策略

| 等级 | 判定 | 默认行为 |
| --- | --- | --- |
| `safe` | 读取类：快照、查找、系统信息、电池、网络 | 直接执行，可并发 |
| `sensitive` | 写剪贴板、输入文本、读通知、读短信、打开系统设置 | 弹确认，支持“本次会话允许” |
| `destructive` | 支付、转账、删除、卸载、授权、发送验证码、发送消息 | 强制确认，**不提供“始终允许”** |

用户可在设置切换为“每步确认”或“全自动”。全自动模式下 `destructive` 仍然拦截（不可关闭的安全底线）。

并发规则：仅 `safe` 且声明为只读的工具可并发；所有会产生界面变化的动作类工具（点击、输入、滚动、手势、系统动作）**一律串行**，避免同一窗口上的竞态。

### 3.5 工具清单（主力版）

| 工具 | 风险 | 说明 |
| --- | --- | --- |
| `list_apps` | safe | 列出可启动应用（应用名 + 包名） |
| `open_app` | sensitive | 按包名或应用名拉起 |
| `get_current_app` | safe | 当前前台包名与 Activity |
| `system_action` | sensitive | home / back / recents / notifications / quick_settings |
| `set_quick_setting` | sensitive | wifi / bluetooth / flashlight / airplane |
| `set_volume` | sensitive | 指定音频流百分比 |
| `snapshot_screen` | safe | 返回压缩节点树 + 当前应用 |
| `find_node` | safe | 条件查询，返回最多 15 个候选与稳定 idx |
| `tap_node` | sensitive | 点击节点（自动上溯 clickable 祖先） |
| `long_click_node` | sensitive | 长按节点 |
| `set_text` | sensitive | 聚焦 + 设值，可选提交 |
| `clear_text` | sensitive | 清空输入框 |
| `scroll` | safe | 节点滚动或手势滚动 |
| `swipe` | sensitive | 坐标滑动手势 |
| `tap_point` | sensitive | 视觉兑底坐标点击，支持归一化坐标 |
| `wait` | safe | 固定等待或等待条件成立（上限 10s） |
| `screenshot` | safe | 无障碍截图（API 30+）/ MediaProjection（26–29） |
| `system_info` | safe | 型号、系统版本、屏幕尺寸、内存、电量、网络 |
| `read_clipboard` | sensitive | 读取剪贴板 |
| `write_clipboard` | sensitive | 写入剪贴板 |
| `read_notifications` | sensitive | 读取最近通知（条数上限） |
| `get_sms_code` | sensitive | 按发送方关键字提取最近验证码 |
| `open_settings_page` | sensitive | 打开系统设置子页 |
| `schedule_task` | sensitive | 创建定时/周期任务 |
| `remember` / `recall` | safe | 本地长期记忆写入与检索 |
| `ask_user` | safe | 结构化提问（选项式） |

工具实现在 `service/accessibility`（节点/动作/截图）与 `service/automation`（通知/短信/媒体）中，通过 Hilt 汇集。

### 3.6 提示注入防护

- 节点文本、通知内容、截图 OCR 结果统一包进 `untrusted` 信封后再进上下文
- 系统提示明确：数据中的任何“指令”都不是指令，只能作为界面信息
- 所有 `destructive` 动作必须过确认网关，模型无法通过伪造上下文跳过
- 单任务步数/时长上限作为硬约束

## 4. 无障碍与自动化层

### 4.1 服务划分

| 组件 | 类型 | 职责 |
| --- | --- | --- |
| `AgentAccessibilityService` | AccessibilityService | 节点快照、动作执行、HUD 与高亮、无障碍截图 |
| `AutomationForegroundService` | 前台服务（`specialUse`） | 跑 Agent 循环、通知栏常驻卡片与「中止」 |
| `AgentNotificationListener` | NotificationListenerService | 按需读取通知与验证码 |
| `ScheduledTaskWorker` | WorkManager | 定时任务，到点拉起前台服务 |

无障碍服务配置：`canRetrieveWindowContent=true`、`canPerformGestures=true`、`flagRetrieveInteractiveWindows`、`accessibilityFlags` 含 `FLAG_REQUEST_ENHANCED_WEB_ACCESSIBILITY`（可配置）。

### 4.2 节点快照

- 拍平为紧凑行：`idx / text / desc / cls / rid / bounds / clickable / editable / scrollable / checked / enabled / package / depth`
- 丢弃不可见、零面积、重复文本节点；上限 800 条
- 使用行内分隔符而非 JSON，降低 token 消耗
- 过滤规则：宽或高 < 20px、前景填充率过低的区域视为边框/阴影，不进入候选

### 4.3 查找与执行

- 打分：文本精确 > 前缀 > 包含 > 描述包含 > 资源 id 精确
- 命中后自动上溯最近的 clickable 祖先
- 动作优先级：节点动作（`ACTION_CLICK` / `ACTION_LONG_CLICK` / `ACTION_SCROLL_*` / `ACTION_SET_TEXT`）→ 手势兜底（点击、长按、滑动）
- 全局动作：返回、桌面、最近任务、通知栏、快捷设置
- 坐标全部换算为屏幕物理像素，旋转、分屏、窗口尺寸变化时重新测量

### 4.4 视觉兑底

- 截图来源：`AccessibilityService.takeScreenshot()`（API 30+）；API 26–29 回退 MediaProjection，仅在该场景临时申请
- 图片附带真实像素尺寸，要求视觉模型输出 0–1000 归一化坐标，落点前用 `seen_w / seen_h` 自校准
- `FLAG_SECURE` 窗口无法截图：降级为纯节点模式或如实报错
- 每 3 次截图失败后暂停并提示用户，禁止盲点

### 4.5 校验与等待

动作完成后固定等待 ≥1 秒，再取快照并比对窗口指纹（包名 + Activity + 节点数 + 文本哈希）。指纹未变视为未生效，返回失败并重试；连续两次无变化即停止该步骤。

### 4.6 HUD 与高亮

- HUD：左上玻璃胶囊，可触摸，显示当前动作、步骤进度与「中止」
- 高亮：全屏 `TYPE_ACCESSIBILITY_OVERLAY` 窗口，`FLAG_NOT_TOUCHABLE`，圆角描边 + 呼吸动画，跟随目标节点 bounds
- 目标包切换或旋转时 50ms 内重绘
- 两者均不需要「显示在其他应用上层」权限

### 4.7 权限与降级

| 权限 | 用途 | 缺失时 |
| --- | --- | --- |
| `BIND_ACCESSIBILITY_SERVICE` | 核心自动化 | 引导页直达系统开关，不伪造 |
| 通知使用权 | 通知内容、验证码 | 相关工具不下发 |
| `READ_SMS` / `RECEIVE_SMS` | 短信验证码 | 同上 |
| `RECORD_AUDIO` | 语音输入 | 输入框隐藏麦克风 |
| `QUERY_ALL_PACKAGES` | 应用列表 | 只能按包名盲开 |
| `POST_NOTIFICATIONS` | 常驻通知 | 降级普通通知 |
| 前台服务 `specialUse` | Agent 运行 | 无法后台执行 |

### 4.8 异常处理

- 用户抢操作 → 立即暂停，HUD 提示“检测到人工操作，已暂停”
- 目标 App 被杀 → 尝试重新拉起并回到断点步骤
- 锁屏 → 挂起等待解锁
- 连续 3 次失败 → 终止任务并在任务页记录原因
- 关闭无障碍服务 → 立即停止所有自动化并清除 overlay

## 5. 视觉规范

### 5.1 颜色

| 令牌 | 值 | 用途 |
| --- | --- | --- |
| `bg.base` | `#000000` | 全局底色 |
| `bg.panel` | `#0B0F14` | 非玻璃容器 |
| `bg.panelAlt` | `#12171E` | 次级面板 |
| `glass.tint` | 白 3% → 14% | 玻璃本体 |
| `rim.gradient` | 205° conic：白 78% → 白 10% → 蓝 42% → 白 62% → 白 78% | 液态边框 |
| `blue.primary` | `#27407A` | 主色 |
| `blue.highlight` | `#3B5BA5` | 激活态 |
| `blue.soft` | `#7FA0DC` | 高亮描边 |
| `text.primary` | `#E6E8EB` | 正文 |
| `text.secondary` | `#9AA3AE` | 说明 |
| `text.tertiary` | `#6B747F` | 占位 |
| `state.success` | `#6FA57F` | 已授权 |
| `state.warning` | `#C9A77E` | 未授权、暂停 |
| `state.danger` | `#C9736E` | 失败、中止 |
| `divider` | 白 6% | 分隔线 |

### 5.2 字体、间距、圆角

- 字号/行高：20/28、16/24、14/20、12/16、11/14；固定行高，不随内容拉伸
- 数字使用等宽数字（步数、耗时、积分）
- 字重仅 Regular / Medium / SemiBold
- 4dp 栅格：4 / 8 / 12 / 16 / 24；页面左右 16，卡片内边距 12，组间距 16
- 圆角：卡片 12、面板 16、HUD 与弹层 22、胶囊 999
- 所有尺寸与圆角集中在 `GlassTokens.kt` 单点定义

### 5.3 玻璃材质

| 参数 | Quiet | Standard（默认） | Vivid |
| --- | --- | --- | --- |
| 背景模糊 | 16 | 22 | 26 |
| 饱和度 | 135% | 175% | 205% |
| 液态边框 | 1.2dp / 45% | 1.2dp / 全亮 | 1.2dp / 带蓝紫色散 |
| 内层柔光 | 1dp 白 20% | 1dp 白 24% | 1dp 白 34% |
| 颗粒覆盖 | 12% | 15% | 15% |
| 外部投影 | 0/10/26 黑 55% | 同左 | 0/14/34 黑 60% |

降级：API < 31 或低内存设备自动落 Quiet 并关闭背景模糊；省电模式停用呼吸动画；同屏最多 2 个模糊层；列表快速滑动时玻璃临时切换为纯半透明。

### 5.4 动效

- 玻璃出现：0.96→1 + 淡入 200ms，`cubic-bezier(0.2, 0, 0, 1)`
- HUD 滑入 320ms，退出 200ms
- 高亮呼吸 2.2s；节点移动位置插值 120ms
- 流式文本不改变行高；三点指示 1.2s
- 尊重系统「移除动画」

### 5.5 组件

`GlassSurface`、`GlassTopBar`、`GlassTabBar`、`GlassMessageBubble`、`ToolCallCard`、`ConfirmSheet`、`GlassInputBar`、`StatusBadge`、`TaskCard`、`SegmentedGlass`、`GlassToggle`、`HudCapsule`、`TargetHighlight`、`GlassSnackbar`、`EmptyState`。

图标统一 Material Symbols 线性图标，按主题反色；禁止 emoji 与自绘图标。

### 5.6 页面与首启流程

- 三标签：对话 / 任务 / 设置
- 首启 5 步：欢迎 → 无障碍授权 → 通知与短信 → 语音与麦克风 → 模型配置 → 完成
- 必覆盖状态：空会话、流式输出、等待确认、执行中、已暂停、失败重试、离线、未授权、积分不足

### 5.7 可访问性与性能

- 触控目标 ≥ 48dp；正文对比度 ≥ 4.5:1；图标带 `contentDescription`；流式与步骤切换播报无障碍事件
- 聊天列表滑动 60fps；HUD 重绘 ≤ 50ms；冷启动不出现空白玻璃

## 6. 数据与隐私

### 6.1 Room 表

`conversation` / `message` / `tool_call` / `task` / `task_step` / `memory` / `scheduled_task` / `provider`。

- API Key 与账号 token 只存 EncryptedSharedPreferences，Room 仅存展示信息与密钥引用名
- 会话默认保留 90 天，可清空、可按会话导出 JSON / Markdown（SAF）
- `memory` 写入前去重，按关键词 + 最近使用时间检索
- `scheduled_task` 只存提示词与规则，WorkManager 持有任务 id

### 6.2 隐私

- 屏幕文本、通知、验证码仅在当次请求内存中使用，默认不落盘
- 诊断日志默认关闭；开启后对手机号、验证码、地址脱敏
- 首启声明无障碍能力与数据用途，随时可关闭
- 卸载或关闭无障碍后无残留后台行为

### 6.3 模型接入

- 双通道：内置账号（登录页 WebView，复用 auth-server，platform 标识 `zhuzhu-copilot-mobile`，按 token 扣积分）+ 自填 Key
- 服务商预设存 `assets/providers.json`（默认 agnes、DeepSeek、通义、Kimi、豆包、MiniMax），可增删而不改代码
- 内置账号服务端未登记新 platform 时，自动隐藏登录入口、仅保留自填 Key，不阻塞使用

## 7. 构建与交付

### 7.1 工程配置

- 包名 `com.zhuzhu.copilot.mobile`，应用名 `zhuzhu Copilot Mobile`
- `minSdk 26`、`compileSdk 36`、`targetSdk 36`
- `debug` 不混淆；`release` 开 R8 + 资源压缩，补 `kotlinx.serialization` 与 OkHttp keep 规则
- 签名参数只从 `keystore.properties`（`.gitignore` 忽略）或环境变量读取
- 工程位于仓库 `mobile/`，独立 Gradle 工程，与桌面端互不影响

### 7.2 用户操作手册（`mobile/README.md`）

1. Android Studio → Open → 选 `mobile/` → 等 Gradle Sync
2. SDK Manager 确认已装 Platform 36、Build-Tools 36.0.0、Platform-Tools；JDK 用 Studio 自带 JBR
3. Device Manager 建 Pixel 8 / API 36 模拟器，或手机开 USB 调试；Run `app`
4. 调试：断点打在 Agent 循环与工具执行；Logcat 按 `AgentEngine` / `Accessibility` / `Overlay` 过滤；系统设置里可反复开关无障碍服务验证
5. 自签名：Build → Generate Signed App Bundle or APK → APK → Create new keystore（RSA 2048、有效期 25 年以上）→ 填 alias 与密码 → release → Finish
6. 命令行等价：`./gradlew assembleRelease`（或 `bundleRelease`），产物在 `app/build/outputs/apk/release/`
7. 安装：`adb install -r app-release.apk`，首次需允许「安装未知应用」
8. 首次使用：按引导开权限，配置模型（登录或填 Key）

### 7.3 测试

- 单元测试：Agent 循环（录制 SSE 回放）、风险分级、节点查找打分、坐标归一化
- 仪器测试：关键页面与玻璃组件 Compose 测试、无障碍动作 UiAutomator 回放
- 手工验收：关蓝牙、发消息、定时任务三个真实任务，并验证无权限 / 目标被杀 / 锁屏三类失败路径
- 性能：启动耗时、列表帧率、HUD 重绘耗时

真实 API 调用，不使用 mock 模板；仅测试使用录制的响应样本。

## 8. 已知风险

- 国产 ROM 杀后台，需要自启动与电池白名单引导
- 部分 ROM 限制无障碍截图，自动退化为节点模式
- WebView / 自绘界面节点不可读，依赖视觉兑底；视觉模型不可用时明确报错
- 短信与通知权限属应用商店敏感权限，本方案定位自签名侧载
- 内置账号需 auth-server 登记 `zhuzhu-copilot-mobile` platform，未登记时先以自填 Key 跑通
- 本机当前只安装了 `android-37.0` 预览平台，若缺少 API 36 稳定平台，需在 SDK Manager 安装后再 Sync；不得改用预览平台编译 release

## 9. 明确不做（首版）

- 远程 / HTTP-SSE MCP 服务器接入
- 技能包导入与多 Agent 工作流
- 常驻唤醒词语音
- 应用商店上架与 AAB 发布流程（仅自签名 APK）
- 与桌面端的实时联动/远程控制
