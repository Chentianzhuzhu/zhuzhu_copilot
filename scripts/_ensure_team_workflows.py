"""确保默认工作团（6 个工作流）在用户工作流目录真实存在。

预设（workflow_templates/presets/*）只是模板，必须经 create_builtin_workflow 复制到
~/.winapp_migrator/workflows/ 才会出现在界面上。本脚本按团队配置缺什么补什么：
  product_manager（领导者）/ zhuzhu_copilot / frontend_design / product_dev /
  backend_dev / product_debug
并确保 team.json（团队配置：领导者 + 成员）落盘。全部为真实 API 调用。
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from winapp_migrator.core import agent_team, agent_workflow

# 默认团队：预设 id → 展示名
TEAM_PRESETS = [
    ("product_manager", "产品经理（工作团核心领导者）"),
    ("zhuzhu_copilot", "默认工作流（继承原有人格 + 预置子 Agent）"),
    ("frontend_design", "前端设计（工作团成员）"),
    ("product_dev", "产品开发（工作团成员）"),
    ("backend_dev", "后端开发（工作团成员）"),
    ("product_debug", "产品调试（工作团成员）"),
]


def main() -> int:
    existing = {w.get("name") for w in agent_workflow.list_workflows()}
    created, skipped, fails = [], [], []
    for preset_id, desc in TEAM_PRESETS:
        if preset_id in existing:
            skipped.append(preset_id)
            continue
        ok, msg = agent_workflow.create_builtin_workflow(preset_id, name=preset_id,
                                                         description=desc)
        if ok:
            created.append(preset_id)
        else:
            fails.append(f"{preset_id}: {msg}")

    # 团队配置落盘（缺省即默认团队，仍显式写入便于用户查看/修改）
    cfg = agent_team.team_config()
    if cfg.get("leader") != agent_team.DEFAULT_TEAM["leader"] or \
            sorted(cfg.get("members") or []) != sorted(agent_team.DEFAULT_TEAM["members"]):
        ok, msg = agent_team.save_team_config(dict(agent_team.DEFAULT_TEAM))
        if not ok:
            fails.append(f"team.json: {msg}")

    print(f"已创建: {created or '无'}")
    print(f"已存在跳过: {skipped or '无'}")
    if fails:
        print("失败:")
        for f in fails:
            print(" -", f)
        return 1
    print("工作流现状:")
    for w in agent_workflow.list_workflows():
        tag = " [激活]" if w.get("active") else (" [默认]" if w.get("is_default") else "")
        print(f" - {w['name']}{tag}  {w['description'][:40]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
