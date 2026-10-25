"""Copilot 插件全链路热插拔：核心层事务（set_enabled/delete）真实驱动。

直接验证用户的硬要求——**任何插件启用/停用/失败/更新都在运行进程内即时完成，
绝不重启应用**：
  · 启用 create_file 新模块 → 运行中 import 立即可用，活实例随之热更新；
  · 停用 → 新模块立即从 sys.modules 摘除，再 import 失败（死代码不残留）；
  · 修改插件再启用 → 新版本即时生效；
  · 热加载失败（运行期错误）→ 自动回滚磁盘与运行态，插件保持停用，无半套变更；
  · 启停全程触发 UI 事件（页面/服务/托盘/快捷键挂摘由 main_window 监听）。
"""
import importlib
import json
import sys

import pytest

from zhuzhu_Copilot.core import (agent_plugins, agent_skills, copilot_plugins,
                                 hot_reload, plugin_guard, plugin_source)
import _copilot_overlay_hook

PROBE_MODULE = "zhuzhu_Copilot.core._hp_probe_mod"


def _v(body_value: str, label: str) -> str:
    return (
        "# 热插拔生命周期探针（测试生成，停用即删）\n"
        "VALUE = %s\n"
        "\n"
        "def value():\n"
        "    return VALUE\n"
        "\n"
        "class Probe:\n"
        "    def label(self):\n"
        "        return %r\n"
        "    def value(self):\n"
        "        return VALUE\n" % (body_value, label)
    )


@pytest.fixture()
def iso(tmp_path, monkeypatch):
    """插件目录、技能配置、转译层、导入钩子全部隔离到临时目录。"""
    monkeypatch.setattr(copilot_plugins, "COPILOT_PLUGINS_DIR",
                        tmp_path / "copilot_plugins")
    monkeypatch.setattr(agent_plugins, "PLUGINS_DIR", tmp_path / "plugins")
    monkeypatch.setattr(agent_skills, "CONFIG_DIR", tmp_path / "agent")
    overlay = tmp_path / "source_overlay"
    overlay.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(plugin_source, "overlay_root", lambda: overlay)
    monkeypatch.setattr(_copilot_overlay_hook, "overlay_dir", lambda: overlay)
    _copilot_overlay_hook.install()
    monkeypatch.setattr(plugin_guard, "_llm_review",
                        lambda blobs: {"verdict": "safe", "reason": "测试替身：安全"})
    # 引擎基线归零，保证每个用例从"进程内首次启停"的最严格场景开始
    hot_reload._CODE_CACHE.clear()
    hot_reload._STATS.clear()
    hot_reload._HASHES.clear()
    setattr(hot_reload, "_BASELINE_READY", False)
    yield {"overlay": overlay, "plugins": tmp_path / "copilot_plugins"}
    # 清理探针模块，避免跨用例残留
    for m in [m for m in list(sys.modules) if m == PROBE_MODULE]:
        sys.modules.pop(m, None)
    hot_reload._CODE_CACHE.clear()
    hot_reload._STATS.clear()
    hot_reload._HASHES.clear()
    setattr(hot_reload, "_BASELINE_READY", False)


