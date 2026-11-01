# 规格：主页面模型悬停调节 + Agent 权限下拉

## 1. 目标（用户原话拆解）

1. 主页面（Agent 对话页）模型选择框：鼠标**停留 1s** 弹出调节浮层，浮层内可拖动滑块调节「思考强度」（**沿用现有 `_CapsuleSlider` 样式，不改外观**）、开关「自动按难度」、显示/开关「上下文窗口」。
2. 设置页删除上述对应入口（仅删除：思考强度滑块、自动按难度、1M 上下文开关及其说明；**保留**思考模式 / 强制思考 / 高效模式）。
3. 模型名称左侧常驻显示「思考强度 + 上下文窗口」两个信息徽标。
4. 鼠标指针恢复系统默认（删除自定义高对比光标）。
5. 「提示词优化」按钮右侧新增 Agent 权限下拉菜单：每步确认（带 SVG 矢量图标）、仅工具调用、全部访问（红色警告样式 + 二次确认弹窗）。权限语义**复用既有执行模式** `agent_mode`（`ask` / `edit` / `yolo`），选择后立即生效。

## 2. 事实基础（已核对代码）

| 项 | 位置 |
| --- | --- |
| 输入卡工具行 | `agent_panel.py` `_build_input_row` 内 `voice_btn` / `attach_btn` / `optimize_btn` → `addStretch(1)` → `model_combo` → `action_btn`（约 21341-21402） |
| 胶囊滑杆 | `_CapsuleSlider`（约 2782），`tick_step=1`、`particles_on_max=True`、`TICK_STYLE_LEVEL` |
| 弹出浮层样式参考 | `_TokenStatsPopover`（约 14010，`popup_animated` / `close_animated` / `apply_theme`） |
| 淡入菜单 | `_FadeMenu`（约 19512） |
| 线条矢量图标 | `_line_icon`（约 1602）+ `_LINE_ICON_KINDS`（约 1886） |
| 设置页模型页 | `_build_model_page`（约 6361），删除块 6427-6486（力度）与 6516-6535（1M 上下文） |
| 设置保存 | `_collect_and_save`（约 10550），`model` 字典 10568-10579 |
| 面板设置应用 | `_apply_agent_settings`（约 29382），`_sync_model_combo`（约 29494） |
| 执行模式 | `QSettings` 键 `agent_mode`，`self._mode`，引擎 `eng.direct = self._mode == "yolo"`（约 29417） |
| 自定义鼠标光标 | `_high_contrast_cursor`（约 934）、`_apply_app_cursor`（约 22741）及其调用点 |
| i18n | `src/zhuzhu_Copilot/locales/en_US.json`（中文原文为 key） |

`_ROUND_BTN_D = 42`，工具按钮统一 `_round_icon_btn_qss(_ROUND_BTN_D)`。

## 3. 设计决策

### D1 浮层组件 `_ModelTunePopover(QFrame)`
- 位置：模块级，紧邻 `_TokenStatsPopover` 定义之前。
- 视觉：`background: _base_color("ELEVATED")`、`border: none`、`border-radius: RADIUS_MD`、内边距 `SPACING_MD`；宽度 320；不新增颜色常量。
- 内容纵向 3 段：
  1. 标题行「思考强度」+ 当前档位中文标签（`_ui(agent_llm.effort_label(...))`）；
  2. `_CapsuleSlider(tick_step=1, particles_on_max=True, tick_style=_CapsuleSlider.TICK_STYLE_LEVEL)`，范围 `0..len(EFFORTS)-1`，高度沿用类内 `setFixedHeight(48)`，**不做任何样式改写**；
  3. 「自动按难度」`QCheckBox`；「上下文窗口」行：`QCheckBox`（1M 开关，复用配置键 `context_1m`）+ 当前上限只读文案 `_fmt_tokens(window)`。
