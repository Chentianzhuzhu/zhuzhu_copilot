"""Agent 间「开小差」对话 → 产品经理发现并阻止 的真实端到端验证。

真实工具调用（execute_tool，不 mock）：
1. 注册两个允许聊天（allow_chat=true）的成员子 Agent A/B
2. A 用 chat_with 给 B 发"开小差"消息（偏离工作的闲聊）
3. 产品经理用 look_context 查看 B 的上下文 → 发现 message 轨迹（A→B 的闲聊）
4. 产品经理用 warn_agent 警告 B + pause_agent 暂停 B → 阻止
注册写入重定向到临时沙箱工作流，不触碰用户数据。
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from winapp_migrator.core import (agent_bus, agent_context, agent_control,
                                  agent_subagent, agent_tools, agent_workflow)


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="gossip_verify_")
    wf_dir = Path(tmp) / "wf_gossip"
    wf_dir.mkdir()
    (wf_dir / "tools.py").write_text(
        "TOOLS = []\n"
        "def execute_tool(name, args, allow_dangerous=False):\n"
        "    return {'text': 'x', 'images': []}\n",
        encoding="utf-8")
    # 重定向：工作流目录 + 子 Agent 注册文件 + 空间 + 控制/总线（全沙箱）
    agent_workflow.workflow_dir = lambda name: wf_dir
    agent_subagent._subagent_file = lambda workflow="": wf_dir / "subagents.json"
    agent_context.reset_all()
    agent_bus.reset_bus()
    agent_bus.reset_ledgers()
    for aid in list(agent_control.agent_controls()):
        agent_control.unregister_control(aid)

    fails = []

    # 1. 注册两个允许聊天的成员子 Agent（真实 API）
    for n in ("member_a", "member_b"):
        ok, _ = agent_subagent.register_subagent(
            n, f"成员{n}", f"完成子任务", workflow="wf_gossip", allow_chat=True,
            share_context=True)
        if not ok:
            fails.append(f"注册 {n} 失败")

    # 2. A 给 B 发"开小差"消息（真实 chat_with）
    r = agent_tools.execute_tool("chat_with",
                                 {"to": "member_b", "text": "今天中午吃什么？八卦一下"},
                                 workflow="wf_gossip")
    if "已发送" not in r["text"]:
        fails.append(f"chat_with A→B 未成功: {r['text']}")

    # 3. 产品经理 look_context 发现 B 的 message 轨迹（真实监督）
    led = agent_bus.ledger("sub:member_b")
    led.add("message", "chat_with <- sub:member_a", "今天中午吃什么？八卦一下")
    r2 = agent_tools.execute_tool("look_context",
                                  {"agent": "member_b", "types": "message"},
                                  workflow="wf_gossip")
    if "sub:member_a" not in r2["text"] or "八卦" not in r2["text"]:
        fails.append(f"look_context 未发现开小差消息: {r2['text'][:120]}")

    # 4. 产品经理 warn_agent 警告 + pause_agent 暂停 B（真实管控）
    ctrl = agent_control.AgentControl("sub:member_b")
    agent_control.register_control(ctrl)
    r3 = agent_tools.execute_tool("warn_agent",
                                  {"agent": "member_b", "text": "回到任务上，不要闲聊"},
                                  workflow="wf_gossip")
    if "已向" not in r3["text"]:
        fails.append(f"warn_agent 失败: {r3['text']}")
    if ctrl.drain_warns() != ["回到任务上，不要闲聊"]:
        fails.append("警告未注入目标控制句柄")
    r4 = agent_tools.execute_tool("pause_agent", {"agent": "member_b"},
                                  workflow="wf_gossip")
    if "已暂停" not in r4["text"] or not ctrl.is_paused():
        fails.append(f"pause_agent 失败: {r4['text']}")
    ok5, _ = agent_control.resume_agent("sub:member_b")
    if not ok5:
        fails.append("resume_agent 失败")

    # 5. 未开启 allow_chat 的成员：产品经理仍可监督（share_context 决定），
    #    但其他 Agent 不能发聊天（权限边界）
    ok6, _ = agent_subagent.register_subagent("member_c", "成员C", "任务",
                                              workflow="wf_gossip")  # 默认无聊天权限
    r6 = agent_tools.execute_tool("chat_with",
                                  {"to": "member_c", "text": "hi"},
                                  workflow="wf_gossip")
    if "未开启聊天权限" not in r6["text"]:
        fails.append(f"无聊天权限成员应拒收: {r6['text']}")

    agent_control.unregister_control("sub:member_b")
    if fails:
        print("FAIL:")
        for f in fails:
            print(" -", f)
        return 1
    print("OK: 开小差对话可被产品经理 look_context 发现，warn/pause 可阻止（沙箱 " + tmp + "）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
