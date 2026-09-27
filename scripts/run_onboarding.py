# -*- coding: utf-8 -*-
"""单独运行「新手指南」弹窗（OnboardingWizard），供离线体验/评审。

用途：不启动整个 AI 面板，只弹这一个向导，方便评估文案、分页、配色与交互。

运行：
    python scripts/run_onboarding.py            # 用当前设置的主题
    python scripts/run_onboarding.py dark       # 深色主题（临时，退出还原）
    python scripts/run_onboarding.py light      # 浅色主题（临时，退出还原）

无副作用保证：
- agent_theme 若要临时切换，运行结束（窗口关闭）时**还原为原值**，不留痕；
- 不写 agent_first_run_done 标记（避免影响应用后续真正的首次启动引导）；
- 向导内点击「完成」才会按其自身逻辑写入偏好（与应用内行为一致，属用户主动选择）。
"""
import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

os.environ.setdefault("QT_MEDIA_BACKEND", "ffmpeg")

from PyQt6.QtCore import Qt, QSettings  # noqa: E402
from PyQt6.QtGui import QFont, QFontDatabase, QIcon  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from winapp_migrator.ui.main_window import _app_icon_path  # noqa: E402
from winapp_migrator.ui.onboarding import OnboardingWizard  # noqa: E402


def main() -> int:
    args = [a.lower().lstrip("-") for a in sys.argv[1:]]
    want = next((a for a in args if a in ("dark", "light")), "")

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("zhuzhu Copilot")
    app.setWindowIcon(QIcon(_app_icon_path()))
    fams = QFontDatabase.families()
    fam = next((f for f in ("Microsoft YaHei UI", "Segoe UI", "PingFang SC") if f in fams), "Arial")
    app.setFont(QFont(fam, 10))

    # 主题：临时写入以驱动模块级颜色常量绑定，退出时还原（无持久副作用）
    q = QSettings("WinAppMigrator", "WinAppMigrator")
    original = q.value("agent_theme", None)
    if want:
        q.setValue("agent_theme", want)
    try:
        # 先按主题刷新内存色板，再导入 agent_panel（其模块级颜色常量在导入时绑定）
        from winapp_migrator.ui import styles as _styles
        theme = str(q.value("agent_theme", "light") or "light")
        if theme == "auto":
            import datetime
            theme = "light" if 8 <= datetime.datetime.now().hour < 20 else "dark"
        _styles.set_palette(theme)
        _styles.apply_palette(app)
        from winapp_migrator.ui import agent_panel as _ap
        try:
            if _ap._APPLIED_THEME != theme:
                _ap.apply_theme()
        except Exception:
            pass

        w = OnboardingWizard(None)
        w.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)  # 便于在其它窗口之上评估
        w.show()
        w.raise_()
        w.activateWindow()
        code = app.exec()
    finally:
        if want:                       # 还原主题设置
            if original is None:
                q.remove("agent_theme")
            else:
                q.setValue("agent_theme", original)
        q.sync()
    print("[onboarding] 已关闭（主题设置已还原）")
    return code


if __name__ == "__main__":
    sys.exit(main())
