"""长上下文对话「任何操作都卡」性能剖析（离屏，真实大会话）。

做法：加载本机最大真实会话 → 全量渲染 → 分别计时各类「用户操作」对应的代码路径，
定位随对话长度线性增长的热点。

用法：python scripts/_probe_longctx_ui.py [会话名，默认取最大 ui.json]
"""
from zhuzhu_Copilot import app_identity
import json
import os
import pathlib
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, "src")

from PyQt6.QtWidgets import QApplication

SESS = app_identity.data_root() / "agent" / "sessions"

app = QApplication([])
from zhuzhu_Copilot.core import agent_llm, agent_skills
from zhuzhu_Copilot.ui import agent_panel as ap

agent_skills.load_settings()
agent_llm.load_model_config()


def pick_session() -> pathlib.Path:
    if len(sys.argv) > 1:
        return SESS / f"{sys.argv[1]}.ui.json"
    return max(SESS.glob("*.ui.json"), key=lambda p: p.stat().st_size)


def pump(n=6):
    for _ in range(n):
        app.processEvents()


def t(label, fn, n=5, warm=1):
    for _ in range(warm):
        fn()
    pump(2)
    t0 = time.perf_counter()
    for _ in range(n):
        fn()
    dt = (time.perf_counter() - t0) / n
    print(f"  {label:38s} {dt * 1000:9.2f} ms")
    return dt


def main():
    f = pick_session()
    data = json.loads(f.read_text(encoding="utf-8"))
    rows = data.get("rows") or []
    segs = data.get("segments") or []
    ums = data.get("user_msgs") or []
    bubbles_est = sum(1 for r in rows if r.get("type") == "user") + sum(
        1 for r in rows if r.get("type") != "user")
    print(f"会话 {f.name}  文件 {f.stat().st_size / 1024:.0f}KB")
    print(f"  rows={len(rows)} user_msgs={len(ums)} segments={len(segs)}"
          f" 预估气泡≈{bubbles_est}")

    panel = ap.AgentPanel()
    panel.resize(1500, 950)
    panel.show()
    pump(12)

    panel._user_msgs = list(ums)
    panel._rows = [dict(r) for r in rows]
    panel._history_segments = [dict(s) for s in segs]
    panel._seg_cache.clear()
    t0 = time.perf_counter()
    panel._render_history_all()
    pump(8)
    print(f"\n全量渲染 {time.perf_counter() - t0:.2f}s  气泡={len(panel._bubble_widgets)}")

    print("\n各类操作的耗时（单次）：")
    t("_relayout_messages()", panel._relayout_messages, n=5)
    t("_sync_bubble_heights() 全量", panel._sync_bubble_heights, n=3)
    t("_scroll_bottom()", panel._scroll_bottom, n=5)
    t("_refresh_meta() 状态栏", panel._refresh_meta, n=5)
    t("_update_cmd_suggestions 输入候选", panel._update_cmd_suggestions, n=3)
    t("_sync_action_style 输入态", panel._sync_action_style, n=5)
    t("_commit_sess() 自动保存", panel._commit_sess, n=3)
    t("_persist_all_on_close 全量落盘", panel._persist_all_on_close, n=2)

    # 滚动一次（滚动条值变化 + 事件循环）
    bar = panel.msg_area.verticalScrollBar()

    def scroll_once():
        v = bar.value()
        bar.setValue(min(bar.maximum(), v + 200))
        app.processEvents()
        bar.setValue(v)
        app.processEvents()

    t("滚动一次（含事件循环）", scroll_once, n=5)

    # 打字：写入一个字 + 触发 debounce 目标
    def type_once():
        panel.input.setPlainText("测试输入一个字")
        app.processEvents()

    t("输入框写字（含 textChanged）", type_once, n=5)

    print("\n窗口缩放（触发全量高度重算）：")

    def resize_once():
        panel.resize(1500, 950 + 40)
        pump(4)
        panel.resize(1500, 950)
        pump(4)

    t("resize 一步（含 settle）", resize_once, n=3)
    panel.close()
    print("DONE")


if __name__ == "__main__":
    main()
