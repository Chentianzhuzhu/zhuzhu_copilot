# 任务清单：主页面模型悬停调节 + Agent 权限下拉

对应 spec：`spec.md`（同目录）

## T1 矢量图标
文件：`src/zhuzhu_Copilot/ui/agent_panel.py`
- [x] `_line_icon` 新增 `shield`（盾牌轮廓）、`warning`（三角 + 感叹号）
- [x] `_LINE_ICON_KINDS` 登记 `shield` / `warning`

## T2 浮层组件
文件：`src/zhuzhu_Copilot/ui/agent_panel.py`
- [x] 模块级常量 `_MODEL_TUNE_HOLD_MS = 1000`、`_MODEL_TUNE_LEAVE_MS = 250`、`_MODEL_TUNE_W = 320`
- [x] `_ModelTunePopover(QFrame)`：标题 / `_CapsuleSlider` / 档位标签 / 自动按难度 / 上下文窗口 + 窗口读数
- [x] `set_state()`、`popup_above()`、`close_animated()`、`apply_theme()`、Esc 关闭、`effort_changed` / `auto_changed` / `context_changed` 信号
- [x] `_HoverHold(QObject)`：Enter 起 1s 计时、Leave 取消；浮层内保持、离开 250ms 后收起

## T3 接入输入卡
文件：`src/zhuzhu_Copilot/ui/agent_panel.py`（`_build_input_row`）
- [x] 构建浮层与悬停过滤器，锚点 `model_combo`
- [x] `self.model_badges = QLabel("")` 插入 `model_combo` 之前（左侧）
- [x] `_sync_model_badges()` 及全部调用点（构建后 / `_sync_model_combo` / `_on_model_combo` / `_apply_agent_settings` / 浮层信号）
- [x] `_on_model_tune_effort/_on_model_tune_auto/_on_model_tune_context`：防抖 250ms 落盘 + `load_model_config()` + `_apply_agent_settings()`

## T4 设置页删入口
文件：`src/zhuzhu_Copilot/ui/agent_panel.py`
- [x] `_build_model_page` 删除力度块与 1M 块，改为读 `self._effort/_auto_effort/_context_1m`
- [x] 删除 `_on_effort_slider`、`_on_effort_auto`、`_on_context_1m_toggled`、`_sync_effort_ui`、`_sync_effort_declared`、`_effort_models`
- [x] `_context_hint_text(providers=None, long_1m=None)` 去掉控件依赖；`_sync_context_hint` 保持静默
- [x] `_collect_and_save` 改为读写 `_effort/_auto_effort/_context_1m` 字段
- [x] 保留思考模式 / 强制思考 / 高效模式

## T5 恢复默认鼠标指针
文件：`src/zhuzhu_Copilot/ui/agent_panel.py`
- [x] 删除 `_high_contrast_cursor()`、`_apply_app_cursor()` 及所有调用
- [x] 保留 `_DropTextEdit` 自定义输入光标（未改动）

## T6 Agent 权限下拉
文件：`src/zhuzhu_Copilot/ui/agent_panel.py`
- [x] `self.perm_btn` 插入 `tools` 于 `optimize_btn` 之后
- [x] `_open_perm_menu()`：`_FadeMenu` + 三个 action（每步确认 / 仅工具调用 / 全部访问）
- [x] `全部访问` 红色图标与红字 + `QMessageBox.question` 二次确认（默认 No）
- [x] `_on_perm_action(mode)` 写入 `agent_mode`、更新 `self._mode` 与既有引擎 `direct`、`_refresh_perm_btn()`（yolo 红染圆盘）

## T7 文案
文件：`src/zhuzhu_Copilot/locales/en_US.json`
- [x] 自动按难度 / 上下文窗口 / 每步确认 / 仅工具调用 / 全部访问 / 开启全部访问 / 三档 tooltip / 徽标片段

## T8 测试
文件：`tests/test_context_settings_ui.py`、`tests/test_client_uiux_polish.py`
- [x] 删除依赖已移除控件的用例，改为配置字段与保存链源码守卫
- [x] `_context_hint_text` 用例改传 `_context_1m` 代理
- [x] 删除自定义鼠标指针用例，改为「系统默认指针」守卫
- [x] 新增：徽标在 `model_combo` 左侧、浮层胶囊滑杆 + 1s/250ms 计时器、权限按钮在 `optimize_btn` 右侧且菜单三项、yolo 需确认且即时生效
- [x] `python -m pytest tests/test_client_uiux_polish.py tests/test_input_row_affordances.py tests/test_context_settings_ui.py -q` → 56 passed（进程退出码 0xC0000409 为既有 Qt 拆除噪音）

## T9 提交
- [x] 仅 stage 本任务文件（代码 + locales + 测试 + 本 spec 目录），提交

