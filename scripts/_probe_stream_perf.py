"""流式长文本输出性能探针：量化「逐字到达」下的两条链路成本。

用法：python scripts/_probe_stream_perf.py [总字数] [chunk 大小(可多个)]
输出：
  A 直连链路  —— 主线程逐块调用 _on_delta（测每块热路径成本：字符串拼接/分段登记）
  B 信号链路  —— 工作线程按上游速率 emit，经 Qt 队列信号进 _on_delta（测跨线程事件风暴）
  两场景同时统计 UI 刷新 tick（_apply_refresh_ai_html）的次数/均值/峰值耗时。

数据目录重定向到临时目录：探针会走真实面板的流式路径，绝不能写进用户真实会话。
"""
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PyQt6.QtCore import QObject, pyqtSignal                              # noqa: E402
from PyQt6.QtWidgets import QApplication                                  # noqa: E402

from zhuzhu_Copilot import app_identity                                   # noqa: E402

_home = Path(tempfile.mkdtemp(prefix="probe_stream_home_"))
app_identity._home = lambda: _home
app_identity._migrated = True

_app = QApplication.instance() or QApplication([])

from zhuzhu_Copilot.ui import main_window as mw                           # noqa: E402
from zhuzhu_Copilot.ui import agent_panel as ap_mod                       # noqa: E402
from zhuzhu_Copilot.ui.agent_panel import AgentPanel                      # noqa: E402


def _pump(seconds: float):
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        _app.processEvents()
        time.sleep(0.001)


class _LiveThread:
    def is_alive(self):
        return True


class _StubEngine:
    """探针桩引擎：只满足「任务进行中」判定所需契约，不参与渲染。

    必要性（实测）：面板有个 400ms 轮询器（_refresh_meta 末尾），只要「无引擎线程在跑」
    且 _task_active=True 就判定任务结束并收尾 —— 合成回合会被清空（_segments 归零、
    气泡置空），流式刷新随之停摆（分步计时全 0）。真机上引擎线程始终在跑，
    桩引擎因此不是「绕过逻辑」，而是把探针还原成真实运行态。
    """
    def __init__(self):
        self._thread = _LiveThread()
        self.tokens = {"prompt": 0, "completion": 0, "cache_hit": 0, "cache_miss": 0}


def _build_panel() -> AgentPanel:
    """真实面板（与 tests/test_ai_turn_wrapper.py 同款减负打桩：不扫描/不托盘/不弹指南）。"""
    mw.CopilotPanel._start_scan = lambda self, *a: None
    mw.CopilotPanel._init_update_check = lambda self: None
    mw.CopilotPanel._check_admin = lambda self: None
    mw.CopilotPanel._setup_tray = lambda self: None
    AgentPanel._maybe_show_onboarding = lambda self: None
    p = AgentPanel(None)
    p.resize(1000, 900)
    p.show()
    # 启动初始化是延迟执行的（singleShot(0) 的 _finish_startup_init + 切会话收尾的单帧延迟），
    # 其中 _switch_to 会把 _engine 覆盖回 None —— 必须等它们全部跑完再装桩引擎。
    _pump(1.2)
    # 任务视为进行中：轮询器不得提前收尾。两处都要装 —— _active_engine 的口径是
    # 「会话内引擎优先，回退 self._engine」，只装 self._engine 会被会话槽里的真引擎盖掉。
    stub = _StubEngine()
    p._engine = stub
    st = p._sess.get(p._session_id)
    if isinstance(st, dict):
        st["engine"] = stub
    return p


_WRAPPED = ("_apply_refresh_ai_html", "_render_ai_frame", "_sync_bubble_heights",
            "_render_seg_html")


def _instrument_refresh(p: AgentPanel):
    """包装 UI 刷新函数与两个子步骤，记录每次调用耗时（ms）：定位 tick 内大头。

    返回 (steps, restore)：steps 为 {"tick"/"render"/"heights" -> 耗时列表}；
    restore 撤销实例属性覆盖（del 掉即可回落类方法），避免场景间层层套壳。
    """
    steps = {"tick": [], "render": [], "heights": [], "html": []}
    orig = {name: getattr(p, name) for name in _WRAPPED}
    keys = {"_apply_refresh_ai_html": "tick", "_render_ai_frame": "render",
            "_sync_bubble_heights": "heights", "_render_seg_html": "html"}

    def wrap(name):
        fn = orig[name]
        key = keys[name]
        def timed(*a, **kw):
            t0 = time.perf_counter()
            try:
                return fn(*a, **kw)
            finally:
                steps[key].append((time.perf_counter() - t0) * 1000.0)
        return timed

    for name in _WRAPPED:
        p.__dict__[name] = wrap(name)

    def restore():
        for name in _WRAPPED:
            p.__dict__.pop(name, None)     # 撤销实例覆盖，回落到原类方法

    return steps, restore


