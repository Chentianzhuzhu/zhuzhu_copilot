"""共同上下文空间（Shared Context Space）回归。

覆盖：
1. agent_context 空间读写：open/append/render（含 exclude）、容量裁剪、条目截断、close/stats
2. 绑定与复位：主 Agent 决策开启时自动补齐空间、线程局部设置与还原
3. run_sub_agent：开启共享 → 注入空间快照 + 结论回写；未开启 → 互不干扰
4. 工具白名单：任务级工具白名单不会裁掉 shared_context（协作元工具）
5. 注册式子 Agent：shared_context 默认值写入注册表并可被 schema 暴露给主 Agent 决策
6. 引擎层：dispatch_sub_agents 的 shared_context/space 透传到任务与跨工作流派发
"""
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from zhuzhu_Copilot.core import (agent_context, agent_subagent, agent_tools,
                                  agent_team_run)

# QApplication 必须保留模块级引用：「用完即弃」（不赋值）会让 C++ 应用对象
# 在无引用时立即销毁，随后任何 QWidget 创建都会触发 Qt 致命错误
# （进程级崩溃 0xC0000409）。见 tests/test_builtin_workflow.py 同注。
_APP = None


def _clean():
    agent_context.reset_all()
    agent_context.set_current("")
    agent_context.set_source("")


# ---------- 1. 空间读写 ----------
def test_open_append_render_and_exclude():
    _clean()
    sid = agent_context.open_space("t1", seed="议题：重构支付模块", owner="中书省")
    assert sid == "t1" and agent_context.active_space() == "t1"
    assert agent_context.append(sid, "shangshu", "已拆解为 3 项任务", kind="result")
    assert agent_context.append(sid, "gongbu", "d:/a.py 已修改", kind="result")

    body = agent_context.render(sid)
    assert "议题：重构支付模块" in body
    assert "shangshu" in body and "gongbu" in body

    # exclude：成员读取时排除自己的产出，避免自我重复
    body2 = agent_context.render(sid, exclude="gongbu")
    assert "shangshu" in body2 and "d:/a.py 已修改" not in body2
    # limit：只取最近一条
    body3 = agent_context.render(sid, limit=1)
    assert "d:/a.py 已修改" in body3 and "已拆解为 3 项任务" not in body3


def test_capacity_default_unlimited():
    """默认不限共享上下文空间容量：条目数不裁剪、单条不截断、渲染不全量预算截断。"""
    _clean()
    assert (agent_context.MAX_ENTRIES, agent_context.MAX_ENTRY_CHARS,
            agent_context.MAX_RENDER_CHARS) == (0, 0, 0)
    sid = agent_context.open_space("t2", seed="s")
    n = 300
    for i in range(n):
        agent_context.append(sid, "member", f"条目{i}")
    assert len(agent_context.entries(sid)) == n
    assert agent_context.entries(sid)[0]["text"] == "条目0"      # 不裁最旧

    long_text = "x" * 20000
    agent_context.append(sid, "member", long_text)
    assert agent_context.entries(sid)[-1]["text"] == long_text   # 单条不截断

    body = agent_context.render(sid)
    assert long_text in body and "未完整显示" not in body        # 全量渲染
    assert agent_context.render("不存在") == ""


def test_capacity_trim_and_truncate_when_configured(monkeypatch):
    """容量上限可配置：常量为正整数时，裁剪最旧条目 / 截断单条 / 渲染预算截断即刻生效。"""
    _clean()
    monkeypatch.setattr(agent_context, "MAX_ENTRIES", 10)
    monkeypatch.setattr(agent_context, "MAX_ENTRY_CHARS", 100)
    sid = agent_context.open_space("t2c", seed="s" * 300)
    seed_line = agent_context.render(sid).split("议题（主 Agent 开启）：")[1].splitlines()[0]
    assert len(seed_line) == agent_context.MAX_ENTRY_CHARS       # 议题种子同样受上限约束

    for i in range(15):
        agent_context.append(sid, "member", f"{i:03d}" + "x" * 96)   # 每条恰好 100 字符
    items = agent_context.entries(sid)
    assert len(items) == agent_context.MAX_ENTRIES
    assert items[0]["text"].startswith("005")                    # 最旧的 5 条被裁掉

    agent_context.append(sid, "member", "x" * (agent_context.MAX_ENTRY_CHARS + 50))
    last = agent_context.entries(sid)[-1]["text"]
    assert len(last) == agent_context.MAX_ENTRY_CHARS + 1        # 截断 + 省略号

    # 渲染上限：超长空间渲染仍受 max_chars 约束（单条超长也按预算截断）并给出省略提示
    body = agent_context.render(sid, max_chars=500)
    assert len(body) < 1000 and "未完整显示" in body


