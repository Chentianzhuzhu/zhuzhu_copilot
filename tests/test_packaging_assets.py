"""打包资产契约：技能 / Agent 插件随安装包分发；Copilot 插件默认必须为空。

静态检查构建链路（种子脚本 → spec → 产物自检 → 首启），并用临时目录验证
Copilot 插件目录默认为空、不会被仓库示例自动填充。
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zhuzhu_Copilot.core import copilot_plugins


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8", errors="replace")


def test_seed_script_collects_skills_and_agent_plugins_only():
    text = _read("scripts/prepare_assets_seed.py")
    assert 'REPO / "plugins"' in text
    assert 'REPO / "skills"' in text
    # Copilot 插件不参与打包：脚本里不得再出现其目录名或写入逻辑
    assert "copilot_plugins" not in text
    assert "copilot_plugins_examples" not in text


def test_spec_does_not_bundle_copilot_plugins():
    spec = _read("build/zhuzhu_Copilot.spec")
    assert "copilot_plugins_seed" not in spec
    assert "copilot_plugins_examples" not in spec
    datas = spec.split("datas=[", 1)[1]
    assert "_skills_datas" in datas and "_plugins_datas" in datas


def test_release_script_verifies_only_skills_and_plugins():
    ps = _read("build_release.ps1")
    assert "skills_seed" in ps and "plugins_seed" in ps
    assert "copilot_plugins_seed" not in ps
    assert "prepare_assets_seed.py" in ps


def test_startup_does_not_seed_copilot_plugins():
    main_py = _read("src/main.py")
    assert "ensure_shipped_copilot_plugins" not in main_py


def test_copilot_plugins_default_empty(tmp_path, monkeypatch):
    """默认安装（空目录）时插件列表必须为空，且模块没有随包同步入口"""
    user_dir = tmp_path / "copilot_plugins"
    user_dir.mkdir()
    monkeypatch.setattr(copilot_plugins, "COPILOT_PLUGINS_DIR", user_dir)
    assert copilot_plugins.list_plugins() == []
    assert not hasattr(copilot_plugins, "ensure_shipped_copilot_plugins")
    assert not hasattr(copilot_plugins, "_shipped_copilot_plugins_dir")
