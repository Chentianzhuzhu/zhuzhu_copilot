"""主题切换探针：跑一遍深↔浅切换，复核用户反馈的三处。

  1. 文件树（工作树侧栏）内容：重建出的窗口是空壳，`_sync_wt_win` 只在「原本不可见」
     时刷新 → 切主题后 `treeItems` 会变 0（应为 1）；
  2. 对话气泡宽度：重建瞬间 `msg_area` 视口未就绪，`_ai_turn_max_width()` 回退 240px
     被 `setMaximumWidth` 钉死 → 内容挤在中间（重排后应跟随视口）；
  3. 残留色：对面板取像素 + 保存截图到 `_retheme_shot/`，比对各区域是否随主题翻转
     （侧栏滚动条曾因控件自带样式表而退回系统默认，浅色下残留深灰）。

用法：python scripts/_probe_retheme.py [--sid xxxx]
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

from zhuzhu_Copilot import app_identity                                     # noqa: E402


def _pump(app, seconds):
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.001)


def _snap(panel, app, ap, label):
    from zhuzhu_Copilot.ui import agent_chat_bubbles as cb
    print(f"\n[{label}] 主题={ap._resolve_theme()} 面板宽={panel.width()} "
          f"视口宽={panel.msg_area.viewport().width()}")
    for i, b in enumerate(panel._bubble_widgets[:6]):
        try:
            print(f"  气泡#{i} align={b.property('align')} maxW={b.maximumWidth()} "
                  f"w={b.width()} h={b.height()}")
        except Exception:
            pass
    for name in ("wt_win", "git_win", "todos_win", "code_win"):
        w = getattr(panel, name, None)
        if w is None:
            print(f"  {name}=None")
            continue
        tree = getattr(w, "tree", None)
        extra = ""
        if tree is not None:
            extra = (f" treeItems={tree.topLevelItemCount()} treeH={tree.height()}"
                     f" vp={tree.viewport().height()} vis={tree.isVisible()}")
        print(f"  {name} visible={w.isVisible()} geom={w.geometry().getRect()}{extra}")
    img = panel.grab().toImage()
    w, h = panel.width(), panel.height()
    pts = [(6, 6, "面板左上"), (w // 2, 8, "顶部中"), (w // 2, h // 2, "中心"),
           (w // 2, h - 6, "底部中"), (12, h // 2, "左侧中")]
    for x, y, nm in pts:
        if 0 <= x < img.width() and 0 <= y < img.height():
            print(f"  像素[{nm}]({x},{y})={img.pixelColor(x, y).name()}")
    try:
        out = Path(__file__).resolve().parents[1] / "_retheme_shot"
        out.mkdir(exist_ok=True)
        img.save(str(out / f"{label}.png"))
    except Exception:
        pass


def main():
    real = app_identity.data_root()
    home = Path(tempfile.mkdtemp(prefix="ret_"))
    if real.exists():
        shutil.copytree(real, home / app_identity.DATA_DIR_NAME,
                        ignore=shutil.ignore_patterns("logs", "*.log", "cache"))
    app_identity._home = lambda: home
    app_identity._migrated = True
    app = QApplication.instance() or QApplication([])

    from zhuzhu_Copilot.ui import main_window as mw
    from zhuzhu_Copilot.ui import agent_panel as ap
    from zhuzhu_Copilot.ui.agent_panel import AgentPanel

    mw.CopilotPanel._start_scan = lambda self, *a: None
    mw.CopilotPanel._init_update_check = lambda self: None
    mw.CopilotPanel._check_admin = lambda self: None
    mw.CopilotPanel._setup_tray = lambda self: None

    panel = AgentPanel(None)
    panel.resize(1000, 900)
    panel.show()
    _pump(app, 0.8)

    sid = sys.argv[sys.argv.index("--sid") + 1] if "--sid" in sys.argv else "41fc94fb824f"
    # 强制「首次进入」：先离开目标会话再切回（对齐 scripts/_probe_switch_perf.py 的做法），
    # 等待条件用会话的 `loaded` 标志而非「有气泡」——后者依赖渲染时序，实测经常空等。
    panel._sess.pop(sid, None)
    if panel.__dict__.get("_session_id") == sid:
        other = next((s for s in (panel._sess or {}) if s != sid), "")
        if other:
            panel._switch_to(other)
            _pump(app, 0.5)
        else:
            panel._new_session()
            _pump(app, 0.5)
    panel._switch_to(sid)
    end = time.perf_counter() + 40
    while time.perf_counter() < end and not (panel._sess.get(sid) or {}).get("loaded"):
        app.processEvents()
        time.sleep(0.002)
    _pump(app, 2.0)
    print(f"会话={panel.__dict__.get('_session_id')} "
          f"loaded={(panel._sess.get(sid) or {}).get('loaded')} "
          f"气泡={len(panel._bubble_widgets)}")

    # 灌两条气泡进会话（写 _rows，主题切换重建后仍会恢复）——本诊断只关心
    # 「重建/重排后气泡宽度是否被钉在未就绪的视口宽度上」。
    _umsg = "用户消息：请观察切换主题后的气泡宽度是否正常。"
    _aseg = [{"type": "text", "raw": "AI 回复：这段正文用于观察气泡宽度是否被钉窄在中间。",
              "html": "AI 回复：这段正文用于观察气泡宽度是否被钉窄在中间。",
              "_shown": 9999, "_shown_f": 0.0}]
    panel._user_msgs.append(_umsg)
    panel._rows.append({"type": "user", "text": _umsg})
    panel._rows.append({"type": "ai", "segs": _aseg, "cost": 1.5,
                        "meta": "10:00:00 → 10:00:01"})
    panel._render_history_all()
    _pump(app, 1.5)
    print(f"造气泡后：气泡={len(panel._bubble_widgets)}")

    # 捕获「重建瞬间」的气泡宽度：这是判断气泡是否被按未就绪宽度钉死的唯一时机
    _orig_render = panel._render_history_all

    def _spy_render():
        _orig_render()
        ai = [b for b in panel._bubble_widgets if b.property("align") == "ai"]
        if ai:
            print(f"    [重建瞬间] 视口宽={panel.msg_area.viewport().width()} "
                  f"面板宽={panel.width()} 首个AI气泡 maxW={ai[0].maximumWidth()} "
                  f"w={ai[0].width()}")

    panel._render_history_all = _spy_render

    qs = app_identity.qsettings()
    _snap(panel, app, ap, "初始")

    for mode in ("light", "dark"):
        print(f"\n===== 切到 {mode} =====")
        qs.setValue("agent_theme", mode)
        try:
            panel._retheme()
        except Exception:
            import traceback
            traceback.print_exc()
        _pump(app, 2.0)
        _snap(panel, app, ap, f"切到 {mode} 后")

    try:
        panel.close()
    except Exception:
        pass


if __name__ == "__main__":
    main()
