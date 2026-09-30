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
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

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
