"""提示词分层：主 Agent 覆盖 / 自定义 Agent 合成 / 全局规则穿透。

三层语义（详见 core/agent_prompt.py）：
- 角色层：默认主 Agent 可被设置页覆盖；自定义 Agent 与子 Agent 各自独立；
- 全局层：任何 Agent 都追加，切换不丢；
- 强制层：内置兜底，不由本模块测试。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from zhuzhu_Copilot.core import agent_agents, agent_prompt, agent_skills, agent_subagent


@pytest.fixture()
def fake_settings(monkeypatch):
    box = {}
    monkeypatch.setattr(agent_prompt, "_settings", lambda: box)
    return box


def test_global_prompt_falls_back_to_legacy_key(fake_settings):
    fake_settings["custom_system_prompt"] = "旧版补充"
    assert agent_prompt.global_prompt() == "旧版补充"
    fake_settings["global_system_prompt"] = "新全局"
    assert agent_prompt.global_prompt() == "新全局"
    assert "新全局" in agent_prompt.global_block()


def test_empty_global_prompt_adds_nothing(fake_settings):
    assert agent_prompt.global_block() == ""


def test_main_agent_override_replaces_builtin_persona(fake_settings):
    builtin = agent_skills.build_system_prompt()
    assert "zhuzhu Copilot" in builtin
    fake_settings["main_agent_persona"] = "你是一只猫"
    fake_settings["main_agent_system_prompt"] = "只回答一个字"
    overridden = agent_skills.build_system_prompt()
    assert "你是一只猫" in overridden and "只回答一个字" in overridden
    assert "zhuzhu Copilot" not in overridden[:400]


def test_partial_override_keeps_other_segment(fake_settings):
    """只填性格时，内置系统提示词必须保留 —— 不然只想改口吻却把工具方法论清空。"""
    fake_settings["main_agent_persona"] = "你是一只猫"
    out = agent_skills.build_system_prompt()
    assert "你是一只猫" in out
    builtin_system = agent_skills.DEFAULT_AGENTS[0]["system_prompt"]
    assert builtin_system[:40] in out


def test_custom_agent_compose_and_legacy_record():
    assert agent_agents.composed_prompt(
        {"persona": "严谨", "system_prompt": "只做审查"}) == "严谨\n\n只做审查"
    # 旧记录只有 system_prompt（当时即整段人格）：拼装结果必须原样返回
    assert agent_agents.composed_prompt({"system_prompt": "旧人格"}) == "旧人格"


def test_sub_agent_prompt_gets_persona_rules_and_global(fake_settings):
    fake_settings["global_system_prompt"] = "GLOBAL-RULE"
    p = agent_subagent._sub_system_prompt(
        persona="子性格", custom=True, system_prompt="子规则")
    assert "子性格" in p and "子规则" in p and "GLOBAL-RULE" in p


def test_custom_agent_persist_and_load(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_agents, "agents_root", lambda: tmp_path)
    ok, _ = agent_agents.save_agent("reviewer", "只做审查", persona="严谨")
    assert ok
    ag = agent_agents.get_agent("reviewer")
    assert ag["persona"] == "严谨" and ag["system_prompt"] == "只做审查"
    assert agent_agents.composed_prompt(ag) == "严谨\n\n只做审查"
    # 两栏都空 → 拒绝保存（切过去会得到一份无身份的空提示词）
    assert agent_agents.save_agent("empty", "", persona="")[0] is False
