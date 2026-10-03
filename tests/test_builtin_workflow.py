"""「三省六部制度」内置工作流预设回归。

覆盖：
1. 预设发现：list_builtin_workflows 读取 preset.json（不硬编码清单）
2. 一键创建：create_builtin_workflow 复制预设资源 + 登记元数据 + 8 个注册式子 Agent
3. 预设自洽：agent.py 可按核心文件契约加载（AGENT_NAME/SYSTEM_PROMPT/build_system_prompt）
4. 预设工具：court_roster 读真实注册表；court_dispatch 校验部门名并规范化派工单
5. 引擎/工具层：create_workflow(preset=...) 与 list_builtin_workflows 工具可用
6. 幂等与安全：同名工作流拒绝覆盖、未知预设给出可用清单
"""
import importlib.util
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from zhuzhu_Copilot.core import (
    agent_context,
    agent_subagent,
    agent_tools,
    agent_workflow,
)

PRESET_ID = "sansheng_liubu"
EXPECTED_AGENTS = {
    "menxia", "shangshu", "ministry_personnel", "ministry_revenue",
    "ministry_rites", "ministry_war", "ministry_justice", "ministry_works",
}
# QApplication 必须保留模块级引用：`QApplication.instance() or QApplication([])`
# 这种「用完即弃」写法在无引用时会让 C++ 应用对象立即销毁，随后创建 QComboBox
# 等 QWidget 触发 Qt 致命错误（进程级崩溃 0xC0000409；单跑本文件必现）。
_APP = None


def _tmp_root(tmp_path, monkeypatch):
    root = tmp_path / "workflows"
    d = root / "_default"
    d.mkdir(parents=True)
    (d / "README.md").write_text("default", encoding="utf-8")
    monkeypatch.setattr(agent_workflow, "workflows_root", lambda: root)
    return root


def _create(tmp_path, monkeypatch):
    root = _tmp_root(tmp_path, monkeypatch)
    ok, msg = agent_workflow.create_builtin_workflow(PRESET_ID)
    assert ok, msg
    return root, "sansheng_liubu"


# ---------- 1. 预设发现 ----------
def test_list_builtin_workflows_finds_preset(tmp_path, monkeypatch):
    _tmp_root(tmp_path, monkeypatch)
    presets = agent_workflow.list_builtin_workflows()
    ids = {p["id"] for p in presets}
    assert PRESET_ID in ids
    p = next(x for x in presets if x["id"] == PRESET_ID)
    assert p["display_name"] == "三省六部制度"
    assert p["workflow_name"] == "sansheng_liubu"
    assert p["created"] is False
    assert len(p["agents"]) == 8 and "中书省" not in " ".join(p["agents"])  # 中书省为主 Agent
    assert "门下省" in " ".join(p["agents"]) and "工部" in " ".join(p["agents"])


# ---------- 2. 一键创建 ----------
def test_create_builtin_workflow_materializes_all(tmp_path, monkeypatch):
    root, name = _create(tmp_path, monkeypatch)
    d = root / name
    assert d.is_dir()
    for f in ("agent.py", "tools.py", "subagents.json", "mcp.json", "skills"):
        assert d.joinpath(f).exists(), f"预设核心文件缺失: {f}"

    meta = json.loads((d / "workflow.json").read_text(encoding="utf-8"))
    assert meta["preset"] == PRESET_ID and meta["enabled"] is True
    assert meta["description"]

    subs = agent_subagent.registered_subagents(name)
    assert {s["name"] for s in subs} == EXPECTED_AGENTS
    assert all(s["goal"] and s["persona"] and s["allowed"] for s in subs)
    assert agent_subagent.subagent_tool("menxia", name)["shared_context"] is True

    # 工作流技能随预设落地并可被识别
    assert agent_workflow._workflow_skill_names(name) == ["sansheng_liubu"]
    # 工具白名单合法（注册表工具均在子 Agent 白名单内）
    for s in subs:
        assert set(s["allowed"]) <= set(agent_subagent.SUB_AGENT_WHITELIST)

    # 预设已在列表中标记为「已创建」
    p = next(x for x in agent_workflow.list_builtin_workflows() if x["id"] == PRESET_ID)
    assert p["created"] is True


