"""注册名自带 sub_ 前缀的子 Agent（如 sub_coding_agent）前缀归属回归。

覆盖本次修复：
1. subagent_tool 直接命中注册名（含自带 sub_ 前缀）；去前缀候选仍兼容
2. subagent_schemas 生成工具名不再双前缀（sub_coding_agent 而非 sub_sub_coding_agent）
3. 面板 @路由：@sub_coding_agent 解析为 subagent 直接调用（不再报未知）
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from winapp_migrator.core import agent_subagent, agent_workflow


def _setup(tmp_path, monkeypatch):
    wf_dir = tmp_path / "wf_prefix"
    wf_dir.mkdir(exist_ok=True)
    (wf_dir / "tools.py").write_text(
        "TOOLS = []\n"
        "def execute_tool(name, args, allow_dangerous=False):\n"
        "    return {'text': 'x', 'images': []}\n",
        encoding="utf-8")
    monkeypatch.setattr(agent_workflow, "workflow_dir",
                        lambda name: wf_dir)
    monkeypatch.setattr(agent_subagent, "_subagent_file",
                        lambda workflow="": wf_dir / "subagents.json")
    # 注册一个原名自带 sub_ 前缀的子 Agent（与预设 zhuzhu_copilot/sub_coding_agent 一致）
    ok, _ = agent_subagent.register_subagent(
        "sub_coding_agent", "高效编码", "完成编码任务",
        allowed="read_file,write_file,edit_file", workflow="wf_prefix")
    assert ok
    return wf_dir


def test_subagent_tool_matches_prefixed_registered_name(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    # 直接命中注册名（自带 sub_ 前缀：此前会因去前缀变为 coding_agent 而永不可达）
    conf = agent_subagent.subagent_tool("sub_coding_agent", "wf_prefix")
    assert conf is not None and conf["name"] == "sub_coding_agent"
    # 双前缀（旧 schema 名）同样命中，避免存量提示词失效
    conf3 = agent_subagent.subagent_tool("sub_sub_coding_agent", "wf_prefix")
    assert conf3 is not None and conf3["name"] == "sub_coding_agent"
    # 无前缀注册名的旧用法仍兼容（注册名不带前缀时，sub_<名> 可命中）
    ok, _ = agent_subagent.register_subagent(
        "coder", "编码", "完成编码任务", workflow="wf_prefix")
    assert ok
    conf4 = agent_subagent.subagent_tool("sub_coder", "wf_prefix")
    assert conf4 is not None and conf4["name"] == "coder"
    # 完全不相关名字仍返回 None
    assert agent_subagent.subagent_tool("nobody", "wf_prefix") is None


def test_subagent_schemas_no_double_prefix(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    names = [s["function"]["name"] for s in agent_subagent.subagent_schemas("wf_prefix")]
    assert "sub_coding_agent" in names, "自带前缀注册名的 schema 不应再双前缀"
    assert "sub_sub_coding_agent" not in names, "不得生成 sub_sub_coding_agent"
    # 工具名集合含可调用形式（引擎 _execute 据此直接命中）
    tool_names = agent_subagent.subagent_tool_names("wf_prefix")
    assert "sub_coding_agent" in tool_names


def test_at_route_resolves_prefixed_subagent(tmp_path, monkeypatch):
    """面板 @路由：@sub_coding_agent → (subagent, 直呼) 直接调用，不再报未知。"""
    _setup(tmp_path, monkeypatch)
    from winapp_migrator.ui import agent_panel
    # __new__ 构造（不跑完整 __init__，避免后台 git 加载线程崩溃），装配最小属性
    p = agent_panel.AgentPanel.__new__(agent_panel.AgentPanel)
    p._sess = {"t": p._new_sess_state("t")}
    p._session_id = "t"
    p._add_status = lambda *a, **k: None
    p._sess["t"]["workflow"] = "wf_prefix"   # 会话工作流 → _effective_workflow 可信
    out, route = p._resolve_at_route("@sub_coding_agent 帮我写个函数")
    assert route == ("subagent", "sub_coding_agent"), "自带前缀注册名应解析为子 Agent 直接调用"
    assert out == "帮我写个函数"