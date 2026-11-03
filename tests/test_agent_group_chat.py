"""AI 群聊调度：发言权、共同上下文增量、轮数上限、暂停/停止与持久化。

不触网：用最小 FakeEngine 顶替 ``GroupChatRunner._engine_for`` —— 本测试验证的是
调度与记录逻辑，LLM 行为由引擎自身负责。
"""

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from zhuzhu_Copilot.core import agent_agents, agent_avatar, agent_context
from zhuzhu_Copilot.core import agent_group_chat as gc


class _FakeEngine:
    """只做流式回放：把预设回复逐字推给 on_delta，行为与真实引擎的对外契约一致。

    on_run：可选回调，在 run 期间触发（用于模拟模型调用工具后再收尾）。
    """

    def __init__(self, reply: str, on_run=None):
        self.reply = reply
        self.on_run = on_run
        self.on_delta = None
        self.on_status = None
        self.confirm = None
        self.ask_user = None
        self._messages = []
        self._stop = None
        self.prompts = []

    def run(self, prompt, *_a, **_kw):
        self.prompts.append(prompt)
        if self.on_run is not None:
            self.on_run(self)
            return
        for ch in self.reply:
            self.on_delta(ch)


def _thinking_on(params: dict) -> bool:
    """判断力度参数是否开启了思考（各家参数形态不同：thinking.type / reasoning_effort）。"""
    if not params:
        return False
    if (params.get("thinking") or {}).get("type") == "enabled":
        return True
    return params.get("reasoning_effort") not in (None, "", "none")


@pytest.fixture()
def chats_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(gc, "chats_root", lambda: tmp_path)
    agent_context.reset_all()
    yield tmp_path
    agent_context.reset_all()


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
    msgs, ends = [], []
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


def test_turn_prompt_carries_shared_delta_and_start_command(chats_dir):
    c = _chat()
    r = gc.GroupChatRunner(c, {})
    p = c["participants"][0]
    first = r._turn_prompt(p, first=True, shared="")
    assert "讨论开始" in first
    assert "【讨论主题】" not in first          # 未设主题时不额外加块
    later = r._turn_prompt(p, first=False, shared="- [10:00:00] 乙（note）：先抛一个观点")
    assert "乙（note）：先抛一个观点" in later
    assert "轮到你" in later
    empty = r._turn_prompt(p, first=False, shared="")
    assert "暂无新内容" in empty


def test_user_topic_is_injected_into_every_turn(chats_dir):
    """用户自定义主题：首轮与后续轮都要带上（长对话防跑题）。"""
    c = _chat()
    c["topic"] = "给桌面应用定 v2 优先级"
    r = gc.GroupChatRunner(c, {})
    p = c["participants"][0]
    first = r._turn_prompt(p, first=True)
    assert "【讨论主题】给桌面应用定 v2 优先级" in first
    assert "围绕上述主题" in first
    later = r._turn_prompt(p, first=False, shared="- [10:00:00] 乙（note）：x")
    assert "【讨论主题】给桌面应用定 v2 优先级" in later
    assert "紧扣主题" in later


def test_topic_used_as_shared_space_seed(chats_dir):
    """主题同时作为共同上下文空间的议题种子（成员读空间时能看到）。"""
    c = _chat()
    c["topic"] = "选题 A"
    r = gc.GroupChatRunner(c, {})
    r._open_space()
    assert agent_context.seed_text(r._space) == "选题 A"


def test_group_engine_disables_full_space_snapshot(chats_dir):
    """群聊按成员增量注入共同上下文：引擎侧必须关闭整份快照，否则 token 平方增长。"""
    c = _chat()
    r = gc.GroupChatRunner(c, {})
    eng = r._engine_for(c["participants"][0])
    assert eng.shared_snapshot is False
    assert eng.conversation == c["id"]


def test_topic_survives_save_and_load(chats_dir):
    c = _chat()
    c["topic"] = "选题 A"
    assert gc.save_chat(c)[0]
    assert gc.load_chat(c["id"])["topic"] == "选题 A"
    # 旧配置文件没有 topic 字段 → 归一化后为空的字符串，不炸
    data = {"id": c["id"], "title": "旧群聊", "participants": []}
    assert gc.normalize_chat(data)["topic"] == ""


