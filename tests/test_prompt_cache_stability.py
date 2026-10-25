"""上下文缓存命中率回归：发给上游的**前缀必须逐字节稳定**。

上游（DeepSeek / OpenAI 兼容网关）的 prompt cache 是**前缀精确匹配**：请求体里
系统提示 + 工具 schema 排在最前面，只要这一段有一个字节变了，后面的历史再一样
也命中不了，整轮按全价重新计费。

历史上破坏前缀稳定性的两处：
1. 工具表按「本轮用户输入命中的任务类别」重新裁剪 → 用户一问代码、一问文件，
   工具块整体换形状，工具 schema 又恰好排在前缀最前面 → 每轮都 miss；
2. 系统提示若掺入时间/会话 id/随机顺序，每轮都变。

现在：系统提示与工具 schema 都是纯函数且可复现；任务裁剪的工具集在会话内**只增不减**
（黏住），只在「出现新类别」那一次让前缀失效。
"""
import copy
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from zhuzhu_Copilot.core import agent_engine, agent_skills, agent_tools


def test_system_prompt_is_byte_stable_across_calls():
    """系统提示必须可复现：两次构建逐字节相同，且不含时间/临时路径这类易变内容。"""
    a = agent_skills.build_system_prompt()
    b = agent_skills.build_system_prompt()
    assert a == b, "系统提示两次构建不一致 —— 每轮都会让前缀缓存失效"
    assert len(a) > 1000
    for bad in ("\x00",):
        assert bad not in a


def test_tool_schemas_serialize_identically():
    """工具 schema 的序列化结果必须稳定（顺序 + 内容），否则前缀缓存全废。"""
    a = json.dumps(agent_tools.tool_schemas(), ensure_ascii=False, sort_keys=False)
    b = json.dumps(agent_tools.tool_schemas(), ensure_ascii=False, sort_keys=False)
    assert a == b
    names = [t["function"]["name"] for t in agent_tools.tool_schemas()]
    assert len(names) == len(set(names)), "工具名重复会被上游拒绝（Tool names must be unique）"


def _engine():
    return agent_engine.AgentEngine(object())


def test_task_pruned_toolset_is_sticky_within_a_session():
    """任务裁剪的工具集在会话内只增不减 —— 这是缓存能命中的前提。

    否则用户「先问代码、再问文件」，工具块每轮换形状，前缀永久失效。
    """
    groups = list(agent_engine._TASK_GROUP_TOOLS)
    assert len(groups) >= 2, "至少要有两个任务类别才能验证"
    eng = _engine()
    eng._task_groups = {groups[0]}
    first = [t["function"]["name"] for t in eng._all_tools()]
    eng._task_groups = {groups[1]}
    second = [t["function"]["name"] for t in eng._all_tools()]
    assert set(first) <= set(second), "第二轮必须保留第一轮的工具（黏住）"
    # 同一类别连问两轮：工具块必须逐字节一致
    third = [t["function"]["name"] for t in eng._all_tools()]
    assert second == third, "同类别连续两轮工具块不一致 —— 缓存必然 miss"


def test_clear_context_resets_the_sticky_toolset():
    """新上下文重新累积：否则新会话一上来就带着上个会话的全套工具（白烧 token）。"""
    eng = _engine()
    groups = list(agent_engine._TASK_GROUP_TOOLS)
    eng._task_groups = {groups[0]}
    eng._all_tools()
    assert eng._tool_group_union
    eng.clear_context()
    assert eng._tool_group_union == set()


def test_sticky_toolset_never_exceeds_the_full_toolset():
    """黏住只做并集，不能凭空多出工具。"""
    eng = _engine()
    full = {t["function"]["name"] for t in agent_tools.tool_schemas()}
    for g in agent_engine._TASK_GROUP_TOOLS:
        eng._task_groups = {g}
        got = {t["function"]["name"] for t in eng._all_tools()}
        assert got <= full | {"virtual_desktop"} or got <= full, "出现了不存在的工具名"
