"""工具增强 + 并发执行 + 打字指示器文案回归。

覆盖本次优化：
1. 工具并发执行（同一轮多个调用互不等待）：
   - 写类工具不同文件真正并行、同一文件按路径加锁串行
   - 只读工具（read_file 等）同样并行（长任务提速）
   - 同一工具同一轮重复调用全部执行（不被去重丢弃）
   - 通过设置 concurrent_edits=false 可关闭并发（回退全串行）
   - 并发写多文件时行数变更统计不丢失（累加有锁保护）
2. 新增/增强工具：
   - search_files 支持 ext/exclude/case_sensitive 过滤
   - grep 支持 context 上下文行 / line_numbers 开关
   - search_code 代码检索按文件分组
   - insert_lines 按行号插入 / read_file number_lines 行号输出
3. 打字指示器文案：工具名 → 进行时状态（无映射回退「正在调用工具 <名>」）

并发断言一律用「并发耗时 / 串行耗时」比值，不用绝对秒数：引擎每轮存在固定开销
（工具集与系统提示构建），与并发能力无关，绝对值断言会被它吞掉而产生偶发误报。
"""
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from zhuzhu_Copilot.core import agent_engine, agent_tools, agent_find, agent_edit
from zhuzhu_Copilot.ui import agent_panel


# ---------- 引擎并发文本编辑 ----------

class _SeqLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self.model = "test-model"
        self.fell_back = False
        self.silent_fallback = False

    def chat_stream(self, messages, **kw):
        self.calls += 1
        r = self.responses[min(self.calls - 1, len(self.responses) - 1)]
        return dict(r)


def _tool_call(cid, name, args):
    return {"id": cid, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}


def _make_engine(llm, monkeypatch, concurrent: bool = True):
    monkeypatch.setattr(agent_engine.agent_tts, "load_config",
                        lambda: {"auto_read": False})
    monkeypatch.setattr(agent_engine.agent_sandbox, "disabled_tools", lambda: frozenset())
    monkeypatch.setattr(agent_engine.agent_sandbox, "tools_disabled_all", lambda: False)
    monkeypatch.setattr(agent_engine.agent_skills, "load_settings",
                        lambda: {"concurrent_edits": concurrent})
    # 跳过「开发前规则确认」首次拦截：该拦截是独立机制，与并发执行无关，
    # 保持本测试聚焦并发本身（否则首轮工具被拦截、需模型重发一轮）
    monkeypatch.setattr(agent_engine, "_DEV_TOOLS", frozenset())
    # 关闭「技能规范化拦截」：该机制会把本轮**首个**被技能覆盖的工具调用拦下
    # （注入规范流程后让模型重发），属于独立机制，会让同一轮调用数少一条，
    # 干扰「N 个工具并发」的计数断言。返回空覆盖表即完全绕过。
    monkeypatch.setattr(agent_engine.agent_skills, "skills_covering_tools",
                        lambda *a, **k: {})
    return agent_engine.AgentEngine(llm, text_only=True)


def _batch_engine(monkeypatch, targets, tool: str, concurrent: bool):
    """构造引擎：同一轮对该批目标各发起一次 tool 调用（多文件/多路径）。"""
    calls = [_tool_call(f"t{i}", tool, {"path": str(p)})
             for i, p in enumerate(targets)]
    if tool == "write_file":
        for i, c in enumerate(calls):
            c["function"]["arguments"] = json.dumps(
                {"path": str(targets[i]), "content": f"# {i}"})
    llm = _SeqLLM([
        {"text": "", "tool_calls": calls, "usage": None, "cache": {"hit": 0, "miss": 0}},
        {"text": "完成", "tool_calls": [], "usage": None, "cache": {"hit": 0, "miss": 0}},
    ])
    return _make_engine(llm, monkeypatch, concurrent=concurrent)


def _bench_batch(monkeypatch, targets, tool: str, concurrent: bool) -> float:
    t0 = time.time()
    _batch_engine(monkeypatch, targets, tool, concurrent).run(f"批处理 {tool}")
    return time.time() - t0


