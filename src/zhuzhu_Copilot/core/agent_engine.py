"""Agent 引擎：循环"浏览器观察 → 调用工具 → 验证结果"

- 流式输出：LLM 逐 token 回调（UI 实时显示）
- 工具调用：内置工具 + MCP 工具；每个工具执行前回调 confirm（UI 弹窗每步确认）
- 浏览器：AI 用 browser_open/navigate/snapshot/click 等独立浏览器工具操控网页，
  browser_snapshot 返回的页面截图作为视觉输入
- 沙盒：危险工具即使批准也由 agent_tools 硬拒绝
- tokens：发送前预计算（estimate），响应后累计实际 usage
"""

import json
import logging
import os
import queue
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from PyQt6.QtCore import Qt, QByteArray, QBuffer, QIODevice
from PyQt6.QtGui import QImage

from zhuzhu_Copilot.core import agent_llm, agent_tools, agent_skills, agent_subagent, agent_tts
from zhuzhu_Copilot.core import agent_workflow
from zhuzhu_Copilot.core import agent_sandbox
from zhuzhu_Copilot.core import agent_background
# 账号系统：内置默认模型（agnes）登录门槛 + 按 token 实时积分消耗
from zhuzhu_Copilot.core.auth_client import (
    get_auth_client as _get_auth_client,
    is_builtin_base_url as _is_builtin_base_url,
    PointsConsumer,
)
from zhuzhu_Copilot.core.i18n import ui as _ui, uif as _uif
from zhuzhu_Copilot.core.agent_json import parse_tool_args
# agent_screen.virtual_desktop 惰性导入：其模块导入链约 500ms，仅任务实际
# 需要切虚拟桌面时（auto_vd 开启）才加载，加快 AI 面板打开与首任务启动

_log = logging.getLogger("zhuzhu_Copilot.agent_engine")

# 开发类工具：动手开发/修改代码前必须先确认用户开发规则（首次调用被拦截，规则确认后下一轮放行）
_DEV_TOOLS = frozenset({"write_file", "edit_file", "delete_file",
                        "insert_lines", "run_command", "create_skill",
                        "dispatch_sub_agents"})

# 可并发工具：同一轮返回多个此类调用时批量并行执行（互不等待），显著缩短长任务总耗时。
# 分两类，语义不同：
#   写类 _CONCURRENT_WRITE_TOOLS：有副作用，同一文件按解析后路径加锁串行（防并发写坏），
#     不同文件真正并行；
#   只读类 _CONCURRENT_READ_TOOLS：无副作用、无共享可变状态，直接并行（同路径无需加锁）。
# 其余工具（run_command/delete_file/浏览器/交互/子 Agent 等有副作用或必须保序者）维持串行。
# 结果回调（on_result/on_preview）始终在串行收尾循环中按原始调用顺序触发，
# 因此并发只影响执行耗时，不会打乱对话展示与上下文落库顺序。
# 可通过设置 concurrent_edits=false 整体关闭（回退全串行）。
_CONCURRENT_WRITE_TOOLS = frozenset({"write_file", "edit_file", "search_replace",
                                     "insert_lines"})
# 注：web_fetch 刻意不入列 —— 直连失败时会回退启动**独立 headless 浏览器**
# （_web_fetch_render，无全局限流），并发多个会同时拉起多个浏览器进程（数百 MB），
# 因资源竞争反而更慢；web_search 走纯 HTTP 多引擎请求，轻量，可安全并发。
_CONCURRENT_READ_TOOLS = frozenset({"read_file", "list_directory", "search_files",
                                    "grep", "search_code", "find_app", "extract_text",
                                    "system_info", "get_time", "env_var", "git_info",
                                    "web_search"})
_CONCURRENT_TOOLS = _CONCURRENT_WRITE_TOOLS | _CONCURRENT_READ_TOOLS
# 并发执行的线程数上限（同一批内的最大并行任务数；磁盘 IO 为串行瓶颈，不宜过大）
_CONCURRENT_MAX_WORKERS = 4

# 基础/交互工具白名单：这些工具的调用永不触发「技能规范硬拦截」。
# 例如 ask_user 提问是用户交互，被无关技能 instruction 顺带提及而误拦截会让
# 模型收到"该工具由某技能规范化"的提示、进而跳过提问/延迟提问（用户已多次遇到）。
_NO_INTERCEPT_TOOLS = frozenset({"ask_user"})

# ---- 子 Agent / 跨工作流派发工具族（准入受任务复杂度分档控制）----
# 简单与中等任务禁止派发：schema 层剔除 + 执行层硬拦截（双保险，提示词干预无法绕过）。
# 注册式子 Agent 工具名为 sub_<name>，按前缀统一判定，无需逐个登记。
_SUBAGENT_TOOLS = frozenset({"dispatch_sub_agents", "explore_project", "search_large",
                             "use_workflow_agent"})
_SUBAGENT_TOOL_PREFIX = "sub_"


def _is_subagent_tool(name: str) -> bool:
    """是否属于「子 Agent / 跨工作流派发」工具族（含注册式 sub_<name>）"""
    n = str(name or "")
    return n in _SUBAGENT_TOOLS or n.startswith(_SUBAGENT_TOOL_PREFIX)

# 朗读分段：只在句末标点处切句（逗号/换行不切，避免零散 API 调用造成卡顿）
_TTS_SENT_END = ("。", "！", "？", "！？", "……", "…", "！", "?", "…")
# 累积到该长度才触发一次合成（过短句子合并，减少 API 往返）
_TTS_MIN_SEG = 18
# 无句末标点时达到该长度强制切分（保证长句也能边输出边朗读）
_TTS_MAX_SEG = 60

# 默认上下文窗口：按模型实际窗口动态计算阈值。中文模型实际 token 密度约 0.5-0.7/字，
# 阈值设高避免频繁压缩打断缓存前缀。窗口来源优先级由 agent_llm.resolve_context 决定：
# 1M 开关 > 上游服务商声明 > 服务商配置手填 > 内置已知表 > 模型名推断。
_DEFAULT_CTX_WINDOW = 262144

# 预留输出（token）：上游声明 max_output_tokens 时优先，否则按此值预留。
# 所有上下文阈值都基于「可用输入预算 = 窗口 − 预留输出」计算（主流 agent 应用口径：
# 拿裸窗口按比例算会把输出空间一起吃掉，长对话末期极易被上游 400 拒绝）。
_DEFAULT_MAX_OUTPUT = 8192

# 压缩触发阈值 = 可用输入预算 × 该比例（留出响应空间）：
# 1M 长上下文模式（设置页「开启 1M 上下文」）用 0.80，默认保持 0.75。
_CTX_RATIO_COMPRESS = 0.75
_CTX_RATIO_COMPRESS_LONG = 0.80

# 预警阈值：只提示不动作，告知用户即将进入自动压缩区间
_CTX_RATIO_WARN = 0.70

# 发送前硬上限 = 可用输入预算 × 该比例：超过即强制裁剪（压缩的兜底），
# 并与「窗口 − 安全余量」取更严者，任何情况下都不发出超窗请求（否则上游 400）
_CTX_RATIO_CEILING = 0.95

# 压缩冷却：上次压缩后至少间隔 N 轮，避免连续压缩破坏上下文缓存
_COMPRESS_COOLDOWN = 5

# 最近窗口保留预算比例：压缩时按此动态收紧 keep_recent，保证"最近 N 条"本身不破窗
# （这是防超窗的关键——单条超大/最近 60 条都可能是主因）。基于可用输入预算计算，
# 保留约一半预算：压缩后明显低于压缩阈值，同时保住足够的近期上下文。
_CTX_RATIO_RECENT = 0.50

# 发送前硬上限的绝对安全余量（与比例口径取更严者）
_CTX_CEILING_MARGIN = 8192

# 上游用量字段（服务商返回并已归一化为 OpenAI 命名，见 agent_llm._norm_usage）：
# prompt/completion 为请求消耗，cache_hit/cache_miss 为提示词前缀缓存命中情况。
_USAGE_KEYS = ("prompt", "completion", "cache_hit", "cache_miss")


# 上游「上下文超长」400 的识别特征（各服务商文案不统一，穷举常见写法）。
# 命中即认为"本轮请求已越窗"，可安全地压缩+硬裁后原地重试（有界），
# 而不是把整个任务判失败（用户报的"1M 上下文爆满 → HTTP 400"）。
_CTX_OVERFLOW_KEYS = ("maximum context length", "context length is",
                      "context_length_exceeded", "context window",
                      "reduce the length", "too long", "exceeds the maximum",
                      "maximum number of tokens", "上下文长度", "上下文超长",
                      "超过最大", "长度超限", "超出上下文")
# 越窗自动恢复的最大次数（每任务归零）：每次都会上修 token 校准系数并强压上下文
_MAX_CTX_OVERFLOW_RETRIES = 2

# token 密度校准：样本足够大的估算才采信（避免工具 schema 等固定开销在小样本上
# 把系数推得过大），系数双向修正但有下界（下界即"最少保留多少保守量"，见 _TOK_FACTOR_MIN）
_TOK_CALIB_MIN_EST = 20000
_TOK_FACTOR_MIN = 0.75
_TOK_FACTOR_MAX = 3.0


def _is_ctx_overflow(err) -> bool:
    """错误是否属于「上游上下文超长（HTTP 400）」类。

    只按已知文案特征识别：命中后引擎会把本轮当作"请求越窗"处理（压缩+硬裁+重试），
    误判代价有限（最多多压缩一次），漏判代价是任务直接失败，故口径宁可宽松。
    """
    t = str(err or "").lower()
    return any(k.lower() in t for k in _CTX_OVERFLOW_KEYS)


def _zero_usage() -> dict:
    """零值用量桶（tokens 累计 / last_usage 最近一次共用同一字段集，避免两处硬编码键名）"""
    return dict.fromkeys(_USAGE_KEYS, 0)

# 读取长文本自动压缩：AI 读取(read_file/web_fetch/extract_text)返回的超长文本
# 在进入上下文前用当前模型生成结构化摘要，避免单条长文本独占上下文预算。
_READ_CONDENSE_TOOLS = frozenset({"read_file", "web_fetch", "extract_text",
                                  "grep", "search_code", "search_files"})
# 触发阈值：单个工具返回文本超过该字符数即触发压缩
_READ_CONDENSE_CHARS = 12000
# 压缩产物的预算：结构化摘要 + 原文头尾备份合起来不得超过该字符数
_READ_CONDENSE_BUDGET = 8000
# 压缩输入截断保护：单次最多喂给模型的原始文本长度（避免超大文本撑爆摘要请求）
_READ_CONDENSE_INPUT = 40000

# 连续失败护栏：一轮内任一工具失败即算失败轮；连续失败达该阈值则强制停止，
# 避免 LLM 反复调用同一失败工具空转（硬性上限，不依赖模型自觉停止）
_MAX_CONSEC_FAIL = 5

# 上游空响应纠正重试上限：返回空结果（无正文且无工具调用）不致命，注入纠正提示
# 重试有限次再报错。避免长任务流中偶发空响应直接失败 → 用户手动重试重跑整个任务
# → 已答过的 ask_user 问题被再次弹出（重复询问）。
_MAX_EMPTY_RESULT_RETRIES = 2

# 任务清单 / 任务技能消息的**定位哨兵**：仅用于本模块在 self._messages 里
# 找到「上一轮注入的那条消息」并原位替换（startswith 匹配），不是给模型看的文案。
# 必须语言无关：若哨兵随提示词语言变化，切换语言后新哨兵匹配不到旧哨兵，
# 旧消息会留在历史里 → 同一份任务清单重复出现两次。给模型看的标题走 _tp 查表。
_TODO_MARK = "[todo]"
_SKILL_MARK = "[task-skills]"

# 单条消息 token 估算缓存：允许缓存条目比消息数多出的余量（超过即整体清空重建）。
# 消息被压缩/裁剪后旧条目会残留，超过余量就重建，避免缓存随长任务无限增长。
_EST_CACHE_SLACK = 64


def _wf_fallback_persona(wf: str) -> str:
    """非默认工作流缺少自定义 agent.py 人设时的兜底：以工作流名派生专属人设，
    确保每个工作流的人格各不相同，且不套用默认提示词/人设。

    走提示词语言查表：末句「请使用简体中文回复」在英文提示词语言下必须变成
    "reply in English"，否则该指令本身是中文 —— 模型收到互相矛盾的语言要求。"""
    return agent_skills._tpf(
        "prompt.wf.persona",
        "你是「{wf}」工作流专属 AI 助手，拥有独立于通用助手的专属人格与职责设定。\n"
        "请始终围绕「{wf}」工作流的定位与目标开展工作，遵循该工作流核心文件"
        "（agent.py / llm.py / tools.py / skills/）定义的职责与约束，"
        "不要套用通用助手的默认行为与提示词。\n"
        "请使用简体中文回复，语言精炼，先思考再行动，必要时调用工具完成任务。",
        wf=wf)


def _heuristic_condense(tool_name: str, text: str) -> str:
    """读取长文本的启发式压缩兜底（LLM 不可用时）：保留开头 + 抽样要点 + 结尾，
    避免退回整段原始长文本进上下文。"""
    text = text or ""
    budget = _READ_CONDENSE_BUDGET // 4
    head = text[:budget]
    tail = text[-budget:]
    # 抽样要点：取非空行的行首若干字符，尽量保留结构（标题/章节/函数签名）
    bullets, seen = [], 0
    for ln in text.splitlines():
        s = ln.strip()
        if len(s) < 6 or s.startswith((" ", "\t")):
            continue
        bullets.append(s[:80])
        seen += 1
        if seen >= 20:
            break
    pts = "\n".join(f"- {b}" for b in bullets) or agent_skills._tp(
        "prompt.condense.no_points", "（无要点）")
    return agent_skills._tpf(
        "prompt.condense.body",
        "[阅读压缩-启发式副稿] {tool} 返回长文本（原文 {n} 字符）的"
        "自动提取要点：\n{pts}\n"
        "\n[原文头尾备份] 如需精确细节请用 {tool} 分段读取核对：\n"
        "--- 开头 ---\n{head}\n--- 结尾 ---\n{tail}",
        tool=tool_name, n=len(text), pts=pts, head=head, tail=tail)


# ---- 按任务裁剪工具集（P0）----
# 核心常备工具：几乎任何任务都可能用到，永远保留
_CORE_TOOLS = frozenset({
    "read_file", "write_file", "edit_file", "search_replace", "insert_lines",
    "undo_file",
    "delete_file", "list_directory", "run_command", "check_command",
    "find_app", "search_files", "grep", "search_code",
    "web_search", "web_fetch", "fast_download",
    # 用户提供文件的读取能力（微信上传/本地拖入的 PPT/Word/Excel/PDF/图片/代码等）：
    # agent 收到「[微信文件] 已保存到本地…」后必须能立刻读取，任务裁剪不得剔除
    "read_docx", "read_pptx", "read_xlsx", "read_pdf", "extract_text", "view_image",
    "system_info", "get_time", "env_var", "ask_user", "clipboard",
    "save_memory", "load_memory", "update_todo", "list_todo", "git_info",
    "explore_project",
    "inspect_customization",   # 深度自定义总入口：任何自定义/新增功能场景都需先盘点现状
    "set_generation_progress",  # 生成类任务实时进度上报：UI/UX 包/插件生成时恒可用
    "shared_context",          # 共同上下文空间：编队协同（主 Agent 开启/成员双向读写）
    "set_session_name",        # 会话命名：AI 可给当前对话起名/改名（用户明确要求时必调）
    "register_sub_agent",      # 注册式子 Agent：用户任何措辞的「创建子 agent」都要能落到它
    "list_sub_agents",         # 先看现状再注册；与上面配套，避免任务裁剪后无法创建子 Agent
    "chat_with", "look_context",       # 工作团沟通与监督：领导者随时可讨论/查看成员上下文
    "pause_agent", "resume_agent", "warn_agent",  # 工作团管控：暂停/恢复/警告成员
    "dispatch_sub_agents",             # 工作团派发：领导者随时可把任务派发给成员/子 Agent
    "preview_open", "preview_refresh",  # 可视化预览：把产物送进用户浏览器并刷新（决策由模型做，恒可用）
    "send_file_to_wechat", "send_files_to_wechat",  # 微信交付：任何任务都可能推送产物到手机微信，不可被任务裁剪
})
# ---- 高效模式（设置页开关）保留的最小工具集 ----
# 只保留完成真实工作所必需的「文件 / 命令 / 检索 / 网络 / 问答 / 清单」工具：
#   · 不含任何 MCP server 工具（_all_tools 直接跳过 MCP 合并）
#   · 不含插件与工作流自定义工具（它们都是"可选扩展"，只进白名单之外的集合）
#   · 不含子 Agent / 工作团 / 技能注册类工具
#   · 不含文档三件套、预览、朗读、微信交付、自定义等功能型工具
# 效果：每轮工具 schema token 大幅下降（首包更快、更省额度），系统提示词里的
# 工具手册/技能路由整段不再注入（见 agent_skills.build_system_prompt 的 efficient）。
_EFFICIENT_TOOLS = frozenset({
    "read_file", "write_file", "edit_file", "search_replace", "insert_lines",
    "undo_file", "delete_file", "list_directory",
    "search_files", "grep", "search_code",
    "run_command", "check_command",
    "web_search", "web_fetch", "find_app", "get_time",
    "ask_user", "update_todo", "list_todo",
})

# 任务类别 → (触发词, 额外暴露的工具)。触发词命中即裁剪到「核心+该类」，
# 减小 schema token、降低选错工具概率；未命中任何类别则保留全部（保守）。
_TASK_GROUPS = [
    ("browser",
     ("打开浏览器", "打开网页", "打开网站", "浏览网页", "网页操作", "网页",
      "登录", "点赞", "点视频", "看视频", "刷视频", "刷网页", "抓取网页",
      "上网站", "进网站", "填表", "打卡", "点一下", "帮我打开", "帮我点"),
     frozenset({"browser_open", "browser_navigate", "browser_snapshot", "browser_click",
                "browser_type", "browser_scroll", "browser_eval", "browser_html",
                "browser_close", "browser_tabs", "browser_switch_tab"})),
    ("doc",
     ("ppt", "pptx", "powerpoint", "演示文稿", "word", "docx", "文档", "excel",
      "xlsx", "表格", "报告", "简历", "计划书", "感言", "总结", "方案",
      "毕业论文", "宣传单", "邀请函", "收款记录", "清单", "三件套"),
     frozenset({"create_docx", "create_pptx", "create_xlsx", "extract_text",
                "beautify_docx", "beautify_pptx", "beautify_xlsx",
                "read_docx", "read_pptx", "read_xlsx", "read_pdf",
                "edit_docx", "edit_pptx", "edit_xlsx",
                "generate_image"})),
    ("image",
     ("图片", "配图", "插图", "生成图", "海报图", "logo", "图像"),
     frozenset({"generate_image"})),
    ("sys",
     ("迁移", "卸载", "内存优化", "优化内存", "安装软件", "系统信息", "开机自启"),
     frozenset({"migrate_app", "uninstall_app", "optimize_memory", "new_project"})),
    ("subagent",
     ("并行", "并发", "大规模搜索", "分布式", "同时处理", "多任务"),
     frozenset({"dispatch_sub_agents", "search_large"})),
    ("skill",
     ("创建技能", "新技能", "自定义技能", "下载技能", "安装技能"),
     frozenset({"create_skill"})),
    ("tts",
     ("朗读", "语音回复", "读出来", "听一下", "配音"),
     frozenset({"tts_speak"})),
    ("cordis",
     ("工作流", "cordis", "自定义 agent", "自定义agent", "agent 核心文件", "agent核心文件",
      "创建工作流", "切换工作流", "编辑agent", "编辑 agent", "创建agent", "创建 agent",
      "agent 模板", "agent模板", "核心文件", "切换调用", "调用工作流", "agent 能力", "agent能力",
      "深度自定义", "深度定制", "全面自定义", "自定义功能", "新增功能", "添加功能",
      "扩展功能", "改造", "深度定制 agent", "自定义"),
     frozenset({"list_workflows", "create_workflow", "switch_workflow",
                "inspect_workflow", "edit_agent_file", "delete_workflow",
                "list_workflow_agents", "use_workflow_agent",
                "inspect_customization", "list_sub_agents", "register_sub_agent",
                "list_builtin_workflows", "shared_context",
                "register_feature_panel",
                # UI/UX 与插件生成：自定义任务裁剪后仍需可用（自然语言生成/手动管理）
                "create_uiux", "manage_uiux", "create_plugin"})),
]
_TASK_GROUP_TOOLS = {g: tools for g, _kw, tools in _TASK_GROUPS}

# 显式规划提示：拼到每条任务的用户消息末尾，强化「先规划再动手、复杂任务必用 todo、结束后自检完成度」
_PLAN_HINT_BASE_ZH = ("\n\n【执行要求】请先输出简要执行计划（编号步骤）再开始调用工具；"
                      "复杂/多步骤任务必须先调用 update_todo 建立任务清单（每步登记 pending/in_progress/completed）"
                      "并每完成一步就立即更新对应状态，严禁跳过 todo；"
                      "每步执行后检查结果，最后对照计划确认任务已全部完成，未完成继续补做。")
# 仅「非常复杂」任务（complex 档）追加的子 Agent 派发引导。
# 简单/中等任务不出现此段，避免给模型"可以组队"的暗示（分级准入见 agent_llm.subagent_allowed）。
_PLAN_HINT_SUBAGENT_ZH = ("非常复杂的任务（跨多模块重构/大规模并发读写/大范围搜索），"
                          "可调用 dispatch_sub_agents 一次性派发多个子 Agent 并发协作"
                          "（子 Agent 可创建/写入/编辑/搜索/查找项目文件），执行后汇总结果并对照计划补全；"
                          "简单与中等任务一律自己直接完成，禁止派发子 Agent 或调用其他工作流主 Agent。")


