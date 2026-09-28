"""tools.py — 自定义工具集模板（Cordis 工作流核心文件）

用途：替换/扩展 Agent 可用工具。内置 agent_tools 会把你声明的工具：
    - 与内置工具同名 → 覆盖内置实现
    - 新增名字      → 追加到工具集
需暴露两个接口：
    TOOLS         工具定义列表（OpenAI function calling schema）
    execute_tool(name, args, allow_dangerous=False) -> {"text", "images"}

下方示例提供 2 个真实工具（今天日期 / 计算器），可运行、可修改。
"""

import time

# 工具定义（OpenAI function schema）
TOOLS = [
    {"type": "function",
     "function": {
         "name": "today_date",
         "description": "返回今天的日期与星期（ISO 格式）",
         "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function",
     "function": {
         "name": "calc",
         "description": "执行四则运算表达式，返回计算结果",
         "parameters": {"type": "object",
                        "properties": {"expr": {"type": "string",
                                                "description": "如 12.5*4+3"}},
                        "required": ["expr"]}}},
]


def execute_tool(name: str, args: dict, allow_dangerous: bool = False) -> dict:
    """工具执行入口：返回 {"text", "images"}。未匹配的名字请交给内置兜底。"""
    args = args or {}
    if name == "today_date":
        return {"text": time.strftime("%Y-%m-%d %A"), "images": []}
    if name == "calc":
        try:
            expr = str(args.get("expr", ""))
            if not expr:
                return {"text": "缺少 expr 参数", "images": []}
            # 仅允许数字与四则运算，杜绝任意代码执行
            import re
            if not re.fullmatch(r"[\d\s+\-*/().]+", expr):
                return {"text": "表达式包含非法字符", "images": []}
            result = eval(expr, {"__builtins__": {}}, {})  # noqa: S307
            return {"text": f"{expr} = {result}", "images": []}
        except Exception as e:
            return {"text": f"计算失败: {e}", "images": []}
    return {"text": f"[tools.py 未处理] 未知工具: {name}", "images": []}
