# 切换工作流后 AI 人设/风格不统一——重写各工作流人设

## Context（背景与目标）

用户反馈：在当前对话中切换工作流后，AI 人设、回答风格**几乎不变**，看起来像"同一个 AI 在角色扮演"。

根因（已查证，非代码 bug）：
- 各工作流引擎都复用**同一套占绝对主体的统一系统提示**——[agent_skills.build_system_prompt](file:///c:/Users/zhuzhu/Desktop/my%20first%20android%20app/src/winapp_migrator/core/agent_skills.py#L2068-L2213) 拼出的"基础规则/工具清单/执行规范/提问机制/记忆/强制要求"等（占全文约 90%）对**所有工作流一视同仁**；
- 各工作流 agent.py 的 `SYSTEM_PROMPT` 只提供"身份 + 职责 + 工作方式"这段（约占 10%），且多数写得很"功能化、无口吻"（如 backend_dev/product_dev/frontend_design 都是"你是XX工程师…语言：简体中文"），风格上没有记忆点；
- `_system_prompt`（[agent_engine.py L1195-1247](file:///c:/Users/zhuzhu/Desktop/my%20first%20android%20app/src/winapp_migrator/core/agent_engine.py#L1195-L1247)）的覆盖链是正确的：`persona`（自定义 Agent）> 工作流 `SYSTEM_PROMPT/build_system_prompt` > `_wf_fallback_persona`（工作流名派生兜底）> 默认 `zhuzhu Copilot`。切换链路（`@工作流` → `_switch_session_workflow` → 清 `st["agent"]`+`eng.persona` → `_rebuild_engine`）上一轮已验证正确。
- 即：**切换机制有效，但工作流人格内容太"平"，被统一风格段稀释**，所以观感像"没换人"。

目标（用户已拍板）：**重写各工作流 agent.py 的 SYSTEM_PROMPT**，让每个工作流有肉眼可辨的身份、口吻、开场与行文风格，且保留原有职责/协作/规则约束，不破坏任何机制。

## 方案：重写各工作流人设（不动代码逻辑）

### 原则
- 只改 `SYSTEM_PROMPT`/`build_system_prompt` 文本，不改函数签名、不改引擎/工具/技能逻辑。
- 每个工作流人设含：**身份定场（鲜明角色+说话风格）、职责与协作方式（保留现有）、语言与行文规范（带风格）、开场/收尾范式（可辨识）**。
- 保留硬性约束：中文回复、先思考再行动、`shared_context` 写回、产品经理"禁止操刀"红线、三省六部文言文（已有完整人设，基本不动，仅校对）。
- 源与用户副本**双写一致**：预设源 `src/winapp_migrator/core/workflow_templates/presets/<wf>/agent.py` 与用户目录 `%USERPROFILE%\.winapp_migrator\workflows\<wf>\agent.py` 同步更新（新机种子、重装、@切换都用用户目录副本）。

### 各工作流人设要点（按现有职责重构，风格差异明显）

| 工作流 | 身份定场（风格） | 保留的职责/约束 |
|---|---|---|
| **product_manager** | 产品经理：冷静克制、一句话切中要害的"总指挥"口吻，开场先"把需求拆成谁做什么"，收尾给"方案/风险/下一步"；刻意不用客套 | 总派发、监督（look_context/chat_with/warn/pause/resume）、共享空间汇总、**禁止操刀红线**、中文 |
| **backend_dev** | 后端工程师：严谨、爱列"接口/数据/性能/安全"四要素清单，口吻偏工程文档风，少废话直给 | API/服务/数据层开发、先读后改、shared_context 写回、中文 |
| **frontend_design** | 前端设计师：重"视觉与体验"表达，口吻带设计评审感（"这里交互/这里层级/这里可读性"），会提样式与组件建议 | UI/UX 设计、页面/组件实现、设计规范、shared_context 写回、中文 |
| **product_dev** | 产品开发：务实执行者，开场确认"做出来什么样、边界在哪"，收尾"改了什么/注意什么" | 功能实现、业务逻辑、接口对接、先读后改、shared_context 写回、中文 |
| **product_debug** | 调试工程师：怀疑论者，开场先复现再定位，口吻像排查现场（"根因/复现/验证"三连），拒绝臆断 | 测试/调试/问题定位、先复现后修、shared_context 写回、中文 |
| **g9_study_helper** | 学习助手：循循善诱、先给思路再给答案，口吻耐心（"先想清楚……"） | 现有 g9_study_helper 职责（学习辅导/计划），保留其自定义结构 |
| **zhuzhu_copilot** | 默认工作流：继承内置 zhuzhu Copilot 人格（不变） | 原有人格与全能力 |
| **sansheng_liubu** | 中书省：文言文（已有完整人设，仅校对不改） | 三省六部编队、文言文、共享空间 |

### 文件清单（双写一致）

用户目录（运行时生效，@切换即用）：
- `%USERPROFILE%\.winapp_migrator\workflows\product_manager\agent.py`
- `%USERPROFILE%\.winapp_migrator\workflows\backend_dev\agent.py`
- `%USERPROFILE%\.winapp_migrator\workflows\frontend_design\agent.py`
- `%USERPROFILE%\.winapp_migrator\workflows\product_dev\agent.py`
- `%USERPROFILE%\.winapp_migrator\workflows\product_debug\agent.py`
- `%USERPROFILE%\.winapp_migrator\workflows\g9_study_helper\agent.py`（只在其 SYSTEM_PROMPT/build_system_prompt 上增强风格，保留其结构）

预设源（新机种子/重装一致）：
- `src/winapp_migrator/core/workflow_templates/presets/{product_manager,backend_dev,frontend_design,product_dev,product_debug,g9_study_helper}/agent.py`（与用户目录同步；g9_study_helper 若预设源不存在则只改用户目录）

不改：`zhuzhu_copilot`（继承默认人格）、`sansheng_liubu`（文言文完整人设）、`agent_engine/agent_skills/agent_panel` 等代码。

## 验证

1. **语法与契约**：`python -m compileall -q` 通过；每个改过的 agent.py 仍导出 `AGENT_NAME`、`SYSTEM_PROMPT`/`build_system_prompt`、`on_task_start`、`on_task_end`（用 `agent_workflow.agent_hooks(wf)` 真实加载验证非空）。
2. **切换后人格生效（重点回归）**：写一个一次性只读脚本——构造 `AgentPanel`（`__new__` 桩，避免后台线程崩溃），`_switch_session_workflow` 依次切 `product_manager/backend_dev/frontend_design/product_dev/product_debug`，每步取 `engine._system_prompt("")`，断言**各工作流系统提示前 200 字互不相同**（风格段差异）且含各自身份关键词；再切回 `_default` 断言恢复默认人格。
3. **全量回归**：`python -m pytest tests -q`（工作团/共享上下文/工作流相关测试都跑），确认无回归（本改动只动 prompt 文本，不应影响逻辑测试；若有 prompt 断言用例需同步）。
4. **人工确认（可选）**：启动应用，@各工作流问一句"你是谁/介绍一下你的风格"，确认口吻明显不同。
5. 自审后按项目规则提交 git（注意上一轮仓库对象库损坏的恢复问题，必要时用 commit-tree 路径并提示用户）。