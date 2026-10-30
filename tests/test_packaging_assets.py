"""打包资产回归：技能 / Agent 插件 / Copilot 插件 / MCP 随安装包分发。

静态检查构建链路的关键接线（种子脚本 → spec → 产物自检 → 首启同步），
并用临时目录验证 Copilot 插件首启落盘逻辑（不触碰真实用户目录）。
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zhuzhu_Copilot.core import copilot_plugins


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8", errors="replace")


def test_seed_script_collects_all_three_asset_kinds():
    text = _read("scripts/prepare_assets_seed.py")
    assert "REPO / \"plugins\"" in text or 'REPO / "plugins"' in text
    assert "REPO / \"skills\"" in text or 'REPO / "skills"' in text
    assert "copilot_plugins_examples" in text
    assert "_write(\"copilot_plugins\"" in text
    assert "ensure_shipped_copilot_plugins" in text


def test_spec_bundles_copilot_seed_with_fallback():
    spec = _read("build/zhuzhu_Copilot.spec")
    assert "copilot_plugins_seed" in spec
    assert "copilot_plugins_examples" in spec
    assert "_copilot_datas" in spec.split("datas=[", 1)[1]


def test_release_script_verifies_asset_seeds_in_dist():
    ps = _read("build_release.ps1")
    assert "copilot_plugins_seed" in ps
    assert "skills_seed" in ps and "plugins_seed" in ps
    assert "prepare_assets_seed.py" in ps


def test_startup_syncs_shipped_copilot_plugins():
    main_py = _read("src/main.py")
    assert "ensure_shipped_copilot_plugins" in main_py


def test_ensure_shipped_copies_missing_and_keeps_existing(tmp_path, monkeypatch):
    seed = tmp_path / "seed"
    user_dir = tmp_path / "copilot_plugins"
    for name, marker in (("demo", None), ("keep", None)):
        d = seed / name
        d.mkdir(parents=True)
        (d / "plugin.json").write_text(
            '{"name": "%s", "kind": "copilot"}' % name, encoding="utf-8")
        if name == "demo":
            (d / "source_ops.json").write_text("[]", encoding="utf-8")
    # 已存在的同名插件：用户改过的文件不能被覆盖
    keep = user_dir / "keep"
    keep.mkdir(parents=True)
    (keep / "plugin.json").write_text('{"name": "keep"}', encoding="utf-8")
    (keep / "user_note.txt").write_text("mine", encoding="utf-8")
    (seed / "README.md").write_text("not a plugin", encoding="utf-8")

    monkeypatch.setattr(copilot_plugins, "COPILOT_PLUGINS_DIR", user_dir)
    monkeypatch.setattr(copilot_plugins, "_shipped_copilot_plugins_dir", lambda: seed)
    copilot_plugins.ensure_shipped_copilot_plugins()

    assert (user_dir / "demo" / "plugin.json").is_file(), "缺失插件必须落盘"
    assert (user_dir / "demo" / "source_ops.json").is_file()
    assert (keep / "user_note.txt").read_text(encoding="utf-8") == "mine"
    assert not (keep / "source_ops.json").exists(), "已存在插件不得被覆盖"
    assert not (user_dir / "README.md").exists(), "非插件文件不得复制"
