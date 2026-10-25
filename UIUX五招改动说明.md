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

## 涉及文件

- `src/zhuzhu_Copilot/ui/agent_panel.py` — 招 1/2/4/5 + 追加精修（输入框圆角融合、弯曲箭头、`_LoadingOverlay` 保存遮罩）
- `src/zhuzhu_Copilot/ui/agent_chat_bubbles.py` — 招 1（PillButton 淡染）+ `ChatStyle.w_emphasis` 字段
- `src/zhuzhu_Copilot/copilot_plugins_examples/agent-scorecard/service/scorecard_service.py` — 招 5（评分对话框）
- `src/zhuzhu_Copilot/ui/desktop_pet.py` — 桌宠 alpha 平滑

## 验证

- 冒烟：`tmp_uiux/smoke_uiux.py`（offscreen，22 项断言全 PASS / 0 FAIL）
- 真实窗口：`tmp_uiux/verify_real.py`（双主题整窗截图）
- 输入框圆角：`tmp_uiux/shot_real_vp.py`（grab 真实输入框，viewport 占满）
- 保存遮罩：`tmp_uiux/verify_overlay.py`（多帧转圈）、`e2e_save.py`（端到端保存→关闭）
- 弯曲箭头：`tmp_uiux/verify_icons.py`
- 模拟流式：`tmp_uiux/sim_stream.py`（不发 API，自动截图后退出）
- 评分框：`tmp_uiux/preview_score.py`

## 已知遗留（次要界面）

- 设置对话框 / 页面配置（表格 th/td、QTabWidget::pane、QDialogButtonBox 等）仍有约 143 处 `border:1px`，属次要界面，不影响主观感。
- 消息导航 12×12 小圆点仍用 `border:1px solid`，影响极小。
- 真实流式水墨晕染需模型账户可用后目验。
