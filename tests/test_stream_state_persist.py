# -*- coding: utf-8 -*-
"""思考/正文流式内容「落盘后从头重播」回归。

用户反馈：「思考过程超过 5 行时气泡会重复播放前几行的输出内容」（长思考进入折叠态后，
可见的就是前几行，所以重播最明显）。

根因（实测，见 git 历史）：会话落盘（任务运行中每 4 秒的自动备份、切会话、发送前提交、
退出兜底）走的是与内存态**同一批 dict 对象**，而 `_strip_seg_render_cache` 是就地剥离
下划线前缀内部键的 —— 于是实时段的流式落字进度 `_shown` 被清掉，下一次 `_reveal_note`
把它重新初始化为 0 → 已经显示出来的前缀从头重播一遍（同时瞬时显示全文，观感是闪一下
再重放）。

守护契约：
  A. `_strip_seg_render_cache` 只返回副本，绝不改动入参；
  B. 落盘后实时段仍保留 `_shown` / `_rd_cache`，而**磁盘内容**必须是剥离后的（否则
     `_rd_cache` 的元组键会让 json 序列化失败）；
  C. 落盘之后再收到新内容，落字进度不得被重置（不得从头重播）。
  D. `rows` 里 AI 行的耗时徽章（`cost`）与系统时间行（`meta`）必须随会话落盘 ——
     尤其是**未归档的最后一轮**（它的 AI 行是落盘时现追加的），否则重启后该轮
     徽章与时间行消失（用户反馈的「重启后时间统计消失」）。
"""
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PyQt6.QtCore import QObject                            # noqa: E402
from PyQt6.QtWidgets import QApplication                    # noqa: E402

_app = QApplication.instance() or QApplication([])

from zhuzhu_Copilot.core import agent_skills                        # noqa: E402
from zhuzhu_Copilot.ui import agent_panel as ap                     # noqa: E402

THINK_TEXT = "推理片段：核对上下文与候选方案，权衡利弊后再决定下一步动作。" * 30


def _live_seg() -> dict:
    return {"type": "think", "html": THINK_TEXT,
            "_shown": 120, "_shown_f": 120.0,
            "_rd_cache": {("块文本", 3): "<p>已渲染</p>"}}


def test_strip_returns_copy_and_keeps_input_intact():
    seg = _live_seg()
    out = ap._strip_seg_render_cache(seg)
    assert out is not seg, "必须返回副本（就地剥离会连累实时段）"
    assert not [k for k in out if k.startswith("_")], "副本里应剥掉全部内部键"
    assert out["html"] == THINK_TEXT
    assert seg["_shown"] == 120 and "_rd_cache" in seg, "入参被改动了"
    assert ap._strip_seg_render_cache(None) is None
    assert ap._strip_seg_render_cache("x") == "x"


def test_persist_keeps_live_progress_and_writes_clean_json(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_skills, "CONFIG_DIR", tmp_path / "agent")
    p = ap.AgentPanel.__new__(ap.AgentPanel)
    seg = _live_seg()
    st = {"segments": [seg], "history_segments": [], "rows": [], "user_msgs": ["你好"],
          "loaded": True}

    # 磁盘内容必须可序列化：_rd_cache 的键是元组，未剥离会直接 TypeError
    p._write_ui_json("s1", st)

    assert seg["_shown"] == 120, "落盘把实时段的落字进度清掉了 → 已显示内容会从头重播"
    assert "_rd_cache" in seg, "落盘把实时段的渲染缓存清掉了"

    on_disk = json.loads((tmp_path / "agent" / "sessions" / "s1.ui.json")
                         .read_text(encoding="utf-8"))
    assert "_shown" not in on_disk["segments"][0], "落盘内容应剥离内部键"
    assert "_rd_cache" not in on_disk["segments"][0]


def test_reveal_does_not_restart_after_persist(tmp_path, monkeypatch):
    """落盘后新内容到达：落字必须接着往下落，而不是回到第 0 个字重播。"""
    monkeypatch.setattr(agent_skills, "CONFIG_DIR", tmp_path / "agent")
    p = ap.AgentPanel.__new__(ap.AgentPanel)
    seg = _live_seg()
    p._segments = [seg]
    st = {"segments": [seg], "history_segments": [], "rows": [], "user_msgs": [], "loaded": True}
    p._write_ui_json("s1", st)

    p._reveal_note(seg)          # 新内容到达（真实链路：_on_reasoning → _reveal_note）
    assert seg.get("_shown") == 120, "落字进度被重置，思考气泡会从第一个字重播"
    assert ap._shown_len(seg) == 120


