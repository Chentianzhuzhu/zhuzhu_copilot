"""回归：空响应绝不“提前成功” + 有限纠正重试，保证任务连续性。

历史修复：上游返回 200 但无正文且无工具调用（协议帧异常/静默空响应）时，
旧逻辑原样返回空结果 → 引擎判定“完成任务”→ 直接显示 Successfully。

现状策略（任务连续性优先）：
- LLM 层（chat/completions 与 Responses）：空响应不再抛错致命化，原样返回空结果。
- 引擎层：空响应注入纠正提示后重试有限次（_MAX_EMPTY_RESULT_RETRIES），
  达上限仍空才报错（绝不把空响应当 done）——长任务流中偶发空响应不再直接失败，
  也不会因失败 → 用户手动重试重跑 → ask_user 重复提问。
- ask_user 去重：同一问题在本次会话已答过，直接复用不二次弹窗。
"""
import os, sys, urllib.request
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath("src"))
from PyQt6.QtWidgets import QApplication
app = QApplication([])
import winapp_migrator.core.agent_llm as agent_llm
from winapp_migrator.core import agent_engine

class _FakeResp:
    def __init__(self, lines):
        self._lines = list(lines)
    def readline(self):
        return self._lines.pop(0) if self._lines else b""
    def close(self):
        pass

class _FakeReq:
    Request = urllib.request.Request
    def __init__(self, lines):
        self._lines = lines
    def urlopen(self, *a, **k):
        return _FakeResp(self._lines)

_orig_req = agent_llm._request_module
_orig_relax = agent_llm._relax_socket_timeout
misframes = [b"event: error\r\n", b"\r\n", b": ping\r\n"]   # 全是非 data: 帧
try:
    agent_llm._relax_socket_timeout = lambda *a, **k: None
    # 1) chat/completions 空响应 → 返回空结果（不再抛错），由引擎做纠正重试
    agent_llm._request_module = lambda: _FakeReq(misframes)
    c = agent_llm.LLMClient(model="test-model")
    out = c.chat_stream([{"role": "user", "content": "hi"}])
    assert out["text"] == "" and out["tool_calls"] == [], f"空响应返回值异常: {out}"
    print("1) chat/completions 空响应 → 返回空结果 PASS")
finally:
    agent_llm._request_module = _orig_req
    agent_llm._relax_socket_timeout = _orig_relax

# 2) 引擎兜底：空响应注入纠正提示有限重试，达上限仍空才报错（源码含守卫断言）
src = open(os.path.join(os.path.dirname(agent_engine.__file__), "agent_engine.py"),
           encoding="utf-8").read()
assert 'if not (result.get("text") or "").strip():' in src, "引擎空正文兜底守卫缺失"
tail = src.split('calls = result["tool_calls"]')[1]
assert "自动补充提示重试" in tail, "引擎空响应纠正重试缺失"
assert "_MAX_EMPTY_RESULT_RETRIES" in tail, "空响应重试上限常量缺失"
assert "AgentLLMError(" in tail, "引擎空响应达上限抛错缺失"
print("2) 引擎空响应纠正重试守卫存在 PASS")

# 3) 空响应在手动指定模式下直接报错（不回退），自动选择模式可回退内置模型
c3 = agent_llm.LLMClient(model="deepseek-v4")
c3.auto_fallback = False      # 手动指定：不回退
assert not c3._fallback_eligible(agent_llm.AgentLLMError("x")), "手动指定模型不应回退"
c3.auto_fallback = True
assert c3._fallback_eligible(agent_llm.AgentLLMError("x")), "自动选择模型应可回退"
print("3) 策略联动（auto_fallback 区分模型来源）PASS")

# 4) ask_user 去重：同一问题第二次调用不再弹窗（直接复用已答）
eng = agent_engine.AgentEngine(agent_llm.LLMClient(model="test-model"))
asked = []
eng.ask_user = lambda args: (asked.append(args), "好的")[1]
r1 = eng._execute("ask_user", {"question": "要确认吗？", "options": ["是"]})
assert r1["text"] == "好的" and len(asked) == 1, f"首次提问异常: {r1}, asked={asked}"
r2 = eng._execute("ask_user", {"question": "要确认吗？"})
assert len(asked) == 1, f"相同问题被二次弹窗: asked={asked}"
assert "此前已询问过" in r2["text"], f"去重回包格式异常: {r2}"
print("4) ask_user 同问题只询问一次 PASS")
print("ALL RESULT: PASS")