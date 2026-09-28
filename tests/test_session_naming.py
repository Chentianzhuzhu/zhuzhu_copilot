"""会话命名回归（远古 bug：AI 无法为对话起名）。

修复前：只有"取用户首条消息前 20 字"的本地截断，且 AI Agent 没有任何改名工具 →
用户看不到 AI 起的名字。修复后：
1. `agent_llm.clean_title` 清洗标题（去围栏/前缀/引号/标点；limit=0 不截断）
2. `agent_llm.generate_session_title` 走真实 API 生成标题（完整保留不截断，失败静默回退）
3. `set_session_name` 工具 + 引擎 on_session_name 回调 → AI Agent 可给对话起名/改名
4. 面板：本地名即时兜底 + 后台 AI 起名覆盖；不覆盖用户/Agent 已定的名字
"""
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from PyQt6.QtWidgets import QApplication, QComboBox

_app = QApplication.instance() or QApplication([])

from zhuzhu_Copilot.core import agent_engine, agent_llm, agent_tools
from zhuzhu_Copilot.ui import agent_panel as ap


# ---------- 1. 标题清洗 ----------
def test_clean_title_strips_noise_and_limits():
    assert agent_llm.clean_title("  帮我写一个快速排序  ") == "帮我写一个快速排序"
    assert agent_llm.clean_title("```python\n排序算法\n```") == "排序算法"
    assert agent_llm.clean_title("标题：排序算法。") == "排序算法"
    assert agent_llm.clean_title("「排序算法」") == "排序算法"
    assert agent_llm.clean_title("多行\n第二行") == "多行"
    assert agent_llm.clean_title("") == "" and agent_llm.clean_title("   ") == ""
    assert len(agent_llm.clean_title("很长" * 30, 20)) == 20
    assert agent_llm.clean_title("很长" * 30, 0) == "很长" * 30    # limit=0：不截断


def test_clean_title_soft_truncate_on_boundary():
    """软截断：超长时优先在自然边界（标点）收尾，不产生半截句；无边界退化硬截"""
    long = "帮我写一个快速排序，然后用Python测试性能"      # 23 字，逗号在第 9 位
    assert agent_llm.clean_title(long, 16) == "帮我写一个快速排序"
    assert len(agent_llm.clean_title("很长" * 30, 16)) == 16        # 无标点：硬截
    # 边界过靠前（逗号在第 1 位会裁得太短）→ 回退硬截到 limit
    assert len(agent_llm.clean_title("早，" + "很长" * 20, 16)) == 16


# ---------- 2. AI 生成标题（真实调用路径，此处注入替身避网） ----------
_STUB_PICKED: dict = {}
_STUB_SEEN: list = []


class _StubClient:
    """LLMClient 替身：记录构造参数与请求消息，返回需清洗的标题（不联网）"""

    def __init__(self, base_url=None, api_key=None, model=None, **kw):
        _STUB_PICKED.clear()
        _STUB_PICKED.update({"base_url": base_url, "model": model})

    def chat(self, messages, max_tokens=1024, timeout=60.0, **kw):
        _STUB_SEEN.clear()
        _STUB_SEEN.extend(messages)
        return {"text": "标题：“快速排序实现”。", "tool_calls": []}


def test_generate_session_title_uses_configured_model_and_cleans(monkeypatch):
    monkeypatch.setattr(agent_llm, "load_model_config",
                        lambda: {"base_url": "https://x/v1", "api_key": "k", "model": "m"})
    monkeypatch.setattr(agent_llm, "LLMClient", _StubClient)
    title = agent_llm.generate_session_title("帮我写一个快速排序的 Python 实现")
    assert title == "快速排序实现"                      # 清洗掉引号/句号/前缀
    assert _STUB_PICKED["model"] == "m"                 # 用当前配置的模型
    assert "帮我写一个快速排序" in json.dumps(_STUB_SEEN, ensure_ascii=False)


