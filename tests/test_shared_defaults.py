"""共享全开策略 + 存量迁移回归。

覆盖本次变更：
1. register_sub_agent 工具缺省全开（allow_chat/share_context/shared_context），提示文案同步
2. register_subagent 缺省 allow_chat/share_context/shared_context 均为 True；显式关闭保留
3. 存量 subagents.json（旧版 false/null）读取时一次性迁移为开启 + 写 _migrated_v2 标记（幂等）
4. 迁移后用显式 false 不再被翻转（权限门可继续拒绝）
5. 主引擎任务开始无活跃空间时自动 open_space(owner="main")（源码守卫，防回归）
"""
import inspect
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from winapp_migrator.core import agent_context, agent_subagent, agent_tools


def _setup(tmp_path, monkeypatch, wf="wf_x"):
    from winapp_migrator.core import agent_workflow
    wf_dir = tmp_path / wf
    wf_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(agent_workflow, "workflow_dir", lambda name: wf_dir)
    monkeypatch.setattr(agent_subagent, "_subagent_file",
                        lambda workflow="": wf_dir / "subagents.json")
    return wf_dir


# ---------- 1. 工具层缺省全开 ----------
def test_register_tool_defaults_open(tmp_path, monkeypatch):
    wf_dir = _setup(tmp_path, monkeypatch)
    out = agent_tools.execute_tool("register_sub_agent",
                                   {"name": "sub_x", "goal": "G"},
                                   workflow="wf_x")
    assert "已开启聊天权限" in out["text"]
    assert "已开启上下文共享" in out["text"]
    assert "默认参与共同上下文空间" in out["text"]
    conf = agent_subagent.subagent_tool("sub_x", "wf_x")
    assert conf["allow_chat"] is True
    assert conf["share_context"] is True
    assert conf["shared_context"] is True
    # schema 文案同步「缺省全开」
    schemas = {s["function"]["name"]: s["function"]["description"]
               for s in agent_tools.tool_schemas("")}
    assert "缺省 true" in schemas["register_sub_agent"] or "缺省全开" in schemas["register_sub_agent"]


# ---------- 2. 函数层默认与显式关闭 ----------
def test_register_subagent_defaults_open_and_explicit_close(tmp_path, monkeypatch):
    wf_dir = _setup(tmp_path, monkeypatch)
    ok, _ = agent_subagent.register_subagent("open1", "开", "任务", workflow="wf_x")
    assert ok
    conf = agent_subagent.subagent_tool("open1", "wf_x")
    assert conf["allow_chat"] is True and conf["share_context"] is True
    assert conf["shared_context"] is True
    # 显式关闭保留（标记后的条目不再被迁移翻转）
    data = json.loads((wf_dir / "subagents.json").read_text(encoding="utf-8"))
    data.setdefault("_migrated_v2", True)
    (wf_dir / "subagents.json").write_text(json.dumps(data, ensure_ascii=False),
                                           encoding="utf-8")
    ok, _ = agent_subagent.register_subagent("closed1", "关", "任务", workflow="wf_x",
                                             allow_chat=False, share_context=False,
                                             shared_context=False)
    assert ok
    conf2 = agent_subagent.subagent_tool("closed1", "wf_x")
    assert conf2["allow_chat"] is False and conf2["share_context"] is False
    assert conf2["shared_context"] is False


# ---------- 3. 存量迁移 ----------
def test_legacy_subagents_migrated_open_once(tmp_path, monkeypatch):
    wf_dir = _setup(tmp_path, monkeypatch)
    legacy = {"legacy": {"name": "legacy", "goal": "g", "allowed": [],
                         "persona": "", "shared_context": False,
                         "allow_chat": False, "share_context": False}}
    (wf_dir / "subagents.json").write_text(json.dumps(legacy), encoding="utf-8")
    recs = agent_subagent.registered_subagents("wf_x")
    assert recs and recs[0]["shared_context"] is True
    assert recs[0]["allow_chat"] is True and recs[0]["share_context"] is True
    # 已回写：标记 + 值全部为开（幂等，二次读取不重写）
    data = json.loads((wf_dir / "subagents.json").read_text(encoding="utf-8"))
    assert data["_migrated_v2"] is True
    assert data["legacy"]["allow_chat"] is True
    assert data["legacy"]["shared_context"] is True
    recs2 = agent_subagent.registered_subagents("wf_x")
    assert recs2[0]["shared_context"] is True          # 缓存指纹一致，不重复迁移


# ---------- 4. 主引擎任务开始自动开空间（源码守卫） ----------
def test_engine_task_start_auto_opens_space_guard():
    from winapp_migrator.core import agent_engine
    src = inspect.getsource(agent_engine.AgentEngine._run_inner)
    assert "open_space(" in src, "任务开始需按共享全开自动开启共同上下文空间"
    assert 'owner="main"' in src, "自动开启的空间属主必须是 main"
    assert "activate=True" in src, "自动开启后须设为全局活跃空间"
    # 且先读收件箱/快照的逻辑仍在（团队消息 → 上下文注入不回归）
    assert "active_space()" in src and ".render(" in src


def test_open_space_activate_owner_semantics():
    """自动开启路径的空间语义：无活跃空间 → open(seed, owner=main, activate) → 可读可回写。"""
    agent_context.reset_all()
    sid = agent_context.open_space(seed="帮我写个插件", owner="main", activate=True)
    assert sid and agent_context.active_space() == sid
    assert agent_context.stats(sid)["owner"] == "main"
    assert agent_context.append(sid, "sub:sub_coding_agent", "已实现", kind="result")
    body = agent_context.render(sid, viewer="main")
    assert "帮我写个插件" in body and "已实现" in body
    agent_context.reset_all()