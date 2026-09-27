"""修复 bug 场景的 Agent 机制回归测试：子 Agent 准入分档 / YOLO 提问 / 上下文按比例压缩。

覆盖（对应用户提出的五项机制要求）：
1. 简单与中等任务（simple/moderate 档）禁止派发子 Agent 或其他工作流主 Agent：
   工具 schema 剔除 + 执行层硬拦截 + 提示词明示 + sub-agent 技能剔除；
2. 非常复杂任务（complex 档）与用户显式要求（并行/编队/@点名/工作团）才放行；
3. 需求明确时不做冗余确认：提问策略块必须要求"已明确就直接执行"；
4. 1M 上下文不会再在远低于 80% 时自动压缩（旧实现"消息数 > 200 即压缩"的旁路已移除）；
5. YOLO（direct）模式下 ask_user 可用（schema 不外露 → 已改为保留，执行层不再拦截）。

判定链路：agent_llm.subagent_allowed 是唯一事实来源，引擎/提示词/面板只消费结论。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from winapp_migrator.core import agent_engine, agent_llm, agent_skills

_ONE_M = agent_llm.ONE_M_CONTEXT


class _RecLLM:
    """记录调用的 LLM 客户端：实现 chat_stream 接口（压缩摘要真实走这条路径）"""
    model = "gating-test-model"

    def __init__(self):
        self.calls = []

    def chat_stream(self, messages, stop=None, **_kw):
        self.calls.append(messages)
        return {"text": "## 任务目标\n修复登录问题"}


def _engine(llm=None, **kw):
    return agent_engine.AgentEngine(llm or _RecLLM(), text_only=True, **kw)


def _tool_names(eng):
    return {t["function"]["name"] for t in eng._all_tools()}


# ---------- 1. 复杂度分档（唯一事实来源） ----------

def test_complexity_tier_maps_every_effort_level():
    """8 个力度档必须全部落到三档之内，且只有最强的三档算「非常复杂」"""
    assert agent_llm.complexity_tier("off") == "simple"
    assert agent_llm.complexity_tier("low") == "simple"
    assert agent_llm.complexity_tier("medium") == "moderate"
    assert agent_llm.complexity_tier("high") == "moderate"
    assert agent_llm.complexity_tier("very_high") == "moderate"
    assert agent_llm.complexity_tier("max") == "complex"
    assert agent_llm.complexity_tier("ultra") == "complex"
    assert agent_llm.complexity_tier("extreme") == "complex"
    # 每个合法力度都有分档（防止新增档位后漏改映射表）
    for e in agent_llm.EFFORTS:
        assert agent_llm.complexity_tier(e) in agent_llm.COMPLEXITY_TIERS
    # 非法值不炸，按中等处理（保守：不放行组队）
    assert agent_llm.complexity_tier("") == "moderate"
    assert agent_llm.complexity_tier("bogus") == "moderate"


def test_subagent_allowed_auto_policy_gates_by_tier():
    """auto 策略：简单/中等禁止，非常复杂放行"""
    for e in ("off", "low", "medium", "high", "very_high"):
        assert agent_llm.subagent_allowed(e, "auto") is False, f"{e} 档不应放行派发"
    for e in ("max", "ultra", "extreme"):
        assert agent_llm.subagent_allowed(e, "auto") is True, f"{e} 档应放行派发"


def test_subagent_allowed_policy_and_explicit_overrides():
    """always/never 强制策略优先；用户显式要求可越过自动分档"""
    assert agent_llm.subagent_allowed("low", "always") is True
    assert agent_llm.subagent_allowed("ultra", "never") is False
    assert agent_llm.subagent_allowed("low", "never", explicit=True) is False, "never 是硬闸"
    assert agent_llm.subagent_allowed("low", "auto", explicit=True) is True
    # 非法策略回落 auto（写错一个词不应把能力锁死或全开）
    assert agent_llm.subagent_allowed("low", "wat") is False
    assert agent_llm.subagent_allowed("ultra", "") is True


def test_wants_subagents_detects_explicit_intent():
    """显式信号：并行/编队类关键词 + @点名"""
    for t in ("帮我并行处理这些文件", "用多个子 agent 一起做", "开工作团来做",
              "分布式跑一遍", "派发给下属", "@product_dev 改这个模块",
              "让它们协作完成这个任务"):
        assert agent_llm.wants_subagents(t) is True, f"应识别为显式要求：{t}"
    for t in ("修复登录按钮点击无响应", "帮我看看这个 bug 的根因", "生成一份周报"):
        assert agent_llm.wants_subagents(t) is False, f"不应误判为显式要求：{t}"


# ---------- 2. 引擎层：schema 剔除 + 执行层硬拦截 ----------

def test_simple_task_strips_subagent_tools_from_schema():
    """简单/中等任务：子 Agent 与跨工作流派发工具不再出现在工具表里"""
    eng = _engine(allow_subagents=False)
    names = _tool_names(eng)
    for n in agent_engine._SUBAGENT_TOOLS:
        assert n not in names, f"{n} 不应暴露给简单/中等任务"
    # 常规工具照常可用（不能把主 Agent 的手脚一起砍掉）
    for n in ("read_file", "write_file", "edit_file", "run_command", "search_files",
              "update_todo", "ask_user"):
        assert n in names, f"基础工具 {n} 必须保留"


def test_complex_task_keeps_subagent_tools_in_schema():
    """非常复杂任务：派发工具照常暴露"""
    eng = _engine(allow_subagents=True)
    names = _tool_names(eng)
    for n in ("dispatch_sub_agents", "explore_project", "search_large"):
        assert n in names, f"{n} 应暴露给非常复杂任务"


def test_engine_hard_blocks_subagent_tools_when_disallowed():
    """执行层硬拦截：即使模型幻觉写出工具名也不会真派发"""
    eng = _engine(allow_subagents=False)
    for n in ("dispatch_sub_agents", "explore_project", "search_large", "use_workflow_agent"):
        res = eng._execute(n, {"tasks": [{"title": "t", "goal": "g"}]})
        assert "子 Agent 已禁用" in res["text"], f"{n} 必须被硬拦截"
        assert "直接完成任务" in res["text"], "拦截提示必须给出替代做法"
    # 注册式子 Agent（sub_<name>）同样受管
    res = eng._execute("sub_reviewer", {"goal": "g"})
    assert "子 Agent 已禁用" in res["text"]


def test_subagent_skill_dropped_when_disallowed():
    """sub-agent 技能通篇教派发，能力禁用时必须剔除（否则把模型推向被禁工具）"""
    names = ["sub-agent", "systematic-debugging", "file-ops"]
    assert agent_skills.filter_subagent_skills(names, False) == \
        ["systematic-debugging", "file-ops"]
    assert agent_skills.filter_subagent_skills(names, True) == names
    assert agent_skills.SUBAGENT_SKILL == "sub-agent"
    # 引擎任务上下文里也确实被剔除
    eng = _engine(allow_subagents=False)
    eng.allow_subagents = False
    assert "sub-agent" not in agent_skills.filter_subagent_skills(
        agent_skills.auto_skill_names("帮我理解一下这个项目的结构"), False)


# ---------- 3. 提示词分级 ----------

def test_prompt_blocks_dispatch_when_simple_or_moderate():
    blocked = agent_skills._subagent_policy_block(False)
    assert "禁止调用 dispatch_sub_agents" in blocked
    assert "use_workflow_agent" in blocked and "其他工作流的主 Agent" in blocked
    assert "禁止" in blocked and "已被系统禁用" in blocked
    # 不能再出现"必须派发"这类引导（旧文案会诱导组队）
    assert "必须调用 dispatch_sub_agents" not in blocked
    assert "必须" not in blocked.replace("必须携带", "")


def test_prompt_allows_dispatch_only_for_very_complex():
    allowed = agent_skills._subagent_policy_block(True)
    assert "非常复杂" in allowed
    assert "dispatch_sub_agents" in allowed
    assert "简单与中等任务一律自己直接完成" in allowed, "放行档也要明确克制边界"


def test_plan_hint_is_graded_by_subagent_admission():
    """拼到用户消息末尾的执行要求同样分级（简单任务不出现组队暗示）"""
    off = agent_engine._plan_hint(False)
    on = agent_engine._plan_hint(True)
    assert "dispatch_sub_agents" not in off
    assert "dispatch_sub_agents" in on
    assert "update_todo" in off and "update_todo" in on, "规划要求本身不能被裁掉"


def test_ask_policy_forbids_redundant_confirmation_when_requirements_clear():
    """需求明确时禁止冗余确认、直接执行（用户第 3 条要求）"""
    blk = agent_skills._ask_policy_block()
    assert "已经明确" in blk and "直接动手修改" in blk
    assert "禁止再向用户复述确认" in blk
    assert "合并为一次询问" in blk          # 确实要问时仍要一次问清
    assert "严禁编造关键信息" in blk


# ---------- 4. YOLO（直行）模式仍可提问 ----------

def test_yolo_direct_mode_exposes_ask_user():
    """直行模式不暴露提问工具的问题已修复"""
    eng = _engine(direct=True)
    assert "ask_user" in _tool_names(eng), "YOLO 下 ask_user 必须可用"


def test_yolo_direct_mode_executes_ask_user():
    """直行模式执行层不再拦截提问，且提问回调真实被调用"""
    seen = []
    eng = _engine(direct=True, ask_user=lambda a: (seen.append(a), "用方案 A")[1])
    res = eng._execute("ask_user", {"question": "选哪个方案？"})
    assert res["text"] == "用方案 A"
    assert seen and seen[0]["question"] == "选哪个方案？"
    # 同一问题不重复弹窗（既有去重契约不得因放开而被破坏）
    assert "已回答" in eng._execute("ask_user", {"question": "选哪个方案？"})["text"]


def test_yolo_prompt_allows_asking_but_still_forbids_noise():
    blk = agent_skills._direct_mode_block()
    assert "禁止调用 ask_user" not in blk
    assert "ask_user 一次性问清" in blk
    assert "无需逐步询问或请求确认" in blk


# ---------- 5. 上下文压缩只看 token 占比 ----------

def _tiny_history(n: int) -> list:
    """大量但极短的消息：条数膨胀而 token 占用很低（复现"不到 80% 就被压缩"）"""
    out = [{"role": "system", "content": "系统提示"}]
    for i in range(n):
        out.append({"role": "user", "content": f"第{i}步 ok"})
        out.append({"role": "assistant", "content": "继续"})
    return out


def _medium_history(n: int = 12, chars: int = 400) -> list:
    """条数适中但每条较长：足以跨过压缩阈值并留下可合并的旧消息"""
    out = [{"role": "system", "content": "系统提示"}]
    for i in range(n):
        out.append({"role": "user", "content": f"第{i}轮 " + "历史上下文内容" * chars})
    return out


def test_no_compress_when_message_count_huge_but_tokens_low():
    """1M 模式下 300+ 条消息、占用仅百分之几 → 不得压缩（旧实现 >200 条即压缩）"""
    llm = _RecLLM()
    eng = _engine(llm, context_window=_ONE_M, max_output_tokens=32768, long_context_1m=True)
    eng._messages = _tiny_history(200)          # 401 条消息
    eng.last_estimate = eng._estimate_tokens()
    assert len(eng._messages) > 200, "用例前提：消息条数超过旧旁路阈值"
    pct = eng.last_estimate * 100 / eng._input_budget()
    assert pct < 20, f"用例前提：token 占用应很低（实际 {pct:.1f}%）"
    assert eng._maybe_auto_compress() == 0, "未达压缩比例不得压缩"
    assert llm.calls == [], "不得发起摘要调用"
    assert len(eng._messages) > 200, "上下文必须原样保留"
    assert eng.context_stats()["compaction"]["count"] == 0


def test_compress_fires_exactly_when_ratio_reached():
    """达到「预算 × 比例」才压缩，未达比例一律不压（阈值判定是唯一开关）

    用小窗口跑，保证用例体量小：预算 7168 → 压缩线 5376。占用判定同时看
    「本地估算」与「上游真实占用」，两条信号都在线内才不压缩（估算偏低时
    真实占用先过线也必须压，否则上游 400）。"""
    llm = _RecLLM()
    eng = _engine(llm, context_window=8192, max_output_tokens=1024)
    eng._messages = _medium_history()
    assert eng._estimate_tokens() > eng._compress_limit(), "用例前提：占用需超过压缩阈值"
    # 用上游真实占用（输入+输出）精确控制"当前占用"边界
    eng.last_estimate = 0
    limit = eng._compress_limit()
    eng._accum_usage({"prompt_tokens": limit - 1, "completion_tokens": 0})  # 恰好未达阈值
    assert eng._maybe_auto_compress() == 0
    assert llm.calls == []
    eng._accum_usage({"prompt_tokens": limit + 1, "completion_tokens": 0})  # 刚越过阈值
    assert eng._maybe_auto_compress() > 0
    assert len(llm.calls) == 1
    assert eng.context_stats()["compaction"]["count"] == 1


def test_1m_ratio_is_80_percent_of_input_budget():
    """1M 开关生效时压缩线就是输入预算的 80%（而不是被条数旁路提前触发）"""
    eng = _engine(context_window=_ONE_M, max_output_tokens=32768, long_context_1m=True)
    assert eng._compress_ratio() == 0.80
    assert eng._compress_limit() == int((_ONE_M - 32768) * 0.80)
    assert eng.context_stats()["thresholds"]["compress"] == eng._compress_limit()
