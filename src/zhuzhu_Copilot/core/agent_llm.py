"""zhuzhu Copilot 的 LLM 客户端：OpenAI 兼容 /v1/chat/completions，流式（SSE）

- stream: true，逐 token 回调（on_delta）实现主流 Agent 的流式输出
- 工具调用：tools / tool_choice（流式 tool_calls 增量按 index 聚合）
- 图像输入：messages[].content[].image_url（支持 data URL 或 http(s) URL）
- tokens 预计算：发送前启发式估算（中文按字、英文按 4 字符），响应后取实际 usage
"""

import json
import logging
import os
import random
import re
import time
import urllib.error
from functools import lru_cache as _lru
from typing import Callable, List, Optional

_log = logging.getLogger("zhuzhu_Copilot.llm")   # 复用根日志设施（文件在 %TEMP%\zhuzhu_Copilot\）

# urllib.request 惰性加载：其导入链（http.client/ssl/email 等）约 250ms，
# 只在真正发起 HTTP 请求时才加载，加快首屏启动与 AI 面板打开
def _request_module():
    import urllib.request
    return urllib.request


def _stream_open(req, timeout: float):
    """流式请求打开入口：优先走 agent_http 连接复用池（长任务每轮一次请求，省去重复
    TCP/TLS 握手；池内含开关/代理判定与 urllib 回退，返回对象与 urlopen 语义一致）。
    惰性导入：池会拉起 http.client/ssl，只在真正请求时加载，保持启动轻量。"""
    from zhuzhu_Copilot.core import agent_http
    return agent_http.open_stream(req, timeout)


DEFAULT_BASE_URL = "https://api.agnes-ai.cn/v1"
DEFAULT_MODEL = "agnes-2.5-flash"
DEFAULT_API_KEY = "sk-iydeFjzDmQr4Se3N6yxEjRHccWQbhSXLOSp27ZH5qhxathwR"
DEFAULT_PROVIDER_NAME = "默认服务商"   # 内置默认服务商（agnes），整行锁定不可删除/编辑
_UA = "zhuzhu_Copilot/1.0 AgentClient"
_MAX_RETRIES = 3    # 请求失败（429/5xx/网络）自动重试次数
_RETRY_DELAY = 2.0  # 重试基础延迟（秒），指数退避
# 请求体里的「思考相关」顶层键：400 降级时整体去掉（思考参数不被接受时的安全网）
_EFFORT_PAYLOAD_KEYS = frozenset({"thinking", "reasoning_effort", "reasoning",
                                 "enable_thinking", "thinking_budget"})
DEFAULT_TIMEOUT = 60.0  # 连接/首包超时（秒）；流式空闲看门狗默认取其 3 倍
# 400 调试日志：上游错误响应体的最大记录字符数（防止超长响应刷爆日志文件）
_LOG_REJECT_BODY = 2000

# CJK 字符（用于 estimate_tokens 的 C 层计数，避免逐字符 Python 循环）
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _ensure_web_proxy():
    try:
        from zhuzhu_Copilot.core import agent_web_llm
        return agent_web_llm.ensure_web_proxy()
    except Exception as e:
        return {"ok": False, "base_url": "", "msg": str(e)}


def _humanize_http_error(code: int, body: str) -> str:
    """把上游 HTTP 错误转成用户可读文案：余额不足（402 / Insufficient Balance）等
    给出明确中文提示与操作建议，而不是一串裸 JSON。"""
    msg = f"HTTP {code}: {str(body or '')[:300]}"
    if code == 402 or "insufficient balance" in (body or "").lower():
        msg += ("（当前服务商账户余额不足：请在对应平台充值后重试，"
                "或在模型下拉中切换到其他服务商的同名模型）")
    return msg

# 主流 coding/Agent 服务商预设（添加服务商时一键填入，仍需填写 Key 并通过连通性测试）。
# 模型名为 2026 主流可用名，仅作预填参考；若测试失败请按各平台控制台实际模型名修改。
PRESET_PROVIDERS = [
    # 火山方舟 Coding Plan：官方专属端点 /api/coding/v3（OpenAI 兼容），必须用 Coding
    # Plan 专属 API Key；切勿用 /api/v3（不消耗套餐额度、产生额外费用）。
    # 官方模型：ark-code-latest(控制台切换) / doubao-seed-2.1-turbo / doubao-seed-2.0-lite /
    # minimax-m3 / glm-5.2(glm-latest) / glm-5.3 / deepseek-v4-flash / deepseek-v4-pro / kimi-k2.7-code
    {"name": "火山方舟（Coding Plan）",
     "base_url": "https://ark.cn-beijing.volces.com/api/coding/v3",
     "models": ["ark-code-latest", "deepseek-v4-flash", "deepseek-v4-pro",
                "kimi-k2.7-code", "doubao-seed-2.1-turbo", "glm-5.3", "minimax-m3"],
     "multimodal_models": [],
     "protocol": "chat",
     "desc": "火山方舟 Coding Plan 专属 OpenAI 端点（/api/coding/v3）；须用 Coding Plan 专属 API Key，"
             "勿用 /api/v3（不消耗套餐额度）。模型可在控制台切换（ark-code-latest）或直接填模型名"},
    # 方舟 Agent Plan：与 Coding Plan 是两套套餐/端点，必须区分——专属端点
    # /api/plan/v3（OpenAI 兼容，支持 Chat 与 Responses API），须用 Agent Plan
    # 专属 API Key（与 Coding Plan Key、通用火山 Key 均不同，请勿混用）。
    {"name": "火山方舟（Agent Plan）",
     "base_url": "https://ark.cn-beijing.volces.com/api/plan/v3",
     "models": ["ark-code-latest", "deepseek-v4-pro", "deepseek-v4-flash",
                "glm-5.3", "glm-5.2", "kimi-k3", "doubao-seed-2.1-turbo"],
     "multimodal_models": ["doubao-seed-2.0-lite"],
     "protocol": "chat",
     "desc": "方舟 Agent Plan 专属端点（/api/plan/v3，OpenAI 兼容，支持 Chat/Responses API）；"
             "须用 Agent Plan 专属 API Key（与 Coding Plan Key、通用火山 Key 均不同），"
             "官方推荐 Responses API 推理效果更好"},
    {"name": "火山方舟（通用）",
     "base_url": "https://ark.cn-beijing.volces.com/api/v3",
     "models": ["doubao-seed-2.1-pro", "doubao-seed-2.1-turbo"],
     "multimodal_models": ["doubao-1-5-vision-pro"],
     "protocol": "chat",
     "desc": "火山方舟数据面 API，模型名可用推理接入点 ID 或基础模型名"},
    # 智谱 GLM Coding Plan：必须用专属端点 /api/coding/paas/v4（OpenAI 兼容），
    # 用普通 /api/paas/v4 无法享受套餐额度且可能报错。支持 glm-5.3 / glm-4.7 / glm-4.7-flash
    {"name": "智谱（GLM Coding Plan）",
     "base_url": "https://open.bigmodel.cn/api/coding/paas/v4",
     "models": ["glm-5.3", "glm-4.7", "glm-4.7-flash"],
     "multimodal_models": [],
     "protocol": "chat",
     "desc": "智谱 GLM Coding Plan 专属端点（/api/coding/paas/v4，OpenAI 兼容）；"
             "须订阅 GLM Coding Plan 并用其专属网关，否则无法享用套餐额度"},
    {"name": "智谱清言",
     "base_url": "https://open.bigmodel.cn/api/paas/v4",
     "models": ["glm-5.3", "glm-5.2", "glm-4.7-flash"],
     "multimodal_models": ["glm-4v-plus"],
     "protocol": "chat",
     "desc": "智谱开放平台（OpenAI 兼容），glm-4.7-flash 为免费模型"},
    {"name": "DeepSeek",
     "base_url": "https://api.deepseek.com/v1",
     "models": ["deepseek-v4-pro", "deepseek-v4-flash"],
     "multimodal_models": [],
     "protocol": "chat",
     "desc": "DeepSeek 官方 API（V4 系列；deepseek-chat / deepseek-reasoner 已停用）"},
    {"name": "OpenAI",
     "base_url": "https://api.openai.com/v1",
     "models": ["gpt-5.4", "gpt-5.4-mini", "gpt-5.3"],
     "multimodal_models": ["gpt-5.4", "gpt-5.4-mini"],
     "protocol": "chat",
     "desc": "OpenAI 官方 API（GPT-5 系列；gpt-4o / gpt-4.1 已退役）"},
    {"name": "Anthropic (Claude)",
     "base_url": "https://api.anthropic.com",
     "models": ["claude-sonnet-4-5", "claude-opus-4-5", "claude-haiku-4-5"],
     "multimodal_models": ["claude-sonnet-4-5", "claude-opus-4-5"],
     "protocol": "anthropic",
     "desc": "Anthropic 官方 API（Claude 系列；Messages API /v1/messages，x-api-key 认证；"
             "支持工具调用与视觉输入）"},
    {"name": "Kimi（月之暗面）",
     "base_url": "https://api.moonshot.cn/v1",
     "models": ["kimi-k3", "kimi-k2.6"],
     "multimodal_models": ["kimi-k2.6"],
     "protocol": "chat",
     "desc": "月之暗面 Kimi API（K3 / K2.6；moonshot-v1 已停用）"},
    {"name": "硅基流动",
     "base_url": "https://api.siliconflow.cn/v1",
     "models": ["deepseek-ai/DeepSeek-V4-Pro", "deepseek-ai/DeepSeek-V4-Flash",
                "zai-org/GLM-5.1"],
     "multimodal_models": ["Qwen/Qwen3-VL-32B-Instruct"],
     "protocol": "chat",
     "desc": "SiliconFlow 聚合平台（OpenAI 兼容）"},
    {"name": "DeepSeek 网页版（免费）",
     "base_url": "http://127.0.0.1:0/v1",     # 占位，运行期由本地代理替换
     "models": ["deepseek-chat-quick", "deepseek-chat-expert", "deepseek-chat-view"],
     "multimodal_models": [],
     "protocol": "chat",
     "kind": "deepseek_web",                  # 供 load_model_config 注入代理 base_url
     "desc": "免费使用：复用 chat.deepseek.com 网页版登录态，\n"
             "经 CDP 浏览器桥接入。须先在设置页完成一次网页版登录；\n"
             "不支持工具调用（agent 降级为对话模式）"},
]

# 纯文本模型关键字（子串匹配）：命中即视为不支持图像输入，禁用截图/视觉能力。
# 只收录"确定无视觉"的文本模型名/前缀，避免误伤 gpt-4o / qwen-vl / glm-4v / hunyuan-vision 等视觉模型
TEXT_ONLY_KEYS = (
    "deepseek",                # deepseek-chat / deepseek-reasoner / deepseek-v4-* 均无视觉
    "glm-4-flash", "glm-4-air", "glm-4-long",     # 智谱文本（glm-4v 有视觉，不匹配）
    "qwen-turbo", "qwen-plus", "qwen-max", "qwen-long", "qwen-lite",  # 通义文本系列
    "moonshot-v1",             # Kimi 旧版文本模型
    "gpt-3.5",                 # OpenAI 旧文本模型（gpt-4o 含视觉，不匹配）
    "text-davinci", "text-babbage", "text-curie", "text-ada",
    "llama-2", "llama3-8b", "llama3-70b", "llama-3-8b", "llama-3-70b",
    "mistral-7b", "mistral-8x", "mixtral",        # Mistral 文本系列
    "phi-3", "phi-4",                            # 微软 Phi 文本
    "gemma-2",                                   # Google 文本（gemma-3 起含视觉，不收录）
    "chatglm", "yi-34b", "yi-large", "baichuan",  # 开源文本
    "ernie-bot", "minimax", "abab",              # 百度文心 / MiniMax 文本
    "spark-lite", "spark-v3",                    # 讯飞星火文本
    "hunyuan-turbo", "hunyuan-lite",             # 腾讯混元文本（hunyuan-vision 不匹配）
)

# 工作力度档位（从关闭到最强，8 档）：决定"力度→模型"路由与是否加大推理。
# 关闭→低→中→高→超高→最高→极致→超级；auto 模式下低于「高」视为不思考。
EFFORTS = ("off", "low", "medium", "high", "very_high", "max", "ultra", "extreme")
# 中文显示名（设置页滑块/提示文案）
EFFORT_LABELS = {"off": "关闭", "low": "低", "medium": "中", "high": "高",
                 "very_high": "超高", "max": "最高", "ultra": "极致", "extreme": "超级"}
# auto 模式下视为「不思考」的档位（需模型支持关闭思考；始终思考模式不受此限）
_THINK_OFF_LEVELS = ("off", "low", "medium")


def effort_index(effort: str) -> int:
    """力度档位序号（0=关闭）；非法值回退 medium 位置，供滑块/临近映射使用"""
    return EFFORTS.index(effort if effort in EFFORTS else "medium")


def effort_by_index(idx: int) -> str:
    """档位序号 → 力度名（越界自动钳制）"""
    return EFFORTS[max(0, min(len(EFFORTS) - 1, int(idx or 0)))]


def effort_label(effort: str) -> str:
    """力度名 → 中文显示名（非法值显示 medium 的中文名）"""
    return EFFORT_LABELS.get(effort if effort in EFFORTS else "medium", str(effort))


# ---------- 通用思考参数兜底（未命中厂商内置表的模型） ----------
# 内置表（deepseek/glm/kimi/o 系列/doubao/minimax/agnes）是按厂商文档逐个核对过的精确
# 映射；但 OpenRouter/硅基流动等聚合平台上的新模型（如 stealth/space-bunny-alpha）
# 往往不在表内，旧实现直接 return {}，导致「思考模式」开关与「思考强度」滑块
# 对这些模型完全失效（请求体不带任何思考参数）。
# 下面给出 OpenAI 兼容层的事实标准兜底：顶层 reasoning_effort（low/medium/high），
# 关闭思考用 "none"（与已实测的 agnes 行为一致，可被主流推理模型识别为关闭）。
_GENERIC_EFFORT = {"off": "none", "low": "low", "medium": "medium", "high": "high",
                   "very_high": "high", "max": "high", "ultra": "high", "extreme": "high"}
# 兜底强度可调时，思考「开启」的最小档：none 不算思考，on 模式下最低给 low
_GENERIC_EFFORT_ON = {"off": "low", "low": "low", "medium": "low", "high": "medium",
                      "very_high": "medium", "max": "high", "ultra": "high", "extreme": "high"}

# 发送给 API 的 reasoning_effort 取值（OpenAI 兼容仅支持 low/medium/high，
# 更高档位折算为 high；off 折算为最低档 low）
_REASONING_EFFORT = {"off": "low", "low": "low", "medium": "medium", "high": "high",
                     "very_high": "high", "max": "high", "ultra": "high", "extreme": "high"}
# 各服务商按力度正确映射的 API 参数（依据各厂商官方文档 2026）
# DeepSeek V4 思考模式仅 high/max（low/粗档取 high 即最低思考强度）；关思考时不传
# reasoning_effort（否则 400）—— 由 build_effort_params 的分支处理，本表只用于思考开启时。
_DEEPSEEK_EFFORT = {"off": "high", "low": "high", "medium": "high", "high": "high",
                    "very_high": "high", "max": "max", "ultra": "max", "extreme": "max"}
_GLM52_EFFORT = {"off": "minimal", "low": "minimal", "medium": "medium", "high": "high",
                 "very_high": "high", "max": "xhigh", "ultra": "max", "extreme": "max"}
_GLM53_EFFORT = {"off": "low", "low": "low", "medium": "high", "high": "high",
                 "very_high": "high", "max": "max", "ultra": "max", "extreme": "max"}
_KIMI3_EFFORT = {"off": "low", "low": "low", "medium": "high", "high": "high",
                 "very_high": "high", "max": "max", "ultra": "max", "extreme": "max"}
_OPENAI_EFFORT = {"off": "low", "low": "low", "medium": "medium", "high": "high",
                  "very_high": "high", "max": "high", "ultra": "high", "extreme": "high"}
# Agnes 系列：思考模式由顶层 reasoning_effort 控制（none=关闭，low/medium/high/max=递进
# 思考强度）。实测 thinking.type 不是思考开关（不产出 reasoning_content）；顶层
# reasoning_effort 才触发思考并返回 reasoning_content。默认始终随力度发送。
_AGNES_EFFORT = {"off": "none", "low": "none", "medium": "low", "high": "medium",
                 "very_high": "medium", "max": "high", "ultra": "max", "extreme": "max"}
