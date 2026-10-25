"""Copilot 插件热插拔：启用即挂上、停用即摘掉（页签 / 玻璃窗口 / 托盘 / 快捷键）。"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PyQt6.QtWidgets import (QApplication, QButtonGroup, QHBoxLayout,  # noqa: E402
                             QPushButton, QStackedWidget, QWidget)


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    yield a


class _FakePanel(QWidget):
    """够用的面板替身：只有页面栈、页签按钮组与扩展登记表。"""

    def __init__(self):
        super().__init__()
        from PyQt6.QtWidgets import QVBoxLayout
        lay = QVBoxLayout(self)
        self._tab_wrap = QWidget()
        tlay = QHBoxLayout(self._tab_wrap)
        tlay.addStretch(1)                      # 右侧预留位（真实面板同款）
        lay.addWidget(self._tab_wrap)
        self._tab_group = QButtonGroup(self)
        self._tab_group.setExclusive(True)
        self._pages = QStackedWidget()
        lay.addWidget(self._pages)
        self._copilot_ext = {"pages": [], "services": [], "tray": [], "shortcuts": []}
        self.tray = None


def _make_plugin(tmp: Path, name: str, page_mode: str = "tab") -> Path:
    d = tmp / name
    d.mkdir(parents=True)
    (d / "plugin.json").write_text(json.dumps(
        {"name": name, "kind": "copilot", "enabled": True, "description": "t"}),
        encoding="utf-8")
    (d / "pages").mkdir()
    (d / "pages" / "p.py").write_text(
        "from PyQt6.QtWidgets import QWidget, QLabel, QVBoxLayout\n\n\n"
        "class DemoPage(QWidget):\n"
        "    def __init__(self, parent=None):\n"
        "        super().__init__(parent)\n"
        "        lay = QVBoxLayout(self)\n"
        "        lay.addWidget(QLabel('demo'))\n",
        encoding="utf-8")
    page = {"id": f"{name}-page", "title": "演示页",
            "module": "pages/p.py", "widget": "DemoPage"}
    if page_mode == "glass":
        page.update({"window": "glass", "auto_show": True,
                     "width": 400, "height": 320})
    (d / "copilot.json").write_text(json.dumps({"pages": [page]}), encoding="utf-8")
    return d


def _buttons(panel) -> list:
    return [b.text() for b in panel._tab_wrap.findChildren(QPushButton)]


def test_tab_page_hotplug(app, tmp_path):
    """页签页：登记 → 有页签；反登记 → 页签与页面都消失。"""
    from zhuzhu_Copilot.ui import main_window as mw

    panel = _FakePanel()
    d = _make_plugin(tmp_path, "hot-tab")
    before = panel._pages.count()
    mw.register_copilot_entry(panel, "hot-tab", d, "pages",
                              {"id": "hot-tab-page", "title": "演示页",
                               "module": "pages/p.py", "widget": "DemoPage"})
    assert panel._pages.count() == before + 1
    assert "演示页" in _buttons(panel)
    assert any(r.get("plugin") == "hot-tab" for r in panel._copilot_ext["pages"])

    mw.unregister_copilot_plugin("hot-tab")
    assert panel._pages.count() == before, "停用后页面必须从栈里移除"
    assert "演示页" not in _buttons(panel), "停用后页签必须消失"
    assert not panel._copilot_ext["pages"]


def test_glass_page_hotplug(app, tmp_path):
    """玻璃窗口页：登记 → 出现独立窗口；反登记 → 窗口关闭且摘除。"""
    from zhuzhu_Copilot.ui import main_window as mw

    panel = _FakePanel()
    d = _make_plugin(tmp_path, "hot-glass", "glass")
    mw.register_copilot_entry(panel, "hot-glass", d, "pages",
                              {"id": "hot-glass-page", "title": "玻璃页",
                               "module": "pages/p.py", "widget": "DemoPage",
                               "window": "glass", "auto_show": False,
                               "width": 400, "height": 320})
    rec = next(r for r in panel._copilot_ext["pages"]
               if r.get("plugin") == "hot-glass")
    win = rec.get("window")
    assert win is not None, "glass 页面必须建独立窗口"
    assert panel._pages.count() == 0, "glass 页面不应进浮层页签"
    assert win.width() == 400 and win.height() == 320
    win.show()
    app.processEvents()
    assert win.isVisible()

    mw.unregister_copilot_plugin("hot-glass")
    assert not panel._copilot_ext["pages"]
    assert not win.isVisible(), "停用后玻璃窗口必须关闭"


def test_register_is_idempotent(app, tmp_path):
    """重复登记同一条目不重复加页签（热插拔多次调用安全）。"""
    from zhuzhu_Copilot.ui import main_window as mw

    panel = _FakePanel()
    d = _make_plugin(tmp_path, "hot-dup")
    entry = {"id": "hot-dup-page", "title": "演示页",
             "module": "pages/p.py", "widget": "DemoPage"}
    mw.register_copilot_entry(panel, "hot-dup", d, "pages", entry)
    mw.register_copilot_entry(panel, "hot-dup", d, "pages", entry)
    assert panel._pages.count() == 1
    mw.unregister_copilot_plugin("hot-dup")
    assert panel._pages.count() == 0


def test_manifest_accepts_glass_window_fields():
    """清单校验接受 window/auto_show/width/height，拒绝非法值。"""
    from zhuzhu_Copilot.core import copilot_plugins as cp

    ok, _ = cp.validate_copilot_manifest({"pages": [{
        "id": "p1", "title": "t", "module": "pages/p.py", "widget": "W",
        "window": "glass", "auto_show": True, "width": 640, "height": 480}]})
    assert ok
    bad, why = cp.validate_copilot_manifest({"pages": [{
        "id": "p2", "title": "t", "module": "pages/p.py", "widget": "W",
        "window": "floating"}]})
    assert not bad and "window" in why
    bad2, _ = cp.validate_copilot_manifest({"pages": [{
        "id": "p3", "title": "t", "module": "pages/p.py", "widget": "W",
        "width": 10}]})
    assert not bad2
