"""回归验证：@子Agent 上下文保留修复（补充官方 _verify_subagent_flow.py 未覆盖的两点）。

覆盖：
1. 修复 A：run_sub_agent 无工具调用直接返回最终文本时，该条 assistant 消息必须进入
   messages（on_history 回传完整历史，不再只存 user 消息）——「子 Agent 回答丢失、
   追问失忆」的根因。
2. 修复 B：AgentPanel._inject_subagent_context 把 @子Agent 对话回合同步进主 Agent
   引擎 _messages（主 Agent 普通提问可延续子 Agent 上下文），游标幂等、不重复注入。
"""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))
from PyQt6.QtWidgets import QApplication
app = QApplication([])
import winapp_migrator.core.agent_subagent as agent_subagent

# ---------------- 1. 修复 A：最终文本回复必须进历史 ----------------
class _FakeLLM:
    def __init__(self, rounds):
        self.rounds = list(rounds)
        self.seen = []
    def chat_stream(self, messages, tools=None, tool_choice=None, stop=None, on_delta=None):
        self.seen.append(list(messages))
        r = self.rounds.pop(0)
        return {"text": r.get("text", ""), "tool_calls": r.get("tool_calls", [])}

# 第一轮：子 Agent 无工具调用直接文本回复
llm1 = _FakeLLM([{"text": "你好！我是学习助手，有什么可以帮你？"}])
saved1 = []
out1 = agent_subagent.run_sub_agent(llm1, "hi", on_history=lambda m: saved1.append(m))
assert out1 == "你好！我是学习助手，有什么可以帮你？"
h1 = saved1[0]
assert len(h1) == 2, f"首轮历史应为 [user, assistant] 两条，实际 {len(h1)}: {[m['role'] for m in h1]}"
assert h1[0]["role"] == "user" and h1[1]["role"] == "assistant", \
    "修复 A 失败：最终文本回复未进入历史（首轮只剩 user 消息 → 追问必失忆）"
assert "学习助手" in str(h1[1].get("content") or ""), "assistant 消息应含最终回复正文"

# 第二轮追问：history 注入首轮完整历史（含 assistant 回复），模型应能看到上轮回答
llm2 = _FakeLLM([{"text": "刚才你说你是学习助手，请继续"}])
saved2 = []
out2 = agent_subagent.run_sub_agent(llm2, "我们说了什么", history=h1,
                                    on_history=lambda m: saved2.append(m))
assert out2 == "刚才你说你是学习助手，请继续"
msgs2 = llm2.seen[0]
roles2 = [m["role"] for m in msgs2]
assert roles2 == ["system", "user", "assistant", "user"], \
    f"第二轮 messages 顺序应为 system+历史(逐条)+goal，实际 {roles2}"
assert "你好！我是学习助手" in str(msgs2[2].get("content") or ""), \
    "第二轮应能看到第一轮的 assistant 回复（否则追问即失忆）"
assert "我们说了什么" in str(msgs2[3].get("content") or ""), "新 goal 应在最后"
print("RESULT: 修复A（最终文本回复入历史 + 追问可复用）PASS")

# ---------------- 2. 修复 B：@子Agent 回合同步进主 Agent 引擎上下文 ----------------
import winapp_migrator.ui.agent_panel as ap
p = ap.AgentPanel.__new__(ap.AgentPanel)

class _FakeEngine:
    def __init__(self):
        self._messages = []
p._engine_for_called = 0
def _fake_engine_for(sid):
    p._engine_for_called += 1
    return p._sess.setdefault(sid, {}).get("engine")
p._engine_for = _fake_engine_for
p._sess = {"sid": {"workflow": "default"}}

# 会话：learning_tutor 已完成两轮 @ 对话（sub_history 已有完整历史）
p._sess["sid"]["sub_history"] = {"learning_tutor": [
    {"role": "user", "content": [{"type": "text", "text": "hi"}]},
    {"role": "assistant", "content": "你好！我是学习助手，有什么可以帮你？"},
    {"role": "user", "content": [{"type": "text", "text": "我们说了什么"}]},
    {"role": "assistant", "content": "你刚才说 hi，我介绍了自己是学习助手。"},
]}
fake_eng = _FakeEngine()
p._sess["sid"]["engine"] = fake_eng

p._inject_subagent_context("sid")
assert len(fake_eng._messages) == 1, "应注入一条 user 上下文消息"
m0 = fake_eng._messages[0]
assert m0["role"] == "user", "注入消息应为 user 角色"
texts = [x.get("text") for x in m0["content"]
         if isinstance(x, dict) and x.get("type") == "text"]
joined = "\n".join(texts or [])
assert "hi" in joined and "学习助手" in joined, "应包含用户提问与子 Agent 回复"
assert "tool" not in joined.lower(), "不应注入工具过程内容"

# 游标已推进 → 再次调用不重复注入
p._inject_subagent_context("sid")
assert len(fake_eng._messages) == 1, "游标幂等失败：未新增内容却重复注入"

# 新增一轮 @ 对话 → 只增量注入新回合
p._sess["sid"]["sub_history"]["learning_tutor"].append(
    {"role": "user", "content": [{"type": "text", "text": "第三轮问题"}]})
p._sess["sid"]["sub_history"]["learning_tutor"].append(
    {"role": "assistant", "content": "第三轮回答"})
p._inject_subagent_context("sid")
assert len(fake_eng._messages) == 2, "新增回合应只追加一条注入消息"
assert "第三轮回答" in str(fake_eng._messages[-1]["content"] or ""), "增量应含新增回合内容"
print("RESULT: 修复B（@子Agent 回合同步主 Agent 上下文 + 游标幂等）PASS")

# 不同子 Agent 各自独立游标
p._sess["sid"]["sub_history"]["coder"] = [
    {"role": "user", "content": [{"type": "text", "text": "帮我查代码"}]},
    {"role": "assistant", "content": "已定位 bug 在 x.py"}]
p._inject_subagent_context("sid")
assert len(fake_eng._messages) == 3, "不同子 Agent 应分别同步"
print("RESULT: 修复B（子 Agent 间游标隔离）PASS")

print("RESULT: ALL PASS")