- API：
  - `set_state(effort: str, auto: bool, long_1m: bool, window_text: str)`
  - 信号 `effort_changed = pyqtSignal(str)`、`auto_changed = pyqtSignal(bool)`、`context_changed = pyqtSignal(bool)`
  - `popup_above(anchor: QWidget)`：以 anchor 顶部为基准，水平右对齐 anchor，垂直置于其上方 8px；透明度 + 8px 上移动画，取 `motion.SETTLE_MS` / `motion.settle()`；超屏时改为下方
  - `close_animated()`：`motion.FADE_OUT_MS` / `motion.fade_out()`
  - `apply_theme()`：按当前主题刷新 QLabel/QCheckBox 颜色（深色 `TEXT`/`TEXT_DIM`，不描边）
  - `keyPressEvent`：Esc → `close_animated()`
- 交互：滑块 `valueChanged` → 发 `effort_changed`；两个勾选框 `toggled` → 发对应信号。**拖动滑块时不改变「自动按难度」，两者独立**（浮层里自动开关由用户显式操作）。

### D2 1 秒悬停触发
- 模块级 `_HoverHold(QObject)`：`eventFilter` 监听 `QEvent.Type.Enter` / `Leave`；
  - Enter 启动 `QTimer.singleShot` 等价的可取消 `QTimer`（`setSingleShot(True)`, `setInterval(1000)`），到点回调 `on_hold`；
  - Leave 停止计时器（1000ms 内移出即不弹）；
  - 若浮层已显示，鼠标移入浮层（浮层的 `Enter` 刷新）则保持；离开浮层后 250ms 关闭（`LEAVE_GRACE_MS`）。
- 面板属性：`self._model_tune`、`self._model_hover`、`self._model_tune_timer`。
- 触发锚点：`self.model_combo`。点击外部 / 按 Esc / 面板隐藏时关闭（复用 `_PopoverDismissFilter` 若签名兼容，否则由浮层 `focusOutEvent` + 定时器兜底）。

### D3 徽标 `_sync_model_badges()`
- 新控件 `self.model_badges = QLabel("")`，插入 `tools` 布局中 `model_combo` **之前**（`insertWidget`）。
- 文案：`思考 {档位} · {窗口}`；自动按难度开启时档位显示「自动」；窗口取 `self._model_cfg["context"]["window"]`（无则 `context_window`），经 `_fmt_tokens` 压缩。
- 样式：`color: TEXT_DIM`，`font-size: FONT_CAPTION`，`background: transparent`，`border: none`；`setCursor(Qt.CursorShape.ArrowCursor)`。
- 调用点：`_build_input_row` 构建后、`_sync_model_combo` 末尾、`_apply_agent_settings` 末尾、浮层三个信号回调后。

### D4 设置页删除与保存适配
- `_build_model_page`：
  - 删除力度块（`effort_slider` / `effort_label` / `effort_hint` / `auto_effort_check` 及 `_sync_effort_ui` / `_sync_effort_declared` 调用），改为从 `cfg` 读入纯状态：`self._effort`、`self._auto_effort`、`self._context_1m`；
  - 删除 1M 块（`context_1m_check` / `context_hint` 及 `_sync_context_hint` 调用），改为 `self._context_1m = bool(cfg.get("context_1m", False))`；
  - 保留 `think_combo` / `force_think_check` / `efficient_check` 原样。
- 删除方法：`_on_effort_slider`、`_on_effort_auto`、`_on_context_1m_toggled`、`_sync_effort_ui`、`_sync_effort_declared`。
- `_context_hint_text(providers=None, long_1m=None)`：显式参数优先，缺省读 `self._providers` / `self._context_1m`；不再访问 `context_1m_check`。`_sync_context_hint` 保留（供其他流程/测试复用的静默刷新）。
- `_collect_and_save` 的 `model` 字典改为：`"effort": getattr(self, "_effort", "") or _old_m.get("effort","medium")`、`"auto_effort": bool(getattr(self, "_auto_effort", True))`、`"context_1m": bool(getattr(self, "_context_1m", False))`。
- 主页面保存链路：浮层变更 → 读 `agent_llm.load_settings()` → 合并 `model` 字典的 `effort` / `auto_effort` / `context_1m` → `agent_skills.save_settings(...)` → `agent_llm.load_model_config()` → `self._apply_agent_settings()`。防抖 250ms（拖动滑块时连续变更只落盘一次），落盘后刷新徽标与浮层状态。

