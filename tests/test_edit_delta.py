"""文件变更行数统计测试：diff 计数 / 线程局部累计 / 任务快照语义（气泡「-N +M」数据源）"""
import threading

import pytest

from zhuzhu_Copilot.core import agent_tools


def test_diff_line_delta_replace():
    # b → x,y（一行替换为两行）：replace 段计 1 删 2 增
    added, removed = agent_tools._diff_line_delta("a\nb\nc\n", "a\nx\ny\nc\n")
    assert added == 2 and removed == 1


def test_diff_line_delta_counts():
    # 新增 2 行：old 无匹配
    a, r = agent_tools._diff_line_delta("", "1\n2\n")
    assert (a, r) == (2, 0)
    # 全删
    a, r = agent_tools._diff_line_delta("1\n2\n3\n", "")
    assert (a, r) == (0, 3)
    # 一行替换为一行 → 1 增 1 删
    a, r = agent_tools._diff_line_delta("a\nb\n", "a\nz\n")
    assert (a, r) == (1, 1)
    # 纯新增一行
    a, r = agent_tools._diff_line_delta("a\n", "a\nb\n")
    assert (a, r) == (1, 0)


def test_accumulate_within_reset_scope():
    """未 reset（无任务上下文）不统计；reset 后累计；take 后取净快照并清零"""
    assert agent_tools.take_edit_delta() == {"added": 0, "removed": 0, "files": {}}
    agent_tools._notify_file_changed("C:/a.py", "x\n", "x\ny\n")
    assert agent_tools.take_edit_delta()["added"] == 0   # 无 reset 上下文：不累计

    agent_tools.reset_edit_delta()
    agent_tools._notify_file_changed("C:/a.py", "l1\nl2\n", "l1\nl2\nl3\nl4\n")
    agent_tools._notify_file_changed("C:/b.py", "p\n", "p\nq\n")
    snap = agent_tools.take_edit_delta()
    assert snap["added"] == 3 and snap["removed"] == 0
    assert snap["files"]["C:/a.py"] == [2, 0]
    assert snap["files"]["C:/b.py"] == [1, 0]
    # take 后清零：下一轮从零开始
    assert agent_tools.take_edit_delta()["added"] == 0


def test_thread_isolation():
    """不同线程的累计互不干扰（多会话并发不串数）"""
    agent_tools.reset_edit_delta()
    got = {}

    def worker():
        agent_tools.reset_edit_delta()
        agent_tools._notify_file_changed("C:/t.py", "a\n", "a\nb\nc\n")
        got["other"] = agent_tools.take_edit_delta()

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    agent_tools._notify_file_changed("C:/main.py", "a\n", "a\nb\n")
    main = agent_tools.take_edit_delta()
    assert main["added"] == 1 and main["files"] == {"C:/main.py": [1, 0]}
    assert got["other"]["added"] == 2   # 子线程各自统计，互不影响


def test_real_write_records_delta(tmp_path):
    """真实 write_file 工具执行后，任务上下文内可读到行数统计"""
    agent_tools.reset_edit_delta()
    p = tmp_path / "f.txt"
    res = agent_tools._write_file(str(p), "1\n2\n3\n", allow_dangerous=True)
    assert "已写入" in res["text"]
    snap = agent_tools.take_edit_delta()
    assert snap["added"] == 3 and snap["removed"] == 0
    # 再编辑：old→new 删 1 增 2
    agent_tools.reset_edit_delta()
    res = agent_tools._edit_file(str(p), "1\n2\n3\n", "1\nx\ny\n3\n", allow_dangerous=True)
    assert "已替换" in res["text"]
    snap = agent_tools.take_edit_delta()
    assert snap["added"] == 2 and snap["removed"] == 1