# agnes 强制思考（on）时的强度表：none 不算思考，最低给 low（none 仅用于关闭）
_AGNES_EFFORT_ON = {"off": "low", "low": "low", "medium": "medium", "high": "medium",
                    "very_high": "medium", "max": "high", "ultra": "max", "extreme": "max"}


def build_effort_params(model: str, effort: str = "medium",
                        think_mode: str = "auto",
                        declared_levels=None) -> dict:
    """把工作力度正确映射为当前模型的 API 参数（自动调节 + 手动思考模式覆盖）。

    依据各厂商官方文档（DeepSeek/智谱/Kimi/OpenAI/MiniMax/火山方舟，2026）：
    - 匹配采用「子串包含」而非 startswith，兼容厂商前缀（zai-org/glm-5.1、
      deepseek-ai/DeepSeek-V4-Pro 等）；判断顺序保持「更特→更普」。
    - DeepSeek V4：thinking.type 控制思考开关；reasoning_effort 仅 high/max，
      low/medium 映射为 high；关思考时不传 reasoning_effort（否则 400）
    - GLM-5.3：思考强制开启不可关，reasoning_effort 仅 max/high/low
    - GLM-5.2：thinking.type + reasoning_effort（max/xhigh/high/medium/low/minimal/none）
    - GLM-5/5.1/5v/5-turbo、GLM-4.5/4.6：仅 thinking.type（不支持 reasoning_effort，勿传）
    - GLM-4.7：强制思考，仅 thinking.type=enabled
    - Kimi K3：顶层 reasoning_effort（low/high/max，默认 max）
    - Kimi K2.7-code：思考强制开启不可关，仅接受 thinking={"type":"enabled","keep":"all"}
      （传 disabled 或其他值会 400），故忽略力度恒全量思考
    - Kimi K2.6/K2.5：thinking.type（默认 enabled）
    - OpenAI o 系列/gpt-5：顶层 reasoning_effort（low/medium/high）
    - 豆包 doubao-seed：thinking.type（enabled/disabled，Chat 与 Responses API 均支持，
      不支持 reasoning_effort）
    - MiniMax-M3：thinking.type（adaptive 显式开启思考 / disabled 关闭；
      各网关默认值不一，显式传参保证力度可靠生效）
    - Agnes：顶层 reasoning_effort（none=关闭思考，其余递进思考强度）
    - 其他未命中内置表的模型（OpenRouter/硅基流动等聚合平台新模型，如
      stealth/space-bunny-alpha）：旧实现直接返回空 dict，导致「思考模式」开关与
      「思考强度」滑块对这些模型完全失效。现改为通用兜底：
      ① declared_levels 给出上游声明的可调级别时，按声明取值就近折算（最贴合该模型）；
      ② 否则用 OpenAI 兼容层事实标准（顶层 reasoning_effort：none/low/medium/high）。
      兜底可能与个别模型的实际参数不符，故请求层另有「400 去掉思考参数重试」的
      安全降级兜底（见 LLMClient._chat_stream_once）。

    think_mode（模型接入页手动选择，默认 "auto" 维持原自动行为）：
    - "auto"：跟随工作力度自动开关思考（引擎默认，无需手动）；力度低于「高」
      （关闭/低/中）视为不思考，模型支持关闭时直接关闭
    - "on"：始终思考（含"每次发送强制思考"）—— 以当前滑动的力度档位经各模型
      的力度量表换算思考强度作为 API 参数（低档=该模型最轻思考，高档=最强思考）
    - "off"：强制关闭思考 —— 对支持关闭的模型全部关闭；强制思考不可关的模型
      （GLM-5.3/GLM-4.7/Kimi K2.7/o 系列）尽力维持最小思考（低/高弹性场景无完全关闭开关）
    """
    m = (model or "").lower()
    eff = effort if effort in EFFORTS else "medium"
    on = think_mode == "on"
    off = think_mode == "off"
    # auto 模式低于「高」档 = 不思考；始终思考（on）不受此限，按力度量表取最低思考档
    think_off = eff in _THINK_OFF_LEVELS
    if "deepseek" in m:
        # DeepSeek V4：思考模式与 reasoning_effort 强耦合（关了思考不能带 effort）
        if off or (think_off and not on):
            return {"thinking": {"type": "disabled"}}
        return {"thinking": {"type": "enabled"},
                "reasoning_effort": _DEEPSEEK_EFFORT.get(eff, "high")}
    if "glm-5.3" in m:
        # GLM-5.3 思考强制开启不可关：off/低档时取最低思考档维持思考
        return {"thinking": {"type": "enabled"},
                "reasoning_effort": _GLM53_EFFORT.get(eff, "high")}
    if "glm-5.2" in m:
        if off or (think_off and not on):
            return {"thinking": {"type": "disabled"}}
        return {"thinking": {"type": "enabled"},
                "reasoning_effort": _GLM52_EFFORT.get(eff, "medium")}
    if "glm-4.7" in m:
        # 强制思考，不可关闭；off 时忽略
        return {"thinking": {"type": "enabled"}}
    if "glm-4.6" in m or "glm-4.5" in m:
        if off or (think_off and not on):
            return {"thinking": {"type": "disabled"}}
        return {"thinking": {"type": "enabled"}}
    if "glm-5" in m:
        # GLM-5 / 5.1 / 5v / 5-turbo：仅 thinking.type，不带 reasoning_effort
        if off or (think_off and not on):
            return {"thinking": {"type": "disabled"}}
        return {"thinking": {"type": "enabled"}}
    if "kimi-k2.7" in m:
        # K2.7 思考强制开启：仅接受 {"type":"enabled","keep":"all"}，忽略力度与 off
        return {"thinking": {"type": "enabled", "keep": "all"}}
    if "kimi-k3" in m:
        if off:
            return {"reasoning_effort": "low"}   # K3 无完全关闭开关，尽力最小思考
        return {"reasoning_effort": _KIMI3_EFFORT.get(eff, "max")}
    if "kimi-k2.6" in m or "kimi-k2.5" in m:
        if off or (think_off and not on):
            return {"thinking": {"type": "disabled"}}
        return {"thinking": {"type": "enabled"}}
    if any(k in m for k in ("o1", "o3", "o4", "o5", "gpt-5")):
        # o 系列/gpt-5 无完全关闭开关：off 用最低档 reasoning_effort 尽力减思考
        if off:
            return {"reasoning_effort": "low"}
        return {"reasoning_effort": _OPENAI_EFFORT.get(eff, "medium")}
    if "doubao-seed" in m:
        # 豆包深度思考：thinking.type（enabled/disabled），不支持 reasoning_effort
        if off or (think_off and not on):
            return {"thinking": {"type": "disabled"}}
        return {"thinking": {"type": "enabled"}}
    if "minimax-m3" in m:
        # MiniMax-M3：adaptive=显式开启思考（模型自适应），disabled=关闭
        if off or (think_off and not on):
            return {"thinking": {"type": "disabled"}}
        return {"thinking": {"type": "adaptive"}}
    if "agnes" in m:
        # Agnes：顶层 reasoning_effort（none=关闭思考，其余递进思考强度）
        if off:
            return {"reasoning_effort": "none"}
        if on:
            return {"reasoning_effort": _AGNES_EFFORT_ON.get(eff, "low")}
        return {"reasoning_effort": _AGNES_EFFORT.get(eff, "low")}
    # ── 通用兜底：未命中内置表的模型（聚合平台新模型等）──────────────────
    # 旧实现此处 return {}，使思考模式/思考强度对这些模型完全失效。
    # 优先按上游声明的可调级别折算；无声明时用 OpenAI 兼容层事实标准。
    return _generic_effort_params(eff, think_mode, declared_levels)


# 多模态模型名关键词：既用于「从服务商上游拉取模型列表后自动检测并填写多模态列表」，
# 也作为纯文本判定的正向排除项——模型名自带视觉标识时一律不判为纯文本，
# 避免 TEXT_ONLY_KEYS 中的厂商前缀（如 deepseek）误伤其视觉版本（如 deepseek-v4-flash-vision-exp）。
VISION_NAME_KEYS = ("vision", "4o", "4v", "vl", "multimodal", "image", "omni")


def is_text_only_model(model: str) -> bool:
    """自动识别纯文本模型：先按 VISION_NAME_KEYS 正向排除视觉模型，
    再按 TEXT_ONLY_KEYS 黑名单判定（顺序不可颠倒，否则厂商前缀会覆盖模型的视觉标识）。

    同时用于设置页展示与运行时的图片上传/粘贴闸门（见 agent_panel._refresh_text_only）。
    """
    m = (model or "").lower()
    if not m:
        return False
    if any(k in m for k in VISION_NAME_KEYS):
        return False
    return any(k in m for k in TEXT_ONLY_KEYS)


def detect_vision_models(models: list) -> list:
    """从模型名自动检测疑似多模态（视觉）模型：仅用于拉取后自动填写多模态模型列表，
    供用户确认/编辑，不作为运行时视觉能力判断依据。"""
    out = []
    for m in models or []:
        mm = str(m or "").strip()
        if mm and any(k in mm.lower() for k in VISION_NAME_KEYS):
            out.append(mm)
    return out


def infer_context_window(model: str) -> int:
    """按模型名推断上下文窗口（token）：长上下文标记（1m/256k/long-context）放宽到
    256k，其余默认 128k。模型配置中服务商显式声明 context_window 时优先于本推断。
    这是最后一级兜底，优先级低于上游声明与内置已知表（见 resolve_context）。"""
    m = (model or "").lower()
    if any(k in m for k in ("1m", "256k", "long-context", "long_context", "longcontext")):
        return 262144
    return 131072


# ---------- 上下文能力声明（provider declaration） ----------
# 上游 /models 里各服务商对上下文长度 / 最大输出的字段命名并不统一，这里集中归一化，
# 新增服务商只需往下面的键名元组里补一项，不改解析逻辑。
_CTX_DECL_KEYS = ("context_length", "context_window", "max_context_length",
                  "max_context_tokens", "max_input_tokens", "max_model_len",
                  "input_token_limit", "max_seq_len", "context_size")
_OUT_DECL_KEYS = ("max_output_tokens", "max_completion_tokens", "max_tokens",
                  "output_token_limit", "max_output_length", "max_response_tokens")

# 「开启 1M 上下文」开关对应的窗口上限（1,048,576 token）
ONE_M_CONTEXT = 1048576

# 内置已知服务商声明表（离线兜底，仅用于上游不返回能力元数据时）：
# (接口地址关键词, 模型名关键词, 窗口 token, 最大输出 token)
# 值为公开文档的近似值，仅作兜底；上游在线声明与用户手填优先于本表，随时可更新。
_KNOWN_DECLARATIONS = (
    ("deepseek", "deepseek-v4", 131072, 65536),
    ("deepseek", "deepseek", 131072, 8192),
    ("anthropic", "claude", 200000, 64000),
    ("moonshot", "kimi", 131072, 16384),
    ("moonshot", "moonshot", 131072, 16384),
    ("dashscope", "qwen-long", 1048576, 8192),
    ("dashscope", "qwen", 131072, 8192),
    ("bigmodel", "glm-4", 131072, 4096),
    ("volces", "doubao", 131072, 4096),
    ("openai", "gpt-4o", 128000, 16384),
    ("openai", "o1", 200000, 100000),
    ("openai", "o3", 200000, 100000),
    ("openai", "gpt-4.1", 1047576, 32768),
    ("generativelanguage", "gemini", 1048576, 8192),
    ("openrouter", "", 131072, 8192),
)


def _pos_int(v) -> int:
    """把声明字段安全转成正整数（None/字符串/0/负数一律返回 0，表示"未声明"）"""
    try:
        n = int(float(str(v).strip()))
    except (TypeError, ValueError):
        return 0
    return n if n > 0 else 0


def declared_context_from_item(item: dict) -> dict:
    """从 /models 的单条模型记录里解析服务商声明的上下文能力。

    兼容主流服务商的多种声明方式：
    - OpenAI / OpenRouter：context_length、top_provider.context_length
    - vLLM / 本地推理：max_model_len
    - 部分平台：max_input_tokens / context_window / max_context_length / input_token_limit
    - 最大输出：max_output_tokens / max_completion_tokens / top_provider.max_completion_tokens
    返回 {"context_window": int, "max_output_tokens": int}（未声明为 0，绝不瞎猜）。"""
    out = {"context_window": 0, "max_output_tokens": 0}
    if not isinstance(item, dict):
        return out
    top = item.get("top_provider") if isinstance(item.get("top_provider"), dict) else {}
    for k in _CTX_DECL_KEYS:
        n = _pos_int(item.get(k))
        if n:
            out["context_window"] = n
            break
    if not out["context_window"]:
        for k in _CTX_DECL_KEYS:
            n = _pos_int(top.get(k))
            if n:
                out["context_window"] = n
                break
    for k in _OUT_DECL_KEYS:
        n = _pos_int(item.get(k))
        if n:
            out["max_output_tokens"] = n
            break
    if not out["max_output_tokens"]:
        for k in _OUT_DECL_KEYS:
            n = _pos_int(top.get(k))
            if n:
                out["max_output_tokens"] = n
                break
    # 合理性守卫：窗口声明过小（<4096）视为噪声；输出声明过小或 ≥ 窗口同样视为噪声
    if out["context_window"] and out["context_window"] < 4096:
        out["context_window"] = 0
    if out["max_output_tokens"]:
        if out["max_output_tokens"] < 256:
            out["max_output_tokens"] = 0
        elif out["context_window"] and out["max_output_tokens"] >= out["context_window"]:
            out["max_output_tokens"] = 0
    return out


# 上游「可调思考力度级别」声明的字段命名（各平台不统一，穷举常见写法）
_EFFORT_DECL_KEYS = ("reasoning_effort_levels", "reasoning_levels", "thinking_levels",
                     "effort_levels", "thinking_efforts", "reasoning_efforts",
                     "effort", "reasoning", "thinking")
# 上游力度取值 → 内部档位名（别名归一化，出现未知取值整体放弃，绝不瞎猜）
_UPSTREAM_EFFORT_ALIAS = {
    "none": "off", "off": "off", "disabled": "off", "关闭": "off",
    "low": "low", "minimal": "low", "低": "low",
    "medium": "medium", "中": "medium",
    "high": "high", "高": "high",
    "xhigh": "very_high", "very_high": "very_high", "very-high": "very_high",
    "highest": "very_high", "超高": "very_high",
    "max": "max", "最高": "max",
    "ultra": "ultra", "极致": "ultra",
    "extreme": "extreme", "extreme_plus": "extreme", "超级": "extreme",
}


def _effort_to_upstream_value(level: str, levels: list) -> str:
    """内部档位 → 上游声明里的原始力度取值（级别列表已归一化为内部档位，故按名反查）。

    声明级别可能来自任意别名写法（minimal/xhigh/extreme…），归一化后已丢失原始拼写；
    这里用常见拼写重建，命中不了时退回标准 low/medium/high，绝不臆造参数。
    """
    canonical = {"off": "none", "low": "low", "medium": "medium", "high": "high",
                 "very_high": "xhigh", "max": "max", "ultra": "ultra",
                 "extreme": "extreme"}
    return canonical.get(level, "medium")


def _declared_level_for(levels: list, eff: str) -> str:
    """把内部档位 eff 就近折算到上游声明的级别子集内（按档位序号取最近者）。"""
    target = effort_index(eff)
    return min(levels, key=lambda x: abs(effort_index(x) - target))


