# -*- coding: utf-8 -*-
"""手动调用技能 / 插件时的上下文注入回归。

用户反馈：「用户手动调用 skill、插件时 agent 不会直接把技能规范、说明、插件说明、
调用规范传入模型上下文中，更不会严格遵守和调用」。

守护契约：
  A. 技能注入必须同时带**说明 + 规范正文**——只给正文时模型不知道这个技能是干什么的，
     最容易出现「调用了但不按流程走」；
  B. 插件要能按名解析出说明 / SKILL.md / 调用规范，并拼成可注入的规范文本；
  C. 引擎把技能与插件规范写进对话末尾的**独立 user 消息**（原位替换、不累积），
     且带「必须严格遵守」的强约束措辞；
  D. 插件登记的技能要能并入本任务技能集（其覆盖的工具才会走技能路由硬拦截）。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest                                                        # noqa: E402

from zhuzhu_Copilot.core import agent_engine, agent_plugins, agent_skills  # noqa: E402

PLUGIN_NAME = "weather"
SKILL_BODY = "第一步：读取城市配置。\n第二步：查询天气并生成出行建议。"
SKILL_MD = (f"---\nname: {PLUGIN_NAME}\ndescription: 天气查询与出行建议\n---\n\n{SKILL_BODY}\n")


@pytest.fixture()
def iso(tmp_path, monkeypatch):
    """隔离插件目录 / 技能配置目录，并失效进程内缓存（插件索引、技能列表）"""
    monkeypatch.setattr(agent_plugins, "PLUGINS_DIR", tmp_path / "plugins")
    monkeypatch.setattr(agent_skills, "CONFIG_DIR", tmp_path / "agent")
    agent_plugins.invalidate_index()
    agent_skills.invalidate_skills_cache()
    yield tmp_path
    agent_plugins.invalidate_index()
    agent_skills.invalidate_skills_cache()


def _make_plugin(root: Path, name: str = PLUGIN_NAME, enabled: bool = True,
                 with_skill: bool = True, with_mcp: bool = True) -> Path:
    d = root / "plugins" / name
    d.mkdir(parents=True, exist_ok=True)
    d.joinpath("plugin.json").write_text(json.dumps({
        "name": name, "description": "查询天气并给出出行建议", "kind": "combined",
        "enabled": enabled, "mcp_name": f"{name}-mcp", "skill_name": name,
    }, ensure_ascii=False), encoding="utf-8")
    if with_skill:
        d.joinpath("SKILL.md").write_text(SKILL_MD, encoding="utf-8")
    if with_mcp:
        d.joinpath("server.py").write_text("# mcp server\n", encoding="utf-8")
    agent_plugins.invalidate_index()
    return d


# ---------- A. 技能：说明必须随规范一起注入 ----------
def test_skill_instructions_carry_description(monkeypatch):
    monkeypatch.setattr(agent_skills, "load_skills", lambda *a, **k: [
        {"name": "doc-gen", "description": "生成排版文档", "instruction": "先读模板，再生成。"}])
    text = agent_skills.skill_instructions(["doc-gen"])
    assert "### 技能 doc-gen" in text
    assert "技能说明：生成排版文档" in text, "只给规范正文、没给说明 → 模型不知道技能用途"
    assert "技能规范：" in text and "先读模板，再生成。" in text


# ---------- B. 插件来源索引与规范文本 ----------
def test_plugin_index_maps_servers_and_skills(iso):
    _make_plugin(iso)
    assert agent_plugins.plugin_of_server("weather-mcp") == PLUGIN_NAME
    assert agent_plugins.plugin_of_skill(PLUGIN_NAME) == PLUGIN_NAME
    assert agent_plugins.plugin_of_server("other-mcp") == ""
    assert agent_plugins.plugin_of_skill("doc-gen") == ""


def test_plugin_spec_text_has_description_skill_md_and_rules(iso):
    _make_plugin(iso)
    text = agent_plugins.plugin_spec_text([PLUGIN_NAME])
    assert "### 插件 weather" in text
    assert "查询天气并给出出行建议" in text, "插件说明缺失"
    assert "weather-mcp" in text, "未告诉模型它的 MCP 工具在哪个服务器下"
    assert SKILL_BODY in text, "插件自带的 SKILL.md 规范没有传达"
    assert "调用规范" in text, "没有给出调用规范"


def test_filter_enabled_plugins_skips_unknown_and_disabled(iso):
    _make_plugin(iso)
    _make_plugin(iso, name="off", enabled=False)
    assert agent_plugins.filter_enabled_plugins([PLUGIN_NAME, "off", "nope"]) == [PLUGIN_NAME]
    assert agent_plugins.plugin_skill_names_of([PLUGIN_NAME]) == [PLUGIN_NAME]
    # 随包插件会被镜像进隔离目录，故这里只断言「本插件可用、停用/不存在的被剔除」
    assert PLUGIN_NAME in [p["name"] for p in agent_plugins.list_plugin_calls()]


# ---------- C. 引擎注入：末尾独立消息 + 强约束措辞 + 原位替换 ----------
class _Eng:
    """只带 _sync_skill_msg 需要的两个属性（该方法不触碰其它状态）。

    首条按引擎真实结构放 system 消息：注入消息因此落在末尾（索引 ≥ 1），
    与运行期一致（原位替换的查找范围从索引 1 起）。
    """

    def __init__(self):
        self._auto_skills = []
        self._messages = [{"role": "system", "content": "system"}]


def _sync(eng, skills, plugins):
    agent_engine.AgentEngine._sync_skill_msg(eng, skills, plugins)


def test_sync_skill_msg_injects_skill_and_plugin_spec(iso, monkeypatch):
    _make_plugin(iso)
    monkeypatch.setattr(agent_skills, "load_skills", lambda *a, **k: [
        {"name": PLUGIN_NAME, "description": "天气查询与出行建议",
         "instruction": SKILL_BODY}])
    eng = _Eng()
    _sync(eng, [PLUGIN_NAME], [PLUGIN_NAME])

    assert len(eng._messages) == 2, "技能/插件规范必须是末尾追加的一条独立 user 消息"
    msg = eng._messages[-1]
    assert msg["role"] == "user" and msg["content"].startswith(agent_engine._SKILL_MARK)
    assert "技能说明：天气查询与出行建议" in msg["content"]
    assert SKILL_BODY in msg["content"]
    assert "插件规范" in msg["content"] and "查询天气并给出出行建议" in msg["content"]
    assert "必须严格遵守" in msg["content"], "缺少强约束措辞时模型容易只复述不执行"
    assert "禁止跳过技能直接调用底层工具" in msg["content"]

    # 原位替换：再次同步不累积、条数不变
    _sync(eng, [PLUGIN_NAME], [PLUGIN_NAME])
    assert len(eng._messages) == 2

    # 无任何技能/插件时把该消息删掉，不留残留规范
    _sync(eng, [], [])
    assert eng._messages == [{"role": "system", "content": "system"}]


def test_sync_skill_msg_keeps_auto_matched_skills(iso, monkeypatch):
    monkeypatch.setattr(agent_skills, "load_skills", lambda *a, **k: [
        {"name": "auto-one", "description": "自动匹配技能", "instruction": "按流程执行。"}])
    eng = _Eng()
    eng._auto_skills = ["auto-one"]
    _sync(eng, [], [])
    assert len(eng._messages) == 2
    assert "auto-one" in eng._messages[-1]["content"]


# ---------- D. 提示词与工具层：预览交由模型决策 ----------
def test_preview_prompt_replaces_local_keyword_decision():
    prompt = agent_skills.build_system_prompt("")
    assert "preview_open" in prompt and "preview_refresh" in prompt, \
        "提示词里没有要求模型自行判断并调用可视化预览"
    from zhuzhu_Copilot.ui import agent_panel as ap
    assert not hasattr(ap, "_UI_VISUAL_KEYWORDS"), \
        "本地词库判定必须移除（改由模型按任务内容决策）"
    assert not hasattr(ap.AgentPanel, "_maybe_ask_visual_browser")