def test_empty_text_and_missing_space_rejected():
    _clean()
    assert agent_context.append("nope", "x", "内容") is False
    sid = agent_context.open_space("t3")
    assert agent_context.append(sid, "x", "   ") is False
    ok, _ = agent_context.close_space(sid)
    assert ok and not agent_context.has_space(sid)
    ok2, _ = agent_context.close_space(sid)
    assert ok2 is False
    assert agent_context.stats("")["exists"] is False


# ---------- 2. 绑定与复位 ----------
def test_bind_shared_context_auto_creates_and_restores():
    _clean()
    sid = agent_subagent.bind_shared_context(True, source="gongbu")
    assert sid == agent_context.DEFAULT_SPACE and agent_context.has_space(sid)
    assert agent_context.current() == sid and agent_context.current_source() == "gongbu"

    agent_subagent.restore_shared_context("", "")
    assert agent_context.thread_local_space() == ""
    assert agent_context.current_source() == ""

    # 未开启共享：不新建空间、不设置线程局部
    before = len(agent_context.list_spaces())
    assert agent_subagent.bind_shared_context(False, source="x") == ""
    assert len(agent_context.list_spaces()) == before
    assert agent_context.thread_local_space() == ""


# ---------- 3. run_sub_agent 注入与回写 ----------
class _FakeLLM:
    model = "test-model"

    def __init__(self, reply="子任务完成"):
        self.reply = reply
        self.seen = []

    def chat_stream(self, messages, **kw):
        self.seen.append(messages)
        return {"text": self.reply, "tool_calls": []}


def test_run_sub_agent_injects_snapshot_and_writes_back():
    _clean()
    sid = agent_context.open_space("team", seed="议题：评估导出方案")
    agent_context.append(sid, "menxia", "已封驳初版：缺少异常路径", kind="result")
    llm = _FakeLLM("工部结论：已实现并自测")
    out = agent_subagent.run_sub_agent(llm, "实现导出", shared_context=True,
                                       space=sid, source="gongbu")
    assert out == "工部结论：已实现并自测"
    # read：注入的消息里带空间快照与议题
    joined = json.dumps(llm.seen[0], ensure_ascii=False)
    assert "共同上下文空间" in joined and "议题：评估导出方案" in joined
    assert "已封驳初版" in joined
    # write：结论回写空间
    rendered = agent_context.render(sid)
    assert "gongbu" in rendered and "工部结论：已实现并自测" in rendered
    # 线程局部已复位（未泄漏到调用线程）
    assert agent_context.thread_local_space() == ""


def test_run_sub_agent_without_shared_stays_isolated():
    _clean()
    sid = agent_context.open_space("team2", seed="议题：x")
    llm = _FakeLLM("独立结论")
    agent_subagent.run_sub_agent(llm, "独立任务", shared_context=False,
                                 space=sid, source="ministry_war")
    joined = json.dumps(llm.seen[0], ensure_ascii=False)
    assert "共同上下文空间" not in joined
    assert "独立结论" not in agent_context.render(sid)


def test_dispatch_passes_per_task_shared_flag(monkeypatch):
    _clean()
    sid = agent_context.open_space("team3", seed="议题：并行改造")
    captured = []

    def fake_run(llm, goal, **kw):
        captured.append((goal, kw.get("shared_context"), kw.get("space"), kw.get("source")))
        return f"done:{goal}"

    monkeypatch.setattr(agent_subagent, "run_sub_agent", fake_run)
    tasks = [{"title": "户部", "goal": "A", "sub": "ministry_revenue", "shared_context": True},
             {"title": "兵部", "goal": "B", "sub": "ministry_war", "shared_context": False}]
    agent_subagent.dispatch_sub_agents(None, tasks, space=sid)
    assert captured[0][1] is True and captured[0][3] == "ministry_revenue"
    assert captured[1][1] is False
    assert all(c[2] == sid for c in captured)


