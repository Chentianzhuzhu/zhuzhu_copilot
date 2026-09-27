"""工作团通信层（agent_bus）：消息总线 + ContextLedger 的单元回归。"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from winapp_migrator.core import agent_bus


def _clean():
    agent_bus.reset_bus()
    agent_bus.reset_ledgers()


def test_send_and_inbox():
    _clean()
    assert agent_bus.send_message("pm", "sub:dev", "请先做接口", "chat")
    assert agent_bus.send_message("pm", "sub:dev", "再补测试", "chat")
    assert agent_bus.message_count("sub:dev") == 2
    msgs = agent_bus.inbox("sub:dev")
    assert [m.text for m in msgs] == ["请先做接口", "再补测试"]
    assert [m.from_id for m in msgs] == ["pm", "pm"]
    assert agent_bus.message_count("sub:dev") == 0, "拉取后应清空"


def test_peek_does_not_consume():
    _clean()
    agent_bus.send_message("a", "b", "hi")
    assert len(agent_bus.peek_inbox("b")) == 1
    assert agent_bus.message_count("b") == 1


def test_bus_snapshot_restore():
    _clean()
    agent_bus.send_message("a", "b", "msg1", "supervise")
    snap = agent_bus.bus_snapshot()
    agent_bus.reset_bus()
    assert agent_bus.message_count("b") == 0
    agent_bus.restore_bus(snap)
    msgs = agent_bus.inbox("b")
    assert len(msgs) == 1 and msgs[0].text == "msg1" and msgs[0].kind == "supervise"


def test_ledger_kinds_and_render():
    _clean()
    led = agent_bus.ledger("sub:dev")
    led.add("command", "run_command", "pip install -q x")
    led.add("file", "read_file", "d:/a.py")
    led.add("tool", "git_info", "ok")
    led.add("message", "chat_with -> pm", "讨论方案")
    text = led.render()
    assert "command" in text and "file" in text and "tool" in text
    only_file = led.render(kinds=("file",))
    assert "read_file" in only_file and "run_command" not in only_file
    assert agent_bus.render_ledger("sub:ghost") == ""


def test_ledger_snapshot_restore_and_cap():
    _clean()
    led = agent_bus.ledger("sub:x")
    for i in range(500):
        led.add("tool", f"tool{i}")
    assert len(led.items()) <= led.MAX_ITEMS, "账本应环形截断"
    snap = agent_bus.ledgers_snapshot()
    agent_bus.reset_ledgers()
    agent_bus.restore_ledgers(snap)
    assert len(agent_bus.ledger("sub:x").items(limit=400)) == led.MAX_ITEMS
