# -*- coding: utf-8 -*-
"""子 Agent 子块的「暂停 / 恢复」链接必须真的能控制到对应子 Agent。

背景（用户反馈）：长任务里点子块上的「暂停 / 恢复」没反应。
根因：子块只带了**任务标题**（自然语言描述，如「扫描主项目结构」），而控制句柄的键是
AgentControl 的注册 id（`sub:<子 Agent 名>` / `wf:<工作流>`）。UI 之前拿标题去反查
（`agent_tools._resolve_agent_id`）几乎必然失败 —— 点击只会弹「未识别为可监督的 Agent」。
修法：dispatch 层把控制 id 随子事件一起送到 UI，记进 sub 段（`agent_id`），点击直接命中。

本文件守护三条契约：
  1. 子事件带的控制 id 必须落到 sub 段上（否则点击无从命中）；
  2. 点击「暂停 / 恢复」必须命中注册句柄，并在段上留下可见状态；
  3. 确实没有可命中的句柄时给出明确提示，不得静默。
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PyQt6.QtWidgets import QApplication                          # noqa: E402

_app = QApplication.instance() or QApplication([])

from zhuzhu_Copilot.core import agent_control                     # noqa: E402
from zhuzhu_Copilot.ui import agent_panel as ap                    # noqa: E402

TITLE = "扫描主项目结构并汇总入口文件"


def _p(segs):
    """轻代理面板：只带「子事件落段」与「链接点击」两条路径需要的状态。"""
    p = ap.AgentPanel.__new__(ap.AgentPanel)
    p._segments = segs
    p._sub_segs = {}
    p._bubble_segs = {}
    p._notices = []
    p._renders = []
    p._last_activity = 0.0
    p._stop_send_spin = lambda: None
    p._ensure_ai_bubble = lambda: None
    p._refresh_ai_html = lambda: None
    p._scroll_bottom = lambda: None
    p._notify_blocked = lambda text, title="操作未完成": p._notices.append(text)
    p._render_ai_frame = lambda *a, **k: p._renders.append(1)
    p._sync_after_toggle = lambda *a, **k: None
    return p


def _click(p, url: str, bubble):
    p._on_bubble_link(url, frame=bubble)


def test_sub_event_carries_control_id_into_segment():
    """dispatch 层送来的控制 id 必须落到 sub 段（这是点击能命中的唯一依据）。"""
    segs = []
    p = _p(segs)
    p._on_sub_event("start", 3, TITLE, "", "sub:代码审查")
    seg = p._sub_segs[3]
    assert seg.get("agent_id") == "sub:代码审查", \
        f"子块没记下控制 id（{seg.get('agent_id')!r}）→ 点击只能靠标题反查，必然失败"

    # 兜底重建的段（索引失效）同样要补上控制 id
    segs.clear()
    p._sub_segs.clear()
    p._on_sub_event("delta", 9, TITLE, "输出", "sub:代码审查")
    assert segs and segs[-1].get("agent_id") == "sub:代码审查"


def test_clicking_pause_and_resume_reaches_the_registered_control():
    """点击「暂停 / 恢复」必须命中 AgentControl，并在子块上留下可见状态。"""
    aid = "sub:测试子Agent"
    ctl = agent_control.AgentControl(aid)
    agent_control.register_control(ctl)
    try:
        segs = []
        p = _p(segs)
        p._on_sub_event("start", 0, TITLE, "", aid)
        seg = p._sub_segs[0]
        bubble = object()                      # 只需可哈希（_bubble_segs 以 id 为键）
        p._ai_bubble = bubble
        p._bubble_segs = {id(bubble): segs}
        idx = segs.index(seg)

        _click(p, f"sub:pause:{idx}", bubble)
        assert ctl.is_paused(), "点击「暂停」没有命中注册句柄"
        assert seg.get("paused") is True, "暂停后子块没有可见状态（用户会以为没生效）"
        assert not p._notices, f"不该报错：{p._notices}"

        _click(p, f"sub:resume:{idx}", bubble)
        assert not ctl.is_paused(), "点击「恢复」没有命中注册句柄"
        assert seg.get("paused") is False
        assert p._renders, "状态变化后必须重渲染子块，否则标记看不见"
    finally:
        agent_control.unregister_control(aid)


def test_unreachable_control_reports_instead_of_silent():
    """没有可命中的句柄（子任务已结束 / 老会话没带 id）时必须给出明确提示。"""
    segs = [{"type": "sub", "title": TITLE, "raw": "", "steps": []}]
    p = _p(segs)
    bubble = object()
    p._ai_bubble = bubble
    p._bubble_segs = {id(bubble): segs}

    _click(p, "sub:pause:0", bubble)
    assert p._notices, "无法控制时不得静默（否则用户无从判断是没生效还是没命中）"
    assert "未在运行" in p._notices[0] or "无法" in p._notices[0]
