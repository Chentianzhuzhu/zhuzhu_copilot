"""上下文预算 / 分层阈值 / 自动摘要压缩 回归测试（对齐主流 agent 应用口径）。

覆盖：
- 阈值基数是「窗口 − 预留输出」的**可用输入预算**，不是裸窗口；
- 分层阈值：预警（只提示一次）/ 压缩（1M 模式 80%、默认 75%）/ 硬上限（预算 95% 与
  窗口 − 安全余量取更严）；
- 超阈值时用**结构化摘要**替换旧历史（真实调用 LLM 客户端的 chat_stream 接口，
  用可控的假客户端记录入参，不做网络打桩）；
- 压缩后保留最近预算、保留首条 system、不切断 assistant(tool_calls)/tool 配对；
- 压缩事件可观测（次数 / 释放 token）并进入 context_stats；
- 冷却期内不重复压缩。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from zhuzhu_Copilot.core import agent_engine, agent_llm

_ONE_M = agent_llm.ONE_M_CONTEXT


class _RecLLM:
    """记录调用的 LLM 客户端：实现 chat_stream 接口，返回结构化摘要文本"""
    model = "compact-test-model"

    def __init__(self, summary: str = None):
        self.calls = []
        self.summary = summary or ("## 任务目标\n修复登录问题\n"
                                  "## 关键文件与路径\nsrc/login.py")

    def chat_stream(self, messages, stop=None, **_kw):
        self.calls.append(messages)
        return {"text": self.summary}


def _engine(llm=None, **kw):
    return agent_engine.AgentEngine(llm or _RecLLM(), text_only=True, **kw)


def _big_history(n: int = 12, chars: int = 400) -> list:
    """构造足够大的历史（触发压缩）：每条 chars 个汉字"""
    out = [{"role": "system", "content": "系统提示"}]
    for i in range(n):
        out.append({"role": "user", "content": f"第{i}轮 " + "历史上下文内容" * chars})
    return out


def _assert_no_orphan_tool(msgs: list):
    """tool 回复必须紧跟声明了 tool_calls 的 assistant —— 压缩不得切断配对"""
    for i, m in enumerate(msgs):
        if m.get("role") != "tool":
            continue
        paired = False
        for j in range(i - 1, -1, -1):
            role = msgs[j].get("role")
            if role == "tool":
                continue
            paired = bool(role == "assistant" and msgs[j].get("tool_calls"))
            break
        assert paired, f"第 {i} 条 tool 回复失去配对的 assistant(tool_calls)"


# ---------- 1. 预算模型与分层阈值 ----------

def test_budget_and_layer_thresholds_default_vs_1m():
    """默认 128k：预算 = 窗口 − 预留输出，压缩 75%；1M 模式：压缩 80%"""
    eng = _engine(context_window=131072, max_output_tokens=8192)
    budget = 131072 - 8192
    assert eng._input_budget() == budget
    assert eng._compress_ratio() == 0.75
    assert eng._compress_limit() == int(budget * 0.75)
    assert eng._warn_limit() == int(budget * 0.70)
    assert eng._recent_budget() == int(budget * 0.50)
    # 硬上限取「预算 × 95%」与「预算 − 安全余量」中更严者（预算已扣预留输出，
    # 两条路径都不会把输出空间挤掉，否则上游 400）
    assert eng._hard_ceiling() == min(int(budget * 0.95), budget - 8192)
    assert eng._hard_ceiling() < eng._input_budget()

    eng1m = _engine(context_window=_ONE_M, max_output_tokens=32768,
                    long_context_1m=True)
    budget_1m = _ONE_M - 32768
    assert eng1m._compress_ratio() == 0.80
    assert eng1m._compress_limit() == int(budget_1m * 0.80)
    s = eng1m.context_stats()
    assert s["window"] == _ONE_M and s["budget"] == budget_1m
    assert s["long_1m"] is True and s["window_source"] == "1m"
    assert s["max_output"] == 32768
    # 窗口来源/压缩统计是对外契约，必须始终存在
    assert s["thresholds"]["warn"] == int(budget_1m * 0.70)
    assert s["compaction"] == {"count": 0, "last_at": 0.0,
                               "last_merged": 0, "last_saved": 0}


def test_warn_fires_once_and_resets_after_falling_below():
    """预警只提示一次，回落到线下后复位可再次提示（长对话不刷屏）"""
    seen = []
    eng = _engine(context_window=8192, max_output_tokens=1024, on_status=seen.append)
    warn = eng._warn_limit()
    assert eng._maybe_warn_context(warn) is True
    assert eng._maybe_warn_context(warn + 100) is False
    eng._maybe_warn_context(max(1, warn - 100))
    assert eng._maybe_warn_context(warn + 100) is True
    tips = [s for s in seen if "上下文已用" in s]
    assert len(tips) == 2 and "自动压缩" in tips[0]
    assert eng.context_stats()["warn"] is False   # 快照里的 warn 只看当前占用


def test_warn_flag_resets_after_compaction():
    """压缩后占用回落，预警标记复位（下一轮仍能提示）"""
    eng = _engine(context_window=8192, max_output_tokens=1024)
    eng._ctx_warned = True
    eng._messages = _big_history()
    eng.last_estimate = eng._estimate_tokens()
    assert eng._maybe_auto_compress() > 0
    assert eng._ctx_warned is False


# ---------- 2. 自动压缩行为 ----------

def test_compress_triggered_over_threshold_and_cooled_down():
    """超过压缩阈值 → 触发一次 LLM 摘要压缩；冷却期内不再触发"""
    llm = _RecLLM()
    eng = _engine(llm, context_window=8192, max_output_tokens=1024)
    eng._messages = _big_history()
    eng.last_estimate = eng._estimate_tokens()
    assert eng.last_estimate > eng._compress_limit(), "用例前提：占用需超过压缩阈值"
    n = eng._maybe_auto_compress()
    assert n > 0, "超过阈值必须触发压缩"
    assert len(llm.calls) == 1, "压缩必须真实发起一次 LLM 调用"
    stats = eng.context_stats()
    assert stats["compaction"]["count"] == 1
    assert stats["compaction"]["last_merged"] == n
    assert stats["compaction"]["last_saved"] > 0
    assert stats["compaction"]["last_at"] > 0
    assert eng._compress_cooldown == agent_engine._COMPRESS_COOLDOWN
    # 冷却期：即便占用仍高也不再压缩（保护服务商前缀缓存）
    assert eng._maybe_auto_compress() == 0
    eng.last_estimate = eng._estimate_tokens()
    assert len(llm.calls) == 1


def test_compress_triggers_on_real_occupancy_when_estimate_is_low():
    """核心回归：本地估算偏低时，只要上游真实占用（输入+输出）越过压缩阈值，
    压缩必须照常触发——否则真实输入逼近窗口时估算还没过线，上游直接 400。

    场景：UI 占用条显示的是上游真实输入+输出（统计面板口径），而引擎此前只按
    偏低估算判定，压缩过晚 → 面板显示 80% 时请求已 400 ContextWindowExceeded。"""
    llm = _RecLLM()
    eng = _engine(llm, context_window=8192, max_output_tokens=1024)
    eng._messages = _big_history()
    eng.last_estimate = 0                      # 模拟本地估算严重偏低（漏工具 schema 等）
    limit = eng._compress_limit()
    assert eng.last_estimate <= limit, "用例前提：估算本身未过压缩阈值"
    # 上游真实占用（输入+输出）已越过压缩阈值
    eng._accum_usage({"prompt_tokens": limit + 2, "completion_tokens": 1})
    assert eng._real_occupancy() > limit, "用例前提：真实占用已过压缩阈值"
    n = eng._maybe_auto_compress()
    assert n > 0, "估算未过线但真实占用过线也必须压缩"
    assert len(llm.calls) == 1, "压缩必须真实发起一次 LLM 调用"


def test_real_occupancy_falls_back_to_estimate_after_invalidation():
    """压缩/裁剪使上游占用失效后，真实占用判定回退本地估算（与统计面板口径一致）"""
    eng = _engine(context_window=8192, max_output_tokens=1024)
    eng._messages = _big_history()
    eng._accum_usage({"prompt_tokens": 5000, "completion_tokens": 5})
    assert eng._real_occupancy() == 5005
    eng._invalidate_usage()
    assert eng._real_occupancy() == eng._estimate_tokens()


def test_structured_summary_prompt_and_rolling_inheritance():
    """摘要按结构化模板（7 个小节）请求，且上一轮摘要进入摘要输入（滚动继承）"""
    prev = "## 任务目标\n上一轮摘要：修复登录\n## 关键文件与路径\nsrc/login.py"
    llm = _RecLLM()
    eng = _engine(llm, context_window=8192, max_output_tokens=1024)
    eng._messages = _big_history()
    eng._messages.insert(1, {"role": "user", "content": prev})
    eng.last_estimate = eng._estimate_tokens()
    assert eng._maybe_auto_compress() > 0

    sys_p = llm.calls[0][0]["content"]
    for section in ("## 任务目标", "## 已完成与结论", "## 未解决与待办",
                    "## 关键文件与路径", "## 用户偏好与约束",
                    "## 重要工具结果", "## 下一步"):
        assert section in sys_p, f"结构化摘要模板缺少小节：{section}"
    assert "合并更新" in sys_p          # 明确要求滚动继承而非丢弃
    user_p = llm.calls[0][1]["content"]
    assert "src/login.py" in user_p, "上一轮摘要必须参与本轮摘要（滚动继承）"
    # 压缩后上下文里保留的是新摘要（作为 user 消息紧随 system）
    assert eng._messages[0]["role"] == "system"
    head = eng._messages[1]
    content = head.get("content")
    text = (content if isinstance(content, str)
            else " ".join(str(x.get("text") or "") for x in content or []
                          if isinstance(x, dict)))
    assert "修复登录问题" in text, "压缩后的上下文必须携带结构化摘要正文"


def test_compression_keeps_head_and_tool_pairing():
    """压缩后：首条仍是 system、无孤立 tool 回复、条数显著减少"""
    llm = _RecLLM()
    eng = _engine(llm, context_window=8192, max_output_tokens=1024)
    msgs = [{"role": "system", "content": "系统提示"}]
    for i in range(6):
        msgs.append({"role": "user", "content": f"任务{i} " + "很长的历史内容" * 200})
        msgs.append({"role": "assistant", "content": None,
                     "tool_calls": [{"id": f"c{i}", "type": "function",
                                     "function": {"name": "run_command",
                                                  "arguments": "{}"}}]})
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": "命令输出"})
    eng._messages = msgs
    eng.last_estimate = eng._estimate_tokens()
    before = len(eng._messages)
    n = eng._maybe_auto_compress()
    assert n > 0 and len(eng._messages) < before
    assert eng._messages[0]["role"] == "system"
    _assert_no_orphan_tool(eng._messages)


def test_hard_trim_also_keeps_head_and_pairing():
    """硬上限裁剪（压缩的兜底）同样不切断配对、不丢 system"""
    eng = _engine(context_window=8192, max_output_tokens=1024)
    msgs = [{"role": "system", "content": "系统提示"}]
    for i in range(4):
        msgs.append({"role": "user", "content": "x" * 2000})
        msgs.append({"role": "assistant", "content": None,
                     "tool_calls": [{"id": f"d{i}", "type": "function",
                                     "function": {"name": "read_file",
                                                  "arguments": "{}"}}]})
        msgs.append({"role": "tool", "tool_call_id": f"d{i}", "content": "y" * 2000})
    eng._messages = msgs
    eng._hard_trim(eng._input_budget() // 4)
    assert eng._messages[0]["role"] == "system"
    _assert_no_orphan_tool(eng._messages)


def test_compress_never_splits_parallel_tool_replies():
    """并行工具调用会连续出现多条 tool 回复：压缩边界必须整体回退到对应 assistant

    这是 `while start > 1 and messages[start].role == "tool": start -= 1` 的守卫场景
    （把最近预算压到最小，强制让压缩边界落在一组 tool 回复中间）。"""
    llm = _RecLLM()
    eng = _engine(llm, context_window=8192, max_output_tokens=1024)
    eng._messages = [
        {"role": "system", "content": "系统提示"},
        {"role": "user", "content": "早期任务 " + "很长内容" * 400},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "p1", "type": "function",
                         "function": {"name": "read_file", "arguments": "{}"}},
                        {"id": "p2", "type": "function",
                         "function": {"name": "read_file", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "p1", "content": "输出一"},
        {"role": "tool", "tool_call_id": "p2", "content": "输出二"},
    ]
    eng._recent_budget = lambda: 1          # 强制 keep=2 → 边界落在 tag 组中间
    eng.last_estimate = eng._estimate_tokens()
    assert eng._auto_compress(keep_recent=2) > 0
    assert eng._messages[0]["role"] == "system"
    _assert_no_orphan_tool(eng._messages)


def test_reset_tokens_clears_compaction_stats():
    """新对话不显示上一轮的压缩统计"""
    llm = _RecLLM()
    eng = _engine(llm, context_window=8192, max_output_tokens=1024)
    eng._messages = _big_history()
    eng.last_estimate = eng._estimate_tokens()
    assert eng._maybe_auto_compress() > 0
    eng.reset_tokens()
    assert eng.context_stats()["compaction"]["count"] == 0
    assert eng._compress_cooldown == 0


# ---------- 窗口热更新（设置保存 / 按模型路由后必须对齐既有引擎） ----------

def test_set_context_budget_updates_window_and_thresholds():
    """勾选 1M 后既有引擎必须整体切到 1M 口径：窗口、预算、三档阈值、来源标记"""
    eng = _engine(context_window=131072, max_output_tokens=8192, window_source="known")
    assert eng._ctx_window == 131072
    before = eng.context_stats()
    assert before["window"] == 131072 and before["window_source"] == "known"

    eng.set_context_budget(window=_ONE_M, max_output=32768, long_1m=True, source="1m")
    s = eng.context_stats()
    assert s["window"] == _ONE_M, "统计面板显示的必须是新窗口（1M）"
    assert s["window_source"] == "1m" and s["long_1m"] is True
    assert s["budget"] == _ONE_M - 32768
    assert s["max_output"] == 32768
    # 阈值随窗口整体放大（不是旧窗口算出来的值）
    assert s["thresholds"]["compress"] == int(s["budget"] * 0.80)
    assert s["thresholds"]["warn"] == int(s["budget"] * 0.70)
    assert s["thresholds"]["ceiling"] == min(int(s["budget"] * 0.95),
                                             s["budget"] - 8192)
    assert s["thresholds"]["compress"] > before["thresholds"]["compress"]

    # 关掉 1M（回退到声明窗口）→ 比例取回默认 0.75，且 1M 标记清除
    eng.set_context_budget(window=200000, max_output=0, long_1m=False, source="upstream")
    s2 = eng.context_stats()
    assert s2["window"] == 200000 and s2["long_1m"] is False
    assert s2["window_source"] == "upstream"
    assert s2["thresholds"]["compress"] == int(s2["budget"] * 0.75)


def test_set_context_budget_partial_and_guards():
    """None = 该项不变；非法值不得污染状态（窗口下限 4096）"""
    eng = _engine(context_window=131072, max_output_tokens=8192, window_source="known")
    assert eng.set_context_budget(max_output=16384) is False, "窗口未变返回 False"
    s = eng.context_stats()
    assert s["window"] == 131072 and s["max_output"] == 16384
    assert s["window_source"] == "known", "未传 source 不得清掉已有来源"

    assert eng.set_context_budget(window=1000) is False, "过小窗口视为噪声，保持原值"
    assert eng._ctx_window == 131072
    eng.set_context_budget(window="bad")             # 非法类型同样忽略
    assert eng._ctx_window == 131072
    assert eng.set_context_budget(window=262144) is True
    assert eng._ctx_window == 262144
    # 只传 long_1m（source=None = 不提"来源"这一项）→ 不改已有来源标记
    eng.set_context_budget(long_1m=True)
    assert eng._window_source == "known"
    # 创建时未给来源 → 按 1M 标记自述，避免统计面板来源列空白
    assert _engine(context_window=131072,
                   long_context_1m=True).context_stats()["window_source"] == "1m"


def test_set_context_budget_resets_warn_only_on_window_change():
    """预警标记只在窗口真的变了时复位（阈值基数变了，旧"已提示过"不再成立）"""
    eng = _engine(context_window=131072, max_output_tokens=8192)
    eng._ctx_warned = True
    eng.set_context_budget(max_output=16384)          # 窗口未变 → 不复位
    assert eng._ctx_warned is True
    eng.set_context_budget(window=262144)             # 窗口变化 → 复位
    assert eng._ctx_warned is False


def test_set_context_budget_keeps_messages_and_usage():
    """热更新只换上限口径，不得动对话历史与用量统计（这正是复用引擎的目的）"""
    eng = _engine(context_window=131072, max_output_tokens=8192)
    eng._messages = [{"role": "system", "content": "s"},
                     {"role": "user", "content": "u"}]
    eng._accum_usage({"prompt_tokens": 1234, "completion_tokens": 10},
                     {"hit": 800, "miss": 434})
    snapshot = ([dict(m) for m in eng._messages], dict(eng.tokens), dict(eng.last_usage))
    eng.set_context_budget(window=_ONE_M, max_output=32768, long_1m=True, source="1m")
    assert [dict(m) for m in eng._messages] == snapshot[0]
    assert dict(eng.tokens) == snapshot[1]
    assert dict(eng.last_usage) == snapshot[2]
    assert eng.context_stats()["cumulative"]["prompt"] == 1234