def _make_plugin(name: str, ops: list, enabled: bool = True):
    d = copilot_plugins.plugin_dir(name)
    d.mkdir(parents=True, exist_ok=True)
    meta = {"name": name, "kind": "copilot", "enabled": enabled,
            "description": "热插拔探针", "author": "测试",
            "updated_at": "2026-01-01 00:00:00"}
    (d / "plugin.json").write_text(json.dumps(meta, ensure_ascii=False),
                                   encoding="utf-8")
    (d / "source_ops.json").write_text(json.dumps(ops, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
    return d


def _create_ops(v: str, label: str) -> list:
    return [{"op": "create_file",
             "path": "zhuzhu_Copilot/core/_hp_probe_mod.py",
             "content": _v(v, label)}]


def _import_probe():
    """确保从 sys.modules 现取（首次导入会走覆盖层 finder）。"""
    sys.modules.pop(PROBE_MODULE, None)
    return importlib.import_module(PROBE_MODULE)


# ══════════════ 1. 启用即生效（进程内首次启停，最严格场景） ══════════════

def test_enable_takes_effect_immediately_no_restart(iso):
    """新模块插件启用：不重启，import 立即可用。"""
    _make_plugin("hp-probe", _create_ops("1", "v1"))
    ok, msg = copilot_plugins.set_enabled("hp-probe", True)
    assert ok, msg
    assert "无需重启" in msg
    mod = _import_probe()
    assert mod.value() == 1
    assert mod.Probe().label() == "v1"
    # 元数据
    assert copilot_plugins.get_plugin("hp-probe")["enabled"] is True


# ══════════════ 2. 停用即摘除（死模块不残留） ══════════════

def test_disable_drops_module_immediately(iso):
    """停用：新模块立刻摘出 sys.modules，再次 import 失败；启用中插件与之一致。"""
    _make_plugin("hp-probe", _create_ops("1", "v1"))
    assert copilot_plugins.set_enabled("hp-probe", True)[0]
    mod = _import_probe()
    assert mod.value() == 1

    ok, msg = copilot_plugins.set_enabled("hp-probe", False)
    assert ok, msg
    assert "无需重启" in msg
    # 覆盖层文件已被回滚删除
    assert not (iso["overlay"] / "zhuzhu_Copilot" / "core" /
                "_hp_probe_mod.py").exists()
    # sys.modules 已摘除
    assert PROBE_MODULE not in sys.modules
    with pytest.raises(ModuleNotFoundError):
        _import_probe()
    assert copilot_plugins.get_plugin("hp-probe")["enabled"] is False


# ══════════════ 3. 改插件再启用：新版本即时生效（活实例热修补） ══════════════

def test_update_plugin_then_reenable_is_immediate(iso):
    """v1 启用 → 磁盘回滚+改 v2 → 再启用：import 与活实例都看到 v2。"""
    _make_plugin("hp-probe", _create_ops("1", "v1"))
    assert copilot_plugins.set_enabled("hp-probe", True)[0]
    mod1 = _import_probe()
    assert mod1.Probe().label() == "v1"

    # 模拟作者更新插件：停用 → 改写 source_ops 为 v2 → 启用
    assert copilot_plugins.set_enabled("hp-probe", False)[0]
    d = copilot_plugins.plugin_dir("hp-probe")
    (d / "source_ops.json").write_text(
        json.dumps(_create_ops("22", "v2"), ensure_ascii=False, indent=2),
        encoding="utf-8")
    ok, msg = copilot_plugins.set_enabled("hp-probe", True)
    assert ok, msg
    mod2 = _import_probe()
    assert mod2.value() == 22
    assert mod2.Probe().label() == "v2"


def test_live_instance_patched_via_core_transaction(iso):
    """不摘模块的原地更新（revert→apply→热加载）：已存在实例立即跑新方法。"""
    _make_plugin("hp-probe", _create_ops("1", "v1"))
    assert copilot_plugins.set_enabled("hp-probe", True)[0]
    mod = _import_probe()
    inst = mod.Probe()
    assert inst.label() == "v1" and inst.value() == 1

    # 原地把 v2 写进转译层（回滚 v1 → 应用 v2）并热加载
    d = copilot_plugins.plugin_dir("hp-probe")
    assert copilot_plugins.revert("hp-probe")[0]
    (d / "source_ops.json").write_text(
        json.dumps(_create_ops("33", "v2"), ensure_ascii=False, indent=2),
        encoding="utf-8")
    assert copilot_plugins.apply("hp-probe")[0]
    res = hot_reload.reload_overlay()
    assert not res["failed"], res["failed"]
    # 旧实例（v1 类对象）被热修补后立刻返回 v2
    assert inst.label() == "v2"
    assert inst.value() == 33


# ══════════════ 4. 热加载失败 → 自动回滚（不留半套变更） ══════════════

def test_runtime_failure_auto_rolls_back(iso):
    """语法合法但运行期抛错的插件：启用失败后自动回滚，保持停用，无死模块残留。"""
    bad_ops = [{"op": "create_file",
                "path": "zhuzhu_Copilot/core/_hp_probe_mod.py",
                "content": "raise RuntimeError('boom from probe')\n"}]
    _make_plugin("hp-bad", bad_ops)
    ok, msg = copilot_plugins.set_enabled("hp-bad", True)
    assert ok is False, msg
    assert "自动回滚" in msg and "无需重启" in msg
    # 元数据保持停用
    assert copilot_plugins.get_plugin("hp-bad")["enabled"] is False
    # 运行态：模块未残留
    assert PROBE_MODULE not in sys.modules
    # 磁盘：转译层无半套文件，插件包无应用清单
    assert not (iso["overlay"] / "zhuzhu_Copilot" / "core" /
                "_hp_probe_mod.py").exists()
    assert not (copilot_plugins.plugin_dir("hp-bad") / "applied.json").exists()


# ══════════════ 5. UI 事件桥：启停事件顺序正确 ══════════════

def test_ui_events_fired_on_toggle(iso):
    events = []
    hot_reload.on_ui_event("before_disable", lambda n: events.append(("off", n)),
                           key="lifecycle.off")
    hot_reload.on_ui_event("after_enable", lambda n: events.append(("on", n)),
                           key="lifecycle.on")
    hot_reload.on_ui_event("after_change", lambda n: events.append(("chg", n)),
                           key="lifecycle.chg")
    try:
        _make_plugin("hp-probe", _create_ops("1", "v1"))
        assert copilot_plugins.set_enabled("hp-probe", True)[0]
        assert ("on", "hp-probe") in events
        events.clear()
        assert copilot_plugins.set_enabled("hp-probe", False)[0]
        assert ("off", "hp-probe") in events
        assert ("chg", "hp-probe") in events
        # off 必须在 chg 之前（先摘 UI 再重建）
        order = [k for k, _ in events]
        assert order.index("off") < order.index("chg")
    finally:
        hot_reload._UI_EVENTS["before_disable"].pop("lifecycle.off", None)
        hot_reload._UI_EVENTS["after_enable"].pop("lifecycle.on", None)
        hot_reload._UI_EVENTS["after_change"].pop("lifecycle.chg", None)


# ══════════════ 6. 成功路径不允许出现"重启"字样 ══════════════

def test_no_restart_wording_on_success(iso):
    _make_plugin("hp-probe", _create_ops("1", "v1"))
    ok, msg = copilot_plugins.set_enabled("hp-probe", True)
    assert ok
    assert "无需重启" in msg
    # 不得出现"需要重启 / 重启后生效 / 请重启"之类的旧承诺
    for bad in ("需要重启", "重启后", "请重启", "重启应用"):
        assert bad not in msg, f"成功文案不应含「{bad}」：{msg}"
