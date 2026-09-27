"""AgentPanel.eventFilter 骨架期守卫回归。

真实缺陷（exe 崩溃日志）：
    AttributeError: 'AgentPanel' object has no attribute 'input'
    agent_panel.py eventFilter 内 `obj is self.input`

成因：build_default_ui 在输入框创建之前就对顶栏 status_btn 调用 installEventFilter(self)。
骨架期该按钮收到的 StyleChange / ToolTip 等事件会进入 eventFilter 并解引用 self.input，
而 self.input 更晚才 new → AttributeError；PyQt6 下未捕获异常直接弹崩溃框。

修法：eventFilter 入口用 getattr 取输入框引用，未就绪一律放行（不依赖初始化顺序）。

用例以绑定方式直接调用真实方法，绕过 Qt 虚函数派发：这样回归时抛出的 AttributeError
能被 pytest 捕获并判为失败，而不是触发 PyQt6 的 qFatal/崩溃框。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication, QDialog, QListWidget, QPlainTextEdit, QWidget

_app = QApplication.instance() or QApplication([])

from winapp_migrator.ui import agent_panel as ap


class _SkeletonPanel(ap.AgentPanel):
    """只搭 QDialog 壳、跳过真实 __init__：精确复现「过滤器已装、input 未建」的时刻。"""

    def __init__(self):
        QDialog.__init__(self)


def test_skeleton_events_do_not_access_missing_input():
    """骨架期（无 input 属性）任意控件事件都必须安全放行，不得 AttributeError。

    历史触发点（已按需求移除）：顶栏「状态」info 按钮在 build_default_ui 内即装上本
    事件过滤器，而 self.input 更晚才 new —— 期间它收到 StyleChange 就会抛
    AttributeError（PyQt6 未捕获异常直接弹崩溃框）。守卫保留以覆盖任何同类新增控件。
    """
    panel = _SkeletonPanel()
    for ev in (QEvent(QEvent.Type.StyleChange),
               QEvent(QEvent.Type.ToolTip),
               QEvent(QEvent.Type.Enter)):
        assert panel.eventFilter(QWidget(), ev) is False


def test_status_button_and_its_tooltip_helper_are_removed():
    """回归：顶栏「状态」按钮已删除，其 tokens/工作流/模型信息并入上下文统计浮层。

    若有人重新引入该按钮（或复活只服务于它的 tooltip 刷新方法），本用例立即失败——
    相关代码只在 agent_panel.py 内，做源码级断言即可防止功能回潮。"""
    import inspect
    src = inspect.getsource(ap.AgentPanel)
    assert "status_btn" not in src
    assert "_refresh_status_tooltip" not in src
    assert "_is_status_btn" not in src
    for api in ("_token_stats_ui_context", "_token_stats", "open_token_stats"):
        assert f"def {api}" in src, f"信息合并入口缺失：{api}"


def test_normal_events_still_pass_through_after_init():
    """输入框就绪后，非目标控件的普通事件仍按 Qt 默认语义返回 False（未被守卫吞掉）。"""
    panel = _SkeletonPanel()
    panel.input = QPlainTextEdit()
    panel.cmd_list = QListWidget()
    assert panel.eventFilter(QWidget(), QEvent(QEvent.Type.StyleChange)) is False


def test_tab_completion_branch_reachable_when_ready():
    """守卫不得误伤正常分支：输入框 + 候选列表可见时 Tab 仍应触发命令补全。"""
    panel = _SkeletonPanel()
    panel.input = QPlainTextEdit()
    panel.cmd_list = QListWidget()
    panel.cmd_list.show()
    hit = []
    panel._complete_cmd = lambda: hit.append(True) or True
    ev = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Tab, Qt.KeyboardModifier.NoModifier)
    assert panel.eventFilter(panel.input, ev) is True
    assert hit, "Tab 补全命令分支不可达（守卫把正常路径一并拦截了）"