# ---------- 4. 工具白名单 ----------
def test_effective_whitelist_keeps_shared_context():
    wl = agent_subagent._effective_whitelist(("read_file",))
    assert "shared_context" in wl and "read_file" in wl
    assert "write_file" not in wl
    assert agent_subagent._effective_whitelist(None) >= {"shared_context", "read_file"}
    # 内置白名单本身包含该工具（子 Agent schema 才能暴露它）
    assert "shared_context" in agent_subagent.SUB_AGENT_WHITELIST


# ---------- 5. 注册表默认值与 schema ----------
def _tmp_workflows_root(tmp_path, monkeypatch):
    root = tmp_path / "workflows"
    (root / "_default").mkdir(parents=True)
    (root / "_default" / "README.md").write_text("default", encoding="utf-8")
    from zhuzhu_Copilot.core import agent_workflow
    monkeypatch.setattr(agent_workflow, "workflows_root", lambda: root)
    return agent_workflow, root


def test_registered_subagent_shared_default_roundtrip(tmp_path, monkeypatch):
    _wf, _root = _tmp_workflows_root(tmp_path, monkeypatch)
    ok, _ = agent_subagent.register_subagent("menxia", "封驳审议", "审议产物",
                                             allowed="read_file,grep",
                                             shared_context=True)
    assert ok
    subs = agent_subagent.registered_subagents("")
    assert subs and subs[0]["shared_context"] is True

    # 未指定 → True（共享全开策略：缺省参与共同上下文空间），且 schema 暴露 shared_context 参数
    ok2, _ = agent_subagent.register_subagent("gongbu", "工程实施", "落地编码")
    assert ok2
    conf = agent_subagent.subagent_tool("gongbu", "")
    assert conf["shared_context"] is True
    schemas = {s["function"]["name"]: s for s in agent_subagent.subagent_schemas("")}
    props = schemas["sub_menxia"]["function"]["parameters"]["properties"]
    assert "shared_context" in props and props["shared_context"]["type"] == "boolean"
    assert "shared_context" in schemas["sub_gongbu"]["function"]["parameters"]["properties"]

    # 字符串形态的开关同样归一化；迁移标记落盘后（此前的 subagent_tool 读取已触发
    # 一次性迁移并写入 _migrated_v2），显式传入的关闭值得到保留
    ok3, _ = agent_subagent.register_subagent("libu", "人事", "配置治理",
                                              shared_context="false")
    assert ok3
    assert agent_subagent.subagent_tool("libu", "")["shared_context"] is False


def test_run_agent_llm_shared_guard_for_unknown_workflow(monkeypatch):
    """跨工作流派发：目标工作流不存在时直接返回失败，不建空间、不泄漏线程局部"""
    _clean()
    out = agent_subagent.run_agent_llm(_FakeLLM(), "不存在的流", "目标", shared_context=True)
    assert "不存在或已禁用" in out
    assert agent_context.thread_local_space() == ""


# ---------- 6. 引擎层透传 ----------
def _make_engine(monkeypatch):
    from PyQt6.QtWidgets import QApplication
    global _APP
    _APP = QApplication.instance() or QApplication([])   # 保留引用（见文件头注释）
    from zhuzhu_Copilot.core import agent_engine

    class _EngineLLM:
        model = "test-model"
        fell_back = False
        silent_fallback = False

        def __init__(self):
            self.tokens = {"prompt": 0, "completion": 0}

        def chat_stream(self, messages, **kw):
            return {"text": "ok", "tool_calls": [], "usage": None,
                    "cache": {"hit": 0, "miss": 0}}

    monkeypatch.setattr(agent_engine.agent_tts, "load_config", lambda: {"auto_read": False})
    return agent_engine.AgentEngine(_EngineLLM(), text_only=True)