def _stats(vals: list) -> dict:
    if not vals:
        return {"n": 0, "mean": 0.0, "max": 0.0, "sum": 0.0}
    return {"n": len(vals), "mean": statistics.fmean(vals), "max": max(vals),
            "sum": sum(vals)}


def _steps_summary(steps: dict) -> dict:
    """把分步耗时汇总为扁平字段：每步的次数 / 均值 / 峰值 / 累计。"""
    out = {}
    for name, vals in steps.items():
        s = _stats(vals)
        out[f"{name}_n"] = s["n"]
        out[f"{name}_mean_ms"] = s["mean"]
        out[f"{name}_max_ms"] = s["max"]
        out[f"{name}_sum_ms"] = s["sum"]
    return out


def _reset_turn(p: AgentPanel):
    p._reveal_flush()
    p._task_active = True
    p._segments = []
    p._ai_bubble = None
    p._add_ai_group_bubble([])


class _Emitter(QObject):
    evt = pyqtSignal(str)


def scenario_direct(p: AgentPanel, total: int, chunk: int):
    """A：主线程逐块直连调用（无事件循环）—— 纯热路径成本。"""
    _reset_turn(p)
    steps, restore = _instrument_refresh(p)
    text = "字" * chunk
    n = max(1, total // chunk)
    t0 = time.perf_counter()
    for _ in range(n):
        p._on_delta(text)
    dt = (time.perf_counter() - t0) * 1000.0
    _pump(0.2)
    p._reveal_flush()
    _pump(0.15)          # 让收尾刷新也落入本次统计
    restore()
    p._task_active = False
    out = {"chunks": n, "hot_ms": dt, "per_chunk_us": dt * 1000.0 / n}
    out.update(_steps_summary(steps))
    return out


def scenario_signal(p: AgentPanel, total: int, chunk: int, rate_cps: int):
    """B：工作线程按「每秒 rate_cps 字」的上游速率 emit，经队列信号进 _on_delta。

    统计：主线程槽执行次数/单次耗时、emit 端到端滞后（最后一次）、工作线程发完总时长。
    """
    import threading
    _reset_turn(p)
    steps, restore = _instrument_refresh(p)
    em = _Emitter()
    slot_durs, slot_at, emit_at = [], [], []
    orig_delta = p._on_delta

    def slot(s: str):
        t0 = time.perf_counter()
        orig_delta(s)
        slot_durs.append((time.perf_counter() - t0) * 1000.0)
        slot_at.append(time.perf_counter())

    em.evt.connect(slot)
    text = "字" * chunk
    n = max(1, total // chunk)
    gap = chunk / float(rate_cps)          # 按字速率换算成块间隔
    stop = threading.Event()

    def worker():
        for _ in range(n):
            if stop.is_set():
                break
            emit_at.append(time.perf_counter())
            em.evt.emit(text)
            time.sleep(gap)

    th = threading.Thread(target=worker, daemon=True)
    t0 = time.perf_counter()
    th.start()
    while th.is_alive():
        _pump(0.02)
    th.join(timeout=5)
    lag_ms = ((slot_at[-1] - emit_at[-1]) * 1000.0) if (slot_at and emit_at) else 0.0
    stop.set()
    _pump(0.2)
    p._reveal_flush()
    _pump(0.15)
    restore()
    p._task_active = False
    out = {"chunks": n, "wall_ms": (time.perf_counter() - t0) * 1000.0,
           "slots": len(slot_durs),
           "slot_mean_ms": statistics.fmean(slot_durs) if slot_durs else 0.0,
           "slot_max_ms": max(slot_durs) if slot_durs else 0.0,
           "last_lag_ms": lag_ms}
    out.update(_steps_summary(steps))
    return out


def scenario_steady(p: AgentPanel, length: int, frames: int, step: int = 0):
    """C：正文长度 L 的**稳态单帧成本**（决定长文本能不能按高频 tick 刷新）。

    step=0：前缀固定为全文（如落字已追平模型），测「同一长度下反复刷新」的单帧成本；
    step>0：每帧推进 step 字（真实流式形状：前缀在增长，尾部块每帧重算）。
    分步口径：html = 面板侧 markdown→HTML 构建（_render_seg_html，含 _render_text_incr）；
    Qt 侧 = render_total - html（富文本解析/布局，QLabel.setText 的大头）。
    """
    _reset_turn(p)
    shown = max(0, length - frames * step) if step else length
    seg = {"type": "text", "raw": "字" * length, "streaming": True,
           "_shown": shown, "_shown_f": float(shown)}
    p._segments.append(seg)
    p._render_ai_frame(p._ai_bubble, p._segments)   # 预热：首帧含控件创建，不计入
    steps, restore = _instrument_refresh(p)
    t0 = time.perf_counter()
    for _ in range(frames):
        if step:
            shown = min(length, shown + step)
            seg["_shown"] = shown
            seg["_shown_f"] = float(shown)
        p._apply_refresh_ai_html()      # 与 singleShot 触发的实际路径同一函数
    dt = (time.perf_counter() - t0) * 1000.0
    _pump(0.15)
    p._reveal_flush()
    _pump(0.15)
    restore()
    p._task_active = False
    out = {"length": length, "frames": frames, "step": step, "total_ms": dt,
           "per_frame_ms": dt / frames}
    tick = steps["tick"]
    if tick:      # 逐帧成本的增长趋势：首帧 vs 末帧
        out["first_frame_ms"] = tick[0]
        out["last_frame_ms"] = tick[-1]
    out.update(_steps_summary(steps))
    return out


def _print(title: str, r: dict):
    print(f"\n{title}")
    for k, v in r.items():
        print(f"  {k:<22} {v:,.2f}" if isinstance(v, float) else f"  {k:<22} {v:,}")


def main():
    args = sys.argv[1:]
    p = _build_panel()
    if args and args[0] == "steady":
        # 模式 C：固定长度下的稳态单帧成本曲线（定位长文本渲染的 O(n) 增长）
        lengths = [int(x) for x in args[1].split(",")] if len(args) > 1 \
            else [2_000, 20_000, 100_000]
        frames = int(os.environ.get("PROBE_FRAMES", "40"))
        print(f"面板宽度 = {p.width()}px | 帧数 = {frames}")
        for L in lengths:
            step = max(1, L // frames)
            _print(f"[C 固定] 正文 {L:,} 字 · 前缀固定全量", scenario_steady(p, L, frames, 0))
            _print(f"[C 增长] 正文 {L:,} 字 · 每帧 +{step} 字（0 → 全量）",
                   scenario_steady(p, L, frames, step))
        print("\n（per_frame_ms = 单帧总耗时；html = 面板侧 markdown→HTML 构建；"
              "Qt 侧 ≈ render - html（富文本解析/布局）；"
              "tick ≈ render + heights —— 它就是主线程被占用的时间）")
        sys.stdout.flush()
        os._exit(0)

    total = int(args[0]) if args else 50_000
    chunks = [int(x) for x in args[1:]] or [1, 50]
    print(f"总字数 = {total:,} | chunk = {chunks} | 面板宽度 = {p.width()}px")
    rate = int(os.environ.get("PROBE_RATE_CPS", "3000"))   # 上游到达速率（字/秒）
    for c in chunks:
        _print(f"[A 直连] chunk={c}", scenario_direct(p, total, c))
        # 同机对比：把占空比目标抬到不可能达到 = 关闭成本自适应（优化前的固定 4ms 节拍）
        keep = ap_mod._STREAM_DUTY_TARGET
        ap_mod._STREAM_DUTY_TARGET = 1e9
        old = scenario_signal(p, total, c, rate)
        ap_mod._STREAM_DUTY_TARGET = keep
        new = scenario_signal(p, total, c, rate)
        for r, tag in ((old, "旧节拍(固定4ms)"), (new, "新节拍(成本自适应)")):
            r["duty_pct"] = (r["tick_sum_ms"] / r["wall_ms"] * 100.0) if r["wall_ms"] else 0.0
            _print(f"[B 信号] chunk={c} · {tag}（上游 {rate} 字/秒）", r)
    print("\n（A 的 hot_ms 随 chunk 变小而暴涨 = 拼接/登记是 O(n²)；"
          "B 的 slots/滞后 = 跨线程事件风暴程度；"
          "tick = 一次 UI 刷新总耗时 = render（HTML 重建/QLabel 落字）+ heights（气泡高度测量））")
    sys.stdout.flush()
    os._exit(0)     # 绕开 Qt 退出期对象析构顺序导致的 0xC0000005（探针脚本无需正常退出）


if __name__ == "__main__":
    main()