## T10 第二轮修订（按用户截图反馈）
- [x] 悬停闸门 `_model_tune_gate`：仅模型名称文本区（排除箭头）且下拉列表未展开时弹层
- [x] 浮层与提示气泡互斥：显示时清空模型框 tooltip，收起时恢复；点击模型框先收起浮层
- [x] 权限入口改胶囊 `_PermPill`（图标 + 当前权限文案 + 下尖角，yolo 整枚转红）
- [x] 权限菜单改为标题 + 三档双行行项（`_PermRow` + `QWidgetAction`，右侧对勾，全部访问红字）
- [x] 新增回归：闸门三态、tooltip 互斥、胶囊文案/图标、菜单标题
- [x] 三个测试文件 58 passed

## T11 第三轮修订（用户反馈：图标丑 / 提示气泡打断弹层）
- [x] 权限图标全部改用 Lucide 开源 SVG（shield / shield-alert / hand / wrench / check / chevron）
- [x] 删除 `_line_icon` 手绘 `shield` / `warning` 分支与 kind 登记
- [x] `_HoverHold` 增加 `on_enter`：进入模型名称即清掉提示气泡，避免打断 1s 弹层
- [x] 回归：SVG 图标可渲染、`on_enter` 接线、进入即清空 tooltip
- [x] 三个测试文件 58 passed

## T12 第四轮修订（用户反馈：浮层闪烁 / 输入框提示 / 权限图标区分）
- [x] `_maybe_close_model_tune` + `_pointer_over_tune`：离开宽限到期先复核指针是否仍在框/浮层（含窄缝）
- [x] 占位文案改为「今天能帮你做点什么呢？」；`_DropTextEdit` 聚焦不再清空 placeholder
- [x] 权限三档图标互不相同：`ask=hand` / `edit=wrench` / `yolo=shield-alert`，胶囊与菜单同源
- [x] 回归：占位文案聚焦保持、浮层不闪烁、三档图标唯一
- [x] 三个测试文件 59 passed

## T13 第五轮修订（勾选框/浮层偏移/权限图标/左栏收起/下拉动画/设置页/1M 滑块）
- [x] 全局 QSS：勾选框/单选框指示器加边框与选中实心（对比度）
- [x] `_ModelTunePopover.LIFT = 5`，浮层整体再上移 5px
- [x] 权限胶囊图标统一淡灰，仅全部访问转红
- [x] 左栏收起改为两枚 38px 圆形入口（folder / git-branch），点击展开并显示面板
- [x] 设置页普通 QComboBox 改 `_ArrowComboBox`（列表淡入动画）
- [x] 通用页勾选框统一缩进对齐 + `_SettingsWheelGuard` 防滚轮误改选项
- [x] 1M 上下文改 `SwitchButton` 左右滑块
- [x] 回归：勾选框 QSS、LIFT、1M 滑块信号、滚轮守卫、通用页对齐、下拉类型、左栏迷你列
- [x] 相关 UI 测试 65 passed（`test_dock_layout` 两项为既有失败，与本次无关）

## T14 第六轮修订（圆形入口语义/滑块右移/120fps/固定位置）
- [x] 收起态两枚圆形按钮改为「对话菜单 / 侧栏」，点击分别展开对话栏、展开左栏
- [x] 收起态栏容器去除圆角矩形卡片（透明、无边框）
- [x] 思考强度滑块圆右移 3px（`THUMB_SHIFT`）
- [x] `ArrowComboBox` 淡入改 120fps（8ms 定时器逐帧）
- [x] 调节浮层固定位置弹出（仅淡入/淡出，不再位移）
- [x] 回归：圆形入口 tooltip + 无方框、固定位置几何一致、滑块右移、120fps 常量
- [x] 相关 UI 测试 91 passed

## T15 第七轮修订（删除窄条/左移按钮/修复下拉崩溃与闪烁）
- [x] 对话栏收起即整栏隐藏（删除 36px 窄条），展开入口由两枚圆形按钮提供
- [x] 迷你列贴左（2px 边距，宽 42px）
- [x] `hidePopup` 不再访问已销毁 QTimer；对已结束动画停止加异常兜底
- [x] 下拉淡入改回 QPropertyAnimation，并在 showPopup 后同步置 0（消除首帧闪白与撕裂）
- [x] 回归：对话栏收起为隐藏、下拉收起无异常、丝滑淡入断言
- [x] 相关 UI 测试 44 passed（`test_dock_layout` 两项仍为既有失败）

## T16 第八轮修订（浮层遮挡/上移/固定位置）
- [x] 浮层改为锚定输入卡：底边距卡顶 10px、右边距卡右缘 12px，整块浮在卡上方
- [x] show 后 raise_()，并 QTimer 0ms 再抬一次防布局压层
- [x] `_pointer_over_tune` 改为「模型框 ↔ 浮层」并集矩形（覆盖上移后的通道）
- [x] 回归：浮层底边位于输入卡上方、固定位置一致
- [x] 三个 UI 测试文件 68 passed

## 已按 spec 执行的决策
- 浮层内「自动按难度」与滑块**独立**（拖滑块不会自动关掉自动模式）。
- 权限菜单复用 `agent_mode`（ask/edit/yolo），三项即「每步确认 / 仅工具调用 / 全部访问」。
