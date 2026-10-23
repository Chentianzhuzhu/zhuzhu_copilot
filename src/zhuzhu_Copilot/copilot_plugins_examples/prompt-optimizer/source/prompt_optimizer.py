"""提示词优化（Copilot 插件 prompt-optimizer）。

两部分能力：
  · `optimize()` —— 调用当前配置的模型，把用户粗略的提示词改写成结构化、可执行的
    高质量提示词（角色 / 目标 / 输入 / 约束 / 输出格式 / 验收标准），并给出改动要点；
    由工具 `optimize_prompt` 调用；
  · `PROMPT_DISCIPLINE` —— 一段「提示词优化纪律」，由插件**替换** `agent_skills`
    里 `_base_prompt_parts` 的最后一行（`return parts`）追加到系统提示词尾部，
    让模型在日常对话里主动把模糊需求问清、把长任务拆成可验收的小步。

本文件是插件 `source/` 下的可读副本；插件实际执行的是 source_ops.json 里
create_file 的 content（两者内容一致）。
"""

#: 追加到系统提示词尾部的纪律块（替换 _base_prompt_parts 末行时插入）
PROMPT_DISCIPLINE = (
    "提示词优化纪律（插件 prompt-optimizer）：\n"
    "· 用户需求模糊时，先用一句话确认目标与验收标准，再动手；不要靠猜执行。\n"
    "· 把长任务拆成可验收的小步，每步给出「做什么 / 怎么算完成」。\n"
    "· 需要用户补充信息时，一次只问最关键的一到两个问题，并给出默认值建议。\n"
    "· 用户要求优化提示词时，调用 optimize_prompt 工具，不要凭空改写。"
)

_SYS_PROMPT = (
    "你是提示词工程专家。把用户给的提示词改写成可直接使用的高质量提示词。\n"
    "改写要求：\n"
    "1. 补齐角色、目标、上下文/输入、约束、输出格式、验收标准六要素（原文已有则保留原意）；\n"
    "2. 保留用户原语言（中文问就中文答）；不要编造用户没提供的业务事实；\n"
    "3. 用 Markdown 小标题组织，可直接复制粘贴使用；\n"
    "4. 输出严格 JSON（不要代码块包裹）：\n"
    '{"prompt":"优化后的完整提示词","notes":["改动要点1","改动要点2"]}\n'
)


def _client():
    """按当前模型配置创建 LLM 客户端（与其他功能同源）。"""
    from zhuzhu_Copilot.core import agent_llm
    cfg = agent_llm.load_model_config()
    return agent_llm.LLMClient(cfg.get("base_url"), cfg.get("api_key"),
                               cfg.get("model") or agent_llm.DEFAULT_MODEL)


def _parse_json(text: str) -> dict:
    import json
    import re
    s = str(text or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n?", "", s)
        s = re.sub(r"\n?```$", "", s)
    try:
        data = json.loads(s)
        return data if isinstance(data, dict) else {}
    except ValueError:
        m = re.search(r"\{[\s\S]*\}", s)
        if not m:
            return {}
        try:
            data = json.loads(m.group(0))
            return data if isinstance(data, dict) else {}
        except ValueError:
            return {}


def optimize(text: str, goal: str = "") -> dict:
    """把提示词改写成高质量版本。返回 {prompt, notes}；失败抛异常由调用方兜底。"""
    from zhuzhu_Copilot.core import agent_llm
    src = str(text or "").strip()
    if not src:
        raise ValueError("提示词为空")
    user = src if not goal else ("%s\n\n【本次优化目标】%s" % (src, goal))
    res = _client().chat(
        [{"role": "system", "content": _SYS_PROMPT},
         {"role": "user", "content": user}],
        max_tokens=1600, timeout=agent_llm.GEN_TIMEOUT_S)
    data = _parse_json((res or {}).get("text") or "")
    prompt = str(data.get("prompt") or "").strip()
    notes = [str(n).strip() for n in (data.get("notes") or []) if str(n).strip()]
    if not prompt:
        raise ValueError("模型未返回可用的优化结果")
    return {"prompt": prompt, "notes": notes}


def format_result(result: dict) -> str:
    """把 optimize() 的结果整理成给用户看的文本。"""
    out = ["**优化后的提示词**\n", result.get("prompt") or ""]
    notes = result.get("notes") or []
    if notes:
        out.append("\n**改动要点**")
        out.extend("- " + n for n in notes)
    return "\n".join(out)
