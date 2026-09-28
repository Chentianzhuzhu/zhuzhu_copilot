"""tools.py — 产品经理工作流的专属工具声明。

SUB_AGENT_ALLOWED：本工作流的子 Agent 额外可用工具（在子 Agent 白名单基础上放行
内置协作工具，使成员可直接参与团队沟通/监督）。无自定义工具（TOOLS 为空列表）。
"""

TOOLS = []


def execute_tool(name: str, args: dict, allow_dangerous: bool = False) -> dict:
    return {"text": f"[product_manager] 无自定义工具: {name}", "images": []}


# 子 Agent 扩展白名单：本工作流的子 Agent 可额外调用这些内置协作工具
SUB_AGENT_ALLOWED = ("chat_with", "look_context", "pause_agent",
                     "resume_agent", "warn_agent")
