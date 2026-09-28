"""工作流会话继承 + 棋类「传教」bug 回归。

覆盖本次修复：
1. agent_workflow.resolve_workflow：工作流名有效性校验（已删除/禁用/空 → 默认）
2. 新建对话沿用上一对话的工作流（不再硬绑 _default），清空对话同样沿用
3. 引擎层：目标工作流已不存在时不得再生效其派生人设（避免已删工作流身份"复活"）
4. 提示词回归守卫：插件/技能创建引导中不再硬编码围棋等棋类范例
"""
import inspect
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from zhuzhu_Copilot.core import agent_tools, agent_workflow

# 棋类措辞（"棋盘" 不列入：PPT 动画名 checkerboard 的中文名合法使用该词）
BANNED_CHESS_WORDS = ("围棋", "五子棋", "棋牌", "对弈", "落子", "棋面")


def _tmp_root(tmp_path, monkeypatch):
    root = tmp_path / "workflows"
    (root / "_default").mkdir(parents=True)
    (root / "dataflow").mkdir()
    (root / "dataflow" / "workflow.json").write_text(
        json.dumps({"name": "dataflow", "enabled": True}), encoding="utf-8")
    (root / "disabled").mkdir()
    (root / "disabled" / "workflow.json").write_text(
        json.dumps({"name": "disabled", "enabled": False}), encoding="utf-8")
    monkeypatch.setattr(agent_workflow, "workflows_root", lambda: root)
    return root


# ---------- 1. resolve_workflow ----------
def test_resolve_workflow_validates_and_falls_back(tmp_path, monkeypatch):
    _tmp_root(tmp_path, monkeypatch)
    assert agent_workflow.resolve_workflow("dataflow") == "dataflow"      # 存在且启用
    assert agent_workflow.resolve_workflow("_default") == "_default"      # 默认工作流恒有效
    assert agent_workflow.resolve_workflow("") == "_default"              # 空 → 默认
    assert agent_workflow.resolve_workflow(None) == "_default"
    assert agent_workflow.resolve_workflow("ghost") == "_default"         # 不存在（如已删除）
    assert agent_workflow.resolve_workflow("disabled") == "_default"      # 已禁用
    # 会话绑定为已被删除的工作流时必须落到默认（否则派生已删工作流人设，AI 反复提它）
    assert agent_workflow.resolve_workflow("go-game") == "_default"


# ---------- 2. 新建对话沿用工作流 ----------
def test_new_session_inherits_previous_workflow_source_guard():
    from zhuzhu_Copilot.ui.agent_panel import AgentPanel
    src = inspect.getsource(AgentPanel._new_session)
    # 必须在切换 self._session_id 之前取上一对话的工作流
    assert "prev_wf" in src, "新对话应计算并沿用上一对话的工作流"
    assert "resolve_workflow" in src, "继承的工作流须经有效性校验"
    assert src.index("prev_wf =") < src.index("self._session_id = s["), \
        "prev_wf 必须在 self._session_id 被改写之前取得"
    assert 'st["workflow"] = prev_wf' in src
    # 不得再硬绑默认工作流
    assert 'st["workflow"] = agent_workflow.DEFAULT_WORKFLOW' not in src

    clear_src = inspect.getsource(AgentPanel._clear_chat)
    assert "prev_wf" in clear_src and '_st["workflow"] = prev_wf' in clear_src


def test_engine_drops_stale_workflow_binding(tmp_path, monkeypatch):
    """引擎在系统提示阶段发现工作流已不存在时，须清除绑定并回退默认人设。"""
    _tmp_root(tmp_path, monkeypatch)
    from zhuzhu_Copilot.core import agent_engine
    prompt = agent_engine.AgentEngine._system_prompt
    src = inspect.getsource(prompt)
    assert "is_workflow(wf)" in src, "派生工作流人设前必须校验工作流存在"
    assert "self.workflow = None" in src, "陈旧绑定须被清除"

    # 行为验证：真实构造引擎（无 PyQt QApplication 依赖）后调用 _system_prompt
    from PyQt6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])

    class _LLM:
        model = "test-model"
        fell_back = False
        silent_fallback = False

        def __init__(self):
            self.tokens = {"prompt": 0, "completion": 0}

        def chat_stream(self, messages, **kw):
            return {"text": "ok", "tool_calls": [], "usage": None,
                    "cache": {"hit": 0, "miss": 0}}

    monkeypatch.setattr(agent_engine.agent_tts, "load_config", lambda: {"auto_read": False})
    eng = agent_engine.AgentEngine(_LLM(), workflow="ghost-wf", text_only=True)
    text = eng._system_prompt("")
    assert "ghost-wf" not in text, "已不存在的工作流不得再出现在人设中"
    assert eng.workflow is None

    eng2 = agent_engine.AgentEngine(_LLM(), workflow="dataflow", text_only=True)
    assert "dataflow" in eng2._system_prompt(""), "有效工作流派生人设应保留其名"


# ---------- 3. 棋类文案回归守卫 ----------
def test_no_chess_examples_in_tool_prompts():
    blob = json.dumps(agent_tools.TOOLS, ensure_ascii=False)
    for w in BANNED_CHESS_WORDS:
        assert w not in blob, f"工具描述仍含棋类措辞「{w}」：会诱导 AI 在不相关话题里反复提它"
    desc = {t["function"]["name"]: t["function"]["description"] for t in agent_tools.TOOLS}
    assert "web 型" in desc["create_plugin"] and "可视化面板" in desc["create_plugin"]


def test_no_chess_examples_in_builtin_skill_texts():
    from zhuzhu_Copilot.core import agent_skills
    for key in ("plugin-create", "skill-create"):
        text = json.dumps(agent_skills._BUILTIN_MD_SKILLS[key], ensure_ascii=False)
        for w in BANNED_CHESS_WORDS:
            assert w not in text, f"内置技能 {key} 仍含棋类措辞「{w}」"

    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "src", "zhuzhu_Copilot", "skills", "plugin-create", "SKILL.md")
    with open(root, encoding="utf-8") as f:
        md = f.read()
    for w in BANNED_CHESS_WORDS:
        assert w not in md, f"plugin-create/SKILL.md 仍含棋类措辞「{w}」"


def test_plugin_prompt_templates_have_no_chess_examples():
    """插件生成提示词模板（web 型 / 非 web 型）不得含棋类范例。"""
    import re
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "src", "zhuzhu_Copilot", "core", "agent_plugins.py")
    with open(path, encoding="utf-8") as f:
        src = f.read()
    # 仅检查提示词字面量区段（去除注释与代码），避免误伤变量名
    for w in BANNED_CHESS_WORDS:
        for m in re.finditer(w, src):
            line = src[:m.start()].count("\n") + 1
            line_text = src.splitlines()[line - 1]
            assert line_text.lstrip().startswith("#"), \
                f"agent_plugins.py:{line} 提示词仍含棋类措辞「{w}」"