def test_shared_space_is_incremental_per_member(chats_dir):
    """共同上下文：后发言者看得到前者；每位成员的第二次发言都能收到增量。"""
    c = _chat(max_rounds=4)
    r = gc.GroupChatRunner(c, {})
    engines = {}

    def _eng(p):
        e = engines.get(p["name"]) or _FakeEngine(f"{p['name']}说：观点")
        engines[p["name"]] = e
        return e

    r._engine_for = _eng
    r.run()
    assert len(engines) == 2
    assert all(len(e.prompts) == 2 for e in engines.values())
    # 每位成员的第二次发言都应收到「别人说过的话」（共同上下文条目格式）
    assert all("（note）" in e.prompts[1] for e in engines.values())
    # 共同上下文不设上限：全部条目都在空间里
    assert len(agent_context.entries(r._space)) == 4


def test_stop_prevents_further_turns(chats_dir):
    c = _chat(max_rounds=99)
    msgs = []
    r = gc.GroupChatRunner(c, {}, on_message=msgs.append)

    def _engine(p):
        e = _FakeEngine("一句话")
        orig = e.run

        def run(prompt, *_a, **_kw):
            orig(prompt)
            r.stop()            # 第一轮发言结束后用户点「结束」
        e.run = run
        return e

    r._engine_for = _engine
    r.run()
    assert len(msgs) == 1


def test_pause_holds_next_turn_until_resume(chats_dir):
    """暂停在「当前一轮说完」后生效：暂停期间不再开新一轮，继续后接着聊。"""
    c = _chat(max_rounds=4)
    msgs = []
    holder = {}

    def _on_msg(m):
        msgs.append(m)
        if len(msgs) == 1:          # 第一轮结束即暂停
            holder["r"].pause()

    r = gc.GroupChatRunner(c, {}, on_message=_on_msg)
    holder["r"] = r
    r._engine_for = lambda p: _FakeEngine("ok")
    t = threading.Thread(target=r.run, daemon=True)
    t.start()
    time.sleep(0.35)
    assert len(msgs) == 1, "暂停后不应继续发言"
    assert r.paused is True
    r.resume()
    t.join(timeout=5)
    assert len(msgs) == 4 and not t.is_alive()


def test_user_interjection_lands_in_shared_space(chats_dir):
    """用户插话发生在主线程（可能是别的对话作用域）：必须仍写入本群聊的空间。"""
    c = _chat(max_rounds=1)
    r = gc.GroupChatRunner(c, {})
    assert r.say("   ") is False
    r._open_space()
    cid = str(c["id"])
    agent_context.set_conversation("other_session")   # 模拟主线程正处于另一个对话
    assert r.say("我插一句") is True
    agent_context.set_conversation(cid)               # 读回本群聊作用域
    assert r._messages[-1]["user"] is True
    assert any("我插一句" in e["text"] for e in agent_context.entries(r._space))


def test_export_member_creates_agent_with_avatar(chats_dir, tmp_path, monkeypatch):
    agents_root = tmp_path / "agents"
    agents_root.mkdir()
    monkeypatch.setattr(agent_agents, "agents_root", lambda: agents_root)
    monkeypatch.setattr(agent_avatar, "avatars_root", lambda kind="agent": chats_dir)

    # 造一个成员头像文件，验证导出时会复制到 agent kind
    src = chats_dir / "src.png"
    src.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
    ok, ref, _msg = agent_avatar.store(str(src), "group")
    assert ok

    c = _chat(names=("甲", "乙"))
    c["participants"][0]["avatar"] = ref
    c["participants"][0]["system_prompt"] = "只做审查"
    ok, msg = gc.export_member(c, "甲", "reviewer")
    assert ok, msg
    ag = agent_agents.get_agent("reviewer")
    assert ag["persona"] == "我是甲" and ag["system_prompt"] == "只做审查"
    assert ag.get("avatar"), "头像应随成员一起复制到 agent 目录"


def test_save_agent_keeps_avatar_when_not_passed(chats_dir, tmp_path, monkeypatch):
    agents_root = tmp_path / "agents"
    agents_root.mkdir()
    monkeypatch.setattr(agent_agents, "agents_root", lambda: agents_root)
    assert agent_agents.save_agent("a1", "规则", persona="性格", avatar="x.png")[0]
    # 只改文字、不传 avatar → 头像必须保留
    assert agent_agents.save_agent("a1", "规则2", persona="性格")[0]
    assert agent_agents.get_agent("a1")["avatar"] == "x.png"
    # 显式传空串 → 清除
    assert agent_agents.save_agent("a1", "规则2", persona="性格", avatar="")[0]
    assert not agent_agents.get_agent("a1").get("avatar")


