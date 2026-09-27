"""任务清单（todos）的「会话隔离」回归。

要求：**每个会话独享自己的任务清单，会话之间不共享**——只有当前会话的 Agent
（主 Agent 与它派发的子 Agent）能查看/修改自己那一份，其他会话既看不到也改不到
（隔离思路与共同上下文空间 agent_context 一致：会话 id 即作用域）。

背景（真实缺陷）：任务清单原先落在单一全局文件 todos.json，且被引擎作为对话末尾
的 todo 消息注入上下文 → 任一会话的任务清单都会被**所有**会话读到并注入，多会话
并发时互相覆盖进度，还平白推高每个会话的请求上下文。

覆盖：
1. 清单文件按会话解析（同会话同文件、异会话异文件、无会话作用域回退兼容文件）
2. 工具层读写隔离：A 会话 update_todo → B 会话读不到，回到 A 内容完好
3. 引擎层：工具在 worker 线程执行时仍落在本引擎所属会话；_todo_text 只读本会话
4. 全部完成自动清空只清本会话
5. 并发会话（多线程）各写各的，互不串写
6. 文件名安全（非法字符会话 id 不越目录、不撞文件）
7. 源码守卫：UI 清空/刷新走会话级 API，不再直写全局文件
"""
import inspect
import os
import sys
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

import pytest
from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from winapp_migrator.core import agent_context, agent_engine, agent_tools


@pytest.fixture(autouse=True)
def _tmp_todo_store(tmp_path, monkeypatch):
    """清单存储重定向到临时目录（不碰用户真实清单），并把线程会话作用域复位"""
    monkeypatch.setattr(agent_tools, "TODO_DIR", tmp_path / "todos")
    monkeypatch.setattr(agent_tools, "TODO_FILE", tmp_path / "todos.json")
    agent_tools._TODO_CACHE.clear()
    prev = agent_context.thread_local_conversation()
    agent_context.set_conversation("")
    try:
        yield
    finally:
        agent_context.set_conversation(prev)
        agent_tools._TODO_CACHE.clear()


class _StubLLM:
    """仅用于构造引擎的 LLM 桩（本组用例不发起任何上游请求）"""
    model = "todos-isolation-test"

    def chat_stream(self, messages, stop=None, **_kw):
        return {"text": ""}


def _engine(conversation: str) -> agent_engine.AgentEngine:
    return agent_engine.AgentEngine(_StubLLM(), text_only=True, conversation=conversation)


def _titles(conv: str) -> list:
    return [t["title"] for t in agent_tools.load_todos(conv)]


# ---------- 1. 清单文件按会话解析 ----------
def test_todo_file_is_per_conversation(tmp_path):
    path_a = agent_tools.todo_file("sessA")
    path_b = agent_tools.todo_file("sessB")
    assert path_a != path_b, "不同会话必须是不同文件"
    assert path_a == agent_tools.todo_file("sessA"), "同一会话反复解析结果稳定"
    assert path_a.parent == agent_tools.TODO_DIR
    # 无会话作用域（无 UI / 测试）回退到兼容的全局文件，不串进任何会话
    assert agent_tools.todo_file("") == agent_tools.TODO_FILE
    assert agent_tools.todo_scope("") == "" and agent_tools.todo_scope("sessA") == "sessA"


def test_todo_file_scope_follows_current_conversation():
    """未显式传会话时，作用域 = 当前会话（线程局部 → UI 当前会话）"""
    agent_context.set_conversation("sessA")
    assert agent_tools.todo_file() == agent_tools.todo_file("sessA")
    agent_context.set_conversation("sessB")
    assert agent_tools.todo_file() == agent_tools.todo_file("sessB")


# ---------- 2. 工具层读写隔离 ----------
def test_update_todo_is_not_shared_across_conversations():
    agent_context.set_conversation("sessA")
    res = agent_tools.execute_tool("update_todo", {"todos": [
        {"title": "A-任务1", "status": "in_progress"},
        {"title": "A-任务2", "status": "pending"}]})
    assert "A-任务1" in res["text"]
    assert _titles("sessA") == ["A-任务1", "A-任务2"]

    # B 会话：看不到 A 的清单，list_todo 也是空的
    agent_context.set_conversation("sessB")
    assert agent_tools.load_todos() == []
    assert "没有任务清单" in agent_tools.execute_tool("list_todo", {})["text"]
    agent_tools.execute_tool("update_todo", {"todos": [{"title": "B-任务1"}]})
    assert _titles("sessB") == ["B-任务1"]

    # 回到 A：内容完好，未被 B 覆盖
    agent_context.set_conversation("sessA")
    assert _titles("sessA") == ["A-任务1", "A-任务2"]


def test_all_completed_clears_only_own_conversation():
    agent_tools.save_todos([{"title": "B-保留", "status": "pending"}], "sessB")
    agent_context.set_conversation("sessA")
    agent_tools.execute_tool("update_todo", {"todos": [{"title": "A-收尾", "status": "completed"}]})
    assert agent_tools.load_todos("sessA") == [], "全部完成 → 自动清空本会话"
    assert _titles("sessB") == ["B-保留"], "不得顺手清空别的会话"


