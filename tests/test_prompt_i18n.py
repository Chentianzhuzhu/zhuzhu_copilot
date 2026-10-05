# -*- coding: utf-8 -*-
"""提示词语言（prompt_lang）回归：英文提示词语言下不得有中文发给模型。

背景（真实缺陷，用户可见）：界面切到 English 后，agent 的系统提示词与
自动生成的对话标题仍是中文。两类根因：
  1. **未走查表**：引擎层/子 Agent 层把中文直接拼进消息（工作目录段、执行要求、
     团队消息与共享快照收尾说明、任务清单标题、子 Agent 身份文本、起名提示词…），
     语言包里有译文也用不上；
  2. **缺语言指令**：模型默认跟随**用户消息**语言输出，用户用中文提问时
     即使拿到英文提示词也大概率回中文 —— 故英文语言下必须显式要求
     "reply in English" / "the title must be in English"。

本用例把这些点固化成回归：任一处漏改（或语言包漏 key）都会在这里失败。
"""
import os
import re
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

CJK = re.compile(r"[\u4e00-\u9fff]")


def _force(lang: str):
    """在用例内强制提示词语言（不落盘）。"""
    from zhuzhu_Copilot.core import i18n
    with i18n._lock:
        i18n._state["ui"] = lang
        i18n._state["prompt"] = lang
    return i18n.current_prompt_lang()


def test_en_prompt_has_no_chinese_in_system_prompt():
    """英文提示词语言下，主 Agent 系统提示词全文无中文（含引擎层追加段）。"""
    from zhuzhu_Copilot.core import agent_engine
    assert _force("en_US") == "en_US"
    eng = agent_engine.AgentEngine.__new__(agent_engine.AgentEngine)
    eng.persona = ""
    eng.workflow = None
    eng.text_only = False
    eng.memory_enabled = True
    eng.direct = False
    eng.allow_subagents = True
    prompt = eng._system_prompt("")
    assert prompt.strip(), "系统提示词不应为空"
    hits = [ln.strip() for ln in prompt.splitlines() if CJK.search(ln)]
    assert not hits, "英文提示词下仍有中文行：\n  " + "\n  ".join(hits[:8])


def test_en_prompt_injected_context_is_english():
    """英文提示词语言下，拼进消息的上下文说明全部为英文。"""
    from zhuzhu_Copilot.core import agent_engine, agent_subagent
    assert _force("en_US") == "en_US"
    targets = {
        "team_msg_note": agent_engine.team_msg_note(),
        "shared_snapshot_note": agent_engine.shared_snapshot_note(),
        "_plan_hint(True)": agent_engine._plan_hint(True),
        "_plan_hint(False)": agent_engine._plan_hint(False),
        "_wf_fallback_persona": agent_engine._wf_fallback_persona("demo"),
        "_heuristic_condense": agent_engine._heuristic_condense("read_file", "a\nb\nc"),
        "sub_system_prompt": agent_subagent._sub_system_prompt(),
        "sub_system_prompt(custom)": agent_subagent._sub_system_prompt(custom=True),
        "_agent_system_prompt": agent_subagent._agent_system_prompt("_default"),
    }
    for name, text in targets.items():
        hits = [ln.strip() for ln in str(text).splitlines() if CJK.search(ln)]
        assert not hits, "%s 在英文提示词下仍有中文：%s" % (name, hits[:3])


def test_en_session_title_forces_english_output():
    """英文提示词语言下，起名提示词必须**显式**要求英文输出。

    只把中文原文换成英文译文是不够的：模型会跟随用户消息语言，
    英文界面下用户用中文提问 → 仍回中文标题。故必须有强制语言指令。
    """
    from zhuzhu_Copilot.core import agent_llm
    assert _force("en_US") == "en_US"
    sys_prompt = agent_llm._title_sys()
    assert CJK.search(sys_prompt) is None, "英文起名提示词不应含中文"
    low = sys_prompt.lower()
    assert "english" in low, "英文起名提示词必须显式要求用英文输出标题"
    # zh 模式仍走中文原文（默认语言零回归）
    assert _force("zh_CN") == "zh_CN"
    assert "会话命名助手" in agent_llm._title_sys()


