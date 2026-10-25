# zhuzhu Copilot · UI/UX 五招美化改动说明

依据 `client-uiux-design` 技能（大厂 AI 客户端质感五招）对 PyQt6 桌面端做的精修。
冒烟测试 24 项断言全部 PASS（0 FAIL），明暗双主题真实窗口已目验。

---

## 招 1 · 微对比色阶分层（不画边框、不用纯黑）

原则：层级靠**色阶 + 色相**拉开，不靠描边；深色底不碰纯黑。

| 位置 | 改前 | 改后 |
|---|---|---|
| 右上预览按钮选中态 | `border:1px solid 蓝` 方框 | 主色 0.18 淡染填充（hover 0.28）、无 border、图标用主色 |
| 「查看执行过程」PillButton（含 dashed 变体） | `solid/dashed 1px` 描边 | 主色 rgba 0.14 淡染胶囊（hover 0.24）、无 border |
| 聚焦光晕（普通） | blur 16 / alpha 170（边缘成实蓝线） | blur 24 / alpha 120（柔化） |
| 聚焦光晕（壁纸透出） | blur 36 / alpha 255（糊成硬光带） | blur 44 / alpha 190 |
| 录音态圆按钮 | `border:1px solid` | 去描边（实心渐变圆盘已够强调） |
| 底部输入框 | 外圆角光晕 + 内部 FIELD 直角填充（「内嵌正方形」，四角不统一） | 见下方「追加精修 · 输入框圆角融合」，内部填充完整匹配外侧圆角 |
| 全局 QSS / QMenu | 输入框、菜单描边 | 全表零 `border:1px`，菜单用 elevated 层 |

**色阶实测（口径 (R+G+B)/3，8bit）**
- 暗色：BG `#101216`(18.7) → PANEL `#181B21`(28) → CARD `#1F232C`(36.7) → ELEVATED `#22262F`(39.7)，步进 9.3 / 8.7 / 3，无纯黑
- 浅色：BG `#F7F8FB` → CARD `#FFFFFF`，步进 6.3

## 招 2 · 暗色模式字重减一档

原则：深色底上字重显脏，统一减一档。

| 位置 | 改前 | 改后 |
|---|---|---|
| 标题栏应用名「zhuzhu Copilot」 | 硬编码 700 | `_W_TITLE`（暗色 600 / 浅色 700） |
| 欢迎页 HERO 主标题 | 硬编码 800（深色底发糊） | `_W_TITLE` |
| 全局 title | 固定 | 暗色 600 / 浅色 700 |

## 招 3 · 流式打字水墨晕染

- 正文落字走 `_EmergeBand` 浮现（`EMERGE_MS=760`），波前轻微模糊、随字快速清晰。
- 本地模拟流式（不发 API）验证：`_EmergeBand` 可见、记录行龄、定时器活跃、正文随内容完整增长（段落 / 加粗 / 列表）。
- **真实正文流式未目验**：当前模型账户余额不足（HTTP 402），充值或切换可用模型后即可看到；机制本身已验证。

## 招 4 · 等宽数字锁死 + 自适应网格

- 顶栏 CPU / MEM / 速度 / 时钟四个实时跳动标签，原本无数字栈，统一补 `_numeric_css()`（数字跳动时不再左右晃）。
- 全局 `NUMERIC_FONT_STACK`（Segoe UI 等宽数字），`ChatStyle.font_numeric` 同步。

## 招 5 · 动效分性格（拖拽咬死 / 点赞 Q弹 / 对勾消失）

| 场景 | 曲线 / 时长 | 性格 |
|---|---|---|
| 面板滑入 / 滑出 | `settle()` / `fade_out()` | 大件沉稳，无过冲 |
| 开关拨动 | `snap()` OutBack | 咬合（轻过冲） |
| 下拉箭头旋转 | `hover()` 110ms | 微反馈 |
| 评分星点击 | OutBack（overshoot 1.05）240ms，图标 30→26 | Q弹 |
| 对勾 | 2.5s 后消失 | 不长驻 |

**任务评分对话框重写**（原 5 个不同长度 `★` 按钮、无主题、无动效）：
- 5 颗等大（42px）单星，未激活灰空心、hover 实时预览（含本颗及之前亮金）、点击锁定并 OutBack Q弹；
- 对话框整体主题适配（PANEL 底、凹陷字段评语框、幽灵「跳过」+ 实心「提交评分」），等宽数字统计。

## 额外 · 桌宠猫黑锯齿

- 根因：GIF 透明仅 1-bit（alpha 只有 0/255），边缘抗锯齿像素被二值化，形成黑锯齿与 101 个孤立杂点。
- 处理：加载每帧时对 alpha 做 `MedianFilter(3)` 去杂点 + `GaussianBlur(1.1)` 羽化硬边，边缘变柔和。

