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
def _isolated_data_root(tmp_path_factory):
    """把用户数据目录（~/.zhuzhu_Copilot）整体重定向到临时目录（全局，集中一处）。

    背景（真实缺陷，用户可见）：`test_ai_turn_wrapper.py` 的 panel fixture 直接构造
    **真实 AgentPanel**，而面板构造时会读 last_session.txt 恢复「上次停靠的会话」=
    用户的真实会话。用例随后置 `_task_active = True` 并灌入 `_on_reasoning(CHUNK)`×24，
    面板 4 秒自动落盘（`_autosave_flush` 只在 task_active 时写盘）就把这段**测试夹具
    文本写进了用户的真实会话文件**，还把它记成 last_session —— 于是用户每次重启应用
    都会看到一段假「思考过程」。

    实测证据：`.zhuzhu_Copilot/agent/sessions/d789d9473a00.ui.json` 里被写入
    `"用户要求扫描全盘部署包并生成迁移清单，先枚举安装产物再按指纹过滤；" × 24`
    （= tests 里的 `CHUNK`），而同一会话的引擎记录里根本没有这段文字。

    做法：把 `app_identity._home()`（代码里已声明为「测试可替换的接缝」）指向临时家目录，
    并把迁移标记置为已完成，避免用例反过来去动真实目录 / 真实注册表。用例若自己要验证
    迁移流程，仍可像 test_app_identity 那样在用例内覆盖这两个接缝。
    """
    from zhuzhu_Copilot import app_identity

    mp = pytest.MonkeyPatch()
    home = tmp_path_factory.mktemp("home")
    (home / app_identity.DATA_DIR_NAME).mkdir(parents=True, exist_ok=True)
    mp.setattr(app_identity, "_home", lambda: home)
    mp.setattr(app_identity, "_migrated", True)
    yield home
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


@pytest.fixture(autouse=True)
def _reset_wallpaper_between_tests():
    """用例之间复位壁纸状态（函数级，收尾执行）。

    背景（真实耦合）：设了可用壁纸后，主界面容器底色会整体改 transparent
    （模块级色板常量被覆写，见 agent_panel._apply_surface_mode）。色板是**进程级**
    的，一个用例设了壁纸没清干净，后续用例就会在透明底上断言实色而假失败 ——
    这类失败与被测逻辑无关，排查成本极高。
    """
    yield
    try:
        from zhuzhu_Copilot.core import app_wallpaper
        app_wallpaper.set_params(app_wallpaper.WallpaperParams(), persist=False)
        app_wallpaper.invalidate()
    except Exception:
        return
    try:
        from zhuzhu_Copilot.ui import agent_panel as _ap
        if _ap._SURFACE_TRANSPARENT:
            _ap.refresh_surface_mode()
    except Exception:
        pass
