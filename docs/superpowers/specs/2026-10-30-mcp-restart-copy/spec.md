# MCP 重启/配置导出 + 截图坐标防漂移 设计说明

日期：2026-10-30
状态：待批准

## 1. 目标

| 编号 | 目标 | 用户可见效果 |
| --- | --- | --- |
| A1 | 单个 MCP 服务器重启 | 设置页每个服务器都能单独重启，不必重启应用 |
| A2 | 全部 MCP 服务器重启 | 一键强制重连所有已启用服务器 |
| A3 | 复制 MCP 配置 JSON | 生成标准 `mcpServers` JSON，可直接粘贴给其他 Agent（如 agnes/Claude Desktop/Cursor） |
| B | 图片缩放自校准提示 | 上下文图片被压缩缩宽时，随图附一行提示，模型改用 nx/ny 或 seen_w/h，避免整体偏移 |
| C | 截图基准防漂移 | 截图后前台窗口被关闭/最小化/换成别的窗口时，旧图坐标点击被拒绝并提示重新截图 |

## 2. 现状（已核实）

- `McpManager.reload(servers)` 只重建「新增 / 配置变更 / 脚本指纹变化」的服务器（`src/zhuzhu_Copilot/core/agent_mcp.py:517`），没有强制重启入口。
- MCP 设置页在 `_AgentSettingsDialog._build_mcp_page`（`src/zhuzhu_Copilot/ui/agent_panel.py:6858`）：`QListWidget` + 勾选停用 + 添加/编辑/删除；配置在 `_finish_save` 落盘并调用 `p._reconnect_mcp()`（:10218）。
- `AgentPanel._reconnect_mcp` / `_mcp_reload_worker`（:27979 / :27988）后台增量重连并通过 `mcp_signal` 回显状态。
- `_context_image`（`src/zhuzhu_Copilot/core/agent_engine.py:527`）降级阶梯第 3 档起会**缩宽**；调用点在 :3304，图片入库后与 `text` 组装为 tool 消息（:3375）。
- 插件截图基准 `_VIEW` + `_set_view` + `_require_fresh_shot`（`src/zhuzhu_Copilot/plugins/computer-control/server.py:3792 / :3801 / :3978`）；`_ensure_click_target`（:3893）只处理「目标被遮挡」，没有「截图后前台已换窗口」的校验。上一提交 `0136e8d` 已加入未校准坐标拦截与 seen 自校准。

## 3. 方案（决策已定，不再二选一）

### 3.1 `core/agent_mcp.py`

```python
def reload(self, servers: list, force=()) -> list:
```

- 新增可选参数 `force`：服务器名集合；`forced = {str(n) for n in (force or ())}`。
- 增量判定改为：`elif name in forced or self._configs.get(name) != want[name] or 指纹变化:` → `_drop` + `updated.append(name)`。
- `force` 中的名字若已不在 `want`（被停用/删除），仍走原「removed」分支，不复活。
- `last_reload["updated"]` 含被强制重启的服务器，供状态行统计。

### 3.2 `core/agent_skills.py`（纯函数，便于单测）

```python
_SECRET_KEY_RE = re.compile(r"(?i)(api[_-]?key|key|token|secret|password|passwd|auth)")
def mcp_servers_json(servers: list, include_secrets: bool = False) -> str:
```

- 输出 `{"mcpServers": {<name>: <cfg>}}`，`ensure_ascii=False, indent=2`。
- 跳过 `enabled is False` 的条目（接入其他 Agent 的应是当前启用集合）。
- stdio：`{"command": ..., "args": [...], "env": {...}}`；sse：`{"url": ..., "type": "sse"}`。
- `env` 保留键名；值为空不输出。键名命中 `_SECRET_KEY_RE` 且 `include_secrets=False` 时，值替换为 `"<请填入你的密钥>"`。

### 3.3 `ui/agent_panel.py`

**MCP 页按钮行**（`_build_mcp_page`）：现有 添加/编辑/删除 后追加

- `重启选中`（ghost）→ `_on_mcp_restart_selected`
- `全部重启`（ghost）→ `_on_mcp_restart_all`
- `复制配置`（ghost）→ `_on_mcp_copy`

**每行快捷操作**（满足「每行都能重启」）：

- `mcp_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)`，右键菜单：
  - `重启此服务器`
  - `复制此服务器配置`
- 右键项通过 `mcp_list.itemAt(pos)` 定位索引，逻辑复用 `_restart_mcp(name)` / `_copy_mcp(names=[name])`。

**新增方法（`_AgentSettingsDialog`）**

- `_current_mcp_name() -> str`：当前选中行的 name（无则空）。
- `_restart_mcp(name: str = "")`：`p = self.parent()`；`hasattr(p, "_restart_mcp")` → `p._restart_mcp(name or None)`；否则提示「需从主面板重启」。
  - 重启使用**已保存配置**；若该行在内存里有未保存修改，用 `QMessageBox.information` 提示「重启使用已保存配置，保存后会自动重连」。
- `_on_mcp_copy(names=None)`：按 names 过滤 `self._mcp_servers`（默认全部启用），弹出 `_McpConfigDialog`。

**新增 `_McpConfigDialog(QDialog)`**

- 只读 `QPlainTextEdit` 显示 JSON；`QCheckBox「包含密钥明文（仅自己使用）」`，切换即重新生成（默认脱敏）。
- `复制到剪贴板` 按钮：`QApplication.clipboard().setText(...)`，成功后按钮文案变「已复制」。
- 样式沿用 `_base_color/_ui`，无 emoji。

