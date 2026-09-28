"""验证：@子Agent 直接调用的事件映射 + 多轮上下文保留（追问）。

覆盖三块：
1. 事件映射为标准主对话流事件（delta/正在执行/执行结果），不再发出 sub 子块事件；
2. _subagent_worker 把本轮对话写入会话状态 sub_history，二次调用时作为 history 注入；
3. run_sub_agent 真实逻辑：history 注入在 system 后、goal 前；on_history 结束时回传
   完整消息（不含 system）；_sanitize_history 剔除未配对的 assistant(tool_calls)。
"""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))
from PyQt6.QtWidgets import QApplication
app = QApplication([])
import zhuzhu_Copilot.core.agent_subagent as agent_subagent

# ---------------- 1. 事件映射 + 历史写回/注入（_subagent_worker） ----------------
events = []
_calls = []

class _Sig:
    def emit(self, sid, kind, payload):
        events.append((kind, payload))

def agent_panel_patched():
    import zhuzhu_Copilot.ui.agent_panel as ap
    p = ap.AgentPanel.__new__(ap.AgentPanel)
    p._sess = {"sid": {"workflow": "default"}}
    p.evt_signal = _Sig()
    p._sub_stop = None
    return p

panel = agent_panel_patched()

def fake_run(llm, goal, allowed=None, stop=None, on_sub_event=None, workflow=None,
             persona="", custom=False, history=None, on_history=None):
    _calls.append({"goal": goal, "history": list(history or [])})
    assert on_sub_event is not None
    on_sub_event("delta", "开始处理")
    on_sub_event("tool", "read_file path=README.md")
    on_sub_event("output", "README 内容摘要")
    on_sub_event("delta", "。总结完毕")
    if on_history is not None:
        on_history([{"role": "user", "content": goal}])   # 模拟 run_sub_agent 回传消息
    return "总结文本"

_real_run = agent_subagent.run_sub_agent
agent_subagent.run_sub_agent = fake_run

# 第一轮调用
panel._subagent_worker("sid", "learning_tutor", {"allowed": []}, "帮我查README", None)
assert events[0] == ("status", "正在思考…"), "应先发出思考态"
assert ("delta", "开始处理") in events, "delta 应映射为 delta"
assert ("status", "正在执行: read_file path=README.md") in events, "tool 应映射为 正在执行 status"
assert ("result", ("learning_tutor", "README 内容摘要", [])) in events, "output 应映射为 result"
assert not any(k == "sub" for k, _ in events), "不应再发出 sub 子块事件"
assert events[-1] == ("subagent_done", "总结文本"), "应以 subagent_done 收尾"
assert _calls and _calls[0]["history"] == [], "第一轮不应有历史"

# 第二轮追问：应注入第一轮写回的历史
panel._subagent_worker("sid", "learning_tutor", {"allowed": []}, "继续分析", None)
assert len(_calls) == 2 and _calls[1]["history"], "第二轮应注入上一轮历史"
assert _calls[1]["history"][0]["content"] == "帮我查README", "历史内容应为上一轮 user 消息"
assert (panel._sess["sid"].get("sub_history") or {}).get("learning_tutor"), \
    "会话状态应保存子 Agent 历史"

# 不同子 Agent 历史隔离
panel._subagent_worker("sid", "another_agent", {"allowed": []}, "其他任务", None)
assert not _calls[2]["history"], "不同子 Agent 不应共享历史"
print("RESULT: 事件映射 + 多轮历史写回/注入 PASS")

# 恢复真实 run_sub_agent，供第二部分验证真实逻辑
agent_subagent.run_sub_agent = _real_run

# ---------------- 2. run_sub_agent 真实逻辑（历史注入 + 回传） ----------------
class _FakeLLM:
    """假 LLM：按预设轮次依次返回，记录每次收到的完整 messages"""
    def __init__(self, rounds):
        self.rounds = list(rounds)
        self.seen = []
    def chat_stream(self, messages, tools=None, tool_choice=None, stop=None, on_delta=None):
        self.seen.append(list(messages))
        r = self.rounds.pop(0)
        return {"text": r.get("text", ""), "tool_calls": r.get("tool_calls", [])}

