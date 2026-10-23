"""agent_engine 回归：强制中断（停止）全路径一致性 + 前置拦截徽章。

用户需求：执行任务时被强制中断 与 再次尝试（重试）时被强制中断，
          提示文字与徽章样式必须一致。

徽章由 end_state 驱动（UI._show_end_badge：done→Successfully /
stopped→Stop by user / error→Error）；提示文字由 on_status 驱动。本测试锁定：

1. 五类停止出口全部产出 end_state="stopped" 且只发一条「已停止」状态，
   无任何自定义变体文案（曾经的「已停止（被领导者暂停后终止）」与
   「工具执行中停止完全不发状态」都会造成同一次中断在不同位置提示不一致）；
2. 重试（start 再跑一轮，_stop 已清零）后中断与首次中断的
   (end_state, 停止相关状态列表) 完全一致；
3. 前置拦截（未登录 / 0 积分）end_state="error" → 红色 Error 徽章，
   不再是绿色 Successfully（拦截=失败，不得误报成功）；
4. 工具执行中/预处理中被停止时，未执行工具的 tool_calls 仍配对完整
   （防下一轮发送上游 400）。
"""
import json

from zhuzhu_Copilot.core import agent_engine
from zhuzhu_Copilot.core import agent_llm


class _StopLLM:
    """可编程桩 LLM：每次 chat_stream 先执行副作用（通常置 eng._stop），
    再返回预设响应或直接抛预设异常。script 元素为 (副作用, 响应|异常)，
    最后一个元素重复使用。"""

    def __init__(self, get_eng, script):
        self._get_eng = get_eng
        self.script = list(script)
        self.calls = 0
        self.model = "test-model"   # infer_context_window 读取
        self.fell_back = False
        self.silent_fallback = False

    def chat_stream(self, messages, **kw):
        self.calls += 1
        fx, r = self.script[min(self.calls - 1, len(self.script) - 1)]
        if fx:
            fx(self._get_eng())
        if isinstance(r, Exception):
            raise r
        return dict(r)


def _tool_resp(*calls):
    return {"text": "", "tool_calls": list(calls),
            "usage": None, "cache": {"hit": 0, "miss": 0}}


def _call(cid, name="web_fetch"):
    # web_fetch：非 _CONCURRENT_TOOLS（走串行分支）、非 _DEV_TOOLS（无规则拦截）、
    # 非子 Agent 工具，且 _execute 在测试中整体替身，不会发生真实网络请求
    return {"id": cid, "type": "function",
            "function": {"name": name, "arguments": json.dumps({})}}


def _make_engine(llm, monkeypatch, **kw):
    # 关闭自动朗读，避免测试触发真实 TTS 网络合成
    monkeypatch.setattr(agent_engine.agent_tts, "load_config",
                        lambda: {"auto_read": False})
    # 隔离「工具管控」用户设置：本机把「禁用全部工具」打开时工具会被执行层硬拦
    monkeypatch.setattr(agent_engine.agent_sandbox, "disabled_tools", lambda: frozenset())
    monkeypatch.setattr(agent_engine.agent_sandbox, "tools_disabled_all", lambda: False)
    return agent_engine.AgentEngine(llm, text_only=True, **kw)


def _stop_feedback(eng, statuses):
    """一次运行的「中断反馈」：(end_state, 停止相关状态列表)。

    徽章样式只由 end_state 决定；提示文字 = 状态流里与停止相关的条目。
    一致性断言直接比较这个元组。
    """
    stop_states = [s for s in statuses if "已停止" in s]
    return (eng.end_state, stop_states)


def _assert_uniform_stop(eng, statuses):
    """所有停止出口都必须满足：stopped + 恰好一条「已停止」+ 无变体文案。"""
    fb = _stop_feedback(eng, statuses)
    assert fb == ("stopped", ["已停止"]), \
        f"中断反馈不一致：end_state={eng.end_state!r}，停止状态={fb[1]!r}"
    # 未执行工具的 tool_calls 必须配对完整（防下一轮上游 400）
    tool_msgs = [m for m in eng._messages if m.get("role") == "tool"]
    call_ids = {c["id"] for m in eng._messages
                for c in (m.get("tool_calls") or [])}
    answered = {m.get("tool_call_id") for m in tool_msgs}
    assert call_ids <= answered, f"tool_calls 未配对完整：{call_ids - answered}"


# ---------- 1. 各停止出口一致性 ----------

def test_stop_at_loop_top(monkeypatch):
    """中断落在工具循环顶部（执行过程中用户点停止，下一轮检查命中）。"""
    cell = {}
    llm = _StopLLM(lambda: cell["eng"], [(None, _tool_resp(_call("t1")))])
    eng = cell["eng"] = _make_engine(llm, monkeypatch)

    def _fake_execute(name, args, allow_dangerous=False):
        eng._stop.set()   # 模拟执行过程中用户点击停止
        return {"text": "ok", "images": []}
    eng._execute = _fake_execute

    st = []
    eng.on_status = st.append
    eng.run("执行中被停止")

    _assert_uniform_stop(eng, st)
    assert llm.calls == 1, "停止后不应再发一轮 LLM 请求"


def test_stop_during_tool_preprocessing(monkeypatch):
    """中断落在工具预处理阶段（LLM 响应已回、尚未执行任何工具）。"""
    cell = {}

    def _set_stop(eng):
        eng._stop.set()
    llm = _StopLLM(lambda: cell["eng"],
                   [(_set_stop, _tool_resp(_call("t1"), _call("t2")))])
    eng = cell["eng"] = _make_engine(llm, monkeypatch)

    st = []
    eng.on_status = st.append
    eng.run("预处理中被停止")

    _assert_uniform_stop(eng, st)
    # 两个未执行调用都补了配对 tool 消息
    paired = [m for m in eng._messages
              if m.get("role") == "tool" and "该工具未执行" in str(m.get("content"))]
    assert len(paired) == 2, f"未执行工具的配对回复应补齐 2 条，实际 {len(paired)}"