---

## 追加精修（第二轮）

### 1. 输入框圆角融合：去除「内嵌正方形」

- **根因**：`QPlainTextEdit` 的 `paintEvent` 运行在 **viewport 绘制上下文**；QSS 的 `padding:5px 10px` 让 viewport 偏移到 (10,5)，且 Qt 把 QSS 背景路由到 viewport 以**直角矩形**绘制。viewport 只覆盖内容区、画不到 padding/圆角弧形区域，于是内部 FIELD 填充在四角呈直角，与外侧圆角光晕割裂（「内嵌正方形」）。
- **方案**（已验证）：
  - 非壁纸模式 QSS 改 `padding:0`、`background:transparent`、`border:none` → **viewport 占满整个输入框**（实测 `QRect(0,0,734,42)`）；
  - `paintEvent` 在 viewport 上用 QPainter 抗锯齿绘制完整圆角矩形（半径 `_INPUT_RADIUS=10`），未聚焦填 FIELD、聚焦填 `_mix_hex(HOVER,FIELD,0.55)`；
  - 文字边距改用 `document().setDocumentMargin(10)`（左右 10px），封装在 `_apply_input_style()`，初始化 / 主题 / 壁纸刷新三处统一调用。
- **效果**：内部填充完整跟随外侧圆角，四角无直角，视觉统一。

### 2. 撤回 / 重做按钮图标：开源弯曲箭头

- 预览窗口「打开…」旁两个圆按钮（撤销 `undo_btn` / 重做 `redo_btn`），图标由自绘线条换成 **Lucide 开源图标**（ISC 许可）：
  - 撤销：`undo-2`（左弯曲箭头）
  - 重做：`redo-2`（右弯曲箭头）
- 走现有 `_svg_icon()` 渲染（QSvgRenderer + `{color}` 占位），线条风格与全 App 一致；主题切换时在 `_apply_surface_qss()` 重新着色。

### 3. 保存设置：高斯模糊遮罩 + 8 圆点转圈

- 新增 `_LoadingOverlay` 控件，点「保存」后覆盖整个设置窗口：
  - **高斯模糊背景**：start 时一次性 `grab()` 父窗口，经 `QGraphicsBlurEffect`（blurRadius 22）模糊，再压一层半透明（磨砂玻璃感）；
  - **8 个圆点转圈**：圆点均匀分布在圆周，head 处最亮最大、沿运动方向拖尾递减（8 点均保持可见）；`QTimer` 16ms + `PreciseTimer` 驱动 ≈ **60fps**，角度由真实经过时间计算（周期 880ms，降频不改变旋转速度）；
  - 下方描述文字：**「请等待，正在应用更改中」**。
- 遮罩拦截其下鼠标 / 键盘事件，防止保存期间误操作；最短展示 900ms（`_SAVE_OVERLAY_MIN_MS`，避免一闪而过）后 `accept` 关闭。端到端测试通过（遮罩显示 → 保存 → 自动关闭，result=Accepted）。

---

## 第三轮排查：「重启源码后没有任何改动」

用户反馈改完源码、重启后界面无变化。排查锁定**两层根因**，均已修复。

### 根因 1 · source_overlay 覆盖层拦截（主因）

- `main.py` 启动时安装了 `_copilot_overlay_hook`：`zhuzhu_Copilot.*` 的导入会**优先命中** `~/.zhuzhu_Copilot/source_overlay/zhuzhu_Copilot/<相对路径>`，overlay 里有同名 `.py` 就加载它，而不是 Desktop 源码。
- 该 overlay 里残留着 **10:25 旧版** `ui/agent_panel.py`（grep 确认仍是 `_line_icon("undo")`、无 `_apply_input_style` / `_LoadingOverlay` / `_UNDO_SVG`）。所以无论怎么改 Desktop 源码、怎么重启，运行时都加载 overlay 旧版 → 无改动。
- **修复**：把 Desktop 最新 `agent_panel.py` 同步覆盖到 overlay（旧版备份为 `agent_panel.py.bak_<时间戳>`），并清理 overlay 的 `__pycache__`。
- **注意**：overlay 的 `core/agent_tools.py`（含 perf-turbo / code-map 插件注入的工具定义）、`core/perf_turbo.py`、`core/code_map.py` 是插件文件，**保留未动**。

### 根因 2 · 5 处 QSS 花括号误用 `}}`

