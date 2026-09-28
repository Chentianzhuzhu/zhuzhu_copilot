"""工具管控 / bash 白名单前缀匹配单元测试。

覆盖：
1. cmd_in_custom_safe 白名单「前缀整词匹配」（git push → git push origin main 免确认）
   + 单 token 白名单项仅精确匹配（防 python -c 注入绕过）
   + 与 assess_command 危险/管道评估的联动（危险命令不因前缀放行）
2. disabled_tools / tools_disabled_all 配置文件解析（字符串/列表/非法值容错）
3. AgentEngine._execute 系统层面硬拦截：禁用工具 / 禁用全部 / 记忆关闭
"""

import pytest

from zhuzhu_Copilot.core import agent_engine, agent_llm, agent_skills
from zhuzhu_Copilot.core import agent_sandbox as sb


@pytest.fixture
def fake_settings(monkeypatch):
    """把 settings.json 读取替换为可控 dict，测试结束后自动复原"""
    store = {}

    def _load():
        return dict(store)

    monkeypatch.setattr(agent_skills, "load_settings", _load)
    return store


# ---------------- bash 白名单前缀匹配 ----------------

def test_whitelist_prefix_multitoken(fake_settings):
    """多 token 白名单项前缀匹配：git push 命中 git push origin main"""
    fake_settings["custom_safe_commands"] = ["git push", "pip install"]
    assert sb.cmd_in_custom_safe("git push origin main") is True
    assert sb.cmd_in_custom_safe("pip install numpy") is True


def test_whitelist_exact_kept(fake_settings):
    """整条精确匹配保留：命令行与白名单项完全一致"""
    fake_settings["custom_safe_commands"] = ["git status"]
    assert sb.cmd_in_custom_safe("git status") is True
    # 大小写与首尾空格容忍
    assert sb.cmd_in_custom_safe("  GIT STATUS  ") is True


def test_whitelist_single_token_exact_only(fake_settings):
    """单 token 白名单项（python/node）只精确匹配，不放开 -c 注入"""
    fake_settings["custom_safe_commands"] = ["python"]
    assert sb.cmd_in_custom_safe("python") is True
    assert sb.cmd_in_custom_safe("python -c \"import os;os.system('x')\"") is False
    fake_settings["custom_safe_commands"] = ["node"]
    assert sb.cmd_in_custom_safe("node app.js") is False


def test_whitelist_word_boundary(fake_settings):
    """前缀按完整参数词切分：pip 不误配 pipx"""
    fake_settings["custom_safe_commands"] = ["pip"]
    assert sb.cmd_in_custom_safe("pipx install foo") is False
    assert sb.cmd_in_custom_safe("pip install foo") is False  # 纯精确：pip 需要整条 "pip"


def test_whitelist_blank_or_wild_ignored(fake_settings):
    """空配置 / 空命令不命中"""
    fake_settings["custom_safe_commands"] = []
    assert sb.cmd_in_custom_safe("git push origin main") is False
    fake_settings["custom_safe_commands"] = ["git push"]
    assert sb.cmd_in_custom_safe("") is False


def test_assess_prefix_does_not_bypass_danger(fake_settings):
    """评估联动：命中前缀但含管道/多命令 → 仍 risly（不因前缀放行为 safe）"""
    fake_settings["custom_safe_commands"] = ["git push"]
    level, _ = sb.assess_command("git push origin main ; del /s C:\\Windows")
    assert level != "safe"
    level2, _ = sb.assess_command("git push -f origin main")
    assert level2 == "dangerous"


# ---------------- 禁用工具 / 禁用全部 ----------------

def test_disabled_tools_parse_str(fake_settings):
    fake_settings["disabled_tools"] = "grep, read_file; Web_Search\nrun_command"
    got = sb.disabled_tools()
    assert got == frozenset({"grep", "read_file", "web_search", "run_command"})


def test_disabled_tools_parse_list(fake_settings):
    fake_settings["disabled_tools"] = ["grep", "read_file", ""]
    got = sb.disabled_tools()
    assert got == frozenset({"grep", "read_file"})


def test_disabled_tools_missing(fake_settings):
    fake_settings.pop("disabled_tools", None)
    assert sb.disabled_tools() == frozenset()


def test_disabled_all_truthy(fake_settings):
    for v in (True, "true", "1", "on", "yes"):
        fake_settings["disable_all_tools"] = v
        assert sb.tools_disabled_all() is True
    for v in (False, "false", "0", "off", "", None):
        fake_settings["disable_all_tools"] = v
        assert sb.tools_disabled_all() is False


# ---------------- 引擎执行层硬拦截 ----------------

def _engine(**kw):
    kw.setdefault("llm", agent_llm.LLMClient())
    return agent_engine.AgentEngine(**kw)


def test_engine_execute_blocks_disabled_tool():
    eng = _engine(memory_enabled=True)
    eng._disabled_tools = frozenset({"grep", "web_search"})
    eng._disabled_all = False
    res = eng._execute("grep", {"pattern": "x", "path": "."})
    assert "已禁用" in res["text"]
    res2 = eng._execute("web_search", {"query": "x"})
    assert "已禁用" in res2["text"]
    # 未禁用的工具不拦（无 mcp，走未知工具而非禁用提示）
    res3 = eng._execute("not_a_real_tool_x", {})
    assert "已禁用" not in res3["text"]


def test_engine_execute_blocks_all():
    eng = _engine(memory_enabled=True)
    eng._disabled_tools = frozenset()
    eng._disabled_all = True
    for name in ("grep", "read_file", "web_search", "ask_user"):
        res = eng._execute(name, {})
        assert "已禁用" in res["text"]


def test_engine_execute_blocks_memory_when_disabled():
    eng = _engine(memory_enabled=False)
    eng._disabled_all = False
    eng._disabled_tools = frozenset()
    res = eng._execute("save_memory", {"content": "x"})
    assert "记忆已关闭" in res["text"]
    res2 = eng._execute("load_memory", {})
    assert "记忆已关闭" in res2["text"]


def test_engine_all_tools_schema_trim():
    """schema 层面剔除禁用工具；禁用全部 → 空工具集（纯对话模式）"""
    eng = _engine(memory_enabled=True)
    eng._disabled_tools = frozenset({"read_file", "web_search"})
    eng._disabled_all = False
    names = {t["function"]["name"] for t in eng._all_tools()}
    assert "read_file" not in names and "web_search" not in names
    assert "run_command" in names            # 其余工具保留
    eng._disabled_all = True
    assert eng._all_tools() == []


if __name__ == "__main__":
    pytest.main([__file__, "-q"])