def test_stop_during_serial_execution(monkeypatch):
    """中断落在串行执行阶段（确认弹窗返回后、工具执行前检查命中）。"""
    cell = {}

    def _confirm(name, args):
        cell["eng"]._stop.set()   # 用户确认的同时点击了停止
        return True
    llm = _StopLLM(lambda: cell["eng"], [(None, _tool_resp(_call("t1")))])
    eng = cell["eng"] = _make_engine(llm, monkeypatch, confirm=_confirm)

    executed = []
    real_execute = eng._execute

    def _spy_execute(name, args, allow_dangerous=False):
        executed.append(name)
        return real_execute(name, args, allow_dangerous=allow_dangerous)
    eng._execute = _spy_execute

    st = []
    eng.on_status = st.append
    eng.run("串行执行前被停止")

    _assert_uniform_stop(eng, st)
    assert executed == [], "停止命中后不应真正执行任何工具"


def test_stop_during_llm_stream(monkeypatch):
    """中断落在 LLM 流式请求中（LLM 层抛「已停止」）。"""
    cell = {}

    def _set_stop(eng):
        eng._stop.set()
    llm = _StopLLM(lambda: cell["eng"],
                   [(_set_stop, agent_llm.AgentLLMError("已停止"))])
    eng = cell["eng"] = _make_engine(llm, monkeypatch)

    st = []
    eng.on_status = st.append
    eng.run("流式中被停止")

    _assert_uniform_stop(eng, st)


def test_stop_during_generic_exception(monkeypatch):
    """中断落在任意异常路径（异常 + _stop 已置位 → stopped 而非 error）。"""
    cell = {}

    def _set_stop(eng):
        eng._stop.set()
    llm = _StopLLM(lambda: cell["eng"], [(_set_stop, RuntimeError("boom"))])
    eng = cell["eng"] = _make_engine(llm, monkeypatch)

    st = []
    eng.on_status = st.append
    eng.run("异常中被停止")

    _assert_uniform_stop(eng, st)


# ---------- 2. 重试（再次尝试）后中断与首次完全一致 ----------

def test_retry_then_stop_identical(monkeypatch):
    """首次执行被中断 → 重试再跑 → 再次被中断：提示文字与徽章状态一致。"""
    cell = {}

    def _set_stop(eng):
        eng._stop.set()
    llm = _StopLLM(lambda: cell["eng"],
                   [(_set_stop, _tool_resp(_call("t1"), _call("t2")))])
    eng = cell["eng"] = _make_engine(llm, monkeypatch)

    st = []
    eng.on_status = st.append

    # 第一次执行（发送即被中断）
    eng.start("第一次执行")
    eng.join()
    first = _stop_feedback(eng, st)

    # 第二次尝试（UI 重试按钮：start 会清零 _stop 后重跑同一请求）
    st.clear()
    eng.start("第一次执行")
    eng.join()
    second = _stop_feedback(eng, st)

    assert first == second, f"首次与重试的中断反馈不一致：{first} vs {second}"
    assert first == ("stopped", ["已停止"]), f"中断反馈不符合统一口径：{first}"


# ---------- 3. 前置拦截：Error 徽章，不是 Successfully ----------

class _FakeAuth:
    def __init__(self, logged_in=True, points=0):
        self._logged_in = logged_in
        self._points = points

    def is_logged_in(self):
        return self._logged_in

    def get_points(self):
        return self._points


def test_block_not_logged_in_shows_error_state(monkeypatch):
    """未登录拦截：end_state=error（Error 徽章），且文案经 on_delta 可见。"""
    monkeypatch.setattr(agent_engine, "_is_builtin_base_url", lambda url: True)
    monkeypatch.setattr(agent_engine, "_get_auth_client",
                        lambda: _FakeAuth(logged_in=False))
    eng = _make_engine(_StopLLM(lambda: None, []), monkeypatch)

    deltas, st = [], []
    eng.on_delta = deltas.append
    eng.on_status = st.append
    eng.run("未登录发送")

    assert eng.end_state == "error", \
        f"未登录拦截应报错（Error 徽章），实际 end_state={eng.end_state!r}"
    assert deltas and "未登录" in deltas[0], "拦截文案未显示给用户"
    # 徽章口径：error → Error（红），绝不得是 done → Successfully（绿）
    assert eng.end_state != "done"


def test_block_zero_points_shows_error_state(monkeypatch):
    """0 积分拦截：end_state=error（Error 徽章），且文案经 on_delta 可见。"""
    monkeypatch.setattr(agent_engine, "_is_builtin_base_url", lambda url: True)
    monkeypatch.setattr(agent_engine, "_get_auth_client",
                        lambda: _FakeAuth(logged_in=True, points=0))
    eng = _make_engine(_StopLLM(lambda: None, []), monkeypatch)

    deltas = []
    eng.on_delta = deltas.append
    eng.run("0 积分发送")

    assert eng.end_state == "error", \
        f"0 积分拦截应报错（Error 徽章），实际 end_state={eng.end_state!r}"
    assert deltas and "积分不足" in deltas[0], "积分不足文案未显示给用户"
    assert eng.end_state != "done", "拦截不得显示 Successfully 徽章"