def _generic_effort_params(effort: str, think_mode: str, declared_levels=None) -> dict:
    """未命中厂商内置表的模型的通用思考参数（OpenAI 兼容层事实标准）。

    背景：OpenRouter/硅基流动等聚合平台上的新模型（如 stealth/space-bunny-alpha）
    不在任何厂商内置映射表内，旧实现对这类模型返回空 dict —— 「思考模式」开关与
    「思考强度」滑块因此完全失效（请求体不带任何思考参数）。

    两级策略：
    ① declared_levels 非空（上游 /models 声明了可调级别）→ 按声明取值就近折算，
       最贴合该模型真实支持的档位；
    ② 无声明 → 顶层 reasoning_effort：关闭用 "none"，其余按力度取
       low/medium/high（与已实测的 agnes 及 OpenAI 兼容推理模型一致）。

    think_mode 语义与内置表保持一致：auto 低于「高」不思考；on 始终思考（最低 low）；
    off 关闭（none）。兜底值可能被个别模型拒绝，请求层有 400 去参重试兜底。
    """
    eff = effort if effort in EFFORTS else "medium"
    on = think_mode == "on"
    off = think_mode == "off"
    think_off = eff in _THINK_OFF_LEVELS
    levels = normalize_declared_efforts(declared_levels)
    if levels:
        # 有上游声明：完全按声明取值折算，思考开关沿用「声明里有没有关闭档」的判断
        has_off = "off" in levels
        if off:
            # 显式强制关闭：声明含关闭档就用它，否则只能取声明内最低思考档
            return {"reasoning_effort": _effort_to_upstream_value("off", levels) if has_off
                    else _effort_to_upstream_value(levels[0], levels)}
        if think_off and not on:
            # auto 低档：声明含关闭档才关闭，否则取声明内最低思考档（无关闭能力）
            return {"reasoning_effort": _effort_to_upstream_value("off", levels) if has_off
                    else _effort_to_upstream_value(levels[0], levels)}
        # on 模式 / auto 高档：按强度折算到声明内最近档
        return {"reasoning_effort":
                _effort_to_upstream_value(_declared_level_for(levels, eff), levels)}
    # 无声明：OpenAI 兼容层事实标准
    if off or think_off and not on:
        return {"reasoning_effort": "none"}
    if on:
        return {"reasoning_effort": _GENERIC_EFFORT_ON.get(eff, "low")}
    return {"reasoning_effort": _GENERIC_EFFORT.get(eff, "medium")}


def normalize_declared_efforts(values) -> list:
    """把上游声明的「可调思考力度级别」映射为 EFFORTS 子集（按档位升序去重）。

    上游取值五花八门（none/off/minimal/xhigh/extreme…），统一走别名表；
    出现无法识别的取值时返回 None —— 宁可让 UI 回退模型族内置映射，也不乱猜档位。
    """
    if not values:
        return None
    if isinstance(values, dict):
        values = list(values.keys())
    elif isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple, set)):
        return None
    out = []
    for v in values:
        k = _UPSTREAM_EFFORT_ALIAS.get(str(v).strip().lower())
        if k is None:
            return None
        if k not in out:
            out.append(k)
    return sorted(out, key=lambda x: EFFORTS.index(x)) if out else None


def declared_efforts_from_item(item) -> list:
    """从 /models 单条模型记录提取「可调思考力度级别」声明（可选级别集合）。

    标量默认值（如 reasoning_effort: "high"）不是"可选级别集合"，忽略；仅当
    上游明确给出一组级别（列表/对象键合集）时才映射为档位子集，未声明返回 None。
    """
    if not isinstance(item, dict):
        return None
    caps = item.get("capabilities") if isinstance(item.get("capabilities"), dict) else {}
    for k in _EFFORT_DECL_KEYS:
        v = item.get(k) if k in item else (caps.get(k) if k in caps else None)
        if v is None or isinstance(v, str):
            continue
        norm = normalize_declared_efforts(v)
        if norm:
            return norm
    return None


def declared_effort_levels(declarations: dict, model: str) -> list:
    """取上游声明的某模型可调思考力度级别（归一化到 EFFORTS 子集）；未声明返回 None。"""
    d = (declarations or {}).get(model)
    if not isinstance(d, dict):
        return None
    return d.get("effort_levels")


def known_context_declaration(base_url: str, model: str) -> dict:
    """内置已知服务商声明表兜底：按接口地址与模型名匹配（两者都命中才采信地址条目，
    仅有模型名时用模型名匹配）。返回 {"context_window", "max_output_tokens", "source"}。"""
    base = (base_url or "").lower()
    mdl = (model or "").lower()
    for host_kw, model_kw, win, out in _KNOWN_DECLARATIONS:
        if host_kw and host_kw not in base:
            continue
        if model_kw and model_kw not in mdl:
            continue
        return {"context_window": int(win), "max_output_tokens": int(out),
                "source": "known"}
    return {}


def resolve_context(base_url: str, model: str, provider: dict = None,
                    long_1m: bool = False) -> dict:
    """统一解析"这次请求该按多大的上下文窗口算"（含来源标记）。

    优先级（高 → 低）：
    1. long_1m=True（设置页「开启 1M 上下文」开关）→ 一律 1,048,576，覆盖一切；
    2. 上游服务商在线声明：provider["model_limits"][model]（拉取 /models 时解析并持久化）；
    3. 服务商配置手填：provider["context_window"]；
    4. 内置已知服务商声明表（离线兜底）；
    5. 模型名推断 infer_context_window（最后兜底）。

    返回 {"window", "max_output", "source", "long_1m"}，source ∈
    {1m, upstream, configured, known, inferred}，供 UI 标注"上限从哪来"。"""
    provider = provider if isinstance(provider, dict) else {}
    if long_1m:
        return {"window": ONE_M_CONTEXT,
                "max_output": _pos_int(provider.get("max_output_tokens"))
                or 32768, "source": "1m", "long_1m": True}
    limits = provider.get("model_limits")
    declared = {}
    if isinstance(limits, dict):
        row = limits.get(model) or limits.get(str(model or "").strip())
        if isinstance(row, dict):
            declared = row
        elif row:
            declared = {"context_window": _pos_int(row)}
    win = _pos_int(declared.get("context_window"))
    if win:
        return {"window": win,
                "max_output": _pos_int(declared.get("max_output_tokens")),
                "source": "upstream", "long_1m": False}
    win = _pos_int(provider.get("context_window"))
    if win:
        return {"window": win,
                "max_output": _pos_int(provider.get("max_output_tokens")),
                "source": "configured", "long_1m": False}
    known = known_context_declaration(base_url, model)
    if known:
        return {"window": known["context_window"],
                "max_output": known["max_output_tokens"],
                "source": "known", "long_1m": False}
    return {"window": infer_context_window(model), "max_output": 0,
            "source": "inferred", "long_1m": False}


def load_model_config() -> dict:
    """从 settings.json 读取模型配置，未配置时返回默认。

    支持多服务商：settings["model"]["providers"] = [{name, base_url, api_key, models, protocol}]，
    "active" 指定当前使用的服务商名。返回当前服务商的 base_url/api_key/model/models/protocol，
    并附带完整 providers 列表供 UI 展示；兼容旧版单服务商字段（base_url/api_key/models）。
    另含 effort_models / effort / auto_effort / send_effort。
    """
    try:
        from zhuzhu_Copilot.core import agent_skills
        m = agent_skills.load_settings().get("model") or {}
        if not isinstance(m, dict):
            m = {}
        providers = [p for p in (m.get("providers") or [])
                     if isinstance(p, dict) and p.get("base_url")]
        # 兼容旧单服务商配置：无 providers 时从旧字段构建一个
        if not providers:
            single = {
                "name": str(m.get("provider_name") or "默认服务商"),
                "base_url": m.get("base_url") or DEFAULT_BASE_URL,
                "api_key": m.get("api_key") or DEFAULT_API_KEY,
                "models": [str(x).strip() for x in (m.get("models") or []) if str(x).strip()],
                "protocol": m.get("protocol") if m.get("protocol") in ("chat", "responses", "anthropic") else "chat",
            }
            single_model = str(m.get("model") or "").strip()
            if single_model and single_model not in single["models"]:
                single["models"].insert(0, single_model)
            # 仅内置默认场景（无任何模型配置）兜底 agnes 默认模型；用户服务商不加
            if not single["models"]:
                single["models"] = [DEFAULT_MODEL]
            providers = [single]
        # 规范化每个服务商（不向用户服务商默认注入 agnes 模型）
        for p in providers:
            p["name"] = str(p.get("name") or "服务商").strip() or "服务商"
            p["base_url"] = str(p.get("base_url") or DEFAULT_BASE_URL)
            p["api_key"] = str(p.get("api_key") or "")
            p["protocol"] = (p.get("protocol") if p.get("protocol") in ("chat", "responses", "anthropic")
                             else "chat")
            p["models"] = [str(x).strip() for x in (p.get("models") or []) if str(x).strip()]
            p["multimodal_models"] = [str(x).strip() for x in (p.get("multimodal_models") or [])
                                      if str(x).strip()]
            p["kind"] = str(p.get("kind") or "")   # 保留 kind 标记
        # 聚合所有服务商的模型作为统一路由池（不再区分「当前服务商」）
        all_models = []
        for p in providers:
            for mm in p["models"]:
                if mm not in all_models:
                    all_models.append(mm)
        # kind=deepseek_web：把 base_url 替换为本地中转代理（幂等启动，不依赖是否已登录）
        for p in providers:
            if p.get("kind") == "deepseek_web":
                r = _ensure_web_proxy()
                if r.get("ok"):
                    p["base_url"] = r["base_url"]
        first = providers[0]
        # 上下文窗口：「基于上游服务商的声明」+ 1M 开关的统一决策（见 resolve_context）
        _long_1m = bool(m.get("context_1m", False))
        _model0 = all_models[0] if all_models else DEFAULT_MODEL
        _ctx = resolve_context(first.get("base_url"), _model0,
                               provider=first, long_1m=_long_1m)
        # 请求超时配置（秒，可选）：timeout=连接/首包预算；idle_timeout=流式空闲看门狗。
        # 推理模型思考期间可能长时间无增量，未配置时按 timeout 的 3 倍默认，避免误杀。
        _timeout = m.get("timeout")
        _idle = m.get("idle_timeout")
        out = {
            "base_url": first["base_url"],
            "api_key": first["api_key"],
            "model": all_models[0] if all_models else DEFAULT_MODEL,
            "models": all_models,
            "protocol": first["protocol"],
            "providers": providers,
            "context_window": _ctx["window"],
            # 上下文决策细节（来源/预留输出/1M 模式）供引擎与 UI 使用：
            # window=窗口, max_output=预留输出, source=1m|upstream|configured|known|inferred
            "context": _ctx,
            "context_1m": _long_1m,
            "effort_models": dict(m.get("effort_models") or {}),
            "effort": m.get("effort") if m.get("effort") in EFFORTS else "medium",
            "auto_effort": bool(m.get("auto_effort", True)),
            "send_effort": bool(m.get("send_effort", False)),
            # 思考模式（模型接入页手动选择）：auto=跟随力度自动 / on=强制开启 / off=强制关闭
            "think_mode": (m.get("think_mode") if m.get("think_mode") in ("auto", "on", "off")
                           else "auto"),
            "force_think": bool(m.get("force_think", False)),
        }
        if isinstance(_timeout, (int, float)) and _timeout > 0:
            out["timeout"] = float(_timeout)
        if isinstance(_idle, (int, float)) and _idle > 0:
            out["idle_timeout"] = float(_idle)
        return out
    except Exception:
        default = {"name": "默认服务商", "base_url": DEFAULT_BASE_URL,
                   "api_key": DEFAULT_API_KEY, "models": [DEFAULT_MODEL],
                   "protocol": "chat"}
        return {"base_url": DEFAULT_BASE_URL, "api_key": DEFAULT_API_KEY,
                "model": DEFAULT_MODEL, "models": [DEFAULT_MODEL],
                "protocol": "chat", "providers": [default],
                "effort_models": {}, "effort": "medium",
                "auto_effort": True, "send_effort": False,
                "think_mode": "auto", "force_think": False}


def provider_for_model(cfg: dict, model: str, provider_name: str = "") -> dict:
    """返回包含指定模型的服务商 dict（取其 base_url/api_key/protocol）。

    model 同名可存在于多个服务商（如 DeepSeek 与火山都有 deepseek-v4-flash）：
    provider_name 指定时优先匹配该服务商（用户最近使用/手动指定的归属），
    否则回退返回 providers 顺序中第一个包含该模型的（旧行为）。找不到返回 {}。"""
    if model:
        if provider_name:
            for p in (cfg.get("providers") or []):
                if p.get("name") == provider_name and model in (p.get("models") or []):
                    return p
        for p in (cfg.get("providers") or []):
            if model in (p.get("models") or []):
                return p
    return {}


def is_default_provider(p: dict) -> bool:
    """是否内置默认服务商（agnes）：整行锁定，不可删除/编辑。
    按名称「默认服务商」或 agnes 默认地址识别（兼容旧配置与兜底生成）。"""
    if not isinstance(p, dict):
        return False
    return (str(p.get("name") or "") == DEFAULT_PROVIDER_NAME
            or str(p.get("base_url") or "").startswith(DEFAULT_BASE_URL))


def _known_provider_models(base_url: str) -> list:
    """按接口地址匹配已知服务商，返回其内置模型列表（无匹配返回空）。

    供上游 /models 拉取失败（需鉴权/不支持枚举）时兜底，让用户无需 Key 也能
    得到一份可用的模型名。匹配规则：base_url 与预设服务商/默认服务商完全相等，
    或宿主（域名）一致且第一段路径相同（容忍尾部 /v1、/chat/completions 差异）。
    """
    base_url = (base_url or "").strip().rstrip("/")
    if not base_url:
        return []
    for suffix in ("/chat/completions", "/responses"):
        if base_url.endswith(suffix):
            base_url = base_url[: -len(suffix)]
    try:
        from urllib.parse import urlparse as _u
        host = (_u(base_url).hostname or "").lower()
        first = [s for s in (_u(base_url).path or "").split("/") if s][:1] or [""]
        first = first[0]
    except Exception:
        host, first = "", ""
    candidates = list(PRESET_PROVIDERS)
    candidates.append({"name": DEFAULT_PROVIDER_NAME,
                       "base_url": DEFAULT_BASE_URL,
                       "models": [DEFAULT_MODEL]})
    for p in candidates:
        b = str(p.get("base_url") or "").rstrip("/")
        if not b:
            continue
        same = (b == base_url or b.rstrip("/") == base_url.rstrip("/"))
        if not same and host:
            try:
                b_host = (_u(b).hostname or "").lower()
                b_first = [s for s in (_u(b).path or "").split("/") if s][:1] or [""]
                b_first = b_first[0]
                # 宿主一致 + 首段路径相同，或任一方为根路径（容忍 /v1 等版本段差异）
                same = (b_host == host and (b_first == first or not first or not b_first))
            except Exception:
                same = False
        if same:
            ms = [str(x).strip() for x in (p.get("models") or []) if str(x).strip()]
            if ms:
                return ms
    return []


