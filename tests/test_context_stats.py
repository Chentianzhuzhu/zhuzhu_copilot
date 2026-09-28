"""上下文统计（上游真实 usage）回归测试。

背景：token 查看面板需要「基于服务商上游的上下文统计」——即用服务商返回的
usage 判断当前上下文占用了多少窗口，而不是只看本地估算。

实现要点（本文件覆盖）：
- `eng.tokens` 为全程累加（计费口径），新增 `eng.last_usage` 记录**最近一次请求**
  的真实用量（上下文占用口径＝输入+输出），二者不可混用；
- `context_stats()` 是 UI 的唯一数据入口：上游优先，无上游数据（尚未请求 / 压缩后
  已失效）自动回退本地估算，并给出压缩阈值与硬上限供面板画刻度线；
- 上下文被压缩/裁剪后必须使上游占用值失效，否则面板会继续显示压缩前的旧占用；
- 与 `save_context`/`load_context` 一同持久化，重启后占用统计仍可用（旧文件兼容）。
"""
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from zhuzhu_Copilot.core import agent_engine


class _FakeLLM:
    """最小可用的 LLM 桩：只提供统计与摘要所需的接口（不做任何网络调用）"""
    model = "ctx-test-model"

    def __init__(self):
        self.summary_calls = 0

    def chat_stream(self, messages, stop=None):
        self.summary_calls += 1
        return {"text": "历史对话摘要"}


def _engine(**kw):
    return agent_engine.AgentEngine(_FakeLLM(), text_only=True, **kw)


def _msgs(n: int) -> list:
    return [{"role": "user", "content": f"第 {i} 条历史消息内容"} for i in range(n)]


def test_accum_usage_keeps_cumulative_and_last_separate():
    """tokens 累加、last_usage 覆盖：面板的"本次"与"累计"必须来自不同口径"""
    eng = _engine()
    eng._accum_usage({"prompt_tokens": 1000, "completion_tokens": 40},
                     {"hit": 800, "miss": 200})
    eng._accum_usage({"prompt_tokens": 1200, "completion_tokens": 60},
                     {"hit": 900, "miss": 300})
    assert eng.tokens == {"prompt": 2200, "completion": 100,
                          "cache_hit": 1700, "cache_miss": 500}
    assert eng.last_usage == {"prompt": 1200, "completion": 60,
                              "cache_hit": 900, "cache_miss": 300}
    assert eng.last_usage_at > 0


def test_accum_usage_accepts_responses_protocol_naming():
    """部分服务商（DeepSeek/Responses）用 input_tokens/output_tokens 命名，同样要计入"""
    eng = _engine()
    eng._accum_usage({"input_tokens": 300, "output_tokens": 12})
    assert eng.last_usage["prompt"] == 300
    assert eng.tokens["completion"] == 12


def test_context_stats_prefers_upstream_then_falls_back_to_estimate():
    """上游优先：有请求记录时用服务商真实用量（输入+输出，标准主流口径）；否则回退本地估算"""
    eng = _engine()
    eng._messages = _msgs(4)
    est = eng._estimate_tokens()
    s = eng.context_stats()
    assert s["source"] == "estimate"
    assert s["used"] == est
    assert s["ratio"] == est / s["window"]

    eng._accum_usage({"prompt_tokens": 4321, "completion_tokens": 8})
    s = eng.context_stats()
    assert s["source"] == "upstream"
    assert s["used"] == 4321 + 8   # 占用 = 输入+输出
    # 上游值可能远大于本地估算（含 system/技能注入），面板以真实值为准
    assert s["used"] != est


def test_real_occupancy_prefers_upstream_then_estimate():
    """真实占用判定（压缩/硬裁的保护口径）与统计面板同一数据源：上游输入+输出优先，
    无上游数据时回退本地估算——这是「面板显示 80% 时请求已 400」根因的修复锚点"""
    eng = _engine()
    eng._messages = _msgs(3)
    assert eng._real_occupancy() == eng._estimate_tokens()      # 尚无请求 → 回退估算
    eng._accum_usage({"prompt_tokens": 700, "completion_tokens": 30})
    assert eng._real_occupancy() == 730                         # 有上游 → 输入+输出
    assert eng._real_occupancy() == eng.context_stats()["used"] # 与面板展示完全一致
    eng._invalidate_usage()                                     # 压缩/裁剪后失效
    assert eng._real_occupancy() == eng._estimate_tokens()