- 相邻字符串拼接中，f-string 之后紧跟**普通字符串**（无 f 前缀），普通串里写了 `}}`。`}}` 在 f-string 中转义为单个 `}`，但在普通字符串里是**两个字面 `}`**，导致规则多出一个 `}`，QSS 解析失败（设置对话框 QListWidget / QLineEdit / QPlainTextEdit / 对话框自身样式失效）。
- **修复**：5 处 `}}` → `}`；静态扫描确认仅剩 2 处合法双花括号（JS 代码、JSON 注释）。

### 根因 3 · 保存遮罩时序缺陷（看不到遮罩）

- 旧顺序把「页面补齐 `_ensure_all_pages()` + 全量数据收集」放在遮罩**之前**，这是主线程同步操作（本机实测约 2.5～3.7s）。遮罩要等收集结束才绘制，而 `_finish_save` 时累计时长已远超 900ms，`wait=0` 又立刻关闭 → 用户看不到遮罩。
- **重构后的时序**（`_save` 拆为多步）：
  1. `_save`：**先**创建并 `start()` 遮罩 → `repaint()` 当帧上屏（高斯模糊 + 转圈第 0 帧 + 文字）；
  2. `_build_pages_step`：**每帧只构建 1 个页面**，页间留 10ms 间隙让 16ms 动画 QTimer 触发（转圈不被卡成定格）；
  3. `_collect_and_save`：全部页面就绪后收集数据；
  4. `_finish_save`：持久化 + 应用，按真实经过时间补足最短展示；
  5. `_close_after_save`：停遮罩、`accept`。
- **实测**：真实环境遮罩即时可见（高斯模糊 + 8 圆点 + 中文「请等待，正在应用更改中」），角度持续推进，约 6.4s 后对话框关闭（result=Accepted）。

> 旁证：开始菜单快捷方式指向 **10/7 打包的安装版**（`C:\Program Files (x86)\zhuzhu Copilot\...`，代码在 mypyc 编译的 `.pyd` 里，不读源码）。要跑最新源码请用项目根目录的「运行源码版.bat」。

---

## 涉及文件

- `src/zhuzhu_Copilot/ui/agent_panel.py` — 招 1/2/4/5 + 追加精修（输入框圆角融合、弯曲箭头、`_LoadingOverlay` 保存遮罩）
- `src/zhuzhu_Copilot/ui/agent_chat_bubbles.py` — 招 1（PillButton 淡染）+ `ChatStyle.w_emphasis` 字段
- `src/zhuzhu_Copilot/copilot_plugins_examples/agent-scorecard/service/scorecard_service.py` — 招 5（评分对话框）
- `src/zhuzhu_Copilot/ui/desktop_pet.py` — 桌宠 alpha 平滑

## 验证

- 冒烟：`tmp_uiux/smoke_uiux.py`（offscreen，22 项断言全 PASS / 0 FAIL）
- 真实窗口：`tmp_uiux/verify_real.py`（双主题整窗截图）
- 输入框圆角：`tmp_uiux/shot_real_vp.py`（grab 真实输入框，viewport 占满）
- 保存遮罩：`tmp_uiux/verify_overlay.py`（多帧转圈）、`e2e_save2.py`（offscreen 验证角度推进）、`e2e_real_save.py`（真实环境遮罩可见 + 中文文字）、`e2e_complete.py`（完整保存→关闭，result=Accepted）、`measure_pages.py`（逐页构建耗时）
- 第三轮排查：`diag_qss.py`（QSS 解析）、`scan_braces.py`（花括号扫描）、`debug_save_timing.py`（保存时序）、`verify_source_icons.py`（overlay 图标核对）
- 弯曲箭头：`tmp_uiux/verify_icons.py`
- 模拟流式：`tmp_uiux/sim_stream.py`（不发 API，自动截图后退出）
- 评分框：`tmp_uiux/preview_score.py`

## 已知遗留（次要界面）

- 设置对话框 / 页面配置（表格 th/td、QTabWidget::pane、QDialogButtonBox 等）仍有约 143 处 `border:1px`，属次要界面，不影响主观感。
- 消息导航 12×12 小圆点仍用 `border:1px solid`，影响极小。
- 真实流式水墨晕染需模型账户可用后目验。

---

## 第四轮（2026-10-25 下午）：计时器组件重构 + 保存遮罩弹出时机深化

用户反馈两条：①「计时器边框丑陋、显示累赘」；②「点保存过 500ms 之后才弹出遮罩」。

### A. 计时器：骑线 pill 徽章 → 左上角纯文本行

