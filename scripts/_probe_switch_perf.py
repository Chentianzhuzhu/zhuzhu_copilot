"""会话切换加载性能探针：长对话「点过去到可见」各阶段计时，定位切换卡顿源。

用法：python scripts/_probe_switch_perf.py [--sid <会话id>] [--real-home] [--top N]
  默认：复制真实数据目录到临时家目录（忠实还原读盘与渲染成本，且不写脏真实数据）；
  目标会话默认取**体积最大**的会话 .ui.json（--top N 则依次测前 N 个）。

三个阶段分别计时：
  ① 同步段：`_switch_to` 里清旧视图 + 建引擎 + 起读盘线程（主线程）；
  ② 后台段：读盘/解析/`load_context`（后台线程，期间界面仍响应）；
  ③ 渲染段：`_finish_switch` → `_render_history_all` 全量重建（主线程，用户可见白屏的时长）。
并输出渲染段内部的环节聚合（气泡创建 / HTML 构建 / 高度测量 / 布局激活 / 定位点）。

注意：进程内两次切换的缓存命中率不同（首次全冷，再入内存态可复用段缓存），
因此同时输出「首次进入（冷）」与「切走再切回（温）」两组数据。
"""
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PyQt6.QtWidgets import QApplication                                   # noqa: E402

from zhuzhu_Copilot import app_identity                                    # noqa: E402

COST: dict = {}
COUNT: dict = {}
FINISH_LOG: list = []          # (sid, start, end, entered)

_WRAPPED = set()


def _wrap(cls, name: str, key: str = None):
    """类级计时包装（构造面板前调用：信号连接会绑定包装后的方法）"""
    fn = getattr(cls, name, None)
    if fn is None:
        return
    k = key or name
    if (cls, k) in _WRAPPED:
        return
    _WRAPPED.add((cls, k))

    def timed(self, *a, **kw):
        t0 = time.perf_counter()
        try:
            return fn(self, *a, **kw)
        finally:
            dt = time.perf_counter() - t0
            COST[k] = COST.get(k, 0.0) + dt
            COUNT[k] = COUNT.get(k, 0) + 1

    setattr(cls, name, timed)


def _wrap_mod(mod, name: str):
    """模块级函数计时包装（模块内以全局名调用，替换属性即可生效）"""
    fn = getattr(mod, name, None)
    if fn is None or (mod, name) in _WRAPPED:
        return
    _WRAPPED.add((mod, name))

    def timed(*a, **kw):
        t0 = time.perf_counter()
        try:
            return fn(*a, **kw)
        finally:
            dt = time.perf_counter() - t0
            COST[name] = COST.get(name, 0.0) + dt
            COUNT[name] = COUNT.get(name, 0) + 1

    setattr(mod, name, timed)


def _wrap_finish(cls, name: str):
    """`_finish_switch` 专用包装：记录 (sid, 起, 止, 是否越过 sid 守卫)"""
    fn = getattr(cls, name)

    def timed(self, sid, *a, **kw):
        t0 = time.perf_counter()
        try:
            return fn(self, sid, *a, **kw)
        finally:
            FINISH_LOG.append((sid, t0, time.perf_counter(),
                               sid == self.__dict__.get("_session_id")))

    setattr(cls, name, timed)


def _pump(app, seconds: float):
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.001)


def _wait(app, cond, timeout: float = 60.0) -> bool:
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        if cond():
            return True
        app.processEvents()
        time.sleep(0.002)
    return False


def _prepare_home() -> Path:
    real = app_identity.data_root()
    if "--real-home" in sys.argv:
        return real.parent
    home = Path(tempfile.mkdtemp(prefix="probe_switch_home_"))
    dst = home / app_identity.DATA_DIR_NAME
    if real.exists():
        shutil.copytree(real, dst, ignore=shutil.ignore_patterns("logs", "*.log", "cache"))
    else:
        dst.mkdir(parents=True, exist_ok=True)
    return home


