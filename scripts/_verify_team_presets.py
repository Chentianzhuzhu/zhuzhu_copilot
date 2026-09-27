"""工作团预设真实端到端验证（不 mock，真实 API/文件操作）：

1. create_builtin_workflow 把 product_manager / zhuzhu_copilot 预设复制到（临时）工作流目录
2. product_manager/agent.py 的 on_task_start 钩子真实激活团队（开启共同上下文空间）
3. zhuzhu_copilot 预设复制出的 subagents.json 含 explorer_project_agent / sub_coding_agent
4. 注册式子 Agent 读取出的 allow_chat / share_context 权限字段真实生效
运行目录被重定向到临时沙箱，不触碰用户真实 ~/.winapp_migrator。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from winapp_migrator.core import (agent_context, agent_skills, agent_subagent,
                                  agent_team, agent_workflow)


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="team_verify_")
    # 重定向工作流根目录与团队配置文件到沙箱（真实 API 创建，仅落盘位置重定向）
    from pathlib import Path
    wf_root = Path(tmp) / "workflows"
    wf_root.mkdir()
    agent_workflow.workflows_root = lambda: wf_root
    agent_workflow.workflow_dir = lambda name: wf_root / name
    agent_team._TEAM_FILE = Path(tmp) / "team.json"
    agent_context.reset_all()

    fails = []

    # 1. 创建 product_manager 与 zhuzhu_copilot 工作流（真实复制预设）
    ok, msg = agent_workflow.create_builtin_workflow(
        "product_manager", name="product_manager", description="产品经理")
    if not ok or not (wf_root / "product_manager" / "agent.py").is_file():
        fails.append(f"创建 product_manager 失败: {msg}")
    ok, msg = agent_workflow.create_builtin_workflow(
        "zhuzhu_copilot", name="zhuzhu_copilot", description="默认工作流")
    if not ok or not (wf_root / "zhuzhu_copilot" / "subagents.json").is_file():
        fails.append(f"创建 zhuzhu_copilot 失败: {msg}")

    # 2. 团队配置默认值
    cfg = agent_team.team_config()
    if cfg["leader"] != "product_manager":
        fails.append(f"团队领导者应为 product_manager，实际 {cfg['leader']}")

    # 3. on_task_start 真实激活团队（模拟引擎：带 _messages 的最小对象）
    hooks = agent_workflow.agent_hooks("product_manager")
    mod = hooks.get("mod")
    if mod is None or not hasattr(mod, "on_task_start"):
        fails.append("product_manager/agent.py 未加载或缺少 on_task_start")
    else:
        class _FakeEng:
            _messages = [{"role": "user", "content": "帮我总派发：开发一个登录模块"}]
        mod.on_task_start(_FakeEng())
        if not agent_team.team_active():
            fails.append("on_task_start 后团队模式未激活（空间未开启）")
        if not agent_context.has_space(agent_context.DEFAULT_SPACE):
            fails.append("团队空间未落到默认空间 id")

    # 4. zhuzhu_copilot 预置子 Agent 真实读出（权限字段）
    recs = agent_subagent.registered_subagents("zhuzhu_copilot")
    by_name = {r["name"]: r for r in recs}
    for n in ("explorer_project_agent", "sub_coding_agent"):
        if n not in by_name:
            fails.append(f"zhuzhu_copilot 缺少预置子 Agent: {n}")
        elif by_name[n].get("allow_chat") is not True or by_name[n].get("share_context") is not True:
            fails.append(f"{n} 权限字段未写入（allow_chat/share_context 应为 True）")

    # 5. 产品经理 tools.py 的 SUB_AGENT_ALLOWED 真实生效
    extra = agent_workflow.workflow_extra_whitelist("product_manager")
    for t in ("chat_with", "look_context", "pause_agent", "resume_agent", "warn_agent"):
        if t not in extra:
            fails.append(f"product_manager SUB_AGENT_ALLOWED 缺少 {t}")

    if fails:
        print("FAIL:")
        for f in fails:
            print(" -", f)
        return 1
    print(f"OK: 团队预设真实验证通过（临时沙箱 {tmp}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
