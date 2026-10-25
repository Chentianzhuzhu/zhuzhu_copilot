# -*- coding: utf-8 -*-
"""对话内调用技能（use_skill）与「描述即生成插件」链路回归。

覆盖：
  1. 工具 schema 已注册 use_skill；
  2. 清单模式返回可用技能（含随包 plugin-create）；
  3. 按名调用返回「说明 + 规范正文」并带 __activate_skill__ 标志；
  4. 未知技能名 → 拦截 + 清单 + 近似建议；
  5. 引擎消费 __activate_skill__：并入 _task_skills / _auto_skills，
     标志不出现在给模型的工具结果里；
  6. 技能关键词覆盖「生成插件」类措辞；任务分组含 use_skill 与插件生成触发词。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

import pytest
from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from zhuzhu_Copilot.core import agent_engine, agent_skills, agent_tools


def _names():
    return [t["function"]["name"] for t in agent_tools.TOOLS]


# ---------- 1. schema 注册 ----------

def test_use_skill_registered_in_tools():
    names = _names()
    assert "use_skill" in names, "use_skill 必须注册为工具"
    schema = next(t for t in agent_tools.TOOLS if t["function"]["name"] == "use_skill")
    props = schema["function"]["parameters"]["properties"]
    assert "name" in props
    assert schema["function"]["parameters"].get("required") == [], \
        "name 可缺省（缺省=返回技能清单）"


# ---------- 2/3/4. 工具行为 ----------

def test_use_skill_list_mode_returns_inventory():
    res = agent_tools._use_skill("")
    assert "plugin-create" in res["text"], "清单应含随包插件创建技能"
    assert "use_skill(name=" in res["text"]
    assert "__activate_skill__" not in res, "清单模式不激活任何技能"


def test_use_skill_by_name_returns_spec_and_flag():
    res = agent_tools._use_skill("plugin-create")
    assert res.get("__activate_skill__") == "plugin-create"
    body = res["text"]
    # 规范正文关键片段（SKILL.md 的核心原则）
    assert "插件" in body and ("SKILL.md" in body or "三件套" in body or "MCP" in body)
    assert "已纳入本任务" in body, "应提示后续轮次持续生效"


def test_use_skill_unknown_name_blocked_with_inventory():
    res = agent_tools._use_skill("压根不存在的技能xyz")
    assert "不存在" in res["text"]
    assert "plugin-create" in res["text"], "未命中时应给出可用清单"
    assert "__activate_skill__" not in res


# ---------- 5. 引擎消费激活标志 ----------

class _LLM:
    model = "test-model"
    fell_back = False
    silent_fallback = False

    def __init__(self):
        self.tokens = {"prompt": 0, "completion": 0}

    def chat_stream(self, messages, **kw):
        return {"text": "ok", "tool_calls": [], "usage": None,
                "cache": {"hit": 0, "miss": 0}}


def test_engine_activates_skill_from_tool_result(monkeypatch):
    monkeypatch.setattr(agent_engine.agent_tts, "load_config",
                        lambda: {"auto_read": False})
    monkeypatch.setattr(agent_engine.agent_sandbox, "disabled_tools",
                        lambda: frozenset())
    monkeypatch.setattr(agent_engine.agent_sandbox, "tools_disabled_all",
                        lambda: False)
    eng = agent_engine.AgentEngine(_LLM(), text_only=True)
    eng._task_skills = set()
    eng._auto_skills = []
    res = eng._execute("use_skill", {"name": "plugin-create"})
    # 标志被消费，不出现在给模型的结果里
    assert "__activate_skill__" not in res
    assert "plugin-create" in eng._task_skills, "技能应并入本任务技能集（路由硬拦截覆盖）"
    assert "plugin-create" in eng._auto_skills, "技能应持续注入（_sync_skill_msg 来源）"
    assert "plugin-create" in eng._skills_read, "规范已由工具给出，标记已读防重复注入"
    assert "已纳入本任务" in res["text"]


# ---------- 6. 关键词与任务分组 ----------

def test_plugin_keywords_cover_natural_phrasing():
    kws = agent_skills._SKILL_KEYWORDS.get("plugin-create") or []
    for w in ("生成插件", "做个插件", "写个插件", "热插拔", "copilot插件",
              "agent插件", "插件开发"):
        assert w in kws, f"plugin-create 关键词缺少「{w}」（直接描述生成会匹配不上）"


def test_task_groups_expose_use_skill_and_plugin_triggers():
    groups = {g: (kws, tools) for g, kws, tools in agent_engine._TASK_GROUPS}
    # use_skill 在常驻核心集（任何任务可调）+ skill/cordis 两组
    assert "use_skill" in agent_engine._CORE_TOOLS
    assert "use_skill" in groups["skill"][1]
    assert "use_skill" in groups["cordis"][1]
    # 「生成一个插件」类措辞命中 cordis 组（插件生成工具不被裁掉）
    cordis_kws = groups["cordis"][0]
    for w in ("生成一个插件", "做个插件", "热插拔"):
        assert w in cordis_kws, f"cordis 组缺少触发词「{w}」"
    assert {"create_plugin", "create_copilot_plugin",
            "apply_copilot_plugin"} <= groups["cordis"][1]


def test_use_skill_never_intercepted_by_skill_routing():
    assert "use_skill" in agent_engine._NO_INTERCEPT_TOOLS, \
        "use_skill 是查阅规范的元工具，不得被技能路由硬拦截"
