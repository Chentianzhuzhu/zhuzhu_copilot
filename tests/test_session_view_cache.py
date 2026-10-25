"""会话视图缓存回归：新开对话不得销毁上一对话已渲染的聊天气泡。

用户实测（P0）：在当前对话里点「新对话」，再切回刚才那个历史对话，
**所有聊天气泡消失**，只剩空白消息区。

根因链路：
  `_new_session()` 先 `_bind_sess(新会话)`（只切数据、不切视图），随后
  `while self.msg_lay.count() > 1: takeAt(0) + _free_layout_item(item)`
  —— 此时 `self.msg_lay` 仍是**上一个会话**的布局，于是把它的气泡控件全销毁；
  而那个会话的视图缓存里 `rows_n / rows_ref / last_row` 仍记着「已渲染到底」，
  切回去时 `_view_reusable()` 判定可复用 → 挂上一个空容器 → 气泡凭空消失。

修法：
  ① `_new_session` 改为给新会话**换挂一块自己的空视图**（takeWidget/setWidget
     只转移所有权，旧控件树原封不动）；
  ② `_view_reusable` 增加控件树完整性护栏：容器里的控件数少于记账行数就判不可复用，
     作为任何「偷偷清过容器」路径的兜底。
"""
import inspect
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from PyQt6.QtWidgets import QApplication, QWidget

# QApplication 必须保留模块级引用：无引用会被立即销毁，后续建 QWidget 直接崩溃
_APP = QApplication.instance() or QApplication([])

from zhuzhu_Copilot.ui import agent_panel as ap


def _make_view(n_rows: int) -> dict:
    """造一个「已渲染 n_rows 条气泡」的视图（末尾保留 stretch）。"""
    view = ap.AgentPanel._new_stream_view(None)
    lay = view["lay"]
    for _ in range(n_rows):
        lay.insertWidget(lay.count() - 1, QWidget())
    return view


def _register_rows(view: dict, n: int) -> dict:
    """按 `_commit_sess` 的记账方式登记视图（rows_n / rows_ref / last_row / theme）。"""
    rows = [{"type": "user", "text": f"第 {i} 条"} for i in range(n)]
    view.update({"rows_ref": rows, "rows_n": n,
                 "last_row": rows[-1] if rows else None,
                 "theme": ap._THEME_VERSION})
    return {"rows": rows}


def test_view_reusable_accepts_fully_rendered_view():
    view = _make_view(3)
    st = _register_rows(view, 3)
    assert ap.AgentPanel._view_reusable(None, view, st) is True


def test_view_reusable_rejects_widget_cleared_container():
    """容器被别的路径清空后，绝不能当成可复用视图挂回去（这就是气泡消失的直接原因）。"""
    view = _make_view(3)
    st = _register_rows(view, 3)
    assert ap.AgentPanel._view_reusable(None, view, st) is True
    while view["lay"].count() > 1:            # 模拟旧 _new_session 的清空动作
        item = view["lay"].takeAt(0)
        w = item.widget()
        if w is not None:
            w.setParent(None)
            w.deleteLater()
    assert view["lay"].count() == 1
    assert ap.AgentPanel._view_reusable(None, view, st) is False, \
        "控件被清空的容器仍被判为可复用 —— 历史气泡会消失"


def test_view_reusable_rejects_partially_cleared_container():
    view = _make_view(5)
    st = _register_rows(view, 5)
    for _ in range(3):                        # 只清掉一部分
        item = view["lay"].takeAt(0)
        w = item.widget()
        if w is not None:
            w.setParent(None)
    assert ap.AgentPanel._view_reusable(None, view, st) is False


def test_new_session_attaches_fresh_view_instead_of_wiping_previous():
    """源码护栏：`_new_session` 不得再清 `msg_lay`（那是上一会话的容器）。"""
    src = inspect.getsource(ap.AgentPanel._new_session)
    assert "while self.msg_lay.count() > 1" not in src, \
        "新开对话仍在清空 msg_lay —— 会销毁上一会话已渲染的气泡"
    assert "_new_stream_view()" in src, "新对话必须建自己的消息流视图"
    assert "_attach_view(view)" in src, "新对话必须把新视图换挂上去"
    assert "_remember_view(" in src, "新视图须登记进视图缓存（LRU 记账）"


def test_switch_to_still_creates_view_for_first_visit():
    """切换会话的首次进入分支也不能退化成「清空当前容器」。"""
    src = inspect.getsource(ap.AgentPanel._switch_to)
    assert "_new_stream_view()" in src
    assert "_attach_view(view)" in src