# 2a. 首轮：无历史；结束时 on_history 回传完整消息（不含 system）
llm1 = _FakeLLM([{"text": "第一轮结论"}])
saved1 = []
out = agent_subagent.run_sub_agent(llm1, "帮我查README", on_history=lambda m: saved1.append(m))
assert out == "第一轮结论", f"首轮输出异常: {out}"
assert len(saved1) == 1 and saved1[0][0]["role"] == "user", "回传消息应不含 system"
assert "帮我查README" in str(saved1[0][0]["content"]), "回传消息应含首轮 goal"
# 修复 A（最终回复入历史）：首轮历史须含最终 assistant 文本，否则追问时子 Agent 失忆
assert len(saved1[0]) == 2 and saved1[0][1]["role"] == "assistant", \
    f"首轮历史应含最终 assistant 回复，实际 {[m['role'] for m in saved1[0]]}"
assert "第一轮结论" in str(saved1[0][1].get("content") or ""), "assistant 应为最终回复正文"

# 2b. 第二轮追问：history 注入在 system 后、新 goal 前
llm2 = _FakeLLM([{"text": "第二轮结论"}])
saved2 = []
out2 = agent_subagent.run_sub_agent(llm2, "继续", history=saved1[0],
                                    on_history=lambda m: saved2.append(m))
assert out2 == "第二轮结论"
msgs2 = llm2.seen[0]
assert msgs2[0]["role"] == "system", "首条应为 system"
roles2 = [m["role"] for m in msgs2]
assert roles2 == ["system", "user", "assistant", "user"], \
    f"应注入完整首轮历史（user+最终回复）+ 新 goal，实际 {roles2}"
assert "帮我查README" in str(msgs2[1]["content"]), "历史应注入在 system 后"
assert "第一轮结论" in str(msgs2[2].get("content") or ""), "首轮最终回复应随历史注入（修复 A）"
assert "继续" in str(msgs2[3]["content"]), "新 goal 应在最后"
assert saved2[0][0]["role"] == "user", "第二轮回传同样不含 system"

# ---------------- 3. _sanitize_history：剔除未配对 tool_calls ----------------
h = [
    {"role": "user", "content": "第一轮"},
    {"role": "assistant", "content": "回答",
     "tool_calls": [{"id": "c1", "type": "function",
                     "function": {"name": "read_file", "arguments": "{}"}}]},
    {"role": "tool", "tool_call_id": "c1", "content": "结果"},
    {"role": "assistant", "content": "最终回答"},
    {"role": "assistant", "content": None,          # 中断残留：无配对 tool 回复 → 剔除
     "tool_calls": [{"id": "c2", "type": "function",
                     "function": {"name": "grep", "arguments": "{}"}}]},
]
clean = agent_subagent._sanitize_history(h)
assert len(clean) == 4, f"应剔除末尾未配对 assistant(tool_calls)，实际 {len(clean)} 条"
assert clean[-1]["role"] == "assistant" and not clean[-1].get("tool_calls"), "末条应为无 tool_calls 的 assistant"
assert agent_subagent._sanitize_history(None) == [], "None 历史应返回空"
assert agent_subagent._sanitize_history([]) == [], "空历史应返回空"
# 超长裁剪：保留尾部且首条不是 tool
long_h = [{"role": "user", "content": f"m{i}"} for i in range(40)]
long_h.append({"role": "assistant", "content": "尾", "tool_calls": [{"id": "z", "type": "function",
                                                                     "function": {"name": "grep", "arguments": "{}"}}]})
long_h.append({"role": "tool", "tool_call_id": "z", "content": "out"})
trimmed = agent_subagent._sanitize_history(long_h, max_keep=10)
assert len(trimmed) == 10 and trimmed[-1]["role"] == "tool", "超长裁剪应保留最近 10 条"
print("RESULT: run_sub_agent 历史注入/回传 + _sanitize_history PASS")

print("RESULT: ALL PASS")