**`AgentPanel`（主面板）**

```python
def _restart_mcp(self, name: str | None = None):
    threading.Thread(target=self._mcp_restart_worker, args=(name,), daemon=True).start()

def _mcp_restart_worker(self, name):
    servers = agent_skills.load_mcp_servers()          # 只含启用项
    force = {name} if name else {s.get("name") for s in servers}
    tools = self._mcp.reload(servers, force=force)
    # 状态行：`MCP: 已重启「X」/ 全部（N 个服务器，M 个工具）；失败: ...`
```

- 复用 `mcp_signal`；失败信息取 `self._mcp.errors` 中属于该服务器的条目。

### 3.4 `core/agent_engine.py`

- 新函数 `_context_image_ex(data_url) -> tuple[str, str]`：返回 `(治理后 URL, 提示文本)`；`_context_image` 改为返回其第一项（保持既有调用/测试不变）。
- 提示仅在**发生缩宽**时非空：
  `⚠ 本图已被压缩为 {w}×{h}（原始 {ow}×{oh}）：读图得到的是压缩后像素，坐标请改用 nx/ny（0~1）或同时传 seen_w={w}/seen_h={h}，否则会整体偏移。`
- 调用点 `agent_engine.py:3304`：
  ```python
  slim = [_context_image_ex(u) for u in imgs]
  imgs = [u for u, _ in slim]
  notes = [n for _, n in slim if n]
  if notes:
      text += "\n" + "\n".join(notes)
  ```
  保证提示与图片进同一 tool 消息，模型先读提示再看图。
- `_prune_images`（:1207）继续用 `_context_image`，不追加提示（历史图无需提示）。

### 3.5 `plugins/computer-control/server.py`

- `_VIEW` 增加 `"fg": 0, "fg_rect": None`。
- `_set_view` 内记录：`fg = foreground_window()`；`_VIEW["fg"] = int(fg or 0)`；`_VIEW["fg_rect"] = (r.left, r.top, r.right, r.bottom) or None`。
- 新函数 `_shot_drift_note(x, y, target) -> str`：
  1. 无截图基准、`fg == 0`，或落点不在 `fg_rect`（截图时前台窗口矩形）内 → `""`（该处内容不由该窗口产生，多窗口并列时避免误报）。
  2. `fg` 已 `IsWindow` 失败或最小化 → 返回拒绝文案（「截图后前台窗口已关闭/最小化，画面已变，请重新 screen_shot」）。
  3. 当前前台 `== fg` → `""`（画面未切换，无漂移）。
  4. `under = window_at(x, y)`；若 `under` 为空、等于 `fg`/`target`、或 `_root_owner(under)` 等于它们 → `""`（同一窗口或其弹出菜单，交给 `_ensure_click_target` 处理遮挡）。
  5. 否则返回拒绝文案：指出「该位置原为『X』，现在已换成『Y』」，要求重新截图。
- `tool_click`：`_screen_bounds_note` 之后、`_ensure_click_target` 之前调用；命中即 `return drift`。`tool_long_press` 同样加一处。
- `plugin.json` `revision` 28 → 30（发布后补了一次防误报分支，需再 bump 才能同步到运行目录）。
- `SKILL.md` 增补两条操作纪律：
  - 每次 `screen_shot` 前先确认前台是目标应用（`list_windows`/`manage_window activate`）。
  - 截图后如果自己或用户切了窗口，坐标会失效，必须重新截图。

## 4. 不做的事（避免过度工程）

- 不把 `QListWidget` 改成 `QTableWidget`；每行操作走右键菜单 + 顶部「重启选中」。
- 不新增 MCP 协议能力，不改工具聚合命名规则。
- 不做像素级整屏指纹比对（成本高且误报多），只做「前台窗口一致性 + 落点顶层窗口」校验。
- 不引入任何 mock/假数据到产品代码。

## 5. 验证

1. 单测：
   - `tests/test_agent_mcp.py`：`force` 只重启指定服务器，其他连接对象不变；`last_reload.updated` 正确。
   - `tests/test_mcp_config_export.py`（新增）：JSON 结构、stdio/sse 字段、停用过滤、密钥脱敏/明文开关。
   - `tests/test_context_image_guard.py`：大图治理返回提示；小图/非图无提示；`_context_image` 行为不变。
   - `tests/test_shot_baseline_guard.py`（新增）：`_set_view` 记录前台；前台未变/同窗口放行；前台被关或换窗口时拒绝。
2. 命令（PowerShell 用 `;` 串联）：
   - `python -m pytest tests/test_agent_mcp.py tests/test_mcp_config_export.py tests/test_context_image_guard.py tests/test_shot_baseline_guard.py tests/test_computer_control_plugin.py -q`
   - 全量 `python -m pytest -q`
3. 插件同步：`python -c "import sys; sys.path.insert(0,'src'); from zhuzhu_Copilot.core import agent_plugins as p; p.ensure_shipped_plugins()"`
4. 手工验收（可选）：设置页右键任一服务器「重启此服务器」→ 状态行出现重启结果；「复制配置」→ 粘贴到 agnes 可用。
5. 自审后 git 提交（仅本次相关文件）。

## 6. 风险

- `force` 重启会中断该服务器进程内状态（这正是「重启」的语义），UI 文案需写明。
- 防漂移可能把「截图后用户手动切走窗口」的旧坐标拒绝一次，代价是重新截图；这是刻意的安全取舍。
- 复制配置默认脱敏，用户接其他 Agent 时需自己填回密钥（对话框提供明文开关）。
