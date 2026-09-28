# DeepSeek 网页版免费接入设计

日期：2026-08-30

## 1. 背景与目标

用户希望在 zhuzhu Copilot 中免费使用 DeepSeek 模型，**以模拟网页版对话请求的方式**接入现有 agent 引擎，使其工具 / skill / 插件 / MCP 链路对网页版模型可用，绕开 OpenAI 兼容付费 API。

已确认的约束（用户确认）：
- 登录态：**程序内引导登录一次**（复用 CDP 浏览器能力），Cookie 持久化复用
- 风险认知：用户已知晓模拟网页版违反 DeepSeek 服务条款、有封号风险、接口易变，仍要求继续
- 架构：**本地中转代理**模式接入，agent 引擎零改动

## 2. 现状

- `agent_llm.py`：OpenAI 兼容 `/v1/chat/completions` + SSE 流式；多服务商配置存于 `settings["model"]["providers"]`（`name/base_url/api_key/models/protocol`），`active` 指定当前服务商；已有预设服务商表与 `/models` 拉取
- `agent_browser.py`：CDP 浏览器自动化（独立 Edge 实例 + DevTools 协议，标准库实现 WebSocket，无第三方依赖），可用于引导登录与取 Cookie
- 引擎所有工具调用依赖 OpenAI 的 `tools` / `tool_calls` 协议

## 3. 架构总览

新增模块 `src/zhuzhu_Copilot/core/agent_web_llm.py`，三块职责：

```
用户消息 → agent_llm.py (OpenAI 兼容 SSE, 零改动)
        → 本地中转代理 (127.0.0.1:{port}/v1/chat/completions)
        → 转译层 (OpenAI 请求 ↔ 网页版 /api/v0/chat/completions)
        → chat.deepseek.com
        ←────────────────────────────────────────── 流式透传 → agent 引擎
```

- 中转代理：标准库 `http.server` 后台线程起本地服务，随机端口（避免端口冲突硬编码）
- 引擎视角：它只是一个普通服务商（OpenAI 兼容 base_url），引擎不需要感知网页版的存在

## 4. 模块设计：agent_web_llm.py

### 4.1 凭证管理（引导登录 + Cookie 持久化）

- 复用 `agent_browser` 启动独立 Edge 实例 → 导航 `https://chat.deepseek.com` → 用户在程序内手动扫码/登录 → 登录完成后从 DevTools 读登录态 Cookie
- Cookie 保存：`~/.zhuzhu_Copilot/web_credentials/deepseek_web_cookies.json`（含过期时间去重）
- 过期检测：透传响应 401/403，或/和请求前 timestamp 校验超过保鲜期 → 抛「凭证过期」错误，UI 提示重新登录
- 提供 `login_now()`（引导重新登录）与 `has_valid_credentials()` 供 UI 显示状态

### 4.2 本地中转代理

- 进程内后台线程，`http.server.ThreadingHTTPServer`，监听 `127.0.0.1:0`（随机端口）
- 路径：`GET /v1/models`（返回网页版可用模型列表；候选名 `deepseek-chat` / `deepseek-reasoner`，**以 PoC 实测返回的模型名为准**，配置于 `_WEB_ENDPOINT_CFG`）、`POST /v1/chat/completions`
- SSE 流式透传：逐块转写（`stream:true` 时），`data: [DONE]` 结尾；非流式亦支持（聚合返回）
- 端口选择后写入该服务商 `base_url` 配置；代理停止（应用退出）后配置指向过期地址，下次启动需重取 —— 由 `ensure_web_proxy()` 在每次引擎构建时幂等启动

### 4.3 转译层（唯一接触网页版的代码）

- 请求映射：OpenAI `messages` → 网页版消息结构；`tools/tool_choice` → 原样透传（先按支持处理，见测试计划）；`model` 透传
- `stream`/`temperature`/`max_tokens` 等参数映射为可配置字段名
- 请求头注入：登录 Cookie + `content-type`，其余网页版要求头放入**可配置常量表**
- **接口映射表不可硬编码**：路径、必需 header 名、请求体字段映射（消息/工具/流式开关）、Cookie 注入位整理为一个模块级可配置字典（`_WEB_ENDPOINT_CFG`），网页版接口变动时只改配置不碰代码

## 5. 服务商注册与 UI（最小改动）

- 预设服务商表新增：name=`DeepSeek 网页版`，`base_url` 运行时由代理端口填充，models=`deepseek-chat` / `deepseek-reasoner`，protocol=`openai`，api_key 留空
- 设置页服务商区新增登录状态指示（未登录 / 已登录 / 已过期）+「网页版登录」按钮；未登录时选择该服务商发起对话 → 提示先登录
- 复用现有模型下拉、流式输出全部机制，不新增设置项

## 6. 数据流示例

1. 用户发送消息 → agent 引擎构建 OpenAI 请求 → 发往本地代理 `http://127.0.0.1:{port}/v1/chat/completions`
2. 代理通过转译层注入 Cookie 并映射为网页版请求 → `POST https://chat.deepseek.com/api/v0/chat/completions`
3. 响应 SSE 流式回传 → 引擎按 OpenAI 协议解析逐 token 增量 → 若含 `tool_calls` 则工具执行链路照常运转（打开浏览器 / 运行命令 / 调技能 / 调 MCP 均可用）
4. 全程对引擎无感

## 7. 错误处理与降级

- 网络抖动 / 5xx：沿用 `agent_llm` 指数退避重试（2 次）
- 4xx：不重试；区分「凭证过期」（UI 提示重登）与「请求错误」（透传错误信息）
- Cookie 过期：抛专用异常 → UI 状态切为「已过期」→ 一键重登
- **工具调用降级**：若 PoC 确认网页版不支持 `tools` → 转译层在收到 `tools` 时先关闭 tools 以纯文本发送并提示「网页版不支持工具调用，agent 降级为对话模式」；反之若支持则全链路可用（目标态）

## 8. 测试计划（先验证再实现）

1. **PoC 真实连通性验证（严禁 mock）**：脚本完成一次真实登录 + 实测网页版接口：
   - 非流式 / 流式对话是否可用
   - `tools` 参数是否被支持并返回 `tool_calls`
   结果决定转译层是否保留 tools 透传，产出验证报告
2. 单元测试：转译层请求/响应映射（本地假上游验证报文结构）；Cookie 存取与过期逻辑
3. 端到端：真实登录后在 agent 引擎发起真实对话，验证流式输出；按 PoC 结果验证工具链路
4. Debug：对「凭证过期」「接口变更导致 4xx」两条路径人工演练

## 9. 风险与对策

- 网页版接口变动 → 接口映射集中配置化 + 4xx 错误可读化提示
- Cookie 失效频繁 → 保鲜期校验 + 一键重登
- 工具调用不支持 → 自动降级纯文本并提示
- 封号风险 → 已告知用户接受；默认低频使用建议写入 UI 提示
- 依赖新增：**零新增第三方依赖**（标准库 HTTP/WebSocket 均已有工程先例，兼容 Cython 打包）

## 10. 验收标准

1. 设置页可选「DeepSeek 网页版」服务商，显示登录状态；一键引导登录后可正常发起对话
2. agent 引擎发消息经本地代理真实请求网页版，流式输出正常，工具 / skill / 插件 / MCP 按 PoC 结论可用或明确降级提示
3. 凭证过期自动检测，UI 提示重登；重登后无需重启即可继续
4. 重启应用后代理自动重建、端口自动更新，对话可继续
5. 无新增第三方依赖；转译层接口映射全部可配置