def test_write_calls_run_concurrently_across_files(monkeypatch, tmp_path):
    """同一轮多个不同文件的 write_file 必须真并发：并发耗时须显著低于串行。

    8 个目标 > 并发上限 4 → 并行为 2 批；串行为 8 个串接。
    并发先跑（冷启动吃亏）仍须明显更快，说明并发确实生效。
    """
    targets = [tmp_path / f"f{i}.py" for i in range(8)]
    orig = agent_tools._write_file

    def slow_write(path, content, append=False, allow_dangerous=False):
        time.sleep(0.3)
        return orig(path, content, append, allow_dangerous)

    monkeypatch.setattr(agent_tools, "_write_file", slow_write)
    conc = _bench_batch(monkeypatch, targets, "write_file", concurrent=True)
    for f in targets:
        f.unlink(missing_ok=True)
    serial = _bench_batch(monkeypatch, targets, "write_file", concurrent=False)
    assert conc < serial * 0.7, \
        f"写类工具应并发：并发 {conc:.2f}s / 串行 {serial:.2f}s"
    for f in targets:
        assert f.exists() and f.read_text(encoding="utf-8").startswith("#")


def test_read_calls_run_concurrently(monkeypatch, tmp_path):
    """只读工具（read_file）同一轮也必须并发：长任务大量读取时显著提速。"""
    targets = [tmp_path / f"r{i}.py" for i in range(8)]
    for i, f in enumerate(targets):
        f.write_text(f"# {i}\n", encoding="utf-8")
    orig = agent_tools._read_file

    def slow_read(path, offset=None, limit=None, allow_dangerous=False,
                  number_lines=False):
        time.sleep(0.3)
        return orig(path, offset, limit, allow_dangerous, number_lines)

    monkeypatch.setattr(agent_tools, "_read_file", slow_read)
    conc = _bench_batch(monkeypatch, targets, "read_file", concurrent=True)
    serial = _bench_batch(monkeypatch, targets, "read_file", concurrent=False)
    assert conc < serial * 0.7, \
        f"只读工具应并发：并发 {conc:.2f}s / 串行 {serial:.2f}s"


def test_concurrent_writes_count_edit_delta(monkeypatch, tmp_path):
    """并发写多个文件时行数变更统计必须完整：每个文件都被计入，总数不丢更新。

    引擎持有的 _edit_bucket 即为本轮累计桶（take 后引用仍可读），
    逐文件断言可同时验证「按文件分账」与「并发累加不丢失」。
    """
    targets = [tmp_path / f"d{i}.txt" for i in range(8)]
    orig = agent_tools._write_file

    def slow_write(path, content, append=False, allow_dangerous=False):
        time.sleep(0.2)
        return orig(path, content, append, allow_dangerous)

    monkeypatch.setattr(agent_tools, "_write_file", slow_write)
    eng = _batch_engine(monkeypatch, targets, "write_file", concurrent=True)
    eng.run("并发写并统计")
    bucket = eng._edit_bucket
    assert len(bucket["files"]) == len(targets), bucket
    assert bucket["added"] == len(targets), bucket   # 每文件新增 1 行（"# i"）
    assert all(p.exists() for p in targets)


def test_edit_delta_accumulates_under_concurrency():
    """_record_edit_delta 多线程共享同一桶时累加不丢失（并发写保护的直接断言）。"""
    bucket = agent_tools.reset_edit_delta()
    n = 32

    def work(i):
        agent_tools.bind_edit_bucket(bucket)    # worker 线程绑定同一桶（引擎同款约定）
        agent_tools._record_edit_delta(f"C:/x/f{i}.py", "a\n", "a\nb\nc\n")

    try:
        with ThreadPoolExecutor(max_workers=8) as ex:
            list(ex.map(work, range(n)))
        assert bucket["added"] == 2 * n, bucket
        assert bucket["removed"] == 0
        assert len(bucket["files"]) == n, f"每个文件都应各自计数：{len(bucket['files'])}"
    finally:
        agent_tools.take_edit_delta()    # 复位，避免污染后续用例


def test_edit_calls_same_file_serialized(monkeypatch, tmp_path):
    """同一文件的两次 edit 在同一轮必须按路径加锁串行（总耗时=各任务之和）。"""
    f = tmp_path / "x.py"
    f.write_text("one\ntwo\nthree\n", encoding="utf-8")
    llm = _SeqLLM([
        {"text": "", "tool_calls": [
            _tool_call("t1", "edit_file", {"path": str(f), "old_text": "one", "new_text": "ONE"}),
            _tool_call("t2", "edit_file", {"path": str(f), "old_text": "two", "new_text": "TWO"})],
         "usage": None, "cache": {"hit": 0, "miss": 0}},
        {"text": "完成", "tool_calls": [], "usage": None, "cache": {"hit": 0, "miss": 0}},
    ])
    eng = _make_engine(llm, monkeypatch)

    orig = agent_tools._edit_file

    def slow_edit(path, old_text, new_text, allow_dangerous=False):
        time.sleep(0.25)
        return orig(path, old_text, new_text, allow_dangerous)

    monkeypatch.setattr(agent_tools, "_edit_file", slow_edit)
    t0 = time.time()
    eng.run("同文件两次编辑")
    el = time.time() - t0
    assert el >= 0.25 * 2 - 0.15, f"同文件编辑应串行，实际耗时 {el:.2f}s"
    text = f.read_text(encoding="utf-8")
    assert "ONE" in text and "TWO" in text