def test_engine_propagates_shared_flags(monkeypatch, tmp_path):
    _clean()
    _wf, _root = _tmp_workflows_root(tmp_path, monkeypatch)
    eng = _make_engine(monkeypatch)
    captured = {}

    def fake_dispatch(llm, tasks, **kw):
        captured["tasks"] = list(tasks)
        return ""

    monkeypatch.setattr(agent_subagent, "dispatch_sub_agents", fake_dispatch)
    eng._run_subagent_tool("dispatch_sub_agents", {
        "shared_context": True, "space": "court1",
        "tasks": [{"title": "户部", "goal": "统计", "context": "c"},
                  {"title": "兵部", "goal": "审查", "context": "c", "shared_context": False}],
    })
    t0, t1 = captured["tasks"]
    assert t0["shared_context"] is True and t0["space"] == "court1"
    assert t1["shared_context"] is False and t1["space"] == "court1"

    # 注册式子 Agent：未显式指定时用注册默认值
    ok, _ = agent_subagent.register_subagent("menxia", "封驳审议", "审议产物",
                                             shared_context=True)
    assert ok
    monkeypatch.setattr(agent_subagent, "dispatch_sub_agents", fake_dispatch)
    eng._run_subagent_tool("sub_menxia", {})
    assert captured["tasks"][0]["shared_context"] is True
    # 显式调用参数可覆盖注册默认值（由主 Agent 逐次决策）
    eng._run_subagent_tool("sub_menxia", {"shared_context": False, "goal": "只审第 2 节"})
    assert captured["tasks"][0]["shared_context"] is False
    assert captured["tasks"][0]["goal"] == "只审第 2 节"


def test_explore_and_search_accept_context_and_shared(monkeypatch, tmp_path):
    """内置子 Agent（explore_project / search_large）也要支持主 Agent 传参 context
    与逐次分配 shared_context（此前这两个 schema 没有开关 → 主 Agent 无法分配）。"""
    _clean()
    _wf, _root = _tmp_workflows_root(tmp_path, monkeypatch)
    eng = _make_engine(monkeypatch)
    captured = {}

    def fake_dispatch(llm, tasks, **kw):
        captured["tasks"] = list(tasks)
        return ""

    monkeypatch.setattr(agent_subagent, "dispatch_sub_agents", fake_dispatch)
    eng._run_subagent_tool("explore_project", {
        "directory": "d:/proj", "context": "已读 README：本地打包工具",
        "shared_context": True, "space": "team"})
    t = captured["tasks"][0]
    assert t["context"] == "已读 README：本地打包工具"
    assert t["shared_context"] is True and t["space"] == "team"

    eng._run_subagent_tool("search_large", {
        "query": "TODO", "directories": ["d:/proj"], "context": "只看 src 下的 TODO"})
    t2 = captured["tasks"][0]
    assert t2["context"] == "只看 src 下的 TODO"
    # 共享全开：未显式传 shared_context 时整批默认开启（space 仍按空 → 绑默认空间）
    assert t2["shared_context"] is True and t2["space"] == ""

    # schema 必须暴露这两个开关，否则主 Agent 无从分配
    schemas = {x["function"]["name"]: x["function"]["parameters"]["properties"]
               for x in agent_tools.tool_schemas("")}
    for nm in ("explore_project", "search_large"):
        assert {"context", "shared_context", "space"} <= set(schemas[nm]), nm


def test_sub_agent_schema_lets_main_agent_assign_context_and_share(tmp_path, monkeypatch):
    """注册式子 Agent 的 sub_<name> schema：主 Agent 可传 goal/context 并逐次分配
    shared_context/space；描述里明确「逐次分配」。"""
    _wf, _root = _tmp_workflows_root(tmp_path, monkeypatch)
    ok, msg = agent_subagent.register_subagent("menxia", "封驳审议", "审议产物",
                                              shared_context=True)
    assert ok
    # 注册回执把三种用法都告知调用方，避免把注册式子 Agent 当普通工具用
    assert "@menxia" in msg and "sub_menxia" in msg and "共同上下文空间" in msg

    fn = {s["function"]["name"]: s["function"]
          for s in agent_subagent.subagent_schemas("")}["sub_menxia"]
    props = fn["parameters"]["properties"]
    assert {"goal", "context", "shared_context", "space"} <= set(props)
    assert "逐次分配" in fn["description"] and "context" in fn["description"]


