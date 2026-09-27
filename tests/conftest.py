"""pytest 全局钩子：会话结束时先回收残留 Qt 控件，再让 QApplication 退出。

背景（真实缺陷）：部分用例直接 new 出无父级的 Qt 控件（如 QComboBox / 无边框悬浮窗口）
并在结束后不释放。所有用例都通过，但解释器退出时 QApplication 先于这些 C++ 对象析构 →
进程以 0xC0000005（访问冲突）/ 0xC0000409 崩溃退出，令 CI 判为失败（pytest 全绿但退出码非 0）。

修法：会话级清理（close + deleteLater + 处理事件），保证所有控件在 QApplication 销毁前
被回收。集中一处，避免在每个用例里重复 close/deleteLater。
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session", autouse=True)
def _no_onboarding_modal():
    """测试内一律屏蔽新手指南模态向导（全局，集中一处）。

    背景（真实缺陷）：AgentPanel 构造时 `QTimer.singleShot(650, self._maybe_show_onboarding)`，
    在首次运行状态（`onboarding.is_first_run()` 为真——本机即是）会弹出 OnboardingWizard
    并 `dlg.exec()`。离屏环境下无人可点击，**嵌套事件循环永不返回** → 整个 pytest 会话挂死：
    实测 test_copilot_popover_host.py / test_dock_layout.py 双双卡满超时，
    全量 `pytest tests/` 跑 30 分钟以上不结束（faulthandler 栈顶落在
    agent_panel.py `_open_onboarding` 的 `dlg.exec()`）。

    用例只验证面板自身行为，无任何用例断言引导流程 → 统一打桩为无操作。
    """
    mp = pytest.MonkeyPatch()
    try:
        from winapp_migrator.ui.agent_panel import AgentPanel
        mp.setattr(AgentPanel, "_maybe_show_onboarding", lambda self: None)
    except Exception:
        pass   # 无 PyQt6 / 非 UI 场景：不影响其余用例
    yield
    mp.undo()


@pytest.fixture(scope="session", autouse=True)
def _qt_widget_teardown():
    yield
    try:
        from PyQt6.QtCore import QEvent
        from PyQt6.QtWidgets import QApplication
    except ImportError:
        return
    app = QApplication.instance()
    if app is None:
        return
    for w in list(app.topLevelWidgets()) + list(app.allWidgets()):
        try:
            w.close()
            w.deleteLater()
        except RuntimeError:
            continue   # 底层 C++ 对象已被销毁，跳过（Qt 正常语义，非异常路径）
    for _ in range(3):
        app.processEvents()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
