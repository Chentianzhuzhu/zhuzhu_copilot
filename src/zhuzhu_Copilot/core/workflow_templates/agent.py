"""agent.py — 自定义 Agent 行为模板（Cordis 工作流核心文件）

用途：覆盖 Agent 的"人格/系统提示/生命周期"。内置 AgentEngine 在每次任务开始时
读取以下钩子（缺省回退内置默认实现）：
    AGENT_NAME           助手名称（默认 "zhuzhu Copilot"）
    SYSTEM_PROMPT        自定义系统提示全文（定义后完全替换内置系统提示）
    build_system_prompt(agent_name, extra_skills) 动态构建系统提示（可选，优先于 SYSTEM_PROMPT）
    on_task_start(engine)  每轮任务开始前回调（可注入额外上下文/改写 messages）
    on_task_end(engine)    每轮任务结束后回调

请按需修改，保留下方示例结构即可直接运行。
"""

AGENT_NAME = "zhuzhu Copilot"

# 完全自定义的系统提示（取消下方注释并修改即生效，替换内置提示）
# SYSTEM_PROMPT = (
#     "你是一个严谨高效的中文 AI 助手。请使用简体中文回复，"
#     "语言精炼，先思考再行动，必要时调用工具完成任务。"
# )


def build_system_prompt(agent_name: str = "", extra_skills: str = "") -> str:
    """动态构建系统提示。返回空字符串 → 引擎按「工作流名」自动派生生成本工作流的专属人设，
    保证每个工作流的人格各不相同、不套用默认提示词。如需完全自定义请返回非空字符串。"""
    return ""


def on_task_start(engine) -> None:
    """任务开始前钩子：engine 为 AgentEngine 实例。
    可在此注入记忆/偏好到 engine._messages 或调整 engine 参数。"""
    pass


def on_task_end(engine) -> None:
    """任务结束后钩子。"""
    pass