def test_bind_edit_bucket_publishes_to_thread():
    """bind_edit_bucket 把指定桶发布到当前线程；_notify_file_changed 会累加进该桶
    （跨线程发布语义：_call_with_stop 派生 worker 线程无法用 reset 的线程局部，
    必须显式 bind 引擎持有的同一 dict）。"""
    bucket = {"added": 0, "removed": 0, "files": {}}

    def worker():
        # 不 reset，直接 bind 外部桶（引擎 _with_edit_bucket 即此模式）
        agent_tools.bind_edit_bucket(bucket)
        agent_tools._notify_file_changed("/p.py", "a\n", "a\nb\nc\n")

    t = threading.Thread(target=worker)
    t.start()
    t.join()

    assert bucket["added"] == 2
    assert bucket["files"]["/p.py"] == [2, 0]
    # 主线程 d 应保持 None（worker 显式绑的是外部 dict，不污染主线程的 thread-local）
    assert getattr(agent_tools._EDIT_DELTA, "d", None) is None


def test_engine_run_pattern_bucket_shared():
    """模拟引擎 run() 模式：主线程 reset 拿桶 → 派发 worker bind 同一桶 → worker 写文件
    累加 → 主线程读同一桶。d5395e1 徽章数据流：reset→bind→notify 跨线程累加。"""
    agent_tools.take_edit_delta()  # 清空主线程 thread-local
    bucket = agent_tools.reset_edit_delta()
    assert bucket == {"added": 0, "removed": 0, "files": {}}

    def worker():
        agent_tools.bind_edit_bucket(bucket)
        agent_tools._notify_file_changed("/a.py", "x\n", "x\ny\nz\n")
        agent_tools._notify_file_changed("/b.py", "p\nq\n", "p\nq\nr\n")

    t = threading.Thread(target=worker)
    t.start()
    t.join()

    assert bucket["added"] == 3
    assert bucket["files"]["/a.py"] == [2, 0]
    assert bucket["files"]["/b.py"] == [1, 0]


# ------------------------------------------------------------
# _build_diff_lines：代码预览面板「AI 变更 · … +N -M」标题行数的数据源
# 旧实现对空文本用 or [""] 占位，导致新建文件场景出现伪 -1 红色行。
# ------------------------------------------------------------
def _import_diff_helper():
    """agent_panel._build_diff_lines 依赖 PyQt6（模块初始化），无 PyQt6 时跳过"""
    pytest.importorskip("PyQt6")
    from zhuzhu_Copilot.ui import agent_panel as ap
    return ap


def test_build_diff_lines_new_file_no_phantom_delete():
    """新文件：old='' 时必须呈现 0 删除 + N 插入（修伪 -1 红行）"""
    ap = _import_diff_helper()
    lines, first = ap._build_diff_lines("", "1\n2\n3\n4\n")
    add = sum(1 for s, _ in lines if s == "+")
    rem = sum(1 for s, _ in lines if s == "-")
    assert (add, rem) == (4, 0)
    assert first == 0
    # 不应有 - 段（无伪删除行）
    assert all(s == "+" for s, _ in lines)


def test_build_diff_lines_new_file_no_trailing_newline():
    ap = _import_diff_helper()
    lines, first = ap._build_diff_lines("", "1\n2\n3\n4")  # 末行无换行
    add = sum(1 for s, _ in lines if s == "+")
    rem = sum(1 for s, _ in lines if s == "-")
    assert (add, rem) == (4, 0)
    assert first == 0


def test_build_diff_lines_empty_both():
    ap = _import_diff_helper()
    lines, first = ap._build_diff_lines("", "")
    assert first is None
    assert lines == []  # 两侧皆空：无 opcodes、无行


def test_build_diff_lines_pure_delete():
    ap = _import_diff_helper()
    lines, first = ap._build_diff_lines("a\nb\nc\n", "")
    add = sum(1 for s, _ in lines if s == "+")
    rem = sum(1 for s, _ in lines if s == "-")
    assert (add, rem) == (0, 3)
    assert first == 0


def test_build_diff_lines_edit_replace():
    ap = _import_diff_helper()
    lines, first = ap._build_diff_lines("a\nb\nc\n", "a\nX\nc\n")
    add = sum(1 for s, _ in lines if s == "+")
    rem = sum(1 for s, _ in lines if s == "-")
    assert (add, rem) == (1, 1)
    assert first == 1


def test_build_diff_lines_no_change():
    ap = _import_diff_helper()
    lines, first = ap._build_diff_lines("a\nb\n", "a\nb\n")
    assert first is None
    add = sum(1 for s, _ in lines if s == "+")
    rem = sum(1 for s, _ in lines if s == "-")
    assert (add, rem) == (0, 0)