def fetch_provider_models(base_url: str, api_key: str = "", timeout: float = 15.0) -> tuple:
    """从服务商上游拉取可用模型列表（OpenAI 兼容 GET {base_url}/models）。

    主流服务商均兼容该端点（OpenAI/DeepSeek/智谱/月之暗面/豆包方舟/模型市场）。
    增强：
    - 免 API Key：很多服务商的 /models 允许匿名枚举；优先带 Key 请求，
      401/403 时降级为不带认证重试一次，Key 为空则直接匿名请求。
    - 多候选端点：根地址自动补全 /v1/models、剥离 /chat/completions 尾缀。
    - 响应识别：兼容 OpenAI data[].id、models[]/list 等数组字段、vLLM 纯对象
      哈希表；解析失败返回响应摘要便于排障。
    - 已知服务商兜底：上游拉取全部失败时，若接口地址命中已知服务商，返回其
      内置模型列表（来源=known），实现无需 API Key 也能获取模型列表。
    返回 (True, [模型名], "upstream"|"known", [多模态模型名], {模型: 声明}) 或
    (False, 错误信息, "", [], {})。
    "声明"为服务商在 /models 里给出的上下文能力（窗口 / 最大输出，见
    declared_context_from_item）——即「基于上游服务商的声明」，供窗口决策与压缩阈值使用。"""
    base_url = (base_url or "").strip().rstrip("/")
    api_key = (api_key or "").strip()
    if not base_url:
        return False, "请先填写接口地址", "", [], {}
    # 请求头预检：Key/地址含中文时 urllib 只抛 latin-1 编码错误（用户无从下手）
    hint = validate_header_fields(base_url, api_key)
    if hint:
        return False, hint, "", [], {}
    # 兼容 base_url 已含 /chat/completions /responses 的写法（去除尾缀再拼 /models）
    for suffix in ("/chat/completions", "/responses"):
        if base_url.endswith(suffix):
            base_url = base_url[: -len(suffix)]
    # 候选端点：原路径 /models；根路径补 /v1/models
    models_urls = [f"{base_url}/models"]
    try:
        from urllib.parse import urlparse as _urlparse
        if _urlparse(base_url).path in ("", "/"):
            models_urls.append(f"{base_url}/v1/models")
    except Exception:
        pass

    urllib_request = _request_module()

    def _parse_models(raw: str) -> tuple:
        """解析 /models 响应 → (models, vision_models, declarations)。
        vision_models 优先取上游能力元数据（capabilities.vision / input_modalities /
        vision 字段），元数据缺失时用模型名关键词辅助检测（detect_vision_models），
        仅作自动填写多模态列表的参考。
        declarations = {模型名: {"context_window": int, "max_output_tokens": int}}，
        只收集真正声明了能力的模型（未声明的模型不入表，避免用 0 覆盖已有声明）。"""
        data = json.loads(raw)
        vision_flags = {}
        decls = {}

        def _note(items):
            for x in items:
                if not isinstance(x, dict):
                    continue
                mid = str(x.get("id") or x.get("name") or x.get("model") or "").strip()
                if not mid:
                    continue
                caps = x.get("capabilities") if isinstance(x, dict) else None
                vis = (x.get("vision") is True
                       or (isinstance(caps, dict)
                           and (caps.get("vision") or caps.get("multimodal")))
                       or (isinstance(x.get("input_modalities"), list)
                           and any(str(i).lower() in ("image", "vision")
                                   for i in x.get("input_modalities"))))
                if vis:
                    vision_flags[mid] = True
                d = declared_context_from_item(x)
                # 上游声明的「可调思考力度级别」：归一化到 EFFORTS 子集存入声明，
                # 设置页据此自动限制/提示可调挡位（见 declared_effort_levels）
                effs = declared_efforts_from_item(x)
                if effs:
                    d = dict(d)
                    d["effort_levels"] = effs
                if d["context_window"] or d["max_output_tokens"] or effs:
                    decls[mid] = d

        def _extract(items) -> list:
            out = []
            for x in items:
                if isinstance(x, dict):
                    m = str(x.get("id") or x.get("name") or x.get("model") or "").strip()
                    if m:
                        out.append(m)
                elif isinstance(x, str):
                    s = x.strip()
                    if s:
                        out.append(s)
            return out

        def _result(out: list) -> tuple:
            # 元数据检测 + 名字关键词检测 合并去重（全部自动识别出的多模态模型）
            merged = []
            for m in ([x for x in out if vision_flags.get(x)] + detect_vision_models(out)):
                if m not in merged:
                    merged.append(m)
            keep = {m: decls[m] for m in out if m in decls}
            return out, merged, keep

        # OpenAI 兼容：{"data":[{"id":...}]}
        items = data.get("data") if isinstance(data, dict) else None
        if isinstance(items, list) and items:
            _note(items)
            out = _extract(items)
            if out:
                return _result(out)
        # 部分平台：{"models":[...]} / {"list":[...]}
        for key in ("models", "list", "items", "model_list", "result"):
            v = data.get(key) if isinstance(data, dict) else None
            if isinstance(v, list) and v:
                _note(v)
                out = _extract(v)
                if out:
                    return _result(out)
        # vLLM 等：纯对象哈希表 {model_id: metadata}（直接取键）
        if isinstance(data, dict) and data and "data" not in data:
            keys = [str(k).strip() for k in data.keys()
                    if not str(k).startswith(("total", "object", "code", "msg", "message"))]
            keys = [m for m in keys if m and not m.isdigit()]
            if keys:
                # 哈希表形态的 value 也可能带能力声明（vLLM /v1/models 常见）
                for k in keys:
                    v_meta = data.get(k)
                    d = declared_context_from_item(v_meta)
                    effs = declared_efforts_from_item(v_meta)
                    if effs:
                        d = dict(d)
                        d["effort_levels"] = effs
                    if d["context_window"] or d["max_output_tokens"] or effs:
                        decls[k] = d
                return keys, detect_vision_models(keys), dict(decls)
        return [], [], {}

    def _do_request(url: str, with_key: bool):
        headers = {"User-Agent": _UA, "Accept": "application/json"}
        if with_key and api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        req = urllib_request.Request(url, headers=headers, method="GET")
        with urllib_request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", "replace")

    # 依次尝试候选端点：每个端点先带 Key（若有），401/403 且带 Key 时匿名重试
    attempts = [False] if not api_key else [True]
    last_err = ""
    for url in models_urls:
        for with_key in attempts:
            try:
                raw = _do_request(url, with_key)
                models, vision, decls = _parse_models(raw)
                if models:
                    return True, models, "upstream", vision, decls
                return False, ("上游返回空模型列表或格式无法识别"
                               f"（接口 {url}；响应前 120 字：{raw.strip()[:120]}"), "", [], {}
            except urllib.error.HTTPError as e:
                detail = ""
                try:
                    detail = (e.read().decode("utf-8", "replace") or "")[:200]
                except Exception:
                    pass
                # 鉴权失败（401/403）且有 Key → 换匿名重试；其余记录后试下一端点
                if e.code in (401, 403) and with_key and api_key:
                    last_err = f"带 Key 被拒（HTTP {e.code}），尝试匿名获取"
                    continue
                last_err = f"HTTP {e.code}：{detail or '上游拒绝（请检查地址/Key，或该服务商不支持枚举模型）'}"
                break
            except urllib.error.URLError as e:
                last_err = f"网络错误：{e.reason}"
                break
            except Exception as e:
                last_err = f"获取失败：{e}"
                break
    # 已知服务商兜底：无需 Key 也能得到可用模型列表；能力声明用内置已知表补齐
    known = _known_provider_models(base_url)
    if known:
        kdecl = {}
        for m in known:
            k = known_context_declaration(base_url, m)
            if k:
                kdecl[m] = {"context_window": k["context_window"],
                            "max_output_tokens": k["max_output_tokens"]}
        return True, known, "known", detect_vision_models(known), kdecl
    return False, (last_err or "获取失败：无法从上游取得模型列表"), "", [], {}


def _title_sys() -> str:
    """AI 起名的 system 提示词：按**提示词语言**取，并显式指定输出语言。

    为什么不只做「把中文原文换成英文译文」：模型默认会**跟随用户消息的语言**输出，
    用户用中文提问时即使拿到英文指令，仍大概率回中文标题。故英文语言下必须
    额外写明 "The title must be in English, even if the conversation is in Chinese."

    不做长度上限：好名字必须完整保留（用户明确要求停止自动截断），
    因此提示词也不再给 2-16 字之类的字数限制，只要求"简洁、写完整"。
    """
    try:
        from zhuzhu_Copilot.core import i18n
        en = i18n.current_prompt_lang() == i18n.EN_US
    except Exception:
        en = False
    if not en:
        return ("你是会话命名助手。用一句简洁的话概括用户意图，作为该对话的标题；"
                "长度不限但要写完整，不要中途截断成半句话。"
                "只输出标题本身：不要解释、不要标点、不要引号、不要 markdown 标记、不要代码块。")
    return ("You are a conversation titling assistant. Summarize the user's intent in one "
            "concise phrase to serve as the title of this conversation. No length limit, "
            "but write it out completely - never cut off mid-sentence.\n"
            "The title must be written in English, even if the conversation is in Chinese "
            "or any other language. Keep it short and specific (2-8 words).\n"
            "Output only the title itself: no explanation, no punctuation at the end, no "
            "quotes, no markdown, no code block.")


# 软截断可用的自然边界字符：句子级标点优先（。！？；），逗号/顿号/冒号与
# 空格次之（。，、：；等后紧跟其他边界时取靠后者，最终一并 rstrip 去尾标点）。
_SOFT_TRUNCATE_BOUNDS = "。！？；!?;，,、：: \u3000"