def _plan_hint(subagents_allowed: bool) -> str:
    """本轮任务的执行要求提示（按子 Agent 准入分级，见 _PLAN_HINT_SUBAGENT_ZH）

    每次调用现查表而非用模块常量：提示词语言可在设置里即时切换，
    缓存成常量会导致切换后仍发旧语言的任务要求。"""
    base = agent_skills._tp("prompt.plan.base", _PLAN_HINT_BASE_ZH)
    if not subagents_allowed:
        return base
    return base + agent_skills._tp("prompt.plan.subagent", _PLAN_HINT_SUBAGENT_ZH)

# 参考性上下文（团队消息 / 共同上下文快照）的收尾说明：既是"无需回应"的降噪提示，
# 更是**身份隔离声明**——快照里常有其他成员或上一个工作流的角色自称（如"我是产品经理，
# 语气务实"），若不加约束，模型会顺从这段更贴近的文本而非本次工作流人设（对话内切换
# 工作流后自称沿用旧身份，就是这一处造成的）。子 Agent 侧同样是"快照在前、任务在最后"，
# 文案与约束保持一致。
_TEAM_MSG_NOTE_ZH = ("\n（团队消息：仅供参考与协同，请结合当前任务处理；"
                     "其中出现的角色自称与身份描述不适用于你）")
_SHARED_SNAPSHOT_NOTE_ZH = (
    "\n\n（以上为共同上下文空间快照，仅供了解团队既有进展与结论，无需单独回应；"
    "其中出现的角色自称、称谓、身份与语气描述，无论署名为谁（包括 main），"
    "均属其他成员或历史轮次的陈述，一律不适用于你，不得沿用或模仿 —— "
    "你的身份、职责与语气严格以系统设定为准；被问及身份时按系统设定回答。"
    "请直接处理用户最新一条消息。）")


def team_msg_note() -> str:
    """团队消息收尾说明（按提示词语言现查）。"""
    return agent_skills._tp("prompt.note.team_msg", _TEAM_MSG_NOTE_ZH)


def shared_snapshot_note() -> str:
    """共同上下文快照收尾说明（按提示词语言现查）。"""
    return agent_skills._tp("prompt.note.shared_snapshot", _SHARED_SNAPSHOT_NOTE_ZH)


def _detect_task_groups(text: str):
    """按用户输入匹配任务类别，返回命中的组名列表；未命中任何类别返回 None（不裁剪）。"""
    t = (text or "").lower()
    if not t:
        return None
    hit = [g for g, kws, _tools in _TASK_GROUPS if any(k in t for k in kws)]
    return hit or None

# 对话上下文持久化路径
CONTEXT_FILE = agent_skills.CONFIG_DIR / "context.json"


def _looks_failed(text: str) -> bool:
    """工具返回文本是否含失败/异常特征（用于触发纠错提示）"""
    return bool(text and any(k in text for k in
                             ("失败", "错误", "未找到", "拒绝", "超时", "[工具", "[沙盒", "[MCP")))


def _compress_data_url(data_url: str, max_width: int = 320, quality: int = 80) -> str:
    """把图片 data URL 压缩为小尺寸 JPEG（保存上下文时防止文件过大）"""
    import base64
    try:
        if not isinstance(data_url, str) or not data_url.startswith("data:image"):
            return data_url
        _, _, b64 = data_url.partition(",")
        img = QImage.fromData(base64.b64decode(b64))
        if img.isNull():
            return data_url
        if img.width() > max_width:
            img = img.scaledToWidth(max_width, Qt.TransformationMode.SmoothTransformation)
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        img.save(buf, "JPEG", quality)
        return "data:image/jpeg;base64," + base64.b64encode(bytes(ba)).decode()
    except Exception:
        return data_url


# ══════════════ 内置工具的「等待上限」 ══════════════
# 语义是「引擎愿意等多久」，不是「工具本身能跑多久」：超时只是放弃等待，
# 后台线程仍在跑 —— 于是超时后模型拿到的是「已停止等待」而非真实结果，上下文与实际进度错位。
_NO_WAIT_TIMEOUT_TOOLS = frozenset({
    "run_command",      # 长命令（构建/训练/长脚本）天然无上限，由停止按钮取消
    "create_plugin",    # 生成类：内部是完整 LLM 往返（设计 + 自检 + 可能自修），远超 40s
    "send_file_to_wechat",   # 微信推送：大文件 CDN 上传可超 40s，超时放弃会与真实结果错位
    "send_files_to_wechat",  # 微信批量推送：N 个文件 × 多次网络往返（getuploadurl+CDN+sendmessage）
})
_DOWNLOAD_WAIT_TIMEOUT_S = 3600.0    # 下载：不按 40s 放弃（长下载由 UI 进度条 + 停止取消）
_DEFAULT_WAIT_TIMEOUT_S = 40.0       # 其余内置工具：40s 后放弃等待，避免长阻塞


def _tool_wait_timeout(name: str):
    """内置工具的等待上限（None = 不设上限，只有 stop 可中断）。

    单独成函数而不是内联三元：新增「生成类/长任务」工具时只需进
    `_NO_WAIT_TIMEOUT_TOOLS`，不必在调用点堆条件（也便于测试直接断言）。
    """
    if name in _NO_WAIT_TIMEOUT_TOOLS:
        return None
    if name == "fast_download":
        return _DOWNLOAD_WAIT_TIMEOUT_S
    return _DEFAULT_WAIT_TIMEOUT_S


def _call_with_stop(fn, stop_event, timeout: float = 30.0):
    """在独立 daemon 线程中执行 fn；超时或 stop 触发时放弃（线程后台自动回收）。

    解决 MCP 等无超时阻塞调用导致引擎线程无法中断、AI 无法停止的问题。
    timeout=None 表示不做时间上限：只有 stop 可中断，长任务持续到完成（如子 Agent）。
    """
    box = {}

    def run():
        try:
            box["v"] = fn()
        except Exception as e:
            box["e"] = e

    t = threading.Thread(target=run, daemon=True)
    t.start()
    deadline = None if timeout is None else time.time() + timeout
    while True:
        if not t.is_alive():
            break
        if stop_event.is_set():
            break
        if deadline is not None and time.time() >= deadline:
            break
        time.sleep(0.1)
    if t.is_alive():
        if stop_event.is_set():
            return None   # 用户停止：放弃等待（线程后台自动回收）
        raise TimeoutError("工具执行超时")
    if "e" in box:
        raise box["e"]
    return box.get("v")