### D5 鼠标指针恢复默认
- 删除 `_high_contrast_cursor()` 与 `_apply_app_cursor()` 及其全部调用（`build_default_ui`、`_apply_panel_mode` 等）。
- **保留** `_DropTextEdit` 自定义闪烁输入光标（用户已确认这是他说的「光标」）。

### D6 权限下拉
- 新按钮 `self.perm_btn`：`QPushButton(_line_icon("shield", 20, ACCENT), "")`，`setFixedSize(_ROUND_BTN_D, _ROUND_BTN_D)`，`_round_icon_btn_qss(_ROUND_BTN_D)`，`setAutoDefault(False)`，插入 `tools` 在 `optimize_btn` 之后。
- 菜单用 `_FadeMenu(self)` + 三个 `QAction`（`setCheckable(True)`，按 `self._mode` 勾选）：
  | 模式 | 文案 | 图标 | 颜色 |
  | --- | --- | --- | --- |
  | `ask` | 每步确认 | `_line_icon("ok", 16, TEXT)` | 常规 |
  | `edit` | 仅工具调用 | `_line_icon("tool", 16, TEXT)` | 常规 |
  | `yolo` | 全部访问 | `_line_icon("warning", 16, ERR)` | 文字 `ERR` 红 |
- `_line_icon` 新增：`shield`（盾牌轮廓）、`warning`（三角 + 感叹号）；两者登记进 `_LINE_ICON_KINDS`。
- `_on_perm_action(mode)`：
  - `yolo`：`QMessageBox.question`，标题「开启全部访问」，正文含不可逆风险说明，默认 No；取消则不改变状态并重开菜单前保持原勾选；
  - 确认后：`self._settings.setValue("agent_mode", mode)`、`self._mode = mode`、遍历 `self._sess` 引擎 `eng.direct = (mode == "yolo")`、`self._refresh_perm_btn()`；
  - `ask` / `edit` 直接生效。
- `_refresh_perm_btn()`：按当前模式更新按钮图标颜色（yolo → `ERR`）、tooltip（三种模式说明）与按钮 `objectName`（`permBtnDanger`，供 QSS 红描边）。
- 菜单弹出位置：`perm_btn` 上方（`_FadeMenu.exec(QPoint)`）。

### D7 i18n
`en_US.json` 追加中文键 → 英文值：
`思考强度`（已存在）、`自动按难度`、`上下文窗口`、`每步确认`、`仅工具调用`、`全部访问`、`开启全部访问`、三种模式 tooltip 与确认正文、`思考 {a0}`/`自动` 徽标片段。

## 4. 交付物
- 代码：`src/zhuzhu_Copilot/ui/agent_panel.py`、`src/zhuzhu_Copilot/locales/en_US.json`
- 测试：`tests/test_client_uiux_polish.py`、`tests/test_context_settings_ui.py`（必要时 `tests/test_input_row_affordances.py`）

## 5. 验收标准
1. 悬停 `model_combo` 1s 出现浮层；0.5s 内移出不出现；浮层内滑块拖动即时改变档位标签；Esc/点击外部收起。
2. 浮层修改后关闭再打开设置页，`settings.json` 的 `effort` / `auto_effort` / `context_1m` 已更新；主页面徽标同步变化。
3. 设置页模型页**不再**出现思考强度滑块、自动按难度勾选框、1M 上下文勾选框；仍保留思考模式与强制思考。
4. `model_combo` 左侧存在徽标标签，文案含中文档位与窗口数字；鼠标样式为默认箭头。
5. `optimize_btn` 右侧存在权限按钮；菜单三项齐全；选「全部访问」必弹确认；确认后 `QSettings["agent_mode"] == "yolo"` 且引擎 `direct=True`。
6. `python -m pytest tests/test_client_uiux_polish.py tests/test_input_row_affordances.py tests/test_context_settings_ui.py -q` 全绿（已知 pytest 进程在用例全过后以 0xC0000409 退出，属既有 Qt 拆除噪音，不作为失败判据）。

