"""真实 API 验证：用户要求「创建一个子 agent」时，主 Agent 是否走注册式子 Agent 通道。

不 mock：读真实 settings.json（解密 model 配置）+ 真实工具 schema + 真实 LLM 调用，
并按主 Agent 的真实多轮流程把工具结果喂回去（list_sub_agents → register_sub_agent）。
注册写入被重定向到临时工作流目录，不触碰用户真实数据。

期望：N 轮内调用 register_sub_agent（或 register_agent_network(op=register_subagent)）；
不接受：create_agent / register_agent_network(op=create_agent)（人格 Agent，主 Agent 调不到）。
"""
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from zhuzhu_Copilot.core import (
    agent_engine,
    agent_llm,
    agent_skills,
    agent_tools,
    agent_workflow,
)

WF = "route_probe"
CASES = [
    "帮我创建一个子 agent，用于审查代码里的安全问题",
    "帮我创建一个子 agent，可以并行处理多个文件",       # 命中 subagent 类别 → 工具被裁剪
    "给我加一个下属 agent，负责把需求文档整理成任务清单",  # 命中 doc 类别 → 工具被裁剪
]
MAX_ROUNDS = 4
# 只允许执行这些"看现状/注册"工具：其余工具只回提示（避免脚本改动用户环境）
RUN_OK = {"list_sub_agents", "list_agents_network", "inspect_customization", "list_workflows"}
REGISTER = {"register_sub_agent"}


def _setup_sandbox() -> None:
    """把工作流根目录指到临时目录：注册式写入落在沙箱里，验证结束即丢弃。"""
    tmp = Path(tempfile.mkdtemp(prefix="subagent_route_"))
    wf_dir = tmp / WF
    wf_dir.mkdir(parents=True)
    (wf_dir / "workflow.json").write_text(
        json.dumps({"name": WF, "enabled": True}), encoding="utf-8")
    (tmp / ".active").write_text(WF, encoding="utf-8")
    agent_workflow.workflows_root = lambda: tmp
    agent_workflow._ROOT_READY = True
    print(f"沙箱工作流目录：{wf_dir}")


def _client() -> tuple:
    cfg = agent_llm.decrypt_model_config(agent_skills.load_settings().get("model") or {})
    model = agent_llm.resolve_model(cfg, "medium", vision_needed=False)
    sel = agent_llm.provider_for_model(cfg, model)
    llm = agent_llm.LLMClient(
        base_url=(sel or {}).get("base_url") or agent_llm.DEFAULT_BASE_URL,
        api_key=(sel or {}).get("api_key") or agent_llm.DEFAULT_API_KEY,
        model=model,
        protocol=(sel or {}).get("protocol") or "chat")
    return llm, model


def _run_case(llm, system: str, text: str) -> tuple:
    """一轮真实对话循环：返回 (是否最终注册, 是否误建人格 Agent, 轨迹)"""
    eng = agent_engine.AgentEngine(llm, text_only=True)
    eng.workflow = WF
    eng._task_groups = agent_engine._detect_task_groups(text)
    tools = eng._all_tools()
    names = {t["function"]["name"] for t in tools}
    assert REGISTER <= names, f"创建通道被任务裁剪剔除了：{sorted(REGISTER - names)}"
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": text}]
    trail, registered, made_persona = [], False, False
    for _ in range(MAX_ROUNDS):
        res = llm.chat_stream(messages, tools=tools, tool_choice="auto")
        calls = res.get("tool_calls") or []
        messages.append({"role": "assistant", "content": res.get("text") or None,
                         "tool_calls": calls})
        if not calls:
            break
        for c in calls:
            name = c["function"]["name"]
            args = c["function"].get("arguments") or "{}"
            trail.append(name)
            if name in ("create_agent",):
                made_persona = True
                out = "（验证脚本拒绝执行 create_agent）"
            elif name == "register_agent_network":
                op = (json.loads(args) if args.strip().startswith("{") else {}).get("op")
                if op == "create_agent":
                    made_persona = True
                    out = "（验证脚本拒绝执行 op=create_agent）"
                else:
                    out = agent_tools.execute_tool(name, json.loads(args), workflow=WF).get("text", "")
                    registered = registered or op in (None, "register_subagent")
            elif name in REGISTER:
                out = agent_tools.execute_tool(name, json.loads(args), workflow=WF).get("text", "")
                registered = True
            elif name in RUN_OK:
                out = agent_tools.execute_tool(name, json.loads(args), workflow=WF).get("text", "")
            else:
                out = "（验证脚本不执行该工具，请直接完成创建子 agent 的动作）"
            messages.append({"role": "tool", "tool_call_id": c["id"], "content": str(out)[:4000]})
        if registered:
            break
    return registered, made_persona, trail


