# 任务清单：常驻抽屉 + 功能胶囊栏

对应 spec：`spec.md`（同目录）

## T1 模块级基础件
文件：`src/zhuzhu_Copilot/ui/agent_panel.py`
- [x] 常量 `_DRAWER_W/_DRAWER_ROW_H/_DRAWER_SEARCH_H/_RAIL_W/_RAIL_BTN_D/_RAIL_DIV_W`
- [x] `_rel_time(ts)`
- [x] `_line_icon` 新增 `more` + 登记 `_LINE_ICON_KINDS`
- [x] `_SessionRowDelegate`
- [x] `_SessionDrawer`
- [x] `_FunctionRail`（含 `_RAIL_ITEMS`、`column()`、`anchor_pos()`、`apply_theme()`）

## T2 接入主面板
文件：`src/zhuzhu_Copilot/ui/agent_panel.py`
- [x] `build_default_ui` 创建抽屉与胶囊列，`_dock_row` 顺序：抽屉 | dock_left | body | dock_right | 胶囊列
- [x] 顶栏移除 `session_combo` 与 `new_btn`；组合框 `setParent(self)+hide()` 保留逻辑
- [x] `_refresh_session_combo` 复用 `lst` 调 `drawer.refresh`
- [x] `_on_drawer_session` / `_refresh_panel_local_qss` 补 `apply_theme`
- [x] 转圈刷新点补 `refresh_status`
- [x] 统计浮层按胶囊预留宽度收缩/右移，避免盖住胶囊

## T3 胶囊动作
文件：`src/zhuzhu_Copilot/ui/agent_panel.py`
- [x] `_on_rail_action` + `_rail_handlers` 映射
- [x] `_reveal_feature`（任务/工作树：写 setting + sync + show/raise）
- [x] `_open_rail_more_menu`
- [x] `_open_settings(page_index=...)` 参数与 `dlg.nav.setCurrentRow`
- [x] 新增文案补 `locales/en_US.json`（搜索/相对时间/胶囊 tooltip/删除会话）

## T4 测试
文件：`tests/test_client_uiux_polish.py`、`tests/test_input_row_affordances.py`
- [x] 新增 6 个抽屉/胶囊用例
- [x] 会话下拉自绘箭头用例适配隐藏态（临时 show 抓图）
- [x] `python -m pytest` 上述四文件全绿（41 passed）

## T5 真机验证
- [x] `python src/main.py` 启动，computer-use 截图确认抽屉/胶囊/跳转
- [x] 点击抽屉会话 → 对话内容切换、选中态跟随
- [x] 点击胶囊 chart → 上下文统计浮层（不遮挡胶囊）
- [x] 点击胶囊「…」→ 设置菜单；选 Skills → 设置页 Skills 分页
- [x] 点击胶囊 MCP → 设置页 MCP Servers 分页
- [x] 窄窗几何：抽屉/胶囊各占固定列，不重叠聊天区（1100 宽下由布局最小宽兜底）

## T6 提交
- [x] 仅 stage 本任务文件与 spec，提交（14d5ba8）
