"""多轮思考过程陈列回归测试。

背景（远古 bug）：AI 气泡内的思考过程只在**首轮**显示，后续工具循环轮次的
思考被整体丢弃。原因是 UI 侧 `_on_reasoning` 以 `_think_done` 作为「整段任务
只思考一次」的门闩：首轮一旦输出正文就把 `_think_done` 置真，之后每一轮的
推理增量都被直接 return 丢弃；且思考段固定插入 `segments[0]`，无法承载多轮。

修复后：每轮「正在思考…」复位本轮思考态；思考增量按时间顺序追加到**当前
一轮**的思考段（末段非思考段即新建一段），与操作/正文交织渲染；渲染侧每个思考段
各成一个独立思考气泡（demo .think-bubble，自带「继续查看」折叠），多轮思考仍各自
独立折叠。
"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from PyQt6.QtWidgets import QApplication

from zhuzhu_Copilot.ui import agent_chat_bubbles

_app = QApplication.instance() or QApplication([])

from zhuzhu_Copilot.ui import agent_panel as ap


def _panel():
    """轻代理面板：只带思考状态机用到的属性，UI 渲染方法以桩替代（不建真实控件）。
    `_think_done` / `_think_start` 是会话感知属性（读写 _sess），故代理需带会话状态。"""
    p = ap.AgentPanel.__new__(ap.AgentPanel)
    segs = []
    p._session_id = "s1"
    p._sess = {"s1": {"segments": segs}}
    p._segments = segs
    p._spinner_lbl = None
    # 活动 AI 气泡引用：新护栏（_on_bubble_link）会比对 bubble is not _ai_bubble，
    # 代理对象需补齐该属性（真实面板在 __init__ 中初始化为 None）
    p._ai_bubble = None
    p._bubble_segs = {}
    p._seg_cache = {}
    p._last_activity = 0.0
    p._ensure_ai_bubble = lambda: None
    p._refresh_ai_html = lambda: None
    p._scroll_bottom = lambda: None
    p._ensure_spinner = lambda: None
    p._stop_send_spin = lambda: None
    p._hide_spinner = lambda: None
    p._add_status = lambda *a, **k: None
    return p


def _think_segs(p):
    return [s for s in p._segments if s.get("type") == "think"]


def test_multi_round_reasoning_all_displayed_in_order():
    """三轮工具循环：每轮思考都独立陈列，按时间顺序与正文/操作交织，不再被丢弃"""
    p = _panel()
    # 第一轮：思考 → 正文
    p._on_status("正在思考…")
    p._on_reasoning("第一轮思考A")
    p._on_reasoning("第一轮思考B")
    p._on_delta("第一轮正文")
    # 第二轮：思考 → 工具调用
    p._on_status("正在思考…")
    p._on_reasoning("第二轮思考")
    p._on_status("待执行工具: read_file")
    # 第三轮：思考 → 正文
    p._on_status("正在思考…")
    p._on_reasoning("第三轮思考")
    p._on_delta("第三轮正文")

    thinks = _think_segs(p)
    assert len(thinks) == 3, f"应陈列 3 轮思考，实际 {len(thinks)}（后续轮次被丢弃）"
    assert "第一轮思考A" in thinks[0]["html"] and "第一轮思考B" in thinks[0]["html"]
    assert "第二轮思考" in thinks[1]["html"]
    assert "第三轮思考" in thinks[2]["html"]
    # 同轮增量合并且不与其他轮混合
    assert "第三轮思考" not in thinks[1]["html"]
    # 时间顺序：思考 → 正文 → 思考 → 操作 → 思考 → 正文
    assert [s["type"] for s in p._segments] == \
        ["think", "text", "think", "op", "think", "text"]
    # 折叠不再由数据层驱动（事件流改造后由思考气泡按行数自动折叠，见
    # tests/test_chat_bubble_demo_parity.py），各轮思考段原样保留供渲染
    assert all(s.get("collapsed") is None for s in thinks)


def test_start_think_resets_round_state():
    """每轮「正在思考…」都复位完成态与计时，使新一轮思考可再次进出"""
    p = _panel()
    p._think_done = True
    p._start_think()
    assert p._think_done is False, "新一轮思考未复位完成态 → 后续思考会被丢弃"
    assert p._think_start > 0


class _FakeLabel:
    """最小 QLabel 替身：只记录文本（转圈行文案断言用，不建真实控件）。"""

    def __init__(self):
        self._t = ""

    def text(self):
        return self._t

    def setText(self, v):
        self._t = v


def test_finish_thinking_shows_elapsed_and_leaves_segments_intact():
    """思考结束只更新转圈行文案（多轮场景下不触碰任何段）：思考内容的折叠改由
    事件流回合的思考气泡按行数自动处理，历史轮次与正文段都不受数据层影响。"""
    p = _panel()
    p._segments = [{"type": "think", "html": "旧轮"}, {"type": "text", "raw": "x"},
                   {"type": "think", "html": "本轮"}]
    p._spinner_lbl = _FakeLabel()
    p._think_start = time.time() - 3
    p._finish_thinking()
    assert p._spinner_lbl.text() == "已思考 3 秒"
    assert [s["type"] for s in p._segments] == ["think", "text", "think"]
    assert all("collapsed" not in s for s in p._segments)


def test_think_inner_html_carries_body_only():
    """思考段的内层 HTML 只含正文：标题行、图标壳、标签胶囊与超行折叠全部由事件流
    回合的思考气泡组件承载（demo .think-bubble），富文本层不再拼装折叠入口 ——
    否则同一气泡里会出现两套语义重复的折叠控件。"""
    p = _panel()
    h = p._render_seg_html({"type": "think", "html": "a"},
                           0, "think", 14, 11, 13, 200)
    assert "think:toggle" not in h
    assert "思考过程" not in h
    assert "收起" not in h
    assert "a" in h


def test_multi_round_thinking_maps_to_independent_bubbles():
    """多轮思考各自成为一个独立区块（思考气泡），标签胶囊按阶段区分：
    首轮属规划（PLANNING）、其后的轮次属执行（EXEC）。各气泡自带折叠开关，
    因此多轮思考仍可独立折叠。"""
    p = _panel()
    p._ai_turn_max_width = lambda: 520
    blocks = p._seg_blocks([
        {"type": "think", "html": "一轮思考"},
        {"type": "op", "html": "▎run_command", "name": "run_command"},
        {"type": "think", "html": "二轮思考"},
    ])
    assert [k for k, _pl, _sig in blocks] == \
        [agent_chat_bubbles.KIND_THINK, agent_chat_bubbles.KIND_TOOL,
         agent_chat_bubbles.KIND_THINK]
    tags = [pl["tag"] for k, pl, _sig in blocks
            if k == agent_chat_bubbles.KIND_THINK]
    assert tags == ["PLANNING", "EXEC"], f"思考阶段标签不正确: {tags}"
    assert "一轮思考" in blocks[0][1]["body"]
    assert "二轮思考" in blocks[2][1]["body"]


def test_bg_event_reasoning_multi_round_and_escaped():
    """后台会话同样按轮分段陈列，且思考文本转义（与前台一致）"""
    p = _panel()
    st = {"segments": [], "last_cmd": "", "last_ask_q": "", "sub_segs": {}}
    p._sess = {"s1": st}
    p._bg_event("s1", "reasoning", "一轮思考 <b>")
    p._bg_event("s1", "delta", "正文")
    p._bg_event("s1", "reasoning", "二轮思考")

    kinds = [s["type"] for s in st["segments"]]
    assert kinds == ["think", "text", "think"], f"后台思考未按轮分段: {kinds}"
    assert "二轮思考" in st["segments"][2]["html"]
    assert "<b>" not in st["segments"][0]["html"], "思考文本未转义"
    assert "&lt;b&gt;" in st["segments"][0]["html"]


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-q"])