| 项 | 改前 | 改后 |
|---|---|---|
| 外壳 | 圆角 pill + 1px 描边 + 卡片底，骑在顶部虚线上 | **无外壳**：一行纯文本坐在气泡左上角 |
| 图标 | 时钟图标 + 文本 | 仅文本（简化） |
| 文案 | `12.4s` / `2m03s` | `已处理 X小时X分钟X秒`（零档省略：`已处理 45秒`/`已处理 23分钟45秒`/`已处理 1小时2分钟5秒`） |
| 字体 | UI 字体（数字比例宽度） | `ChatStyle.font_numeric` 等宽数字栈（tabular figures），逐秒刷新不抖动 |
| 结构 | 徽章压线 | **计时行 → 虚线 → 输出内容** 三段式（`TIMER_ROW_H=17` + `TIMER_DASH_GAP=1`） |

- 新格式化函数 `fmt_elapsed()`（`agent_chat_bubbles.py`）：整数秒、零档省略、不补零。
- 无耗时数据（老会话）占位文案：`已处理 --`（`RIBBON_UNKNOWN_TEXT`）。
- `ChatStyle` 新增 `w_emphasis` 字重档位字段（计时行用，随主题降档）。

### B. 连带修复：顶部虚线被内容盒「吃掉」（Qt 父子合成陷阱）

- **现象**：重构后虚线只剩右缘约 9px 可见。真机抓图 + 离屏双平台复现。
- **根因**：虚线原由 `ChatTurn.paintEvent` 画，位置落在子控件 `_box` 矩形内；Qt 对带 `WA_TranslucentBackground` 的父控件会**先清除每个子控件的区域再合成子控件**——父控件画在子控件矩形下的内容被整块清掉（hid _box 实验：虚线从 5px 变 269px 完整可见，实锤）。
- **修法**：新增 `_TurnBox(QWidget)` 子类，虚线改由内容盒在自己的 `paintEvent` 里画（z 序稳定位于盒内区块之下）。`ChatTurn.paintEvent` 只保留 inset 小于盒 y 的兜底路径。
- 回归断言：`tests/test_chat_bubble_demo_parity.py::test_timer_row_sits_above_dash_line` 锁定 `isinstance(turn._box, cb._TurnBox)`。

### C. 保存遮罩：弹出时机从「几百 ms」压到「当帧」

实测分 phase 定位（`tmp_uiux/dbg_trace.py`）：

| 阶段 | 旧 | 新 |
|---|---|---|
| 遮罩创建时机 | 页面补齐（2.5~3.7s）+ 数据收集**之后** | `_save()` 第一行（重活全部让路） |
| `show()` 与模糊的顺序 | 同步全尺寸高斯模糊（几百 ms）**之后**才 show | show → `repaint()` 当帧上屏 → 模糊异步 |
| 高斯模糊 | 同步、全尺寸、半径 22 | `singleShot(0)` 异步 + 底图降采样 1/4（成本降一个数量级） |
| 首帧实测 | >500ms | **37ms**（冷启动最差 204ms，均 <500ms） |

- 关键细节：`show()` 只入队绘制，必须补 `repaint()` 同步画一帧，否则后续重活（分帧建页）占住主线程，绘制仍被推迟。
- 底图采集选型：`grab()`（冷 20ms）vs painter 缩放 `render()`（冷 96ms，光栅引擎冷启动）——**选 grab**，模糊前降采样放在异步段做。
- 遮罩新增 `resizeEvent` 跟随父对话框尺寸；`stop()` 释放两幅全尺寸位图。
- 新测试 `tests/test_settings_save_overlay.py`：首绘 <500ms、首绘早于 `_build_pages_step`、遮罩铺满、模糊异步就位且原图释放。

### D. 运行时同步（重要）

`~/.zhuzhu_Copilot/source_overlay/` 会遮蔽 Desktop 源码（安装版不含 overlay hook，不受影响）。
本次已将 `ui/agent_panel.py` 同步到 overlay（旧版备份 `agent_panel.py.bak_20261025_142949`，ui 目录 `__pycache__` 已清）。
**以后改 `agent_panel.py` 必须同步 overlay，否则运行源码版不生效。**

### 涉及文件（本轮）

- `src/zhuzhu_Copilot/ui/agent_chat_bubbles.py` — `fmt_elapsed` / `CostRibbon` 重写 / `TIMER_ROW_H` / `_TurnBox` / ChatStyle 字段
- `src/zhuzhu_Copilot/ui/agent_panel.py` — `_THEMES` 加 ELEVATED/FIELD、`_save` 遮罩先行、`_LoadingOverlay` 异步模糊、`_chat_style` 注入新字段
- `src/zhuzhu_Copilot/ui/tokens.py` — `NUMERIC_FONT_STACK` / `FONT_WEIGHT` 档位表
- `tests/test_chat_bubble_demo_parity.py`、`tests/test_settings_save_overlay.py` — 新回归用例