def test_repeated_same_tool_call_all_executed(monkeypatch, tmp_path):
    """同一轮重复调用同一工具多次：全部执行，不被去重丢弃。"""
    files = [tmp_path / f"r{i}.txt" for i in range(3)]
    llm = _SeqLLM([
        {"text": "", "tool_calls": [
            _tool_call(f"t{i}", "write_file", {"path": str(f), "content": "x"})
            for i, f in enumerate(files)],
         "usage": None, "cache": {"hit": 0, "miss": 0}},
        {"text": "完成", "tool_calls": [], "usage": None, "cache": {"hit": 0, "miss": 0}},
    ])
    eng = _make_engine(llm, monkeypatch)
    eng.run("重复写三个文件")
    assert all(f.exists() for f in files), "同工具重复调用应全部执行"


def test_concurrent_edits_off_falls_back_serial(monkeypatch, tmp_path):
    """设置 concurrent_edits=false：编辑类工具回退串行执行（功能正常）。"""
    files = [tmp_path / f"s{i}.txt" for i in range(2)]
    llm = _SeqLLM([
        {"text": "", "tool_calls": [
            _tool_call(f"t{i}", "write_file", {"path": str(f), "content": "y"})
            for i, f in enumerate(files)],
         "usage": None, "cache": {"hit": 0, "miss": 0}},
        {"text": "完成", "tool_calls": [], "usage": None, "cache": {"hit": 0, "miss": 0}},
    ])
    monkeypatch.setattr(agent_engine.agent_tts, "load_config",
                        lambda: {"auto_read": False})
    monkeypatch.setattr(agent_engine.agent_sandbox, "disabled_tools", lambda: frozenset())
    monkeypatch.setattr(agent_engine.agent_sandbox, "tools_disabled_all", lambda: False)
    monkeypatch.setattr(agent_engine.agent_skills, "load_settings",
                        lambda: {"concurrent_edits": False})
    monkeypatch.setattr(agent_engine, "_DEV_TOOLS", frozenset())
    eng = agent_engine.AgentEngine(llm, text_only=True)
    eng.run("串行写两个文件")
    assert all(f.exists() for f in files)


# ---------- 工具增强 ----------

def test_search_files_ext_and_exclude(tmp_path):
    d = tmp_path / "proj"
    (d / "sub").mkdir(parents=True)
    (d / "hello.py").write_text("", encoding="utf-8")
    (d / "hello.txt").write_text("", encoding="utf-8")
    (d / "sub" / "world.kt").write_text("", encoding="utf-8")
    r = agent_find.search_files("hello", str(d), ext="py")
    assert "hello.py" in r and "hello.txt" not in r
    r = agent_find.search_files("world", str(d), ext="kt", exclude="sub")
    assert "未找到" in r


def test_grep_context_and_line_numbers(tmp_path):
    d = tmp_path / "g"
    d.mkdir()
    f = d / "m.py"
    f.write_text("alpha\nbeta\nfoo\ngamma\n", encoding="utf-8")
    r = agent_find.grep_contents("foo", str(d), glob="*.py", context=1)
    assert "分节" in r
    assert ":1:" in r or ":2:" in r   # 上下文行也带行号
    r2 = agent_find.grep_contents("foo", str(d), line_numbers=False)
    assert "m.py: foo" in r2 and ":3:" not in r2


def test_search_code_groups_by_file(tmp_path):
    d = tmp_path / "c"
    d.mkdir()
    (d / "app.py").write_text("def run():\n    pass\n", encoding="utf-8")
    (d / "note.md").write_text("run note\n", encoding="utf-8")
    r = agent_find.search_code("run", str(d), name="app")
    assert "==== app.py ====" in r
    assert "note.md" not in r.split("====")[0] or "note" not in r   # 仅代码文件