## 5.1 按用户反馈修订（第二轮）

1. 悬停触发收窄：指针必须停在模型下拉框的**模型名称文本区**（右侧箭头区不触发），且**模型下拉列表未展开**时才允许弹层；列表展开时逐项 tooltip 已够用，不再叠加调节浮层。
2. 浮层互斥：调节浮层显示期间清空下拉框 tooltip（并 `QToolTip.hideText()`），收起时恢复，避免提示气泡与调节浮层叠在一起。
3. 模型徽标保持参考样式：`思考 {档位} · {窗口}`（窗口用千分位完整数字，如 `262,144`），右对齐贴模型名左侧。
4. 权限入口改为胶囊（`_PermPill`）：图标 + 当前权限文案 + 下尖角；全部访问时图标/文字/底色转红。
5. 权限菜单改为「标题 + 三档双行行项」：标题「如何批准 Agent 的操作?」，每行=图标 + 标题 + 灰色副标题 + 当前档位右侧对勾；全部访问整行红字。点击行使用 `QWidgetAction`（行内 QLabel 透明鼠标，点击冒泡回 `QMenu` 触发）。

## 5.2 按用户反馈修订（第三轮）

1. 权限图标改为**开源 SVG（Lucide，ISC 许可）**：胶囊 `shield` / `shield-alert`（全部访问）、下尖角 `chevron`；菜单行 `hand`（每步确认）/ `wrench`（仅工具调用）/ `shield-alert`（全部访问）、对勾 `check`。删除 `_line_icon` 里手绘的 `shield` / `warning` 分支（不再维护两套图形）。
2. 提示气泡与调节浮层互斥提前到**指针进入瞬间**：`_HoverHold` 新增 `on_enter` 回调，进入模型框即 `QToolTip.hideText()` + 清空 tooltip，避免 QToolTip 在约 0.7s 出现、正好打断 1s 时弹出的调节浮层；浮层收起/指针离开后恢复提示语。

## 5.3 按用户反馈修订（第四轮）

1. 调节浮层不再频繁弹出/关闭：离开宽限到期后先复核指针位置（模型框 / 浮层各自外扩 12px，覆盖两者之间窄缝），仍在任一处就保持打开；两个悬停守卫的离开动作统一走 `_maybe_close_model_tune`。
2. 输入框提示文案改为「今天能帮你做点什么呢？」；聚焦（点击输入框）不再清空 placeholder，提示文字保持显示，有文本时由 Qt 自然隐藏。
3. 权限胶囊图标随当前档位切换，且三档互不相同：`ask=hand` / `edit=wrench` / `yolo=shield-alert`（与权限菜单三行 SVG 同源），前两档不再共用盾牌。

## 5.4 按用户反馈修订（第五轮）

1. 勾选框对比度：全局 QSS 接管 `QCheckBox/QRadioButton::indicator` —— 未选中为凹陷字段底 + 次级灰描边，选中为主色实心（系统默认在浅色卡片上是白框叠白底，几乎不可见）。
2. 思考强度浮层与模型框的间距再上移 5px（`_ModelTunePopover.LIFT`）。
3. 权限胶囊图标线条统一淡灰（`TEXT_DIM`），仅「全部访问」保持红色警示。
4. 左栏收起态改为两枚 38px 圆形入口（文件树 folder / Git git-branch，均为 Lucide SVG）：点击即展开整栏并显示对应面板；标题条与窄条样式废弃，收起列宽 54px。
5. 下拉菜单平滑动画：设置页残余的普通 `QComboBox` 全部改为 `ArrowComboBox`（弹出列表淡入 + 箭头旋转）；菜单仍走 `_FadeMenu`。
6. 设置页：通用页所有顶层勾选框统一缩进到标签列宽（左缘与下拉框对齐）；新增 `_SettingsWheelGuard`，未聚焦的下拉框/数值控件不吃滚轮（滚轮转发给页面滚动），避免误改选项。
7. 1M 上下文开关改为左右滑块 `SwitchButton`（与设置页同款），点击/拨动即写回 `context_1m`。

