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

## 已按 spec 执行的决策
- 浮层内「自动按难度」与滑块**独立**（拖滑块不会自动关掉自动模式）。
- 权限菜单复用 `agent_mode`（ask/edit/yolo），三项即「每步确认 / 仅工具调用 / 全部访问」。
