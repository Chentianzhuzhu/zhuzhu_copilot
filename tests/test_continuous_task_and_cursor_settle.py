"""持续任务纪律 + 光标定位兜底回归。

1. Agent 规范：交互型任务在同一对话任务内持续推进，结束前必须先 ask_user 确认；
2. 光标定位：SendInput 被低层钩子吞掉时，_settle_at 必须改用 SetCursorPos 兜底，
   失败文案要按可验证原因归因（锁屏 / ClipCursor / 钩子拦截），不再一律甩锅「真实鼠标」。
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zhuzhu_Copilot.core import agent_skills

SERVER = (ROOT / "src" / "zhuzhu_Copilot" / "plugins" / "computer-control" / "server.py")
_MODULE = None


def _cc():
    global _MODULE
    if _MODULE is None:
        spec = importlib.util.spec_from_file_location("cc_settle_server", SERVER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _MODULE = mod
    return _MODULE


# ---------------------------------------------------------------- 持续任务规范

def test_continuous_task_block_requires_ask_user_before_ending():
    block = agent_skills._continuous_task_block()
    assert "持续任务" in block
    assert "ask_user" in block and "是否结束任务" in block
    assert "继续调用工具" in block
    # 不能只写在函数里，必须真的接进基础提示词骨架
    joined = "\n".join(agent_skills._base_prompt_parts(agent_skills.DEFAULT_AGENTS[0]))
    assert "是否结束任务" in joined


def test_default_agent_has_continuous_task_rule():
    rules = " ".join(agent_skills.DEFAULT_AGENTS[0]["rules"])
    assert "持续任务" in rules and "ask_user" in rules


def test_skill_doc_has_continuous_task_section():
    doc = (ROOT / "src" / "zhuzhu_Copilot" / "plugins" / "computer-control"
           / "SKILL.md").read_text(encoding="utf-8")
    assert "## 持续任务" in doc
    assert "是否结束任务" in doc


def test_english_locale_has_continuous_task_translation():
    import json
    loc = ROOT / "src" / "zhuzhu_Copilot" / "locales" / "prompt_en_US.json"
    data = json.loads(loc.read_text(encoding="utf-8"))
    text = data["prompt"]["prompt.block.continuous_task"]
    assert "ask_user" in text and "End task" in text


# ---------------------------------------------------------------- 光标兜底

def test_settle_falls_back_to_setcursorpos(monkeypatch):
    cc = _cc()
    pos = [0, 0]
    calls = []
    monkeypatch.setattr(cc, "_cursor_pos", lambda: tuple(pos))
    monkeypatch.setattr(cc, "_move_absolute", lambda x, y: None)   # SendInput 被吞
    monkeypatch.setattr(cc, "_log", lambda *a, **k: None)

    def fake_set(x, y):
        calls.append((x, y))
        pos[0], pos[1] = int(x), int(y)
        return True

    monkeypatch.setattr(cc, "_set_cursor_pos", fake_set)
    assert cc._settle_at(640, 360) is True
    assert calls, "SendInput 失效时必须走 SetCursorPos 兜底"


def test_settle_first_try_needs_no_fallback(monkeypatch):
    cc = _cc()
    pos = [0, 0]
    calls = []
    monkeypatch.setattr(cc, "_cursor_pos", lambda: tuple(pos))
    monkeypatch.setattr(cc, "_move_absolute",
                        lambda x, y: pos.__setitem__(slice(None), (int(x), int(y))))
    monkeypatch.setattr(cc, "_set_cursor_pos", lambda x, y: calls.append((x, y)) or True)
    assert cc._settle_at(640, 360) is True
    assert calls == [], "首次 SendInput 已成功时不应再调 SetCursorPos"


def test_settle_fails_when_both_mechanisms_blocked(monkeypatch):
    cc = _cc()
    monkeypatch.setattr(cc, "_cursor_pos", lambda: (10, 20))
    monkeypatch.setattr(cc, "_move_absolute", lambda x, y: None)
    monkeypatch.setattr(cc, "_set_cursor_pos", lambda x, y: False)
    assert cc._settle_at(640, 360) is False


def test_lost_note_names_verified_causes(monkeypatch):
    cc = _cc()
    monkeypatch.setattr(cc, "_last_send_ok", True)
    monkeypatch.setattr(cc, "_interactive_desktop_note", lambda: "")
    monkeypatch.setattr(cc, "_cursor_clip_note", lambda x, y: "")
    monkeypatch.setattr(cc, "_cursor_pos", lambda: (1110, 1056))
    note = cc._inject_lost_note(1197, 234)
    assert "SetCursorPos" in note
    assert "(1110,1056)" in note
    assert "不要重复重试" in note
    assert "低层鼠标钩子" in note


def test_lost_note_reports_lock_screen_instead_of_blaming_user(monkeypatch):
    cc = _cc()
    monkeypatch.setattr(cc, "_last_send_ok", True)
    monkeypatch.setattr(cc, "_interactive_desktop_note", lambda: "当前处于锁屏 / UAC 安全桌面")
    note = cc._inject_lost_note(100, 100)
    assert "锁屏" in note
    assert "真实鼠标" not in note


def test_lost_note_reports_clip_cursor(monkeypatch):
    cc = _cc()
    monkeypatch.setattr(cc, "_last_send_ok", True)
    monkeypatch.setattr(cc, "_interactive_desktop_note", lambda: "")
    monkeypatch.setattr(cc, "_cursor_clip_note",
                        lambda x, y: "当前前台程序把光标限制在 (0,0)-(1000,800)")
    note = cc._inject_lost_note(1197, 234)
    assert "限制在" in note
    assert "不要重复重试" in note