# ---------- D. 未归档最后一轮的耗时/时间行必须一并落盘 ----------

class _FrozenTurn(QObject):
    """已冻结回合的最小替身：只需 `cost`（耗时）与 `turn_meta`（系统时间行）两个读数。

    必须继承 QObject：`_bubble_alive` 用 sip.isdeleted 探活，非 QObject 会走
    except 分支返回 False，落盘路径就取不到快照（测试会假绿）。
    """

    def __init__(self, cost, meta):
        super().__init__()
        self.cost = cost
        self.turn_meta = meta


def _panel_stub(tmp_path, monkeypatch, sid="s1", turn=None):
    """轻量面板桩：只接线落盘所需属性（不构造真面板，避免 MCP/托盘副作用）。"""
    monkeypatch.setattr(agent_skills, "CONFIG_DIR", tmp_path / "agent")
    p = ap.AgentPanel.__new__(ap.AgentPanel)
    p._session_id = sid
    p._ai_bubble = turn
    return p


def _disk_rows(tmp_path, sid="s1") -> list:
    path = tmp_path / "agent" / "sessions" / f"{sid}.ui.json"
    return json.loads(path.read_text(encoding="utf-8"))["rows"]


def test_last_unarchived_turn_stamps_cost_meta_on_disk(tmp_path, monkeypatch):
    """未归档的最后一轮：AI 行由落盘时现追加，必须带上已冻结的 cost/meta。

    否则重启/切回后这一轮的耗时徽章与系统时间行消失（用户反馈的「时间统计消失」）。
    """
    p = _panel_stub(tmp_path, monkeypatch, turn=_FrozenTurn(12.4, "14:02:31 → 14:02:43"))
    st = {"segments": [{"type": "text", "raw": "回复正文"}], "history_segments": [],
          "rows": [{"type": "user", "text": "你好"}], "user_msgs": ["你好"], "loaded": True}

    p._write_ui_json("s1", st)

    rows = _disk_rows(tmp_path)
    assert [r["type"] for r in rows] == ["user", "ai"]
    assert rows[-1]["cost"] == 12.4, "最后一轮的耗时徽章没落盘 → 重启后消失"
    assert rows[-1]["meta"] == "14:02:31 → 14:02:43", "最后一轮的系统时间行没落盘"


def test_persist_refreshes_same_turn_row_in_place(tmp_path, monkeypatch):
    """同一轮重复落盘（任务结束 → 关闭兜底）必须原位刷新，不得堆重复行。"""
    seg = {"type": "text", "raw": "回复正文"}
    p = _panel_stub(tmp_path, monkeypatch, turn=_FrozenTurn(3.0, "09:00:00 → 09:00:03"))
    st = {"segments": [dict(seg)], "history_segments": [],
          "rows": [{"type": "user", "text": "你好"}, {"type": "ai", "segs": [dict(seg)]}],
          "user_msgs": ["你好"], "loaded": True}

    p._write_ui_json("s1", st)
    p._write_ui_json("s1", st)

    rows = _disk_rows(tmp_path)
    assert len(rows) == 2, f"重复落盘堆出了重复行：{rows}"
    assert rows[-1]["cost"] == 3.0 and rows[-1]["meta"] == "09:00:00 → 09:00:03"


def test_no_stamp_for_other_session_or_unfinished_turn(tmp_path, monkeypatch):
    """后台会话与进行中回合都不得带 cost/meta：回合帧只属于当前会话，
    进行中回合耗时未定论 —— 错取会把别的会话/半截数据写进磁盘。"""
    st = {"segments": [{"type": "text", "raw": "回复正文"}], "history_segments": [],
          "rows": [{"type": "user", "text": "你好"}], "user_msgs": ["你好"], "loaded": True}

    # ① 写的是非当前会话（s1 的回合帧不得串到 s2）
    p = _panel_stub(tmp_path, monkeypatch, sid="s1",
                    turn=_FrozenTurn(12.4, "14:02:31 → 14:02:43"))
    p._write_ui_json("s2", dict(st, rows=[dict(r) for r in st["rows"]]))
    row = _disk_rows(tmp_path, "s2")[-1]
    assert "cost" not in row and "meta" not in row, "当前会话的耗时串到了别的会话"

    # ② 当前会话但回合未冻结（进行中：cost 为 None、无时间行）
    p2 = _panel_stub(tmp_path, monkeypatch, sid="s2", turn=_FrozenTurn(None, ""))
    p2._write_ui_json("s2", st)
    row = _disk_rows(tmp_path, "s2")[-1]
    assert "cost" not in row and "meta" not in row, "进行中的回合落了半截耗时数据"