def test_create_twice_and_unknown_preset(tmp_path, monkeypatch):
    _create(tmp_path, monkeypatch)
    ok, msg = agent_workflow.create_builtin_workflow(PRESET_ID)
    assert ok is False and "已存在" in msg
    ok2, msg2 = agent_workflow.create_builtin_workflow("no_such_preset")
    assert ok2 is False and "不存在" in msg2 and PRESET_ID in msg2


def test_create_with_custom_name(tmp_path, monkeypatch):
    _tmp_root(tmp_path, monkeypatch)
    ok, _ = agent_workflow.create_builtin_workflow(PRESET_ID, name="court_v2")
    assert ok and (agent_workflow.workflow_dir("court_v2") / "agent.py").is_file()
    assert len(agent_subagent.registered_subagents("court_v2")) == 8


# ---------- 3. 预设 agent.py 契约 ----------
def test_preset_agent_contract(tmp_path, monkeypatch):
    _create(tmp_path, monkeypatch)
    hooks = agent_workflow.agent_hooks("sansheng_liubu")
    mod = hooks.get("mod")
    assert mod is not None, "预设 agent.py 未能按核心文件契约加载"
    assert mod.AGENT_NAME == "中书省"
    prompt = mod.build_system_prompt("", "")
    assert isinstance(prompt, str) and len(prompt) > 200
    assert "共同上下文空间" in prompt and "create-cordis" in prompt
    assert callable(mod.on_task_start) and callable(mod.on_task_end)
    assert isinstance(mod.COURT_KEYWORDS, tuple) and "编队" in mod.COURT_KEYWORDS

    # on_task_start：编队类任务自动开启共同上下文空间；普通问答不开启
    agent_context.reset_all()

    class _Eng:
        on_status = None

        def __init__(self, text):
            self._messages = [{"role": "user", "content": text}]

    mod.on_task_start(_Eng("请派发六部并行改造这个模块"))
    assert agent_context.active_space(), "编队任务应自动开启共同上下文空间"
    sid = agent_context.active_space()
    assert "派发六部" in agent_context.seed_text(sid)

    agent_context.reset_all()
    mod.on_task_start(_Eng("今天天气如何"))
    assert agent_context.active_space() == ""
    agent_context.reset_all()


def test_preset_every_agent_speaks_classical_chinese(tmp_path, monkeypatch):
    """全编队文言文：主 Agent 人设 + 8 个注册式子 Agent 人格/任务 + 工作流技能 + 预设说明。"""
    _create(tmp_path, monkeypatch)
    mod = agent_workflow.agent_hooks("sansheng_liubu").get("mod")
    prompt = mod.build_system_prompt("", "")
    assert "文言文" in prompt and "语言规范" in prompt
    # 明确要求：所遣子 Agent 亦须以文言文奏报
    assert "子 Agent" in prompt and "文言" in prompt

    for s in agent_subagent.registered_subagents("sansheng_liubu"):
        assert "文言" in s["persona"], f"{s['name']} 人格未要求文言文"
        assert "文言" in s["goal"], f"{s['name']} 任务未要求文言文"

    skill = (agent_workflow.workflow_dir("sansheng_liubu")
             / "skills" / "sansheng_liubu" / "SKILL.md")
    assert "文言文" in skill.read_text(encoding="utf-8")

    preset = agent_workflow._preset_by_id(PRESET_ID)
    assert "文言" in preset["description"]


