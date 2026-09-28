# 修复 @工作流切换与共享上下文全开

## Context（背景与目标）

用户在真实使用中发现三个问题，已完成根因定位（均已用只读脚本复现确认）：

1. **@工作流无法切换 / 问 hi 人格不统一**：引擎层切换已验证正确（`_rebuild_engine` + `_system_prompt` 按 engine.workflow 重建人格正常）。用户感受到的"没切换"来自 `@工作流名+任务正文` 的**任务完成后自动切回原工作流**设计（`wf_return` 机制，提交 3f86c40 引入）：任务结束静默回到原工作流，再问 hi 就回复成原工作流人格。用户已确认：**改为永久切换**，删除 wf_return 自动切回。
2. **sub_coding_agent 提示未知的子 agent**：`agent_subagent.subagent_tool()` 对调用名去一次 `sub_` 前缀，注册名自带 `sub_` 前缀（`sub_coding_agent`）时永久无法命中（已复现返回 None）；`subagent_schemas()` 又把工具名生成为 `sub_` + n，出现 `sub_sub_coding_agent` 双前缀。两条都导致 `[未知工具] sub_coding_agent`。
3. **共享上下文默认关**：`register_subagent` 的 `allow_chat/share_context` 默认 False、`shared_context` 默认 None；`run_sub_agent/run_agent_llm/dispatch_sub_agents` 默认 False；主引擎任务开始只读活跃空间从不自动开启。用户已确认：**全部默认开启 + 存量 migration**，使随便调用子 agent / 工作流都能查看所有上下文并直接应答。

约束：改动最小侵入、不破坏既有测试体系（pytest + `.github/workflows/ci.yml` 全量 `python -m pytest tests -v`），代码简洁、无硬编码、扩展友好。

## 阶段 A：@工作流 永久切换（移除 wf_return 自动切回）

文件：`src/zhuzhu_Copilot/ui/agent_panel.py`

- `_new_sess_state`（约 L10971）：删除 `"wf_return": ""` 键。
- `_send`（约 L15947-15951）：删除 `kind == "workflow" and (text or images)` 时记录 `st["wf_return"]` 的整块代码与注释；@工作流 一律走 `_switch_session_workflow` 持久切换，带正文则正常发送任务。
- 任务完成收尾块（约 L17484-17506）：删除消费 `wf_return` 自动切回逻辑（含 `__global__` 分支、`_rebuild_engine(display_wf=None)` 恢复跟随全局），仅保留其后的 `_flush_queue/_scroll_bottom` 等后置动作。
- `_switch_session_workflow` 内 space 继承逻辑（约 L15895-15915）**保留不动**。

## 阶段 B：sub_coding_agent 前缀归属修复

文件：`src/zhuzhu_Copilot/core/agent_subagent.py`

- `subagent_tool()`（约 L917-920）：命中条件改为 `it["name"] == n or it["name"] == cand`（先直接命中，再匹配去前缀候选），修复自带 `sub_` 前缀注册名的永不可达问题。
- `subagent_schemas()`（约 L971）：工具名改为 `n if n.startswith("sub_") else "sub_" + n`（与 `register_subagent`（L860）tool_name 逻辑对齐），消除 `sub_sub_coding_agent` 双前缀。
- `_resolve_at_route`（L15816）经 subagent_tool 自动生效，无需改动；`subagent_tool_names` 已含两种形式，不动。

## 阶段 C：共享上下文默认全开 + 存量迁移

文件：`src/zhuzhu_Copilot/core/agent_subagent.py`

- `register_subagent()`（L813-816）：参数默认改为 `shared_context=True, allow_chat=True, share_context=True`；同步 docstring 与 tips 文案（L862-879）。
- `registered_subagents()`（L805-806）：解析缺省值改为开启（字段缺失按 True），并新增幂等迁移 `_migrate_shared_defaults(f, data)`：对每条目将 `allow_chat/share_context/shared_context` 显式补为 true，有变更则以 `json.dumps(data, ensure_ascii=False, indent=2)` 回写文件，回写后重读 `f.stat()` 刷新 `_SUB_CACHE` 指纹（避免下次重复迁移/缓存失效问题）。注意：显式 false 按决定也翻转为 true（存量全部迁移）。
- `run_sub_agent()`（L200）/ `run_agent_llm()`（L495）/ `dispatch_sub_agents()`（L401）：`shared_context` 默认值 False → True。

文件：`src/zhuzhu_Copilot/core/agent_tools.py`