def test_todo_and_skill_marks_are_language_neutral():
    """注入消息的定位哨兵必须语言无关。

    哨兵用于在 self._messages 里定位「上一轮注入的那条消息」并原位替换。
    若哨兵随提示词语言变化，切换语言后新哨兵匹配不到旧哨兵，
    旧消息留在历史里 → 同一份任务清单重复出现两次。
    """
    from zhuzhu_Copilot.core import agent_engine
    for lang in ("zh_CN", "en_US"):
        assert _force(lang) == lang
        assert CJK.search(agent_engine._TODO_MARK) is None
        assert CJK.search(agent_engine._SKILL_MARK) is None
    # 中文模式的模型可见标题仍是中文
    assert _force("zh_CN") == "zh_CN"
    assert agent_engine.agent_tools is not None      # 触达模块，确保导入正常


def test_prompt_lang_follows_ui_lang():
    """切换界面语言时，提示词语言必须跟随（这正是本bug 的语义前提）。

    历史缺陷：set_lang 对「已缓存的 prompt 语言」在 persist=False 时不做跟随，
    于是用户切到 English 而提示词仍是中文 —— 界面英文、系统提示词中文。
    """
    from zhuzhu_Copilot.core import i18n
    with i18n._lock:
        i18n._state["ui"] = i18n.ZH_CN
        i18n._state["prompt"] = i18n.ZH_CN
    try:
        i18n.set_lang("en_US", persist=False)
        assert i18n.current_lang() == "en_US"
        assert i18n.current_prompt_lang() == "en_US", "提示词语言未跟随界面语言"
        # 用户在设置里显式单独选了提示词语言时，以显式值为准
        i18n.set_lang("en_US", persist=False, prompt_lang="zh_CN")
        assert i18n.current_prompt_lang() == "zh_CN", "显式提示词语言应被尊重"
    finally:
        with i18n._lock:
            i18n._state["ui"] = i18n.ZH_CN
            i18n._state["prompt"] = i18n.ZH_CN


def test_en_prompt_declares_output_language_on_every_branch():
    """英文提示词语言下，**每条**系统提示词分支都必须显式声明输出语言。

    这是本bug 的核心根因：persona / 规则 / 工具清单只描述身份与纪律，从不规定
    「用什么语言回答」。提示词全文已是英文，但模型仍按训练分布回中文（用户实测：
    英文界面 + 英文输入「hi」→ 回「你好！我是 zhuzhu Copilot…」）。
    故必须有显式语言指令，且放system 开头拿最高指令权重。
    """
    from zhuzhu_Copilot.core import agent_engine, agent_llm, agent_skills, agent_subagent
    assert _force("en_US") == "en_US"
    marker = "always reply in English"

    def _mk(**kw):
        e = agent_engine.AgentEngine.__new__(agent_engine.AgentEngine)
        e.persona = ""; e.workflow = None; e.text_only = False
        e.memory_enabled = True; e.direct = False; e.allow_subagents = True
        for k, v in kw.items():
            setattr(e, k, v)
        return e

    branches = {
        "default": _mk()._system_prompt(""),
        # 整体替换分支：最容易绕过语言指令
        "persona覆盖": _mk(persona="You are a data analyst.")._system_prompt(""),
        "text_only": _mk(text_only=True)._system_prompt(""),
        "direct": _mk(direct=True)._system_prompt(""),
        "mem_off": _mk(memory_enabled=False)._system_prompt(""),
        "no_subagent": _mk(allow_subagents=False)._system_prompt(""),
        "subagent_builtin": agent_subagent._sub_system_prompt(),
        "subagent_custom": agent_subagent._sub_system_prompt(custom=True),
        "subagent_persona": agent_subagent._sub_system_prompt(persona="You are a tester."),
        "title": agent_llm._title_sys(),
    }
    for name, text in branches.items():
        assert marker in text, "%s 分支缺少英文输出语言指令" % name
        # 语言指令必须在首行（最高指令权重），不能塞在末尾
        assert marker in text.splitlines()[0], "%s 的语言指令未置于首行" % name

    # 中文模式同样必须显式声明（对称，避免中文界面被英文指令污染）
    assert _force("zh_CN") == "zh_CN"
    zh_mark = "必须始终使用简体中文回复"
    assert zh_mark in agent_skills.build_system_prompt("")
    assert zh_mark in agent_subagent._sub_system_prompt()
    assert zh_mark in _mk_engine()._system_prompt("")


