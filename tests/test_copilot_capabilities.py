"""Copilot 插件能力验证 + 转译层性能预算（真实热插拔，不重启）。

验证用户关心的四类源码级能力（全部走转译层，应用源码文件保持不变）：
  1) 修改/替换**现有源码**：insert_after / replace_module 命中真实模块（utils/helpers）
  2) 新增源码文件作为**新功能模块**：新建包 + 模块，热加载后可直接 import 调用
  3) **引用任意文件**：插件模块可读取任意绝对路径文件、跨模块互相引用
  4) **综合性开发**：一次插件同时「改现有模块 + 新建模块 + 模块间登记/调用」，
     热加载后立即生效，revert 后完整还原（新模块从 sys.modules 摘除）

性能预算：大型插件（30 个新模块，约 240KB）「apply + 热加载」总耗时 ≤3s。
"""
import json
import os
import sys
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

import _copilot_overlay_hook                      # noqa: E402
from zhuzhu_Copilot.core import copilot_plugins    # noqa: E402
from zhuzhu_Copilot.core import hot_reload          # noqa: E402
from zhuzhu_Copilot.core import plugin_guard        # noqa: E402
from zhuzhu_Copilot.core import plugin_source       # noqa: E402

HELPERS_REL = "zhuzhu_Copilot/utils/helpers.py"
_CRASH_ANCHOR = '_CRASH_DIR = app_identity.temp_dir() / "crashes"'


def _reset_engine() -> None:
    hot_reload._CODE_CACHE.clear()
    hot_reload._STATS.clear()
    hot_reload._HASHES.clear()
    hot_reload._BASELINE_READY = False


@pytest.fixture()
def live(tmp_path, monkeypatch):
    """真实模块 + 隔离转译层的热插拔环境。"""
    overlay = tmp_path / "overlay"
    monkeypatch.setattr(plugin_source, "overlay_root", lambda: overlay)
    monkeypatch.setattr(_copilot_overlay_hook, "overlay_dir", lambda: overlay)
    monkeypatch.setattr(copilot_plugins, "COPILOT_PLUGINS_DIR",
                        tmp_path / "copilot_plugins")
    monkeypatch.setattr(plugin_guard, "_llm_review",
                        lambda blobs: {"verdict": "safe", "reason": "测试替身：安全"})
    import zhuzhu_Copilot.utils.helpers as helpers   # noqa: F401  真实模块作为改造目标
    _reset_engine()
    hot_reload.establish_baseline(scan_all=False)   # 等价启动后的基线（只覆盖已导入模块）
    yield {"overlay": overlay, "tmp": tmp_path}
    # 清理：回滚全部插件并强制热加载回原版，摘除新建模块
    for name in [p.name for p in copilot_plugins.plugins_dir().iterdir()
                 if p.is_dir()] if copilot_plugins.plugins_dir().is_dir() else []:
        try:
            copilot_plugins.revert(name)
        except Exception:
            pass
    hot_reload.reload_overlay(force=True)
    _reset_engine()
    for m in [x for x in sys.modules if x.startswith("zhuzhu_Copilot.gen_cap")]:
        sys.modules.pop(m, None)
    import zhuzhu_Copilot
    for attr in ("gen_cap",):
        try:
            delattr(zhuzhu_Copilot, attr)
        except Exception:
            pass


