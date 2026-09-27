"""工作团团队层（agent_team）：团队配置 + 激活制 + 空间权限/继承的回归。

覆盖：
1. team.json 默认团队（领导者 product_manager + 成员工作流）
2. 激活制：activate_team 开空间、team_active、deactivate_team 解散
3. agent_context 权限：readers 限制 viewer、grant_read、快照落盘/恢复
4. 切换工作流继承：活跃空间在切换后仍可被主 Agent 视角读取
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from winapp_migrator.core import agent_context, agent_team


def _clean():
    agent_context.reset_all()
    agent_context.set_current("")
    agent_context.set_source("")


def test_default_team_config():
    cfg = agent_team.team_config()
    assert cfg["leader"] == "product_manager"
    for m in ("zhuzhu_copilot", "frontend_design", "product_dev",
              "backend_dev", "product_debug"):
        assert m in cfg["members"]
    assert agent_team.is_leader_workflow("product_manager")
    assert not agent_team.is_leader_workflow("backend_dev")
    assert agent_team.is_member_workflow("backend_dev")


def test_activate_deactivate(tmp_path, monkeypatch):
    _clean()
    monkeypatch.setattr(agent_team, "_TEAM_FILE", tmp_path / "team.json")
    assert not agent_team.team_active()
    sid = agent_team.activate_team(seed="重构登录模块")
    assert agent_context.has_space(sid)
    assert agent_context.seed_text(sid) == "重构登录模块"
    assert agent_context.stats(sid).get("owner") == "product_manager"
    assert agent_team.team_active()
    # 再次激活不重置（保留既有协同记忆）
    agent_context.append(sid, "frontend_design", "已出设计稿")
    agent_team.activate_team()
    assert agent_context.has_space(sid)
    assert agent_context.entries(sid), "重复激活不应清空既有条目"
    ok, _ = agent_team.deactivate_team()
    assert ok and not agent_team.team_active()


def test_space_readers_permission():
    _clean()
    sid = agent_context.open_space("s1", owner="product_manager",
                                   readers=["wf:frontend_design"])
    # 空间设限后：非 reader 读取被拒
    assert agent_context.render(sid, viewer="sub:other") == ""
    assert agent_context.entries(sid, viewer="sub:other") == []
    # owner 可读
    assert agent_context.render(sid, viewer="product_manager")
    # grant_read 后 reader 可读
    agent_context.append(sid, "pm", "开始设计")
    assert agent_context.grant_read(sid, "sub:frontend")
    assert agent_context.render(sid, viewer="sub:frontend")
    # 未设限空间：任何人可读（向后兼容）
    sid2 = agent_context.open_space("s2", owner="a")
    assert agent_context.render(sid2, viewer="anyone")


def test_space_snapshot_restore():
    _clean()
    sid = agent_context.open_space("s9", seed="议题", owner="pm", readers=["sub:x"])
    agent_context.append(sid, "pm", "结论", kind="result", source_type="tool")
    snap = agent_context.spaces_snapshot()
    agent_context.reset_all()
    assert not agent_context.has_space(sid)
    agent_context.restore_spaces(snap)
    assert agent_context.has_space(sid)
    assert agent_context.active_space() == sid
    it = agent_context.entries(sid)[0]
    assert it.get("source_type") == "tool", "来源类型应随条目落盘/恢复"
    # readers 恢复为集合后权限依旧生效
    assert agent_context.render(sid, viewer="sub:other") == ""
    assert agent_context.render(sid, viewer="sub:x")


def test_switch_inherits_active_space():
    """@工作流 切换继承原工作流活跃空间：新工作流主 Agent（viewer=main）仍可读到。"""
    _clean()
    sid = agent_context.open_space("w1", seed="原工作流背景", owner="frontend_design")
    agent_context.append(sid, "frontend_design", "已产出设计稿", kind="result")
    # 模拟切换：新工作流激活后活跃空间仍指向 w1（UI 侧 set_active 恢复会话 space）
    agent_context.set_active(sid)
    body = agent_context.render(sid, viewer="main")
    assert "原工作流背景" in body and "已产出设计稿" in body