def test_context_stats_reports_thresholds_and_cumulative():
    """快照需自洽：阈值以「可用输入预算 = 窗口 − 预留输出」为基数，且累计与缓存率可核对"""
    eng = _engine()
    eng._accum_usage({"prompt_tokens": 500, "completion_tokens": 100},
                     {"hit": 300, "miss": 200})
    s = eng.context_stats()
    win = s["window"]
    budget = s["budget"]
    assert budget == win - s["max_output"]            # 预算是窗口扣掉预留输出
    assert s["thresholds"]["compress"] == int(budget * 0.75)
    assert s["thresholds"]["warn"] == int(budget * 0.70)
    assert s["thresholds"]["ceiling"] == min(int(budget * 0.95), budget - 8192)
    assert s["thresholds"]["recent"] == int(budget * 0.50)
    assert s["cumulative"] == {"prompt": 500, "completion": 100, "total": 600,
                               "cache_hit": 300, "cache_miss": 200}
    assert abs(s["cache_rate"] - 0.6) < 1e-9
    assert s["model"] == "ctx-test-model"
    assert s["compaction"]["count"] == 0 and s["warn"] is False
    json.dumps(s)   # 快照必须可序列化（日志/接口复用）


def test_context_stats_ratio_capped_and_cache_rate_absent_without_cache_data():
    """边界：上游返回超过窗口的异常值 → ratio 钳制为 1；无缓存明细 → 命中率为 None"""
    eng = _engine()
    eng._accum_usage({"prompt_tokens": eng._ctx_window * 3, "completion_tokens": 1})
    s = eng.context_stats()
    assert s["ratio"] == 1.0
    assert s["cache_rate"] is None


def test_reset_tokens_clears_upstream_occupancy():
    """新对话/清空上下文后不得残留上一轮的占用统计"""
    eng = _engine()
    eng._accum_usage({"prompt_tokens": 900, "completion_tokens": 10},
                     {"hit": 1, "miss": 1})
    eng.reset_tokens()
    s = eng.context_stats()
    assert s["source"] == "estimate"
    assert s["cumulative"]["total"] == 0
    assert s["cache_rate"] is None
    assert eng.last_usage_at == 0.0


def test_compression_invalidates_upstream_occupancy():
    """压缩后上下文已变短：上游旧占用值必须失效，回落本地估算（否则面板显示偏大）"""
    eng = _engine()
    eng._messages = _msgs(8)
    eng._accum_usage({"prompt_tokens": 99999, "completion_tokens": 5})
    assert eng.context_stats()["source"] == "upstream"

    merged = eng._auto_compress(keep_recent=2)
    assert merged > 0, "用例前提：本次应触发实际压缩"
    eng.last_estimate = eng._estimate_tokens()
    s = eng.context_stats()
    assert s["source"] == "estimate"
    assert s["used"] == eng.last_estimate < 99999


def test_hard_trim_invalidates_upstream_occupancy():
    """硬上限裁剪（丢弃最旧消息）同样使上游占用值失真"""
    eng = _engine()
    eng._messages = _msgs(6)
    eng._accum_usage({"prompt_tokens": 5000, "completion_tokens": 5})
    eng._hard_trim(1)   # 极限值：必然裁剪
    assert eng.context_stats()["source"] == "estimate"


def test_context_persistence_round_trip(tmp_path):
    """持久化：重启后累计与最近一次上游用量都要恢复（面板不用等下一次请求）"""
    path = tmp_path / "ctx.json"
    eng = _engine()
    eng._messages = _msgs(3)
    eng._accum_usage({"prompt_tokens": 700, "completion_tokens": 30},
                     {"hit": 400, "miss": 100})
    assert eng.save_context(str(path))

    eng2 = _engine()
    assert eng2.load_context(str(path)) == 3
    assert eng2.tokens["prompt"] == 700
    s = eng2.context_stats()
    assert s["source"] == "upstream"
    assert s["used"] == 700 + 30   # 占用 = 输入+输出
    assert s["last"]["cache_hit"] == 400


def test_legacy_context_file_without_last_usage_still_loads(tmp_path):
    """旧版会话文件（无 last_usage 字段）必须照常恢复，且统计回退估算不报错"""
    path = tmp_path / "ctx.json"
    path.write_text(json.dumps({"messages": _msgs(2),
                                "tokens": {"prompt": 120, "completion": 20}}),
                    encoding="utf-8")
    eng = _engine()
    assert eng.load_context(str(path)) == 2
    assert eng.tokens["prompt"] == 120
    s = eng.context_stats()
    assert s["source"] == "estimate"
    assert s["cumulative"]["total"] == 140


def test_context_stats_used_is_input_plus_output():
    """统计口径（标准主流）：占用 = 最近一次请求的输入+输出；明细仍分别可见"""
    eng = _engine()
    eng._accum_usage({"prompt_tokens": 4000, "completion_tokens": 500},
                     {"hit": 3000, "miss": 1000})
    s = eng.context_stats()
    assert s["used"] == 4500
    assert s["last"]["prompt"] == 4000 and s["last"]["completion"] == 500
