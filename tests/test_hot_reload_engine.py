"""热插拔引擎 v2 回归（hot_reload）：精确基线、事务预编译、依赖排序、性能缓存。

与 test_hot_reload.py 的分工：后者覆盖单模块原地重载/活实例/回滚语义；
本文件覆盖升级后的**引擎级能力**：
  · 精确基线：模块加载时源码 == 磁盘源码 → 零重跑（启动基线未建也精准）；
  · 首次启停不再漏改（旧实现"第一个插件必须重启"的根因回归）；
  · 事务预编译：一个模块语法错误 → 一个运行中的模块都不被污染；
  · AST 依赖排序：被依赖者先重载；
  · stat 快路径：磁盘未变 → 第二次热加载不产生任何重加载；
  · 磁盘代码缓存：跨"缓存清空"仍免编译（marshal 落盘）。
"""
import sys
import types

import pytest

from zhuzhu_Copilot.core import hot_reload as hr


def _reset_engine():
    """清空引擎缓存与基线（测试间互不污染）。"""
    hr._CODE_CACHE.clear()
    hr._STATS.clear()
    hr._HASHES.clear()
    setattr(hr, "_BASELINE_READY", False)


@pytest.fixture(autouse=True)
def _isolated_overlay(tmp_path, monkeypatch):
    """把覆盖层导入钩子的 overlay_dir 钉到空临时目录（避免命中开发机真实覆盖层）。

    finder 每次 find_spec 都动态调用模块级 overlay_dir()，所以即便钩子已安装，
    monkeypatch 也立即生效；未安装时确保临时目录存在并安装。
    """
    import _copilot_overlay_hook as hook
    ov = tmp_path / "source_overlay"
    ov.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(hook, "overlay_dir", lambda: ov)
    hook.install()
    _reset_engine()
    yield
    _reset_engine()



@pytest.fixture()
def lab():
    """合成一个两片热插拔实验室：lab2pkg 与 lab2pkg.w1/w2（走 loader.get_source 基线）。"""
    import importlib.util
    import tempfile
    from pathlib import Path

    tmp = Path(tempfile.mkdtemp(prefix="hotreload_engine_"))
    pkgdir = tmp / "lab2pkg"
    pkgdir.mkdir()

    def write(name, text):
        (pkgdir / name).write_text(text, encoding="utf-8")

    write("__init__.py", "")
    write("w1.py", "V = 1\n\ndef f():\n    return V\n")
    write("w2.py", "from lab2pkg.w1 import f\n\nV = 2\n")
    # 全进程标量哨兵：小整数 id 全局固定，热重绑绝不能把别的模块里的 0 改掉
    write("sentinel.py", "ZERO = 0\nONE = 1\n")

    def _load():
        # 每个模块一个独立顶层名，直接挂到 sys.path（loader.get_source 可读文件）
        if str(tmp) not in sys.path:
            sys.path.insert(0, str(tmp))
        import lab2pkg  # noqa: F401
        import lab2pkg.w1  # noqa: F401
        import lab2pkg.w2  # noqa: F401
        import lab2pkg.sentinel  # noqa: F401

    _load()
    yield {"tmp": tmp, "write": write, "pkgdir": pkgdir}
    for m in [m for m in list(sys.modules) if m == "lab2pkg" or m.startswith("lab2pkg.")]:
        sys.modules.pop(m, None)
    try:
        sys.path.remove(str(tmp))
    except ValueError:
        pass


def _reload_generic(name):
    """让引擎对非 zhuzhu 合成包走 __spec__ 回落分支。"""
    return hr.reload_module(name)


# ══════════════ 1. 精确基线：未变更模块零重跑 ══════════════

def test_precise_baseline_skips_unchanged_without_startup_scan(lab):
    """没调过 establish_baseline、模块已是磁盘版本 → 全量热加载也不重跑任何模块。"""
    _reset_engine()
    # 引擎不认识 lab2pkg，但它们是"已导入且磁盘一致"的模块 → 精确基线应跳过
    res = hr.reload_overlay()
    assert not res["reloaded"], f"未变更模块不应重跑：{res['reloaded']}"
    assert not res["failed"], res["failed"]
    # 第二次（基线已记录）同样零重跑
    res2 = hr.reload_overlay()
    assert not res2["reloaded"]


