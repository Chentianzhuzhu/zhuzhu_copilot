"""agent.py — zhuzhu Copilot（默认工作流，继承原有人格）

默认工作流 = 原内置 zhuzhu Copilot 人格与全能力；本文件只显式继承，不替换。
预置子 Agent（explorer_project_agent / sub_coding_agent）在 subagents.json 注册。
"""

AGENT_NAME = "zhuzhu Copilot"


def build_system_prompt(agent_name: str = "", extra_skills: str = "") -> str:
    """继承内置 zhuzhu Copilot 人格（= 默认工作流人格，保持原体验不变）。"""
    try:
        from winapp_migrator.core import agent_skills
        return agent_skills.build_system_prompt(agent_name)
    except Exception:
        return ""


def on_task_start(engine) -> None:
    pass


def on_task_end(engine) -> None:
    pass
