"""后台任务面板（左下角「后台任务」列表）行为回归。

需求基线（用户原话）：
    「最大显示 5 个后台任务，超出则新任务自动排列到顶部，旧任务自动移动到下方，
      然后添加滚动条，用户可以滑动查看所有任务情况。」
    「注意：确保大型命令实时监听输出，确保用户能获得最新任务进度。」

覆盖点：
1. 新任务置顶、旧任务下移；超过 5 个出现纵向滚动条（高度=前 5 张之和）；
2. ≤5 个任务时不出现滚动条；
3. 用户停在中段查看旧任务时，新增任务不打扰（只在顶部时跟随）；
4. 流式输出：卡片缺失时兜底补建 → 任何增量都不丢；
5. 折叠态「最新一行」摘要 + tooltip 全文 + 按宽度省略；
6. 展开态渲染正文、贴底跟随、上翻读历史不被拽回；
7. 运行中秒表按真实耗时推进，不被流式增量重置为 0；
8. 迟到的零头输出不得把已完成/取消的任务"翻活"回运行中。

环境说明：offscreen 平台下 app.exec() 会让顶层窗口被判为不可见、布局随之失效，
因此本用例只用 processEvents 驱动事件（与 conftest 里的既有 UI 用例一致）。
"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

import pytest
from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout

from zhuzhu_Copilot.ui.agent_panel import _BackgroundTaskPanel
from zhuzhu_Copilot.core import agent_background as ab


@pytest.fixture(scope="module")
def app():
    qapp = QApplication.instance() or QApplication([])
    yield qapp


# 每个用例新建一个宿主窗口：必须持有引用，否则 Python GC 一回收，
# 窗口及其布局的 C++ 对象就被 deleteLater，后续操作全部报
# "wrapped C/C++ object of type QVBoxLayout has been deleted"。
_KEEPALIVE = []


@pytest.fixture()
def panel(app):
    """与 AgentPanel._build_ui 中 right.addWidget(self.bg_task_panel) 同构：
    面板放进可伸缩的右侧列（上方 stretch=对话流，面板贴输入行）。"""
    win = QWidget()
    win.resize(420, 900)
    lay = QVBoxLayout(win)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    lay.addStretch(1)
    p = _BackgroundTaskPanel(win)
    lay.addWidget(p)
    win.show()
    _KEEPALIVE.append(win)
    return p


def flush(app, n=6):
    for _ in range(n):
        app.processEvents()


def add(panel, app, tid, title="任务"):
    panel.add_task(tid, title)
    flush(app)


# ---------------------------------------------------------------- 排序 / 滚动
def test_new_task_moves_to_top_and_panel_scrolls(panel, app):
    for i in range(8):
        add(panel, app, "bg_%03d" % i, "任务%d" % i)

    assert panel._order[0] == "bg_007" and panel._order[-1] == "bg_000"
    assert len(panel._widgets) == 8

    sb = panel.scroll.verticalScrollBar()
    assert sb.maximum() > 0, "超过 5 个任务却没有滚动条"
    assert panel.scroll.widget().height() >= 8 * 51, "内容被压扁（minHeight 未生效）"

    # 一屏最多 5 张：高度≈前 5 张之和
    card_h = [panel._widgets[t].sizeHint().height() for t in panel._order]
    top5 = sum(card_h[:5]) + 4 * 4
    assert abs(panel.scroll.height() - top5) <= 8
    visible = int((panel.scroll.height() + 8) // (card_h[0] + 4))
    assert visible <= 5, "一屏完整可见卡片超过 5 张：%d" % visible
    # 高度硬上限：窗口一半，展开大输出也不顶爆输入区
    assert panel.height() <= 460


def test_five_or_fewer_tasks_no_scrollbar(panel, app):
    for i in range(5):
        add(panel, app, "ok_%d" % i, "任务%d" % i)
    assert panel.scroll.verticalScrollBar().maximum() == 0


def test_scroll_position_only_follows_when_at_top(panel, app):
    for i in range(8):
        add(panel, app, "s_%03d" % i, "任务%d" % i)
    sb = panel.scroll.verticalScrollBar()
    mid = int(sb.maximum() / 2)
    sb.setValue(mid)
    flush(app)

    panel.add_task("s_008", "新任务")          # 用户停在中段
    flush(app)
    assert sb.value() == mid, "停在中段查看旧任务时被新增项拽走了"
    assert panel._order[0] == "s_008", "新任务没排到顶部"

    sb.setValue(0)                             # 回到顶部
    flush(app)
    panel.add_task("s_009", "更新的任务")
    flush(app)
    assert sb.value() <= 20, "停在顶部时新增任务没有跟随到顶部"


def test_empty_state_hides_panel(panel, app):
    for i in range(3):
        add(panel, app, "e_%d" % i)
        panel.remove_task("e_%d" % i, 0)
    flush(app, 10)
    assert not panel._widgets
    assert panel.empty_label.isVisible()
    # 清空后面板还要再停留 300ms 才 hide（让用户看到最后一个任务的收尾状态）
    t0 = time.time()
    while panel.isVisible() and time.time() - t0 < 1.0:
        app.processEvents()
        time.sleep(0.03)
    assert not panel.isVisible()


# ---------------------------------------------------------------- 实时输出
def test_streaming_output_creates_card_when_missing(panel, app):
    """"后台任务已启动"状态通知若漏匹配，输出也不能整段丢失。"""
    ab.BackgroundTaskManager.reset_instance()
    t = ab.bg_manager().submit(task_type=ab.TYPE_COMMAND, title="实时命令任务",
                               fn=lambda task: time.sleep(1.5))
    panel.append_output(t.task_id, "building...\ndownloading 3/5\n")
    flush(app)

    w = panel._widgets[t.task_id]
    assert w._last_line == "downloading 3/5"
    assert w.preview_label.toolTip() == "downloading 3/5"
    assert len(w.preview_label.text()) <= len("downloading 3/5") + 2, "摘要行撑宽卡片"
    assert not w.output_area.isVisible(), "折叠态不该渲染正文"


def test_expanded_follows_tail_and_respects_scroll_up(panel, app):
    ab.BackgroundTaskManager.reset_instance()
    t = ab.bg_manager().submit(task_type=ab.TYPE_COMMAND, title="展开任务",
                               fn=lambda task: time.sleep(1.5))
    panel.append_output(t.task_id, "building...\ndownloading 3/5\n")
    flush(app)
    w = panel._widgets[t.task_id]

    w._toggle_expand()
    flush(app)
    assert "downloading 3/5" in w.output_area.toPlainText()
    assert not w.preview_label.isVisible(), "展开后摘要行应让位"

    w.append_output("tail line after expand\n")
    for _ in range(25):
        app.processEvents()
        time.sleep(0.02)
    assert "tail line after expand" in w.output_area.toPlainText()

    w.output_area.verticalScrollBar().setValue(0)     # 用户上翻读历史
    flush(app)
    w.append_output("second tail\n")
    for _ in range(25):
        app.processEvents()
        time.sleep(0.02)
    assert w.output_area.verticalScrollBar().value() == 0, "上翻时被拽回底部"
    assert "second tail" in w.output_area.toPlainText()


def test_elapsed_seconds_never_reset_by_streaming(panel, app):
    ab.BackgroundTaskManager.reset_instance()
    t = ab.bg_manager().submit(task_type=ab.TYPE_COMMAND, title="秒表任务",
                               fn=lambda task: time.sleep(2.2))
    panel.add_task(t.task_id, "秒表任务")
    flush(app)
    before = panel._widgets[t.task_id].status_label.text()
    panel.append_output(t.task_id, "chunk-of-output\n")   # 旧实现会把 duration=0 传回去
    flush(app)
    assert "（0s）" not in panel._widgets[t.task_id].status_label.text() or \
        before == panel._widgets[t.task_id].status_label.text()

    t0 = time.time()
    while time.time() - t0 < 1.4:
        app.processEvents()
        time.sleep(0.03)
    txt = panel._widgets[t.task_id].status_label.text()
    assert "(1s)" in txt or "(2s)" in txt, txt


def test_late_output_does_not_revive_finished_task(panel, app):
    ab.BackgroundTaskManager.reset_instance()
    t = ab.bg_manager().submit(task_type=ab.TYPE_COMMAND, title="已完成",
                               fn=lambda task: "x")
    panel.add_task(t.task_id, "已完成")
    ab.bg_manager().get(t.task_id)._mark_done("x")
    panel.update_task(t.task_id, "done", 1.5)
    done_txt = panel._widgets[t.task_id].status_label.text()

    panel.append_output(t.task_id, "迟到的零头输出\n")     # 改版前会把状态设回 running
    flush(app)
    assert panel._widgets[t.task_id].status_label.text() == done_txt
