# 常驻左侧历史对话抽屉 + 右侧功能胶囊栏 设计说明

日期：2026-11-01
状态：待批准
范围：`src/zhuzhu_Copilot/ui/agent_panel.py`、`tests/`（仅本任务用例）

## 1. 目标

| 编号 | 目标 | 用户可见效果 |
| --- | --- | --- |
| A1 | 左侧常驻「历史对话抽屉」 | 对话列表固定可见，支持搜索、切换、新建、右键删除；顶栏不再有会话下拉 |
| A2 | 右侧常驻「功能胶囊栏」 | 细长圆角白胶囊，竖排淡灰线条图标 + 细分隔线 + 底部 `…`；点击直接跳到对应功能页 |
| A3 | 状态同步 | 会话增删改/切换/运行中/待确认都在抽屉实时反映，与既有逻辑一致 |
| A4 | 视觉一致 | 纯黑 / 淡灰 / 白 / 深蓝；无 emoji；圆角取 tokens；不描边（招 1） |

参照图：用户提供的 TRAE 右侧胶囊 —— 窄竖向圆角白色岛，图标按钮等距、两组分隔线、底部 `…`。

## 2. 现状（已核实）

- 顶栏 `build_default_ui` 在 `agent_panel.py:20290-20331` 创建 `self.session_combo`（`_ArrowComboBox` + `_SessionStatusDelegate`）与 `self.new_btn`，`top.addWidget(...)` 直接排在顶栏。
- 会话数据：`_load_session_list()`（:20970）/ `_save_session_list()`（:20978）；切换 `_switch_to()`（:21960）；新建 `_new_session()`（:22243）；删除 `_remove_session()`（:29769，含确认框）；刷新入口 `_refresh_session_combo()`（:21942，被 8 处调用）。
- 会话运行态：`self._sess[sid]` 的 `task_active` / `pending_confirm` / `pending_ask`；自绘状态见 `_SessionStatusDelegate`（:18753）；转圈角度 `self._spin_angle` 在 :25075 刷新时重绘。
- 右侧现有入口：`_toggle_preview()`（:22561）、`toggle_token_stats()`（:24700）、`toggle_copilot_panel()`（:24854）；任务/工作树/Git 面板开关写入 `agent_show_todos` / `agent_show_worktree` 后由 `_sync_todos_win()` / `_sync_wt_win()` / `_sync_git_win()` 同步（:23785 / :23948 / :23828）。
- 设置页导航：`_NAV_ITEMS`（:1353）0 通用与记忆、6 技能、7 MCP 服务器、14 工作流、15 背景、17 会员与积分；对话框导航控件 `dlg.nav`（:5225）。
- 换肤快路径 `_refresh_panel_local_qss()`（:26546）逐控件重设构造期写死的 QSS。
- 线条图标工厂 `_line_icon()`（:1521），已登记集合 `_LINE_ICON_KINDS`（:1785），暂无 `more`。

## 3. 方案（决策已定）

### 3.1 新增模块级组件（`agent_panel.py`，`_SessionStatusDelegate` 之后）

**常量**
```python
_DRAWER_W = 244          # 抽屉固定宽（大厂同款侧栏档位）
_DRAWER_ROW_H = 54       # 会话行高
_DRAWER_SEARCH_H = 30
_RAIL_W = 56             # 胶囊宽（圆角取半宽 = 28，见 pill_radius）
_RAIL_BTN_D = 40         # 图标按钮 40x40，胶囊内左右各留 8
_RAIL_DIV_W = 24         # 分隔线长度
```

**`_rel_time(ts: float) -> str`**
- `<60s` → `_ui("刚刚")`；`<1h` → `_uif("{a0} 分钟前", a0=n)`；`<24h` → `_uif("{a0} 小时前", a0=n)`；`<7d` → `_uif("{a0} 天前", a0=n)`；否则 `datetime.fromtimestamp(ts).strftime("%Y-%m-%d")`。异常回退空串。

**`_SessionRowDelegate(QStyledItemDelegate)`**
- `sizeHint` → `QSize(w, _DRAWER_ROW_H)`。
- `paint`：选中 → `_mix_hex(ACCENT, PANEL, 0.12)` 圆角 `RADIUS_SM` 内缩 2px 填充；hover → `HOVER`；主行会话名 13px `TEXT` 右省略；副行 `_rel_time` 11px `TEXT_DIM`；右侧状态点（`task_active` → `ACCENT_HOVER` 实心 6px；`pending_*` → `WARN` 6px），数据取自 `panel._sess`，与下拉 delegate 同源。
- 行数据：`DisplayRole` 名称、`UserRole` sid、`UserRole+1` updated。