class AgentEngine:
    def __init__(self, llm: agent_llm.LLMClient,
                 mcp_manager=None,
                 on_delta=None, on_status=None, on_result=None, confirm=None,
                 ask_user=None, on_reasoning=None, auto_vd: bool = False,
                 text_only: bool = False, memory_enabled: bool = True,
                 direct: bool = False, on_sub_event=None,
                 allow_subagents: bool = True,
                 efficient_mode: bool = False,
                 on_engine_rebuild=None,
                 on_uiux_rebuild=None,
                 on_btn_reg=None,
                 on_session_name=None,
                 on_preview=None,
                 workflow: str = None,
                 persona: str = None,
                 context_window: int = None,
                 max_output_tokens: int = None,
                 long_context_1m: bool = False,
                 window_source: str = "",
                 conversation: str = ""):
        """
        on_delta: Callable[[str], None]      流式文本增量
        on_status: Callable[[str], None]     步骤状态（如"正在思考/执行工具 click"）
        on_result: Callable[[str, str], None] 工具执行结果（工具名, 输出文本）
        confirm: Callable[[str, dict], bool] 工具执行前确认；None 表示自动放行（测试用）
        ask_user: Callable[[dict], str]      ask_user 提问回调（阻塞式，返回用户回答）
        on_reasoning: Callable[[str], None]  流式思考过程增量
        on_sub_event: Callable[[str, int, str, str, str], None]
                 子 Agent 事件（kind, task_idx, title, text, agent_id）：
                 start=子任务开始；delta=子 Agent 流式输出增量；
                 agent_id=该子 Agent 的控制注册 id，供 UI 子块「暂停/恢复」直接命中。
                 与主 Agent 共用同一个聊天气泡，子任务实时输出以子块形式显示。
        auto_vd: bool 任务自动在独立虚拟桌面执行（开始新建并切入，结束自动返回主桌面），
                 实现"完全静默无感"：AI 操作不打扰用户主桌面
        text_only: bool 纯文本模型（无图像输入），过滤截图/视觉工具
        memory_enabled: bool 记忆开关，关闭时过滤 save_memory/load_memory 工具
        direct: bool 直接工作模式（无确认直行）：跳过开发规则确认与技能路由硬拦截，
                减少询问/约束，直接调用必要技能与命令完成任务
        workflow: str 本引擎所属工作流（@工作流 会话级切换后各会话独立）；None=全局激活
        context_window: int 模型上下文窗口（token）。所有阈值按「窗口 − 预留输出」得到的
                可用输入预算动态计算；None 时按模型名推断（agent_llm.infer_context_window）。
                取值由 agent_llm.resolve_context 决定：
                1M 开关 > 上游服务商声明 > 服务商配置手填 > 内置已知表 > 模型名推断。
        max_output_tokens: int 预留输出（token），上游声明优先，None 时用 _DEFAULT_MAX_OUTPUT
        long_context_1m: bool 是否处于「1M 上下文」模式（开启后压缩阈值由 0.75 提到 0.80）
        window_source: str 窗口来源标记（仅用于展示：1m/upstream/configured/known/inferred）
        allow_subagents: bool 是否允许派发子 Agent / 其他工作流主 Agent。由
                 agent_llm.subagent_allowed（任务复杂度分档 × 面板策略 × 用户显式要求）
                 决定，面板每次任务按当轮力度重算后写入；False 时 schema 剔除 + 执行层硬拦截。
        conversation: str 本引擎所属对话（会话 id）。共同上下文空间按对话隔离——同一对话内
                 主 Agent 与各子 Agent / 成员工作流共用一份空间，不同对话互不可见。
                 任务线程入口据此设置线程局部作用域，派发侧再下传给子 Agent 工作线程。
        efficient_mode: bool 高效模式（设置页开关）：只把最小必需工具集（_EFFICIENT_TOOLS）
                 发给上游 —— 不提供任何 MCP server 工具、插件与工作流自定义工具，也没有
                 子 Agent / 工作团 / 功能型工具；系统提示词同步精简（不注入工具手册、
                 技能路由、技能规范、插件规范），显著降低每轮 token 消耗。
        """
        self.llm = llm
        self.conversation = str(conversation or "")
        self._ctx_window = max(4096, int(context_window or
                                         agent_llm.infer_context_window(
                                             getattr(llm, "model", None) or "")))
        try:
            self._max_output = max(0, int(max_output_tokens or 0))
        except (TypeError, ValueError):
            self._max_output = 0
        self._long_1m = bool(long_context_1m)
        # 窗口来源标记：面板会传入 resolve_context 的判定结果（upstream/configured/known/
        # inferred）；未传时至少能自述是否处于 1M 模式，避免统计面板显示空白来源。
        self._window_source = str(window_source or ("1m" if self._long_1m else ""))
        self._ctx_warned = False      # 预警是否已提示（压缩/回落后复位，避免每轮刷屏）
        self._compaction = {"count": 0, "last_at": 0.0,
                            "last_merged": 0, "last_saved": 0}
        self.workflow = workflow or None
        self.persona = persona or None      # 自定义 Agent 人格覆盖（@agent 切换；None=用工作流人格）
        self.mcp = mcp_manager
        self.on_delta = on_delta
        self.on_status = on_status
        self.on_result = on_result
        self.confirm = confirm
        self.ask_user = ask_user
        self.on_reasoning = on_reasoning
        self.on_sub_event = on_sub_event
        self.auto_vd = auto_vd
        self.text_only = text_only
        self.memory_enabled = memory_enabled
        self.direct = direct
        # 子 Agent / 跨工作流派发准入：简单与中等任务禁止组队（详见 _SUBAGENT_TOOLS 注释）
        self.allow_subagents = bool(allow_subagents)
        # 高效模式：最小工具集 + 精简系统提示词（无 MCP/插件/技能规范），见 _EFFICIENT_TOOLS
        self.efficient_mode = bool(efficient_mode)
        self.on_engine_rebuild = on_engine_rebuild   # Cordis 工作流切换：Callable[[str], None] 接收新工作流名
        self.on_uiux_rebuild = on_uiux_rebuild       # UI/UX 包切换回调：Callable[[], None]（主线程调用）
        self.on_btn_reg = on_btn_reg                 # 按钮注册回调：Callable[[dict], None]，UI 操作须回主线程
        self.on_session_name = on_session_name       # 会话命名回调：Callable[[str], None]（AI 给对话起名，UI 主线程写入）
        self.on_preview = on_preview                 # 文件预览回调：Callable[[str], None]（工具改/读文件时自动下发路径，主线程渲染）
        self._messages: list = []
        # 单条消息 token 估算缓存（键=id(消息对象)，值=(消息对象, content, tool_calls, 估算)）。
        # 见 _estimate_tokens：每轮对全量历史多次重算是长任务的主要固定开销。
        self._est_cache: dict = {}
        self.tokens = _zero_usage()          # 全程累计（跨请求累加，计费/统计口径）
        # 最近一次请求的真实上游 usage（非累加）：上下文占用面板据此判断"当前上下文用了多少"，
        # 比本地估算更准（服务商计费口径）。prompt=0 表示尚无上游数据 → UI 回退本地估算。
        self.last_usage = _zero_usage()
        self.last_usage_at = 0.0             # 最近一次上游 usage 的时间戳（UI 展示新鲜度）
        self.last_estimate = 0       # 最近一次请求前的预计算（输入 tokens，校准口径）
        # token 密度校准系数（0.75~3.0）：本地估算相对上游真实计费的偏差倍率，由每次
        # 请求的真实 usage 反推（扣掉工具 schema 后 real_prompt / 本地原文估算）并
        # 平滑更新，双向修正且始终保留下界 —— 下界即"最少保留的保守量"：
        # 低估会让压缩/硬裁触发过晚 → 上游 HTTP 400（上下文超窗）；高估只是更早压缩。
        self._tok_factor = 1.0
        self._sent_raw_est = 0       # 上一次实际发送前的本地原文估算（校准样本）
        self._ctx_overflow_retries = 0   # 上游越窗 400 的自动恢复次数（每任务归零）
        self._tools_est = 0          # 工具 schema token 估算（见 _tool_schema_tokens）
        self._tools_est_at = 0.0     # 上面那份估算的取样时间（TTL 缓存用）
        self.end_state = ""          # 本轮结束状态: done|stopped|error
        self._edit_bucket = {"added": 0, "removed": 0, "files": {}}   # 本轮变更累计桶（跨线程共享，run 起始重建）
        self._last_edit_delta = {"added": 0, "removed": 0, "files": {}}   # 本轮文件变更统计（气泡末尾「-N +M」）
        self._stop = threading.Event()
        self._thread: threading.Thread = None
        self._builtin_names = {t["function"]["name"] for t in agent_tools.TOOLS}
        self._rules_confirmed = False   # 当前任务是否已确认开发规则
        self._skills_read = set()       # 已注入/已读取规范流程的技能名
        self._skill_consulted = set()   # 已注入规范流程的技能名（每技能最多注入一次，减少拦截频率）
        self._auto_skills = []          # 按用户提示词自动匹配并注入的技能名
        self._task_skills = set()       # 当前任务相关的技能名（自动匹配 + 手动指定，技能规范拦截仅限这些）
        # 任务开始时的技能集快照：热加载时据此找出「刚生成/刚导入」的技能（见 reload_extensions）
        self._skills_at_start: set = set()
        # 扩展热加载串行化：同一批并发工具里可能同时有 create_skill 与 create_plugin 都请求
        # 热加载，而 MCP 重连会重建聚合工具表 —— 并发执行会互相踩（表被半边写入）。
        self._ext_reload_lock = threading.Lock()
        self._consec_fail = 0           # 连续失败护栏计数器（达 _MAX_CONSEC_FAIL 即强制停止）
        self._empty_retries = 0         # 上游空响应连续纠正重试计数（每任务归零，达上限仍空才报错）
        self._ask_answered = {}         # ask_user 问答去重缓存（会话级）：question -> 已回答文本
        self._task_groups = None        # 按任务裁剪的工具类别（run 时按 user_input 计算；None=不裁剪）
        self._compress_cooldown = 0     # 压缩冷却计数器（>0 时跳过压缩）
        # 自动朗读（用户要求"朗读/语音回复"时，AI 流式输出边生成边合成播放）
        self._tts_auto = False          # 本任务是否需要自动朗读
        self._tts_buf = ""              # 流式文本累积游标（未切分部分）
        self._tts_queue = queue.Queue()  # 待朗读句子队列（朗读线程消费）
        self._tts_finish = False        # 是否已停止接收新句子（完成后让队列读完）
        self._tts_stop = threading.Event()  # 立即停止朗读（用户手动停止时置位）
        self._tts_thread = None         # 朗读工作线程
        self._control = None            # 工作团控制句柄（start/run 时注册，结束注销；复用 _stop）
        # 后台任务：本轮启动的后台任务ID列表（用于完成后通知/清理）
        self._bg_tasks_this_turn: list = []
        # 后台任务输出去重：避免同一段输出被重复推送
        self._bg_output_seen: dict = {}
        # 复杂任务自动后台标记（_run_inner 入口按 assess_task_complexity 设置）
        self._auto_background: bool = False

    # ---------- 控制 ----------
    def stop(self):
        self._stop.set()
        self._tts_stop_read()   # 用户停止：立即停掉正在播放/合成的语音

    def reset_tokens(self):
        self.tokens = _zero_usage()
        self.last_usage = _zero_usage()
        self.last_usage_at = 0.0
        self.last_estimate = 0
        # 压缩统计与预警去重随上下文一起清零（新对话不应显示上一轮的压缩次数）
        self._compaction = {"count": 0, "last_at": 0.0, "last_merged": 0, "last_saved": 0}
        self._ctx_warned = False
        self._compress_cooldown = 0

    def clear_history(self):
        """清空全部对话上下文与 tokens（新对话从零开始）"""
        self._messages = []
        self.reset_tokens()

    # ---------- 上下文持久化（重启保留对话） ----------
    def save_context(self, path=CONTEXT_FILE) -> bool:
        """把对话上下文（不含 system）保存到磁盘；图片压缩后存储。
        同时保存已累计的 tokens 统计（含缓存命中）与最近一次上游 usage，
        重启恢复后计数不归零、上下文占用面板可直接沿用真实值"""
        path = Path(path)
        msgs = []
        for m in self._messages:
            if not isinstance(m, dict) or m.get("role") == "system":
                continue
            c = m.get("content")
            if isinstance(c, list):
                out = []
                for x in c:
                    if isinstance(x, dict) and x.get("type") == "image_url":
                        url = (x.get("image_url") or {}).get("url", "")
                        out.append({"type": "image_url",
                                    "image_url": {"url": _compress_data_url(url)}})
                    else:
                        out.append(x)
                m = dict(m)
                m["content"] = out
            msgs.append(m)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"messages": msgs, "tokens": self.tokens,
                           "last_usage": self.last_usage,
                           "last_usage_at": self.last_usage_at},
                          f, ensure_ascii=False)
            return True
        except Exception:
            return False

    def load_context(self, path=CONTEXT_FILE) -> int:
        """从磁盘恢复上下文、已累计 tokens 与最近一次上游 usage，返回恢复的消息条数
        （0 表示无历史）。旧格式文件没有 last_usage 字段时自动跳过（向后兼容）"""
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):   # 旧格式：直接是消息列表
                msgs = data
            elif isinstance(data, dict):
                msgs = data.get("messages") or []
                toks = data.get("tokens")
                if isinstance(toks, dict):
                    for k in _USAGE_KEYS:
                        try:
                            self.tokens[k] += int(toks.get(k, 0))
                        except Exception:
                            pass
                # 最近一次上游 usage：取较大值（重复 load 时不回退到更旧的数据）
                last = data.get("last_usage")
                if isinstance(last, dict):
                    for k in _USAGE_KEYS:
                        try:
                            self.last_usage[k] = max(
                                int(self.last_usage.get(k, 0)), int(last.get(k, 0)))
                        except Exception:
                            pass
                try:
                    self.last_usage_at = max(float(self.last_usage_at),
                                             float(data.get("last_usage_at") or 0))
                except Exception:
                    pass
            else:
                msgs = []
            if isinstance(msgs, list):
                self._messages = [m for m in msgs
                                  if isinstance(m, dict) and m.get("role") != "system"]
            return len(self._messages)
        except Exception:
            return 0

    def clear_context(self, path=CONTEXT_FILE):
        """删除持久化上下文文件（配合 /clear 使用）"""
        try:
            Path(path).unlink(missing_ok=True)
        except Exception:
            pass

    def set_context_budget(self, window=None, max_output=None, long_1m=None,
                           source=None) -> bool:
        """热更新上下文上限（窗口 / 预留输出 / 1M 模式 / 来源标记），返回窗口是否变化。

        为什么必须能热更新：引擎为了保留对话上下文（_messages）与 token 统计而长期复用，
        而「窗口多大」由设置与上游声明决定。若只在创建时读一次，用户在设置里勾了
        「开启 1M 上下文」后，既有会话仍按旧窗口算 —— 统计面板显示的仍是旧上限，
        压缩/硬裁阈值也跟着错。设置保存与按模型路由后都必须调用本方法对齐。
        参数传 None 表示"该项不变"；窗口变化会复位预警标记（阈值随窗口改变，
        旧的"已提示过"不再成立，否则新基数上永远不会预警）。"""
        changed = False
        if window is not None:
            try:
                w = int(window)
            except (TypeError, ValueError):
                w = 0
            # 噪声守卫：<4096 视为无效声明（与 agent_llm 声明解析同口径）。
            # 这里不做"钳到 4096"——那会把异常值当真，静默把上下文缩到最小。
            if w >= 4096:
                changed = w != int(self._ctx_window)
                self._ctx_window = w
        if max_output is not None:
            try:
                self._max_output = max(0, int(max_output or 0))
            except (TypeError, ValueError):
                pass
        if long_1m is not None:
            self._long_1m = bool(long_1m)
        if source is not None:
            self._window_source = str(source or ("1m" if self._long_1m else ""))
        if changed:
            self._ctx_warned = False
        return changed

    # ---------- 上下文窗口阈值（基于「可用输入预算」，长上下文模型自动放宽） ----------
    # 主流 agent 应用口径：阈值不以裸窗口为基数，而是先扣掉预留输出得到输入预算，
    # 否则长对话末期输入会把输出空间挤掉，上游直接 400。
    def _input_budget(self) -> int:
        """可用输入预算 = 窗口 − 预留输出（上游声明 max_output_tokens 优先）"""
        reserve = int(self._max_output or 0) or _DEFAULT_MAX_OUTPUT
        return max(4096, int(self._ctx_window) - reserve)

    def _compress_ratio(self) -> float:
        """压缩触发比例：1M 长上下文模式 0.80，默认 0.75"""
        return _CTX_RATIO_COMPRESS_LONG if self._long_1m else _CTX_RATIO_COMPRESS

    def _warn_limit(self) -> int:
        """预警阈值（只提示不动作）：输入预算 × 0.70"""
        return int(self._input_budget() * _CTX_RATIO_WARN)

    def _compress_limit(self) -> int:
        """摘要压缩触发阈值 = 输入预算 × 比例（1M 模式 0.80 / 默认 0.75）"""
        return int(self._input_budget() * self._compress_ratio())

    def _recent_budget(self) -> int:
        """压缩时最近窗口保留 token 预算 = 输入预算 × 比例"""
        return int(self._input_budget() * _CTX_RATIO_RECENT)

    def _hard_ceiling(self) -> int:
        """发送前硬上限：取「输入预算 × 95%」与「输入预算 − 安全余量」中更严者。
        输入预算本身已扣预留输出，两条路径都不会把输出空间挤掉（否则上游 400）。"""
        budget = self._input_budget()
        by_ratio = int(budget * _CTX_RATIO_CEILING)
        by_margin = max(8192, int(budget) - _CTX_CEILING_MARGIN)
        return max(8192, min(by_ratio, by_margin))

    def _real_occupancy(self) -> int:
        """当前上下文真实占用（输入+输出）：以最近一次上游真实用量为准。

        该值与统计面板展示口径完全一致（窗口由输入+输出共同占用），且是下一轮
        请求 prompt 的保守下界（消息只增不减，压缩/裁剪后会复位回退估算）。
        _estimate_tokens 是启发式、常偏低（漏工具 schema / 消息结构开销，中文
        tokenizer 也比 1 字/token 更密），拿它做压缩判定会触发过晚——真实输入
        已逼近/超出窗口时估算才刚过线，上游直接 400 ContextWindowExceeded。
        无上游数据（尚未请求 / 压缩后已失效）时回退本地估算。"""
        up = int(self.last_usage.get("prompt") or 0)
        if up > 0:
            return up + int(self.last_usage.get("completion") or 0)
        return self._estimate_tokens()

    def _maybe_auto_compress(self) -> int:
        """按分层阈值决定本轮是否需要压缩上下文，返回被合并的消息条数（0=未触发）。

        触发条件（唯一口径）：占用超过「可用输入预算 × 压缩比例」
        （1M 模式 0.80 / 默认 0.75），且冷却期已过。
        冷却 = 触发后至少隔 _COMPRESS_COOLDOWN 轮，避免连环压缩打爆服务商前缀缓存。

        为什么只看 token 占比、不再看消息条数：
        旧实现附带"条数 > 200 也压缩"的旁路，与 1M 长上下文开关直接冲突——工具密集的
        长任务（一轮多工具调用）很容易堆出 200+ 条消息，而 token 占用可能只有百分之十几，
        于是模型明明还有大量余量却被压缩、丢上下文（用户报的"不到 80% 就自动压缩"）。
        窗口安全本就由 token 口径兜全（_hard_ceiling 亦按 token 判定），
        消息条数不构成破窗风险，故彻底移除该旁路。"""
        if self._compress_cooldown > 0:
            self._compress_cooldown -= 1
            return 0
        # 占用判定取「本地估算」与「上游真实占用」的更大者：估算偏低（漏工具 schema/
        # 消息结构开销）时，真实占用早已逼近阈值——只信估算会压缩过晚、上游 400。
        if max(self.last_estimate, self._real_occupancy()) <= self._compress_limit():
            return 0
        n = self._auto_compress(keep_recent=60)
        if n:
            self._compress_cooldown = _COMPRESS_COOLDOWN
        return n

    def _maybe_warn_context(self, used: int) -> bool:
        """上下文接近压缩阈值时提示一次（只提示不动作）。

        去重规则：越线仅提示一次，占用回落到预警线以下（或被压缩）后复位，可再次提示——
        避免长对话里每轮都刷同一条状态。返回本轮是否提示。"""
        try:
            if used >= self._warn_limit():
                if not self._ctx_warned:
                    self._ctx_warned = True
                    budget = max(1, self._input_budget())
                    pct = int(used * 100 / budget)
                    if self.on_status:
                        self.on_status(
                            f"上下文已用 {pct}%（预算 {budget} tokens），"
                            f"达到 {int(self._compress_ratio() * 100)}% 将自动压缩")
                    return True
            else:
                self._ctx_warned = False
        except Exception:
            pass
        return False

    def _auto_compress(self, keep_recent: int = 60) -> int:
        """上下文压缩：优先让当前模型自主生成**结构化摘要**（保留关键信息与关键文件），
        失败回退启发式合并。返回被合并的消息条数（0 表示无需压缩）。

        主流 agent 应用的三个要点在此落实：
        1. keep_recent 不是简单条数：按 token 预算 _recent_budget() 从末尾动态决定保留
           多少条，保证"最近窗口"本身不会因单条超大/条数多而破窗（超窗主因）；
        2. 不切断 assistant(tool_calls)/tool 回复配对（start 前移），保留首条 system；
        3. 摘要滚动继承：上一轮摘要位于 messages[1]，必然落在被压缩区间内 → 新摘要
           在其基础上更新，而非从零重述。"""
        before = self._estimate_tokens()
        if len(self._messages) <= keep_recent + 1 and before <= self._recent_budget():
            return 0
        # 按 token 预算从末尾累计，决定保留条数（上限 keep_recent，下限 2）
        acc, n = 0, 0
        for m in reversed(self._messages):
            acc += self._msg_estimate(m)
            n += 1
            if n >= 2 and acc >= self._recent_budget():
                break
        keep = max(2, min(keep_recent, n))
        # 压缩边界不切断 assistant(tool_calls)/tool 回复配对
        start = max(len(self._messages) - keep, 1)
        while start > 1 and self._messages[start].get("role") == "tool":
            start -= 1
        head = self._messages[0]
        recent = self._messages[start:]
        old = self._messages[1:start]
        summary = self._llm_summarize(old)   # 当前模型自主摘要（失败返回空串）
        if not summary:
            summary = self._heuristic_summary(old)
        self._messages = [head]
        if summary:
            self._messages.append({"role": "user",
                                   "content": agent_llm.build_content(summary)})
        self._messages.extend(recent)
        self._invalidate_usage()   # 上下文已被压缩 → 旧的上游占用值失效，UI 回退估算
        self._ctx_warned = False   # 压缩后占用已回落，预警可再次提示
        saved = max(0, before - self._estimate_tokens())
        self._compaction.update({"count": self._compaction["count"] + 1,
                                 "last_at": time.time(),
                                 "last_merged": len(old),
                                 "last_saved": saved})
        return len(old)

    def _llm_summarize(self, old: list) -> str:
        """调用当前模型把被压缩的历史生成**结构化摘要**（失败/超时返回空串，回退启发式）。

        结构化模板对齐主流 agent 应用（Claude Code / Cline 的 handoff summary）：
        任务目标 · 已完成与结论 · 未解决与待办 · 关键文件与路径 · 用户偏好与约束 ·
        重要工具结果 · 下一步。结构化摘要能让模型在压缩后继续无缝工作，而不是"重开一轮"。
        若历史中包含上一轮摘要（滚动继承），要求其合并更新而非丢弃。"""
        try:
            texts = []
            for m in old:
                c = m.get("content")
                if isinstance(c, str) and c:
                    texts.append(f"[{m.get('role')}] {c}")
                elif isinstance(c, list):
                    parts = [x.get("text") for x in c
                             if isinstance(x, dict) and x.get("type") == "text" and x.get("text")]
                    if parts:
                        texts.append(f"[{m.get('role')}] {' '.join(parts)}")
            joined = "\n".join(texts).strip()
            if not joined:
                return ""
            if len(joined) > 40000:   # 摘要输入截断保护：只保留更近的部分
                joined = joined[-40000:]
            sys_p = agent_skills._tp(
                "prompt.condense.history_sys",
                "你是对话上下文压缩助手，负责把历史对话压缩成可供后续继续工作的结构化摘要。\n"
                "严格按以下小节输出（无内容的写“无”，不要省略小节标题）：\n"
                "## 任务目标\n## 已完成与结论\n## 未解决与待办\n"
                "## 关键文件与路径\n## 用户偏好与约束\n## 重要工具结果\n## 下一步\n"
                "要求：\n"
                "1) 只保留对后续工作有用的信息，丢弃寒暄与重复尝试；\n"
                "2) 文件路径、命令、参数、错误原因必须原样保留，不得改写或猜测；\n"
                "3) 如历史中包含上一轮摘要（形如“## 任务目标”的内容），"
                "请在其基础上合并更新，不要丢弃其中仍然有效的信息；\n"
                "4) 直接输出摘要正文，不要任何前言、解释或代码围栏。")
            # 使用 chat_stream 而非 chat：responses 协议不支持非流式请求
            res = self.llm.chat_stream(
                [{"role": "system", "content": sys_p},
                 {"role": "user", "content": agent_skills._tp(
                     "prompt.condense.history_input", "历史对话：") + "\n" + joined}],
                stop=lambda: self._stop.is_set())
            return str(res.get("text") or "").strip()
        except Exception:
            return ""

    def _condense_tool_text(self, tool_name: str, text: str) -> str:
        """读取长文本自动压缩：对内容获取型工具(read_file/web_fetch/extract_text)的超长
        返回，在进入上下文前用当前模型生成结构化摘要（概览+要点+关键片段+原文头尾备份），
        避免单条长文本独占上下文预算。写入型工具不在此列，内容完整进上下文以守卫文件
        完整性（AI 该写多大就多大，绝不压缩改写）。
        失败回退链：LLM 异常 → 启发式(开头+要点+结尾) → 原样返回。"""
        if tool_name not in _READ_CONDENSE_TOOLS:
            return text   # 写入/其他工具保持完整，不做压缩
        text = text or ""
        if len(text) <= _READ_CONDENSE_CHARS:
            return text   # 短/中文本不压缩，避免不必要干预
        src = text[:_READ_CONDENSE_INPUT]
        if len(text) > _READ_CONDENSE_INPUT:
            src += agent_skills._tp(
                "prompt.condense.truncated",
                "\n…（原始内容超长，仅取前 {n} 字符生成摘要）").format(n=_READ_CONDENSE_INPUT)
        sys_p = agent_skills._tpf(
            "prompt.condense.longtext_sys",
            "你是长文本结构化压缩助手。把用户提供的一段长文本压缩为结构化摘要，"
            "要求：1) 开头一段 2-3 句全文概览；2) 按原文结构列出要点（标题/章节/"
            "段落首句/代码中的类与函数签名，尽量保真缩略）；3) 结尾保留 1-2 处最"
            "关键的关键片段原文。使用 Markdown 组织，控制在 {budget} 字符以内。只输出摘要正文，不要前缀。",
            budget=_READ_CONDENSE_BUDGET // 2)
        try:
            res = self.llm.chat_stream(
                [{"role": "system", "content": sys_p},
                 {"role": "user", "content": agent_skills._tp(
                     "prompt.condense.longtext_input", "长文本：") + "\n" + src}],
                stop=lambda: self._stop.is_set())
            out = str(res.get("text") or "").strip()
            if out:
                head, tail = text[:_READ_CONDENSE_BUDGET // 4], text[-_READ_CONDENSE_BUDGET // 4:]
                return (f"[阅读压缩] {tool_name} 返回长文本（原文 {len(text)} 字符）的自动"
                        f"结构化摘要：\n\n{out}\n\n"
                        f"[原文头尾备份] 如需精确细节请用 {tool_name} 分段读取核对：\n"
                        f"--- 开头 ---\n{head}\n--- 结尾 ---\n{tail}")
            return _heuristic_condense(tool_name, text)
        except Exception:
            return _heuristic_condense(tool_name, text)

    @staticmethod
    def _heuristic_summary(old: list) -> str:
        """启发式摘要兜底：任务目标 + 旧消息尾部细节（仅在 LLM 摘要失败时使用）"""
        parts, first_goal = [], ""
        for m in old:
            c = m.get("content")
            texts = []
            if isinstance(c, str) and c:
                texts.append(c)
            elif isinstance(c, list):
                for x in c:
                    if isinstance(x, dict) and x.get("type") == "text" and x.get("text"):
                        texts.append(x["text"])
            joined = "\n".join(texts)
            if joined:
                if not first_goal and m.get("role") == "user":
                    first_goal = joined[:500]
                parts.append(joined)
        summary_parts = []
        if first_goal:
            summary_parts.append(f"任务目标：{first_goal}")
        tail = "\n".join(parts)
        if tail:
            summary_parts.append(tail[-1500:])
        return ("（上下文已压缩，以下是此前对话的关键信息）\n"
                + "\n".join(summary_parts)) if summary_parts else ""

    def _prune_images(self, max_keep=2):
        """历史中的截图只保留最近 max_keep 张，其余剥离 image_url 只留文本，防止上下文膨胀"""
        seen = 0
        for m in reversed(self._messages):
            c = m.get("content")
            if not isinstance(c, list):
                continue
            keep, drop = [], []
            for x in c:
                if isinstance(x, dict) and x.get("type") == "image_url":
                    if seen < max_keep:
                        seen += 1
                        keep.append(x)
                    else:
                        drop.append(x)
                else:
                    keep.append(x)
            if drop:
                # 纯图片消息被全部剥离后会成为空数组，补占位文本防上游拒绝
                m["content"] = keep if keep else \
                    [{"type": "text", "text": "（截图已忽略）"}]

    @staticmethod
    def _strip_images(messages: list):
        """纯文本模型：移除全部 image_url 内容项（历史残留/自动截图均清理）。
        消息若因此只剩图，补占位文本，避免发送空 content 被上游拒绝"""
        for m in messages:
            c = m.get("content")
            if not isinstance(c, list):
                continue
            kept = [x for x in c
                    if not (isinstance(x, dict) and x.get("type") == "image_url")]
            if not kept:
                kept = [{"type": "text", "text": "（截图已忽略）"}]
            m["content"] = kept

    def start(self, user_input: str, agent_name: str = "", images: list = None,
              skills: list = None, direct: bool = None, plugins: list = None):
        """后台线程执行一轮任务；images: 用户拖入的图片 data URL 列表；
        skills: 手动调用的技能名列表（/技能名 提示），其「说明 + 规范」注入上下文；
        plugins: 手动调用的插件名列表（/插件名 提示），其说明、SKILL.md 与调用规范同样注入；
        direct: 覆盖直接工作模式（None=沿用构造时设置）"""
        if direct is not None:
            self.direct = direct
        self._stop.clear()
        self._skills_read.clear()      # 每轮任务重置技能读取/注入状态
        self._skill_consulted.clear()
        self._thread = threading.Thread(target=self.run,
                                        args=(user_input, agent_name, images, skills, plugins),
                                        daemon=True)
        self._thread.start()

    def join(self, timeout=None):
        if self._thread:
            self._thread.join(timeout)

    # ---------- 工具 ----------
    def _all_tools(self) -> list:
        tools = list(agent_tools.tool_schemas(self.workflow))
        if self.efficient_mode:
            # 高效模式：只给上游最小必需工具集（无 MCP / 插件 / 工作流自定义 /
            # 子 Agent / 功能型工具），系统层面「禁用工具」仍然优先。
            return self._efficient_tools(tools)
        # 任务裁剪：user_input 明确命中某任务类别时，只暴露「核心 + 命中类别」工具，
        # 显著减小 schema token（首包更小）并降低模型选错工具概率；
        # 未命中任何类别则保留全部（保守，避免误裁影响能力）。
        if self._task_groups:
            keep = set(_CORE_TOOLS)
            for g in self._task_groups:
                keep |= _TASK_GROUP_TOOLS.get(g, frozenset())
            # 工作流自定义工具（tools.py 注册）随时可调：任务裁剪永不清除自定义工具，
            # 保证自定义工具在默认/任意任务类型下都具备可调用性
            try:
                keep |= agent_tools.custom_tool_names(self.workflow)
            except Exception:
                pass
            # 工作流自定义子 Agent 工具（sub_<name>）：注册后随时可调，不被任务裁剪剔除
            try:
                keep |= agent_subagent.subagent_tool_names(self.workflow)
            except Exception:
                pass
            # 命中技能时保留其覆盖的底层工具（技能路由硬拦截依赖这些工具可用）
            if self._auto_skills:
                names = [t["function"]["name"] for t in tools]
                for tool, skills in agent_skills.skills_covering_tools(names).items():
                    if any(s in self._auto_skills for s in skills):
                        keep.add(tool)
            tools = [t for t in tools if t["function"]["name"] in keep]
        if self.auto_vd:
            # 自动虚拟桌面接管时，不再暴露 virtual_desktop 工具（避免 AI 重复切桌面）
            tools = [t for t in tools if t["function"]["name"] != "virtual_desktop"]
        if not self.memory_enabled:
            # 记忆关闭：不暴露 save_memory/load_memory
            tools = [t for t in tools
                     if t["function"]["name"] not in ("save_memory", "load_memory")]
        if not self.allow_subagents:
            # 简单/中等任务：不暴露子 Agent 与跨工作流派发工具（执行层在 _execute 再兜底），
            # 主 Agent 必须自己用 read_file/write_file/edit_file/run_command 直接完成。
            tools = [t for t in tools
                     if not _is_subagent_tool(t["function"]["name"])]
        if self.mcp:
            # MCP 工具并入：与内置工具/其他服务器同名时跳过（内置优先），
            # 否则同名工具会让上游报 "Tool names must be unique"。
            # 工作流隔离：仅暴露当前工作流允许的服务器工具（未绑定=全局，绑定=该工作流；
            # 空集合表示无允许服务器 → 不暴露任何 MCP 工具）。
            seen = {t["function"]["name"] for t in tools}
            try:
                allowed = agent_workflow.allowed_mcp_servers(self.workflow)
            except Exception:
                allowed = set()
            for t in self.mcp.tool_schemas_for(allowed):
                name = t["function"]["name"]
                if name and name not in seen:
                    tools.append(t)
                    seen.add(name)
        # 系统层面禁用工具（设置页「工具管控」）：从 schema 剔除，AI 无法选择调用。
        # 禁用全部工具 → 空工具集（纯对话模式）；能力开关关闭的 MCP/插件工具也在
        # _execute 执行层再兜底一次，提示词干预（幻觉/诱导调用）无法绕过。
        if getattr(self, "_disabled_all", False):
            tools = []
        elif getattr(self, "_disabled_tools", None):
            tools = [t for t in tools
                     if t["function"]["name"].lower() not in self._disabled_tools]
        return tools

    def _efficient_tools(self, tools: list) -> list:
        """高效模式工具集：仅保留 _EFFICIENT_TOOLS 白名单内的内置工具。

        与能力开关/系统管控的关系：系统层面「禁用全部工具 / 禁用指定工具」优先级更高
        （用户显式禁用必须生效），故此处先按白名单过滤，再套用同一套禁用规则。
        """
        out = [t for t in tools if t["function"]["name"] in _EFFICIENT_TOOLS]
        if getattr(self, "_disabled_all", False):
            return []
        if getattr(self, "_disabled_tools", None):
            out = [t for t in out
                   if t["function"]["name"].lower() not in self._disabled_tools]
        return out

    def plugin_of_tool(self, name: str) -> str:
        """该工具是否由插件提供 → 归属插件名（内置工具/普通 MCP 工具返回空串）。

        插件登记的 MCP 服务器名为「{插件名}-mcp」，而工具名由插件自己决定，静态映射不了，
        只能走运行期的「工具 → 服务器 → 插件」链路。UI 据此给该行换插件专属矢量图标。
        """
        if not self.mcp:
            return ""
        try:
            from zhuzhu_Copilot.core import agent_plugins
            return agent_plugins.plugin_of_server(self.mcp.server_for_tool(name))
        except Exception:
            return ""

    def _with_edit_bucket(self, fn):
        """把本轮文件变更桶绑定到工具执行 worker 线程（_call_with_stop 每次派生新线程，
        线程局部不穿透，须在 worker 入口重新绑定同一桶，写入才能累计到引擎快照）。"""
        def _wrapped():
            agent_tools.bind_edit_bucket(self._edit_bucket)
            return fn()
        return _wrapped

    def reload_extensions(self) -> str:
        """热加载刚生成/导入的扩展（技能 / MCP / 插件），返回可读摘要（回填进工具结果文案）。

        为什么必须显式刷新：create_skill / create_plugin / create_mcp 只把文件与配置落到磁盘，
        而**运行中的引擎**持有三份快照 —— agent_skills 的技能缓存、agent_plugins 的插件索引、
        `self.mcp` 的已连接服务器与工具表。不刷新的话，模型在同一次任务里刚生成的工具要等
        下一个任务（甚至重启）才可调用，表现为「创建成功却调不动，只能干等」。

        刷新范围与顺序：
          1) 技能缓存 + 插件索引失效 → 后续读取即包含新建的技能/插件；
          2) 按本任务输入**重新匹配技能**并并入本任务技能集 → 新技能在本轮就能被注入并照做；
          3) MCP **增量**重连（只处理新增/变更/移除的服务器）→ 新工具出现在下一轮工具列表。
        系统提示与工具列表都是每轮重新生成的（见 _system_prompt / _all_tools），故下一轮即生效。
        """
        notes: list = []
        # 1) 缓存失效：技能库与插件索引
        try:
            agent_skills.invalidate_skills_cache()
        except Exception:
            pass
        try:
            from zhuzhu_Copilot.core import agent_plugins
            agent_plugins.invalidate_index()
        except Exception:
            pass
        # 2) 新技能并入本任务：与任务开始时的快照做差集，把「刚生成/刚导入」的技能直接纳入
        #    —— 不走关键词匹配：_SKILL_KEYWORDS 是人工维护的静态表，新建技能的触发词不在其中，
        #    靠匹配会永远命中不了（表现就是「技能建好了却在本任务里用不上」）。
        #    `_sync_skill_msg` 每轮重建（见轮循环），故加入后下一轮即注入其 instruction。
        try:
            now = {s.get("name") for s in (agent_skills.load_skills(self.workflow) or [])
                   if s.get("name")}
            fresh = sorted(n for n in now if n not in self._skills_at_start)
            if fresh:
                self._skills_at_start = now
                for n in fresh:
                    self._task_skills.add(n)
                    if n not in (self._auto_skills or []):
                        self._auto_skills = list(self._auto_skills or []) + [n]
                notes.append(f"技能 {'、'.join(fresh)}")
        except Exception:
            pass
        # 3) MCP 增量重连：只重建新增/变更的服务器，未变的连接保持不动
        if self.mcp:
            try:
                tools = self.mcp.reload(agent_skills.load_mcp_servers())
                act = getattr(self.mcp, "last_reload", {}) or {}
                parts = []
                for key, label in (("added", "新增"), ("updated", "更新"), ("removed", "移除")):
                    names = act.get(key) or []
                    if names:
                        parts.append(f"{label} {len(names)} 个（{', '.join(names)}）")
                if parts:
                    notes.append("MCP 服务器 " + "；".join(parts))
                errs = getattr(self.mcp, "errors", None) or []
                notes.append(f"MCP 工具共 {len(tools)} 个"
                             + (f"，失败: {'；'.join(errs)}" if errs else ""))
            except Exception as e:
                notes.append(f"MCP 重连失败: {e}")
        return "；".join(notes)

    def _exec_tool(self, name: str, args: dict, allow_dangerous: bool) -> dict:
        """在工具执行 worker 线程内落地本会话作用域后再执行内置工具。

        工具在 worker 线程执行（并发线程池 + _call_with_stop 派生线程），线程局部作用域
        不会继承 → 任务清单、共同上下文空间等「会话级数据」必须在 worker 入口重新落地，
        否则后台并发会话会读写到别的会话的数据。"""
        from zhuzhu_Copilot.core import agent_context
        prev = agent_context.set_conversation(self.conversation)
        try:
            return agent_tools.execute_tool(name, args, allow_dangerous=allow_dangerous,
                                            status_cb=self.on_status,
                                            workflow=self.workflow)
        finally:
            agent_context.set_conversation(prev)

    def _run_one_tool(self, name: str, args: dict, allow_dangerous: bool,
                      is_write: bool, lock) -> dict:
        """并发执行单个工具调用（并发线程池的 worker 入口）。

        写类工具先按「解析后的绝对路径」加锁：同一文件串行、不同文件真正并行，
        避免并发写坏同一文件；只读类无副作用，直接执行。
        两者都在 worker 入口重新绑定本轮文件变更桶（线程局部不穿透，见 _with_edit_bucket）。
        """
        def _call():
            agent_tools.bind_edit_bucket(self._edit_bucket)
            return self._execute(name, args, allow_dangerous=allow_dangerous)
        if not is_write:
            return _call()
        key = ""
        try:
            key = str(agent_tools._resolve(str((args or {}).get("path") or "").strip()))
        except Exception:
            key = ""
        with lock(key):
            return _call()

    def _should_run_in_background(self, name: str, args: dict) -> bool:
        """判断本次工具调用是否应后台执行：
        1. 显式传了 run_in_background=true → 任何 background_capable 工具均后台；
        2. 复杂任务自动后台模式下，仅 run_command 自动后台（子Agent派发工具内部
           已是线程池并发，不需要再套后台层；前台同步派发不显示在底部后台任务栏）。
        """
        args = args or {}
        raw = args.get("run_in_background")
        # 健壮布尔解析：LLM 可能传 True/"true"/"True"/1/"1"，也可能传 "false"/0/None
        if isinstance(raw, bool):
            explicit = raw
        elif isinstance(raw, (int, float)):
            explicit = raw != 0
        elif isinstance(raw, str):
            explicit = raw.strip().lower() in ("true", "1", "yes", "y", "on")
        else:
            explicit = False
        if explicit:
            return True
        # 复杂任务自动后台：仅对 run_command 生效，子Agent派发保持前台并发
        if getattr(self, "_auto_background", False) and name == "run_command":
            return True
        return False

    def _execute(self, name: str, args: dict, allow_dangerous: bool = False) -> dict:
        """执行内置或 MCP 工具，返回 {"text", "images"}"""
        # 系统层面（sandbox 级）硬拦截：禁用工具/禁用全部 —— schema 已不外露，
        # 此处兜底拦截提示词干预（模型幻觉/被诱导直接调用禁用工具名）的情况
        if getattr(self, "_disabled_all", False) or \
                name.lower() in (getattr(self, "_disabled_tools", None) or frozenset()):
            return {"text": f"[工具已禁用] 「{name}」已被你在设置中禁用，本次未执行。"
                            "如需使用请到 AI 设置页（工具管控）重新启用。", "images": []}
        if not self.memory_enabled and name in ("save_memory", "load_memory"):
            # 记忆关闭（系统层面）：schema 已不外露记忆工具，此处兜底硬拦
            return {"text": "[记忆已关闭] 记忆功能已在设置中禁用，本次未执行。", "images": []}
        if not self.allow_subagents and _is_subagent_tool(name):
            # 子 Agent / 跨工作流派发的执行层硬拦截：schema 已剔除，此处兜底提示词干预
            # （模型幻觉或被诱导直接写出工具名）。本任务判定为简单/中等复杂度 →
            # 要求主 Agent 自己动手，不得把工作转派出去。
            return {"text": f"[子 Agent 已禁用] 本任务被判定为简单/中等复杂度，"
                            f"不允许派发子 Agent 或调用其他工作流主 Agent，"
                            f"「{name}」本次未执行。请自己用 read_file / write_file / "
                            f"edit_file / run_command 等工具直接完成任务。", "images": []}
        if name == "tts_speak":
            # AI 主动调用朗读工具：停掉引擎的自动分段朗读，避免与 tts_speak 双重播放
            self._tts_stop_read()
        if self.auto_vd and name == "virtual_desktop":
            # 自动虚拟桌面接管时，禁止 AI 手动切换桌面（防止重复 new/back 打乱静默流程）
            return {"text": "[自动模式] 系统已在独立虚拟桌面执行本任务，结束后自动返回主桌面，无需手动切换", "images": []}
        if name == "ask_user":
            # 提问工具：不经沙盒/确认，直接向用户提问。
            # YOLO（direct）模式同样允许提问：直行指的是"不设操作约束"，不是"不许澄清需求"；
            # 关键信息缺失（目标文件/预期结果不明）时提问比盲猜更符合用户利益。
            if self.ask_user:
                q = str(args.get("question") or "").strip()
                # 同一问题已在本次会话问过并收到回答：直接复用，不再弹窗打扰。
                # 修复「ask_user 重复询问相同问题（出现两次）」：模型在同一批次、
                # 重试重跑、长任务偶发空响应失败后手动重试等场景再次发出相同提问时，
                # 不再二次弹出问题框，保证每个问题只询问一次。
                if q:
                    prev = self._ask_answered.get(q)
                    if prev is not None:
                        return {"text": f"[该问题此前已询问过并已回答：{prev}]\n"
                                        "请直接使用该回答继续执行任务，不要重复提问。",
                                "images": []}
                ans = self.ask_user(args)
                if q:
                    self._ask_answered[q] = ans
                return {"text": ans, "images": []}
            return {"text": "[ask_user] 未接入提问面板", "images": []}
        if name == "read_file":
            # 命中技能 SKILL.md 即视为已读取该技能规范流程，放行其覆盖的工具
            mp = re.search(r"skills[\\/]([^\\/]+?)[\\/]SKILL\.md$",
                           str(args.get("path") or args.get("file") or ""))
            if mp:
                self._skills_read.add(mp.group(1))
        if name in agent_tools.SUB_AGENT_TOOLS or \
                agent_subagent.subagent_tool(name, self.workflow) is not None:
            # 后台执行：立即返回任务ID，不阻塞
            if self._should_run_in_background(name, args):
                return self._exec_subagent_background(name, args)
            # 子 Agent 工具：并发派发子任务（可读写项目文件）；不做轮数与时间上限，
            # 长任务持续到完成或被用户停止（stop），与主 Agent 无轮数上限一致。
            # 含工作流自定义子 Agent（sub_<name>，注册进现有工作流）。
            try:
                res = _call_with_stop(self._with_edit_bucket(
                    lambda: self._run_subagent_tool(name, args)),
                    self._stop, timeout=None)
                if res is None:   # stop 触发已放弃等待（子 Agent 仍在后台执行）
                    return {"text": "[已停止等待] 子 Agent 仍在后台执行，本轮已跳过", "images": []}
                return res
            except Exception as e:
                return {"text": f"[子Agent错误] {name}: {e}", "images": []}
        if name in self._builtin_names:
            # 后台命令执行
            if name == "run_command" and self._should_run_in_background(name, args):
                return self._exec_command_background(args)
            # 内置工具（run_command 等）同样可能长时间阻塞 → 用带超时/可中断封装
            try:
                timeout = _tool_wait_timeout(name)
                res = _call_with_stop(
                    self._with_edit_bucket(
                        lambda: self._exec_tool(name, args, allow_dangerous)),
                    self._stop, timeout=timeout)
                if res is None:   # stop 触发已放弃等待（工具仍在后台线程执行）
                    if name == "fast_download":
                        agent_tools.cancel_active_download()   # 停止即取消后台下载
                    return {"text": "[已停止等待] 工具仍在后台执行，本轮已跳过", "images": []}
                # Cordis：工作流切换工具返回重建标记 → 通知 UI 立即重建引擎（热插拔）
                if isinstance(res, dict) and res.get("__rebuild_engine__"):
                    res = {k: v for k, v in res.items() if k != "__rebuild_engine__"}
                    if self.on_engine_rebuild:
                        self.on_engine_rebuild(None)
                # UI/UX 包切换：返回重建标记 → 通知 UI 重建面板（热插拔）
                if isinstance(res, dict) and res.get("rebuild_uiux"):
                    res = {k: v for k, v in res.items() if k != "rebuild_uiux"}
                    if self.on_uiux_rebuild:
                        self.on_uiux_rebuild()
                # 按钮注册：返回待应用载荷 → 通知 UI 在主线程注册/注销按钮
                if isinstance(res, dict) and "btn_reg_pending" in res:
                    payload = res.get("btn_reg_pending")
                    res = {k: v for k, v in res.items() if k != "btn_reg_pending"}
                    if self.on_btn_reg and payload is not None:
                        self.on_btn_reg(payload)
                # 会话命名：set_session_name 工具返回待应用标题 → 通知 UI 主线程改名
                if isinstance(res, dict) and "__session_name__" in res:
                    title = res.get("__session_name__")
                    res = {k: v for k, v in res.items() if k != "__session_name__"}
                    if self.on_session_name and title:
                        self.on_session_name(str(title))
                # 对话内临时切换工作流 agent → 更新当前会话工作流并重建引擎
                if isinstance(res, dict) and "__use_workflow__" in res:
                    wf_name = res.get("__use_workflow__", "")
                    res = {k: v for k, v in res.items() if k != "__use_workflow__"}
                    # 规范化比较：空字符串表示切回默认，self.workflow 可能为 None
                    current = self.workflow or ""
                    target = wf_name if wf_name else agent_workflow.DEFAULT_WORKFLOW
                    if target != (current or agent_workflow.DEFAULT_WORKFLOW):
                        self.workflow = wf_name or None
                        if self.on_engine_rebuild:
                            self.on_engine_rebuild(self.workflow or "")
                # 扩展热加载：create_skill / create_plugin / create_mcp 等只把文件与配置写盘，
                # 运行中的引擎还持有技能缓存与 MCP 连接两处快照 —— 不刷新的话模型在同一次任务里
                # 刚生成的工具调不动（要等下一个任务甚至重启）。刷新后摘要回填进工具结果，
                # 让模型知道「现在就能用」以及新增了哪些工具。
                if isinstance(res, dict) and res.get("reload_extensions"):
                    res = {k: v for k, v in res.items() if k != "reload_extensions"}
                    with self._ext_reload_lock:
                        summary = self.reload_extensions()
                    if summary:
                        res["text"] = (res.get("text") or "") + \
                            f"\n✅ 已热加载扩展（本次任务后续轮次可直接调用）：{summary}"
                return res
            except TimeoutError:
                return {"text": f"[工具超时] {name} 无响应，已放弃（{timeout:.0f} 秒）", "images": []}
            except Exception as e:
                return {"text": f"[工具错误] {name}: {e}", "images": []}
        if self.mcp:
            # MCP 调用无超时可能卡死 → 用带超时/可中断封装。
            # 工作流隔离兜底：即便工具名被模型猜到，非允许服务器的调用也拒绝。
            try:
                srv = self.mcp.server_for_tool(name)
                if srv:
                    # 能力开关执行层兜底：MCP / 插件能力关闭时，即使 schema 未暴露
                    # 也硬拦模型幻觉/被诱导直接调用 MCP 工具（提示词干预无法绕过）
                    if not getattr(self, "_cap_mcp", True):
                        return {"text": f"[能力已关闭] MCP 服务器「{srv}」已被禁用"
                                        "（设置-能力开关），本次未执行。", "images": []}
                    if not getattr(self, "_cap_plugin", True) and \
                            srv in (getattr(self, "_plugin_mcp_names", None) or frozenset()):
                        return {"text": f"[能力已关闭] 插件提供的 MCP 服务器「{srv}」"
                                        "已被禁用（设置-能力开关），本次未执行。", "images": []}
                    allowed = agent_workflow.allowed_mcp_servers(self.workflow)
                    if srv not in allowed:
                        return {"text": f"[工作流隔离] MCP 服务器「{srv}」未绑定当前工作流，已拒绝调用。"
                                        "可在设置-MCP服务器 中为该服务器指定工作流。", "images": []}
                text, imgs = _call_with_stop(self._with_edit_bucket(
                    lambda: self.mcp.call_tool(name, args)),
                    self._stop, timeout=30.0)
                if imgs and self.text_only:
                    # 纯文本模型：图片内容项会被接口拒绝，丢弃图片并提示改用文本感知
                    imgs = []
                    text += "\n（当前模型不支持图片输入，截图已省略）"
                return {"text": text, "images": imgs}
            except TimeoutError:
                return {"text": f"[MCP 超时] 工具 {name} 无响应，已放弃（30 秒）", "images": []}
            except Exception as e:
                return {"text": f"[MCP 错误] {e}", "images": []}
        return {"text": f"[未知工具] {name}", "images": []}

    # ---------- 后台执行（命令 / 子 Agent） ----------
    def _exec_command_background(self, args: dict) -> dict:
        """后台执行命令：提交到 BackgroundTaskManager，立即返回任务ID。
        命令实际执行复用 agent_tools._run_command，但在后台线程中运行，
        输出通过 task.append_output 流式累积，并触发 on_status 回调通知UI。"""
        from zhuzhu_Copilot.core import agent_background as _ab
        cmd = str((args or {}).get("command") or "").strip()
        title = cmd[:60] + ("..." if len(cmd) > 60 else "")
        wait = int((args or {}).get("wait") or 5)
        force_quit = bool((args or {}).get("force_quit"))
        cwd = str((args or {}).get("cwd") or "")
        stdin_text = str((args or {}).get("stdin") or "")
        max_output = (args or {}).get("max_output")

        def _fn(task):
            # 在后台线程中执行命令，wait 设为较大值让它持续运行直到完成
            res = agent_tools._run_command(
                command=cmd, wait=max(wait, 300), force_quit=False,
                cwd=cwd, stdin_text=stdin_text,
                max_output=max_output,
                status_cb=lambda s: task.append_output(s + "\n"))
            text = res.get("text", "") if isinstance(res, dict) else str(res)
            task.append_output(text)
            return res

        try:
            task = _ab.bg_manager().submit(
                task_type=_ab.TYPE_COMMAND,
                title=_uif("命令: {cmd}", cmd=title),
                description=cmd,
                fn=_fn,
                on_start=lambda t: self._bg_notify_start(t),
                on_output=lambda t, delta: self._bg_notify_output(t, delta),
                on_done=lambda t: self._bg_notify_done(t),
                on_failed=lambda t, err: self._bg_notify_failed(t, err),
                metadata={"command": cmd, "wait": wait, "force_quit": force_quit},
            )
        except Exception as e:
            return {"text": _uif("[后台执行失败] 提交后台命令时出错: {err}\n请检查命令参数，或改为前台同步执行。", err=str(e)), "images": []}
        self._bg_tasks_this_turn.append(task.task_id)
        return {"text": _uif("[已后台执行] 命令已转入后台（任务ID: {tid}），不阻塞当前对话。可用 background_task_status(task_id=\"{tid}\") 查询进度，或 cancel_background_task(task_id=\"{tid}\") 取消。\n命令: {cmd}",
                              tid=task.task_id, cmd=cmd[:100]), "images": []}

    def _exec_subagent_background(self, name: str, args: dict) -> dict:
        """后台派发子Agent：注册式/临时/跨工作流均支持。
        复用 _run_subagent_tool 的任务构建逻辑，但在后台线程中执行 _dispatch_tasks，
        输出流式回传，完成后汇总。"""
        from zhuzhu_Copilot.core import agent_background as _ab
        task_title = _ui("后台子Agent派发")
        if name == "explore_project":
            task_title = _uif("探索: {dir}", dir=str((args or {}).get('directory', ''))[:40])
        elif name == "search_large":
            task_title = _uif("搜索: {q}", q=str((args or {}).get('query', ''))[:40])
        elif name.startswith("sub_"):
            # 注册式子 agent：用注册名作为标题
            reg_name = name[4:]
            goal = str((args or {}).get("goal") or (args or {}).get("prompt") or "")
            task_title = _uif("子Agent: {name}", name=reg_name) + (f" - {goal[:30]}" if goal else "")
        else:
            # dispatch_sub_agents 等批量派发：安全计算任务数
            tasks_raw = (args or {}).get("tasks")
            if isinstance(tasks_raw, list):
                n_tasks = len(tasks_raw)
            elif isinstance(tasks_raw, str):
                # LLM 误传 JSON 字符串：尝试解析，失败则标记为1个
                try:
                    import json as _json
                    parsed = _json.loads(tasks_raw)
                    n_tasks = len(parsed) if isinstance(parsed, list) else 1
                except Exception:
                    n_tasks = 1
            else:
                n_tasks = 1
            # 显示上限：超过50个显示 "50+"，避免离谱数字
            display_n = f"{n_tasks}+" if n_tasks > 50 else str(n_tasks)
            task_title = _uif("派发 {n} 个子Agent", n=display_n)

        # 在主线程解析当前工作流名（self.workflow 为 None 时用 active_workflow()，
        # 必须在主线程解析，因为 active_workflow() 依赖线程局部状态）。
        from zhuzhu_Copilot.core import agent_workflow as _awf
        _wf_name = (self.workflow if isinstance(self.workflow, str) and self.workflow.strip()
                    else _awf.active_workflow())

        def _fn(task):
            # 后台线程中执行子Agent派发（同步等待全部完成）
            # 必须在后台线程重新绑定：1) 编辑桶（子Agent写文件依赖）
            # 2) conversation 作用域（共同上下文空间按对话隔离）；
            # 3) 线程局部工作流（注册子Agent查找依赖 _WF_LOCAL，后台线程默认为空）。
            # 注意：不修改 self.on_sub_event（多后台任务并发时会竞态），而是创建
            # 独立 wrapper 作为参数传给 _run_subagent_tool，每个任务各自捕获输出。
            import time as _time
            from zhuzhu_Copilot.core import agent_context
            agent_tools.bind_edit_bucket(self._edit_bucket)
            prev_conv = agent_context.thread_local_conversation()
            agent_context.set_conversation(self.conversation)
            _awf.set_current_workflow(_wf_name)
            _orig_sub = self.on_sub_event  # 仅读取引用，不修改

            # —— 输出缓冲：按子agent标题分组，批量flush ——
            _buf = []                 # 累积的文本片段
            _titles_seen = set()     # 已输出过标题头的子agent
            _last_flush = [_time.time()]
            _FLUSH_INTERVAL = 0.4    # 最多每400ms flush一次

            def _flush(force=False):
                if not _buf:
                    return
                now = _time.time()
                if not force and (now - _last_flush[0]) < _FLUSH_INTERVAL:
                    return
                chunk = "".join(_buf)
                _buf.clear()
                _last_flush[0] = now
                task.append_output(chunk)

            def _bg_sub_wrapper(kind, idx, title, text, aid):
                # 捕获所有子agent事件：delta(流式文本)、tool(工具调用)、output(工具输出)
                # 之前只捕获 delta，导致以工具调用为主的子agent(explore_project/search_large等)
                # 完全没有输出可见。
                if not text and kind != "start":
                    pass  # 空文本不写入，但仍透传给原始回调
                elif title and title not in _titles_seen:
                    _titles_seen.add(title)
                    _buf.append(f"\n### {title}\n\n")
                if kind == "delta" and text:
                    _buf.append(text)
                    _flush()
                elif kind == "tool" and text:
                    _buf.append(f"\n> 调用工具: {text}\n")
                    _flush()
                elif kind == "output" and text:
                    _buf.append(f"\n```\n{text}\n```\n")
                    _flush()
                if _orig_sub:
                    try:
                        _orig_sub(kind, idx, title, text, aid)
                    except Exception:
                        pass  # UI回调异常不影响后台任务执行

            try:
                # wrapper 作为参数传入，不修改 self.on_sub_event → 无竞态
                res = self._run_subagent_tool(name, args, on_sub_event=_bg_sub_wrapper)
                _flush(force=True)  # 收尾：flush剩余缓冲
                text = res.get("text", "") if isinstance(res, dict) else str(res)
                # 最终汇总追加到输出末尾（不覆盖已累积的流式输出），
                # 并标记分隔线，方便区分流式过程与最终结论
                if text:
                    task.append_output(f"\n\n---\n**最终结果:**\n\n{text}")
                return res
            finally:
                agent_context.set_conversation(prev_conv or "")
                _awf.set_current_workflow("")

        try:
            task = _ab.bg_manager().submit(
                task_type=_ab.TYPE_SUBAGENT if name != "dispatch_sub_agents" else _ab.TYPE_BATCH,
                title=task_title,
                description=f"后台派发: {name}",
                fn=_fn,
                on_start=lambda t: self._bg_notify_start(t),
                on_output=lambda t, delta: self._bg_notify_output(t, delta),
                on_done=lambda t: self._bg_notify_done(t),
                on_failed=lambda t, err: self._bg_notify_failed(t, err),
                metadata={"tool": name, "args_count": len(str(args or {}))},
            )
        except Exception as e:
            return {"text": _uif("[后台派发失败] 提交后台任务时出错: {err}\n请检查参数是否正确，或改为前台同步执行。", err=str(e)), "images": []}
        self._bg_tasks_this_turn.append(task.task_id)
        return {"text": _uif("[已后台派发] 子Agent已转入后台并发执行（任务ID: {tid}），不阻塞当前对话。各子Agent输出流式回传，完成后自动汇总。\n可用 background_task_status(task_id=\"{tid}\") 查询进度。",
                              tid=task.task_id),
                "images": []}

    def _bg_notify_start(self, task):
        """后台任务启动：通知UI底部面板更新状态。"""
        try:
            if self.on_status:
                self.on_status(f"后台任务已启动「{task.title}」(ID: {task.task_id})，"
                               f"你可以继续发送其他消息")
        except Exception:
            pass  # UI回调异常不影响任务执行

    def _bg_notify_output(self, task, delta):
        """后台任务输出增量：通过 on_result 推送到UI（不与主对话流式输出冲突，
        因为标记为后台任务输出）。"""
        try:
            if self.on_result and delta:
                # 用特殊前缀标记后台输出，UI可区分展示
                # 注意：on_result 实际签名为 (name, text, images) 三参数，与主循环一致
                self.on_result(f"[bg:{task.task_id}]", delta, [])
        except Exception:
            pass  # UI回调异常不影响任务执行

    def _bg_notify_done(self, task):
        """后台任务完成：通知UI底部面板更新状态。"""
        try:
            if self.on_status:
                self.on_status(f"后台任务完成「{task.title}」(ID: {task.task_id})，"
                               f"耗时 {task.duration:.1f}s")
        except Exception:
            pass

    def _bg_notify_failed(self, task, error):
        """后台任务失败：通知UI底部面板更新状态。"""
        try:
            if self.on_status:
                err_text = str(error or "")[:100]
                self.on_status(f"后台任务失败「{task.title}」(ID: {task.task_id})，错误: {err_text}")
        except Exception:
            pass

    # ---------- 子 Agent 工具（explorer / 搜索 / 通用并发分发） ----------
    def _run_subagent_tool(self, name: str, args: dict, on_sub_event=None) -> dict:
        """子 Agent 工具执行：复用同一 LLM 客户端，派发子任务（可读写项目文件）并汇总结果。
        dispatch_sub_agents 的任务项支持：
          - agent="<工作流名>"：把该子任务派发到「其他工作流的主 Agent」执行（目标工作流
            agent.py 人格 + tools.py 工具集 + llm.py 客户端），由主 Agent 引领/监督并汇总；
          - subagents={<名>: {...}}：为自定义子 Agent（sub_<名>）按任务覆盖
            description/goal/allowed/persona，未覆盖部分沿用注册配置。
        on_sub_event：可选回调覆盖（后台任务用，避免修改 self.on_sub_event 引发多线程竞态）。"""
        args = args or {}
        # 主 Agent 决策的共同上下文空间：顶层 shared_context/space 为整批默认
        # （共享全开：缺省开启，显式传 false 可关闭整批），任务项可覆盖
        batch_shared = bool(args.get("shared_context", True))
        batch_space = str(args.get("space") or "").strip()
        # 主 Agent 直接传给内置子 Agent（explore_project/search_large）的上下文：子 Agent
        # 上下文独立于主对话，带上可免其重复读取/搜索（与 dispatch_sub_agents 的 context 同义）
        single_ctx = str(args.get("context") or "").strip()
        tasks = []
        if name == "explore_project":
            tasks = [{"title": "探索项目",
                      "goal": agent_subagent.explore_goal(str(args.get("directory", ""))),
                      "allowed": ("list_directory", "read_file", "search_files"),
                      "context": single_ctx,
                      "shared_context": batch_shared, "space": batch_space}]
        elif name == "search_large":
            dirs = [str(d) for d in (args.get("directories") or []) if str(d).strip()]
            tasks = [{"title": f"搜索「{args.get('query', '')}」",
                      "goal": agent_subagent.search_goal(
                          str(args.get("query", "")), dirs,
                          self._to_int(args.get("max_results"), 20)),
                      "allowed": ("grep", "search_files", "read_file", "list_directory"),
                      "context": single_ctx,
                      "shared_context": batch_shared, "space": batch_space}]
        else:   # dispatch_sub_agents
            # 任务级子 Agent 覆盖：{名: {description/goal/allowed}}，执行时按名合并
            overrides = (args.get("subagents") or {})
            overrides = overrides if isinstance(overrides, dict) else {}
            # 任务级主 Agent 派发：{工作流名: 目标} 或 [工作流名]，agent 键缺省目标说明
            wf_agents = (args.get("agents") or {})
            wf_agents = wf_agents if isinstance(wf_agents, dict) else {}
            for i, t in enumerate((args.get("tasks") or []), 1):
                if not isinstance(t, dict) or not str(t.get("goal") or "").strip():
                    continue
                goal = str(t["goal"])
                awf = str(t.get("agent") or "").strip()
                if not awf and isinstance(t.get("agents"), dict):
                    awf = str((t["agents"] or {}).get("wf") or "").strip()
                if not awf and isinstance(t.get("agents"), list):
                    _al = [str(x) for x in t["agents"] if str(x).strip()]
                    awf = _al[0] if _al else ""
                if awf:
                    # 跨工作流主 Agent 派发：由目标工作流主 Agent 执行并汇报
                    if not agent_workflow.is_workflow(awf):
                        tasks.append({
                            "title": str(t.get("title") or f"子任务 {i}"),
                            "goal": f"[派发失败] 工作流「{awf}」不存在或已禁用，"
                                    f"请先用 list_workflow_agents 确认可用 agent 后重新派发。"
                                    f"原目标：{goal[:200]}",
                            "agent_failed": awf})
                    else:
                        _aname = agent_workflow._agent_name(awf)
                        tasks.append({
                            "title": str(t.get("title") or f"派发 Agent「{_aname}」"),
                            "goal": goal,
                            "agent": awf,
                            "context": str(t.get("context") or "").strip(),
                            "shared_context": bool(t.get("shared_context", batch_shared)),
                            "space": str(t.get("space") or batch_space or "").strip(),
                            "async": bool(args.get("async", True)),
                        })
                    continue
                tasks.append({
                    "title": str(t.get("title") or f"子任务 {i}"),
                    "goal": goal,
                    "allowed": self._sub_allowed(str(t.get("tools") or "")),
                    "sub": str(t.get("sub") or "").strip() or None,
                    "overrides": overrides,
                    "context": str(t.get("context") or "").strip(),
                    "persona": str(t.get("persona") or "").strip() or None,
                    "shared_context": bool(t.get("shared_context", batch_shared)),
                    "space": str(t.get("space") or batch_space or "").strip(),
                })
        # 工作流自定义子 Agent（sub_<name> 或直接注册名）：运行单个注册子 Agent。
        # 本次调用可覆盖 goal/context/shared_context（主 Agent 逐次决策，缺省用注册配置）。
        custom = agent_subagent.subagent_tool(name, self.workflow)
        if custom is not None:
            sc = args.get("shared_context")
            if sc is None:
                sc = custom.get("shared_context")   # 注册默认值；None 表示由主 Agent 决策（默认关）
            tasks = [{"title": f"子Agent「{custom['name']}」",
                      "goal": str(args.get("goal") or "").strip() or custom["goal"],
                      "allowed": tuple(custom.get("allowed") or []) or None,
                      "persona": custom.get("persona") or "",
                      "context": str(args.get("context") or "").strip(),
                      "shared_context": bool(sc),
                      "space": str(args.get("space") or batch_space or "").strip(),
                      "custom": True}]
        if not tasks:
            return {"text": f"[{name}] 缺少任务参数，无法派发子 Agent", "images": []}
        # 派发：含跨工作流主 Agent 任务（agent 键）与普通子任务；统一由引擎侧
        # 循环执行，沿用 on_sub_event 子块展示，让主 Agent 引领/监督全部编队成员。
        n_agent = sum(1 for t in tasks if t.get("agent") or t.get("agent_failed"))
        if self.on_status:
            if n_agent:
                self.on_status(f"正在派发 {len(tasks)} 个子任务"
                               f"（含 {n_agent} 个跨工作流主 Agent）并发执行…")
            else:
                self.on_status(f"正在派发 {len(tasks)} 个子 Agent 并发执行…")
        text = self._dispatch_tasks(tasks, on_sub_event=on_sub_event)
        return {"text": text, "images": []}

    def _dispatch_tasks(self, tasks: list, on_sub_event=None) -> str:
        """把任务清单派发执行并汇总（按任务原始顺序输出）。
        on_sub_event：可选回调覆盖（后台任务用，避免修改 self.on_sub_event 引发竞态）。

        并发策略（子 Agent 可重复派发同名 / 多点并行处理不同文件）：
        - 普通子任务与自定义子 Agent（sub=）任务合并为「一批」经 dispatch_sub_agents
          线程池并发执行（可同时创建/写入/编辑/搜索/查找多个文件，互不等待）；
        - 跨工作流主 Agent（agent=）任务串行独立执行并汇报（目标工作流人格+工具）。

        主 Agent 必须给每个子任务携带必要 context（工具描述已强提示）；此处兜底：
        任务缺 context 时自动注入主对话最近背景，保证独立上下文的子 Agent 不盲跑。"""
        missing_bg = None   # 惰性生成一次，避免每个缺 context 任务重复拼装
        parts = []
        batch = []          # 并入并发批的普通/自定义子任务（保持原始顺序）
        batch_origin = {}   # 原任务序号(1基, 批内序号+1) -> 批内序号(0基)
        _warnings = {}      # 原任务序号 -> 回退警告文本（sub未注册时回退临时agent）
        for i, t in enumerate(tasks, 1):
            if not (t.get("context") or "").strip():
                if missing_bg is None:
                    missing_bg = self._auto_sub_context()
                if missing_bg:
                    t["context"] = missing_bg
            if t.get("agent_failed"):
                parts.append(f"【子任务 {i}】{t['title']}\n{t['goal']}")
                continue
            if t.get("agent"):
                if t.get("async", True):
                    # 异步派发：成员后台独立执行（团队成员模式），立即返回，
                    # 领导者随后 look_context 监督 / chat_with 催促 / 共享空间收结果。
                    # 成员客户端与主 Agent 同一套配置（client_from 派生独立实例），
                    # 避免成员用 settings 默认配置与主会话错位导致开工失败。
                    from zhuzhu_Copilot.core import agent_team_run
                    _aid = "wf:" + t["agent"]
                    _mem_client = None
                    try:
                        _mem_client = agent_team_run.client_from(self.llm)
                    except Exception:
                        _mem_client = None
                    ok, msg = agent_team_run.team_start(
                        _aid, t["agent"], t["goal"], context=t.get("context") or "",
                        space=str(t.get("space") or ""),
                        shared=bool(t.get("shared_context")),
                        source=f"Agent({t['agent']})",
                        client=_mem_client,
                        conversation=self.conversation)
                    parts.append(f"【子任务 {i}】{t['title']}\n[{msg}]")
                    continue
                # 跨工作流主 Agent：独立上下文执行并汇报（无轮数上限，可被用户停止）；
                # 开启共享时同样加入共同上下文空间，与主 Agent、其他成员协同。
                out = agent_subagent.dispatch_agent_llm(
                    self.llm, t["agent"], t["goal"], stop=lambda: self._stop.is_set(),
                    on_sub_event=self.on_sub_event, title=t["title"],
                    context=t.get("context") or "",
                    shared_context=bool(t.get("shared_context")),
                    space=str(t.get("space") or ""),
                    conversation=self.conversation)
                parts.append(f"【子任务 {i}】{t['title']}\n{out}")
                continue
            entry = {"title": str(t.get("title") or f"子任务 {i}"),
                     "goal": t["goal"],
                     "context": t.get("context") or "",
                     "shared_context": bool(t.get("shared_context")),
                     "space": str(t.get("space") or "")}
            if t.get("sub"):
                # 任务级指定自定义子 Agent（sub_<名>）：按任务覆盖 description/goal/allowed
                conf = agent_subagent.subagent_tool("sub_" + t["sub"].lstrip("_")
                                                    if not t["sub"].startswith("sub_")
                                                    else t["sub"], self.workflow)
                if conf is None:
                    # sub 未注册：不中断任务，回退为通用临时子 Agent 并记录警告
                    # （LLM 常混淆临时/注册子agent：临时agent应不传sub参数）
                    _warnings[i] = (f"[警告] 子 Agent「{t['sub']}」未在当前工作流注册，"
                                    f"已回退为通用临时子 Agent 执行。"
                                    f"临时子 Agent 请勿传 sub 参数；"
                                    f"注册子 Agent 请先用 list_sub_agents 查看可用名称。")
                else:
                    ov = (t.get("overrides") or {}).get(conf["name"], {})
                    ov = ov if isinstance(ov, dict) else {}
                    goal = str(ov.get("goal") or t["goal"] or conf["goal"]).strip()
                    allowed = ov.get("allowed")
                    # 已注册自定义子 Agent：人格 = 显式 persona，否则用注册 goal 的自述
                    # （goal 通常内含「你是一位…」式人格，不能被通用子 Agent 身份文本覆盖）
                    persona = str(ov.get("persona") or conf.get("persona")
                                  or conf.get("goal") or "").strip()
                    entry["goal"] = goal
                    if isinstance(allowed, (list, tuple)) and allowed:
                        entry["allowed"] = tuple(allowed)
                    if persona:
                        entry["persona"] = persona
                    entry["custom"] = True
            batch.append(entry)
            batch_origin[i] = len(batch) - 1
        if batch:
            collect = []
            agent_subagent.dispatch_sub_agents(
                self.llm, batch, stop=lambda: self._stop.is_set(),
                on_status=self.on_status, on_sub_event=on_sub_event or self.on_sub_event,
                workflow=self.workflow, collect=collect,
                conversation=self.conversation)
            out_by = {bi: txt for (bi, _title, txt) in collect}
            for i in sorted(batch_origin):
                bi = batch_origin[i]
                title = str(tasks[i - 1].get("title") or f"子任务 {i}")
                warn = _warnings.get(i, "")
                result = out_by.get(bi, "（无结果）")
                parts.append(f"【子任务 {i}】{title}\n{warn}{result}")
        return "\n\n".join(parts)

    def _auto_sub_context(self, limit: int = 2000) -> str:
        """子任务未带 context 时的兜底背景摘要：取主对话最近一段「用户/助手」文本
        （跳过 system 与工具结果，文本类 content 提取，图片剥离），
        让独立上下文的子 Agent 至少掌握任务背景，而非完全盲猜/重复读取。"""
        parts = []
        used = 0
        for m in reversed(self._messages or []):
            if not isinstance(m, dict):
                continue
            role = m.get("role")
            if role in ("system", "tool"):
                continue
            c = m.get("content")
            if isinstance(c, list):
                c = "".join(str(x.get("text") or "") for x in c
                            if isinstance(x, dict) and x.get("type") == "text")
            c = str(c or "").strip()
            if not c:
                continue
            seg = f"{'用户' if role == 'user' else '助手'}: {c[:400]}"
            parts.append(seg)
            used += len(seg)
            if used >= limit:
                break
        if not parts:
            return ""
        return "主 Agent 对话最近背景（请结合本任务使用）：\n" \
               + "\n".join(reversed(parts))[:limit]

    @staticmethod
    def _to_int(v, default: int) -> int:
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _sub_allowed(tools_str: str):
        """子任务可用工具：与子 Agent 白名单取交集；空则用全部白名单工具"""
        if not tools_str:
            return None
        names = {x.strip() for x in str(tools_str).replace("，", ",").split(",") if x.strip()}
        inter = tuple(n for n in names if n in agent_subagent.SUB_AGENT_WHITELIST)
        return inter or None

    # ---------- 自动朗读（边输出边朗读 / 回复自动朗读） ----------
    def _on_stream_delta(self, s: str):
        """LLM 流式文本增量：转发面板显示，同时把完整句子切出投递给朗读线程"""
        if self.on_delta:
            self.on_delta(s)
        # 积分消耗：内置模型按 token 增量实时扣费；积分耗尽立即截断输出并追加固定文案
        pc = getattr(self, "_points_consumer", None)
        if pc is not None and s:
            try:
                if pc.feed(s):
                    if self.on_delta:
                        self.on_delta(pc.get_insufficient_message())
                    self._points_exhausted = True
                    self._stop.set()
            except Exception:
                pass
        if self._tts_auto and s and not self._tts_finish:
            self._tts_buf += s
            self._tts_flush_sentences()

    def _tts_flush_sentences(self):
        """把累积文本按句末标点切成短句入队（未完成部分留在缓冲继续累积）。
        短句（长度 < _TTS_MIN_SEG）不立即切出，与后续文本合并成一句再合成，
        减少零散 API 调用导致的卡顿；无标点的长文本到 _TTS_MAX_SEG 强制切分，
        保证长句也能边输出边朗读。"""
        buf = self._tts_buf
        if not buf:
            return
        n = len(buf)
        # 找到最后一个句末标点位置
        cut = -1
        for i, ch in enumerate(buf):
            if ch in _TTS_SENT_END:
                cut = i + 1
        if cut > 0 and cut >= _TTS_MIN_SEG:
            # 有句末标点且长度足够 → 切出（含标点），剩余留缓冲
            seg, self._tts_buf = buf[:cut], buf[cut:]
        elif n >= _TTS_MAX_SEG:
            # 无足够标点但文本已超长 → 整体强制切出，保证长文持续朗读
            seg, self._tts_buf = buf, ""
        else:
            # 文本太短或只有半句：等待更多内容（finish 时统一入队）
            return
        seg = seg.strip()
        if seg:
            self._tts_queue.put(seg)

    def _tts_start_worker(self):
        """启动朗读线程：若上一轮线程仍在收尾（队列未读完）先等其退出，避免双播放源"""
        if self._tts_thread and self._tts_thread.is_alive():
            self._tts_thread.join(timeout=10)
        self._tts_thread = threading.Thread(target=self._tts_worker, daemon=True)
        self._tts_thread.start()

    def _tts_worker(self):
        """朗读线程：逐句调 DashScope 流式合成，边合成边 pygame 播放（串行队列）"""
        if not agent_tools._tts_play_start():
            if self.on_status:
                self.on_status("自动朗读不可用：pygame 未安装或音频初始化失败，已跳过语音播放")
            return   # 播放器不可用：不合成（避免白耗 API）
        err_shown = False   # 只上报首次合成失败，避免刷屏
        while True:
            if self._tts_stop.is_set():
                break
            try:
                seg = self._tts_queue.get(timeout=0.5)
            except queue.Empty:
                if self._tts_finish:
                    break
                continue
            if self._tts_stop.is_set():
                break
            # 每句开始前标记新段：句首淡入消除爆音
            agent_tools._tts_play_segment_start()
            try:
                agent_tts.synthesize_stream(
                    seg, voice_id="", on_chunk=agent_tools._tts_play_chunk)
                agent_tools._tts_play_flush()   # 整句累积完成：一次性播放，句内无切块
            except Exception as e:
                if not err_shown:
                    err_shown = True
                    if self.on_status:
                        self.on_status(f"自动朗读合成失败（已跳过本句）：{e}")
        if self._tts_stop.is_set():
            agent_tools._tts_play_stop()   # 用户停止：立即停声并清缓冲
        else:
            agent_tools._tts_play_finish()  # 正常结束：播完剩余整句后退出

    def _tts_finish_read(self):
        """任务正常完成：停止接收新句子，残余文本入队，让朗读线程读完队列后自行退出"""
        if not self._tts_auto:
            return
        self._tts_finish = True
        if self._tts_buf.strip():
            self._tts_queue.put(self._tts_buf.strip())
            self._tts_buf = ""

    def _tts_stop_read(self):
        """立即停止朗读：清空队列并通知朗读线程退出（用户停止 / AI 主动 tts_speak 时）"""
        self._tts_stop.set()
        self._tts_finish = True
        self._tts_buf = ""
        while not self._tts_queue.empty():
            try:
                self._tts_queue.get_nowait()
            except queue.Empty:
                break

    # ---------- 主循环 ----------
    def _system_prompt(self, agent_name: str = "") -> str:
        """构建系统提示词：每次都重新读取 settings.json，
        用户中途新增/修改的自定义规则在下一轮立即生效。
        工作目录绝对路径强制注入 system（每轮必然发送给 AI），设置后整段保持
        稳定不破坏服务端前缀缓存；任务清单/任务技能仍以末尾独立消息注入。

        Cordis：激活工作流的 agent.py 若提供 SYSTEM_PROMPT 或 build_system_prompt，
        则整体替换内置系统提示（用户自定义 Agent 人格）。
        注意：agent_hooks 显式传入 self.workflow（而非依赖线程局部），保证在
        非任务线程（重启恢复/后台评估/上下文压缩等）也能读到会话级工作流人格，
        避免回退到全局激活工作流而漏用自定义提示词。"""
        # 自定义 Agent 人格完整覆盖（@agent 切到该会话；_switch_session_agent 设置）。
        # 输出语言指令一律前置拼接：这两条「整体替换」分支（自定义 persona /
        # 工作流 SYSTEM_PROMPT）会绕过 build_system_prompt，若不补则英文提示词
        # 语言下用户自定义人格的 Agent 仍回中文。人格是用户内容，故前置而非替换。
        _lang = agent_skills._tp("prompt.lang.directive",
                                 agent_skills._LANG_DIRECTIVE_ZH)
        if self.persona:
            _p = _lang + "\n\n" + self.persona
            # 高效模式：自定义人格同样整体替换提示词 → 补挂"本次只有这些工具"的说明
            # （人格是用户内容，可能按全套工具描述流程，而那些工具本次并不存在）
            if self.efficient_mode:
                _p = agent_skills.ensure_efficient_notice(_p)
            return _p
        try:
            hooks = agent_workflow.agent_hooks(self.workflow)
            mod = hooks.get("mod")
            custom = None
            if mod is not None:
                if hasattr(mod, "build_system_prompt"):
                    custom = mod.build_system_prompt(agent_name, "") or None
                if custom is None and getattr(mod, "SYSTEM_PROMPT", None):
                    custom = getattr(mod, "SYSTEM_PROMPT")
            if custom:
                # 防重复：内置 build_system_prompt 的首段**本身就是**同一语言指令，
                # 而默认工作流的 agent.py 会把 build_system_prompt 委托给内置实现
                # （见 zhuzhu_copilot/agent.py）→ 此处若再无脑前置，语言指令会被拼两次。
                # 仅当自定义提示词尚未包含该指令时才前置（自定义人格是用户内容，
                # 必须补语言指令；委托内置的则已自带，无需重复）。
                c = str(custom)
                if _lang.strip() and _lang.strip() not in c[:len(_lang) + 80]:
                    c = _lang + "\n\n" + c
                # 高效模式：工作流提示词可能仍按"全套工具"描述流程（自带方法论里会让人
                # 先调 browser_open / create_docx 等），而在高效模式下这些工具并不在
                # 工具表中 → 补挂说明，避免模型反复调用被剔除的工具白烧轮次。
                if self.efficient_mode:
                    c = agent_skills.ensure_efficient_notice(c)
                return c
        except Exception:
            pass
        # 非默认工作流：禁止静默套用默认人设 —— 工作流未提供自定义 agent.py 人设时，
        # 用工作流名派生专属人设兜底，确保每个工作流的人格各不相同（不套用默认提示词）。
        wf = self.workflow or agent_workflow.active_workflow()
        if wf and wf != agent_workflow.DEFAULT_WORKFLOW:
            if agent_workflow.is_workflow(wf):
                _fp = _wf_fallback_persona(wf)
                if self.efficient_mode:
                    _fp = agent_skills.ensure_efficient_notice(_fp)
                return _fp
            # 工作流已不存在（被删除/禁用）：绝不能再派生其专属人设，否则已删工作流的
            # 身份会被"复活"（AI 从此自称该工作流助手并反复提及该工作流）。
            # 就地清除陈旧绑定并回退内置默认提示。
            _log.warning("会话绑定的工作流「%s」已不存在，回退内置默认人设", wf)
            self.workflow = None
        prompt = agent_skills.build_system_prompt(agent_name,
                                                  text_only=self.text_only,
                                                  memory_enabled=self.memory_enabled,
                                                  direct=self.direct,
                                                  subagents_allowed=self.allow_subagents,
                                                  efficient=self.efficient_mode)
        wd = agent_tools.get_workdir()
        if wd:
            prompt += agent_skills._tpf(
                "prompt.block.workdir",
                "\n\n【当前工作目录】{wd}\n"
                "文件查找/创建/修改/删除、命令执行默认在此目录内进行；"
                "未指定绝对路径时，相对路径一律基于该工作目录解析。", wd=wd)
        else:
            prompt += agent_skills._tp(
                "prompt.block.workdir_unset",
                "\n\n【当前工作目录】未设置\n"
                "文件查找/创建/修改/删除、命令执行默认在当前进程目录内进行；"
                "未指定绝对路径时，相对路径一律基于当前进程目录解析。")
        # 目录名/文件名可能具有误导性（如目录叫 my first android app 但实际不是
        # Android 工程）：凡涉及当前项目/代码/文件内容的问题必须先经工具核实再回答，
        # 禁止凭目录名、文件名或刻板印象臆测项目类型、技术栈或代码内容。
        prompt += agent_skills._tp(
            "prompt.block.verify_before_assume",
            "\n\n涉及当前工作目录内的项目/代码/文件内容时：必须先调用 "
            "list_directory / read_file / run_command 等工具核实真实内容后再作答；"
            "严禁仅凭目录名、文件名或先入为主的印象臆测项目类型、技术栈或代码内容。")
        return prompt

    def _wf_hook(self, hook: str):
        """调用本会话工作流 agent.py 的生命周期钩子（异常静默，不影响主流程）。
        显式传入 self.workflow（而非依赖线程局部），保证非任务线程
        （重启恢复/后台评估/上下文压缩等）也能读到会话级工作流钩子。"""
        try:
            mod = agent_workflow.agent_hooks(self.workflow).get("mod")
            if mod is not None:
                fn = getattr(mod, hook, None)
                if callable(fn):
                    fn(self)
        except Exception:
            pass

    def _todo_text(self) -> str:
        """读取**本引擎所属会话**未完成的任务清单，格式化为对话消息文本（无任务返回空串）

        任务清单按会话隔离：显式传入 self.conversation，避免依赖线程局部作用域
        （压缩/重启恢复等非任务线程同样要读到本会话自己的清单）。"""
        try:
            active = [t for t in agent_tools.load_todos(self.conversation)
                      if t.get("status") != "completed"]
        except Exception:
            return ""
        if not active:
            return ""
        head = agent_skills._tp("prompt.todo.head",
                                 "【当前任务清单】用 update_todo 跟踪进度"
                                 "（全量提交含已完成项）：")
        lines = [f"{_TODO_MARK}\n{head}"]
        for i, t in enumerate(active, 1):
            lines.append(f"{i}. [{t.get('status', 'pending')}] {t.get('title', '')}")
        return "\n".join(lines)

    def _sync_todo_msg(self):
        """把最新任务清单同步为对话末尾的独立 user 消息（原位替换，不累积）。

        历史中唯一 todo 消息位于末尾 → system + 早期历史保持字节级不变，
        服务端前缀缓存（DeepSeek prompt_cache_hit / OpenAI cached_tokens）持续命中；
        todo 变化只 miss 这几十 token 的短消息。"""
        text = self._todo_text()
        idx = None
        for i in range(len(self._messages) - 1, 0, -1):   # 从末尾向前找（最新一条）
            m = self._messages[i]
            if (isinstance(m, dict) and m.get("role") == "user"
                    and isinstance(m.get("content"), str)
                    and m["content"].startswith(_TODO_MARK)):
                idx = i
                break
        if not text:
            if idx is not None:
                del self._messages[idx]
            return
        if idx is not None:
            self._messages[idx]["content"] = text
        else:
            self._messages.append({"role": "user", "content": text})

    def _skill_mark_index(self):
        """返回对话中「任务技能/插件规范」那条哨兵消息的下标（无则 None）。

        与 _sync_skill_msg 的定位规则完全一致（末尾往前找第一条以 _SKILL_MARK 开头的
        user 消息），抽出来供高效模式清理残留技能消息复用，避免两处扫描逻辑漂移。
        """
        for i in range(len(self._messages) - 1, 0, -1):
            m = self._messages[i]
            if (isinstance(m, dict) and m.get("role") == "user"
                    and isinstance(m.get("content"), str)
                    and m["content"].startswith(_SKILL_MARK)):
                return i
        return None

    def _sync_skill_msg(self, manual_skills: list = None, manual_plugins: list = None):
        """把当前任务的技能 / 插件规范同步为对话末尾独立 user 消息（原位替换，不累积）。

        技能指令按任务（user_input）变化，若注入 system 会让每条新任务都改变
        system → 服务端前缀缓存整段 miss、全量重计费。改为末尾消息后，
        system + 早期历史保持字节级不变，技能变化只 miss 末尾几十 token 的短消息。
        技能指令内容与路由硬拦截（skills_covering_tools）完全不受影响。

        用户手动调用的技能与插件**必须**把说明与调用规范一并给出，且用强约束措辞
        （「必须严格…不得跳过…」）—— 只给名字或只给正文时，模型常常按自己的理解自由发挥，
        表现为「调用了但没按技能流程走」。

        （高效模式 efficient_mode=True 时不调用本方法：技能/插件规范不注入上游。）"""
        # 兜底：高效模式即便被调用也不注入技能/插件规范，并清掉历史里可能残留的
        # 那一条（用户中途开启高效模式时，旧技能消息不该继续占用上下文）。
        if self.efficient_mode:
            _idx = self._skill_mark_index()
            if _idx is not None:
                del self._messages[_idx]
            return
        merged = list(self._auto_skills or [])
        for s in (agent_skills.filter_enabled_skills(manual_skills) or []):
            if s not in merged:
                merged.append(s)
        parts = []
        if merged:
            inst = agent_skills.skill_instructions(merged)
            if inst:
                parts.append(agent_skills._tp("prompt.inject.skill_head",
                "【必须严格遵守的技能规范】\n"
                "以下技能由系统匹配或用户手动调用，其说明与规范即为本任务的"
                "最高优先执行依据：\n"
                "· 必须先按其流程组织步骤再行动，禁止跳过技能直接调用底层工具；\n"
                "· 技能规范与其它习惯冲突时，以技能规范为准；\n"
                "· 不得只复述规范而不真正执行。\n\n") + inst)
        plugins = []
        try:
            from zhuzhu_Copilot.core import agent_plugins
            plugins = agent_plugins.filter_enabled_plugins(manual_plugins)
            spec = agent_plugins.plugin_spec_text(plugins)
        except Exception:
            spec = ""
        if spec:
            parts.append(agent_skills._tp("prompt.inject.plugin_head",
            "【必须严格遵守的插件规范】\n"
            "用户手动调用了以下插件，其说明、SKILL.md 规范与调用规范如下，"
            "必须按规范真正调用它提供的工具完成任务：\n\n") + spec)
        text = (f"{_SKILL_MARK}\n" + "\n\n".join(parts)) if parts else ""
        idx = self._skill_mark_index()
        if not text:
            if idx is not None:
                del self._messages[idx]
            return
        if idx is not None:
            self._messages[idx]["content"] = text
        else:
            self._messages.append({"role": "user", "content": text})

    @staticmethod
    def _rules_text() -> str:
        """真实读取用户自定义开发规则文本（settings.json 的 custom_rules）"""
        rules = [str(r).strip()
                 for r in (agent_skills.load_settings().get("custom_rules") or [])
                 if str(r).strip()]
        if not rules:
            return "（当前未设置自定义开发规则）"
        return "\n".join(f"- {r}" for r in rules)

    @staticmethod
    def _tool_touched_config(name: str, args: dict) -> bool:
        """判定某文件工具的目标是否为持有自定义规则的配置（settings.json/agents.json）。
        命中时引擎会在执行后复位规则确认标志，令下一开发工具重新读取新增规则。"""
        if name not in ("write_file", "edit_file", "search_replace", "delete_file"):
            return False
        p = (args or {}).get("path") or (args or {}).get("file") or ""
        try:
            bn = str(p).replace("\\", "/").rsplit("/", 1)[-1].lower()
        except Exception:
            bn = ""
        return bn in ("settings.json", "agents.json")

    # 触发自动预览的工具 → 其路径参数键（AI 操作这些文件时把路径下发给右侧预览面板）
    _PREVIEW_TOOLS = {
        "read_file": "path", "write_file": "path", "edit_file": "path",
        "search_replace": "path", "insert_lines": "path", "delete_file": "path",
        "create_docx": "path", "create_pptx": "path", "create_xlsx": "path",
        # 办公读取/编辑后自动刷新右侧预览面板（读也刷新，便于看到最新保真渲染）
        "read_docx": "path", "read_pptx": "path", "read_xlsx": "path", "read_pdf": "path",
        "edit_docx": "path", "edit_pptx": "path", "edit_xlsx": "path",
        # view_image：查看图片后自动在右侧预览面板展示
        "view_image": "path",
    }

    def _preview_path_of(self, name: str, args: dict) -> str:
        """返回应自动预览的文件绝对路径；非文件工具/无路径返回空串。
        相对路径按当前全局工作目录补齐为绝对路径，便于右侧预览器直接读取。"""
        key = self._PREVIEW_TOOLS.get(name)
        if not key:
            return ""
        p = str((args or {}).get(key) or "")
        p = p.strip().strip("\"'")
        if not p:
            return ""
        if not os.path.isabs(p):
            try:
                from zhuzhu_Copilot.core import agent_tools as _at
                wd = _at.WORKDIR or ""
                if wd:
                    p = os.path.join(wd, p) if not p.startswith("\\") \
                        else os.path.normpath(os.path.join(wd, p.lstrip("\\")))
            except Exception:
                pass
        return p

    def _inject_reference_before_user(self, msg: dict):
        """把参考性上下文（团队消息 / 共同上下文快照）插到**用户最新一条消息之前**。

        参考内容只是资料：排在用户输入之前，模型会把它当背景；排在最后（历史实现如此），
        模型会转而回应/顺从那一段文本 —— 对话内切换工作流后沿用快照里旧的角色自称、
        而不按本次工作流人设陈述身份，根因就在这。子 Agent 侧的注入顺序同此约定。
        找不到 user 消息时退化为追加（仅极少数由工作流钩子改写历史的场景）。"""
        idx = -1
        for i in range(len(self._messages) - 1, -1, -1):
            if self._messages[i].get("role") == "user":
                idx = i
                break
        if idx < 0:
            self._messages.append(msg)
        else:
            self._messages.insert(idx, msg)

    def run(self, user_input: str, agent_name: str = "", images: list = None,
            skills: list = None, plugins: list = None):
        # 本任务所属工作流：引擎线程内技能/LLM/工具/钩子按会话工作流隔离
        # （@工作流 切换后各会话独立，互不干扰并发会话）；finally 中复位
        wf = self.workflow or ""
        agent_skills.set_current_workflow(wf)
        agent_workflow.set_current_workflow(wf)
        # 本任务所属对话：共同上下文空间按对话隔离——同一对话内主 Agent 与各子 Agent /
        # 成员工作流共用一份空间，不同对话即使并发运行也互不可见、互不覆盖。
        # 必须在 on_task_start 钩子（工作团激活开启空间）之前落地；finally 中复位。
        from zhuzhu_Copilot.core import agent_context
        _prev_conv = agent_context.thread_local_conversation()
        agent_context.set_conversation(self.conversation)
        # 工作团控制句柄：注册「main」，供领导者 pause/resume/warn/chat_with（复用 _stop 事件）
        try:
            from zhuzhu_Copilot.core import agent_control
            self._control = agent_control.control_from_stop("main", self._stop)
        except Exception:
            self._control = None
        try:
            # 本轮文件变更统计起始点：重建跨线程共享的累计桶（run 线程自身绑定一份）
            self._edit_bucket = agent_tools.reset_edit_delta()
            self._run_inner(user_input, agent_name, images, skills, plugins)
        finally:
            # 取快照挂到实例供 UI 主线程读取（worker 线程已把写入累进 _edit_bucket）
            b = self._edit_bucket or {}
            self._last_edit_delta = {
                "added": int(b.get("added") or 0),
                "removed": int(b.get("removed") or 0),
                "files": {str(k): [int(a or 0), int(r or 0)]
                          for k, (a, r) in (b.get("files") or {}).items()},
            }
            if self._control is not None:
                try:
                    from zhuzhu_Copilot.core import agent_control
                    agent_control.unregister_control("main")
                except Exception:
                    pass
                self._control = None
            # 用户主动停止主任务 → 停止后台运行中的团队成员（正常结束则保留，成员可继续后台工作）。
            # 只停**本对话**的成员：其他对话的后台成员不受本对话停止影响（对话隔离）。
            if self._stop.is_set():
                try:
                    from zhuzhu_Copilot.core import agent_team_run
                    agent_team_run.team_stop_all(conversation=self.conversation)
                except Exception:
                    pass
            # 用户主动停止主任务 → 取消本轮启动的后台任务
            if self._stop.is_set() and self._bg_tasks_this_turn:
                try:
                    from zhuzhu_Copilot.core import agent_background as _ab
                    for tid in self._bg_tasks_this_turn:
                        _ab.bg_manager().cancel(tid)
                except Exception:
                    pass
            agent_skills.set_current_workflow("")
            agent_workflow.set_current_workflow("")
            # 对话作用域复位（线程可能被复用：下次任务入口会重新落地）
            agent_context.set_conversation(_prev_conv or "")

    def _run_inner(self, user_input: str, agent_name: str = "", images: list = None,
                   skills: list = None, plugins: list = None):
        self.end_state = ""
        self._rules_confirmed = False   # 每个新任务重新强制规则确认
        self._empty_retries = 0         # 每任务重置上游空响应纠正重试计数
        self._ctx_overflow_retries = 0  # 每任务重置「上游越窗 400」自动恢复计数
        # 账号系统：内置默认模型（agnes）必须登录后使用，未登录直接报错结束；
        # 已登录则初始化积分消耗器，流式输出按 token 增量实时扣费（见 _on_stream_delta）。
        self._points_consumer = None
        self._points_exhausted = False
        try:
            if _is_builtin_base_url(getattr(self.llm, "base_url", "")):
                _auth = _get_auth_client()
                if not _auth.is_logged_in():
                    _msg = _ui("未登录：内置模型（agnes）需登录账号后使用，请在右上角点击「登录」")
                    if self.on_status:
                        self.on_status(_msg)
                    self._messages.append({"role": "assistant", "content": _msg})
                    self.end_state = "done"
                    return
                self._points_consumer = PointsConsumer(_auth)
        except Exception:
            self._points_consumer = None
        # 每任务重置后台任务列表与自动后台标记
        self._bg_tasks_this_turn = []
        self._auto_background = False
        # 复杂任务判定：评估是否应自动后台执行
        try:
            complexity = agent_background.assess_task_complexity(user_input)
            if complexity["should_background"]:
                # 仅置位自动后台标记，不硬编码提示用户——由引导 Agent 在回复中自然告知
                self._auto_background = True
        except Exception:
            pass  # 判定失败不影响正常执行
        # 任务开始时的技能集快照：中途生成/导入的技能由 reload_extensions 与它做差集找出
        try:
            self._skills_at_start = {s.get("name") for s in
                                     (agent_skills.load_skills(self.workflow) or [])
                                     if s.get("name")}
        except Exception:
            self._skills_at_start = set()
        # 按用户提示词自动匹配技能并注入：简单提示词（如"生成一个毕业感言PPT"）也先走 skill 流程。
        # 命中技能再按当前工作流过滤（被禁用的技能不注入，遵循工作流技能隔离）；
        # 最后按子 Agent 准入过滤：能力禁用时剔除 sub-agent 技能（否则它通篇教派发，
        # 会把模型推向已被 schema 剔除的工具）
        # 高效模式：不匹配任何技能（上游只拿最小工具集与其说明，见 _EFFICIENT_TOOLS）
        self._auto_skills = [] if self.efficient_mode else (
            agent_skills.filter_subagent_skills(
                agent_skills.filter_enabled_skills(
                    agent_skills.auto_skill_names(user_input)),
                self.allow_subagents))
        # 当前任务相关技能 = 自动匹配 + 手动指定（技能规范硬拦截仅限这些技能覆盖的工具，
        # 避免无关技能 instruction 顺带提及的基础工具被误拦截）
        self._task_skills = set(self._auto_skills or [])
        for _s in (agent_skills.filter_subagent_skills(
                agent_skills.filter_enabled_skills(skills), self.allow_subagents) or []):
            self._task_skills.add(_s)
        # 用户手动调用的插件（/插件名）：插件对模型是黑盒，必须把「插件说明 + SKILL.md +
        # 调用规范」直接注入上下文（见 _sync_skill_msg），并把插件登记的技能并入本任务
        # 技能集 —— 其覆盖的底层工具走技能路由硬拦截，模型无法绕过技能乱调工具。
        self._manual_plugins = []
        try:
            from zhuzhu_Copilot.core import agent_plugins
            self._manual_plugins = agent_plugins.filter_enabled_plugins(plugins)
            for _s in (agent_skills.filter_subagent_skills(
                    agent_skills.filter_enabled_skills(
                        agent_plugins.plugin_skill_names_of(self._manual_plugins)),
                    self.allow_subagents) or []):
                self._task_skills.add(_s)
        except Exception:
            self._manual_plugins = []
        if self.efficient_mode:
            # 高效模式：技能集整体置空 —— 不注入技能/插件规范，也不做技能路由硬拦截
            # （规范里教的是被剔除的那些工具，注入只会诱导模型调用不可用工具）。
            self._task_skills = set()
            self._manual_plugins = []
        self._task_groups = _detect_task_groups(user_input)
        # 系统层面（sandbox 级）工具硬拦截状态：每任务开始时读取一次，
        # 设置变更下个任务生效；schema 与执行层同时生效，提示词干预无法绕过
        self._disabled_tools = agent_sandbox.disabled_tools()
        self._disabled_all = agent_sandbox.tools_disabled_all()
        self._cap_mcp = agent_skills.cap_enabled("mcp")
        self._cap_plugin = agent_skills.cap_enabled("plugin")
        self._plugin_mcp_names = frozenset()
        if not self._cap_plugin:
            try:
                from zhuzhu_Copilot.core import agent_plugins
                self._plugin_mcp_names = agent_plugins.plugin_mcp_names()
            except Exception:
                pass
        # 技能状态反馈：自动匹配与用户手动调用（/技能名）都要给出「正在调用/已调用」提示。
        # 此前只对自动匹配发状态，手动调用在界面上完全没有反馈 —— 用户看不到技能被调用，
        # 自然认为「手动调用技能没生效」。
        _invoked = list(self._auto_skills or [])
        for _s in (agent_skills.filter_enabled_skills(skills) or []):
            if _s not in _invoked:
                _invoked.append(_s)
        if self.efficient_mode:
            # 高效模式：技能/插件规范一律不注入 —— 用户用 /技能名、/插件名 手动点名时
            # 必须明确告知（否则界面显示"已调用技能"却毫无规范注入，用户会以为技能失效）。
            if _invoked or plugins:
                if self.on_status:
                    self.on_status("高效模式已开启：技能/插件规范不注入上游（请在设置页"
                                   "关闭高效模式后再使用技能与插件）")
            _invoked = []
        if _invoked and self.on_status:
            self.on_status(f"正在调用技能: {', '.join(_invoked)}")
            self.on_status(f"技能已调用: {', '.join(_invoked)}")
        # 插件调用同此：单独一条状态（界面用插件专属矢量图与气泡提示区分于技能行）
        if self._manual_plugins and self.on_status:
            _pnames = ", ".join(self._manual_plugins)
            self.on_status(f"正在调用插件: {_pnames}")
            self.on_status(f"插件已调用: {_pnames}")
        # 自动朗读：设置中「自动朗读」开关默认开启，或用户明确要求"朗读/语音回复"时开启。
        # AI 流式输出边生成边合成播放；未配置音色或 API Key 时给出提示并自动关闭（避免无声假象）。
        self._tts_finish = False
        self._tts_stop.clear()
        tts_cfg = agent_tts.load_config()
        self._tts_auto = bool(tts_cfg.get("auto_read", True)) or \
            agent_tts.has_read_intent(user_input)
        if self._tts_auto:
            if not tts_cfg.get("voice_id") or not agent_tts.load_api_key():
                self._tts_auto = False
                if self.on_status:
                    self.on_status("检测到朗读请求，但未配置音色或 DashScope API Key，"
                                   "已跳过自动朗读（可在设置-语音合成中配置）")
            else:
                self._tts_start_worker()
                if self.on_status:
                    self.on_status("已开启自动朗读：AI 输出时将边生成边播放语音")
        if not self._messages or self._messages[0].get("role") != "system":
            self._messages.insert(0, {"role": "system",
                                      "content": self._system_prompt(agent_name)})
        else:
            self._messages[0]["content"] = self._system_prompt(agent_name)
        self._messages.append({"role": "user",
                               "content": agent_llm.build_content(
                                   user_input + _plan_hint(self.allow_subagents),
                                   images)})
        # 静默虚拟桌面：任务开始切到独立桌面，结束自动返回主桌面（finally 兜底所有结束路径）
        switched = False
        if self.auto_vd:
            try:
                from zhuzhu_Copilot.core.agent_screen import virtual_desktop
                virtual_desktop("new")
                switched = True
                if self.on_status:
                    self.on_status("已在独立虚拟桌面开始工作")
            except Exception:
                switched = False
        try:
            # 生命周期钩子：任务正式开始前调用工作流 agent.py 的 on_task_start，
            # 允许自定义 agent 注入额外上下文 / 改写 engine._messages（异常静默）
            self._wf_hook("on_task_start")
            # 工作团：任务开始拉取主 Agent 收件箱（chat_with 消息）与共享上下文空间快照，
            # 使切换工作流/收到团队消息后能基于既有上下文继续回答。
            # 共享全开：无活跃空间时自动以本轮任务开启空间（owner=main），保证随便
            # 调用子 Agent/工作流都能读到同一份共同上下文并直接应答。
            # ★ 注入位置：一律插到**用户最新一条消息之前**（_inject_reference_before_user）。
            #   参考性内容若排在用户提问之后（历史实现如此），模型会把快照当成当前要回应
            #   的对象，并顺从其中旧的角色自称——表现为"对话内切换工作流后不按新人设陈述
            #   身份"。子 Agent 侧同样是"快照在前、任务目标在最后"。
            try:
                from zhuzhu_Copilot.core import agent_bus, agent_context
                _msgs = agent_bus.inbox("main", 20)
                if _msgs:
                    _lines = [f"[{m.from_id}] {m.text}" for m in _msgs]
                    self._inject_reference_before_user(
                        {"role": "user",
                         "content": agent_skills._tp(
                             "prompt.note.team_msg_head",
                             "收到来自其他 Agent 的消息：")
                                    + "\n" + "\n".join(_lines) + team_msg_note()})
                _sid = agent_context.active_space()
                if not _sid:
                    try:
                        _sid = agent_context.open_space(
                            seed=str(user_input or "")[:200], owner="main", activate=True)
                    except Exception:
                        _sid = ""
                if _sid and agent_context.has_space(_sid):
                    _snap = agent_context.render(_sid, viewer="main")
                    if _snap and _snap.strip():
                        self._inject_reference_before_user(
                            {"role": "user", "content": _snap + shared_snapshot_note()})
            except Exception:
                pass
            # 工具循环不设轮数上限：用户可随时点击停止，上下文自动压缩防遗忘；
            # 每轮都检查 _stop，长任务可一直执行下去
            while True:
                if self._stop.is_set():
                    self.end_state = "stopped"
                    if self.on_status:
                        self.on_status("已停止")
                    return
                # 工作团控制：暂停中挂起等待恢复；领导者警告注入本轮上下文（仅首次）
                if self._control is not None:
                    if not self._control.wait_if_paused():
                        self.end_state = "stopped"
                        if self.on_status:
                            self.on_status("已停止（被领导者暂停后终止）")
                        return
                    _warns = self._control.drain_warns()
                    if _warns:
                        self._messages.append({
                            "role": "user",
                            "content": (agent_skills._tp(
                                "prompt.note.leader_warn", "[领导者警告提醒]")
                                + "\n" + "\n".join(_warns)
                                + agent_skills._tp(
                                    "prompt.note.leader_warn_tail",
                                    "\n（请严格遵守该要求调整后续工作）"))})
                # 每轮重建系统提示词：用户中途新增/修改的规则在下一轮立即生效；
                # 内容未变化时不覆盖，保持发送前缀稳定利于上下文缓存命中
                new_prompt = self._system_prompt(agent_name)
                if self._messages[0].get("content") != new_prompt:
                    self._messages[0]["content"] = new_prompt
                # 任务技能 / 插件规范 + 任务清单同步为末尾独立消息（原位替换，不污染 system 前缀缓存）
                self._sync_skill_msg(skills, self._manual_plugins)
                self._sync_todo_msg()
                if self.on_status:
                    self.on_status("正在思考…")
                # 视觉模型：历史截图只保留最近 2 张，防上下文膨胀（就地修剪 self._messages）
                if not self.text_only:
                    self._prune_images(2)
                # tokens 预计算（含数组文本，供 token 感知压缩）
                self.last_estimate = self._estimate_tokens()
                # 预警/占用：取估算与上游真实占用更大者，提示口径与统计面板一致
                # （面板显示的就是上游输入+输出；只信偏低估算会让提示比实际晚到）
                _occupancy = max(self.last_estimate, self._real_occupancy())
                self._maybe_warn_context(_occupancy)
                # 自动压缩：token 估算超「可用输入预算 × 压缩比例」且冷却期满才触发
                # （1M 模式 0.80 / 默认 0.75）。冷却机制避免频繁压缩打断服务端前缀缓存，
                # 保持上下文连续可缓存。压缩/修剪都作用于 self._messages 本身。
                n = self._maybe_auto_compress()
                if n and self.on_status:
                    _saved = int(self._compaction.get("last_saved") or 0)
                    self.on_status(
                        f"上下文较长，已由模型自动摘要压缩 {n} 条旧消息"
                        + (f"（约释放 {_saved} tokens）" if _saved > 0 else ""))
                # 硬上限兜底：任何情况都不得发出超窗请求（估算误差/单条超大也兜住），
                # 否则上游直接 HTTP 400 ContextWindowExceeded。冷却期也照样裁剪。
                # 占用判定同样并入上游真实占用：估算偏低时真实输入可能已越硬上限。
                if max(self.last_estimate, self._real_occupancy()) > self._hard_ceiling():
                    self._auto_compress(keep_recent=20)
                    self.last_estimate = self._estimate_tokens()
                    if max(self.last_estimate, self._real_occupancy()) > self._hard_ceiling():
                        self._hard_trim(self._hard_ceiling())
                        self.last_estimate = self._estimate_tokens()
                # ★ 压缩（可能重建 self._messages 列表）已全部完成，此刻再取发送引用，
                # 否则 send_msgs 仍绑定压缩前的旧列表/副本，导致压缩对实际发送无效。
                if self.text_only:
                    # 纯文本模型：发送副本剥离图片，不改存储历史 → 前缀稳定利于缓存命中
                    send_msgs = [dict(m) for m in self._messages]
                    self._strip_images(send_msgs)
                else:
                    send_msgs = self._messages
                result = None
                try:
                    # 发送前的本地原文估算留档（消息 + 工具 schema，同一套密度口径）：
                    # 请求返回后用真实 usage 反推「本地估算 / 上游真实计费」的倍率，
                    # 见下方 token 校准。
                    self._sent_raw_est = (self._estimate_raw()
                                          + self._tool_schema_tokens())
                    result = self.llm.chat_stream(
                        send_msgs, tools=self._all_tools(), tool_choice="auto",
                        on_delta=self._on_stream_delta,
                        on_reasoning=self.on_reasoning,
                        stop=lambda: self._stop.is_set(),
                        max_tokens=int(self._max_output or _DEFAULT_MAX_OUTPUT))
                except agent_llm.AgentLLMError as _e:
                    # 上游报「上下文超长」（HTTP 400 ContextWindowExceeded）：
                    # 说明本轮请求确实越窗（本地估算偏低）。原地强压上下文后重试
                    # （有界），而不是把整个任务判失败 —— 用户报的
                    # 「没开 1M、1M 模型上下文却爆满并 400」正是走到这里的场景。
                    if (self._stop.is_set() or not _is_ctx_overflow(_e) or
                            self._ctx_overflow_retries >= _MAX_CTX_OVERFLOW_RETRIES):
                        raise
                    self._ctx_overflow_retries += 1
                    self._recover_ctx_overflow()
                    if self.on_status:
                        self.on_status(
                            "上游提示上下文超长（HTTP 400），已自动压缩并重试本轮"
                            f"（{self._ctx_overflow_retries}/{_MAX_CTX_OVERFLOW_RETRIES}）")
                    continue
                # 积分结算：本轮 LLM 调用结束，结算未达扣费阈值的零头 token
                if getattr(self, "_points_consumer", None) is not None:
                    try:
                        self._points_consumer.finalize()
                    except Exception:
                        pass
                # 积分耗尽截断：流式阶段已追加固定文案，此处并入助手消息并结束任务
                if getattr(self, "_points_exhausted", False):
                    _tail = (self._points_consumer.get_insufficient_message()
                             if getattr(self, "_points_consumer", None) is not None
                             else _ui(PointsConsumer.INSUFFICIENT_MESSAGE))
                    self._messages.append({"role": "assistant",
                                           "content": (result.get("text") or "") + _tail})
                    if self.on_status:
                        self.on_status(_ui("积分不足，任务已截断"))
                    self.end_state = "done"
                    return
                # 模型自动回退提示：选中模型请求失败已改用内置默认模型（连接参数已恢复）
                if (getattr(self.llm, "fell_back", False) and self.on_status
                    and not getattr(self.llm, "silent_fallback", False)):
                    self.on_status("模型调用失败，已自动回退内置默认模型继续")
                self._accum_usage(result["usage"], result.get("cache"))
                # token 密度校准：用上游真实 prompt 用量反推本地估算的偏差倍率并平滑
                # 修正（双向、有界）。
                # · 比对的是"消息估算 + 工具 schema 估算"这一个合计数（同一套密度口径）；
                # · 只采信足够大的样本（≥_TOK_CALIB_MIN_EST），否则固定开销占比过高；
                # · 下界 _TOK_FACTOR_MIN 保证始终留一份保守量（宁可早压缩，不可越窗）。
                try:
                    _real_p = int((result.get("usage") or {}).get("prompt") or 0)
                    _real_p = _real_p or int((result.get("usage") or {})
                                            .get("prompt_tokens") or 0)
                except Exception:
                    _real_p = 0
                if _real_p > 0 and self._sent_raw_est >= _TOK_CALIB_MIN_EST:
                    _f = _real_p / float(self._sent_raw_est)
                    _f = min(_TOK_FACTOR_MAX, max(_TOK_FACTOR_MIN, _f))
                    self._tok_factor = min(_TOK_FACTOR_MAX,
                                           max(_TOK_FACTOR_MIN,
                                               0.5 * self._tok_factor + 0.5 * _f))

                calls = result["tool_calls"]
                # 上游空响应（正文与工具调用均为空）不致命：注入纠正提示重试有限次，
                # 避免长任务流中偶发空响应直接失败 → 用户手动重试重跑 → ask_user 重复
                # 提问；达上限仍空才报错（绝不把空响应当“完成”显示 Successfully）。
                if calls or (result.get("text") or "").strip():
                    self._empty_retries = 0
                if not calls:
                    # 无工具调用且正文为空：先做有限纠正重试，而不是直接判失败
                    if not (result.get("text") or "").strip():
                        if self._empty_retries < _MAX_EMPTY_RESULT_RETRIES:
                            self._empty_retries += 1
                            self._messages.append({
                                "role": "user",
                                "content": agent_skills._tp(
                                    "prompt.inject.empty_response",
                                    "[系统提示] 上游服务商本次返回了空结果（无正文且无工具"
                                    "调用）。请忽略该空响应并基于已有上下文继续执行任务："
                                    "可直接给出结论，或调用所需工具获取信息后再继续。")})
                            if self.on_status:
                                self.on_status(f"上游返回空响应，已自动补充提示重试"
                                               f"（{self._empty_retries}/{_MAX_EMPTY_RESULT_RETRIES}）")
                            continue
                        raise agent_llm.AgentLLMError(
                            f"上游连续 {_MAX_EMPTY_RESULT_RETRIES + 1} 次返回空响应，"
                            "任务未完成（无正文且无工具调用），请稍后重试")
                    self.end_state = "done"
                    self._messages.append({"role": "assistant",
                                           "content": result["text"] or "(完成)"})
                    if self.on_status:
                        self.on_status("完成")
                    return

                # 组装 assistant 消息（含 tool_calls）。
                # 关键：arguments 必须是合法 JSON，否则上游（尤其字节 agent plan 等
                # 严格校验的端点）对历史里这条 assistant tool_call 直接报 400
                # "arguments must be valid JSON"。这里先净化非法 arguments 为 {} 再
                # 落库，同时标记待重试，让工具层以「参数错误」提示模型重新发起。
                bad_indexes = set()
                cleaned_calls = []
                for i, c in enumerate(calls):
                    c = dict(c)
                    fn = c.get("function") or {}
                    parsed = parse_tool_args(fn.get("arguments"))
                    if parsed is None:
                        bad_indexes.add(i)
                        c["function"] = dict(fn, arguments="{}")
                    else:
                        # LLM 偶发返回带瑕疵的参数（围栏/尾部逗号/单引号），
                        # 容错解析成功后落库规范化 JSON，避免上游 400 且不误判为重试
                        c["function"] = dict(fn,
                                             arguments=json.dumps(parsed, ensure_ascii=False))
                    cleaned_calls.append(c)
                self._messages.append({
                    "role": "assistant",
                    "content": result["text"] or None,
                    "tool_calls": cleaned_calls,
                })
                last_images = []
                last_failed = False
                answered = set()
                rules_just = False   # 本轮是否触发过开发规则确认（全部拦截后统一置位）
                # 并发开关：默认开启，settings.json 可设 concurrent_edits=false 关闭
                concurrent_edits = bool(
                    agent_skills.load_settings().get("concurrent_edits", True))
                # 先预处理全部调用：技能拦截 / 规则确认 / 参数解析（同步，保持调用顺序语义），
                # 产出「待执行」列表（name, args, call, skill_read, approved）；并发编辑类在
                # 预处理完成后再批量并行执行，其余维持串行。预处理阶段的拦截/拒绝结果直接
                # 落上下文，未进入执行阶段。
                pending = []   # (index, name, args, call, skill_read, approved, req_failed)
                for i, call in enumerate(calls):
                    if self._stop.is_set():
                        # 补齐未执行工具的回复，保持 tool_calls 配对完整，防下一轮发送 400
                        for c in calls:
                            if c.get("id") and c["id"] not in answered:
                                self._messages.append({
                                    "role": "tool", "tool_call_id": c["id"],
                                    "content": "[已停止] 用户已停止任务，该工具未执行"})
                        self.end_state = "stopped"
                        return
                    name = call["function"]["name"]
                    # 技能路由硬拦截：被技能覆盖的工具，首次调用直接把该技能的规范流程
                    # 注入本轮上下文并拦截，强制按技能执行（防 AI 跳过 skill 直接裸调工具）。
                    # 每技能最多注入一次（_skill_consulted 记录技能名）：同一技能覆盖的
                    # 多个工具只在首个调用时拦截注入，之后该技能覆盖的工具直接放行，
                    # 显著减少技能规范化的拦截频率（原按工具名记录会反复拦截同一技能）。
                    if not self.direct and name not in _NO_INTERCEPT_TOOLS:
                        # 基础/交互工具（如 ask_user）已在 _NO_INTERCEPT_TOOLS 中直接放行；
                        # 其余工具只拦截「当前任务实际命中/指定」技能（_task_skills）覆盖的场景，
                        # 避免无关技能 instruction 顺带提及的基础工具（write_file/read_file 等）
                        # 被误拦截导致模型收到无关规范流程而跳过/延迟正常调用。
                        covered = [s for s in agent_skills.skills_covering_tools([name]).get(name, [])
                                   if s in self._task_skills]
                        unseen = [s for s in covered
                                  if s not in self._skills_read
                                  and s not in self._skill_consulted]
                        if unseen:
                            sname = unseen[0]
                            self._skill_consulted.add(sname)
                            skill = next((s for s in agent_skills.load_skills()
                                          if s.get("name") == sname), None)
                            body = (skill or {}).get("instruction") or ""
                            text = (f"[技能规范化] 工具「{name}」的操作由技能「{sname}」规范化，"
                                    f"已加载规范流程，请严格按以下流程执行"
                                    f"（含配图角度/位置等质量要求）：\n\n{body}\n\n"
                                    f"按此流程重新发起该工具调用。")
                            self._messages.append({"role": "tool", "tool_call_id": call["id"],
                                                   "content": text})
                            answered.add(call["id"])
                            if self.on_result:
                                self.on_result(name, text, [])
                            continue
                    if name in _DEV_TOOLS and not self._rules_confirmed and not self.direct:
                        # 动手开发前的强制规则读取：首次调用开发类工具不放行，
                        # 真实读取规则文本回给模型确认，下一轮重新发起再正常执行
                        rules_just = True
                        text = ("[开发前规则确认] 动手开发前必须先确认用户开发规则，"
                                "已读取规则文件，请严格遵守：\n" + self._rules_text()
                                + "\n规则已确认。现在重新发起你刚才的开发工具调用。")
                        self._messages.append({"role": "tool", "tool_call_id": call["id"],
                                               "content": text})
                        answered.add(call["id"])
                        if self.on_result:
                            self.on_result(name, text, [])
                        continue
                    if i in bad_indexes:
                        # 原参数非法 JSON：已净化落库，这里不误执行，返回提示让模型重新生成
                        text = ("[工具参数错误] tool_calls.arguments 不是合法 JSON，"
                                "请检查参数格式（字符串需正确转义引号）并重新发起该工具调用。")
                        self._messages.append({"role": "tool", "tool_call_id": call["id"],
                                               "content": text})
                        answered.add(call["id"])
                        if self.on_result:
                            self.on_result(name, text, [])
                        last_failed = True
                        continue
                    try:
                        parsed = parse_tool_args(call["function"]["arguments"])
                        if isinstance(parsed, dict):
                            args = parsed
                        elif isinstance(parsed, list):
                            # 列表形式参数（部分上游/模型以数组传参）：按工具签名顺序
                            # 映射为字典（write_file=["路径","内容"] 等），避免被一律置空
                            # 参导致反复报缺参、模型又看不到自己提交的内容而无法自纠
                            args = agent_tools._positional_args(name, parsed)
                        else:
                            args = {}
                    except Exception:
                        # 工具参数非法 JSON：不静默执行，返回提示让模型重新生成合法参数，
                        # 避免以空参误调用或直接向上游抛 400
                        text = ("[工具参数错误] tool_calls.arguments 不是合法 JSON，"
                                "请检查参数格式（字符串需正确转义引号）并重新发起该工具调用。")
                        self._messages.append({"role": "tool", "tool_call_id": call["id"],
                                               "content": text})
                        answered.add(call["id"])
                        if self.on_result:
                            self.on_result(name, text, [])
                        last_failed = True
                        continue
                    # 技能阅读：read_file 命中 skills/<名>/SKILL.md 时，用"正在调用技能"替代"read_file"
                    skill_read = None
                    if name == "read_file":
                        _sm = re.search(r"skills[\\/]([^\\/]+?)[\\/]SKILL\.md$",
                                        str(args.get("path") or args.get("file") or ""))
                        if _sm:
                            skill_read = _sm.group(1)
                    if self.on_status:
                        self.on_status(f"正在调用技能: {skill_read}" if skill_read
                                       else f"待执行工具: {name}")
                    # 每步确认：用户显式确认（AskBeforeEdit）后放行危险操作；YOLO 下 confirm
                    # 直接全量放行（含危险/系统目录/任意命令），dangerous 一并授权执行。
                    # ask_user 提问工具本身无需"允许执行"确认（弹窗即用户交互）。
                    approved = True if name == "ask_user" \
                        else (self.confirm(name, args) if self.confirm else True)
                    pending.append((i, name, args, call, skill_read, approved))
                # 批量并行执行可并发工具：写类按路径加锁（同文件串行、异文件并行），
                # 只读类直接并行。命令/浏览器/交互/子 Agent 等仍按原始顺序串行执行。
                # 被用户拒绝的调用不参与并发，由下方串行循环统一落拒绝结果。
                if concurrent_edits and pending:
                    from zhuzhu_Copilot.core import agent_edit
                    lock = agent_edit.file_lock   # 同一路径解析后的写入互斥
                    results = {}
                    _jobs = [(i, name, args, approved)
                             for (i, name, args, call, skill_read, approved) in pending
                             if approved and name in _CONCURRENT_TOOLS]
                    if _jobs:
                        if self.on_status and len(_jobs) == 1:
                            # 单个工具：发精准的「正在执行: <工具>」，
                            # 让 UI 显示该工具对应的状态（如「正在搜索文件」「正在读取时间」）
                            self.on_status(f"正在执行: {_jobs[0][1]}")
                        # 多工具并发：不再发「正在并行执行 N 个工具调用」状态提示
                        # （用户反馈该提示冗余，各工具的行内条目已足够反馈）
                        _futs = {}
                        with ThreadPoolExecutor(
                                max_workers=min(_CONCURRENT_MAX_WORKERS,
                                                len(_jobs))) as _ex:
                            for _ji, _nm, _ar, _ap in _jobs:
                                _futs[_ex.submit(self._run_one_tool, _nm, _ar, _ap,
                                                 _nm in _CONCURRENT_WRITE_TOOLS,
                                                 lock)] = (_ji, _nm)
                            for _f in as_completed(_futs):
                                _ji, _nm = _futs[_f]
                                try:
                                    _res = _f.result()
                                except Exception as _e:
                                    _res = {"text": f"[并发执行异常] {_nm}: {_e}",
                                            "images": []}
                                results[_ji] = _res
                # 串行执行其余调用（保持原始顺序）；可并发工具已在并发阶段执行完成
                for i, name, args, call, skill_read, approved in pending:
                    if self._stop.is_set():
                        for c in calls:
                            if c.get("id") and c["id"] not in answered:
                                self._messages.append({
                                    "role": "tool", "tool_call_id": c["id"],
                                    "content": "[已停止] 用户已停止任务，该工具未执行"})
                        self.end_state = "stopped"
                        return
                    if not approved:
                        text = "[用户拒绝执行此操作]"
                        imgs = []
                        # 拒绝结果仍要落上下文（保持 tool_calls 配对完整）
                        self._messages.append({
                            "role": "tool", "tool_call_id": call["id"],
                            "content": text})
                        answered.add(call["id"])
                        if self.on_result:
                            self.on_result(name, text, [])
                        if _looks_failed(text):
                            last_failed = True
                        continue
                    if name in _CONCURRENT_TOOLS and concurrent_edits and i in results:
                        res = results[i]   # 已在并发阶段执行完成，此处只取结果落上下文
                    else:
                        if self.on_status:
                            self.on_status(f"技能已调用: {skill_read}" if skill_read
                                           else f"正在执行: {name}")
                        res = self._execute(name, args, allow_dangerous=approved)
                    text, imgs = res["text"], res["images"]
                    # 工作团监督：主 Agent 工具轨迹写入 ContextLedger（look_context 数据源）
                    if self._control is not None:
                        try:
                            from zhuzhu_Copilot.core import agent_bus
                            _kind = "tool"
                            if name == "run_command":
                                _kind = "command"
                            elif name in ("read_file", "write_file", "edit_file",
                                          "search_replace", "insert_lines", "delete_file",
                                          "list_directory", "search_files", "grep",
                                          "search_code", "extract_text"):
                                _kind = "file"
                            elif name.startswith(("read_skill", "list_skill")):
                                _kind = "skill"
                            elif name.startswith(("mcp", "create_mcp")):
                                _kind = "mcp"
                            agent_bus.ledger("main").add(_kind, name, text[:300])
                        except Exception:
                            pass
                    # 本轮改到了持有自定义规则的配置（settings.json/agents.json）：
                    # 复位规则确认标志，让下一开发工具强制重新读取新增/修改的规则
                    if self._tool_touched_config(name, args):
                        self._rules_confirmed = False
                    # 缺参拦截时回显模型本次提交的 arguments 原文 + 输出要求：
                    # 帮助模型看清自己发出的是数组/错键名/空对象，直接按规范自纠，
                    # 避免同参数反复失败 5 轮后被动停止
                    if (text.startswith("[工具参数缺失]")
                            and call.get("function", {}).get("arguments")):
                        raw = str(call["function"]["arguments"])
                        if len(raw) > 500:
                            raw = raw[:500] + f"…（共 {len(raw)} 字符）"
                        text += (f"\n你本次提交的 arguments 原文：{raw}"
                                 "\n请按上述参数名输出标准 JSON 对象（键值对形式），"
                                 "不要用数组或位置参数。")
                    if self.on_result and name not in agent_tools.SUB_AGENT_TOOLS:
                        # 压缩缩略图副本给 UI 展示（原图仍喂给模型视觉验证）。
                        # 子 Agent 工具不在此渲染结果块：其各子任务的执行已通过 on_sub_event
                        # 实时流入「子Agent」子块，避免把汇总长文再刷成独立大块导致排版混乱。
                        self.on_result(name, text,
                                       [_compress_data_url(u, 480) for u in imgs])
                    # 子 Agent 工具结束后仅标记一条精简收尾状态（子块已展示全部过程）
                    if name in agent_tools.SUB_AGENT_TOOLS and self.on_status:
                        self.on_status(f"{name} 已完成，子 Agent 结果已汇总")
                    # 文件类工具执行成功 → 自动派发右侧预览（AI 操作相关文件时同步展示，
                    # 便于用户直观看到读/写/改/建的文档、图片、表格、Markdown 等）
                    if not _looks_failed(text):
                        try:
                            _pv = self._preview_path_of(name, args)
                        except Exception:
                            _pv = None
                        if _pv and self.on_preview:
                            try:
                                self.on_preview(_pv)
                            except Exception:
                                pass
                        # 可视化预览（用户外部浏览器）兜底自动刷新：AI 重写了正在被预览
                        # 的那个产物时，浏览器里的页面自动跟上（服务端比对文件指纹，
                        # 只是读取不会触发，不会打扰用户）。
                        if _pv:
                            try:
                                from zhuzhu_Copilot.core import agent_preview
                                agent_preview.notify_source_changed(_pv)
                            except Exception:
                                pass
                    if _looks_failed(text):
                        last_failed = True
                    # 完整工具输出直接进入上下文（用户要求禁止上下文截断限制）；
                    # 读取型工具的超长返回在此自动 LLM 结构化压缩进上下文，写入型保持完整。
                    context_text = self._condense_tool_text(name, text)
                    # 如果工具返回了图片，以数组形式写入 tool 消息（支持多模态模型查看工具输出图片）
                    if imgs:
                        self._messages.append({
                            "role": "tool", "tool_call_id": call["id"],
                            "content": [{"type": "text", "text": context_text},
                                        *({"type": "image_url",
                                           "image_url": {"url": u, "detail": "high"}} for u in imgs)]
                        })
                    else:
                        self._messages.append({
                            "role": "tool", "tool_call_id": call["id"],
                            "content": context_text,   # 纯字符串更兼容（部分 API 拒绝数组 content）
                        })
                    answered.add(call["id"])
                    if imgs:
                        last_images = imgs   # 本轮全部截图喂给下一轮视觉验证，不做裁剪
                # 并发编辑阶段被跳过的「拒绝项」与「并发阶段未纳入 jobs 的编辑项」已处理；
                # 并发编辑结果按原始顺序在串行循环中统一落上下文，此处无需额外处理
                if rules_just:
                    self._rules_confirmed = True   # 本轮已确认规则，下轮开发工具正常放行
                if last_images:
                    prompt = ("请观察最新屏幕截图，验证上一步操作结果并继续。"
                              if not last_failed else
                              "上一步工具调用失败，请结合截图分析原因（目标不在屏幕/坐标偏移/"
                              "弹窗未展开/参数错误），换方案重试（最多 2 次），仍失败则 ask_user 求助。")
                    new_content = agent_llm.build_content(prompt, last_images)
                    # 截图消息原位替换：历史中始终只有末尾一条视觉消息，
                    # 中间历史永不因截图变化 → 前缀缓存持续命中（无需 _prune_images 剥离）
                    for i in range(len(self._messages) - 1, 0, -1):
                        m = self._messages[i]
                        c = m.get("content")
                        if (isinstance(m, dict) and m.get("role") == "user"
                                and isinstance(c, list)
                                and any(isinstance(x, dict) and x.get("type") == "image_url"
                                        for x in c)):
                            del self._messages[i]
                            break
                    self._messages.append({"role": "user", "content": new_content})
                # 连续失败护栏：本轮任一工具失败则累加。默认**关闭自动停止**（用户要求取消
                # "连续失败自动停止任务"）；如需防空转可在 settings.json 设 consec_fail_stop=true
                # 重新开启（关闭时仅注入纠错提示并重置计数，任务继续由模型自纠，不硬停）。
                if last_failed:
                    self._consec_fail += 1
                    fail_stop = bool(
                        agent_skills.load_settings().get("consec_fail_stop", False))
                    if fail_stop and self._consec_fail >= _MAX_CONSEC_FAIL:
                        self._messages.append({
                            "role": "user",
                            "content": "已连续多次工具调用失败，请停止操作，检查环境/需求后重新发起。"})
                        self.end_state = "error"
                        if self.on_status:
                            # 用"错误"前缀 → 前端显式红色标记，避免失败后静默停止让用户误以为无响应
                            self.on_status(f"错误: 连续 {_MAX_CONSEC_FAIL} 轮工具调用失败，"
                                          "任务已自动停止。请检查工具参数/环境后重新发起。")
                        return
                    if self._consec_fail >= _MAX_CONSEC_FAIL:
                        # 未开启硬停：注入纠错提示并重置计数，任务继续（不自动终止）
                        self._messages.append({
                            "role": "user",
                            "content": "已连续多次工具调用失败，请检查参数格式/路径/环境后改进重试；"
                                       "若确认无法完成，请用 ask_user 询问用户。"})
                        self._consec_fail = 0
                else:
                    self._consec_fail = 0
        except agent_llm.AgentLLMError as e:
            # 用户主动停止（含 LLM 层"已停止"）优先识别为 stopped，而不是 error
            stopped = self._stop.is_set()
            self.end_state = "stopped" if stopped else "error"
            if self.on_status:
                self.on_status("已停止" if stopped else f"错误: {e}")
        except Exception as e:
            stopped = self._stop.is_set()
            self.end_state = "stopped" if stopped else "error"
            if self.on_status:
                self.on_status("已停止" if stopped else f"错误: {e}")
        finally:
            # 朗读收尾：正常完成则让朗读线程把剩余文本读完；停止/出错则立即停
            if self.end_state == "done":
                self._tts_finish_read()
            else:
                self._tts_stop_read()
            # 任何结束路径（完成/停止/错误/超轮数）都返回用户桌面，AI 操作完全无感
            if switched:
                try:
                    from zhuzhu_Copilot.core.agent_screen import virtual_desktop
                    virtual_desktop("back")
                except Exception:
                    pass
            # 生命周期钩子：任务结束（完成/停止/错误）后回调，engine.end_state 可读
            self._wf_hook("on_task_end")

    def _msg_estimate(self, m) -> int:
        """单条消息 token 估算：文本 content + tool_calls.arguments + 图片。
        原先只统计 content 文本，漏掉 tool_calls 的 arguments（常是最大的单条贡献），
        导致严重低估、压缩不触发、最终超窗 400。"""
        texts = []
        est = 0
        c = m.get("content")
        if isinstance(c, str):
            texts.append(c)
        elif isinstance(c, list):
            for x in c or []:
                if not isinstance(x, dict):
                    continue
                t = x.get("type")
                if t == "text" and x.get("text"):
                    texts.append(x["text"])
                elif t == "image_url":
                    est += agent_llm.estimate_image_tokens()
        for tc in (m.get("tool_calls") or []):
            if isinstance(tc, dict):
                fn = tc.get("function") or {}
                a = fn.get("arguments")
                if isinstance(a, str) and a:
                    texts.append(a)
        if texts:
            est += agent_llm.estimate_tokens("\n".join(texts))
        return est

    def _estimate_raw(self) -> int:
        """估算当前上下文 token（**未校准**口径：含数组文本/tool_calls 参数/图片）

        性能：本函数每轮被多次调用（发送前预计算 / 占用判定 / 压缩与硬裁判定，实测
        4 次/轮），而每条消息的估算含 C 层正则扫全文（system 提示词约万字符 → 单次
        数 ms）。故按**消息对象身份**缓存单条估算：只有新消息或内容被替换过的消息
        才重新估算，长任务每轮只多算新增的 1~2 条。

        缓存正确性：值里持有 (消息对象, content, tool_calls) 引用一起比对 ——
        ① 持有消息对象引用可防止其被回收后 id() 复用造成的错配；
        ② content/tool_calls 被**替换为新对象**（改写/修剪图片/压缩重建）时身份比对
        立即失效并重算，与不缓存的语义一致（内容字符串不可变，故身份即内容）。
        消息被压缩/裁剪后缓存里会残留旧条目，超过 _EST_CACHE_SLACK 即整体清空重建。

        校准系数不参与本函数（故缓存不受系数变化影响），由 _estimate_tokens 统一乘上。"""
        cache = self._est_cache
        msgs = self._messages
        if len(cache) > len(msgs) + _EST_CACHE_SLACK:
            cache.clear()
        total = 0
        for m in msgs:
            key = id(m)
            ent = cache.get(key)
            if (ent is not None and ent[0] is m and ent[1] is m.get("content")
                    and ent[2] is m.get("tool_calls")):
                total += ent[3]
                continue
            est = self._msg_estimate(m)
            cache[key] = (m, m.get("content"), m.get("tool_calls"), est)
            total += est
        return total

    def _tool_schema_tokens(self) -> int:
        """本轮工具 schema 的 token 开销（随请求一起发给上游，但不属于任何消息，
        旧实现完全没算 → 又一处系统性低估）。

        按 1 秒 TTL 缓存：`_all_tools()` 每轮会被 _estimate_tokens 调用多次，而 MCP
        重连 / 高效模式切换 / 任务裁剪都会改变工具表 —— TTL 兼顾"跟得上变化"与"不重复
        序列化整个工具表"。"""
        now = time.time()
        if now - self._tools_est_at < 1.0:
            return self._tools_est
        self._tools_est_at = now
        try:
            tools = self._all_tools()
            self._tools_est = (agent_llm.estimate_tokens(
                json.dumps(tools, ensure_ascii=False)) if tools else 0)
        except Exception:
            self._tools_est = 0
        return self._tools_est

    def _estimate_tokens(self) -> int:
        """当前上下文 token 估算（**校准口径**）：(本地原文估算 + 工具 schema 开销)
        × 运行期校准系数。

        所有阈值/压缩/硬裁/占用口径统一用本口径：本地估算受 tokenizer 密度差异
        影响必然有偏差，而阈值本身是按窗口（上游真实 token 口径）算出来的 ——
        只把"原始估算"和"真实口径阈值"比较，就是超窗 400 的直接来源。
        两项估算出自同一套密度口径，故共用同一个校准系数（而不是只缩放其中一项）。
        """
        return int(self._tok_factor
                   * (self._estimate_raw() + self._tool_schema_tokens()))

    def _recover_ctx_overflow(self) -> None:
        """上游「上下文超长」400 的自动恢复：上修校准系数 + 强制压缩 + 硬裁。

        上游明确告知"请求 token 数超过窗口上限"时，本地的占用判定一定是低估了
        （tokenizer 密度 / 工具 schema 等固定开销未被算入）。故这里：
        1. 直接把校准系数上修一档（下次判定立刻更保守）；
        2. 强制摘要压缩（忽略冷却，越窗优先于缓存命中）；
        3. 仍超硬上限就硬裁到上限的 60%（留足余量），保证重试的请求一定进窗口。
        """
        self._tok_factor = min(_TOK_FACTOR_MAX,
                               max(1.05, self._tok_factor * 1.25))
        self._est_cache.clear()
        try:
            self._auto_compress(keep_recent=10)
        except Exception:
            pass
        if self._estimate_tokens() > self._hard_ceiling():
            self._hard_trim(max(4096, int(self._hard_ceiling() * 0.6)))
        self._est_cache.clear()
        self._invalidate_usage()   # 上下文已重压 → 旧占用值失效
        self._compress_cooldown = 0
        self.last_estimate = self._estimate_tokens()

    def _hard_trim(self, limit: int) -> None:
        """硬上限兜底：配对完整地从最旧处丢弃整段（assistant(tool_calls)+tool 回复），
        直到估算 ≤ limit 或只剩 system。保证请求一定进窗口，避免上游 400。"""
        guard = 0
        before = len(self._messages)
        while self._estimate_tokens() > limit and len(self._messages) > 2 and guard < 2000:
            guard += 1
            removed = False
            for i in range(1, len(self._messages)):
                m = self._messages[i]
                if isinstance(m, dict) and m.get("role") == "assistant" and m.get("tool_calls"):
                    j = i + 1
                    while j < len(self._messages) and \
                            self._messages[j].get("role") == "tool":
                        j += 1
                    del self._messages[1:j]
                    removed = True
                    break
            if not removed:
                del self._messages[1:2]   # 无 tool 结构：直接丢最旧一条
        if len(self._messages) != before:
            self._invalidate_usage()   # 上下文被裁剪 → 旧的上游占用值已失真

    def _invalidate_usage(self):
        """上下文被压缩/裁剪后，最近一次上游 usage 不再代表当前上下文 →
        复位占用值，UI 自动回退本地估算（避免面板继续显示压缩前的旧占用）。"""
        for k in _USAGE_KEYS:
            self.last_usage[k] = 0
        self.last_usage_at = 0.0

    def _accum_usage(self, usage, cache=None):
        """累计上游用量，并单独记录「最近一次请求」的真实用量（last_usage）。
        tokens 为全程累加（计费口径）；last_usage 每次被覆盖（上下文占用口径）。"""
        if not usage:
            return
        # 兼容 OpenAI(prompt_tokens/completion_tokens) 与 DeepSeek/Responses
        # (input_tokens/output_tokens) 两套命名（chat_stream 已归一化，此处兜底）
        prompt = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        completion = int(usage.get("completion_tokens")
                         or usage.get("output_tokens") or 0)
        self.tokens["prompt"] += prompt
        self.tokens["completion"] += completion
        hit = miss = 0
        if cache:
            hit, miss = int(cache.get("hit", 0)), int(cache.get("miss", 0))
            self.tokens["cache_hit"] += hit
            self.tokens["cache_miss"] += miss
        self.last_usage.update({"prompt": prompt, "completion": completion,
                                "cache_hit": hit, "cache_miss": miss})
        self.last_usage_at = time.time()

    # ---------- 上下文统计（UI 唯一数据入口） ----------
    def context_stats(self) -> dict:
        """上下文 / 用量统计快照，供 token 统计面板直接渲染。

        口径（对齐主流 agent 应用）：
        - used / source：当前上下文占用 = 最近一次请求的**上游真实** 输入+输出 tokens 之和
          （标准主流口径：上下文窗口由输入与输出共同占用；服务商计费口径，最准）；
          无上游数据（尚无请求，或压缩后已失效）时回退本地估算。
        - window / budget：模型上下文窗口与**可用输入预算**（窗口 − 预留输出）。
          所有阈值以预算为基数，因此进度条刻度取自绝对 token 值而非窗口比例。
        - thresholds：warn（预警，只提示）/ compress（触发 LLM 摘要压缩）/ ceiling（发送前
          硬裁）/ recent（压缩时保留最近多少预算）。
        - 注：warn 仅作面板提示，与压缩/裁剪触发解耦（后二者按本地估算对输入预算判定）；
          used 含输出而阈值基数仍为输入预算，属展示口径与保护口径的分层。
        - window_source：窗口取值来源（1m / upstream / configured / known / inferred），
          供 UI 标注"这个上限是从哪来的"。
        - compaction：压缩次数 / 最近一次时间 / 合并条数 / 释放 token。
        - cumulative / cache_rate：全程累计消耗与提示词缓存命中率。

        只读派生值，不改动引擎状态；字段只增不改，便于后续扩展。"""
        win = max(1, int(self._ctx_window))
        budget = int(self._input_budget())
        up_prompt = int(self.last_usage.get("prompt") or 0)
        up_completion = int(self.last_usage.get("completion") or 0)
        source = "upstream" if up_prompt > 0 else "estimate"
        # 标准主流口径：上下文窗口由输入+输出共同占用，占用取二者之和（上游计费口径最准）；
        # 无上游数据（尚未请求 / 压缩后已失效）时回退本地估算（估算含全部消息，同样并入输出）。
        used = (up_prompt + up_completion) if up_prompt > 0 else self._estimate_tokens()
        hit = int(self.tokens.get("cache_hit") or 0)
        miss = int(self.tokens.get("cache_miss") or 0)
        lhit = int(self.last_usage.get("cache_hit") or 0)
        lmiss = int(self.last_usage.get("cache_miss") or 0)
        warn = self._warn_limit()
        comp = self._compress_limit()
        ceil = self._hard_ceiling()
        return {
            "used": int(used),
            "source": source,
            "window": win,
            "budget": budget,
            "ratio": min(1.0, used / win),
            "budget_ratio": min(1.0, used / max(1, budget)),
            "compress_ratio": self._compress_ratio(),
            "warn": bool(used >= warn),
            "window_source": self._window_source,
            "long_1m": bool(self._long_1m),
            "max_output": int(self._max_output or _DEFAULT_MAX_OUTPUT),
            "thresholds": {"warn": int(warn), "compress": int(comp),
                           "ceiling": int(ceil),
                           "recent": int(self._recent_budget())},
            "compaction": {"count": int(self._compaction.get("count") or 0),
                           "last_at": float(self._compaction.get("last_at") or 0.0),
                           "last_merged": int(self._compaction.get("last_merged") or 0),
                           "last_saved": int(self._compaction.get("last_saved") or 0)},
            "last": {"prompt": up_prompt,
                     "completion": int(self.last_usage.get("completion") or 0),
                     "cache_hit": lhit, "cache_miss": lmiss,
                     "at": float(self.last_usage_at)},
            "cumulative": {"prompt": int(self.tokens.get("prompt") or 0),
                           "completion": int(self.tokens.get("completion") or 0),
                           "total": int((self.tokens.get("prompt") or 0)
                                        + (self.tokens.get("completion") or 0)),
                           "cache_hit": hit, "cache_miss": miss},
            "cache_rate": (hit / (hit + miss)) if (hit + miss) > 0 else None,
            "messages": len(self._messages),
            "model": str(getattr(self.llm, "model", "") or ""),
        }
