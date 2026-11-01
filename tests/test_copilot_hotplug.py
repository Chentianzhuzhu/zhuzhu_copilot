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


# ══════════════ 活类热修补：PyQt 信号（崩溃/失联回归） ══════════════

def test_live_class_patch_keeps_pyqt_signals(app):
    """热修补不得把新类的 pyqtSignal 描述符搬进旧类。

    回归：插件启停会原地重执行 agent_panel，旧实例随后访问自己的信号时报
    「_CardBoard does not have a signal with the signature PyQt_PyObject,...」。
    """
    import types
    from zhuzhu_Copilot.core import hot_reload as hr

    mod = types.ModuleType("zz_hp_sig_mod")
    src = (
        "from PyQt6.QtCore import QObject, pyqtSignal\n"
        "\n"
        "class Sig(QObject):\n"
        "    changed = pyqtSignal(object)\n"
        "\n"
        "    def fire(self, v):\n"
        "        self.changed.emit(v)\n"
    )
    exec(compile(src, "zz_hp_sig_mod", "exec"), mod.__dict__)
    obj = mod.Sig()
    got = []
    obj.changed.connect(got.append)

    old_ns = dict(mod.__dict__)
    exec(compile(src, "zz_hp_sig_mod", "exec"), mod.__dict__)
    patched = hr._patch_live_classes(mod, old_ns)
    assert "Sig" in patched
    obj.fire(7)             # 旧实例 + 新方法 + 旧类自己的信号
    assert got == [7]


# ══════════════ 定向热插拔：启停一个插件不得重启其他插件 ══════════════

def _read_trace(d: Path) -> list:
    f = d / "trace.log"
    return f.read_text(encoding="utf-8").splitlines() if f.is_file() else []


def _make_service_plugin(root: Path, name: str) -> Path:
    """带后台服务的插件：服务 start/stop 都写进插件目录下的 trace.log。"""
    d = root / name
    d.mkdir(parents=True)
    (d / "plugin.json").write_text(json.dumps(
        {"name": name, "kind": "copilot", "enabled": True, "description": "t"}),
        encoding="utf-8")
    (d / "svc").mkdir()
    (d / "svc" / "s.py").write_text(
        "from pathlib import Path\n"
        "_LOG = Path(__file__).resolve().parent.parent / 'trace.log'\n"
        "\n"
        "def _log(t):\n"
        "    with open(_LOG, 'a', encoding='utf-8') as f:\n"
        "        f.write(t + '\\n')\n"
        "\n"
        "class Handle:\n"
        "    def stop(self):\n"
        "        _log('stop')\n"
        "\n"
        "def start():\n"
        "    _log('start')\n"
        "    return Handle()\n",
        encoding="utf-8")
    (d / "copilot.json").write_text(json.dumps({"services": [{
        "id": f"{name}-svc", "title": "svc", "module": "svc/s.py",
        "entry": "start", "stop": "stop"}]}), encoding="utf-8")
    return d


def test_toggle_one_plugin_keeps_other_plugins_running(app, tmp_path, monkeypatch):
    """停用/启用 alpha 只影响 alpha：beta 的服务不得被 stop/start 一遍。"""
    from zhuzhu_Copilot.core import copilot_plugins
    from zhuzhu_Copilot.ui import main_window as mw

    root = tmp_path / "plugins"
    monkeypatch.setattr(copilot_plugins, "COPILOT_PLUGINS_DIR", root)
    state = {"started": set(), "services": [], "tray": [], "shortcuts": [],
             "host": None, "errors": {}}
    monkeypatch.setattr(mw, "_COPILOT_EXT_STATE", state)
    panel = _FakePanel()
    state["host"] = panel
    da = _make_service_plugin(root, "alpha")
    db = _make_service_plugin(root, "beta")
    try:
        mw.load_copilot_extensions(panel)
        assert _read_trace(da) == ["start"] and _read_trace(db) == ["start"]

        mw._hot_hook_after_change("alpha")
        assert _read_trace(da) == ["start", "stop"]
        assert _read_trace(db) == ["start"], "停用 alpha 不得重启其他插件的服务"

        mw._hot_hook_after_enable("alpha")
        assert _read_trace(da) == ["start", "stop", "start"]
        assert _read_trace(db) == ["start"], "启用 alpha 不得重启其他插件的服务"
    finally:
        mw.unload_extension_modules("alpha")
        mw.unload_extension_modules("beta")


def test_host_registered_with_zero_plugins_then_late_plugin_mounts(app, tmp_path,
                                                                  monkeypatch):
    """启动时一个启用插件都没有：host 也必须登记；之后下载/启用的页面立即挂上。

    回归：旧实现只在 register_copilot_entry 里登记宿主，零插件时 host 恒为 None，
    之后从市场下载插件 → pages 被静默跳过（只有服务起来），页面重启后才出现。
    """
    from zhuzhu_Copilot.core import copilot_plugins
    from zhuzhu_Copilot.ui import main_window as mw

    root = tmp_path / "plugins"
    monkeypatch.setattr(copilot_plugins, "COPILOT_PLUGINS_DIR", root)
    state = {"started": set(), "services": [], "tray": [], "shortcuts": [],
             "host": None, "errors": {}}
    monkeypatch.setattr(mw, "_COPILOT_EXT_STATE", state)
    panel = _FakePanel()
    try:
        mw.load_copilot_extensions(panel)
        assert state["host"] is panel, "零插件时也必须登记页面宿主"

        _make_plugin(root, "late-page")
        ok, msg = mw.start_copilot_plugin("late-page")   # 不传 panel：走已登记的宿主
        assert ok, msg
        assert panel._pages.count() == 1, "启用后页面必须立即挂到宿主页签"
        assert any(r.get("plugin") == "late-page"
                   for r in panel._copilot_ext["pages"])
    finally:
        mw.unregister_copilot_plugin("late-page")
        mw.unload_extension_modules("late-page")