def test_generate_session_title_not_truncated(monkeypatch):
    """AI 起名不做长度截断：超过旧上限（12/16/20）的标题也完整保留（用户明确要求）"""

    class _StubTitleClient:
        def __init__(self, *a, **kw):
            pass

        def chat(self, *a, **kw):
            # 23 字：旧上限（12/16/20）都会截掉尾部，现在应完整保留
            return {"text": "快速排序算法时间复杂度与空间复杂度分析工具对比", "tool_calls": []}

    monkeypatch.setattr(agent_llm, "load_model_config", lambda: {"model": "m"})
    monkeypatch.setattr(agent_llm, "LLMClient", _StubTitleClient)
    title = agent_llm.generate_session_title("随便问点什么")
    assert title == "快速排序算法时间复杂度与空间复杂度分析工具对比"
    assert len(title) > 20


def test_generate_session_title_falls_back_on_failure(monkeypatch):
    class _Boom:
        def __init__(self, *a, **kw):
            raise agent_llm.AgentLLMError("配置错误：API Key 含非 ASCII 字符")

    monkeypatch.setattr(agent_llm, "load_model_config", lambda: {"model": "m"})
    monkeypatch.setattr(agent_llm, "LLMClient", _Boom)
    assert agent_llm.generate_session_title("随便什么") == ""      # 静默失败，调用方回退
    assert agent_llm.generate_session_title("") == ""


# ---------- 3. 工具 + 引擎回调 ----------
def test_set_session_name_tool_returns_marker():
    out = agent_tools.execute_tool("set_session_name", {"title": "「排序算法」"})
    assert out.get("__session_name__") == "排序算法"
    assert "排序算法" in out["text"]
    missing = agent_tools.execute_tool("set_session_name", {})
    assert "title" in missing["text"] and "__session_name__" not in missing
    names = {t["function"]["name"] for t in agent_tools.TOOLS}
    assert "set_session_name" in names


def test_set_session_name_tool_keeps_full_title():
    """手动/Agent 改名不做长度截断（旧版被截到 20 字，常成半句）"""
    long = "快速排序算法时间复杂度与空间复杂度分析工具对比"
    out = agent_tools.execute_tool("set_session_name", {"title": long})
    assert out.get("__session_name__") == long
    assert long in out["text"]


def test_engine_forwards_session_name_to_ui(monkeypatch):
    monkeypatch.setattr(agent_engine.agent_tts, "load_config", lambda: {"auto_read": False})
    # 隔离用户「工具管控」设置（本机可能开着"禁用全部工具"，会让工具被硬拦）
    monkeypatch.setattr(agent_engine.agent_sandbox, "disabled_tools", lambda: frozenset())
    monkeypatch.setattr(agent_engine.agent_sandbox, "tools_disabled_all", lambda: False)

    class _LLM:
        model = "test-model"
        fell_back = False
        silent_fallback = False

        def __init__(self):
            self.tokens = {"prompt": 0, "completion": 0}

        def chat_stream(self, messages, **kw):
            return {"text": "ok", "tool_calls": [], "usage": None,
                    "cache": {"hit": 0, "miss": 0}}

    got = []
    eng = agent_engine.AgentEngine(_LLM(), text_only=True, on_session_name=got.append)
    res = eng._execute("set_session_name", {"title": "排序算法"})
    assert got == ["排序算法"], "引擎未把标题回传 UI"
    assert "__session_name__" not in res and "排序算法" in res["text"]


# ---------- 4. 面板：本地兜底 + AI 覆盖 + 不覆盖人工名 ----------
class _StubSignal:
    """轻代理对象没有 Qt 元对象（未调用 super().__init__），用替身承接后台线程的 emit。"""

    def __init__(self):
        self.emitted = []

    def emit(self, sid, title):
        self.emitted.append((sid, title))


