# -*- coding: utf-8 -*-
"""历史回合的隐藏过程块「延迟建块 + 延迟收尾」契约（长对话切换不再阻塞主线程）。

背景（探针实测，见 `scripts/_probe_switch_perf.py`）：内层输出标签首次 setVisible 时，
Qt 会为整篇富文本做一次排版（2000 字符 ≈ 20ms，2 万字符级可达百毫秒）；切换到一个含
上百个过程块的长会话时，这些排版累积成秒级主线程阻塞（白屏）。历史回合（已结束）的
过程块处于收起隐藏态，其排版结果用户当下看不到 —— 故整块推迟处理。

**两级延迟**（都由不可见前提支撑）：
  1. 懒建：done 回合的隐藏过程块只登记 ref（widget=None），**连控件都不建**；
  2. 懒收尾：控件一旦建出（展开时补建），内层可见性收尾（首次 setVisible 触发整篇富文本
     排版）仍推迟到空闲切片，只留折叠标志在控件上。

断言口径（都与机器速度无关）：
  1. done 回合渲染后：隐藏过程块未建控件，渲染路径上零重活；
  2. 空闲推进**不会**后台预热它们（闲置必须零开销，见下方测试的实测回归背景）；
  3. 展开（`_apply_done(False)`）会先**同步补建并补齐**再显示 —— 展开必须立刻可见完整内容；
  4. 进行中的回合（流式，done=False）不推迟：渲染即可见，不走空闲队列。

「待收尾」的判定已从 ChatTurn 级列表（旧的 `turn._deferred`）改为**控件级标志**
（`_deferred_show` / `_deferred_fold`）：回合对象不再维护镜像列表，延迟标志留在控件上，
展开时逐块同步补齐（幂等）。
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
    """尚未「收尾完成」的过程块数：懒建块（widget=None）+ 已建但挂着延迟标志的块。

    历史回合（done）的隐藏过程块走**两级延迟**：先只登记 ref 不建控件（`_insert_blocks`
    的懒建分支），由空闲切片 `_ensure_block` 补建；补建时内容一次性写入、但内层可见性
    收尾（首次 setVisible 会触发整篇富文本排版）再推迟一档（`_deferred_show`）。
    两级都对用户不可见，因此断言口径统一为「尚未收尾」。
    """
    n = 0
    for ref in turn._items:
        w = ref.widget
        if w is None:
            n += 1                      # 控件尚未构造（收尾必然也没做）
            continue
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
    """回合内过程块控件（按布局顺序）；懒建未完成的块为 None"""
    return [ref.widget for ref in turn._items]


def test_done_turn_defers_hidden_block_finish(panel):
    """done 回合：隐藏过程块不参与渲染路径的重活（不建控件 / 不排富文本、不显示输出框）"""
    turn = panel._add_bubble("", "ai")
    try:
        turn.render(_tool_specs(3), done=True)
        assert len(turn._items) == 3, "过程块应已登记（结构即时可见）"
        assert _pending_defers(turn) == 3, "done 回合的隐藏过程块应处于延迟状态"
        for ref in turn._items:
            row = ref.widget
            if row is None:
                continue      # 懒建：控件尚未构造，渲染路径自然没做任何重活
            # 全文存 `_fold_full`（折叠机制的唯一真相），标签在收尾时才按折叠态铺前缀
            assert LONG_OUT in row._fold_full, "内容必须已完整写入（只是显示收尾被推迟）"
            assert row._out.text() == "", "渲染路径上不应触发标签排版"
            assert row._out_box.isHidden(), "渲染路径上不应触发输出框显示（首 show 排版）"
    finally:
        turn.setParent(None)
        turn.deleteLater()
        _pump()


def test_hidden_blocks_are_not_prewarmed_in_background(panel):
    """隐藏过程块**不做后台预热**：空闲推进后仍不建控件（闲置零开销）。

    回归背景（实测）：曾用 `_IdleSpreader` 的持久队列在空闲时补建全部隐藏块 —— 一个真实
    长会话会堆出 ~700 个待建任务（≈7s 空闲 CPU），且这些控件全部挂在回合树里，此后每次
    主题切换/壁纸翻转都要对整棵树重设 QSS 并 polish（连续三次切换 1.0s → 3.4s 退化）。
    既然隐藏块只有展开时才可见，建控件必须推迟到"真的要看"的那一刻。
    """
    turn = panel._add_bubble("", "ai")
    try:
        turn.render(_tool_specs(2), done=True)
        assert _pending_defers(turn) == 2, "前置条件：应有待收尾的块"
        _pump(300)                        # 给足事件循环空闲时间
        assert [r.widget for r in turn._items] == [None, None], \
            "隐藏过程块不应被后台预热建控件（闲置必须零开销）"
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
            assert row is not None, "展开必须先同步补建懒建块"
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
        for ref in turn._items:
            assert ref.widget is not None, "流式回合必须同步建控件"
            assert not ref.widget._out_box.isHidden(), "流式回合的输出框应即刻显示"
    finally:
        turn.setParent(None)
        turn.deleteLater()
        _pump()