def _soft_truncate(s: str, limit: int) -> str:
    """软截断：超过 limit 时优先截到 limit 内最近的边界字符处（去掉尾标点），
    避免出现半截句/半截词；边界过靠前（< limit//2，会把名字裁得过短）或
    找不到边界时退化为按 limit 硬截。截断结果一律 ≤ limit。"""
    if limit <= 0 or len(s) <= limit:
        return s
    window = s[:limit + 1]              # 边界字符落在第 limit 位时也纳入窗口
    cut = max(window.rfind(ch) for ch in _SOFT_TRUNCATE_BOUNDS)
    if cut >= max(1, limit // 2):
        return s[:cut].rstrip(" \u3000，,、：:;；。！？!?")
    return s[:limit].rstrip()


def clean_title(raw: str, limit: int = 20) -> str:
    """把（模型输出或用户首条消息）清洗成可用作会话标题的短串。

    - 去代码块围栏、只取首行、去「标题：」类前缀、去首尾引号/书名号/标点
    - 合并空白；limit > 0 时限长（超长走 _soft_truncate 软截断，优先在自然边界
      收尾，避免半截 markdown / 半截句子）；limit <= 0 表示不做任何长度截断。
    - AI 起名与用户/Agent 改名均传 limit=0（完整保留）；仅本地兜底摘要用默认 20。
    返回空串表示无法得到有效标题（调用方应保持原标题）。"""
    s = (raw or "").strip()
    if not s:
        return ""
    s = re.sub(r"^```[A-Za-z0-9_+-]*\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    s = s.splitlines()[0].strip() if s.strip() else ""
    s = re.sub(r"^(标题|题目|会话名|对话名|title|name)\s*[:：]\s*", "", s, flags=re.IGNORECASE)
    s = s.strip(" \t\"'“”‘’「」『』《》〈〉〔〕[]【】()（）{}<>"
                ".。!！?？,，、:：;；-—_~*#`=+")
    s = re.sub(r"\s+", " ", s).strip()
    return _soft_truncate(s, limit)


def generate_session_title(user_text: str, ai_reply: str = "",
                           timeout: float = 12.0) -> str:
    """用当前配置的模型为对话生成简短标题（真实 API 调用，非本地截断）。

    user_text：用户首条消息；ai_reply：（可选）助手首轮回复摘要，帮助更准确概括。
    返回清洗后的标题（完整保留，不做长度截断，仅去掉引号/标点/markdown 等噪声）；
    调用失败/超时/输出不可用 → 返回空串，由调用方回退本地命名（clean_title），
    保证兜底永远可用。"""
    text = (user_text or "").strip()
    if not text:
        return ""
    try:
        cfg = load_model_config()
        client = LLMClient(cfg.get("base_url"), cfg.get("api_key"),
                           cfg.get("model") or DEFAULT_MODEL)
        ctx = text[:500]
        reply = (ai_reply or "").strip()
        if reply:
            try:
                from zhuzhu_Copilot.core import i18n
                label = i18n.tp("prompt.title.reply_hint", "（助手首轮回复摘要）")
            except Exception:
                label = "（助手首轮回复摘要）"
            ctx += "\n\n" + label + reply[:300]
        resp = client.chat([{"role": "system", "content": _title_sys()},
                            {"role": "user", "content": ctx}],
                           max_tokens=128, timeout=timeout)
        # limit=0：AI 起的名字完整保留，不做任何长度截断（用户明确要求）
        return clean_title((resp or {}).get("text") or "", limit=0)
    except Exception:   # noqa: BLE001 - 起名是锦上添花：任何失败都静默回退本地命名
        return ""


def header_unsafe_hint(field: str, value: str) -> str:
    """检测将作为 HTTP 请求头发送的字段是否含非 ASCII 字符；返回提示（空串=通过）。

    urllib 按 latin-1 编码请求头：Key/接口地址里混入中文时会抛
    `UnicodeEncodeError: 'latin-1' codec can't encode characters in position 7-60`
    —— 用户只看到一句与"无法接入模型"毫不相干的报错，无从下手。
    这里在发请求前预检，直接指明是哪个字段、从第几个字符开始有问题。"""
    s = value or ""
    bad = [i for i, ch in enumerate(s) if ord(ch) > 255]
    if not bad:
        return ""
    at = bad[0] + 1
    sample = s[bad[0]:bad[0] + 12]
    return (f"{field} 含非 ASCII 字符（第 {at} 个字符起「{sample}」）。"
            f"该字段会作为 HTTP 请求头发送，只能含英文/数字/常见符号："
            f"请检查是否误粘贴了中文说明或全角字符；API Key 通常形如 sk-xxxx。")


def validate_header_fields(base_url: str, api_key: str) -> str:
    """校验请求头相关字段（接口地址 / API Key）；返回空串表示可用。"""
    return (header_unsafe_hint("接口地址", base_url)
            or header_unsafe_hint("API Key", api_key))


def test_provider_connection(base_url: str, api_key: str, model: str,
                             protocol: str = "chat", timeout: float = 15.0) -> tuple:
    """真实连通性测试：用最小请求验证 base_url + api_key + 模型可用。
    全程真实 API 调用，不 mock；返回 (ok, message)，失败附具体 HTTP/网络错误便于排障。
    支持 chat（/v1/chat/completions）、responses（/v1/responses）、anthropic（/v1/messages）三种协议。"""
    base_url = (base_url or "").strip().rstrip("/")
    api_key = (api_key or "").strip()
    model = (model or "").strip()
    if not base_url or not api_key or not model:
        return False, "请填写完整的接口地址、API Key 与模型名"
    # 请求头字段预检：含中文/全角字符时最早由 urllib 抛 latin-1 编码错误，
    # 报错文案与"接入失败"毫无关联，用户无法自查 → 此处直接给出可操作提示。
    hint = validate_header_fields(base_url, api_key)
    if hint:
        return False, f"测试失败：{hint}"
    protocol = (protocol or "chat").lower()
    urllib_request = _request_module()
    if protocol == "anthropic":
        # Anthropic Messages API：端点 /v1/messages，请求头 x-api-key + anthropic-version
        url = f"{base_url}/v1/messages"
        payload = {"model": model,
                   "max_tokens": 1,
                   "messages": [{"role": "user", "content": "ping"}]}
        headers = {"Content-Type": "application/json",
                   "User-Agent": _UA,
                   "x-api-key": api_key,
                   "anthropic-version": "2023-06-01"}
    else:
        url = f"{base_url}/chat/completions"
        payload = {"model": model,
                   "messages": [{"role": "user", "content": "ping"}],
                   "max_tokens": 1, "stream": False}
        headers = {"Content-Type": "application/json",
                   "User-Agent": _UA,
                   "Authorization": f"Bearer {api_key}"}
    req = urllib_request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers=headers, method="POST")
    try:
        with urllib_request.urlopen(req, timeout=timeout) as resp:
            resp.read()
            return True, f"连接成功（HTTP {resp.status}），Key 与模型「{model}」可用"
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = (e.read().decode("utf-8", "replace") or "")[:200]
        except Exception:
            pass
        return False, f"HTTP {e.code}：{detail or '请求被拒绝（请检查地址/Key/模型名）'}"
    except urllib.error.URLError as e:
        return False, f"网络错误：{e.reason}"
    except Exception as e:
        return False, f"测试失败：{e}"


def analyze_connection_error(context: str) -> str:
    """用默认 AI 分析服务商连通性失败原因并给出修复建议（排障助手）。

    始终走内置默认 API（与用户自定义配置无关），失败时返回空串由调用方兜底。
    返回简短中文建议（2-5 条），便于用户在连通性测试失败后快速定位（如
    Coding Plan 端点/专属 Key/模型名/协议等）。"""
    prompt = ("你是 API 接入排障助手。用户配置的 AI 服务商连通性测试失败，"
              "请分析失败信息，给出最可能的原因与可操作修复建议。\n"
              "用简洁中文输出，使用 markdown 格式（如 **加粗** 关键词、`代码` 标记参数名、"
              "- 列表条目），2-5 条，不要客套。\n"
              f"失败信息：{(context or '').strip()[:800]}")
    payload = {
        "model": DEFAULT_MODEL,
        "messages": [
            {"role": "system", "content": "你是 API 接入排障助手，只输出简洁的中文分析与建议。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 300,
    }
    try:
        urllib_request = _request_module()
        req = urllib_request.Request(
            f"{DEFAULT_BASE_URL}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "User-Agent": _UA,
                     "Authorization": f"Bearer {DEFAULT_API_KEY}"},
            method="POST")
        raw = urllib_request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
        resp = json.loads(raw)
        return ((resp.get("choices") or [{}])[0].get("message") or {}).get("content", "") or ""
    except Exception:
        return ""


def is_vision_model(cfg: dict, model: str) -> bool:
    """模型是否具备视觉能力：用户显式配置的多模态模型列表（multimodal_models）优先；
    其次按模型名识别——模型名自带视觉标识（vision/4o/4v/vl/omni 等，见 VISION_NAME_KEYS）
    且未被纯文本黑名单命中时视为视觉模型。与 is_text_only_model 的「先正向排除、后黑名单」
    判定保持一致，避免 deepseek-v4-flash-vision-exp 等视觉模型因厂商前缀（deepseek）被
    误判为纯文本、图片输入被静默剥离。"""
    m = (model or "").strip()
    if not m:
        return False
    for p in (cfg.get("providers") or []):
        if m in (p.get("multimodal_models") or []):
            return True
    return any(k in m.lower() for k in VISION_NAME_KEYS) and not is_text_only_model(m)


def _model_tier(model: str) -> str:
    """按模型名后缀判断能力档位：flash/lite/mini/small/turbo≈轻量，pro/max/plus/ultra≈重量，其余通用"""
    m = (model or "").lower()
    if any(k in m for k in ("flash", "lite", "mini", "small", "turbo")):
        return "light"
    if any(k in m for k in ("pro", "max", "plus", "ultra", "reasoner")):
        return "heavy"
    return "general"


def requires_vision(text: str, images: list) -> bool:
    """是否需要视觉模型：本次带图，或为需看页面截图/浏览器操作的任务"""
    if images:
        return True
    t = (text or "").strip()
    if not t:
        return False
    keys = ("打开", "点击", "点一下", "登录", "点赞", "打卡", "截屏", "截图",
            "屏幕", "浏览器", "看视频", "刷视频", "鼠标", "输入框", "按钮",
            "帮我开", "帮我点", "帮我登", "打开网页")
    return any(k in t for k in keys)


def resolve_model(cfg: dict, effort: str = "medium", vision_needed: bool = False) -> str:
    """按工作力度路由模型（自动选择模式）。

    - 优先用户配置的 effort_models 映射（确定性）。
    - 否则按模型档位 + 能力选：轻量任务(low/medium)优先 flash 类轻量模型，
      重量任务(high/max/ultra)优先 pro 类重量模型。
    - vision_needed(视觉/截图任务)时先在视觉模型集合内路由。
    - 同档位多模型时随机挑选（避免死磕同一模型）。
    - 保证与引擎单例共用上下文：仅切换模型连接，不重建对话。
    """
    m = cfg or {}
    effort = effort if effort in EFFORTS else "medium"
    em = m.get("effort_models") or {}
    name = str(em.get(effort) or "").strip() or str(em.get("medium") or "").strip()
    if name:
        return name
    all_models = m.get("models") or []
    # 视觉任务：只在视觉模型内路由；无视觉模型时回退内置 agnes（视为视觉模型），
    # 否则纯文本模型看不到浏览器页面截图，无法理解网页状态
    if vision_needed:
        pool = [x for x in all_models if is_vision_model(m, x)]
        if not pool:
            return DEFAULT_MODEL
    else:
        pool = list(all_models)
    if not pool:
        return m.get("model") or DEFAULT_MODEL
    want = "light" if effort in ("off", "low", "medium") else "heavy"
    tiers = [x for x in pool if _model_tier(x) == want]
    if not tiers:   # 无对应档位：用全部候选
        tiers = list(pool)
    return random.choice(tiers)


def estimate_effort(text: str) -> str:
    """按任务难度智能估算工作力度：文本越长、关键操作词越多 → 力度越重"""
    t = (text or "").strip()
    if not t:
        return "medium"
    n = len(t)
    hard = ("分析", "编写", "开发", "调试", "配置", "迁移", "优化", "卸载",
            "安装", "重构", "计划", "步骤", "然后", "并且", "同时", "多个",
            "项目", "代码", "构建", "测试", "部署")
    hits = sum(1 for k in hard if k in t)
    if n >= 300 or (n >= 100 and hits >= 2):
        return "ultra"
    if n >= 100 or (n >= 40 and hits >= 1) or hits >= 3:
        return "max"
    if n >= 60 or (n >= 25 and hits >= 1) or hits >= 2:
        return "high"
    if n >= 12:
        return "medium"
    return "low"


def reasoning_effort_for(effort: str) -> str:
    """工作力度 → API 的 reasoning_effort 参数值（max/ultra 折算为 high）"""
    return _REASONING_EFFORT.get(effort if effort in EFFORTS else "medium")


def assess_effort(text: str) -> str:
    """用默认轻量模型（agnes-2.5-flash）评估任务难度/工作量，返回 EFFORTS 之一。

    始终走内置默认 API（与用户自定义配置无关），保证评估模型不被隐藏；
    请求极小（max_tokens=8），评估失败时回退本地启发式估算 estimate_effort。
    """
    import re
    prompt = ("你是任务难度评估器。根据用户的任务描述评估工作量和复杂度，"
              "只输出一个等级词：low、medium、high、max 或 ultra，不要输出任何其他内容。\n"
              f"任务描述：{(text or '').strip()[:800]}")
    payload = {
        "model": DEFAULT_MODEL,
        "messages": [
            {"role": "system", "content": "你是任务难度评估器，只输出 low/medium/high/max/ultra 之一。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
        "max_tokens": 8,
    }
    try:
        req = urllib.request.Request(
            f"{DEFAULT_BASE_URL}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "User-Agent": _UA,
                     "Authorization": f"Bearer {DEFAULT_API_KEY}"},
            method="POST")
        # 网络/5xx 抖动自动重试，保证「内置 agnes 评估」更可靠地执行
        last = None
        for attempt in range(_MAX_RETRIES):
            try:
                raw = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
                resp = json.loads(raw)
                txt = ((resp.get("choices") or [{}])[0].get("message") or {}).get("content", "") or ""
                m = re.search(r"\b(low|medium|high|max|ultra)\b", txt.lower())
                if m:
                    return m.group(1)
                break   # 有响应但格式不符：不再重试，走回退
            except Exception as e:   # noqa: BLE001
                last = e
                time.sleep(_RETRY_DELAY * (2 ** attempt))
        if last:
            raise last
    except Exception:
        pass
    return estimate_effort(text)   # 评估失败：回退本地估算，保证流程不中断


# ---- 任务复杂度分档：子 Agent / 跨工作流派发的准入依据 ----
# 口径（用户硬性要求）：简单与中等任务一律由主 Agent 自己动手，禁止拉起子 Agent
# 编队或调用其他工作流的主 Agent（为小事组队会显著拖慢、放大成本且难以收敛）；
# 只有「非常复杂」的任务才放行派发。所有准入判定统一走 subagent_allowed()，
# 禁止在引擎/面板/提示词里另写一套判断（本表即唯一事实来源，扩展力度档位只改这里）。
COMPLEXITY_TIERS = ("simple", "moderate", "complex")
# 力度档 → 复杂度档：off/low 简单；medium/high/very_high 中等；max/ultra/extreme 非常复杂
_EFFORT_COMPLEXITY = {
    "off": "simple", "low": "simple",
    "medium": "moderate", "high": "moderate", "very_high": "moderate",
    "max": "complex", "ultra": "complex", "extreme": "complex",
}
# 面板策略：auto=按任务复杂度自动分档（默认）；always=始终允许派发；never=始终禁止
SUBAGENT_POLICIES = ("auto", "always", "never")

# 用户显式要求多 Agent 协作的硬信号（命中即放行，不受任务力度分档限制）：
# 用户已经点名要编队/并行/派发，属于明确指令，不属"AI 自作主张组队"。
_SUBAGENT_INTENT_KEYS = ("并行", "并发", "多agent", "多智能体", "子agent", "子代理",
                         "工作团", "编队", "分布式", "同时处理", "多任务", "派发",
                         "调度", "下属", "协作完成")


def complexity_tier(effort: str) -> str:
    """工作力度 → 任务复杂度档：simple / moderate / complex"""
    return _EFFORT_COMPLEXITY.get(str(effort or "").strip().lower(), "moderate")


def wants_subagents(text: str) -> bool:
    """用户是否显式要求多 Agent 协作（自然语言硬信号 + @点名）。

    @点名（@子Agent / @工作流 / @成员）是用户主动指定执行者，同样视为显式要求。"""
    t = str(text or "").lower().replace(" ", "")
    if any(k.lower().replace(" ", "") in t for k in _SUBAGENT_INTENT_KEYS):
        return True
    return bool(re.search(r"@[\w\-]+", str(text or "")))


def subagent_allowed(effort: str, policy: str = "auto", explicit: bool = False) -> bool:
    """是否允许派发子 Agent / 其他工作流主 Agent。

    policy  : auto(按任务复杂度自动分档，默认) / always(强制放行) / never(强制禁止)
    explicit: 用户显式要求多 Agent 协作（wants_subagents 或工作团工作流等硬信号），
              优先级高于自动分档（用户点名的编队必须放行，否则指令无法执行）
    """
    p = str(policy or "auto").strip().lower()
    if p == "never":
        return False
    if p == "always" or explicit:
        return True
    return complexity_tier(effort) == "complex"


class AgentLLMError(Exception):
    pass


def estimate_tokens(text: str) -> int:
    """启发式估算文本 tokens（无 tiktoken 依赖）：中文约 1/字，英文约 1/4 字符。

    性能：本函数位于引擎每轮的 token 预算计算链路上（对全量历史逐条调用），
    原实现用 Python 生成器逐字符判断 CJK，长上下文下是热点；
    改用 C 层正则计数（结果完全一致，快一个数量级）。"""
    if not text:
        return 0
    cjk = _CJK_RE.subn("", text)[1]
    return int(cjk + (len(text) - cjk) / 4) + 4


def estimate_image_tokens() -> int:
    """单张截图约估 tokens（OpenAI 中尺寸近似）"""
    return 765


def _norm_usage(usage) -> Optional[dict]:
    """归一化两套 usage 字段命名（OpenAI: prompt_tokens/completion_tokens；
    DeepSeek/Responses: input_tokens/output_tokens）为统一结构：
    {"prompt_tokens","completion_tokens","cache_hit","cache_miss"}。
    缓存命中兼容 prompt_cache_hit/miss_tokens、prompt_tokens_details.cached_tokens、
    input_tokens_details.cached_tokens 三种来源。usage 缺失返回 None。"""
    if not usage:
        return None
    prompt = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    completion = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    hit = int(usage.get("prompt_cache_hit_tokens") or 0)
    miss = int(usage.get("prompt_cache_miss_tokens") or 0)
    if not hit:
        hit = int(((usage.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0)
    if not hit:
        hit = int(((usage.get("input_tokens_details") or {}).get("cached_tokens")) or 0)
    if not miss and prompt:
        miss = max(0, prompt - hit)
    return {"prompt_tokens": prompt, "completion_tokens": completion,
            "cache_hit": hit, "cache_miss": miss}


def build_content(text: str = "", images: Optional[List[str]] = None) -> list:
    """构造 OpenAI 兼容 content 列表：文本 + image_url（data URL 或 http(s) URL）。

    detail="high"：要求 API 以高细节处理截图，防止自动降采样把叠加的坐标
    网格刻度压成无法辨认的小字，是精确点击的基础保障。
    """
    parts = []
    if text:
        parts.append({"type": "text", "text": text})
    for img in images or []:
        parts.append({"type": "image_url",
                      "image_url": {"url": img, "detail": "high"}})
    return parts or [{"type": "text", "text": ""}]


def _repair_tool_pairs(messages: list) -> list:
    """修复工具调用配对：assistant(tool_calls) 与其 tool 回复必须一一对应，
    否则上游会报 "tool_calls must be followed by tool messages" 400。

    覆盖场景：上下文压缩/截断切断配对、任务中途停止留下半截 tool_calls、
    历史持久化恢复的残缺状态。规则：
    - 孤儿 tool 消息（前面没有 assistant 声明该 call_id）→ 丢弃
    - assistant 中未被任何 tool 回复的 tool_call → 从 tool_calls 中剔除（保留文本）
    """
    pending = {}          # call_id -> 是否已收到回复（False=已回复）
    first = []
    for m in messages or []:
        if not isinstance(m, dict):
            first.append(m)
            continue
        if m.get("role") == "assistant" and m.get("tool_calls"):
            kept = [tc for tc in m["tool_calls"]
                    if isinstance(tc, dict) and tc.get("id")]
            nm = dict(m)
            if kept:
                nm["tool_calls"] = kept
                for tc in kept:
                    pending.setdefault(tc["id"], True)
            else:
                nm.pop("tool_calls", None)
            first.append(nm)
        elif m.get("role") == "tool":
            cid = m.get("tool_call_id")
            if cid and cid in pending:
                pending[cid] = False
                first.append(m)
            # 无对应声明的孤儿 tool 消息：丢弃
        else:
            first.append(m)
    out = []
    for m in first:
        if (isinstance(m, dict) and m.get("role") == "assistant"
                and m.get("tool_calls")):
            kept = [tc for tc in m["tool_calls"]
                    if pending.get(tc.get("id")) is False]
            nm = dict(m)
            if kept:
                nm["tool_calls"] = kept
            else:
                nm.pop("tool_calls", None)
            out.append(nm)
        else:
            out.append(m)
    return out


def _sanitize_messages(messages: list) -> list:
    """发送前统一清洗消息：剔除内容数组里的空文本/空图部分、空数组补占位文本、
    空 content 补空串，并修复 tool_calls/tool 回复配对完整性（见 _repair_tool_pairs）"""
    out = []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        c = m.get("content")
        if isinstance(c, list):
            kept = []
            for x in c:
                if not isinstance(x, dict):
                    kept.append(x)
                    continue
                t = x.get("type")
                if t == "text":
                    if str(x.get("text") or "").strip():
                        kept.append(x)
                elif t == "image_url":
                    if (x.get("image_url") or {}).get("url"):
                        kept.append(x)
                else:
                    kept.append(x)
            if not kept:
                kept = [{"type": "text", "text": "（内容已忽略）"}]
            m = dict(m, content=kept)
        elif c is None:
            m = dict(m, content="")
        out.append(m)
    out = _repair_tool_pairs(out)
    # 兜底：部分上游（如 agnes）严格要求消息列表必须含至少一条 user 消息，
    # 仅 system 消息（工具循环极端压缩/清空后）会被 HTTP 400 "No user query found"
    # 拒绝。此处保证任何发送路径都带 user 消息：无 user 时以 system 内容补一条
    # 占位 user 消息，既不丢上下文也不破坏工具配对（占位追加在末尾）。
    if not any(isinstance(m, dict) and m.get("role") == "user" for m in out):
        ctx = ""
        for m in out:
            if not isinstance(m, dict):
                continue
            c = m.get("content")
            if isinstance(c, str) and c.strip():
                ctx += c + "\n"
            elif isinstance(c, list):
                for x in c:
                    if isinstance(x, dict) and x.get("type") == "text" and str(x.get("text") or "").strip():
                        ctx += str(x["text"]) + "\n"
        out.append({"role": "user",
                    "content": ctx.strip() or "请基于以上系统提示继续。"})
    return out


def _request_stats(payload: dict) -> dict:
    """统计请求体组成：消息条数/文本字符量/图片数/工具数/力度参数。chat 与 responses 两种协议通用。"""
    msgs = payload.get("messages") if isinstance(payload, dict) else None
    if not isinstance(msgs, list):   # responses 协议用 input 项数组
        msgs = payload.get("input") if isinstance(payload, dict) else None
    n_msg = chars = images = 0
    for m in msgs or []:
        if not isinstance(m, dict):
            continue
        n_msg += 1
        c = m.get("content")
        if isinstance(c, str):
            chars += len(c)
        elif isinstance(c, list):
            for x in c:
                if not isinstance(x, dict):
                    continue
                if x.get("type") == "text":
                    chars += len(str(x.get("text") or ""))
                elif x.get("type") == "image_url":
                    images += 1
    tools = payload.get("tools") if isinstance(payload, dict) else None
    effort = {k: payload.get(k) for k in ("thinking", "reasoning_effort", "reasoning")
              if isinstance(payload, dict) and payload.get(k) is not None}
    return {
        "model": payload.get("model") if isinstance(payload, dict) else "",
        "protocol": ("responses" if isinstance(payload, dict) and "input" in payload
                     else "chat"),
        "n_msg": n_msg, "chars": chars, "images": images,
        "tools": len(tools) if isinstance(tools, list) else 0,
        "effort": json.dumps(effort, ensure_ascii=False),
    }


def _log_rejected(phase: str, url: str, payload: dict, code: int, body: str) -> str:
    """请求被上游拒绝（HTTP 400 等不可重试错误）时：记录日志并返回完整诊断文本。

    诊断文本含请求摘要（模型/协议/消息规模/图片/工具数/力度参数）与上游完整错误响应，
    由调用方直接作为 AgentLLMError 消息抛给 UI，让用户无需翻日志即可在对话页看到全部报错。
    日志仅记录截断响应体（_LOG_REJECT_BODY），避免超长响应刷爆日志文件。
    """
    st = _request_stats(payload)
    try:
        _log.warning(
            "AI 请求被上游拒绝 HTTP %s | phase=%s url=%s model=%s protocol=%s "
            "messages=%d chars=%d images=%d tools=%d effort=%s | 上游响应: %s",
            code, phase, url, st["model"], st["protocol"], st["n_msg"],
            st["chars"], st["images"], st["tools"], st["effort"],
            str(body)[:_LOG_REJECT_BODY])
    except Exception:
        pass
    return (f"AI 请求被上游拒绝 HTTP {code}（{phase}）\n"
            f"接口: {url}\n模型: {st['model']}（协议 {st['protocol']}）\n"
            f"请求规模: {st['n_msg']} 条消息 / {st['chars']} 字符 / "
            f"{st['images']} 张图片 / {st['tools']} 个工具 / 力度 {st['effort']}\n"
            f"上游错误响应: {body}")


def _to_responses_input(messages: list) -> list:
    """把 Chat Completions 格式的 messages 转为 Responses API 的 input 项数组。

    - system → 单独走 instructions 参数，不放入 input
    - assistant：文本内容 → message 项；工具调用 → function_call 项（必须回放，
      否则后续 function_call_output 因找不到对应 call_id 报 400）
    - tool → {"type":"function_call_output","call_id","output"}
    - user/assistant 文本/图片 → {"type":"message","role","content":[...]}
    """
    items = []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role == "system":
            continue
        if role == "tool":
            items.append({"type": "function_call_output",
                          "call_id": str(m.get("tool_call_id") or ""),
                          "output": str(m.get("content") or "")})
            continue
        if role not in ("user", "assistant"):
            continue
        c = m.get("content")
        if isinstance(c, list):
            parts = []
            for x in c:
                if not isinstance(x, dict):
                    continue
                if x.get("type") == "text" and x.get("text"):
                    parts.append({"type": "input_text", "text": x["text"]})
                elif x.get("type") == "image_url":
                    url = (x.get("image_url") or {}).get("url")
                    if url:
                        parts.append({"type": "input_image", "image_url": url})
            if parts:
                items.append({"type": "message", "role": role, "content": parts})
        else:
            text = str(c or "").strip()
            if text:
                items.append({"type": "message", "role": role,
                              "content": [{"type": "input_text", "text": text}]})
        # assistant 的工具调用回放为 function_call 项（保留 call_id 供 function_call_output 匹配）
        if role == "assistant":
            for tc in m.get("tool_calls") or []:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") or {}
                items.append({"type": "function_call",
                              "call_id": str(tc.get("id") or ""),
                              "name": str(fn.get("name") or ""),
                              "arguments": str(fn.get("arguments") or "{}"),
                              "status": "completed"})
    return items


def _relax_socket_timeout(stream, secs: float) -> None:
    """流式响应已建立后，把底层 socket 读超时放宽到 secs：
    推理模型思考期间可能长时间无增量，若沿用连接期的短读超时会被误杀为
    "read operation timed out"。stream 为 urllib 的 HTTPResponse，
    fp.raw._sock 是 CPython 稳定内部结构；放宽失败时静默回退（维持原超时，不劣化）。"""
    try:
        fp = getattr(stream, "fp", None)
        raw = getattr(fp, "raw", None)      # BufferedReader.raw → SocketIO
        sock = getattr(raw, "_sock", None)  # SocketIO._sock → socket
        if sock is not None:
            sock.settimeout(secs)
    except Exception:
        pass


def _parse_responses_stream(stream, on_delta=None, on_reasoning=None, stop=None,
                            timeout: float = DEFAULT_TIMEOUT,
                            idle_timeout: float = None) -> dict:
    """解析 Responses API 的 SSE 流（event: / data: 行），返回 {text, tool_calls, usage, cache}。

    事件：response.output_text.delta（正文）、response.function_call_arguments.delta（工具参数，
    按 item_id 聚合）、response.output_item.done / response.completed（工具结果与 usage）、
    response.reasoning_text.delta（思考过程）、error（上游错误）。
    timeout：连接/首包超时；idle_timeout：流式无数据看门狗，超时判定假死连接并中断，
    避免无限等待（推理模型思考期应设得更宽松，默认取 timeout 的 3 倍）。"""
    if idle_timeout and idle_timeout > 0:
        idle_timeout = float(idle_timeout)
    else:
        idle_timeout = (timeout or DEFAULT_TIMEOUT) * 3
    _relax_socket_timeout(stream, idle_timeout)
    text_parts: List[str] = []
    calls: dict = {}        # item_id -> {"id","name","args"}
    usage = None
    last_data = time.time()
    try:
        while True:
            if stop and stop():
                raise AgentLLMError("已停止")
            raw = stream.readline()
            if not raw:
                break
            if time.time() - last_data > idle_timeout:
                raise AgentLLMError(f"流式响应超过 {idle_timeout:.0f}s 无数据，已中断")
            last_data = time.time()
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if not data:
                continue
            try:
                obj = json.loads(data)
            except json.JSONDecodeError:
                continue
            t = obj.get("type") or ""
            if t == "response.completed":
                resp = obj.get("response") or {}
                usage = resp.get("usage") or usage
                for it in resp.get("output") or []:
                    if isinstance(it, dict) and it.get("type") == "function_call":
                        cid = it.get("id") or ""
                        cur = calls.setdefault(cid, {"id": "", "name": "", "args": ""})
                        cur["id"] = it.get("call_id") or cur["id"]
                        if it.get("name"):
                            cur["name"] = it["name"]
                        if it.get("arguments"):
                            cur["args"] = it["arguments"]
            elif t == "response.output_item.added":
                it = obj.get("item") or {}
                if isinstance(it, dict) and it.get("type") == "function_call":
                    cid = it.get("id") or ""
                    if cid not in calls:
                        calls[cid] = {"id": "", "name": it.get("name", ""), "args": ""}
            elif t == "response.output_item.done":
                it = obj.get("item") or {}
                if isinstance(it, dict) and it.get("type") == "function_call":
                    cid = it.get("id") or ""
                    cur = calls.setdefault(cid, {"id": "", "name": "", "args": ""})
                    cur["id"] = it.get("call_id") or cur["id"]
                    if it.get("name"):
                        cur["name"] = it["name"]
                    if it.get("arguments"):
                        cur["args"] = it["arguments"]
            elif t == "response.function_call_arguments.delta":
                cur = calls.setdefault(str(obj.get("item_id") or ""),
                                       {"id": "", "name": "", "args": ""})
                cur["args"] += str(obj.get("delta") or "")
            elif t == "response.function_call_arguments.done":
                # 部分上游不逐段发 delta，仅在 done 事件一次性携带完整 arguments；
                # 不处理该事件会把参数漏成空串 → 下游误判「缺少必填参数」且模型无法自纠。
                # 已累积过 delta 时优先保留累积值（done 可能只给状态/摘要）
                cur = calls.setdefault(str(obj.get("item_id") or ""),
                                       {"id": "", "name": "", "args": ""})
                _full = str(obj.get("arguments") or "")
                if _full and (not cur["args"] or not cur["args"].strip().startswith("{")):
                    cur["args"] = _full
            elif t == "response.output_text.delta":
                d = obj.get("delta")
                if d:
                    text_parts.append(d)
                    if on_delta:
                        on_delta(d)
            elif t in ("response.reasoning_text.delta",
                       "response.reasoning_summary_text.delta",
                       "response.reasoning_effort.delta"):
                d = obj.get("delta")
                if d and on_reasoning:
                    on_reasoning(d)
            elif t == "error":
                msg = obj.get("message") or (obj.get("error") or {})
                if isinstance(msg, dict):
                    msg = msg.get("message", "")
                if msg:
                    raise AgentLLMError(str(msg))
    except AgentLLMError:
        raise
    except Exception as e:
        raise AgentLLMError(f"读取响应失败: {e}")

    calls_out = [{"id": calls[i]["id"] or i, "type": "function",
                  "function": {"name": calls[i]["name"],
                               "arguments": calls[i]["args"] or "{}"}}
                 for i in sorted(calls) if calls[i]["name"]]
    norm = _norm_usage(usage)
    if norm is not None:
        usage = norm   # 归一化为 OpenAI 标准字段，下游 _accum_usage 直接可读
        hit, miss = norm["cache_hit"], norm["cache_miss"]
    else:
        hit = miss = 0
    # 上游 200 但无正文且无工具调用（协议帧异常/静默空响应）：不在此抛错致命化，
    # 原样返回空结果交由引擎做「有限次纠正重试」（注入提示后继续）——长任务流中
    # 偶发空响应直接抛错会导致任务失败、用户手动重试重跑、ask_user 重复提问。
    # 引擎侧仍保证绝不把空响应当「完成任务」显示 Successfully。
    return {"text": "".join(text_parts), "tool_calls": calls_out, "usage": usage,
            "cache": {"hit": hit, "miss": miss}}


# 长文本生成类请求（插件整页 HTML + 多个端点实现 / 整套工作流核心文件）的超时。
# 单次就要产出数千 token，慢 Provider 上默认 60s 常常不够，会被误报成「生成失败」——
# 与引擎侧「生成类工具不设放弃等待超时」保持一致（见 agent_engine._NO_WAIT_TIMEOUT_TOOLS）。
GEN_TIMEOUT_S = 600.0


class LLMClient:
    def __init__(self, base_url: str = DEFAULT_BASE_URL,
                 api_key: str = DEFAULT_API_KEY, model: str = DEFAULT_MODEL,
                 timeout: float = DEFAULT_TIMEOUT, protocol: str = "chat",
                 idle_timeout: float = None):
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = api_key or DEFAULT_API_KEY
        self.model = model or DEFAULT_MODEL
        self.timeout = timeout
        # 流式空闲看门狗：推理模型思考期间可能长时间无增量，须比 socket 读超时更宽松
        # （默认按连接超时的 3 倍缩放，避免固定写死；可经模型配置 idle_timeout 覆盖）
        self.idle_timeout = (idle_timeout if idle_timeout and idle_timeout > 0
                             else (timeout or DEFAULT_TIMEOUT) * 3)
        # 接口协议：chat = /v1/chat/completions（默认）；responses = /v1/responses；
        # anthropic = Anthropic Messages API（/v1/messages，x-api-key 认证）
        self.protocol = (protocol or "chat").lower() or "chat"
        # 由上层按工作力度设置；None 表示不发送（兼容不支持该参数的 API）
        self.reasoning_effort = None
        # 按模型映射的力度参数（thinking/reasoning_effort 等），由上层赋值；无则不发
        self.effort_params = {}
        # 自动模型回退：选中模型/服务商请求失败（且未流出任何内容）时，用内置默认
        # 模型（agnes）重试一次；成功后置 fell_back=True 供上层提示。用户停止不回退。
        # 上层按任务模型来源控制：
        #   auto_fallback=False → 手动指定的模型失败不回落（直接报错给用户）；
        #   silent_fallback=True → 回退完全静默（不弹「已回退内置」提示，自动选择模式用）。
        # 请求头合法性预检：接口地址/API Key 混入中文等非 ASCII 字符时，urllib 会抛
        # latin-1 编码错误（文案与"接入失败"无关）。预检后统一给出明确中文提示，
        # 且此类"配置错误"不触发内置模型回退（回退会掩盖配置问题，让用户以为是自己没配错）。
        self._header_hint = validate_header_fields(self.base_url, self.api_key)
        self.auto_fallback = True
        self.fell_back = False       # 本次 chat_stream 调用是否发生过回退（每次调用前复位）
        self.silent_fallback = False # 回退是否静默（不向上层提示，供自动选择模式）
        self._streamed = False       # 本次调用是否已流出内容（回退决策依据：已流出不回退）

    def chat_stream(self, messages: list,
                    tools: Optional[list] = None,
                    tool_choice="auto",
                    on_delta: Optional[Callable[[str], None]] = None,
                    on_reasoning: Optional[Callable[[str], None]] = None,
                    stop: Optional[Callable[[], bool]] = None,
                    max_tokens: Optional[int] = None) -> dict:
        """流式对话。返回 {text, tool_calls, usage}。

        tool_calls: [{"id","type":"function","function":{"name","arguments"}}]
        usage: {"prompt_tokens","completion_tokens","total_tokens"} 或 None
        on_reasoning: 思考过程增量（delta.reasoning_content / thinking），不保证所有模型返回
        max_tokens: 请求输出预算（>0 时写入请求体）。不传则由上游按默认输出额度预留，
                其值可能远大于本地「窗口 − 预留输出」的预留值，导致上游实际允许的输入
                上限比本地预算更小——长对话末期输入挤掉输出空间直接 400。显式传入
                预留输出（chat: max_tokens / responses: max_output_tokens）可让上游
                校验与本地预算对齐。

        自动模型回退（auto_fallback=True）：当前选中的模型/服务商请求失败且尚未流出
        任何内容时，自动改用内置默认模型（agnes-2.5-flash）重试一次。回退成功后
        self.fell_back=True 供上层提示（连接参数随后恢复原值）；用户主动停止不回退。
        """
        self._streamed = False
        self.fell_back = False
        # 包装 delta 回调：无论 chat 还是 responses 路径，只要流出过内容就记录，
        # 用于回退决策（已流出内容再回退会造成 UI 重复渲染）
        _d, _r = on_delta, on_reasoning

        def _wrapped_delta(s):
            self._streamed = True
            if _d:
                _d(s)

        def _wrapped_reasoning(s):
            self._streamed = True
            if _r:
                _r(s)

        try:
            if self.protocol == "responses":
                return self._responses_stream(messages, tools, _wrapped_delta,
                                              _wrapped_reasoning, stop,
                                              max_tokens=max_tokens)
            if self.protocol == "anthropic":
                return self._anthropic_stream(messages, tools, _wrapped_delta,
                                              _wrapped_reasoning, stop,
                                              max_tokens=max_tokens)
            return self._chat_stream_once(messages, tools, tool_choice,
                                          _wrapped_delta, _wrapped_reasoning, stop,
                                          max_tokens=max_tokens)
        except AgentLLMError as e:
            if not self._fallback_eligible(e):
                raise
            # 回退内置默认模型：临时切换连接参数（协议统一 chat，力度参数清空——
            # agnes 不支持 thinking/reasoning_effort），失败时恢复原值再抛错
            self.fell_back = True
            saved = (self.base_url, self.api_key, self.model,
                     self.protocol, self.effort_params)
            self.base_url, self.api_key, self.model = (
                DEFAULT_BASE_URL, DEFAULT_API_KEY, DEFAULT_MODEL)
            self.protocol, self.effort_params = "chat", {}
            try:
                if saved[3] == "responses":
                    return self._responses_stream(messages, tools, _wrapped_delta,
                                                  _wrapped_reasoning, stop,
                                                  max_tokens=max_tokens)
                return self._chat_stream_once(messages, tools, tool_choice,
                                              _wrapped_delta, _wrapped_reasoning, stop,
                                              max_tokens=max_tokens)
            except AgentLLMError as e2:
                raise AgentLLMError(f"{e}（已自动回退内置默认模型，仍失败：{e2}）")
            finally:
                (self.base_url, self.api_key, self.model,
                 self.protocol, self.effort_params) = saved

    def _fallback_eligible(self, e: AgentLLMError) -> bool:
        """是否允许回退内置默认模型：开关开启、未流出内容、非用户停止、非配置错误、
        且当前不是默认内置模型（已是则无更可回退对象）。"""
        if not getattr(self, "auto_fallback", True):
            return False
        if self._streamed:
            return False
        if str(e).startswith("已停止"):
            return False
        if str(e).startswith("配置错误"):
            # Key/地址本身有问题：回退内置模型只会掩盖它，必须让用户看到并修正
            return False
        if (self.model == DEFAULT_MODEL
                and (self.base_url or "").rstrip("/") == DEFAULT_BASE_URL):
            return False
        return True

    def _raise_if_bad_headers(self) -> None:
        """请求头含非 ASCII（Key/地址里混入中文）时提前抛明确错误，替代 urllib 的
        `'latin-1' codec can't encode characters in position ...` 天书报错。"""
        hint = getattr(self, "_header_hint", "")
        if hint:
            raise AgentLLMError("配置错误：" + hint)

    def _chat_stream_once(self, messages: list,
                          tools: Optional[list] = None,
                          tool_choice="auto",
                          on_delta: Optional[Callable[[str], None]] = None,
                          on_reasoning: Optional[Callable[[str], None]] = None,
                          stop: Optional[Callable[[], bool]] = None,
                          max_tokens: Optional[int] = None) -> dict:
        """单次流式请求（含 3 次自动重试），连接参数读取 self.*（回退由外层切换）。"""
        self._raise_if_bad_headers()
        payload = {
            "model": self.model,
            "messages": _sanitize_messages(messages),
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        # 显式预留输出：让上游按「窗口 − max_tokens」校验输入上限，与本地预算口径
        # 一致（不传时上游按自身默认输出额度预留，输入上限可能比预算更小 → 400）
        if max_tokens and int(max_tokens) > 0:
            payload["max_tokens"] = int(max_tokens)
        # 工作力度/思考参数：DeepSeek/GLM/Kimi 等的 effort_params（thinking/reasoning_effort）
        # 必须真正合入 chat/completions 请求体，否则上游拿不到力度参数
        if self.effort_params:
            payload.update(self.effort_params)
        elif self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice
        urllib_request = _request_module()
        req = urllib_request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "User-Agent": _UA,
                     "Authorization": f"Bearer {self.api_key}"},
            method="POST")
        # 请求失败自动重试（429 限流 / 5xx / 网络抖动 / 读超时），指数退避；重试全程响应 stop。
        # 读超时只在「尚未流出任何内容」时重试，避免已显示到 UI 的流式内容被重复渲染。
        resp = None
        last_err = None
        text_parts: List[str] = []
        tool_calls: dict = {}   # index -> {id, name, args}
        usage = None
        # 400 去掉思考参数降级是否已用过（单次请求内只降级一次，避免无限重试）
        _strip_effort_retry_done = False
        for attempt in range(_MAX_RETRIES):
            if stop and stop():
                raise AgentLLMError("已停止")
            # 每次重试清空流缓冲，避免上一次不完整流污染本次结果
            text_parts = []
            tool_calls = {}
            usage = None
            try:
                resp = _stream_open(req, self.timeout)
                # 连接/首包已建立：放宽 socket 读超时到空闲看门狗阈值，
                # 避免推理模型长时间思考（无增量）被短读超时误杀
                _relax_socket_timeout(resp, self.idle_timeout)
                last_data = time.time()   # 流式无数据看门狗：假死连接不无限等待
                # 手动 readline 循环：每次迭代前检查 stop，命中立即断开连接，无需等下一行数据
                while True:
                    if stop and stop():
                        resp.close()
                        raise AgentLLMError("已停止")
                    raw = resp.readline()
                    if not raw:
                        break
                    # 超过空闲阈值无任何数据 → 判定假死（慢速/断连但未报错），
                    # 立即中断，避免发送按钮/任务无限转圈
                    if time.time() - last_data > self.idle_timeout:
                        resp.close()
                        raise AgentLLMError(
                            f"流式响应超过 {self.idle_timeout:.0f}s 无数据，已中断")
                    last_data = time.time()
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if obj.get("usage"):
                        usage = obj["usage"]
                    for ch in obj.get("choices") or []:
                        delta = ch.get("delta") or {}
                        # 思考过程（reasoning_content / thinking），逐段流式回调
                        rc = delta.get("reasoning_content") or delta.get("thinking")
                        if rc:
                            if on_reasoning:
                                on_reasoning(rc)
                        content = delta.get("content")
                        if content:
                            self._streamed = True
                            text_parts.append(content)
                            if on_delta:
                                on_delta(content)
                        # 部分网关不在 delta 里逐段下发工具参数，而是在结束 chunk
                        # （finish_reason=tool_calls）的 message 字段一次性回显完整
                        # tool_calls：只读 delta 会把参数漏成空 → 误判「缺少必填参数」。
                        # 完整 JSON 参数（以 { 开头）整体覆盖增量拼凑，避免双写。
                        msg_tc = (ch.get("message") or {}).get("tool_calls") or []
                        for tc in delta.get("tool_calls") or []:
                            idx = tc.get("index", 0)
                            cur = tool_calls.setdefault(idx, {"id": "", "name": "", "args": ""})
                            if tc.get("id"):
                                cur["id"] = tc["id"]
                            fn = tc.get("function") or {}
                            if fn.get("name"):
                                cur["name"] += fn["name"]
                            if fn.get("arguments"):
                                cur["args"] += fn["arguments"]
                        for tc in msg_tc:
                            idx = tc.get("index", 0)
                            fn = tc.get("function") or {}
                            full = str(fn.get("arguments") or "")
                            name = str(fn.get("name") or "")
                            tid = str(tc.get("id") or "")
                            # 无 name 且无参数/无 id 的空白项忽略；有 name 或 id 的调用
                            # 即使缺参数也要保留（交由下游缺参提示），不能静默丢弃——
                            # 否则模型以为已调用成功，实际无执行也无反馈
                            if not full and not name and not tid:
                                continue
                            self._streamed = True
                            cur = tool_calls.setdefault(idx, {"id": "", "name": "", "args": ""})
                            if tid:
                                cur["id"] = tid
                            if name:
                                cur["name"] = name
                            if full:
                                cur["args"] = (full if full.lstrip().startswith("{")
                                               else cur["args"] + full)
                break   # 完整读完，跳出重试
            except AgentLLMError:
                raise
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "replace")
                if e.code == 400:
                    # 400 安全降级：若本次带了思考参数且上游拒绝，去掉思考参数重试一次。
                    # 未命中厂商内置表的模型走通用兜底（见 build_effort_params），其参数
                    # 可能与该模型真实支持的字段不符（如只认 thinking.type 不认
                    # reasoning_effort）。降级保证「思考参数不被接受」最多损失思考能力，
                    # 不会让整个请求失败；仅降级一次，避免无限重试。
                    if _strip_effort_retry_done and _EFFORT_PAYLOAD_KEYS & set(payload):
                        for k in _EFFORT_PAYLOAD_KEYS:
                            payload.pop(k, None)
                        _strip_effort_retry_done = True
                        _log.warning(
                            "思考参数被上游拒绝(HTTP 400)，已去掉思考参数重试 | model=%s",
                            self.model)
                        # 必须重建请求对象：req 的 body 在构造时已固化，只改 payload
                        # 不会影响实际发出的内容（否则重发的仍是带思考参数的旧请求）
                        req = urllib_request.Request(
                            f"{self.base_url}/chat/completions",
                            data=json.dumps(payload).encode("utf-8"),
                            headers={"Content-Type": "application/json",
                                     "User-Agent": _UA,
                                     "Authorization": f"Bearer {self.api_key}"},
                            method="POST")
                        text_parts = []
                        tool_calls = {}
                        usage = None
                        continue
                    raise AgentLLMError(
                        _log_rejected("chat_stream", req.full_url, payload, e.code, body))
                last_err = _humanize_http_error(e.code, body)
                if e.code in (429, 500, 502, 503, 504) and attempt < _MAX_RETRIES - 1:
                    time.sleep(_RETRY_DELAY * (attempt + 1))
                    continue
                raise AgentLLMError(last_err)
            except urllib.error.URLError as e:
                last_err = f"网络错误: {e.reason}"
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_RETRY_DELAY * (attempt + 1))
                    continue
                raise AgentLLMError(last_err)
            except OSError as e:
                # socket.timeout / 连接被重置等读异常：仅未流出任何内容时重试，
                # 避免已渲染到 UI 的流式文本被重复
                last_err = f"读取响应失败: {e}"
                if not text_parts and not tool_calls and attempt < _MAX_RETRIES - 1:
                    time.sleep(_RETRY_DELAY * (attempt + 1))
                    continue
                raise AgentLLMError(last_err)
        else:
            raise AgentLLMError(last_err or "请求失败")

        calls = [{"id": tool_calls[i]["id"], "type": "function",
                  "function": {"name": tool_calls[i]["name"],
                               "arguments": tool_calls[i]["args"] or "{}"}}
                 for i in sorted(tool_calls)]
        # 上下文缓存统计（DeepSeek 返回 prompt_cache_hit/miss_tokens；
        # 部分服务商在 prompt_tokens_details.cached_tokens 提供命中数；
        # DeepSeek 新版 / Responses 用 input_tokens_details.cached_tokens）
        norm = _norm_usage(usage)
        if norm is not None:
            usage = norm   # 归一化为 OpenAI 标准字段，下游 _accum_usage 直接可读
            hit, miss = norm["cache_hit"], norm["cache_miss"]
        else:
            hit = miss = 0
        # 上游 200 但无正文且无工具调用（协议帧异常/静默空响应）：不在此抛错致命化，
        # 原样返回空结果交由引擎做「有限次纠正重试」（注入提示后继续）——长任务流中
        # 偶发空响应直接抛错会导致任务失败、用户手动重试重跑、ask_user 重复提问。
        # 引擎侧仍保证绝不把空响应当「完成任务」显示 Successfully。
        return {"text": "".join(text_parts), "tool_calls": calls, "usage": usage,
                "cache": {"hit": hit, "miss": miss}}

    def chat(self, messages: list, max_tokens: int = 1024,
             timeout: float = 60.0, stop: Optional[Callable[[], bool]] = None) -> dict:
        """非流式单次对话（内部小请求：上下文自主摘要等）。返回 {"text", "usage"}。

        自动模型回退：当前模型失败（非用户停止）时改用内置默认模型重试一次，
        fell_back 置 True 供上层提示（连接参数随后恢复）。"""
        try:
            return self._chat_once(messages, max_tokens, timeout, stop)
        except AgentLLMError as e:
            if not self._fallback_eligible(e):
                raise
            self.fell_back = True
            saved = (self.base_url, self.api_key, self.model,
                     self.protocol, self.effort_params)
            self.base_url, self.api_key, self.model = (
                DEFAULT_BASE_URL, DEFAULT_API_KEY, DEFAULT_MODEL)
            self.protocol, self.effort_params = "chat", {}
            try:
                return self._chat_once(messages, max_tokens, timeout, stop)
            except AgentLLMError as e2:
                raise AgentLLMError(f"{e}（已自动回退内置默认模型，仍失败：{e2}）")
            finally:
                (self.base_url, self.api_key, self.model,
                 self.protocol, self.effort_params) = saved

    def _chat_once(self, messages: list, max_tokens: int = 1024,
                   timeout: float = 60.0,
                   stop: Optional[Callable[[], bool]] = None) -> dict:
        """单次非流式请求（含重试），连接参数读取 self.*。
        支持 chat（/v1/chat/completions）与 anthropic（/v1/messages）协议；
        responses 协议不支持内部摘要请求。"""
        if self.protocol == "responses":
            raise AgentLLMError("responses 协议不支持内部摘要请求")
        self._raise_if_bad_headers()
        urllib_request = _request_module()
        if self.protocol == "anthropic":
            # Anthropic Messages API 非流式请求
            system_text = ""
            anthropic_msgs = []
            for m in _sanitize_messages(messages):
                role = m.get("role")
                content = m.get("content")
                if role == "system":
                    if isinstance(content, str):
                        system_text += (content + "\n") if content else ""
                    continue
                anthropic_msgs.append({"role": role,
                                       "content": str(content) if content is not None else ""})
            payload = {
                "model": self.model,
                "messages": anthropic_msgs,
                "max_tokens": max_tokens,
                "stream": False,
            }
            if system_text.strip():
                payload["system"] = system_text.strip()
            req = urllib_request.Request(
                f"{self.base_url}/v1/messages",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json",
                         "User-Agent": _UA,
                         "x-api-key": self.api_key,
                         "anthropic-version": "2023-06-01"},
                method="POST")
        else:
            payload = {
                "model": self.model,
                "messages": _sanitize_messages(messages),
                "stream": False,
                "max_tokens": max_tokens,
            }
            req = urllib_request.Request(
                f"{self.base_url}/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json",
                         "User-Agent": _UA,
                         "Authorization": f"Bearer {self.api_key}"},
                method="POST")
        resp = None
        last_err = None
        data = None
        for attempt in range(_MAX_RETRIES):
            if stop and stop():
                raise AgentLLMError("已停止")
            try:
                resp = urllib_request.urlopen(req, timeout=timeout)
                data = json.loads(resp.read().decode("utf-8", "replace"))
                break
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "replace")
                if e.code == 400:
                    raise AgentLLMError(
                        _log_rejected("chat", req.full_url, payload, e.code, body))
                last_err = _humanize_http_error(e.code, body)
                if e.code in (429, 500, 502, 503, 504) and attempt < _MAX_RETRIES - 1:
                    time.sleep(_RETRY_DELAY * (attempt + 1))
                    continue
                raise AgentLLMError(last_err)
            except urllib.error.URLError as e:
                last_err = f"网络错误: {e.reason}"
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_RETRY_DELAY * (attempt + 1))
                    continue
                raise AgentLLMError(last_err)
            except OSError as e:
                # socket.timeout / 连接被重置等读异常：也纳入重试（与 chat_stream 对齐）
                last_err = f"读取响应失败: {e}"
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_RETRY_DELAY * (attempt + 1))
                    continue
                raise AgentLLMError(last_err)
        else:
            raise AgentLLMError(last_err or "请求失败")
        if self.protocol == "anthropic":
            # Anthropic 响应：content 数组中的 text 块
            text = ""
            for blk in (data or {}).get("content") or []:
                if isinstance(blk, dict) and blk.get("type") == "text":
                    text += str(blk.get("text") or "")
            u = (data or {}).get("usage") or {}
            usage = {"prompt_tokens": u.get("input_tokens", 0),
                     "completion_tokens": u.get("output_tokens", 0),
                     "total_tokens": u.get("input_tokens", 0) + u.get("output_tokens", 0)}
            return {"text": text, "usage": usage}
        msg = ((data or {}).get("choices") or [{}])[0].get("message") or {}
        c = msg.get("content")
        if isinstance(c, list):   # 部分 API 返回分段数组
            text = "".join(str(x.get("text") or "") for x in c if isinstance(x, dict))
        else:
            text = str(c or "")
        return {"text": text, "usage": _norm_usage(data.get("usage"))}

    def _responses_stream(self, messages: list, tools=None,
                          on_delta=None, on_reasoning=None, stop=None,
                          max_tokens: Optional[int] = None) -> dict:
        """Responses API（/v1/responses）流式对话。返回结构与 chat_stream 一致。

        请求：instructions=system、input=消息项数组（工具结果用 function_call_output）、
        tools、stream、reasoning.effort（可选）。流解析见 _parse_responses_stream。
        """
        self._raise_if_bad_headers()
        payload = {
            "model": self.model,
            "input": _to_responses_input(_sanitize_messages(messages)),
            "stream": True,
            "store": False,
        }
        # 与 chat 协议同理：显式预留输出（Responses 用 max_output_tokens），
        # 使上游输入上限 = 窗口 − 预留输出，与本地预算口径一致
        if max_tokens and int(max_tokens) > 0:
            payload["max_output_tokens"] = int(max_tokens)
        if messages and isinstance(messages[0], dict) and messages[0].get("role") == "system":
            sys_txt = messages[0].get("content")
            if isinstance(sys_txt, str) and sys_txt.strip():
                payload["instructions"] = sys_txt
        if self.effort_params:
            # Responses 协议：reasoning_effort → reasoning.effort；thinking → 顶层 thinking
            # （DeepSeek/火山方舟等 Responses API 顶层支持 thinking.type，缺了力度不生效）
            if "reasoning_effort" in self.effort_params:
                payload["reasoning"] = {"effort": self.effort_params["reasoning_effort"]}
            if "thinking" in self.effort_params:
                payload["thinking"] = self.effort_params["thinking"]
        elif self.reasoning_effort:
            payload["reasoning"] = {"effort": self.reasoning_effort}
        if tools:
            payload["tools"] = [
                {"type": "function", "name": t["function"]["name"],
                 "parameters": t["function"].get("parameters", {})}
                for t in tools if isinstance(t, dict)]
        urllib_request = _request_module()
        req = urllib_request.Request(
            f"{self.base_url}/responses",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "User-Agent": _UA,
                     "Authorization": f"Bearer {self.api_key}"},
            method="POST")
        resp = None
        last_err = None
        # 400 去掉思考参数降级是否已用过（单次请求内只降级一次）
        _strip_effort_retry_done = False
        for attempt in range(_MAX_RETRIES):
            if stop and stop():
                raise AgentLLMError("已停止")
            try:
                resp = _stream_open(req, self.timeout)
                break
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "replace")
                if e.code == 400:
                    # 与 chat 协议一致：思考参数被拒时去掉重试一次（见 _chat_stream_once）
                    if _strip_effort_retry_done and _EFFORT_PAYLOAD_KEYS & set(payload):
                        for k in _EFFORT_PAYLOAD_KEYS:
                            payload.pop(k, None)
                        _strip_effort_retry_done = True
                        _log.warning(
                            "思考参数被上游拒绝(HTTP 400)，已去掉思考参数重试 | model=%s",
                            self.model)
                        req = urllib_request.Request(
                            f"{self.base_url}/responses",
                            data=json.dumps(payload).encode("utf-8"),
                            headers={"Content-Type": "application/json",
                                     "User-Agent": _UA,
                                     "Authorization": f"Bearer {self.api_key}"},
                            method="POST")
                        continue
                    raise AgentLLMError(
                        _log_rejected("responses", req.full_url, payload, e.code, body))
                last_err = _humanize_http_error(e.code, body)
                if e.code in (429, 500, 502, 503, 504) and attempt < _MAX_RETRIES - 1:
                    time.sleep(_RETRY_DELAY * (attempt + 1))
                    continue
                raise AgentLLMError(last_err)
            except urllib.error.URLError as e:
                last_err = f"网络错误: {e.reason}"
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_RETRY_DELAY * (attempt + 1))
                    continue
                raise AgentLLMError(last_err)
        if resp is None:
            raise AgentLLMError(last_err or "请求失败")
        return _parse_responses_stream(resp, on_delta, on_reasoning, stop,
                                       timeout=self.timeout,
                                       idle_timeout=self.idle_timeout)

    def _anthropic_stream(self, messages: list, tools=None,
                          on_delta=None, on_reasoning=None, stop=None,
                          max_tokens: Optional[int] = None) -> dict:
        """Anthropic Messages API（/v1/messages）流式对话。返回结构与 chat_stream 一致。

        与 OpenAI 协议的关键差异：
        - 认证头：x-api-key（非 Authorization: Bearer）+ anthropic-version
        - system 是独立顶层字段（非 messages 中的 role=system）
        - 工具调用用 content 块数组（tool_use / tool_result），非 tool_calls 字段
        - 工具定义用 input_schema（非 function.parameters）
        - max_tokens 必填
        - 流式 SSE 事件：message_start / content_block_start / content_block_delta /
          content_block_stop / message_delta / message_stop
        """
        self._raise_if_bad_headers()
        # ---- 消息格式转换：OpenAI → Anthropic ----
        system_text = ""
        anthropic_msgs = []
        for m in _sanitize_messages(messages):
            role = m.get("role")
            content = m.get("content")
            if role == "system":
                # system 消息提取文本拼入顶层 system 字段
                if isinstance(content, str):
                    system_text += (content + "\n") if content else ""
                elif isinstance(content, list):
                    for blk in content:
                        if isinstance(blk, dict) and blk.get("type") == "text":
                            system_text += str(blk.get("text") or "") + "\n"
                continue
            if role == "tool":
                # OpenAI tool 回复 → Anthropic user 消息中的 tool_result 块
                tool_call_id = m.get("tool_call_id", "")
                anthropic_msgs.append({
                    "role": "user",
                    "content": [{"type": "tool_result",
                                 "tool_use_id": tool_call_id,
                                 "content": str(content) if content is not None else ""}]
                })
                continue
            if role == "assistant" and m.get("tool_calls"):
                # OpenAI assistant tool_calls → Anthropic assistant content 块数组
                blocks = []
                if isinstance(content, str) and content.strip():
                    blocks.append({"type": "text", "text": content})
                for tc in m["tool_calls"]:
                    fn = tc.get("function") or {}
                    try:
                        inp = json.loads(fn.get("arguments") or "{}")
                    except (json.JSONDecodeError, TypeError):
                        inp = {}
                    blocks.append({"type": "tool_use",
                                   "id": tc.get("id", ""),
                                   "name": fn.get("name", ""),
                                   "input": inp})
                anthropic_msgs.append({"role": "assistant", "content": blocks})
                continue
            # 普通 user/assistant 消息：content 为字符串或 OpenAI 内容块数组
            if isinstance(content, list):
                # OpenAI 内容块（text / image_url）→ Anthropic 块
                blocks = []
                for blk in content:
                    if not isinstance(blk, dict):
                        continue
                    if blk.get("type") == "text":
                        blocks.append({"type": "text", "text": str(blk.get("text") or "")})
                    elif blk.get("type") == "image_url":
                        url = (blk.get("image_url") or {}).get("url", "")
                        if url.startswith("data:"):
                            # data URI → base64 source
                            try:
                                mime, b64 = url.split(",", 1)
                                media_type = mime.split(";")[0].replace("data:", "")
                                blocks.append({"type": "image",
                                               "source": {"type": "base64",
                                                          "media_type": media_type,
                                                          "data": b64}})
                            except ValueError:
                                pass
                        # 纯 URL 图片 Anthropic 不直接支持，跳过（降级为文本）
                anthropic_msgs.append({"role": role, "content": blocks if blocks else ""})
            else:
                anthropic_msgs.append({"role": role,
                                       "content": str(content) if content is not None else ""})
        # ---- 构建请求 ----
        payload = {
            "model": self.model,
            "messages": anthropic_msgs,
            "max_tokens": int(max_tokens) if max_tokens and int(max_tokens) > 0 else 4096,
            "stream": True,
        }
        if system_text.strip():
            payload["system"] = system_text.strip()
        # 工具格式转换：OpenAI function → Anthropic name/description/input_schema
        if tools:
            payload["tools"] = [
                {"name": t["function"]["name"],
                 "description": t["function"].get("description", ""),
                 "input_schema": t["function"].get("parameters", {"type": "object", "properties": {}})}
                for t in tools if isinstance(t, dict) and t.get("function")
            ]
        urllib_request = _request_module()
        req = urllib_request.Request(
            f"{self.base_url}/v1/messages",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "User-Agent": _UA,
                     "x-api-key": self.api_key,
                     "anthropic-version": "2023-06-01"},
            method="POST")
        # ---- 重试循环（与 chat/responses 一致：429/5xx/网络抖动指数退避）----
        resp = None
        last_err = None
        for attempt in range(_MAX_RETRIES):
            if stop and stop():
                raise AgentLLMError("已停止")
            try:
                resp = _stream_open(req, self.timeout)
                break
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "replace")
                last_err = _humanize_http_error(e.code, body)
                if e.code in (429, 500, 502, 503, 504) and attempt < _MAX_RETRIES - 1:
                    time.sleep(_RETRY_DELAY * (attempt + 1))
                    continue
                if e.code == 400:
                    raise AgentLLMError(
                        _log_rejected("anthropic", req.full_url, payload, e.code, body))
                raise AgentLLMError(last_err)
            except urllib.error.URLError as e:
                last_err = f"网络错误: {e.reason}"
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_RETRY_DELAY * (attempt + 1))
                    continue
                raise AgentLLMError(last_err)
        if resp is None:
            raise AgentLLMError(last_err or "请求失败")
        # ---- 流式解析 ----
        _relax_socket_timeout(resp, self.idle_timeout)
        last_data = time.time()
        text_parts: List[str] = []
        tool_calls: dict = {}   # index → {id, name, args}
        usage = None
        cur_block_idx = -1
        cur_block_type = None   # "text" | "tool_use"
        while True:
            if stop and stop():
                resp.close()
                raise AgentLLMError("已停止")
            raw = resp.readline()
            if not raw:
                break
            if time.time() - last_data > self.idle_timeout:
                resp.close()
                raise AgentLLMError(f"流式响应超过 {self.idle_timeout:.0f}s 无数据，已中断")
            last_data = time.time()
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if not data or data == "[DONE]":
                continue
            try:
                obj = json.loads(data)
            except json.JSONDecodeError:
                continue
            evt = obj.get("type", "")
            if evt == "message_start":
                msg = obj.get("message") or {}
                u = msg.get("usage") or {}
                if u:
                    usage = {"prompt_tokens": u.get("input_tokens", 0),
                             "completion_tokens": 0,
                             "total_tokens": u.get("input_tokens", 0)}
            elif evt == "content_block_start":
                cur_block_idx = obj.get("index", cur_block_idx + 1)
                blk = obj.get("content_block") or {}
                cur_block_type = blk.get("type")
                if cur_block_type == "tool_use":
                    tc = tool_calls.setdefault(cur_block_idx,
                                               {"id": blk.get("id", ""),
                                                "name": blk.get("name", ""),
                                                "args": ""})
                    if blk.get("id"):
                        tc["id"] = blk["id"]
                    if blk.get("name"):
                        tc["name"] = blk["name"]
            elif evt == "content_block_delta":
                delta = obj.get("delta") or {}
                dtype = delta.get("type")
                if dtype == "text_delta":
                    txt = delta.get("text", "")
                    if txt:
                        self._streamed = True
                        text_parts.append(txt)
                        if on_delta:
                            on_delta(txt)
                elif dtype == "thinking_delta":
                    # Claude 扩展思考块（部分模型返回）
                    txt = delta.get("thinking", "")
                    if txt and on_reasoning:
                        on_reasoning(txt)
                elif dtype == "input_json_delta":
                    # 工具参数 JSON 增量
                    partial = delta.get("partial_json", "")
                    if partial and cur_block_idx >= 0:
                        tc = tool_calls.setdefault(cur_block_idx,
                                                   {"id": "", "name": "", "args": ""})
                        tc["args"] += partial
            elif evt == "content_block_stop":
                cur_block_type = None
            elif evt == "message_delta":
                d = obj.get("delta") or {}
                u = obj.get("usage") or {}
                if u and usage:
                    usage["completion_tokens"] = u.get("output_tokens", 0)
                    usage["total_tokens"] = usage["prompt_tokens"] + u.get("output_tokens", 0)
                elif u:
                    usage = {"prompt_tokens": 0,
                             "completion_tokens": u.get("output_tokens", 0),
                             "total_tokens": u.get("output_tokens", 0)}
            elif evt == "message_stop":
                break
            elif evt == "error":
                err = obj.get("error") or {}
                raise AgentLLMError(f"Anthropic API 错误：{err.get('message', str(obj))}")
        resp.close()
        # 工具调用参数 JSON 增量拼凑后解析为 dict（与 chat 协议输出格式一致）
        out_tc = []
        for idx in sorted(tool_calls.keys()):
            tc = tool_calls[idx]
            args_str = tc.get("args", "") or "{}"
            try:
                json.loads(args_str)   # 验证 JSON 合法性
            except json.JSONDecodeError:
                args_str = "{}"
            out_tc.append({"id": tc.get("id", ""),
                           "type": "function",
                           "function": {"name": tc.get("name", ""),
                                        "arguments": args_str}})
        return {"text": "".join(text_parts),
                "tool_calls": out_tc,
                "usage": usage}