def _light_panel(tmp_path):
    obj = ap.AgentPanel.__new__(ap.AgentPanel)
    obj._sessions_dir = lambda: tmp_path
    obj.session_combo = QComboBox()
    obj.sess_name_signal = _StubSignal()   # 后台线程回主线程的通道（此处只记录不弹窗）
    obj._session_id = "s1"
    obj._session_name = "新对话"
    obj._name_ai_started = None
    obj._sess = {}
    (tmp_path / "sessions.json").write_text(
        json.dumps([{"id": "s1", "name": "新对话"}], ensure_ascii=False), encoding="utf-8")
    return obj


def test_apply_session_name_persists_and_refreshes(tmp_path):
    p = _light_panel(tmp_path)
    assert p._apply_session_name("s1", "排序算法", source="ai") is True
    assert p._session_name == "排序算法"
    lst = json.loads((tmp_path / "sessions.json").read_text(encoding="utf-8"))
    assert lst[0]["name"] == "排序算法" and lst[0]["name_source"] == "ai"
    assert p.session_combo.itemText(0) == "排序算法"
    assert p._apply_session_name("不存在", "X") is False
    assert p._apply_session_name("s1", "   ") is False      # 空标题不改名


def test_apply_session_name_truncates_only_local(tmp_path):
    """AI/人工名完整保留；本地兜底名（首条消息摘要）仍软截断到 20 字防超长"""
    p = _light_panel(tmp_path)
    long = "快速排序算法时间复杂度与空间复杂度分析工具对比"
    assert p._apply_session_name("s1", long, source="ai") is True
    lst = json.loads((tmp_path / "sessions.json").read_text(encoding="utf-8"))
    assert lst[0]["name"] == long                       # AI 名不截断
    assert p._apply_session_name("s1", long, source="manual") is True
    lst = json.loads((tmp_path / "sessions.json").read_text(encoding="utf-8"))
    assert lst[0]["name"] == long                       # 人工/Agent 名不截断
    assert p._apply_session_name("s1", long, source="local") is True
    lst = json.loads((tmp_path / "sessions.json").read_text(encoding="utf-8"))
    assert len(lst[0]["name"]) == 20                    # 本地兜底摘要仍限 20 字


def test_auto_name_local_then_ai_override(tmp_path, monkeypatch):
    p = _light_panel(tmp_path)
    monkeypatch.setattr(agent_llm, "generate_session_title", lambda *a, **k: "AI起的名")
    text = "帮我写一个快速排序的 Python 实现"
    p._auto_name_session(text)
    lst = json.loads((tmp_path / "sessions.json").read_text(encoding="utf-8"))
    assert lst[0]["name"] == agent_llm.clean_title(text, 20)   # 本地名立即可见
    assert lst[0]["name_source"] == "local"
    # 后台 AI 结果回主线程 → 覆盖
    p._on_ai_session_title("s1", "AI起的名")
    lst = json.loads((tmp_path / "sessions.json").read_text(encoding="utf-8"))
    assert lst[0]["name"] == "AI起的名" and lst[0]["name_source"] == "ai"
    # 已有 AI 名 → 后续消息不再改名（不打扰上游）
    p._auto_name_session("换一个话题聊别的东西")
    lst = json.loads((tmp_path / "sessions.json").read_text(encoding="utf-8"))
    assert lst[0]["name"] == "AI起的名"


def test_auto_name_does_not_override_manual_name(tmp_path):
    p = _light_panel(tmp_path)
    (tmp_path / "sessions.json").write_text(
        json.dumps([{"id": "s1", "name": "我的名字", "name_source": "manual"}],
                   ensure_ascii=False), encoding="utf-8")
    p._session_name = "我的名字"
    p._auto_name_session("这是新的第一条消息")
    lst = json.loads((tmp_path / "sessions.json").read_text(encoding="utf-8"))
    assert lst[0]["name"] == "我的名字", "人工/Agent 指定的名字被自动改名覆盖"


def test_auto_name_ignores_blank_text(tmp_path):
    p = _light_panel(tmp_path)
    p._auto_name_session("\n\n   ")
    lst = json.loads((tmp_path / "sessions.json").read_text(encoding="utf-8"))
    assert lst[0]["name"] == "新对话"
