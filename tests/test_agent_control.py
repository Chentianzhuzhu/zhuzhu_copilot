"""工作团控制层（agent_control）：暂停/恢复/警告/停止的单元回归。"""
import os
import sys
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from winapp_migrator.core import agent_control


def _clean():
    for aid in list(agent_control.agent_controls()):
        agent_control.unregister_control(aid)


def test_pause_blocks_and_resume_releases():
    _clean()
    c = agent_control.AgentControl("sub:t")
    c.pause()
    assert c.is_paused()
    c.resume()
    assert not c.is_paused()
    # wait_if_paused：未暂停时立即通过
    assert c.wait_if_paused() is True


def test_pause_blocks_until_resume():
    _clean()
    c = agent_control.AgentControl("sub:t2")
    c.pause()
    released = []
    t = threading.Thread(target=lambda: (c.wait_if_paused(), released.append(True)))
    t.start()
    time.sleep(0.3)
    assert not released, "暂停中不应放行"
    c.resume()
    t.join(timeout=2)
    assert released and released[0] is True


def test_stop_beats_pause():
    _clean()
    c = agent_control.AgentControl("sub:t3")
    c.pause()
    c.stop()
    assert c.wait_if_paused() is False, "stop 置位后暂停等待必须立即返回 False"


def test_warn_drain():
    _clean()
    c = agent_control.AgentControl("sub:t4")
    assert c.drain_warns() == []
    c.warn("  请重做  ")
    c.warn("补充验证")
    c.warn("   ")
    assert c.drain_warns() == ["请重做", "补充验证"], "空白警告应被忽略"
    assert c.drain_warns() == []


def test_registry_pause_resume_warn():
    _clean()
    c = agent_control.AgentControl("sub:menxia")
    agent_control.register_control(c)
    ok, _ = agent_control.pause_agent("sub:menxia")
    assert ok and agent_control.agent_control("sub:menxia").is_paused()
    ok, _ = agent_control.resume_agent("sub:menxia")
    assert ok and not c.is_paused()
    ok, _ = agent_control.warn_agent("sub:menxia", "注意质量")
    assert ok and c.drain_warns() == ["注意质量"]
    # 未运行目标：提示不可控
    ok, _ = agent_control.pause_agent("sub:ghost")
    assert not ok
    agent_control.unregister_control("sub:menxia")
    assert agent_control.agent_control("sub:menxia") is None


def test_control_from_stop_reuses_event():
    _clean()
    ev = threading.Event()
    c = agent_control.control_from_stop("main", ev)
    assert c.stop_event is ev
    ev.set()
    assert c.is_stopped()
    agent_control.unregister_control("main")