**`_SessionDrawer(QFrame)`**
- `objectName("sessionDrawer")`；`setFixedWidth(_DRAWER_W)`；QSS `background: {_panel_surface('PANEL')}; border: none; border-radius: {RADIUS_LG}px;`（不描边，靠明度分层）。
- 结构：标题行（`QLabel(_ui("对话"))` 13px/600 + stretch + 新建按钮 28x28，`_line_icon("new",16,TEXT_DIM)`，皮肤复用 `_BTN_ICON`）→ 搜索框 `QLineEdit`（`objectName("sessionSearch")`，高 30，`FIELD` 底 + `RADIUS_SM`，placeholder `_ui("搜索对话")`）→ `QListWidget`（无边框透明底、`_scrollbar_css(6,3,both=False)`、`_SessionRowDelegate`）。
- 信号：`session_activated(str)` / `new_requested()` / `delete_requested(str, str)`。
- `refresh(sessions: list, current_sid: str)`：按 `updated` 倒序；`clear()` 后 `addItem`（DisplayRole=`_uim(name or "新对话")`，UserRole=sid，UserRole+1=updated）；选中 `current_sid`；末尾重新套用当前搜索过滤。
- `_apply_filter(text)`：只 `setHidden` 不删除条目，保证搜索可逆。
- 右键菜单（列表 `CustomContextMenu`）：`_uif("删除对话「{a0}」", a0=name)` → `delete_requested`。
- `refresh_status()`：`list.viewport().update()`（供转圈/待确认刷新）。
- `apply_theme()`：按当前色板重设自身 QSS + 搜索框 QSS（换肤快路径调用）。

**`_FunctionRail(QFrame)`**
- `objectName("functionRail")`；`setFixedWidth(_RAIL_W)`；QSS `background: {_panel_surface('PANEL')}; border: none; border-radius: {pill_radius(_RAIL_W)}px;`。
- 条目表（数据驱动，避免硬编码分支）：
  ```python
  _RAIL_ITEMS = (
      ("tasks",    "list",    "任务与后台"),
      ("worktree", "folder",  "工作树与文件"),
      ("stats",    "chart",   "上下文统计"),
      ("__div__",  "",        ""),           # 分隔线
      ("preview",  "preview", "预览面板"),
      ("mcp",      "server",  "MCP / 能力状态"),
      ("copilot",  "apps",    "zhuzhu Copilot"),
      ("__div__",  "",        ""),
      ("more",     "more",    "更多功能"),
  )
  ```
- 每个按钮 40x40、`_line_icon(kind, 20, TEXT_DIM)`、无色块（透明底），hover 用 `_mix_hex(BORDER, PANEL, 0.18)` 圆角 10；tooltip = `_ui(文案)`；`clicked` → `action_triggered.emit(kind)`。
- 分隔线：`QFrame` 高 1、宽 `_RAIL_DIV_W`、居中，颜色 `_mix_hex(BORDER, PANEL, 0.35)`。
- `more` 按钮几何对面板可见：暴露 `anchor_pos(kind) -> QPoint`（`btn.mapToGlobal(btn.rect().bottomLeft())`）。
- 垂直居中：外层 `_rail_col`（宽 `_RAIL_W`、透明）内 `addStretch(1)` + 胶囊 + `addStretch(1)`，胶囊高度由内容决定（不拉满窗口）。
- `apply_theme()` 同上。

### 3.2 `_line_icon` 新增 `more`
在 `p.end()` 前追加：
```python
elif kind == "more":        # 更多（三个实心圆点）
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    for _dx in (0.30, 0.50, 0.70):
        p.drawEllipse(QPointF(s * _dx, s * 0.50), s * 0.055, s * 0.055)
```
并把 `"more"` 加入 `_LINE_ICON_KINDS`。

### 3.3 `build_default_ui` 改造
1. 删除 `top.addWidget(self.session_combo)`，改为（保留组合框对象以承载 20+ 处既有逻辑与测试）：
   ```python
   self.session_combo.setParent(self)
   self.session_combo.hide()
   ```
2. 删除 `self.new_btn` 创建块（:20323-20331）及换肤快路径里的 `new_btn` 一行（:26570）——新建入口由抽屉承担，不留隐形重复控件。
3. 在 `_dock_row` 组装处（:20719）改为：
   ```python
   self._dock_row.addWidget(self.session_drawer, 0)
   self._dock_row.addWidget(self._dock_left, 0)
   self._dock_row.addLayout(body, 1)
   self._dock_row.addWidget(self._dock_right, 0)
   self._dock_row.addWidget(self._rail_col, 0)
   ```
