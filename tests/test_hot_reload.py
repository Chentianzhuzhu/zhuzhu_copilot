"""热加载引擎：原地重载 + 活类热修补 + 旧引用重绑 + 容器保留。

用合成包 `hotplug_lab` 验证机制本身，不依赖转译层与应用 UI。
"""

import importlib
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from zhuzhu_Copilot.core import hot_reload  # noqa: E402


@pytest.fixture(scope="module")
def lab(tmp_path_factory):
    """建一个临时包，并加进 sys.path。"""
    root = tmp_path_factory.mktemp("hotplug_lab_pkg")
    pkg = root / "hotplug_lab"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    sys.path.insert(0, str(root))
    yield pkg
    for m in [m for m in sys.modules if m.startswith("hotplug_lab")]:
        sys.modules.pop(m, None)
    try:
        sys.path.remove(str(root))
    except ValueError:
        pass


def _write(pkg: Path, rel: str, text: str):
    f = pkg / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text, encoding="utf-8")
    return "hotplug_lab." + rel[:-3].replace("/", ".")


def test_live_class_patch_updates_existing_instance(lab):
    """改了类方法 → **已存在的实例**立刻用上新实现（这就是"不重启"的关键）。"""
    name = _write(lab, "widgetish.py", '''
class Panel:
    def label(self):
        return "v1"
''')
    mod = importlib.import_module(name)
    inst = mod.Panel()
    assert inst.label() == "v1"

    _write(lab, "widgetish.py", '''
class Panel:
    def label(self):
        return "v2"

    def extra(self):            # 新增方法也要能用
        return "extra"
''')
    r = hot_reload.reload_module(name)
    assert r["ok"] and "Panel" in r["patched"], r
    assert inst.label() == "v2", "旧实例必须拿到新方法"
    assert inst.extra() == "extra"
    assert type(inst) is mod.Panel or type(inst).__name__ == "Panel"


def test_stale_from_import_rebound(lab):
    """`from x import y` 的旧引用被重绑到新对象（Python 语义的坑，这里补掉）。"""
    _write(lab, "w2.py", '''
def render(x):
    return "v1:" + str(x)
''')
    _write(lab, "c2.py", '''
from hotplug_lab.w2 import render
''')
    w2 = importlib.import_module("hotplug_lab.w2")
    c2 = importlib.import_module("hotplug_lab.c2")
    assert c2.render(1) == "v1:1"

    _write(lab, "w2.py", '''
def render(x):
    return "v2:" + str(x)
''')
    hot_reload.reload_module("hotplug_lab.w2")
    assert w2.render(1) == "v2:1"
    assert c2.render(1) == "v2:1", "旧引用必须被重绑"


def test_container_preserved_inplace(lab, monkeypatch):
    """容器全局量：保留旧对象、原地灌新内容（既有引用不断裂）。"""
    name = _write(lab, "registry.py", '''
TOOLS = [1, 2]
''')
    monkeypatch.setitem(hot_reload._INPLACE_CONTAINERS, name, ("TOOLS",))
    mod = importlib.import_module(name)
    held = mod.TOOLS                     # 模拟引擎启动时抓走的引用
    assert held == [1, 2]

    _write(lab, "registry.py", '''
TOOLS = [1, 2, 3]
''')
    hot_reload.reload_module(name)
    assert mod.TOOLS is held, "必须保留旧 list 对象（引用不变）"
    assert held == [1, 2, 3], "新内容原地灌入"


def test_process_state_preserved(lab, monkeypatch):
    """进程级状态：reload 不会把模块级登记表清空。"""
    name = _write(lab, "stateful.py", '''
STATE = {"k": 1}
''')
    monkeypatch.setitem(hot_reload._STATE_PRESERVE, name, ("STATE",))
    mod = importlib.import_module(name)
    held = mod.STATE
    held["k"] = 99
    _write(lab, "stateful.py", '''
STATE = {"k": 0}
''')
    hot_reload.reload_module(name)
    assert mod.STATE is held
    assert mod.STATE["k"] == 99, "reload 不得清空运行中的登记表"


def test_reload_failure_reports_error(lab):
    """语法坏掉的模块：报错而不是崩，且旧实现尽量保住。"""
    name = _write(lab, "broken.py", '''
def ok():
    return "v1"
''')
    mod = importlib.import_module(name)
    _write(lab, "broken.py", '''
def ok(:
    return broken
''')
    r = hot_reload.reload_module(name)
    assert not r["ok"] and r["error"], "必须报告错误"
    assert mod.ok() == "v1", "旧实现应尽量保住"


def test_no_restart_guarantee():
    """零重启保证：界面模块不在跳过名单；needs_restart 恒 False；文案不提重启。"""
    for name in ("zhuzhu_Copilot.ui.agent_panel", "zhuzhu_Copilot.ui.main_window",
                 "zhuzhu_Copilot.ui.styles"):
        assert name not in hot_reload._HOT_SKIP, f"{name} 必须支持热加载"
    assert hot_reload.needs_restart() is False
    s = hot_reload.summary({"reloaded": ["zhuzhu_Copilot.ui.agent_panel"],
                            "patched": ["zhuzhu_Copilot.ui.agent_panel.CopilotPanel"]})
    assert "重启" not in s