# ============================================================
# 模型配置加密存储（本地 JSON 文件加密）
# ============================================================
# 密钥派生：Windows MachineGuid（每台机器唯一，注册表 HKLM\SOFTWARE\Microsoft\Cryptography）
# 用「随机盐 + PBKDF2-SHA256(机器密钥)」派生独立的加密/认证密钥，规避固定密钥 XOR 的
# 已知明文/重放问题；流式加密 + HMAC 完整性标签（类 AES-GCM 的纯标准库实现）。
# 防止 settings.json 明文暴露用户的 API Key 与模型配置。仍兼容读取旧版 XOR 密文。

def _machine_key() -> bytes:
    """从 Windows MachineGuid 派生 64 字节机器主密钥（机器唯一，重装系统会变）"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\Microsoft\Cryptography") as k:
            guid, _ = winreg.QueryValueEx(k, "MachineGuid")
        import hashlib
        return hashlib.sha256(guid.encode()).digest() * 2   # 扩大熵，供加密/认证各取半
    except OSError:
        # 兜底：用用户名 + 主机名（跨机器不通用但至少不裸露）
        import hashlib
        seed = f"{os.environ.get('USERNAME','')}{os.environ.get('COMPUTERNAME','')}zhuzhu_copilot"
        return hashlib.sha256(seed.encode()).digest() * 2


@_lru(maxsize=16)
def _pbkdf2_derive(master: bytes, salt: bytes, info: str) -> bytes:
    """PBKDF2-HMAC-SHA256 派生（模块级 lru_cache：同 master+salt+info 只派生一次）。
    200k 迭代约 0.3s/次，解密需派生 enc+mac 两把钥匙共 ~0.6s；settings.json 的
    密文 salt 固定不变，进程内多次解密（面板构造/设置页/引擎加载）直接命中缓存。"""
    import hashlib
    return hashlib.pbkdf2_hmac("sha256",
                               master,
                               salt + info.encode("utf-8"),
                               200_000, dklen=32)


def _derive_key(master: bytes, salt: bytes, info: str) -> bytes:
    """PBKDF2-HMAC-SHA256(机器主密钥 + 随机盐) 派生态密钥（按用途 info 区分）。
    派生结果按 (master, salt, info) 缓存，避免每次解密重复 200k 迭代。"""
    return _pbkdf2_derive(bytes(master), bytes(salt), info)


def _cts_ctr_keystream(enc_key: bytes, nonce: bytes, length: int) -> bytes:
    """计数器流生成：以 SHA-256(enc_key||nonce||counter) 逐块扩展加密流。
    与固定密钥 XOR 不同：nonce 每次随机，密文流互不相同，杜绝重放/联网碰撞。"""
    import hashlib
    out, ctr = bytearray(), 1
    while len(out) < length:
        out += hashlib.sha256(enc_key + nonce + ctr.to_bytes(4, "big")).digest()
        ctr += 1
    return bytes(out[:length])


def _encrypt_config(data: str) -> str:
    """A2 方案加密：A2:<base64url( salt(16) + nonce(16) + ciphertext + tag(32) )>。
    空串原样返回（与 A2 标记区分）。"""
    import base64
    import hashlib
    import hmac
    import os as _os
    if not data:
        return data
    master = _machine_key()
    salt = _os.urandom(16)
    nonce = _os.urandom(16)
    enc_key = _derive_key(master, salt, "enc")
    mac_key = _derive_key(master, salt, "mac")
    raw = data.encode("utf-8")
    stream = _cts_ctr_keystream(enc_key, nonce, len(raw))
    ct = bytes(b ^ s for b, s in zip(raw, stream))
    tag = hmac.new(mac_key, salt + nonce + ct, hashlib.sha256).digest()
    blob = base64.urlsafe_b64encode(salt + nonce + ct + tag).decode("ascii")
    return "A2:" + blob


def _decrypt_config(data: str) -> str:
    """解密：识别 A2: 前缀走新 AES 类方案；否则回退旧版 XOR（兼容已存密文）。
    完整性校验失败/解析失败返回原字符串（由调用方回退，不抛异常崩溃）。"""
    import base64
    import hashlib
    import hmac
    if not data:
        return data
    if data.startswith("A2:"):
        try:
            blob = base64.urlsafe_b64decode(data[3:].encode("ascii"))
            salt, nonce, ct, tag = blob[:16], blob[16:32], blob[32:-32], blob[-32:]
            master = _machine_key()
            enc_key = _derive_key(master, salt, "enc")
            mac_key = _derive_key(master, salt, "mac")
            expect = hmac.new(mac_key, salt + nonce + ct, hashlib.sha256).digest()
            if not hmac.compare_digest(expect, tag):
                return data   # 完整性校验失败（被篡改/错机器），安全回退
            stream = _cts_ctr_keystream(enc_key, nonce, len(ct))
            return bytes(b ^ s for b, s in zip(ct, stream)).decode("utf-8", "replace")
        except Exception:
            return data
    # 旧版 XOR 密文：机器主密钥前 32 字节即旧 SHA-256 单块密钥
    key = _machine_key()[:32]
    try:
        raw = base64.urlsafe_b64decode(data.encode("ascii"))
    except Exception:
        return data
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(raw)).decode("utf-8", "replace")


def encrypt_model_config(model_section: dict) -> dict:
    """将 model 配置节加密后存入 settings.json。
    
    返回一个新 dict，其中 model 字段替换为加密后的 {"_encrypted": true, "data": "<cipher>"}。
    外部调用方用此返回值安全存储。
    """
    if not model_section or not isinstance(model_section, dict):
        return model_section
    raw = json.dumps(model_section, ensure_ascii=False, separators=(",", ":"))
    return {"_encrypted": True, "data": _encrypt_config(raw)}


def decrypt_model_config(model_section: dict) -> dict:
    """解密 settings.json 中的 model 配置节。
    
    若 model_section 包含 _encrypted 标记则解密还原；否则原样返回（兼容旧版明文配置）。
    """
    if not isinstance(model_section, dict):
        return model_section
    if not model_section.get("_encrypted"):
        return model_section  # 旧版明文，直接返回
    cipher = model_section.get("data")
    if not cipher:
        return model_section
    try:
        plain = _decrypt_config(cipher)
        return json.loads(plain)
    except Exception:
        return model_section  # 解密失败（如换机器），回退旧数据
