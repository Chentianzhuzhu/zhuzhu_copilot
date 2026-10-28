"""技能 / Agent 插件种子（随安装包分发）回归。

覆盖：
1. prepare_assets_seed 的有效性规则：技能需 SKILL.md + frontmatter name + 正文足够长；
   插件需可解析的 plugin.json（含 BOM 也要能读）
2. 合并规则：仓库内置优先、用户目录补齐、插件投影的技能不重复打包、
   用户插件的 config.json 不随包发行
3. 运行时目录探测：打包态优先 skills_seed / plugins_seed，缺失回退内置目录
"""
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts")))

import prepare_assets_seed as pas               # noqa: E402
import verify_release_artifact as vra          # noqa: E402
from zhuzhu_Copilot.core import agent_plugins   # noqa: E402
from zhuzhu_Copilot.core import agent_skills    # noqa: E402


def _mk_skill(root, name, body="正文：" + "执行流程说明。" * 10, frontmatter=True):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    fm = f"---\nname: {name}\ndescription: 测试技能\n---\n\n" if frontmatter else ""
    (d / "SKILL.md").write_text(fm + body, encoding="utf-8")
    return d


def _mk_plugin(root, name, skill_name="", bom=False):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    meta = {"name": name, "kind": "mcp", "description": "t"}
    if skill_name:
        meta["skill_name"] = skill_name
    raw = json.dumps(meta, ensure_ascii=False)
    (d / "plugin.json").write_text(("\ufeff" if bom else "") + raw, encoding="utf-8")
    (d / "server.py").write_text("# server\n", encoding="utf-8")
    (d / "config.json").write_text('{"api_key": "secret"}', encoding="utf-8")
    return d


def test_skill_reject_rules(tmp_path):
    assert pas._skill_reject(_mk_skill(tmp_path, "ok")) == ""
    short = _mk_skill(tmp_path, "x", body="x")
    assert "正文过短" in pas._skill_reject(short)
    no_fm = _mk_skill(tmp_path, "no-fm", frontmatter=False)
    assert "name" in pas._skill_reject(no_fm)
    (tmp_path / "empty").mkdir()
    assert "缺少 SKILL.md" in pas._skill_reject(tmp_path / "empty")


def test_plugin_reject_rules(tmp_path):
    assert pas._plugin_reject(_mk_plugin(tmp_path, "good")) == ""
    # Windows 写出的带 BOM 的 plugin.json 必须能解析（audio-analyzer 就踩过这条）
    assert pas._plugin_reject(_mk_plugin(tmp_path, "bom", bom=True)) == ""
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "plugin.json").write_text("{not json", encoding="utf-8")
    assert "无法解析" in pas._plugin_reject(bad)


def test_bom_plugin_meta_readable(tmp_path):
    """运行时读插件元数据同样要兼容 BOM，否则插件被静默判为无元数据。"""
    d = _mk_plugin(tmp_path, "bom", bom=True)
    assert agent_plugins._plugin_meta(d).get("name") == "bom"


def test_collect_prefers_repo_and_skips_plugin_skills(tmp_path):
    repo, user = tmp_path / "repo", tmp_path / "user"
    _mk_skill(repo, "shared")
    _mk_skill(user, "shared", body="用户改过的版本：" + "y" * 80)
    _mk_skill(user, "self-made")
    _mk_skill(user, "projection")
    picked, skipped, shadowed = pas._collect(
        [("repo", repo), ("user", user)], pas._skill_reject,
        skip_names={"projection"})
    assert sorted(picked) == ["self-made", "shared"]
    assert picked["shared"][0] == "repo", "仓库内置优先，用户同名副本不覆盖"
    assert "shared" in shadowed
    assert any("projection" in s for s in skipped)


def test_main_writes_seed_without_user_config(tmp_path, monkeypatch):
    repo, user, seed = tmp_path / "repo", tmp_path / "user", tmp_path / "seed"
    _mk_plugin(repo / "plugins", "builtin", skill_name="builtin-skill")
    _mk_plugin(user / "plugins", "userplugin")
    _mk_skill(user / "plugins" / "userplugin", "userplugin-skill")   # 缺 SKILL.md 的插件不会被误收
    _mk_skill(repo / "skills", "repo-skill")
    _mk_skill(user / "agent" / "skills", "user-skill")
    _mk_skill(user / "agent" / "skills", "userplugin")               # 插件投影，跳过

    monkeypatch.setattr(pas, "SEED", seed)
    monkeypatch.setattr(pas, "REPO", repo)
    monkeypatch.setattr(pas.app_identity, "data_root", lambda: user)
    monkeypatch.setattr(pas.agent_skills, "_skills_dir",
                        lambda: user / "agent" / "skills")

    assert pas.main() == 0
    assert sorted(p.name for p in (seed / "skills").iterdir()) == ["repo-skill", "user-skill"]
    plugins = sorted(p.name for p in (seed / "plugins").iterdir())
    assert plugins == ["builtin", "userplugin"]
    # 用户插件的 config.json（含密钥）不随包发行；仓库内置的默认配置保留
    assert not (seed / "plugins" / "userplugin" / "config.json").exists()
    assert (seed / "plugins" / "builtin" / "config.json").is_file()


def test_shipped_dirs_prefer_seed(tmp_path, monkeypatch):
    (tmp_path / "skills_seed" / "a").mkdir(parents=True)
    (tmp_path / "skills" / "b").mkdir(parents=True)
    (tmp_path / "plugins_seed" / "p").mkdir(parents=True)
    (tmp_path / "plugins" / "q").mkdir(parents=True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    assert agent_skills._shipped_skills_dir() == tmp_path / "skills_seed"
    assert agent_plugins._shipped_plugins_dir() == tmp_path / "plugins_seed"

    # 种子缺失时回退内置目录（源码直跑 / 未跑准备脚本的构建）
    import shutil
    shutil.rmtree(tmp_path / "skills_seed")
    shutil.rmtree(tmp_path / "plugins_seed")
    assert agent_skills._shipped_skills_dir() == tmp_path / "skills"
    assert agent_plugins._shipped_plugins_dir() == tmp_path / "plugins"


def test_release_artifact_checks_resource_dirs(tmp_path):
    """发布会自检：技能 / 插件资源目录缺失或为空 → 构建失败（避免装完什么都没有）。"""
    assert len(vra._check_resources(str(tmp_path))) == 2   # 两个目录都不存在

    (tmp_path / "skills_seed" / "empty").mkdir(parents=True)
    (tmp_path / "plugins_seed" / "p").mkdir(parents=True)
    assert len(vra._check_resources(str(tmp_path))) == 2   # 目录在但没有任何有效资产

    (tmp_path / "skills_seed" / "empty" / "SKILL.md").write_text("x", encoding="utf-8")
    (tmp_path / "plugins_seed" / "p" / "plugin.json").write_text("{}", encoding="utf-8")
    assert vra._check_resources(str(tmp_path)) == []