def test_insert_lines_and_read_numbered(tmp_path):
    f = tmp_path / "f.py"
    f.write_text("a\nb\nc\n", encoding="utf-8")
    r = agent_tools._insert_lines(str(f), 2, "INS")
    assert "第 2 行前" in r["text"]
    assert f.read_text(encoding="utf-8") == "a\nINS\nb\nc\n"
    # 越界 → 末尾追加
    agent_tools._insert_lines(str(f), 99, "TAIL")
    assert f.read_text(encoding="utf-8").endswith("TAIL\n")
    # 行号读取
    rn = agent_tools._read_file(str(f), number_lines=True)
    assert "L1:" in rn["text"] and "L2:" in rn["text"]


# ---------- 打字指示器文案 ----------

def test_op_status_text_mapping():
    assert agent_panel._op_status_text("write_file") == "正在写入文件"
    assert agent_panel._op_status_text("run_command") == "正在执行命令"
    assert agent_panel._op_status_text("edit_file") == "正在编辑文件"
    assert agent_panel._op_status_text("delete_file") == "正在删除文件"
    assert agent_panel._op_status_text("web_search") == "正在联网搜索"
    # 无映射工具 → 回退
    assert agent_panel._op_status_text("some_custom") == "正在调用工具 some_custom"


def test_file_lock_same_path_shared():
    l1 = agent_edit.file_lock("C:/x/y.py")
    l2 = agent_edit.file_lock("C:/x/y.py")
    assert l1 is l2
    l3 = agent_edit.file_lock("C:/x/z.py")
    assert l3 is not l1


# ---------- 并发执行的状态文案（单个走精准文案，多个走批次文案） ----------

def test_single_concurrent_tool_reports_precise_status(monkeypatch, tmp_path):
    """单个工具即使属于并发集合，也必须发「正在执行: <工具>」。

    否则转圈行显示「正在并行执行 1 个工具调用」而非该工具对应的状态
    （如「正在搜索文件」「正在读取时间」），与用户要求的「同步显示对应状态」不符。
    """
    (tmp_path / "a.py").write_text("x", encoding="utf-8")
    statuses = []
    llm = _SeqLLM([
        {"text": "", "tool_calls": [
            _tool_call("t1", "search_files",
                       {"query": "a", "folder": str(tmp_path)})],
         "usage": None, "cache": {"hit": 0, "miss": 0}},
        {"text": "完成", "tool_calls": [], "usage": None, "cache": {"hit": 0, "miss": 0}},
    ])
    eng = _make_engine(llm, monkeypatch)
    eng.on_status = statuses.append
    eng.run("搜索文件")
    assert "正在执行: search_files" in statuses, statuses
    assert not any(s.startswith("正在并行执行") for s in statuses), statuses


def test_multiple_concurrent_tools_report_per_tool_status(monkeypatch, tmp_path):
    """多个并发工具：逐条发「待执行工具: <名>」，**不再**发「正在并行执行 N 个」批次文案。

    批次文案一度用来把多条状态压成一条，但用户反馈它与各工具的行内条目重复、属冗余提示，
    已从引擎移除（见 agent_engine._run_inner 并发分支的注释）。本用例锁定该现状：
      · 同一轮的每个调用仍逐条给出「待执行工具: <工具名>」（受理反馈不缺）；
      · 不再出现「正在并行执行」批次文案（不重复刷屏）。
    并发能力本身由 test_read_calls_run_concurrently / test_write_calls_run_concurrently_
    across_files 用耗时比守护，这里只守状态文案契约。
    """
    files = [tmp_path / f"{c}.py" for c in "abc"]
    for f in files:
        f.write_text("x", encoding="utf-8")
    statuses = []
    llm = _SeqLLM([
        {"text": "", "tool_calls": [
            _tool_call(f"t{i}", "read_file", {"path": str(f)})
            for i, f in enumerate(files)],
         "usage": None, "cache": {"hit": 0, "miss": 0}},
        {"text": "完成", "tool_calls": [], "usage": None, "cache": {"hit": 0, "miss": 0}},
    ])
    eng = _make_engine(llm, monkeypatch)
    eng.on_status = statuses.append
    eng.run("并发读三个文件")

    per_tool = [s for s in statuses if s == "待执行工具: read_file"]
    assert len(per_tool) == len(files), f"同一轮三个调用都应逐条受理并反馈：{statuses}"
    assert not any(s.startswith("正在并行执行") for s in statuses), \
        f"「正在并行执行」批次文案已按用户反馈移除，不得再出现：{statuses}"
    assert llm.calls >= 2, "该轮应跑完并把结果交回模型（进入下一轮收尾）"
