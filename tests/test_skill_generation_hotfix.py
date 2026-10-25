"""回归：agent 生成 skill 后必须能直接导入/配置/在当前会话使用（无需重启）。

覆盖四个曾导致「生成后无法直接导入/配置」的缺陷：
1. 中文/非法技能名直接被拒 → AI 工具归一为合法 slug，不再阻断生成；
2. 工具在 worker 线程（线程池/_call_with_stop 派生线程）执行时，线程局部工作流不继承，
   技能被错绑到全局/别的工作流 → execute_tool 入口统一落地本会话工作流；
3. 带 frontmatter 的完整 SKILL.md 被当作 instruction 落盘 → 双重 frontmatter；
4. 技能设置页按工作流视图列出了「绑定到其他工作流」的技能，勾选了也不生效。
"""
import os
import sys
import threading

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from zhuzhu_Copilot.core import agent_plugins, agent_skills, agent_tools, agent_workflow


@pytest.fixture()
def iso(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_skills, "CONFIG_DIR", tmp_path / "agent")
    monkeypatch.setattr(agent_plugins, "PLUGINS_DIR", tmp_path / "plugins")
    monkeypatch.setattr(agent_workflow, "workflows_root", lambda: tmp_path / "workflows")
    agent_skills._MCP_CACHE["data"] = None
    agent_skills.invalidate_skills_cache()
    agent_plugins.invalidate_index()
    agent_workflow._ACTIVE_CACHE.clear()
    yield tmp_path
    agent_skills._MCP_CACHE["data"] = None
    agent_skills.invalidate_skills_cache()
    agent_plugins.invalidate_index()


_BODY = ("# x\n\n## 触发\n当用户要求时。\n\n## 流程\n1. 执行。\n\n"
         "## 参数\n无。\n\n## 禁止\n无。\n")


# ---------- 1. 非法技能名不阻断生成 ----------

def test_create_skill_chinese_name_is_slugged(iso):
    res = agent_tools.execute_tool("create_skill", {
        "name": "周报助手", "description": "周报生成技能", "instruction": _BODY})
    assert res.get("reload_extensions") is True, res
    text = res.get("text", "")
    assert text.startswith("已创建技能"), text
    # 落盘的技能名必须是合法标识符
    created = [s["name"] for s in agent_skills.load_skills()
               if s["name"] not in {d.get("name") for d in agent_skills.DEFAULT_SKILLS}]
    assert created and all(__import__("re").match(r"^[A-Za-z0-9_-]{1,50}$", n)
                           for n in created), created


# ---------- 2. worker 线程内技能按会话工作流绑定（TLS 穿透） ----------

def test_create_skill_in_worker_thread_binds_session_workflow(iso):
    ok, msg = agent_workflow.create_workflow("devwf", "开发工作流")
    assert ok, msg
    # 主线程 TLS 置空（模拟全局默认）；工具在另一个 worker 线程里执行，
    # 由引擎显式传入 workflow="devwf" —— 这是 _call_with_stop 派生线程的真实场景。
    box = {}

    def worker():
        box["res"] = agent_tools.execute_tool(
            "create_skill", {"name": "team-skill", "description": "团队技能",
                             "instruction": _BODY},
            workflow="devwf")

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join()
    assert box["res"].get("reload_extensions") is True, box["res"]
    assert "devwf" in box["res"]["text"], box["res"]
    assert agent_skills.get_workflow_binding("skill", "team-skill") == ["devwf"]
    assert "team-skill" in [s["name"] for s in agent_skills.load_skills("devwf")]
    assert "team-skill" not in [s["name"] for s in agent_skills.load_skills("_default")]


def test_execute_tool_restores_thread_local_workflow(iso):
    """execute_tool 退出后必须还原 worker 线程原 TLS，避免线程池复用时工作流串号。"""
    agent_workflow.set_current_workflow("")
    agent_tools.execute_tool("create_skill", {"name": "tls_skill", "description": "x",
                                              "instruction": _BODY},
                             workflow="_default")
    assert agent_workflow._current_workflow() == ""
    assert agent_skills._current_workflow() == ""


# ---------- 3. 双重 frontmatter 防御 ----------

def test_create_md_skill_strips_embedded_frontmatter(iso):
    skill_md = ("---\nname: my-plugin\ndescription: 我的插件技能\n---\n\n"
                "# my-plugin\n\n## 触发\n测试。\n\n## 流程\n1. 做。\n")
    ok, msg = agent_skills.create_md_skill("my-plugin", "我的插件技能", skill_md)
    assert ok, msg
    text = (agent_skills._skills_dir() / "my-plugin" / "SKILL.md").read_text(encoding="utf-8")
    assert text.count("---") == 2, f"只允许一对 frontmatter 分隔线，实际:\n{text}"
    loaded = next(s for s in agent_skills.load_skills() if s["name"] == "my-plugin")
    assert not loaded["instruction"].startswith("---"), loaded["instruction"]


def test_create_plugin_skill_kind_no_double_frontmatter(iso, monkeypatch):
    """skill 型插件经 AI 生成后，登记到技能目录的 SKILL.md 不得出现双重 frontmatter。"""
    def fake_spec(desc, kind):
        return {"name": "plain-skill", "summary": "纯技能插件",
                "skill_md": ("---\nname: plain-skill\ndescription: 纯技能插件\n---\n\n"
                             "# plain-skill\n\n## 触发\n测试。\n\n## 流程\n1. 做。\n")}
    monkeypatch.setattr(agent_plugins, "_ai_generate_spec", fake_spec)
    ok, msg = agent_plugins.create_plugin_from_nl("做一个纯技能插件", "skill")
    assert ok, msg
    text = (agent_skills._skills_dir() / "plain-skill" / "SKILL.md").read_text(encoding="utf-8")
    assert text.count("---") == 2, f"双重 frontmatter:\n{text}"


# ---------- 4. 工作流视图只展示该工作流实际加载的技能 ----------

def test_list_skills_for_workflow_hides_foreign_bindings(iso):
    agent_workflow.create_workflow("wf_a", "A")
    agent_workflow.create_workflow("wf_b", "B")
    agent_skills.create_md_skill("a_skill", "A 专属", _BODY, workflow="wf_a")
    agent_skills.create_md_skill("global_skill", "全局技能", _BODY)

    a_rows = {r["name"] for r in agent_skills.list_skills_for_workflow("wf_a")}
    b_rows = {r["name"] for r in agent_skills.list_skills_for_workflow("wf_b")}
    assert "a_skill" in a_rows and "global_skill" in a_rows
    assert "a_skill" not in b_rows, "绑定到 wf_a 的技能不得在 wf_b 视图中可配置"
    assert "global_skill" in b_rows
    # 管理全量视图（workflow=""）仍列出全部
    all_rows = {r["name"] for r in agent_skills.list_skills_for_workflow("")}
    assert {"a_skill", "global_skill"} <= all_rows
