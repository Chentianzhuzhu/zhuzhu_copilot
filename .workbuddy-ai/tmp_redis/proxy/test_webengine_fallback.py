"""WebEngine 缺失降级验证：屏蔽 PyQt6.QtWebEngine* 导入后，相关入口应安全降级。"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
SRC = r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\src"
sys.path.insert(0, SRC)

import importlib.abc  # noqa: E402


class _BlockWebEngine(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith("PyQt6.QtWebEngine"):
            raise ImportError(f"blocked for test: {fullname}")
        return None


sys.meta_path.insert(0, _BlockWebEngine())

from PyQt6.QtWidgets import QApplication  # noqa: E402
app = QApplication([])

from zhuzhu_Copilot.core import agent_ui_ux  # noqa: E402

assert agent_ui_ux.web_engine_available() is False, "should report unavailable"
assert agent_ui_ux.new_web_view() is None, "should return None"
panel = agent_ui_ux._PreviewPanel(kind="web")
assert panel._web is None, "web view must degrade to None"

from zhuzhu_Copilot.ui import slideshow  # noqa: E402

win = slideshow.make_slideshow(None, "<html><body>x</body></html>", "", "")
assert win is not None, "slideshow window should still be created with hint"

print("FALLBACK OK: web_engine_available=False, new_web_view=None, slideshow degraded")
