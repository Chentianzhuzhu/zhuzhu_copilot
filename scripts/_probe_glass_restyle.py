"""端到端验证：玻璃开关切换后，四个浮窗与设置页的控件底是否真的换成玻璃填充。

用户反馈「设置页左侧设置项、右侧每个子项、以及工作树 / Git / 任务清单 / 预览面板
都残留原有 UI」—— 根因是这两条刷新链路都没生效（浮窗属性名写错；设置页控件
QSS 构造后固化）。本探针构造真实 AgentPanel + 真实设置对话框，跑一遍玻璃开↔关。

用法：python scripts/_probe_glass_restyle.py
"""
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PyQt6.QtWidgets import QApplication                                    # noqa: E402


def _pump(app, seconds):
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.002)


def _probe(label, panel, dlg):
    from zhuzhu_Copilot.core import app_glass
    print(f"\n=== {label}（enabled={app_glass.params().enabled}）===")
    targets = [
        ("工作树 tree", getattr(panel.wt_win, "tree", None)),
        ("Git list", getattr(panel.git_win, "list", None)),
        ("任务清单 panel", getattr(panel.todos_win, "panel", None)),
        ("预览 text", getattr(panel.code_win, "text", None)),
        ("设置页 nav", getattr(dlg, "nav", None)),
        ("设置页 取消按钮", getattr(dlg, "cancel_btn", None)),
    ]
    glassy = 0
    for name, w in targets:
        if w is None:
            print(f"  {name:<16} <无此控件>")
            continue
        qss = w.styleSheet()
        i = qss.find("background")
        frag = qss[i:i + 34].replace("\n", " ")
        hit = "rgba(" in qss
        glassy += int(hit)
        print(f"  {name:<16} {'玻璃' if hit else '实色'}  {frag}")
    return glassy


def _page_of(dlg, w) -> str:
    """控件属于设置页第几页（定位「哪页没被重建」）。"""
    try:
        p = w.parentWidget()
        while p is not None and p is not dlg:
            idx = dlg.stack.indexOf(p)
            if idx >= 0:
                return f"页{idx}"
            p = p.parentWidget()
    except Exception:
        pass
    return "页外"


def _scan_solid(label, roots, dlg=None):
    """列出仍带「不透明实色底」的控件（玻璃化收尾用）。

    只报 `background: #RRGGBB`；rgba(...) / transparent / 强调色按钮与视频黑台
    属刻意设计，不在此列。
    """
    import re
    from PyQt6.QtWidgets import QWidget
    pat = re.compile(r"background:\s*#[0-9A-Fa-f]{6}")
    print(f"\n=== {label}：仍为不透明实色底的控件 ===")
    seen = 0
    for root in roots:
        if root is None:
            continue
        for w in root.findChildren(QWidget):
            try:
                qss = w.styleSheet() or ""
            except Exception:
                continue
            m = None
            for cand in pat.finditer(qss):
                # 跳过 :hover / :selected 这类状态规则里的实色（刻意保留的交互反馈）
                head = qss[max(0, cand.start() - 34):cand.start()]
                if any(k in head for k in (":hover", ":selected", ":checked",
                                           ":pressed", ":focus")):
                    continue
                m = cand
                break
            if m is None:
                continue
            seen += 1
            if seen > 40:
                continue
            frag = qss[:132].replace("\n", " ")
            txt = ""
            try:
                txt = (w.text() if hasattr(w, "text") else "")[:20]
            except Exception:
                pass
            print(f"  {_page_of(dlg, w) if dlg is not None else '':<5} "
                  f"{type(w).__name__:<16} {frag}  {txt!r}")
    print(f"  合计 {seen} 处")


def main():
    real = None
    from zhuzhu_Copilot import app_identity
    real = app_identity.data_root()
    home = Path(tempfile.mkdtemp(prefix="gr_"))
    if real.exists():
        shutil.copytree(real, home / real.name,
                        ignore=shutil.ignore_patterns("logs", "*.log", "cache"))
    app_identity._home = lambda: home
    app_identity._migrated = True
    app = QApplication.instance() or QApplication([])

    from zhuzhu_Copilot.core import app_glass
    from zhuzhu_Copilot.ui import agent_panel as ap
    from zhuzhu_Copilot.ui.agent_panel import AgentPanel

    AgentPanel._maybe_show_onboarding = lambda self: None
    from zhuzhu_Copilot.ui import main_window as mw
    mw.CopilotPanel._start_scan = lambda self, *a: None
    mw.CopilotPanel._init_update_check = lambda self: None
    mw.CopilotPanel._check_admin = lambda self: None
    mw.CopilotPanel._setup_tray = lambda self: None

    before = app_glass.params()
    app_glass.set_fields(persist=False, enabled=False)   # 从「玻璃关」开始

    panel = AgentPanel(None)
    panel.resize(1000, 900)
    panel.show()
    _pump(app, 1.0)
    dlg = ap._AgentSettingsDialog(parent=panel)
    dlg.resize(991, 687)
    dlg.show()
    _pump(app, 0.6)

    _probe("玻璃关闭", panel, dlg)

    app_glass.set_fields(persist=False, enabled=True)
    _pump(app, 1.2)          # 覆盖主面板 120ms 防抖
    n_on = _probe("切到玻璃开启", panel, dlg)

    app_glass.set_fields(persist=False, enabled=False)
    _pump(app, 1.2)
    n_off = _probe("再切回玻璃关闭", panel, dlg)

    print(f"\n结论：开玻璃后玻璃态控件 {n_on}/6，关玻璃后 {n_off}/6（期望 6 / 0）")

    app_glass.set_fields(persist=False, enabled=True)   # 扫描必须在玻璃开启态
    _pump(app, 1.0)
    dlg._ensure_all_pages()
    _pump(app, 0.6)
    _scan_solid("设置页（14 页全建）", [dlg], dlg=dlg)
    _scan_solid("四个浮窗", [panel.wt_win, panel.git_win,
                            panel.todos_win, panel.code_win])

    app_glass.set_params(before, persist=False)

    try:
        dlg.done(0)
        panel.close()
    except Exception:
        pass


if __name__ == "__main__":
    main()
