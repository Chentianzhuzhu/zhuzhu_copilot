# -*- coding: utf-8 -*-
"""「优化提示词」组合态误报回归：输入法拼字未上屏时不得报「请先输入内容」。

用户反馈（截图）：明明在输入法里打了字，点魔法棒却弹
「操作未完成：请先输入内容，再点击优化提示词」。

根因（实测复现）：输入法组合态文字只存在于预编辑区、**不进文档** —— 此时
`toPlainText()` 为空。旧实现把「拼字未上屏」与「真的没输入」混为一谈，误报没输入。

断言（真实 AgentPanel，离屏）：
  1. 拼字未上屏：点击优化 → 明确提示「拼字尚未上屏…」，不得再出现「请先输入内容」，
     且焦点还给输入框（便于先确认候选词）；
  2. 上屏后：点击优化 → 正常进入优化流程（后台 worker 启动），无阻断提示；
  3. 真的没输入（无组合态）：仍提示「请先输入内容，再点击优化提示词」（原行为不回归）。
"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest                                        # noqa: E402
from PyQt6.QtWidgets import QApplication             # noqa: E402
from PyQt6.QtGui import QInputMethodEvent            # noqa: E402

_app = QApplication.instance() or QApplication([])

from zhuzhu_Copilot.ui import main_window as mw      # noqa: E402
from zhuzhu_Copilot.ui.agent_panel import AgentPanel  # noqa: E402

OLD_MSG = "请先输入内容，再点击优化提示词"


def _pump(ms: int):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        _app.processEvents()
        time.sleep(0.005)


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
    mp.setattr(AgentPanel, "_maybe_show_onboarding", lambda self: None)
    p = AgentPanel(None)
    p.resize(1000, 900)
    p.show()
    _pump(400)
    yield p
    try:
        p.hide()
        _pump(30)
    except Exception:
        pass
    mp.undo()


@pytest.fixture()
def recorder(panel):
    """拦截阻断提示与后台优化（不发真实 LLM 请求），返回 calls 列表。"""
    calls = []
    orig_blocked = panel._notify_blocked
    orig_worker = panel._optimize_worker
    panel._notify_blocked = lambda text, title="操作未完成": calls.append(("blocked", title, str(text)))
    panel._optimize_worker = lambda *a, **k: calls.append(("worker", "started", ""))
    yield calls
    panel._notify_blocked = orig_blocked
    panel._optimize_worker = orig_worker
    panel._optimizing = False
    panel.optimize_btn.setEnabled(True)


def _reset_input(panel):
    """清空输入内容与组合态标记，回到干净起点。"""
    panel.input.clear()
    _preedit(panel.input, "")


def _preedit(w, s: str):
    QApplication.sendEvent(w, QInputMethodEvent(s, []))


def _commit(w, s: str):
    e = QInputMethodEvent("", [])
    e.setCommitString(s)
    QApplication.sendEvent(w, e)


def _click_optimize(panel, calls):
    panel._optimizing = False
    panel.optimize_btn.setEnabled(True)
    before = len(calls)
    panel.optimize_btn.click()
    _pump(60)
    return calls[before:]


def test_pending_composition_not_reported_as_empty(panel, recorder):
    """拼字未上屏：不得误报「请先输入内容」，并给可执行提示 + 焦点还给输入框。"""
    calls = recorder
    inp = panel.input
    _reset_input(panel)
    inp.setFocus()
    _preedit(inp, "nihao")
    assert inp.has_pending_composition(), "预编辑串投递后应处于组合态"

    events = _click_optimize(panel, calls)
    msgs = [m for k, _t, m in events if k == "blocked"]
    assert msgs, "组合态点击必须有明确回执（不能静默）"
    assert "请先输入内容" not in msgs[0], \
        f"拼字未上屏被误报成「没输入」：{msgs[0]!r}"
    assert "上屏" in msgs[0], f"提示应指明拼字未上屏：{msgs[0]!r}"
    assert inp.hasFocus(), "提示后焦点应还给输入框（便于先确认候选词）"
    # 点击不应吞掉组合态：上屏后仍可正常优化
    _commit(inp, "你好世界")
    assert not inp.has_pending_composition()
    events2 = _click_optimize(panel, calls)
    assert ("worker", "started", "") in events2, "上屏后再次点击应正常进入优化"


def test_committed_text_optimizes_normally(panel, recorder):
    """正常路径：文字已上屏 → 直接进入优化，不出现任何阻断提示。"""
    calls = recorder
    inp = panel.input
    _reset_input(panel)
    _preedit(inp, "nihao")
    _commit(inp, "你好世界")
    assert not inp.has_pending_composition()

    events = _click_optimize(panel, calls)
    kinds = [k for k, _t, _m in events]
    assert "worker" in kinds, "已上屏文字点击优化应启动优化流程"
    assert "blocked" not in kinds, f"正常路径不应有阻断提示：{events}"


def test_truly_empty_still_uses_original_message(panel, recorder):
    """真的没输入（无组合态）：保持原提示文案，防止本次修复把该分支改坏。"""
    calls = recorder
    _reset_input(panel)

    events = _click_optimize(panel, calls)
    msgs = [m for k, _t, m in events if k == "blocked"]
    assert msgs == [OLD_MSG], f"空输入应提示原文案，实得：{events}"
