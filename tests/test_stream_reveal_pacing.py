# -*- coding: utf-8 -*-
"""流式落字节奏（reveal pacing）：模型成批吐字必须摊成连续落字。

背景：模型一次吐 10~200 字。UI 若把最新文本整段塞进气泡，观感就是「一跳一大段」，
与浮现动效叠加依旧生硬 —— 这就是用户反馈的「文字输出速度依旧太快」。面板因此给
text/think 段维护「已显示长度 `_shown`」：按基础速度逐帧推进前缀，积压超过
`_REVEAL_MAX_LAG_S` 就按积压量加速追赶（滞后有上界，不会长期落后于模型）。

本文件守护五条契约：
  1. 首批与后续批次一律逐帧推进（不整段跳出），且落字速度不被节拍放大；
  2. 推进单调、不丢字，最终一定显示全部内容；
  3. 未走节奏的段（历史回放 / 后台缓冲）默认显示全文；
  4. 只有最靠后的流式段按节奏推进，更早的段直接补齐；
  5. 收尾 flush 立即补齐（否则正文末尾会被截在节奏里）。
"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PyQt6.QtWidgets import QApplication                              # noqa: E402

_app = QApplication.instance() or QApplication([])

from zhuzhu_Copilot.ui import agent_panel as ap                       # noqa: E402


def _p(segs):
    """轻代理面板：只带落字节奏与段渲染需要的状态（不建真实控件）。"""
    p = ap.AgentPanel.__new__(ap.AgentPanel)
    p._segments = segs
    p._seg_cache = {}
    p._font_scale = lambda: 1.0
    p._ai_turn_max_width = lambda: 520
    p._renders = []
    p._refresh_ai_html = lambda: p._renders.append(1)
    return p


def _tick(p, dt: float = 0.05):
    """手动推进一拍（把上一次触发时刻挪到 dt 之前，结果完全确定）。"""
    p._reveal_t = time.perf_counter() - dt
    p._reveal_tick()


# ---------- 1. 批次按节奏逐帧推进（含首批） ----------

def test_batches_are_revealed_gradually_not_in_one_jump():
    seg = {"type": "text", "raw": "甲" * 30, "streaming": True}
    p = _p([seg])
    p._reveal_note(seg)
    assert ap._shown_len(seg) == 0, "应从空开始按节奏落下（首批也不整段跳出）"

    _tick(p, 0.05)
    first = ap._shown_len(seg)
    assert 0 < first < 30, f"首批应只落下一部分（{ap._REVEAL_CPS} 字/秒）：{first}"

    seg["raw"] += "乙" * 300                     # 第二批 300 字
    p._reveal_note(seg)
    _tick(p, 0.05)
    second = ap._shown_len(seg)
    assert first < second < 330, f"第二批不得整段跳出：{first} → {second}"


def test_reveal_speed_stays_near_base_rate_on_fast_tick():
    """节拍远快于速度时也必须守速度：每拍至少推 1 字会把 90 字/秒放大成 250 字/秒。

    积压取 40 字（小于 基础速度 × 最大滞后 = 54）→ 走「基础速度」分支，
    这正是模型稳定吐字时的常态；若实现里写成「每拍至少 1 字」，100ms 会落下 25 字。
    """
    seg = {"type": "text", "raw": "A" * 40, "streaming": True}
    p = _p([seg])
    p._reveal_note(seg)
    n, dt = 25, 0.004                            # 25 拍 × 4ms = 100ms
    for _ in range(n):
        _tick(p, dt)
    shown = ap._shown_len(seg)
    expect = ap._REVEAL_CPS * dt * n
    assert shown <= expect * 1.6, \
        f"落字速度被节拍放大：100ms 落了 {shown} 字（基础速度约 {expect:.0f} 字）"
    assert shown > 0, "必须确实在落字"


def test_steady_stream_never_drains_the_queue():
    """稳定流式到达下，落字队列不得被抽空 —— 抽空就是「一顿一顿」的急停。

    「丝滑」的充要条件是**落字速度低于到达速度**：队列永远不空，每一拍都有字可落。
    反过来（落字比到达快）就会「攒够一批 → 一口气落完 → 空等下一批」，每个断点都是
    一次可见的急停，观感反而是「一下子跳出好几个字」。基础速度因此必须守在常见
    中文流式到达速度（30~60 字/秒）之下。
    """
    arrival = 60.0                       # 字/秒（常见到达速度上限）
    tick = 1.0 / arrival
    assert ap._REVEAL_CPS < arrival, "基础落字速度必须低于常见到达速度，否则队列会被抽空"
    seg = {"type": "text", "raw": "", "streaming": True}
    p = _p([seg])
    p._reveal_note(seg)
    drained = 0
    for _ in range(60):
        seg["raw"] += "字"
        p._reveal_note(seg)
        _tick(p, tick)
        if ap._shown_len(seg) >= len(seg["raw"]):
            drained += 1
    assert drained <= 3, f"落字队列被抽空 {drained} 次（急停），落字速度相对到达速度过快"


def test_pacing_spreads_a_big_burst_over_time():
    """一次性大段（粘贴式输出）也不许一瞬间全刷出来。"""
    seg = {"type": "text", "raw": "A" * 20, "streaming": True}
    p = _p([seg])
    p._reveal_note(seg)
    seg["raw"] += "B" * 4000
    p._reveal_note(seg)
    _tick(p, 0.05)
    assert ap._shown_len(seg) < 20 + 4000, "4000 字的大段被一次刷完了"


# ---------- 2. 单调、不丢字 ----------

def test_reveal_is_monotonic_and_loses_no_char():
    seg = {"type": "think", "html": "思" * 800}
    p = _p([seg])
    p._reveal_note(seg)
    seg["html"] += "想" * 1200
    prev = ap._shown_len(seg)
    for _ in range(400):                 # 追赶是指数收敛（每拍恒定比例），给足拍数
        _tick(p, 0.05)
        now = ap._shown_len(seg)
        assert now >= prev, "显示长度不得回退"
        prev = now
        if now == len(seg["html"]):
            break
    assert prev == len(seg["html"]), "最终必须显示全部内容（不得丢字）"
    assert p._renders, "推进过程中必须触发渲染（否则界面不动）"


def test_reveal_never_exceeds_arrived_text():
    seg = {"type": "text", "raw": "短", "streaming": True}
    p = _p([seg])
    p._reveal_note(seg)
    for _ in range(5):
        _tick(p, 0.5)
    assert ap._shown_len(seg) <= len(seg["raw"]), "显示长度不得超过已到达字符数"


# ---------- 3. 未走节奏的段默认全文 ----------

def test_segment_without_pacing_state_renders_full_text():
    """历史回放 / 后台会话缓冲的段没有 _shown → 必须默认全文（不得只显示一部分）。"""
    seg = {"type": "text", "raw": "完整正文"}
    assert ap._shown_len(seg) == len(seg["raw"])
    p = _p([seg])
    assert "完整正文" in p._render_seg_html(seg, 0, "text", 14, 11, 13, 300)


def test_non_streaming_segments_do_not_participate():
    for t in ("result", "op", "sub", "image", "mark"):
        assert ap._seg_full_len({"type": t}) is None, t


# ---------- 4. 只有最靠后的流式段按节奏推进 ----------

def test_earlier_streaming_segment_is_flushed_not_paced():
    a = {"type": "text", "raw": "A" * 200, "streaming": True, "_shown": 10}
    b = {"type": "think", "html": "B" * 200, "_shown": 10}
    p = _p([a, b])
    _tick(p, 0.05)
    assert ap._shown_len(a) == 200, "已被后续段接管的旧流式段应直接补齐"
    assert ap._shown_len(b) < 200, "只有最靠后的段按节奏推进"


# ---------- 5. 渲染与签名只看已显示前缀 ----------

def test_rendered_prefix_follows_shown_length():
    seg = {"type": "text", "raw": "甲乙丙丁戊", "streaming": True, "_shown": 2}
    p = _p([seg])
    html = p._render_seg_html(seg, 0, "text", 14, 11, 13, 300)
    assert "甲乙" in html, "已显示前缀必须渲染出来"
    assert "丙丁" not in html, "未推进到的字符不得提前出现"


def test_signature_advances_with_shown_prefix():
    seg = {"type": "text", "raw": "A" * 100, "streaming": True, "_shown": 10}
    before = ap._seg_sig(seg)
    seg["_shown"] = 20
    assert ap._seg_sig(seg) != before, "显示前缀推进必须改变签名，否则块不会重渲染"


def test_flush_reveals_everything():
    seg = {"type": "text", "raw": "字" * 500, "streaming": True, "_shown": 10}
    other = {"type": "think", "html": "思" * 80, "_shown": 10}
    p = _p([seg, other])
    p._reveal_flush()
    assert ap._shown_len(seg) == 500 and ap._shown_len(other) == 80, \
        "收尾 flush 必须补齐全部内容（否则正文末尾被截断）"
    assert p._segments[0]["_shown"] == 500