def _verify_assignment(llm, system: str) -> int:
    """真实 API 验证：主 Agent 是否对「注册式子 Agent」与「临时子任务」都分配
    shared_context 开关、并把 context 传给子 Agent。

    按真实多轮流程走：准备性工具（读文件/记忆等）真执行，直到模型发出目标派发工具，
    此时只检查其参数、不真跑子 Agent（避免真起子 Agent 循环）。"""
    from zhuzhu_Copilot.core import agent_subagent
    ok_reg, msg = agent_subagent.register_subagent(
        "menxia", "封驳审议", "审议所付方案并指出风险", workflow=WF)
    assert ok_reg, msg
    # 准备性只读工具：真执行以推进真实流程（子 Agent 派发工具只取参数，不执行）
    prep = {"read_file", "list_directory", "search_files", "grep", "load_memory",
            "list_sub_agents", "list_agents_network", "inspect_customization",
            "list_workflows", "shared_context", "update_todo", "list_todo"}
    cases = [
        (("必须调用 menxia 子 agent 来完成对这段方案的审议：「导出功能先做 CSV，"
          "下个版本再做 PDF」。调用时让它加入共享上下文空间，并把上面这段方案原文"
          "作为 context 传给它。不要自己给出审议结论、不要读文件、不要派发其他子任务。"),
         "sub_menxia", "context"),
        (("把两个互不依赖的小任务并行派给子 agent：①简述导出模块 A 的职责；"
          "②简述导出模块 B 的职责。要求：开启共享上下文空间，"
          "并把「A 与 B 同属导出功能」这句作为每个子任务的 context 传下去；不要读文件"),
         "dispatch_sub_agents", "tasks"),
    ]
    failed = 0
    for text, target, expect_key in cases:
        eng = agent_engine.AgentEngine(llm, text_only=True)
        eng.workflow = WF
        eng._task_groups = agent_engine._detect_task_groups(text)
        tools = eng._all_tools()
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": text}]
        args, got, trail = {}, "（未派发）", []
        for _ in range(6):
            res = llm.chat_stream(messages, tools=tools, tool_choice="auto")
            calls = res.get("tool_calls") or []
            messages.append({"role": "assistant", "content": res.get("text") or None,
                             "tool_calls": calls})
            if not calls:
                break
            hit = False
            for c in calls:
                name = c["function"]["name"]
                trail.append(name)
                if name == target:
                    got, hit = name, True
                    try:
                        args = json.loads(c["function"].get("arguments") or "{}")
                    except Exception:
                        args = {}
                    break
                if name == "ask_user":
                    out = "（验证脚本代答：按你的判断继续）"
                elif name in prep:
                    try:
                        out = agent_tools.execute_tool(
                            name, json.loads(c["function"].get("arguments") or "{}"),
                            workflow=WF).get("text", "")
                    except Exception as e:
                        out = f"[工具错误] {name}: {e}"
                else:
                    out = "（验证脚本不执行该工具，请直接完成本次派发）"
                messages.append({"role": "tool", "tool_call_id": c["id"],
                                 "content": str(out)[:4000]})
            if hit:
                break
        shared = args.get("shared_context") is True
        if expect_key == "context":
            ctx_ok = bool(str(args.get("context") or "").strip())
        else:
            ctx_ok = bool(args.get("tasks")) and all(
                str(t.get("context") or "").strip() for t in args["tasks"] if isinstance(t, dict))
        ok = got == target and shared and ctx_ok
        print(f"[{'通过' if ok else '失败'}] {text}\n"
              f"  派发工具={got}  分配 shared_context={shared}  传参 context={ctx_ok}\n"
              f"  轨迹={trail}\n  参数={json.dumps(args, ensure_ascii=False)[:220]}")
        failed += 0 if ok else 1
    return failed


def main() -> int:
    _setup_sandbox()
    llm, model = _client()
    print(f"工作流={WF}  模型={model}\n")
    system = agent_skills.build_system_prompt(text_only=True)
    print("== 一、创建子 agent 的形态（必须为注册式）==")
    failed = 0
    for text in CASES:
        try:
            registered, made_persona, trail = _run_case(llm, system, text)
        except Exception as e:
            print(f"[失败] {text}\n  异常：{e}")
            failed += 1
            continue
        ok = registered and not made_persona
        print(f"[{'通过' if ok else '失败'}] {text}\n"
              f"  注册式={registered}  误建人格 Agent={made_persona}\n  工具轨迹={trail}")
        failed += 0 if ok else 1
    print("\n== 二、主 Agent 对子 Agent 的共享分配与 context 传参 ==")
    failed += _verify_assignment(llm, system)
    print(f"\n失败 {failed} / {len(CASES) + 2}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
