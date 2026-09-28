"""构建期：把当前机器「现成工作流」快照成种子目录，随安装包分发。

背景：内置预设（workflow_templates/presets/）是模板，安装后要手动创建工作流；
本脚本把 ~/.zhuzhu_Copilot/workflows/ 下全部已实例化工作流（含自定义
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

# 包在 src/ 下（不装进 site-packages），脚本被构建流水线直接执行时必须先补 sys.path，
# 否则 `from zhuzhu_Copilot import ...` 直接 ModuleNotFoundError（构建 [5/11] 会失败）。
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from zhuzhu_Copilot import app_identity   # noqa: E402

SRC = app_identity.data_root() / "workflows"
DST = ROOT / "build" / "workflows_seed"


def _team_config() -> dict:
    """团队配置：优先用户 team.json，缺省用内置默认团队（与 agent_team 一致）。"""
    try:
        sys.path.insert(0, str(ROOT / "src"))
        from zhuzhu_Copilot.core import agent_team
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
    from zhuzhu_Copilot.core import agent_team
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
    # 快照来自**用户数据目录**，里面的工作流脚本很可能还写着旧包名
    # （winapp_migrator.*）—— 随包分发前必须换成当前身份，否则新装机器上
    # agent.py / tools.py / llm.py 一律 ModuleNotFoundError、自定义静默失效。
    fixed = app_identity.rewrite_legacy_names_in_tree(DST)
    # 团队配置种子（新机首启落盘为 ~/.zhuzhu_Copilot/team.json）
    (DST / "team.json").write_text(
        json.dumps(_team_config(), ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"工作流种子已生成: {DST}")
    print(f"  工作流 {len(copied)} 个: {', '.join(copied)}")
    print(f"  旧包名改写: {fixed} 个文件")
    print("  团队配置: leader=" + str(_team_config().get("leader") or ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