def _mk(name, ops, **extra):
    d = copilot_plugins.plugin_dir(name)
    d.mkdir(parents=True, exist_ok=True)
    meta = {"name": name, "kind": "copilot", "enabled": True,
            "description": "t", "author": "测试作者"}
    meta.update(extra)
    (d / "plugin.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    (d / "source_ops.json").write_text(json.dumps(ops, ensure_ascii=False), encoding="utf-8")
    return d


def _apply_live(name):
    ok, msg = copilot_plugins.set_enabled(name, True)
    assert ok, msg
    return msg


def _original_helpers() -> Path:
    """应用原版 helpers.py（转译层之外的真实源码文件）。"""
    return Path(plugin_source.__file__).resolve().parents[2] / HELPERS_REL


def test_modify_and_replace_existing_source(live):
    """能力 1：修改现有源码（插入新常量）+ 用插件代码替换现有函数，热加载立即生效。"""
    original_before = _original_helpers().read_text(encoding="utf-8")
    _mk("cap-mod", [
        {"op": "insert_after", "path": HELPERS_REL, "anchor": _CRASH_ANCHOR,
         "content": 'CAP_MARK = "cap-v1"'},
        {"op": "replace_module", "path": HELPERS_REL,
         "start": "def setup_logging() -> logging.Logger:",
         "end": "    return logger",
         "content": 'def setup_logging():\n    return "plugin-version"'},
    ])
    _apply_live("cap-mod")

    # 应用源码文件一字未改（变更只在转译层）
    assert _original_helpers().read_text(encoding="utf-8") == original_before
    overlay_text = (live["overlay"] / HELPERS_REL).read_text(encoding="utf-8")
    assert 'CAP_MARK = "cap-v1"' in overlay_text
    assert "plugin-version" in overlay_text and "    return logger" not in overlay_text

    # 热加载后运行中的模块就是插件版本
    import zhuzhu_Copilot.utils.helpers as helpers
    assert getattr(helpers, "CAP_MARK", "") == "cap-v1"
    assert helpers.setup_logging() == "plugin-version"

    ok, msg = copilot_plugins.revert("cap-mod")
    assert ok, msg
    hot_reload.reload_overlay()
    # 重执行语义不清空旧全局（避免误删运行态），因此只断言「代码已还原」：
    assert "plugin-version" not in helpers.setup_logging.__code__.co_consts
    assert not (live["overlay"] / HELPERS_REL).exists()


def test_new_source_module_as_feature(live):
    """能力 2：新建源码文件（含新包）作为功能模块，热加载后可直接 import 调用。"""
    _mk("cap-new", [
        {"op": "create_file", "path": "zhuzhu_Copilot/gen_cap/__init__.py",
         "content": 'TAG = "gen-cap"\n'},
        {"op": "create_file", "path": "zhuzhu_Copilot/gen_cap/feature.py",
         "content": 'def greet(name):\n    return f"hello {name}"\n'},
    ])
    _apply_live("cap-new")
    import zhuzhu_Copilot.gen_cap.feature as feature
    assert feature.greet("world") == "hello world"
    from zhuzhu_Copilot.gen_cap import TAG
    assert TAG == "gen-cap"


def test_plugin_code_references_arbitrary_files(live):
    """能力 3：插件代码可引用任意文件（绝对路径读取 + 包内资源 + 跨模块引用）。"""
    data = live["tmp"] / "external-data.txt"
    data.write_text("外部文件内容", encoding="utf-8")
    pkg = _mk("cap-file", [
        {"op": "create_file", "path": "zhuzhu_Copilot/gen_cap/reader.py",
         "content": (f'PATH = r"{data}"\n\n'
                     'def read_external():\n'
                     '    with open(PATH, encoding="utf-8") as f:\n'
                     '        return f.read()\n\n'
                     'def read_relative(p):\n'
                     '    with open(p, encoding="utf-8") as f:\n'
                     '        return f.read()\n'),
         },
    ])
    (pkg / "resources").mkdir()
    (pkg / "resources" / "note.txt").write_text("包内资源", encoding="utf-8")
    _apply_live("cap-file")

    import zhuzhu_Copilot.gen_cap.reader as reader
    assert reader.read_external() == "外部文件内容"
    assert reader.read_relative(str(pkg / "resources" / "note.txt")) == "包内资源"


def test_combined_development(live):
    """能力 4：综合开发——改现有模块 + 新建模块 + 新模块登记进现有模块并被调用。"""
    _mk("cap-combo", [
        {"op": "insert_after", "path": HELPERS_REL, "anchor": _CRASH_ANCHOR,
         "content": "CAP_TOOLS = {}\n\ndef cap_register(name, fn):\n"
                    "    CAP_TOOLS[name] = fn\n    return name"},
        {"op": "create_file", "path": "zhuzhu_Copilot/gen_cap/tool.py",
         "content": ("from zhuzhu_Copilot.utils.helpers import cap_register\n\n"
                     "def double(x):\n    return x * 2\n\n"
                     'cap_register("double", double)\n')},
    ])
    _apply_live("cap-combo")

    import zhuzhu_Copilot.utils.helpers as helpers
    import zhuzhu_Copilot.gen_cap.tool  # noqa: F401  导入即登记
    assert helpers.CAP_TOOLS["double"](21) == 42

    # 卸载：现有模块还原、新建模块从 sys.modules 摘除，功能整体消失
    ok, msg = copilot_plugins.revert("cap-combo")
    assert ok, msg
    hot_reload.reload_overlay()
    assert not (live["overlay"] / HELPERS_REL).exists()
    assert not copilot_plugins.status("cap-combo")["applied"]
    assert "zhuzhu_Copilot.gen_cap.tool" not in sys.modules


def test_large_plugin_insert_within_budget(live):
    """性能预算：30 个新模块（约 240KB）+ 改 1 个现有模块，apply + 热加载 ≤3s。"""
    def mod(i: int) -> str:
        body = "\n".join(f"def f_{i}_{j}(a, b):\n    return a + b + {j}\n"
                         for j in range(240))
        return f"# cap perf module {i}\n{body}\n"

    ops = [{"op": "create_file", "path": f"zhuzhu_Copilot/gen_cap/p{i}.py",
            "content": mod(i)} for i in range(29)]
    ops.append({"op": "append", "path": HELPERS_REL,
                "content": "PERF_TAG = 'v1'\n"})
    _mk("cap-perf", ops)

    t0 = time.perf_counter()
    ok, msg = copilot_plugins.apply("cap-perf")
    t_apply = time.perf_counter() - t0
    assert ok, msg
    t1 = time.perf_counter()
    ok, msg = copilot_plugins.set_enabled("cap-perf", True)   # 已应用 → 只走热加载
    t_reload = time.perf_counter() - t1
    elapsed = t_apply + t_reload
    assert ok, msg
    assert elapsed <= 3.0, (f"大型插件插入耗时 {elapsed:.2f}s 超过 3s 预算"
                            f"（apply {t_apply:.2f}s / 热加载 {t_reload:.2f}s；"
                            f"{msg}；分阶段 {plugin_source.LAST_APPLY}）")
    import zhuzhu_Copilot.utils.helpers as helpers
    assert getattr(helpers, "PERF_TAG", "") == "v1"
    assert plugin_source.LAST_APPLY.get("ops") == 30
