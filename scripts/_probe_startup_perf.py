"""启动耗时探针：把「主面板可用之前」的各阶段单独计时，定位启动大头。

用法：python scripts/_probe_startup_perf.py [--real-home] [--sim]
  默认：把真实数据目录整份复制到临时家目录后测（忠实还原会话恢复成本，且不写脏真实数据）；
  --real-home：直接使用真实数据目录（只读意图，但面板自身可能刷新 last_session 等配置）。
  --sim：先按**真实启动顺序**模拟一遍（模块链 → 启动动画 2s 内并行预热 → 动画结束构造面板
         → 首帧后才预热浮层），输出「动画结束后到界面可见」的关键路径耗时；再跑常规阶段表。
        只有该模式能量化「预热提前并行 / 浮层预热移出关键路径」的收益。

输出：阶段耗时表（import / 配置解密 / 面板构造 / 预创建浮层）+ 会话体量与首屏渲染规模。
"""
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PyQt6.QtWidgets import QApplication                                   # noqa: E402

from zhuzhu_Copilot import app_identity                                    # noqa: E402

# 启动动画展示时长：与 src/main.py 的 QTimer.singleShot(2000, _close_splash_then_open_panel) 对齐
SPLASH_SECONDS = 2.0

MARKS: list = []


def mark(name: str, t0: float):
    MARKS.append((name, (time.perf_counter() - t0) * 1000.0))


def _pump(app, seconds: float):
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.001)


def _simulate_real_order(app) -> None:
    """按真实启动顺序走一遍，量化关键路径（仅 --sim 模式；必须在模块冷启动时先跑）。

    顺序与 src/main.py 完全一致：
      ① 进程模块链（main.py 顶层：main_window/WebEngine/env 设置）
      ② 启动动画展示前启动预热线程（import agent_panel + 配置解密）→ 与固定 2s 动画并行
      ③ 动画结束：构造面板 + show（此时预热应已就绪）
      ④ 首帧之后才预热 Copilot 浮层（不在关键路径上）
    """
    import main as app_main                                                # noqa: E402
    from zhuzhu_Copilot.ui import main_window as mw                        # noqa: E402

    mw.CopilotPanel._start_scan = lambda self, *a: None
    mw.CopilotPanel._init_update_check = lambda self: None
    mw.CopilotPanel._check_admin = lambda self: None
    mw.CopilotPanel._setup_tray = lambda self: None

    t = time.perf_counter()
    th = threading.Thread(target=app_main._preload_agent_panel_ready,
                          daemon=True, name="agent_panel_preload")
    th.start()
    _pump(app, SPLASH_SECONDS)
    preload_done = not th.is_alive()
    mark(f"启动动画窗口 {SPLASH_SECONDS:g}s（并行预热"
         f"{'已完成' if preload_done else '未完成，将拖慢下一步'}）", t)

    from zhuzhu_Copilot.ui.agent_panel import AgentPanel
    AgentPanel._maybe_show_onboarding = lambda self: None
    t = time.perf_counter()
    panel = AgentPanel(None)
    panel.resize(1000, 900)
    panel.show()
    _pump(app, 0.35)
    mark("动画结束 → 界面可见（import 命中 + 构造 + 首帧）= 关键路径", t)

    t = time.perf_counter()
    try:
        panel.prewarm_copilot_panel()
    except Exception as e:          # 离屏下托盘/扫描可能不可用：如实记录
        print(f"  prewarm 异常（已忽略）：{e!r}")
    mark("预热浮层（首个 QTimer 间隔后执行，不在关键路径）", t)
    panel.close()
    panel.deleteLater()
    _pump(app, 0.2)


def _prepare_home() -> Path:
    real = app_identity.data_root()
    if "--real-home" in sys.argv:
        return real.parent
    home = Path(tempfile.mkdtemp(prefix="probe_start_home_"))
    dst = home / app_identity.DATA_DIR_NAME
    if real.exists():
        t = time.perf_counter()
        shutil.copytree(real, dst, ignore=shutil.ignore_patterns("logs", "*.log", "cache"))
        mark(f"复制数据目录（{sum(f.stat().st_size for f in dst.rglob('*') if f.is_file()) / 1048576:.0f}MB，不计入耗时）",
             t)
    else:
        dst.mkdir(parents=True, exist_ok=True)
    return home


def main():
    home = _prepare_home()
    app_identity._home = lambda: home
    app_identity._migrated = True

    t = time.perf_counter()
    import zhuzhu_Copilot.ui.main_window  # noqa: F401
    mark("import main_window（含样式链）", t)

    t = time.perf_counter()
    _app = QApplication.instance() or QApplication([])
    mark("QApplication 创建", t)

    if "--sim" in sys.argv:
        _simulate_real_order(_app)

    t = time.perf_counter()
    from zhuzhu_Copilot.ui import agent_panel as ap
    mark("import agent_panel（16k 行 + 主题初始化）", t)

    t = time.perf_counter()
    from zhuzhu_Copilot.core import agent_skills, agent_llm
    agent_skills.load_settings()
    mark("agent_skills.load_settings（解密缓存）", t)

    t = time.perf_counter()
    agent_llm.load_model_config()
    mark("agent_llm.load_model_config（PBKDF2 + 解密）", t)

    from zhuzhu_Copilot.ui import main_window as mw
    mw.CopilotPanel._start_scan = lambda self, *a: None
    mw.CopilotPanel._init_update_check = lambda self: None
    mw.CopilotPanel._check_admin = lambda self: None
    mw.CopilotPanel._setup_tray = lambda self: None
    ap.AgentPanel._maybe_show_onboarding = lambda self: None

    t = time.perf_counter()
    panel = ap.AgentPanel(None)
    mark("AgentPanel 构造（含会话恢复 + 首屏渲染）", t)

    t = time.perf_counter()
    panel.resize(1000, 900)
    panel.show()
    _pump = time.perf_counter() + 0.3
    while time.perf_counter() < _pump:
        _app.processEvents()
        time.sleep(0.001)
    mark("show + 首帧事件循环（300ms 窗口）", t)

    t = time.perf_counter()
    try:
        panel.prewarm_copilot_panel()
    except Exception as e:          # 离屏下托盘/扫描可能不可用：如实记录
        print(f"  prewarm 异常（已忽略）：{e!r}")
    mark("prewarm_copilot_panel（预创建浮层）", t)

    total = sum(ms for name, ms in MARKS if "不计入" not in name)
    print(f"\n启动阶段耗时表（数据目录：{home}）")
    for name, ms in MARKS:
        print(f"  {ms:>9.1f} ms  {name}")
    print(f"  {'-' * 9}  合计（不计复制） {total:.1f} ms")

    # 会话规模：解释首屏渲染成本
    try:
        sess_dir = app_identity.data_root() / "agent" / "sessions"
        files = sorted(sess_dir.glob("*.ui.json"), key=lambda f: f.stat().st_mtime, reverse=True)
        print(f"\n会话文件 {len(files)} 个；最近一个 {files[0].name if files else '无'} "
              f"（{files[0].stat().st_size / 1024:.0f}KB）" if files else "\n无会话文件")
    except Exception as e:
        print(f"会话统计失败：{e!r}")


if __name__ == "__main__":
    main()