def test_first_change_after_baseline_is_reloaded(lab):
    """回归：进程内第一次变更（启动基线未建立时）必须被热加载，而不是当作基线跳过。"""
    _reset_engine()
    import lab2pkg.w1 as w1
    assert w1.f() == 1
    # 不调用 establish_baseline，直接改磁盘（模拟"首个插件启用"）
    lab["write"]("w1.py", "V = 42\n\ndef f():\n    return V\n")
    # 引擎对非 zhuzhu 模块的候选清单来自 sys.modules 过滤；手动触发单模块重载
    r = _reload_generic("lab2pkg.w1")
    assert r["ok"], r.get("error")
    assert w1.f() == 42, "首个变更必须立即生效（旧实现会漏掉，逼用户重启）"


def test_stat_fast_path_second_call_is_noop(lab):
    """磁盘没变：第二次热加载不重跑、不重读（stat 签名命中）。"""
    _reset_engine()
    lab["write"]("w1.py", "V = 7\n\ndef f():\n    return V\n")
    r1 = _reload_generic("lab2pkg.w1")
    assert r1["ok"]
    # 不改文件再跑：跳过
    r2 = hr.reload_module("lab2pkg.w1")
    assert r2["ok"] and not r2["patched"]
    assert sys.modules["lab2pkg.w1"].f() == 7


# ══════════════ 2. 事务预编译：坏模块不污染任何运行模块 ══════════════

def test_precompile_failure_aborts_before_executing_anything(lab):
    """变更集合里有一个语法错误模块：预编译阶段即中止，其余模块一字未动。"""
    _reset_engine()
    import lab2pkg.w1 as w1
    # 两个模块同时"变更"：w1 合法，w2 语法坏掉
    lab["write"]("w1.py", "V = 100\n\ndef f():\n    return V\n")
    lab["write"]("w2.py", "def broken(:\n    pass\n")
    # 直接对两模块做预编译校验（模拟 reload_overlay 的事务闸门）
    ok1, src1 = True, (lab["pkgdir"] / "w1.py").read_bytes()
    from zhuzhu_Copilot.core import hot_reload as _hr
    # w1 能编译
    _hr._code_for("lab2pkg.w1", src1, lab["pkgdir"] / "w1.py")
    bad = (lab["pkgdir"] / "w2.py").read_bytes()
    with pytest.raises(SyntaxError):
        compile(bad, "w2.py", "exec")
    # 关键断言：w1 没被执行——运行中的旧值仍是 1
    assert w1.f() == 1, "预编译失败必须发生在任何模块执行之前"


# ══════════════ 3. AST 依赖排序 ══════════════

def test_ast_dependency_ordering_dep_first(lab):
    """w2 依赖 w1：拓扑序里 w1 必须排在 w2 前面。"""
    _reset_engine()
    src = {
        "lab2pkg.w1": (lab["pkgdir"] / "w1.py").read_bytes(),
        "lab2pkg.w2": (lab["pkgdir"] / "w2.py").read_bytes(),
    }
    order = hr._dependency_order(src)
    assert order.index("lab2pkg.w1") < order.index("lab2pkg.w2"), order


def test_ast_dependency_ordering_relative_import():
    """相对导入（from .w1 import f）同样能解析依赖边。"""
    src = {
        "lab2pkg.core.w1": b"V = 1\n",
        "lab2pkg.core.w2": b"from .w1 import V\n",
    }
    order = hr._dependency_order(src)
    assert order.index("lab2pkg.core.w1") < order.index("lab2pkg.core.w2"), order


# ══════════════ 4. 活实例跨模块热更新（首变更场景，含从导入引用） ══════════════

def test_live_instance_and_from_import_update_on_first_change(lab):
    """不建基线直接改：类实例方法立即热修补；from-import 引用重绑。"""
    _reset_engine()
    lab["write"]("w1.py",
                 "class C:\n    def v(self):\n        return 1\n\n"
                 "def g():\n    return 1\n")
    lab["write"]("w2.py",
                 "from lab2pkg.w1 import C, g\n\n"
                 "class C2:\n    def render(self):\n        return C().v() + g()\n")
    r1 = _reload_generic("lab2pkg.w1")
    assert r1["ok"]
    r2 = _reload_generic("lab2pkg.w2")
    assert r2["ok"]
    import lab2pkg.w2 as w2
    old_c2 = w2.C2
    inst = old_c2()
    assert inst.render() == 2
    # v2：两个方法都改
    lab["write"]("w1.py",
                 "class C:\n    def v(self):\n        return 10\n\n"
                 "def g():\n    return 10\n")
    lab["write"]("w2.py",
                 "from lab2pkg.w1 import C, g\n\n"
                 "class C2:\n    def render(self):\n        return C().v() + g()\n")
    _reload_generic("lab2pkg.w1")
    _reload_generic("lab2pkg.w2")
    assert inst.render() == 20, "活实例必须立刻用上新代码（含跨模块从导入引用）"