# ---------- 4. 预设 tools.py ----------
def _load_preset_tools():
    p = (agent_workflow._preset_dir() / PRESET_ID / "tools.py")
    spec = importlib.util.spec_from_file_location("preset_sansheng_tools", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_preset_tools_roster_and_dispatch(tmp_path, monkeypatch):
    _create(tmp_path, monkeypatch)
    # 预设激活为当前工作流，工具解析到其注册表
    ok, msg = agent_workflow.set_active("sansheng_liubu")
    assert ok, msg
    tools = _load_preset_tools()
    assert {t["function"]["name"] for t in tools.TOOLS} == {"court_roster", "court_dispatch"}

    roster = tools.execute_tool("court_roster", {})
    assert all(n in roster["text"] for n in EXPECTED_AGENTS)
    assert "工部" in roster["text"] and "共同上下文空间" in roster["text"]

    # 合法派工单：被规范化为 dispatch_sub_agents 任务（含 sub 与共享开关）
    out = tools.execute_tool("court_dispatch", {"assignments": [
        {"sub": "ministry_revenue", "goal": "统计资源占用", "context": "已读取配置"},
        {"sub": "menxia", "goal": "审议方案", "context": "方案全文"},
    ]})
    payload = json.loads(out["text"].split("：\n", 1)[1])
    assert [t["sub"] for t in payload["tasks"]] == ["ministry_revenue", "menxia"]
    assert all(t["shared_context"] is True for t in payload["tasks"])

    # 非法部门名：拒绝并给出可用清单
    bad = tools.execute_tool("court_dispatch", {"assignments": [{"sub": "nobody", "goal": "x"}]})
    assert "校验未过" in bad["text"] and "menxia" in bad["text"]
    assert "未见 assignments" in tools.execute_tool("court_dispatch", {})["text"]
    assert tools.execute_tool("unknown_tool", {})["text"].startswith("[tools.py 未处理]")


# ---------- 5. 工具层接入 ----------
def test_tool_layer_lists_and_creates_preset(tmp_path, monkeypatch):
    _tmp_root(tmp_path, monkeypatch)
    listed = agent_tools.execute_tool("list_builtin_workflows", {})
    assert "三省六部制度" in listed["text"] and PRESET_ID in listed["text"]

    created = agent_tools.execute_tool("create_workflow", {"preset": PRESET_ID})
    assert "已创建内置工作流" in created["text"]
    assert {t["function"]["name"] for t in agent_tools.TOOLS} >= {
        "list_builtin_workflows", "shared_context", "create_workflow"}
    # 主 Agent 侧 schema 暴露共享开关
    dispatch = next(t for t in agent_tools.TOOLS
                    if t["function"]["name"] == "dispatch_sub_agents")
    props = dispatch["function"]["parameters"]["properties"]
    assert "shared_context" in props and "space" in props
    task_props = props["tasks"]["items"]["properties"]
    assert "shared_context" in task_props


# ---------- 6. UI：内置工作流预设下拉 ----------
def test_ui_preset_combo_lists_builtin(tmp_path, monkeypatch):
    _tmp_root(tmp_path, monkeypatch)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication, QComboBox
    global _APP
    _APP = QApplication.instance() or QApplication([])   # 保留引用（见文件头注释）
    from zhuzhu_Copilot.ui import agent_panel

    dlg = agent_panel._AgentSettingsDialog.__new__(agent_panel._AgentSettingsDialog)
    dlg.wf_preset_combo = QComboBox()
    dlg._reload_builtin_presets()
    n = dlg.wf_preset_combo.count()
    ids = [dlg.wf_preset_combo.itemData(i) for i in range(n)]
    texts = [dlg.wf_preset_combo.itemText(i) for i in range(n)]
    assert PRESET_ID in ids and any("三省六部制度" in t for t in texts)
    assert callable(dlg._on_workflow_create_builtin)

    # 创建后下拉标记「已创建」（数据源 = 真实的工作流目录）
    ok, msg = agent_workflow.create_builtin_workflow(PRESET_ID)
    assert ok, msg
    dlg._reload_builtin_presets()
    assert any("已创建" in dlg.wf_preset_combo.itemText(i)
               for i in range(dlg.wf_preset_combo.count()))


def test_shared_context_tool_open_read_append():
    agent_context.reset_all()
    opened = agent_tools.execute_tool("shared_context",
                                      {"op": "open", "space": "court1", "seed": "议题：A"})
    assert "已开启" in opened["text"]
    assert "已写入" in agent_tools.execute_tool(
        "shared_context", {"op": "append", "text": "结论：完成", "source": "gongbu"})["text"]
    read = agent_tools.execute_tool("shared_context", {"op": "read"})
    assert "议题：A" in read["text"] and "结论：完成" in read["text"]
    assert "court1" in agent_tools.execute_tool("shared_context", {"op": "list"})["text"]
    assert "已关闭" in agent_tools.execute_tool("shared_context", {"op": "close"})["text"]
    # 未开启时 append 明确拒绝
    assert "尚未开启" in agent_tools.execute_tool(
        "shared_context", {"op": "append", "text": "x"})["text"]
    agent_context.reset_all()
