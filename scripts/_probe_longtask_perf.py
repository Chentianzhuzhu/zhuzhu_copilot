"""长任务性能剖析：把 _run_inner 每轮重复做的事分别计时，定位热点。

做法：用桩 LLM 返回「一次工具调用 + 最终收尾」，循环 N 轮；_execute 打成廉价桩，
从而测得的耗时**纯属循环本身的开销**（提示词重建 / 工具 schema 组装 / token 估算 /
清单与技能同步 / 图片修剪 / 工具输出压缩），不含真实工具与网络耗时。
"""
import json
import os
import sys
import time

sys.path.insert(0, "src")

from winapp_migrator.core import agent_engine   # noqa: E402
from winapp_migrator.core import agent_workflow  # noqa: E402

ROUNDS = 120

COST = {}
COUNT = {}


def _wrap(eng, name):
    fn = getattr(eng, name, None)
    if fn is None:
        return
    def timed(*a, **kw):
        t0 = time.perf_counter()
        try:
            return fn(*a, **kw)
        finally:
            dt = time.perf_counter() - t0
            COST[name] = COST.get(name, 0.0) + dt
            COUNT[name] = COUNT.get(name, 0) + 1
    setattr(eng, name, timed)


class _LoopLLM:
    model = "test-model"
    fell_back = False
    silent_fallback = False

    def __init__(self, rounds):
        self.rounds = rounds
        self.calls = 0

    def chat_stream(self, messages, **kw):
        self.calls += 1
        if self.calls > self.rounds:
            return {"text": "完成", "tool_calls": [], "usage": None,
                    "cache": {"hit": 0, "miss": 0}}
        call = {"id": f"c{self.calls}", "type": "function",
                "function": {"name": "list_directory", "arguments": '{"path": "."}'}}
        return {"text": "", "tool_calls": [call], "usage": None,
                "cache": {"hit": 0, "miss": 0}}


def main():
    agent_engine.agent_tts.load_config = lambda: {"auto_read": False}
    eng = agent_engine.AgentEngine(_LoopLLM(ROUNDS), text_only=True)
    eng.auto_vd = False
    eng.on_status = None
    eng.on_result = None
    eng.on_reasoning = None
    # 工具执行打成廉价桩：隔离「循环开销」与「真实工具/网络开销」
    eng._execute = lambda name, args, allow_dangerous=False: {
        "text": "ok: 目录列表 20 项 " + ("x" * 200), "images": []}
    for nm in ("_system_prompt", "_all_tools", "_sync_skill_msg", "_sync_todo_msg",
               "_estimate_tokens", "_prune_images", "_condense_tool_text",
               "_auto_compress", "_hard_trim"):
        _wrap(eng, nm)

    wf = os.environ.get("PROBE_WF") or agent_workflow.active_workflow()
    if wf and agent_workflow.is_workflow(wf):
        eng.workflow = wf
    print(f"工作流 = {eng.workflow!r} | 轮数 = {ROUNDS} | 会话消息数 = {len(eng._messages)}")

    t0 = time.perf_counter()
    eng.run("请逐项检查这个项目并给出一份完整的长任务报告，需要多轮工具调用")
    total = time.perf_counter() - t0

    print(f"\n总耗时 {total:.3f}s（{ROUNDS} 轮，即 {total / ROUNDS * 1000:.1f} ms/轮）")
    print(f"消息数 = {len(eng._messages)}")
    print("\n各环节累计耗时（按总耗时降序）：")
    rows = sorted(COST.items(), key=lambda kv: -kv[1])
    for name, sec in rows:
        n = COUNT.get(name, 0)
        if sec < 0.0005:
            continue
        print(f"  {name:22s} {sec:8.3f}s  调用 {n:4d} 次  均 {sec / max(n, 1) * 1000:8.3f} ms")
    accounted = sum(COST.values())
    print(f"\n计入合计 {accounted:.3f}s / 总 {total:.3f}s（占比 {accounted / total * 100:.1f}%）")


if __name__ == "__main__":
    main()
