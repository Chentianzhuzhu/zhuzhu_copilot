"""tools.py — 「三省六部制度」工作流专属工具（真实读取注册表，不硬编码编队）。

暴露契约：
    TOOLS         工具定义列表（OpenAI function calling schema）
    execute_tool(name, args, allow_dangerous=False) -> {"text", "images"}

提供的工具：
    court_roster   读取当前工作流已注册的子 Agent，输出三省六部编队与各自的真实工具白名单
                   （数据来源 subagents.json，新增/调整部门只需改注册表，工具无需改代码）
    court_dispatch 把中书省的派工单（部门 + 目标 + 上下文）校验并规范化为可直接交给
                   dispatch_sub_agents 的任务清单，避免子 Agent 名写错、共享开关漏传
"""

import json

TOOLS = [
    {"type": "function",
     "function": {
         "name": "court_roster",
         "description": "稽三省六部编队之现状：本工作流已注册之子 Agent（诸司）、所载职掌、"
                        "可用器具之限与共同上下文空间之默认开关。遣司之前先以之核其司名，不得凭记忆妄拟。",
         "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function",
     "function": {
         "name": "court_dispatch",
         "description": "校派工之单而正其体，成可付 dispatch_sub_agents 之任务列（返 JSON 任务数组）。"
                        "assignments 为 [{sub, goal, context}]：sub 乃诸司之于子 Agent 名（如 ministry_revenue），"
                        "goal 为本次承办之的，context 为承办所须已阅之文与所守之约。"
                        "逐条核其司是否已注册，并统带共同上下文空间之开关，以免漏载致诸司不见彼此所出。",
         "parameters": {"type": "object",
                        "properties": {
                            "assignments": {"type": "array",
                                            "description": "派工之单（列表）",
                                            "items": {"type": "object",
                                                      "properties": {
                                                          "sub": {"type": "string",
                                                                  "description": "诸司之子 Agent 名（court_roster 可稽）"},
                                                          "goal": {"type": "string",
                                                                   "description": "本次承办之的与所须（须令其以文言文奏报）"},
                                                          "context": {"type": "string",
                                                                      "description": "承办所须之上下文（已阅之内容/所守之约束）"},
                                                          "title": {"type": "string",
                                                                    "description": "任务题名（可省，缺省用该司之职掌说明）"}},
                                                      "required": ["sub", "goal"]}},
                            "shared_context": {"type": "boolean",
                                               "description": "本批是否入共同上下文空间（缺省 true）"},
                            "space": {"type": "string",
                                      "description": "共同上下文空间之 id（缺省用当前活跃空间）"}},
                        "required": ["assignments"]}}},
]


def _sub():
    """惰性导入子 Agent 注册表（避免工具模块加载时拉起整条引擎依赖链）"""
    from zhuzhu_Copilot.core import agent_subagent
    return agent_subagent


def _roster() -> dict:
    subs = _sub().registered_subagents("")
    if not subs:
        return {"text": "本工作流尚未注册任何子 Agent（可以 register_sub_agent 增设诸司）。",
                "images": []}
    lines = ["# 三省六部编队（本工作流已注册诸司）"]
    for s in subs:
        line = f"- {s['name']}：{s['description'] or '（未载职掌）'}"
        if s.get("allowed"):
            line += f"\n    可用器具：{', '.join(s['allowed'])}"
        if s.get("shared_context") is True:
            line += "\n    共同上下文空间：默认入共享"
        elif s.get("shared_context") is False:
            line += "\n    共同上下文空间：默认独处"
        lines.append(line)
    lines.append("")
    lines.append("遣司用 dispatch_sub_agents（任务条 sub=<司名>），或先经 court_dispatch 理其派工之单。")
    return {"text": "\n".join(lines), "images": []}


def _dispatch(args: dict) -> dict:
    raw = args.get("assignments")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as e:
            return {"text": f"[court_dispatch] assignments 非合法 JSON：{e}", "images": []}
    if not isinstance(raw, list) or not raw:
        return {"text": "[court_dispatch] 未见 assignments（派工之单）", "images": []}
    roster = {s["name"]: s for s in _sub().registered_subagents("")}
    if not roster:
        return {"text": "[court_dispatch] 本工作流尚未注册任何诸司", "images": []}
    shared = bool(args.get("shared_context", True))
    space = str(args.get("space") or "").strip()
    tasks, errors = [], []
    for i, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            errors.append(f"第 {i} 条非对象之形")
            continue
        sub = str(item.get("sub") or "").strip()
        goal = str(item.get("goal") or "").strip()
        if not sub or not goal:
            errors.append(f"第 {i} 条缺 sub 或 goal")
            continue
        conf = roster.get(sub)
        if conf is None:
            errors.append(f"第 {i} 条之司「{sub}」未注册")
            continue
        task = {"title": str(item.get("title") or conf.get("description") or sub),
                "goal": goal,
                "sub": sub,
                "context": str(item.get("context") or "").strip(),
                "shared_context": shared}
        if space:
            task["space"] = space
        tasks.append(task)
    if errors:
        valid = ", ".join(sorted(roster))
        return {"text": ("[court_dispatch] 派工之单校验未过：\n- " + "\n- ".join(errors)
                         + f"\n可用诸司：{valid}"), "images": []}
    body = json.dumps({"tasks": tasks}, ensure_ascii=False, indent=2)
    head = (f"# 派工之单已具（{len(tasks)} 条，共同上下文空间"
            f"{'已开' if shared else '未开'}）\n"
            "以下 tasks 之列，径作 dispatch_sub_agents 之 tasks 参数即可（sub 已备，"
            "shared_context 已依本批之设载入）：\n")
    return {"text": head + body, "images": []}


def execute_tool(name: str, args: dict, allow_dangerous: bool = False) -> dict:
    """工具执行入口：返回 {"text", "images"}。未匹配的名字请交给内置兜底。"""
    args = args or {}
    try:
        if name == "court_roster":
            return _roster()
        if name == "court_dispatch":
            return _dispatch(args)
    except Exception as e:   # noqa: BLE001 - 工具边界统一收口为文本结果，避免异常外抛中断对话
        return {"text": f"[{name}] 执行失败: {e}", "images": []}
    return {"text": f"[tools.py 未处理] 未知工具: {name}", "images": []}