def test_clear_todos_only_clears_own_conversation():
    agent_tools.save_todos([{"title": "A-1"}], "sessA")
    agent_tools.save_todos([{"title": "B-1"}], "sessB")
    assert agent_tools.clear_todos("sessA") is True
    assert _titles("sessA") == [] and _titles("sessB") == ["B-1"]


def test_unscoped_legacy_file_is_separate_from_sessions():
    """无会话作用域沿用兼容文件：会话清单与其互不影响（老数据不注入任何会话）"""
    agent_tools.save_todos([{"title": "无会话-1", "status": "pending"}], "")
    assert _titles("") == ["无会话-1"]
    assert _titles("sessA") == []


def test_drop_todos_removes_only_own_file():
    """删除会话：该会话的清单随会话回收，不残留、不影响其他会话"""
    agent_tools.save_todos([{"title": "A-1"}], "sessA")
    agent_tools.save_todos([{"title": "B-1"}], "sessB")
    assert agent_tools.drop_todos("sessA") is True
    assert not agent_tools.todo_file("sessA").exists()
    assert _titles("sessA") == [] and _titles("sessB") == ["B-1"]


# ---------- 3. 引擎层：工具在 worker 线程内仍落在本会话 ----------
def test_engine_tool_execution_lands_in_own_conversation():
    eng_a, eng_b = _engine("sessA"), _engine("sessB")
    out = eng_a._execute("update_todo", {"todos": [{"title": "引擎A-1", "status": "pending"}]})
    assert "引擎A-1" in out["text"]

    deep = eng_a._execute("list_todo", {})["text"]
    assert "引擎A-1" in deep, "会话级作用域必须在工具 worker 线程内落地"
    assert eng_b._execute("list_todo", {})["text"].find("引擎A-1") == -1
    assert "引擎A-1" in eng_a._todo_text() and eng_b._todo_text() == ""
    assert _titles("sessA") == ["引擎A-1"] and _titles("sessB") == []


def test_engine_todo_text_reads_own_conversation_only():
    agent_tools.save_todos([{"title": "A-未完成", "status": "in_progress"},
                            {"title": "A-已完成", "status": "completed"}], "sessA")
    agent_tools.save_todos([{"title": "B-未完成", "status": "pending"}], "sessB")
    text_a = _engine("sessA")._todo_text()
    assert "A-未完成" in text_a and "A-已完成" not in text_a
    assert "B-未完成" not in text_a, "不得把别的会话任务清单注入本会话上下文"
    assert "B-未完成" in _engine("sessB")._todo_text()


# ---------- 4. 并发会话互不串写 ----------
def test_concurrent_conversations_do_not_cross_write():
    errors = []

    def worker(conv: str, tag: str):
        try:
            agent_context.set_conversation(conv)
            for i in range(20):
                agent_tools.execute_tool("update_todo", {"todos": [
                    {"title": f"{tag}-{i}", "status": "pending"}]})
                assert [t["title"] for t in agent_tools.load_todos()] == [f"{tag}-{i}"]
        except Exception as e:   # pragma: no cover - 失败时把异常带回主线程断言
            errors.append(e)
        finally:
            agent_context.set_conversation("")

    threads = [threading.Thread(target=worker, args=(f"sess{i}", f"T{i}")) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors, f"并发会话读写异常: {errors!r}"
    for i in range(4):
        assert _titles(f"sess{i}") == [f"T{i}-19"], "并发写入必须各落各的会话文件"


# ---------- 5. 文件名安全 ----------
def test_todo_slug_is_filesystem_safe_and_unique():
    for bad in ("a/b", "a\\b", "会话 1:2*?", "..", ""):
        slug = agent_tools._todo_slug(bad)
        assert slug and not set(slug) & set("/\\:*?\"<>|"), f"非法字符未归一化: {bad!r}"
    assert agent_tools._todo_slug("a/b") != agent_tools._todo_slug("a_b"), "不同会话不得撞同一文件"
    assert agent_tools._todo_slug("hex1234") == "hex1234", "安全 id 保持原样（可读）"


# ---------- 6. 源码守卫：UI / 引擎接线 ----------
def test_engine_and_ui_use_session_scoped_todo_api():
    pkg = os.path.dirname(os.path.dirname(inspect.getfile(agent_engine)))   # winapp_migrator 包目录
    panel = open(os.path.join(pkg, "ui", "agent_panel.py"), encoding="utf-8").read()
    assert "agent_tools.TODO_FILE.write_text" not in panel, \
        "UI 清空清单必须走会话级 API，禁止直写全局文件"
    assert "agent_tools.clear_todos()" in panel
    assert panel.count("agent_tools.load_todos()") >= 3, "切会话/新会话/工具回调都要按当前会话刷新"
    assert "agent_tools.drop_todos(sid)" in panel, "删除会话时该会话清单要随会话回收"

    tools_src = open(os.path.join(pkg, "core", "agent_tools.py"), encoding="utf-8").read()
    assert "TODO_DIR" in tools_src and "_todo_slug" in tools_src

    engine_src = open(os.path.join(pkg, "core", "agent_engine.py"), encoding="utf-8").read()
    assert "agent_tools.load_todos(self.conversation)" in engine_src, \
        "引擎注入的任务清单必须是本引擎所属会话的"
    assert "agent_context.set_conversation(self.conversation)" in engine_src, \
        "工具执行 worker 线程需重新落地本会话作用域"