def _mk_engine(**kw):
    from zhuzhu_Copilot.core import agent_engine
    e = agent_engine.AgentEngine.__new__(agent_engine.AgentEngine)
    e.persona = ""; e.workflow = None; e.text_only = False
    e.memory_enabled = True; e.direct = False; e.allow_subagents = True
    for k, v in kw.items():
        setattr(e, k, v)
    return e


def test_en_sidechannel_requests_are_english():
    """旁路请求（chat_stream 直调）也必须英文：上下文摘要与长文本压缩。

    这两条不经工具循环、最容易被漏改，且压缩产物会长期留在上下文里 ——
    中文摘要会持续把模型往中文方向带。
    """
    from zhuzhu_Copilot.core import agent_engine
    assert _force("en_US") == "en_US"

    class _CapLLM:
        def __init__(self):
            self.seen = []

        def chat_stream(self, messages, **kw):
            self.seen = messages
            return {"text": "x", "tool_calls": [], "usage": {}}

    e = _mk_engine()
    e.llm = _CapLLM()
    e._est_cache = {}
    e._stop = type("S", (), {"is_set": staticmethod(lambda: False)})()
    e._llm_summarize([{"role": "user", "content": "hi"},
                      {"role": "assistant", "content": "hello"}])
    joined = "\n".join(str(m.get("content")) for m in e.llm.seen)
    assert joined.strip(), "摘要请求未发出"
    assert not CJK.search(joined), "历史摘要请求在英文提示词下含中文"

    e2 = _mk_engine()
    e2.llm = _CapLLM()
    e2._est_cache = {}
    e2._stop = type("S", (), {"is_set": staticmethod(lambda: False)})()
    e2._condense_tool_text("read_file", "x" * (agent_engine._READ_CONDENSE_CHARS + 10))
    joined2 = "\n".join(str(m.get("content")) for m in e2.llm.seen)
    assert joined2.strip(), "长文本压缩请求未发出"
    assert not CJK.search(joined2), "长文本压缩请求在英文提示词下含中文"


def test_zh_prompt_unchanged_after_lookup_refactor():
    """中文模式（源语言）不受查表改造影响：原文逐字返回，且不混入内部 key。"""
    from zhuzhu_Copilot.core import agent_engine, agent_subagent
    assert _force("zh_CN") == "zh_CN"
    cases = {
        "team_msg_note": (agent_engine.team_msg_note(), "团队消息"),
        "shared_snapshot_note": (agent_engine.shared_snapshot_note(), "共同上下文空间快照"),
        "_plan_hint": (agent_engine._plan_hint(True), "【执行要求】"),
        "_wf_fallback_persona": (agent_engine._wf_fallback_persona("demo"), "请使用简体中文回复"),
        "_heuristic_condense": (agent_engine._heuristic_condense("f", "a\nb\nc"), "阅读压缩"),
        "sub_system_prompt": (agent_subagent._sub_system_prompt(), "你是子 Agent"),
        "sub_system_prompt(custom)": (agent_subagent._sub_system_prompt(custom=True),
                                       "请完成交给你的任务"),
    }
    for name, (got, must) in cases.items():
        assert got.strip(), "%s 中文模式返回空串" % name
        assert must in got, "%s 中文模式丢失原文：%r" % (name, must[:30])
        # 缺译文时 i18n 会退回 key，绝不能把内部标识拼进提示词
        assert "prompt." not in got, "%s 混入了内部 key" % name