def _session_scale(path: Path) -> dict:
    """目标会话规模：行数 / 段数 / 正文与思考字符量（解释渲染成本）"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"err": repr(e)}
    rows = [r for r in (data.get("rows") or []) if isinstance(r, dict)]
    segs = [s for r in rows for s in (r.get("segs") or []) if isinstance(s, dict)]
    segs += [s for s in (data.get("segments") or []) if isinstance(s, dict)]
    chars = sum(len(s.get("raw") or s.get("html") or "") for s in segs)
    kinds = {}
    for s in segs:
        kinds[s.get("type")] = kinds.get(s.get("type"), 0) + 1
    return {"kb": path.stat().st_size / 1024, "rows": len(rows), "ai_rows":
            sum(1 for r in rows if r.get("type") == "ai"), "segs": len(segs),
            "chars": chars, "kinds": kinds}


def _measure(app, panel, sid: str, label: str) -> dict:
    warm = bool((panel._sess.get(sid) or {}).get("loaded"))
    COST.clear()
    COUNT.clear()
    FINISH_LOG.clear()
    t0 = time.perf_counter()
    if "--profile" in sys.argv:
        import cProfile
        import pstats
        import io
        pr = cProfile.Profile()
        pr.enable()
        panel._switch_to(sid)
        pr.disable()
        st = pstats.Stats(pr, stream=sys.stdout)
        st.sort_stats("cumulative").print_stats(35)
        print("\n=== setVisible 调用方归因 ===")
        st.print_callers("setVisible")
    else:
        panel._switch_to(sid)
    t1 = time.perf_counter()
    render_ms = None
    bg_ms = None
    if warm:
        t_done = t1
    else:
        _wait(app, lambda: any(e[3] and e[0] == sid for e in FINISH_LOG), 60.0)
        t_done = time.perf_counter()
        for _s, _fs, _fe, _en in FINISH_LOG:
            print(f"    [debug] finish sid={_s} +{( _fs - t0) * 1000:.1f}→+{(_fe - t0) * 1000:.1f} ms "
                  f"历时 {(_fe - _fs) * 1000:.1f} ms entered={_en}（目标 {sid}）")
        hit = next((e for e in FINISH_LOG if e[3] and e[0] == sid), None)
        if hit:
            _sid, fs, fe, _entered = hit
            render_ms = (fe - fs) * 1000.0
            bg_ms = (fs - t1) * 1000.0
    _pump(app, 0.30)               # 让 0ms / 200ms 的延迟重排自愈跑完
    total = (t_done - t0) * 1000.0
    # 空闲切片把隐藏过程块铺完所需时间（不计入切换阻塞：发生在事件循环空闲里）
    from zhuzhu_Copilot.ui import agent_chat_bubbles as cb_mod
    sp = getattr(cb_mod, "_SPREADER", None)
    fill_ms = None
    if sp is not None and sp._queue:
        tf = time.perf_counter()
        while sp._queue and time.perf_counter() - tf < 15.0:
            app.processEvents()
            time.sleep(0.001)
        fill_ms = (time.perf_counter() - tf) * 1000.0
    print(f"\n[{label}] sid={sid} {'（内存态命中）' if warm else '（首次读盘）'}")
    print(f"  总计 {total:8.1f} ms = 同步段 {(t1 - t0) * 1000:7.1f}"
          + (f" + 后台读盘 {bg_ms:7.1f}" if bg_ms is not None else "（+ 后台读盘并入同步段内无）")
          + (f" + 主线程渲染 {render_ms:7.1f}" if render_ms is not None else "")
          + (f"，空闲铺完 {fill_ms:7.1f}" if fill_ms is not None else ""))
    rows = sorted(COST.items(), key=lambda kv: -kv[1])
    for name, sec in rows:
        n = COUNT.get(name, 0)
        if sec < 0.0005:
            continue
        print(f"    {name:24s} {sec * 1000:9.1f} ms  调用 {n:4d} 次  均 "
              f"{sec / max(n, 1) * 1000:7.2f} ms")
    return {"total": total, "render": render_ms}


def main():
    home = _prepare_home()
    app_identity._home = lambda: home
    app_identity._migrated = True
    app = QApplication.instance() or QApplication([])

    sess_dir = app_identity.data_root() / "agent" / "sessions"
    files = sorted(sess_dir.glob("*.ui.json"), key=lambda f: f.stat().st_size, reverse=True)
    if "--sid" in sys.argv:
        want = sys.argv[sys.argv.index("--sid") + 1]
        files = [f for f in files if f.name.startswith(want)]
    top = int(sys.argv[sys.argv.index("--top") + 1]) if "--top" in sys.argv else 1
    targets = [f.stem[:-3] for f in files[:top]]       # xxx.ui.json → xxx
    if not targets:
        print("没有可测会话（sessions 目录为空）")
        return

    from zhuzhu_Copilot.ui import main_window as mw                        # noqa: E402
    from zhuzhu_Copilot.ui import agent_chat_bubbles as cb                 # noqa: E402
    from zhuzhu_Copilot.ui.agent_panel import AgentPanel                   # noqa: E402

    mw.CopilotPanel._start_scan = lambda self, *a: None
    mw.CopilotPanel._init_update_check = lambda self: None
    mw.CopilotPanel._check_admin = lambda self: None
    mw.CopilotPanel._setup_tray = lambda self: None
    AgentPanel._maybe_show_onboarding = lambda self: None

    _wrap(AgentPanel, "_switch_to")
    _wrap_finish(AgentPanel, "_finish_switch")
    for nm in ("_render_history_all", "_add_bubble", "_add_ai_group_bubble",
               "_render_ai_frame", "_seg_blocks", "_relayout_messages",
               "_sync_bubble_heights", "_free_layout_item", "_scroll_bottom",
               "_reconstruct_rows", "_msg_nav_push", "_commit_sess", "_bind_sess"):
        _wrap(AgentPanel, nm)
    _wrap(cb.ChatTurn, "render")
    _wrap(cb.ChatTurn, "relayout_heights")
    _wrap(cb.ChatTurn, "_rebuild_blocks")
    for nm in ("_insert_blocks", "_make_block", "_update_block", "_wire", "_drop_from"):
        _wrap(cb.ChatTurn, nm)
    _wrap(cb.ThinkBubble, "set_content", key="Think.set_content")
    _wrap(cb.ToolCallRow, "set_content", key="Tool.set_content")
    _wrap(cb.CmdBlock, "set_content", key="Cmd.set_content")
    _wrap(cb.RichBlock, "set_html", key="RichBlock.set_html")
    _wrap_mod(cb, "_label_hfw")
    _wrap_mod(cb, "_tile_pixmap")
    for nm in ("_bump_content", "_apply_fold", "_emerge_touch"):
        _wrap(cb.ThinkBubble, nm, key=f"Think.{nm}")

    if os.environ.get("PROBE_INLINE_RESUME"):
        # A/B 对照：把空闲切片退化为「立即执行」——等价于延迟收尾上线前的行为
        cb._IdleSpreader.push = lambda self, fn: fn()
        print("（A/B 对照：PROBE_INLINE_RESUME=1，隐藏过程块收尾立即执行）")

    t = time.perf_counter()
    panel = AgentPanel(None)
    panel.resize(1000, 900)
    panel.show()
    _pump(app, 0.4)
    print(f"面板构造 + 首帧：{(time.perf_counter() - t) * 1000:.0f} ms")

    for sid in targets:
        scale = _session_scale(sess_dir / f"{sid}.ui.json")
        print(f"\n=== 目标会话 {sid} 规模：{scale}")

        # 先离开目标会话（切到别的会话或新建空会话），并清掉其内存态 → 强制「首次进入」
        panel._sess.pop(sid, None)
        if panel._session_id == sid:
            other = next((s for s in (panel._sess or {}) if s != sid), "")
            if other:
                panel._switch_to(other)
                _pump(app, 0.3)
            else:
                panel._new_session()
                _pump(app, 0.3)
        _measure(app, panel, sid, "首次进入（冷）")

        # 切走再切回（内存态命中：测纯渲染）
        other = next((s for s in (panel._sess or {}) if s != sid), "")
        if other:
            panel._switch_to(other)
            _pump(app, 0.2)
            _measure(app, panel, sid, "切走再切回（温，含清目标视图的内存态渲染）")
        else:
            panel._new_session()
            _pump(app, 0.2)
            _measure(app, panel, sid, "切走再切回（温）")

    try:
        panel.close()
    except Exception:
        pass
    print(f"\n（数据目录副本：{home}，可整体删除）")


if __name__ == "__main__":
    main()