"""全链路复现：AI 用 write_file 创建新文件后，引擎快照 _last_edit_delta 是否含新增行数。

用桩 LLM 驱动真实 AgentEngine.run()：第 1 轮返回 write_file 工具调用（新文件），
第 2 轮返回无工具正文 → 任务 done。检查快照 added 应为新文件行数。
"""
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from winapp_migrator.core import agent_engine, agent_tools


class _StubLLM:
    """两阶段的桩 LLM：先发工具调用，再收尾正文（真实驱动引擎执行循环）"""
    model = "stub-model"

    def __init__(self, tool_responses):
        self._seq = tool_responses   # [(text, tool_calls_or_None), ...]
        self._i = 0
        self.fell_back = False

    def chat_stream(self, messages, tools=None, tool_choice="auto",
                    on_delta=None, on_reasoning=None, stop=None, **kw):
        step = self._i
        self._i += 1
        text, calls = self._seq[min(step, len(self._seq) - 1)]
        if on_delta and text:
            on_delta(text)
        return {"text": text, "tool_calls": calls or [],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                "cache": None}


tmp_dir = Path(tempfile.mkdtemp(prefix="e2e_delta_"))
target = tmp_dir / "new_file.txt"
edit_target = tmp_dir / "edit_file.txt"
edit_target.write_text("a\nb\nc\nd\n", encoding="utf-8")  # 已有 4 行，供后续编辑

write_call = {
    "id": "call_1",
    "type": "function",
    "function": {"name": "write_file",
                 "arguments": json.dumps({"path": str(target), "content": "1\n2\n3\n4\n"})},
}
# 编辑：old=现有 a\nb\nc\nd\n → new=a\nX\nY\nc\nd\n（删 1 b 行 + 增 1 X/Y 段两次替换）
# difflib 视角：b→X/Y 视为 replace 1 行删 2 行增 → +2 -1
edit_call = {
    "id": "call_2",
    "type": "function",
    "function": {"name": "edit_file",
                 "arguments": json.dumps({"path": str(edit_target),
                                          "old_text": "b\n", "new_text": "X\nY\n"})},
}
stub = _StubLLM([
    ("正在创建文件…", [write_call]),
    ("正在编辑文件…", [edit_call]),
    ("全部完成", None),
])

# 直接模式 + 禁 TTS + 禁网络 LLM：仅验证工具执行与变更统计链路
import winapp_migrator.core.agent_tts as _tts
_orig_cfg = _tts.load_config
_tts.load_config = lambda: {"auto_read": False, "voice_id": "", "api_key": ""}
tid = threading.get_ident()
_reset_n = {"n": 0}
_orig_reset = agent_tools.reset_edit_delta
_orig_take = agent_tools.take_edit_delta
_orig_rec = agent_tools._record_edit_delta
_orig_notify = agent_tools._notify_file_changed


def _dbg_reset():
    _reset_n["n"] += 1
    if _reset_n["n"] <= 3:
        import traceback
        print("[dbg] reset #%d thread=%s\n%s"
              % (_reset_n["n"], threading.get_ident(),
                 "".join(traceback.format_stack()[-5:-1])))
    return _orig_reset()


def _dbg_take():
    print("[dbg] take   thread=%s" % threading.get_ident())
    return _orig_take()


def _dbg_record(path, old, new):
    d = getattr(agent_tools._EDIT_DELTA, "d", "MISSING")
    print("[dbg] record thread=%s old_len=%d new_len=%d ctx=%s"
          % (threading.get_ident(), len(old or ""), len(new or ""),
             type(d).__name__ if d != "MISSING" else d))
    _orig_rec(path, old, new)


def _dbg_notify(path, old, new):
    print("[dbg] notify thread=%s path=%s old_len=%d new_len=%d"
          % (threading.get_ident(), path, len(old or ""), len(new or "")))
    _orig_notify(path, old, new)


agent_tools.reset_edit_delta = _dbg_reset
agent_tools.take_edit_delta = _dbg_take
agent_tools._notify_file_changed = _dbg_notify
agent_tools._record_edit_delta = _dbg_record

try:
    eng = agent_engine.AgentEngine(
        stub, confirm=None, on_delta=lambda s: None, on_status=lambda s: None,
        on_result=lambda n, t, im: None, direct=True, memory_enabled=False,
        persona=None)
    eng.run("在新文件里写入四行内容")
    agent_tools.take_edit_delta = _dbg_take
    print("[dbg] main thread=%s" % tid)
finally:
    _tts.load_config = _orig_cfg
    agent_tools.reset_edit_delta = _orig_reset
    agent_tools.take_edit_delta = _orig_take
    agent_tools._notify_file_changed = _orig_notify
    agent_tools._record_edit_delta = _orig_rec

state = eng.end_state
delta = eng._last_edit_delta

print("end_state:", state)
print("delta:", delta)
assert state == "done", f"任务应完成（{state}）"
# 新建文件：write_file 写入 4 行
new_key = str(target)
assert new_key in delta["files"], f"新建文件应进入 files（实际 {list(delta['files'])})"
na, nr = delta["files"][new_key]
assert (na, nr) == (4, 0), f"新建文件应 +4 -0（实际 +{na} -{nr}）"
assert target.exists() and target.read_text() == "1\n2\n3\n4\n", "文件应已真实创建"
# 编辑文件：b→X,Y 视为 1 行删 2 行增（replace 段：-1 +2）
edit_key = str(edit_target)
assert edit_key in delta["files"], f"编辑文件应进入 files（实际 {list(delta['files'])})"
ea, er = delta["files"][edit_key]
assert (ea, er) == (2, 1), f"编辑 b→X\\nY 应 +2 -1（实际 +{ea} -{er}）"
# 整体聚合：6 行增 1 行删
assert delta["added"] == 6 and delta["removed"] == 1, \
    f"整体聚合应 +6 -1（实际 +{delta['added']} -{delta['removed']}）"
print(f"[OK] 新建文件 {target.name}  +4 -0")
print(f"[OK] 编辑文件 {edit_target.name}  +2 -1")
print("[OK] 整体聚合 +6 -1，气泡末尾 _add_change_badge 数据源就绪")
print("全链路复现通过")