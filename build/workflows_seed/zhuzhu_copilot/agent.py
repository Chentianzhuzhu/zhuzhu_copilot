"""agent.py — zhuzhu Copilot（默认工作流，继承原有人格）

默认工作流 = 原内置 zhuzhu Copilot 人格与全能力；本文件只显式继承，不替换。
预置子 Agent（explorer_project_agent / sub_coding_agent）在 subagents.json 注册。
"""

AGENT_NAME = "zhuzhu Copilot"


def build_system_prompt(agent_name: str = "", extra_skills: str = "") -> str:
    """继承内置 zhuzhu Copilot 人格（= 默认工作流人格，保持原体验不变）。

    必须把 settings.json 里的运行时开关原样转交给内置实现：本工作流是「继承」而非
    「替换」人设，若只传 agent_name，用户在这些开关上的设置就会对本工作流全部失效——
    最典型的是「高效模式」：开着高效模式却仍收到整份工具手册/技能规范（与工具表
    自相矛盾，既浪费 token 又诱导模型调用被剔除的工具）；记忆开关同此。

    text_only（纯文本模型）与 direct（直行模式）不在 settings.json 里（前者是运行时按
    模型判定的、后者在 QSettings），此处不臆测，交回内置默认值。
    """
    try:
        from zhuzhu_Copilot.core import agent_skills
        s = agent_skills.load_settings() or {}
        return agent_skills.build_system_prompt(
            agent_name,
            memory_enabled=bool(s.get("memory_enabled", True)),
            efficient=bool(s.get("efficient_mode", False)))
    except Exception:
        return ""


def on_task_start(engine) -> None:
    pass


def on_task_end(engine) -> None:
    pass
