# -*- coding: utf-8 -*-
"""长对话流式性能基准：单流 / 并发流 / 会话切换（offscreen Qt，真实布局与信号路由）。

三个场景都用真实代码路径，不 mock：
  A 单流：把 token 通过 evt_signal 从工作线程发到面板（与真实引擎回调同构），
    主线程只跑事件循环 → 量「主线程卡顿峰值 / 每秒可消费 token 数」。
  B 并发流：N 个会话同时发 token（1 个前台 + N-1 个后台），量主线程吞吐与卡顿。
  C 切换：多个长会话之间反复切换，量「首个内容可见耗时」与「全量渲染完成耗时」。

用法：python scripts/bench_stream_sessions.py [--turns 120] [--sessions 4] [--chunks 1500]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import uuid

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)

from PyQt6.QtWidgets import QApplication          # noqa: E402

APP = QApplication.instance() or QApplication(sys.argv)


def settle(max_iter=400):
    for _ in range(max_iter):
        APP.processEvents()
    for _ in range(max_iter):
        if not getattr(APP, "_x", None):
            break
        APP.processEvents()


def _txt(raw, **kw):
    s = {"type": "text", "raw": raw, "streaming": False, "n": len(raw)}
    s.update(kw)
    return s


def make_rows(n_turns: int, user_chars=60, ai_chars=900):
    rows = []
    for i in range(n_turns):
        rows.append({"type": "user",
                     "text": f"第 {i+1} 个问题：" + "请帮我分析这段内容并给出结论。" * max(1, user_chars // 20)})
        segs = [
            _txt("## 分析结论\n\n" + "结论要点说明文字。" * (ai_chars // 12)),
            {"type": "op", "html": "run_command", "cmd": "python script.py",
             "out": "done\n", "name": "run_command", "meta": "", "ico": "", "tip": ""},
            {"type": "split"},
        ]
        rows.append({"type": "ai", "segs": segs, "cost": 3.2, "meta": "2026-01-01 10:00:00"})
    return rows


def install_session(panel, rows, name="长对话"):
    sid = uuid.uuid4().hex[:12]
    st = panel._new_sess_state(sid)
    st["rows"] = json.loads(json.dumps(rows))
    st["segments"] = []
    st["history_segments"] = []
    st["user_msgs"] = [r["text"] for r in rows if r.get("type") == "user"]
    st["_rows_frozen"] = json.loads(json.dumps(rows))
    st["loaded"] = True
    panel._sess[sid] = st
    return sid


def switch_and_wait(panel, sid, max_ms=20000):
    """切到 sid 并推进事件循环直到分片重建结束（或超时），返回 (首内容ms, 完成ms)。"""
    t0 = time.perf_counter()
    panel._switch_to(sid)
    first = None
    while (time.perf_counter() - t0) * 1000 < max_ms:
        APP.processEvents()
        if first is None and panel.msg_lay.count() > 1:
            first = (time.perf_counter() - t0) * 1000
        if panel.__dict__.get("_hist_build") is None and panel._bubble_widgets:
            break
        time.sleep(0.001)
    done = (time.perf_counter() - t0) * 1000
    return first if first is not None else done, done


def bench_stream(panel, sid, chunks, chunk_len=24, pace_ms=2.0, label="单流"):
    """从工作线程发 chunks 个 delta，主线程跑事件循环；返回统计。"""
    stop = threading.Event()
    sent = [0]

    def producer():
        for i in range(chunks):
            if stop.is_set():
                break
            panel.evt_signal.emit(sid, "delta", "字" * chunk_len)
            sent[0] += 1
            if pace_ms:
                time.sleep(pace_ms / 1000.0)
        stop.set()

    panel._session_id = sid
    panel._bind_sess(sid)
    th = threading.Thread(target=producer, daemon=True)
    t0 = time.perf_counter()
    th.start()
    stalls = []
    while th.is_alive():
        t1 = time.perf_counter()
        APP.processEvents()
        stalls.append((time.perf_counter() - t1) * 1000)
        time.sleep(0.001)
    # 收尾：把剩余计时器跑完
    for _ in range(200):
        APP.processEvents()
    wall = (time.perf_counter() - t0) * 1000
    stalls.sort()
    p50 = stalls[len(stalls) // 2] if stalls else 0
    p99 = stalls[int(len(stalls) * 0.99)] if stalls else 0
    print("  [%s] chunks=%d wall=%.0fms  %.0f tok/s  stall p50=%.1f p99=%.1f max=%.1fms"
          % (label, sent[0], wall, sent[0] / max(1e-6, wall / 1000.0), p50, p99,
             max(stalls or [0])))
    return wall, max(stalls or [0])


def bench_switch(panel, sids, rounds=3, label="切换"):
    firsts, dones = [], []
    for _ in range(rounds):
        for sid in sids:
            if sid == panel._session_id:
                continue
            f, d = switch_and_wait(panel, sid)
            firsts.append(f)
            dones.append(d)
    avg1 = sum(firsts) / max(1, len(firsts))
    avg2 = sum(dones) / max(1, len(dones))
    print("  [%s] n=%d  首内容 avg=%.0fms max=%.0fms | 全量 avg=%.0fms max=%.0fms"
          % (label, len(dones), avg1, max(firsts), avg2, max(dones)))
    return avg1, avg2


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--turns", type=int, default=120)
    ap.add_argument("--sessions", type=int, default=4)
    ap.add_argument("--chunks", type=int, default=1500)
    args = ap.parse_args()

    from zhuzhu_Copilot.ui import agent_panel
    panel = agent_panel.AgentPanel(None)
    panel.show()
    settle()

    rows = make_rows(args.turns)
    sids = [install_session(panel, rows, f"会话{i+1}") for i in range(args.sessions)]
    print("=" * 96)
    print("长对话流式基准：turns=%d sessions=%d chunks=%d（每 chunk %d 字）"
          % (args.turns, args.sessions, args.chunks, 24))
    print("=" * 96)

    # --- A 单流 ---
    bench_switch(panel, [sids[0]])            # 先切到会话1，进入长会话态
    print("\n--- A. 单会话长对话流式 ---")
    bench_stream(panel, sids[0], args.chunks, label="A 单流(%d轮)" % args.turns)

    # --- B 并发流 ---
    print("\n--- B. %d 会话并发流式（1 前台 + %d 后台） ---" % (args.sessions, args.sessions - 1))
    panel._session_id = sids[0]
    panel._bind_sess(sids[0])
    per = max(1, args.chunks // args.sessions)
    threads = []

    def producer(sid, n):
        for _ in range(n):
            panel.evt_signal.emit(sid, "delta", "字" * 24)
            time.sleep(0.002)

    t0 = time.perf_counter()
    for sid in sids:
        th = threading.Thread(target=producer, args=(sid, per), daemon=True)
        threads.append(th)
        th.start()
    stalls = []
    while any(t.is_alive() for t in threads):
        t1 = time.perf_counter()
        APP.processEvents()
        stalls.append((time.perf_counter() - t1) * 1000)
        time.sleep(0.001)
    for _ in range(200):
        APP.processEvents()
    wall = (time.perf_counter() - t0) * 1000
    stalls.sort()
    total = per * len(sids)
    print("  [B 并发] chunks=%d wall=%.0fms  %.0f tok/s  stall p50=%.1f p99=%.1f max=%.1fms"
          % (total, wall, total / max(1e-6, wall / 1000.0),
             stalls[len(stalls) // 2], stalls[int(len(stalls) * 0.99)], max(stalls)))

    # --- C 切换 ---
    print("\n--- C. %d 个长会话之间切换 ---" % args.sessions)
    bench_switch(panel, sids, rounds=3)

    print("=" * 96)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())