def test_create_agent_result_points_back_to_register_sub_agent(monkeypatch):
    """用户要「创建子 agent」时模型误用 create_agent / op=create_agent：结果文本必须
    回带纠偏提示（人格 Agent 主 Agent 调不到），使下一轮改走 register_sub_agent。"""
    from zhuzhu_Copilot.core import agent_tools

    class _FakeAgents:
        def save_agent(self, name, prompt, bound):
            return True, f"已保存自定义 Agent「{name}」"

    monkeypatch.setattr(agent_tools, "_ag", lambda: _FakeAgents())
    out = agent_tools._register_agent_network(
        {"op": "create_agent", "name": "study", "system_prompt": "你是学习助手"})
    assert "主 Agent 无法调用" in out["text"]
    assert "register_sub_agent" in out["text"]
    out2 = agent_tools._create_agent({"name": "study2", "system_prompt": "你是助手"})
    assert "register_sub_agent" in out2["text"]


def test_prompt_rule_prefers_registered_sub_agent():
    """系统提示的扩展规则：创建子 agent 走 register_sub_agent，并声明主 Agent 的
    共享分配与 context 传参（防止模型用 create_agent 顶替）。"""
    from zhuzhu_Copilot.core import agent_skills
    rule = agent_skills._extend_dont_create_rule()
    assert "register_sub_agent" in rule
    assert "create_agent" in rule and "禁止用它顶替" in rule
    assert "shared_context" in rule and "context" in rule


def test_creation_tools_survive_task_pruning(monkeypatch, tmp_path):
    """「创建一个子agent（并行处理…）」这类措辞会命中 subagent 任务类别 → 工具集被裁剪；
    创建通道（register_sub_agent/list_sub_agents）与已注册的 sub_<name> 必须仍然暴露，
    否则该轮根本无法把子 Agent 创建出来（用户反馈的「创建不了/建错形态」根因之一）。"""
    from zhuzhu_Copilot.core import agent_engine
    _wf, _root = _tmp_workflows_root(tmp_path, monkeypatch)
    ok, _ = agent_subagent.register_subagent("menxia", "封驳审议", "审议产物")
    assert ok
    eng = _make_engine(monkeypatch)
    eng._task_groups = agent_engine._detect_task_groups(
        "帮我创建一个子agent，可以并行处理多个文件")
    assert eng._task_groups, "用例前提：该措辞应命中 subagent 类别（触发工具裁剪）"
    names = {t["function"]["name"] for t in eng._all_tools()}
    assert {"register_sub_agent", "list_sub_agents"} <= names, names
    assert "sub_menxia" in names, "已注册子 Agent 的工具不能被任务裁剪剔除"


def test_engine_cross_workflow_agent_gets_shared(monkeypatch, tmp_path):
    _clean()
    wf, root = _tmp_workflows_root(tmp_path, monkeypatch)
    other = root / "dataflow"
    other.mkdir()
    (other / "workflow.json").write_text(json.dumps({"name": "dataflow", "enabled": True}),
                                        encoding="utf-8")
    assert wf.is_workflow("dataflow")
    eng = _make_engine(monkeypatch)
    seen = {}

    # agent= 任务默认异步投递到成员后台运行器（team_start），共享开关/空间透传
    def fake_team_start(agent_id, wf, goal, **kw):
        seen.update(kw)
        return True, "已派发"

    monkeypatch.setattr(agent_team_run, "team_start", fake_team_start)
    eng._run_subagent_tool("dispatch_sub_agents", {
        "shared_context": True, "space": "court9",
        "tasks": [{"title": "派发", "goal": "分析", "agent": "dataflow", "context": "c"}],
    })
    assert seen.get("shared") is True and seen.get("space") == "court9"