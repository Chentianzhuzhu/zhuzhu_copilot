# -*- coding: utf-8 -*-
"""历史回合的隐藏过程块「延迟收尾」契约（长对话切换不再一次性阻塞主线程）。

背景（探针实测，见 `scripts/_probe_switch_perf.py`）：内层输出标签首次 setVisible 时，
Qt 会为整篇富文本做一次排版（2000 字符 ≈ 20ms，2 万字符级可达百毫秒）；切换到一个含
上百个过程块的长会话时，这些排版累积成秒级主线程阻塞（白屏）。历史回合（已结束）的
过程块处于收起隐藏态，其排版结果用户当下看不到 —— 故收尾推迟到事件循环空闲分片执行。

断言口径（都与机器速度无关）：
  1. done 回合渲染后：输出框**尚未显式显示**（`isHidden()` 为真），但内容已完整写入；
  2. 推进事件循环后收尾自动补齐（输出框显式显示）；
  3. 展开（`_apply_done(False)`）会先**同步补齐**再显示过程块 —— 展开必须立刻可见完整内容；
  4. 进行中的回合（流式，done=False）不推迟：渲染即可见，不走空闲队列。

「待收尾」的判定已从 ChatTurn 级列表（旧的 `turn._deferred`）改为**控件级标志**
（`_deferred_show` / `_deferred_fold`）：收尾任务推入全局 `_IdleSpreader` 队列，控件自己
持有延迟标志，因此回合对象不再需要维护一份镜像列表（展开时仍逐块同步补齐，幂等）。
"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest                                        # noqa: E402
from PyQt6.QtWidgets import QApplication             # noqa: E402

_app = QApplication.instance() or QApplication([])

from zhuzhu_Copilot.ui import agent_chat_bubbles as cb   # noqa: E402
from zhuzhu_Copilot.ui import main_window as mw          # noqa: E402
from zhuzhu_Copilot.ui.agent_panel import AgentPanel     # noqa: E402

LONG_OUT = "ok " + ("构建产物 payload 已写入 /usr/lib/pkg/payload.bin\n" * 40)


def _pump(ms: int = 30):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        _app.processEvents()
        time.sleep(0.002)


def _pending_defers(turn) -> int:
    """仍挂着延迟收尾标志的块数（展开时会由 `_apply_proc_visible` 同步补齐）"""
    n = 0
    for ref in turn._items:
        w = ref.widget
        if (getattr(w, "_deferred_show", None) is not None
                or getattr(w, "_deferred_fold", False)):
            n += 1
    return n


def _tool_specs(n: int = 3) -> list:
    return [(cb.KIND_TOOL, {"name": f"read_file_{i}", "meta": "", "params": {},
                            "ico": None, "out": LONG_OUT, "tip": ""},
             ("tool", i, len(LONG_OUT))) for i in range(n)]


@pytest.fixture(scope="module")
def panel():
    mp = pytest.MonkeyPatch()
    mp.setattr(mw.CopilotPanel, "_start_scan", lambda self, *a: None)
    mp.setattr(mw.CopilotPanel, "_init_update_check", lambda self: None)
    mp.setattr(mw.CopilotPanel, "_check_admin", lambda self: None)
    mp.setattr(mw.CopilotPanel, "_setup_tray", lambda self: None)
    try:
        mp.setattr("zhuzhu_Copilot.ui.desktop_pet.ensure_pet", lambda w: None)
    except Exception:
        pass
    p = AgentPanel(None)
    p.resize(1000, 900)
    p.show()
    _pump(400)
    yield p
    try:
        p.hide()
        _pump(30)
    except RuntimeError:
        pass
    mp.undo()


def _rows_of(turn) -> list:
    """回合内已建区块的控件（按布局顺序）"""
    return [ref.widget for ref in turn._items]


def test_done_turn_defers_hidden_block_finish(panel):
    """done 回合：内容完整写入，但内层输出框的显示收尾被推迟（不在渲染路径上排整篇富文本）"""
    turn = panel._add_bubble("", "ai")
    try:
        turn.render(_tool_specs(3), done=True)
        rows = _rows_of(turn)
        assert len(rows) == 3, "过程块应已建控件（结构即时可见）"
        assert _pending_defers(turn) == 3, "done 回合的隐藏过程块应进入延迟收尾队列"
        for row in rows:
            # 全文存 `_fold_full`（折叠机制的唯一真相），标签在收尾时才按折叠态铺前缀
            assert LONG_OUT in row._fold_full, "内容必须已完整写入（只是显示收尾被推迟）"
            assert row._out.text() == "", "渲染路径上不应触发标签排版"
            assert row._out_box.isHidden(), "渲染路径上不应触发输出框显示（首 show 排版）"
    finally:
        turn.setParent(None)
        turn.deleteLater()
        _pump()


def test_idle_slices_finish_deferred_blocks(panel):
    """事件循环空闲推进后，延迟的收尾自动补齐（输出框显式显示）"""
    turn = panel._add_bubble("", "ai")
    try:
        turn.render(_tool_specs(2), done=True)
        assert _pending_defers(turn) == 2, "前置条件：应有待收尾的块"
        deadline = time.time() + 5.0
        while _pending_defers(turn) and time.time() < deadline:
            _pump(20)
        for row in _rows_of(turn):
            assert not row._out_box.isHidden(), "空闲切片后输出框应已收尾（显式显示）"
    finally:
        turn.setParent(None)
        turn.deleteLater()
        _pump()


def test_expand_resumes_pending_deferred_blocks_synchronously(panel):
    """用户在铺完前展开：`_apply_done(False)` 先同步补齐，展开即可见完整内容"""
    turn = panel._add_bubble("", "ai")
    try:
        turn.render(_tool_specs(2), done=True)
        assert _pending_defers(turn) == 2, "前置条件：应有待收尾的块"
        turn._apply_done(False)               # 展开过程区（等价于点「查看执行过程」）
        assert _pending_defers(turn) == 0, "展开必须先同步补齐待收尾块"
        for ref in turn._items:
            row = ref.widget
            assert row._out.text() == LONG_OUT
            assert not row._out_box.isHidden(), "展开后输出框应可见（内容完整）"
            assert not ref.widget.isHidden(), "展开后过程块本体应可见"
    finally:
        turn.setParent(None)
        turn.deleteLater()
        _pump()


def test_live_turn_does_not_defer(panel):
    """进行中的回合（流式）不推迟：渲染即可见，输出必须即时呈现给用户"""
    turn = panel._add_bubble("", "ai")
    try:
        turn.render(_tool_specs(2), done=False)
        assert _pending_defers(turn) == 0, "进行中的回合不应进入延迟队列"
        for row in _rows_of(turn):
            assert not row._out_box.isHidden(), "流式回合的输出框应即刻显示"
    finally:
        turn.setParent(None)
        turn.deleteLater()
        _pump()