# 任务清单：MCP 重启/配置导出 + 截图防漂移

对应 spec：`spec.md`（同目录）

## T1 核心：单服务器强制重连
文件：`src/zhuzhu_Copilot/core/agent_mcp.py`
- [x] `McpManager.reload(self, servers, force=())`
- [x] 强制集合命中即 `_drop` + `updated`，未在 `want` 中仍走 removed
- [x] `last_reload` 反映强制重启

## T2 配置导出纯函数
文件：`src/zhuzhu_Copilot/core/agent_skills.py`
- [x] `_SECRET_KEY_RE`
- [x] `mcp_servers_json(servers, include_secrets=False)`
- [x] 过滤停用项、stdio/sse 字段、env 脱敏

## T3 设置页：重启按钮 + 右键菜单 + 复制配置
文件：`src/zhuzhu_Copilot/ui/agent_panel.py`
- [x] `_build_mcp_page` 增加 重启选中 / 全部重启 / 复制配置
- [x] `mcp_list` 右键菜单（重启此服务器 / 复制此服务器配置）
- [x] `_current_mcp_name`、`_restart_mcp`、`_on_mcp_restart_selected`、`_on_mcp_restart_all`、`_on_mcp_copy`
- [x] `_McpConfigDialog`（只读 JSON + 明文开关 + 复制到剪贴板）

## T4 主面板单服务器重启
文件：`src/zhuzhu_Copilot/ui/agent_panel.py`（`AgentPanel`）
- [x] `_restart_mcp(name=None)`
- [x] `_mcp_restart_worker(name)`，状态行含服务器名/工具数/失败原因

## T5 图片压缩自校准提示
文件：`src/zhuzhu_Copilot/core/agent_engine.py`
- [x] `_context_image_ex`（缩宽时才出提示）
- [x] `_context_image` 委托，行为不变
- [x] 调用点 :3304 把提示拼进 tool 文本

## T6 截图基准防漂移
文件：`src/zhuzhu_Copilot/plugins/computer-control/server.py`
- [x] `_VIEW` 增加 `fg` / `fg_rect`，`_set_view` 记录
- [x] `_shot_drift_note(x, y, target)`
- [x] `tool_click` / `tool_long_press` 接入
- [x] `plugin.json` revision 30；`SKILL.md` 两条纪律

## T7 回归测试
- [x] `tests/test_agent_mcp.py`：force 重启单个
- [x] `tests/test_mcp_config_export.py`（新增）
- [x] `tests/test_context_image_guard.py`：提示开/关
- [x] `tests/test_shot_baseline_guard.py`（新增）

## T8 验证与交付
- [x] 定向 pytest + 全量 pytest（全量中的既有失败已在 HEAD worktree 复现，与本改动无关）
- [x] 同步插件到运行目录（revision 30 已生效）
- [x] 自审 + git 提交（仅本次文件）

## 执行顺序
T1 → T2 → T3 → T4 → T5 → T6 → T7 → T8
