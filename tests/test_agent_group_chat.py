"""AI 群聊调度：发言权、上下文注入、轮数上限、停止与持久化。

不触网：用最小 FakeEngine 顶替 ``GroupChatRunner._engine_for`` —— 本测试验证的是
调度与记录逻辑，LLM 行为由引擎自身负责。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from zhuzhu_Copilot.core import agent_group_chat as gc


class _FakeEngine:
    """只做流式回放：把预设回复逐字推给 on_delta，行为与真实引擎的对外契约一致。"""

    def __init__(self, reply: str):
        self.reply = reply
        self.on_delta = None
        self.on_status = None
        self.confirm = None
        self.ask_user = None
        self._messages = []
        self._stop = None
        self.prompts = []

    def run(self, prompt, *_a, **_kw):
        self.prompts.append(prompt)
        for ch in self.reply:
            self.on_delta(ch)


@pytest.fixture()
def chats_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(gc, "chats_root", lambda: tmp_path)
    return tmp_path


def _chat(names=("甲", "乙"), max_rounds=2):
    return gc.new_chat([{"name": n, "persona": f"我是{n}"} for n in names],
                       max_rounds=max_rounds)


def test_persist_roundtrip(chats_dir):
    c = _chat()
    ok, _ = gc.save_chat(c)
    assert ok and (chats_dir / f"{c['id']}.json").is_file()
    d = gc.load_chat(c["id"])
    assert d["id"] == c["id"]
    assert [p["name"] for p in d["participants"]] == ["甲", "乙"]
    assert gc.list_chats()[0]["id"] == c["id"]
    assert gc.delete_chat(c["id"])[0] is True
    assert gc.load_chat(c["id"]) is None


def test_runner_respects_max_rounds_and_records(chats_dir):
    c = _chat(max_rounds=3)
    msgs = []
    ends = []
    r = gc.GroupChatRunner(c, {}, on_message=msgs.append, on_end=ends.append)
    r._engine_for = lambda p: _FakeEngine(f"{p['name']}的发言")
    r.run()
    assert len(msgs) == 3
    assert [m["speaker"] for m in msgs][0] in ("甲", "乙")
    assert all(m["text"].endswith("的发言") for m in msgs)
    assert "轮数上限" in ends[-1]


def test_handoff_decides_next_speaker(chats_dir):
    c = _chat(names=("甲", "乙", "丙"), max_rounds=1)
    r = gc.GroupChatRunner(c, {})
    r._last_reply = "@丙\n我讲完了"
    nxt = r._next_speaker(c["participants"][0], c["participants"])
    assert nxt["name"] == "丙"


def test_turn_prompt_carries_transcript_and_first_command(chats_dir):
    c = _chat()
    r = gc.GroupChatRunner(c, {})
    p = c["participants"][0]
    r._emit("乙", "先抛一个观点")
    prompt = r._turn_prompt(p, first=True)
    assert "[乙] 先抛一个观点" in prompt
    assert "讨论开始" in prompt
    later = r._turn_prompt(p, first=False)
    assert "轮到你" in later


def test_stop_prevents_further_turns(chats_dir):
    c = _chat(max_rounds=99)
    msgs = []
    r = gc.GroupChatRunner(c, {}, on_message=msgs.append)

    def _engine(p):
        e = _FakeEngine("一句话")
        orig = e.run

        def run(prompt, *_a, **_kw):
            orig(prompt)
            r._stop.set()      # 第一轮发言结束后用户点「结束」
        e.run = run
        return e

    r._engine_for = _engine
    r.run()
    assert len(msgs) == 1


def test_user_interjection_visible_next_turn(chats_dir):
    c = _chat(max_rounds=1)
    r = gc.GroupChatRunner(c, {})
    assert r.say("   ") is False
    assert r.say("我插一句") is True
    assert "我插一句" in r._transcript()
    assert r._messages[-1]["user"] is True