- `_register_sub_agent`（L5051-5053）：`allow_chat/share_context` 不再用 `bool(args.get(...) or False)` 强制关，改为 `args.get("allow_chat")` 缺省 None 方式让 `register_subagent` 新默认生效（shared_context 已直传 None）。
- `register_sub_agent` 工具 schema（约 L1753-1790）：`allow_chat/shared_context/share_context` 参数 description 改为"缺省全开"，与实现一致。

文件：`src/zhuzhu_Copilot/core/agent_engine.py`

- `_run_subagent_tool`（L863）：`batch_shared = bool(args.get("shared_context", True))`（默认开启整批共享）。
- 任务开始处（L1508-1525）：`agent_context.active_space()` 为空时先 `open_space(seed=user_input 前 200 字, owner="main", activate=True)` 再拉快照注入；子 Agent 侧 `bind_shared_context` 已自带自动建空间（DEFAULT_SPACE），无需改动。

文件：`src/zhuzhu_Copilot/ui/agent_panel.py`

- `_subagent_worker`（L16185）：`shared_context=bool(conf.get("shared_context"))` 已随注册默认值生效（迁移后为 True），无需改动，仅确认注释同步（可选）。

文件：`src/zhuzhu_Copilot/core/workflow_templates/presets/zhuzhu_copilot/subagents.json`

- `explorer_project_agent` / `sub_coding_agent` 两条 `shared_context: false` 显式改为 `true`（sansheng_liubu 预设已全 true）。用户副本（`%USERPROFILE%\.zhuzhu_Copilot\workflows\*\subagents.json`）由阶段 C 的迁移函数处理。

`_chat_with` / `_look_context` 权限门（agent_tools.py L5121/L5144）保留：显式 false 仍拒绝，默认随注册开启。

## 测试改动与新增

更新（与既有断言冲突处同步修改）：

- `tests/test_wf_return.py`：重写为断言 `_new_sess_state` 不再含 `wf_return` 键、`@工作流` 切换持久（`_emit` 记录块删除后无切回目标、任务收尾不再消费切回），保留 `_mk_panel` 直测模式。
- `tests/test_agent_collab_perms.py` `test_register_subagent_perms`（L66-72）：缺省断言 False → True；`test_chat_with_permission_gate` / `test_look_context_permission_gate`（L89/L117）改为显式传 `allow_chat=False` / `share_context=False` 维持拒绝用例。
- `tests/test_shared_context.py`（L186-190 断言 `shared_context is None` 处）：同步为 True（实施时逐处核对涉及 shared_context 缺省语义的断言）。
- `tests/test_sub_agent_parallel.py` / `tests/test_agent_team*.py`：核对 shared_context 相关断言，随默认值翻转同步（如有）。

新增：

- `tests/test_subagent_prefix.py`：`subagent_tool("sub_coding_agent")` 命中注册名自带前缀者；`subagent_schemas` 生成名无双前缀（`sub_coding_agent` 而非 `sub_sub_coding_agent`）；面板 `_resolve_at_route("@sub_coding_agent")` 解析为 subagent 路由（offscreen Qt 模式）。
- `tests/test_wf_return.py`（并入重写）：`@工作流名+正文` 不记录 wf_return、任务结束后工作流保持不变。
- `tests/test_shared_defaults.py`：register_subagent 缺省 allow_chat/share_context/shared_context 全为 True、显式传 False 保持 False；存量 subagents.json（模拟旧值）读取后自动迁移回写且 `_SUB_CACHE` 指纹同步；引擎任务开始无活跃空间时自动 `open_space(owner="main")`。

## 验证

1. 全量回归：`python -m pytest tests -v`（CI `.github/workflows/ci.yml` 同款命令，安全引擎/工作团全量通过）。
2. 重点回归：`python -m pytest tests/test_agent_collab_perms.py tests/test_shared_context.py tests/test_wf_return.py tests/test_sub_agent_parallel.py tests/test_builtin_workflow.py tests/test_agent_team_run.py tests/test_agent_bus.py -v`。
3. 人工要点（实现后自查）：`_resolve_at_route("@sub_coding_agent")` 返回 subagent 路由；`_switch_session_workflow` 后 `_system_prompt` 正确切换人格且不再自动切回；`run_sub_agent` 默认绑定共享空间并回写结论。
4. 自审后按用户规则提交 git（可分阶段提交：wf_return 下线 / 前缀修复 / 共享全开，各含对应测试）。