## 5.5 按用户反馈修订（第六轮）

1. 左栏收起态两枚圆形入口改为「对话菜单」(`chat` → 展开对话栏) 与「侧栏」(`panel-left` → 展开左栏）；收起态栏容器不再画圆角矩形卡片（透明、无边框），只有两枚圆形按钮浮在背景上。
2. 思考强度滑块的圆形整体右移 3px（`_CapsuleSlider.THUMB_SHIFT`），视觉上更贴胶囊右缘。
3. 下拉菜单淡入改为 120fps：`ArrowComboBox` 用 8ms 定时器逐帧写窗口透明度（QPropertyAnimation 约 60fps，有台阶感）。
4. 调节浮层「固定位置」弹出：几何只定位一次，只做透明度淡入/淡出，取消 8px 上移动画（避免面板在飘）。

## 5.6 按用户反馈修订（第七轮）

1. 删除对话栏收起后的 36px 窄条：`_SessionDrawer.set_collapsed(True)` 直接整栏隐藏，展开入口由左栏两枚圆形按钮提供；两枚按钮贴左（2px 边距，列宽 42px）。
2. 修复 `hidePopup` 的 `RuntimeError: QTimer has been deleted`：放弃 8ms 自建定时器，回到 `QPropertyAnimation`，收起时对已结束动画 `stop()` 加 RuntimeError 兜底。
3. 修复下拉淡入的撕裂/闪烁：不再高频改原生窗口透明度（与合成器抢帧），改由 Qt 动画引擎驱动；且 `opacity=0` 在 `showPopup` 之后**同步**设置（旧版晚一帧才归零，会先闪一帧全不透明）。

## 5.7 按用户反馈修订（第八轮）

1. 调节浮层改为相对**输入卡**固定定位：底边 = 卡顶 − 10px、右边 = 卡右缘 − 12px，整块浮在输入卡上方，下半截不再被输入卡/底栏遮挡；位置不随模型名长短或下拉框尺寸变化。
2. 显示后 `raise_()` 并在下一帧再抬一次，避免布局重排把浮层压到兄弟控件下面。
3. 悬停保持判定改为「模型框 ↔ 浮层」两矩形**并集**（+12px）：浮层上移后，鼠标从模型框向上经过输入卡内部也不会被误判为离开而收起。

## 5.8 本轮需求（工作流页卡顿/闪烁 + 工作目录下拉 + 移除对话流页）

1. 工作流页卡顿与列表闪烁：`_CardBoard` 重排改为 16ms 去抖（批量 `clear + N×add_card` 只重排一次），并去掉「每次重排把卡片 `setParent(None)` 再插回」的做法（只补缺/删多余）；宽度抖动 ≤2px 不重排，避免滚动条出现/消失引发的重排循环。
2. 输入卡权限菜单右侧新增**工作目录下拉**：列出最近使用目录（QSettings `agent_workdir_recent`，自动过滤已失效路径）+ 当前目录 + 「添加文件夹…」；选中即写入当前会话并应用（Git/文件树/引擎提示同步刷新）；新增目录走原生目录选择。
3. 新开对话沿用上一个对话的实际生效工作目录（已有实现，加接线守卫防回退）。
4. 移除设置页「对话流」分页：`_NAV_ITEMS` / `_page_builders` 同步删除，`_COLLECT_PAGE_COUNT` 6→5；对应的会话工作目录行、闪烁定位、保存回写与 `highlight_sid` 全部删除；提示条「去设置」改为展开输入卡的工作目录下拉。

## 6. 非目标
- 不改 `agent_llm.EFFORTS` 与力度映射算法、不改上下文解析优先级链、不改执行模式在引擎内的语义、不做设置页其他控件重排。
