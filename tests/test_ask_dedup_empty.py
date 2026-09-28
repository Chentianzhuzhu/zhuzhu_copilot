"""agent_engine 回归：ask_user 去重 + 上游空响应有限纠正重试。

覆盖两个修复点：
1. 同一问题只在本次会话询问一次：模型同批/重试重跑/长任务偶发失败后再次
   发出相同 ask_user 时，直接复用已答内容，不再二次弹窗。
2. 上游返回空响应（无正文且无工具调用）不致命：注入纠正提示重试有限次，
   达上限仍空才报错；绝不把空响应当「完成任务」显示 Successfully。
"""
import json

from zhuzhu_Copilot.core import agent_engine


class _SeqLLM:
    """按调用序号依次返回预设响应（最后一个重复使用）；元素为 dict=正常响应,
    Exception=直接抛错。绑定任意换行：chat_stream(messages, **kw)。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self.model = "test-model"   # infer_context_window 读取
        self.fell_back = False
        self.silent_fallback = False

    def chat_stream(self, messages, **kw):
        self.calls += 1
        r = self.responses[min(self.calls - 1, len(self.responses) - 1)]
        if isinstance(r, Exception):
            raise r
        return dict(r)


def _empty():
    return {"text": "", "tool_calls": [], "usage": None, "cache": {"hit": 0, "miss": 0}}


def _done(text="完成"):
    return {"text": text, "tool_calls": [], "usage": None, "cache": {"hit": 0, "miss": 0}}


def _ask_call(cid, question, options=None):
    args = {"question": question}
    if options:
        args["options"] = options
    return {"id": cid, "type": "function",
            "function": {"name": "ask_user",
                         "arguments": json.dumps(args)}}


def _make_engine(llm, monkeypatch):
    # 关闭自动朗读，避免测试触发真实 TTS 网络合成
    monkeypatch.setattr(agent_engine.agent_tts, "load_config",
                        lambda: {"auto_read": False})
    # 隔离「工具管控」用户设置：本机把「禁用全部工具」打开时，工具会被执行层硬拦，
    # 使本测试假失败（与代码无关）。测试固定为「全部工具可用」。
    monkeypatch.setattr(agent_engine.agent_sandbox, "disabled_tools", lambda: frozenset())
    monkeypatch.setattr(agent_engine.agent_sandbox, "tools_disabled_all", lambda: False)
    return agent_engine.AgentEngine(llm, text_only=True)


def _run_statuses(eng):
    st = []
    eng.on_status = st.append
    return st


def test_ask_user_same_question_dedup(monkeypatch):
    """同一问题重复发出：弹窗只出现一次，第二次直接复用已答内容。"""
    llm = _SeqLLM([
        {"text": "", "tool_calls": [_ask_call("t1", "要确认吗？", ["是"])],
         "usage": None, "cache": {"hit": 0, "miss": 0}},
        {"text": "", "tool_calls": [_ask_call("t2", "要确认吗？")],
         "usage": None, "cache": {"hit": 0, "miss": 0}},
        _done(),
    ])
    eng = _make_engine(llm, monkeypatch)
    asked = []
    eng.ask_user = lambda a: (asked.append(a), "好的")[1]
    _run_statuses(eng)
    eng.run("完成一次带确认的长任务")

    assert eng.end_state == "done"
    assert len(asked) == 1, f"相同问题被弹窗 {len(asked)} 次，应仅 1 次"
    assert llm.calls == 3   # 第三轮正常收尾（未被死循环）
    # 第二次重复提问的工具回复是“去重回包”，不是再次弹窗的用户回答
    tool_texts = [m["content"] for m in eng._messages
                  if m.get("role") == "tool"]
    assert any("此前已询问过" in str(t) for t in tool_texts), "第二次提问未走去重回包"


def test_ask_user_different_question_not_dedup(monkeypatch):
    """不同问题仍需再次弹窗（去重只按问题文本）。"""
    eng = _make_engine(_SeqLLM([]), monkeypatch)
    asked = []
    eng.ask_user = lambda a: (asked.append(a), "A")[1]
    eng._execute("ask_user", {"question": "问题一"})
    eng._execute("ask_user", {"question": "问题二"})
    assert len(asked) == 2


def test_empty_response_corrective_retry_then_done(monkeypatch):
    """上游空响应：注入纠正提示后继续，绝不提前 done。"""
    llm = _SeqLLM([_empty(), _empty(), _done()])
    eng = _make_engine(llm, monkeypatch)
    st = _run_statuses(eng)
    eng.run("测试空响应自我修复")

    assert llm.calls == 3
    assert eng.end_state == "done", f"应正常完成，实际: {eng.end_state} ({st})"
    retried = [s for s in st if "自动补充提示重试" in s]
    assert len(retried) == 2, f"纠正提示重试次数异常: {retried}"
    assert eng._empty_retries == 0, "完成后空响应计数应归零"
    # 纠正提示已进上下文
    assert any("[系统提示]" in str(m.get("content") or "")
               for m in eng._messages if m.get("role") == "user")


def test_empty_response_error_after_bound(monkeypatch):
    """空响应达上限仍空：明确报错（end_state=error），且不是 done。"""
    llm = _SeqLLM([_empty(), _empty(), _empty(), _empty()])
    eng = _make_engine(llm, monkeypatch)
    st = _run_statuses(eng)
    eng.run("测试空响应兜底报错")

    assert llm.calls == 3   # 前 2 次空响应纠正重试，第 3 次达上限报错
    assert eng.end_state == "error", f"应报错而非完成，实际: {eng.end_state} ({st})"
    assert any("连续 3 次返回空响应" in s for s in st), "未给出连续空响应报错信息"


def test_empty_response_never_done_without_text(monkeypatch):
    """空响应 + 无文本 → end_state 不得是 done（防止误报 Successfully）。"""
    llm = _SeqLLM([_empty(), _empty(), _empty(), _empty()])
    eng = _make_engine(llm, monkeypatch)
    _run_statuses(eng)
    eng.run("空响应不得提前成功")
    assert eng.end_state != "done"