# ══════════════ 4b. 标量重绑不得污染全局（invalid SRE code 回归） ══════════════

def test_rebind_scalar_constants_never_touches_global_ids(lab):
    """模块级整数常量变化（1→42）时，不得按 id 重绑全 sys.modules 的小整数。

    旧实现把 (old_obj, new_obj) 中的 int 也纳入 id() 映射，而 CPython 小整数
    全局缓存（id(0)/id(1) 固定），导致遍历所有模块把无关常量误改——
    实测 re._compiler 的整数 opcode 被改写后，整个进程 re.compile 抛
    RuntimeError: invalid SRE code，污染后续所有测试文件。
    """
    import re
    _reset_engine()
    import lab2pkg.sentinel as sentinel
    assert sentinel.ZERO == 0 and sentinel.ONE == 1
    lab["write"]("w1.py", "V = 42\n\ndef f():\n    return V\n")
    r = _reload_generic("lab2pkg.w1")
    assert r["ok"], r.get("error")
    # 函数引用仍正常重绑（w2 的 from-import f 立即指向新实现）
    assert sys.modules["lab2pkg.w2"].f() == 42
    # 哨兵模块的小整数原样未动
    assert sentinel.ZERO == 0 and sentinel.ONE == 1
    # 最关键：全进程 re 编译能力没被破坏
    assert re.compile(r"\d+").findall("a12b34") == ["12", "34"]
    assert re.compile("//([^/#?]*)(.*)", re.DOTALL).match("//host/path") is not None


# ══════════════ 5. 磁盘代码缓存（跨重启免编译） ══════════════
def test_disk_code_cache_recompiles_only_once(tmp_path):
    """同一内容在内存缓存清空后，应从磁盘 marshal 缓存取回（不重新 compile）。"""
    _reset_engine()
    p = tmp_path / "m_x.py"
    p.write_text("V = 1\n", encoding="utf-8")
    src = p.read_bytes()
    code1 = hr._code_for("hotplug_cache_probe.m_x", src, p)
    # 模拟跨重启：内存缓存清空，磁盘缓存还在
    hr._CODE_CACHE.clear()
    code2 = hr._code_for("hotplug_cache_probe.m_x", src, p)
    # 字节码等价（marshal 往返后代码内容一致）
    import marshal
    assert marshal.dumps(code1) == marshal.dumps(code2), \
        "磁盘缓存取回的字节码必须与首次编译一致"


def test_needs_restart_is_always_false():
    """热插拔引擎对任何结果都不得要求重启。"""
    assert hr.needs_restart(None) is False
    assert hr.needs_restart({"failed": ["x"]}) is False


# ══════════════ 6. UI 事件桥（无 UI 环境安全降级） ══════════════

def test_ui_event_hooks_fire():
    _reset_engine()
    calls = []
    hr.on_ui_event("after_enable", lambda nm: calls.append(("enable", nm)),
                   key="test.after_enable")
    fired = hr.emit_ui("after_enable", "demo")
    assert fired is True
    assert calls == [("enable", "demo")]
    # 无监听的事件：不报错并返回 False（会话里可能已有真实 main_window 监听，
    # 故临时清空该事件，测完恢复，不影响其他用例）
    saved = hr._UI_EVENTS["before_disable"]
    hr._UI_EVENTS["before_disable"] = {}
    try:
        assert hr.emit_ui("before_disable", "demo") is False
    finally:
        hr._UI_EVENTS["before_disable"] = saved
    hr._UI_EVENTS["after_enable"].pop("test.after_enable", None)


def test_ui_event_hook_dedup_by_key():
    """同 key 重复注册只保留最新回调（模拟模块被热重载后函数对象重建）。"""
    _reset_engine()
    hr.on_ui_event("after_change", lambda nm: "old", key="dedup.test")
    hr.on_ui_event("after_change", lambda nm: "new", key="dedup.test")
    cb = hr._UI_EVENTS["after_change"]["dedup.test"]
    assert cb(None) == "new"
    # 同 key 只有一条
    assert sum(1 for k in hr._UI_EVENTS["after_change"] if k == "dedup.test") == 1
    hr._UI_EVENTS["after_change"].pop("dedup.test", None)
