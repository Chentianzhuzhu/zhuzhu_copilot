"""构建期：把当前机器「现成工作流」快照成种子目录，随安装包分发。

背景：内置预设（workflow_templates/presets/）是模板，安装后要手动创建工作流；
本脚本把 ~/.winapp_migrator/workflows/ 下全部已实例化工作流（含自定义
g9_study_helper / sansheng_liubu 等）连同团队配置快照到 build/workflows_seed/，
打包进 setup。新机器安装完成后，agent_workflow 首启自动把缺失工作流补到
用户目录，做到「安装完成即 @ 调用」。

用法：
    python scripts/prepare_workflow_seed.py
"""
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = Path.home() / ".winapp_migrator" / "workflows"
DST = ROOT / "build" / "workflows_seed"


def _team_config() -> dict:
    """团队配置：优先用户 team.json，缺省用内置默认团队（与 agent_team 一致）。"""
    try:
        sys.path.insert(0, str(ROOT / "src"))
        from winapp_migrator.core import agent_team
        f = agent_team._team_path()
        if f.is_file():
            data = json.loads(f.read_text(encoding="utf-8", errors="replace"))
            if isinstance(data, dict) and str(data.get("leader") or "").strip():
                return {"name": str(data.get("name") or ""),
                        "leader": str(data.get("leader") or ""),
                        "members": [str(m).strip() for m in (data.get("members") or [])
                                    if str(m).strip()]}
    except Exception:
        pass
    from winapp_migrator.core import agent_team
    return dict(agent_team.DEFAULT_TEAM)


def main() -> int:
    if not SRC.is_dir():
        print(f"源工作流目录不存在，跳过: {SRC}")
        return 0
    if DST.exists():
        shutil.rmtree(DST)
    DST.mkdir(parents=True, exist_ok=True)
    copied = []
    for d in sorted(SRC.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        # 仅快照「实例化工作流」：目录含 workflow.json 才算（跳过 _default 等说明目录）
        if not (d / "workflow.json").is_file():
            continue
        shutil.copytree(d, DST / d.name, dirs_exist_ok=True)
        copied.append(d.name)
    # 团队配置种子（新机首启落盘为 ~/.winapp_migrator/team.json）
    (DST / "team.json").write_text(
        json.dumps(_team_config(), ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"工作流种子已生成: {DST}")
    print(f"  工作流 {len(copied)} 个: {', '.join(copied)}")
    print("  团队配置: leader=" + str(_team_config().get("leader") or ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