def test_member_can_exit_speech_and_hand_over_turn(chats_dir):
    """成员可调用 exit_speech 退出发言：不产出正文，发言权交给下一位。"""
    c = _chat(names=("甲", "乙"), max_rounds=2)
    msgs = []
    r = gc.GroupChatRunner(c, {}, on_message=msgs.append)
    seen = {"n": 0}

    def _eng(p):
        def _hook(eng):
            seen["n"] += 1
            if seen["n"] == 1:              # 只有第一位退出
                eng.extra_tool_handlers["exit_speech"]({"reason": "无补充"})
                eng._stop.set()
        return _FakeEngine("", on_run=_hook)

    r._engine_for = _eng
    r.run()
    assert len(msgs) == 2
    assert msgs[0].get("skip") is True and "跳过" in msgs[0]["text"]
    assert msgs[1].get("skip") is None
    # 退出的那一轮不写进共同上下文（没有内容可共享）
    assert not any("跳过" in e["text"] for e in agent_context.entries(r._space))


def test_exit_speech_tool_is_exposed_only_in_discussion(chats_dir):
    """工具 schema 名字与描述固定（模型靠它学会「让出发言权」）。"""
    fn = gc.EXIT_SPEECH_SCHEMA["function"]
    assert fn["name"] == "exit_speech"
    assert "退出" in fn["description"] and "reason" in fn["parameters"]["properties"]


def test_group_rules_forbid_plans_in_body_and_mention_exit_tool(chats_dir):
    """正文禁止输出执行计划；退出发言工具在规则里说明。"""
    c = _chat()
    r = gc.GroupChatRunner(c, {})
    persona = r._system_persona(c["participants"][0])
    assert "正文禁止输出执行计划" in persona
    assert "exit_speech" in persona


def test_effort_follows_global_thinking_setting(chats_dir):
    """思考强度跟随全局设置：高力度开思考，低力度在 auto 下关思考。"""
    c = _chat()
    base = {"providers": [{"name": "P", "base_url": "http://x", "api_key": "k",
                           "models": ["m"]}]}
    p = c["participants"][0]

    cfg = dict(base, effort="high")
    r = gc.GroupChatRunner(c, cfg)
    eng = r._engine_for(p)
    r._apply_effort(eng, p, "讨论")
    assert _thinking_on(eng.llm.effort_params), "高力度应开启思考"

    cfg_low = dict(base, effort="low")
    r2 = gc.GroupChatRunner(c, cfg_low)
    eng2 = r2._engine_for(p)
    r2._apply_effort(eng2, p, "讨论")
    assert not _thinking_on(eng2.llm.effort_params), "低力度在 auto 下应关闭思考"

    cfg_on = dict(base, effort="low", think_mode="on")
    r3 = gc.GroupChatRunner(c, cfg_on)
    eng3 = r3._engine_for(p)
    r3._apply_effort(eng3, p, "讨论")
    assert _thinking_on(eng3.llm.effort_params), "思考模式 on 时即使低力度也应思考"

    cfg_force = dict(base, effort="low", force_think=True)
    r4 = gc.GroupChatRunner(c, cfg_force)
    eng4 = r4._engine_for(p)
    r4._apply_effort(eng4, p, "讨论")
    assert _thinking_on(eng4.llm.effort_params), "强制思考时即使低力度也应思考"


def test_summarize_by_designated_member(chats_dir):
    """指定成员总结：产出 kind=summary 消息；总结不写回共同上下文。"""
    c = _chat(names=("甲", "乙"), max_rounds=1)
    c["topic"] = "选题"
    r = gc.GroupChatRunner(c, {})
    r._engine_for = lambda p: _FakeEngine("结论：选 A。")
    r._open_space()
    r._append_space("甲", "我认为是 A")
    msg = r.summarize("甲")
    assert msg["kind"] == "summary" and msg["speaker"] == "甲"
    assert msg["text"] == "结论：选 A。"
    # 总结不进空间：否则后续成员会把它当成一条发言引用
    assert not any("选 A" in e["text"] for e in agent_context.entries(r._space))
    # 提示词带完整空间与固定三段式要求
    prompt = r._summary_prompt()
    assert "我认为是 A" in prompt and "一句话" in prompt and "不要输出执行计划" in prompt


def test_summarize_unknown_member_returns_empty(chats_dir):
    c = _chat()
    r = gc.GroupChatRunner(c, {})
    assert r.summarize("不存在") == {}