4. 创建位置在 `self._dock_right = ...` 之后：
   ```python
   self.session_drawer = _SessionDrawer(self)
   self.session_drawer.session_activated.connect(self._on_drawer_session)
   self.session_drawer.new_requested.connect(lambda: self._new_session())
   self.session_drawer.delete_requested.connect(self._remove_session)
   self._function_rail = _FunctionRail(self)
   self._function_rail.action_triggered.connect(self._on_rail_action)
   self._rail_col = _FunctionRail.column(self._function_rail)   # 透明列 + 上下 stretch
   ```

### 3.4 面板方法
- `_on_drawer_session(sid)`：`sid and sid != self._session_id` → `self._switch_to(sid)`。
- `_refresh_session_combo()` 末尾追加：
  ```python
  drawer = getattr(self, "session_drawer", None)
  if drawer is not None:
      drawer.refresh(self._load_session_list(), self._session_id)
  ```
  （`lst` 已在方法开头加载，直接复用 `lst`，避免二次读盘。）
- `_on_rail_action(kind: str)`：字典分派（`_RAIL_ACTIONS` 映射到方法名），全部走既有入口：
  - `tasks` → `_reveal_feature("agent_show_todos", ("_sync_todos_win",), "todos_win")`
  - `worktree` → `_reveal_feature("agent_show_worktree", ("_sync_wt_win", "_sync_git_win"), "wt_win")`
  - `stats` → `self.toggle_token_stats()`
  - `preview` → `self._toggle_preview()`
  - `mcp` → `self._open_settings(page_index=7)`
  - `copilot` → `self.toggle_copilot_panel()`
  - `more` → `_open_rail_more_menu()`
- `_reveal_feature(key: str, syncs: tuple, win_name: str)`：`qsettings.setValue(key, "1")`；逐个调用 sync；再 `win.show(); win.raise_()`（dock 内子控件同样适用）。
- `_open_rail_more_menu()`：`QMenu(self)`，条目 → `_open_settings(page_index=i)`：通用与记忆 0、技能 6、工作流 14、背景 15、会员与积分 17；`exec(self._function_rail.anchor_pos("more"))`。
- `_open_settings(self, *_args, highlight_sid: str = None, page_index: int = None)`：
  exec 前 `QTimer.singleShot(0, lambda: dlg.nav.setCurrentRow(page_index))`（仅 `page_index` 非 None 且 `0 <= page_index < dlg.nav.count()`；异常吞掉不影响打开）。
- `_refresh_panel_local_qss()` 追加 `drawer.apply_theme()`、`rail.apply_theme()`（`getattr` 守卫 + `try/except RuntimeError`）。
- 转圈/状态重绘点（:25075）追加 `self.session_drawer.refresh_status()`（存在性守卫）。

### 3.5 测试
`tests/test_client_uiux_polish.py` 新增：
1. `test_session_drawer_persistent_left_column`：`panel.session_drawer` 存在、可见、`parent() is panel`、`_dock_row.indexOf(...) == 0`、宽 244、含 `sessionList` / `sessionSearch` / 新建按钮。
2. `test_session_drawer_matches_session_list`：新建会话后 `drawer 条目数 == combo.count()`，当前项为 `panel._session_id`。
3. `test_session_drawer_switches_session`：直接 emit `session_activated(other_sid)` → `panel._session_id == other_sid`。
4. `test_function_rail_capsule_and_icons`：`_function_rail` 可见、宽 56、QSS 含 `border-radius: 28px` 与 `border: none`；按钮 ≥7 个、均无文字（纯图标）、无 emoji；分隔线 ≥2。
5. `test_function_rail_dispatch`：monkeypatch `panel.toggle_token_stats` / `panel._open_settings`，分别触发 `stats`、`mcp`，断言被调用且 `mcp` 传 `page_index=7`。
6. `test_session_combo_leaves_top_bar`：`panel.session_combo.isHidden()` 且 `parent() is panel`（逻辑仍可调用）。

`tests/test_input_row_affordances.py`：
- `test_session_combo_arrow_is_actually_painted` 改为「临时 show 再 grab 再 hide」，避免隐藏控件抓图无效。

### 3.6 验收
- `python -m pytest tests/test_client_uiux_polish.py tests/test_input_row_affordances.py tests/test_session_naming.py tests/test_session_name_display.py -q` 全绿（既有 4+2 个历史失败不在本任务范围）。
- 真机验证：`python src/main.py` 启动后，用 computer-use（`screen_shot` / `click`）确认：
  1. 左侧抽屉常驻、可搜索、点击切换、新建；
  2. 右侧胶囊竖排图标、分隔线、`…`，点击统计/预览/MCP 各自打开对应浮层或设置页；
  3. 窗口缩放至 1100×760 时抽屉与胶囊不重叠聊天区、无横向滚动条。
- 只提交本任务文件（`agent_panel.py`、两个测试文件、本 spec 目录）；仓库内其他并发改动一律不 stage。
