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

# ---------------- MCP 服务器逐项停用 ----------------

@pytest.fixture
def fake_mcp(monkeypatch):
    """把 mcp_servers.json 读取替换为可控列表，并清掉进程内缓存。"""
    store = []
    monkeypatch.setattr(agent_skills, "_load",
                        lambda name, default=None: list(store) if name == "mcp_servers.json" else default)
    monkeypatch.setitem(agent_skills._MCP_CACHE, "data", None)
    return store


def test_disabled_mcp_server_is_not_returned(fake_mcp):
    """停用的服务器**默认不返回** —— 这是「停用即不向上游暴露接口」的唯一开关点。

    连接器、工具聚合、工作流绑定都读 load_mcp_servers()，过滤只在这里做一次：
    停用的服务器连进程都不该起，更不该出现在发给模型的工具表里。
    """
    fake_mcp.extend([
        {"name": "keep", "command": "a"},                       # 老配置：没有 enabled 字段
        {"name": "on", "command": "b", "enabled": True},
        {"name": "off", "command": "c", "enabled": False},
    ])
    got = [x["name"] for x in agent_skills.load_mcp_servers()]
    assert got == ["keep", "on"], got


def test_include_disabled_returns_everything_for_the_settings_page(fake_mcp):
    """设置页必须看到全部条目，否则停用后再也改不回启用。"""
    fake_mcp.extend([
        {"name": "on", "command": "b", "enabled": True},
        {"name": "off", "command": "c", "enabled": False},
    ])
    got = [x["name"] for x in agent_skills.load_mcp_servers(include_disabled=True)]
    assert got == ["on", "off"]


def test_mcp_cache_is_not_mutated_by_callers(fake_mcp):
    """返回深拷贝：调用方改返回值不能污染缓存（否则下次读到被改过的配置）。"""
    fake_mcp.append({"name": "x", "command": "a"})
    a = agent_skills.load_mcp_servers()
    a[0]["name"] = "hacked"
    assert agent_skills.load_mcp_servers()[0]["name"] == "x"


# ---------------- 设置页：工具勾选清单 ----------------

def _fake_tools_page(monkeypatch, names, disabled=()):
    """构造一个只带「工具清单」所需属性/方法的假设置页（不建整个设置对话框）。"""
    from PyQt6.QtWidgets import (QApplication, QLabel, QLineEdit, QListWidget,
                                 QPlainTextEdit)
    _app = QApplication.instance() or QApplication([])
    from zhuzhu_Copilot.ui import agent_panel as ap

    monkeypatch.setattr(ap.agent_sandbox, "disabled_tools",
                        lambda: frozenset(disabled))

    class _Page:
        pass

    f = _Page()
    f._tool_sync_guard = False
    f.tool_filter_edit = QLineEdit()
    f.tools_check_list = QListWidget()
    f.tool_count_label = QLabel("")
    f.disable_tools_edit = QPlainTextEdit()
    f._all_tool_names = lambda: list(names)
    f._reload_tool_checklist = ap._AgentSettingsDialog._reload_tool_checklist.__get__(f)
    f._on_tool_check_changed = ap._AgentSettingsDialog._on_tool_check_changed.__get__(f)
    f._on_tool_text_changed = ap._AgentSettingsDialog._on_tool_text_changed.__get__(f)
    return f, _app


def test_tool_checklist_reflects_disabled_tools(monkeypatch):
    f, _ = _fake_tools_page(monkeypatch, ["grep", "read_file", "run_command"],
                            disabled=("read_file",))
    f._reload_tool_checklist()
    lst = f.tools_check_list
    assert lst.count() == 3
    states = {lst.item(i).text(): lst.item(i).checkState().name for i in range(lst.count())}
    assert states["read_file"] == "Unchecked"
    assert states["grep"] == "Checked"
    assert "3 个工具" in f.tool_count_label.text()


def test_unchecking_a_tool_writes_the_disabled_list(monkeypatch):
    """取消勾选 → 文本框里出现该工具名（随后保存即生效）。"""
    f, _ = _fake_tools_page(monkeypatch, ["grep", "read_file"])
    f._reload_tool_checklist()
    lst = f.tools_check_list
    from PyQt6.QtCore import Qt
    lst.item(0).setCheckState(Qt.CheckState.Unchecked)
    f._on_tool_check_changed()
    assert "grep" in f.disable_tools_edit.toPlainText().splitlines()


def test_editing_the_text_syncs_back_to_checkboxes(monkeypatch):
    """手动编辑禁用清单 → 勾选状态跟着变（双向同步）。"""
    f, _ = _fake_tools_page(monkeypatch, ["grep", "read_file"])
    f._reload_tool_checklist()
    f._tool_sync_guard = False
    f.disable_tools_edit.setPlainText("read_file")
    f._on_tool_text_changed()
    lst = f.tools_check_list
    states = {lst.item(i).text(): lst.item(i).checkState().name for i in range(lst.count())}
    assert states["read_file"] == "Unchecked"
    assert states["grep"] == "Checked"


def test_tool_filter_narrows_the_checklist(monkeypatch):
    f, _ = _fake_tools_page(monkeypatch, ["grep", "read_file", "run_command"])
    f._reload_tool_checklist()
    f.tool_filter_edit.setText("re")
    f._reload_tool_checklist()
    assert f.tools_check_list.count() == 2      # read_file + run_command
