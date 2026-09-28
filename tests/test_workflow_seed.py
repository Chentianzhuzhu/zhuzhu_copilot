"""工作流种子（首启即 @ 调用）：快照打包 + 首启补齐回归。

覆盖：
1. agent_workflow._seed_dir 开发模式解析到 <项目根>/build/workflows_seed
2. _seed_missing：用户目录缺失的工作流整体复制、已存在不覆盖、team.json 落盘
3. prepare_workflow_seed.py：构建期快照源工作流（含 workflow.json 才收）到种子目录
"""
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from zhuzhu_Copilot.core import agent_workflow


def _mk_workflow(d, name, extra="agent.py"):
    d.mkdir(parents=True, exist_ok=True)
    (d / "workflow.json").write_text(
        json.dumps({"name": name, "enabled": True, "description": "t"}),
        encoding="utf-8")
    if extra:
        (d / extra).write_text(f"# {name} core file", encoding="utf-8")


def test_seed_dir_dev_mode():
    """开发模式种子目录 = 项目根 build/workflows_seed。"""
    sd = agent_workflow._seed_dir()
    assert sd.name == "workflows_seed"
    assert sd.parent.name == "build"


def test_seed_missing_copies_workflows_and_team(tmp_path, monkeypatch):
    """缺的工作流整体复制、已存在不覆盖、team.json 落盘到 home。"""
    seed = tmp_path / "seed"
    _mk_workflow(seed / "frontend_design", "frontend_design")
    _mk_workflow(seed / "g9_study_helper", "g9_study_helper", extra="subagents.json")
    (seed / "team.json").write_text(
        json.dumps({"name": "默认开发团队", "leader": "product_manager",
                    "members": ["frontend_design"]}),
        encoding="utf-8")
    monkeypatch.setattr(agent_workflow, "_seed_dir", lambda: seed)

    root = tmp_path / "root"
    root.mkdir()
    # 已存在的工作流（用户自定义）不应被种子覆盖
    _mk_workflow(root / "frontend_design", "frontend_design")
    (root / "frontend_design" / "workflow.json").write_text(
        json.dumps({"name": "frontend_design", "custom": 1}), encoding="utf-8")

    home = tmp_path / "home"
    agent_workflow._seed_missing(root, home)

    # 缺失工作流整体复制（含子文件）
    assert (root / "g9_study_helper" / "workflow.json").is_file()
    assert (root / "g9_study_helper" / "subagents.json").is_file()
    # 已存在不覆盖
    assert json.loads((root / "frontend_design" / "workflow.json").read_text())["custom"] == 1
    # team.json 落盘（home 隔离）
    tgt = home / ".zhuzhu_Copilot" / "team.json"
    assert tgt.is_file()
    assert json.loads(tgt.read_text(encoding="utf-8"))["leader"] == "product_manager"


def test_seed_missing_no_seed_ok(tmp_path, monkeypatch):
    """无种子目录（源码直跑未生成）时静默跳过，不抛错。"""
    monkeypatch.setattr(agent_workflow, "_seed_dir",
                        lambda: tmp_path / "not_exist_seed")
    root = tmp_path / "root"
    root.mkdir()
    home = tmp_path / "home"
    agent_workflow._seed_missing(root, home)
    assert not (home / ".zhuzhu_Copilot").exists()


def test_prepare_seed_snapshot(tmp_path, monkeypatch):
    """prepare_workflow_seed.py：快照含 workflow.json 的工作流 + 团队配置。"""
    scripts = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import prepare_workflow_seed as pws

    src = tmp_path / "src"
    _mk_workflow(src / "backend_dev", "backend_dev")
    _mk_workflow(src / "sansheng_liubu", "sansheng_liubu", extra="tools.py")
    (src / "notes.txt").write_text("非工作流文件", encoding="utf-8")   # 无 workflow.json 应跳过
    dst = tmp_path / "seed"
    monkeypatch.setattr(pws, "SRC", src)
    monkeypatch.setattr(pws, "DST", dst)

    assert pws.main() == 0
    assert (dst / "backend_dev" / "workflow.json").is_file()
    assert (dst / "sansheng_liubu" / "tools.py").is_file()
    assert not (dst / "notes.txt").exists(), "无 workflow.json 的目录不进入种子"
    assert (dst / "team.json").is_file()
    assert json.loads((dst / "team.json").read_text(encoding="utf-8"))["leader"] == "product_manager"
