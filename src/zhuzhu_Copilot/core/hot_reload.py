"""转译层热加载引擎：插件启用/停用后**全部立即生效，绝不要求重启应用**。

转译层（`~/.zhuzhu_Copilot/source_overlay`）写入后，Python 里已导入的模块仍是旧代码。
旧引擎对 `agent_panel` / `main_window` / `styles` 这类"持有进程级状态"的模块直接跳过，
让用户重启——本版把这条路彻底封死，靠四件事把热加载做完整：

  1. **原地重载**：`importlib.reload` 重执行模块（模块对象不变，`__dict__` 原地更新），
     所有"调用时才查全局名"的旧代码立刻看到新实现；
  2. **活类热修补**：reload 会重建类对象，而已存在的实例仍指向旧类。这里把新类的全部
     属性（方法/类属性）**就地补进旧类对象**，运行中的窗口实例立刻用上新方法；
  3. **旧引用重绑**：遍历 `sys.modules`，把与旧对象**同一身份**（`is`）的属性换成新对象
     ——解决 `from x import y` 拿到旧函数/旧常量的经典难题；
  4. **进程级状态保留**：reload 会重执行模块级赋值（`_COPILOT_EXT_STATE = {...}`），
     会把运行中的登记表清空。这里对白名单全局量做"快照→恢复"，容器类（list/dict）
     做**就地更新**，保证既有引用不被替换掉。

外加：基于内容哈希的增量重载（没变的模块不碰）、插件新建模块被回滚后从 `sys.modules`
摘除、UI 模块重载后自动重建主题/派生 QSS 并刷新活实例。

失败策略（配合"绝不重启"）：任何模块热加载失败都会在结果里如实报告，调用方
（设置页开关）据此**自动回滚该插件**——要么生效、要么回滚，永远不给"重启一下就好了"。

仍不可热重载的只有插件引擎自身（重载等于拆掉热加载的地基）：
`plugin_source` / `copilot_plugins` / `plugin_guard` / `hot_reload` 与包根。
"""

import hashlib
import importlib
import sys
from pathlib import Path

#: 不可热重载：插件引擎自身与包根（重载它们等于拆地基）
_HOT_SKIP = frozenset({
    "zhuzhu_Copilot",                    # 重载包根会让所有子模块引用脱节
    "zhuzhu_Copilot.core.plugin_source",
    "zhuzhu_Copilot.core.copilot_plugins",
    "zhuzhu_Copilot.core.plugin_guard",
    "zhuzhu_Copilot.core.hot_reload",
})

#: reload 会重执行的模块级**进程状态**：快照 → 重载 → 原样放回（保持同一对象）
_STATE_PRESERVE = {
    "zhuzhu_Copilot.ui.main_window": ("_COPILOT_EXT_STATE",),
    # _THEME_VERSION 必须保留：清零会让旧渲染缓存键与新主题版本撞车 → 旧配色残留
    "zhuzhu_Copilot.ui.agent_panel": ("_THEME_VERSION", "_LAST_PALETTE"),
}

#: reload 会重建的**容器**全局量：保留旧对象、把新内容灌进去（既有引用继续有效）。
#: 典型如 agent_tools.TOOLS——引擎/面板可能持有该 list 的引用，reload 换新 list 会让
#"已注册的工具"在新旧之间分裂。
_INPLACE_CONTAINERS = {
    "zhuzhu_Copilot.core.agent_tools": ("TOOLS",),
    "zhuzhu_Copilot.core.agent_skills": ("_SKILL_KEYWORDS",),
}

#: 旧引用重绑时跳过的模块（系统/解释器内部，动了会出诡异问题）
_REBIND_SKIP = frozenset({"sys", "builtins", "_frozen_importlib", "_frozen_importlib_external",
                          "zhuzhu_Copilot.core.hot_reload"})

#: 内容哈希缓存：{模块名: sha256}——没变的模块不重载（高性能的关键）
_HASHES = {}
_BASELINE_READY = False


# ---------------------------------------------------------------------------
# 转译层模块清单
# ---------------------------------------------------------------------------
def _overlay_hook():
    import _copilot_overlay_hook
    _copilot_overlay_hook.install()      # 首次启用时覆盖层目录刚创建
    return _copilot_overlay_hook


def _module_names() -> list:
    """转译层里的模块名（按层级由浅到深排序：先重载被依赖的）。"""
    try:
        rels = _overlay_hook().overlay_modules()
    except Exception:
        return []
    names = []
    for rel in rels:
        rel = str(rel).replace("\\", "/")
        if not rel.endswith(".py"):
            continue
        name = rel[:-3].replace("/", ".")
        if name == "zhuzhu_Copilot" or name.startswith("zhuzhu_Copilot."):
            names.append(name)
    names.sort(key=lambda n: (n.count("."), n))
    return names


def _file_hash(path: Path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return ""


def _drop_pyc(root, name: str) -> None:
    """删掉该模块的字节码缓存：源文件 mtime 只精确到秒，同秒内改写会误用旧 pyc。"""
    try:
        src = root / (name.replace(".", "/") + ".py")
        pyc = importlib.util.cache_from_source(str(src))
        Path(pyc).unlink(missing_ok=True)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 活类热修补：新类的属性就地补进旧类对象
# ---------------------------------------------------------------------------
def _patch_live_class(live_cls, new_cls) -> int:
    """把 new_cls 的属性补进 live_cls（已存在实例仍指向 live_cls）。

    跳过 `__dict__` / `__weakref__` / `__module__`（CPython 类型槽，不能 setattr）；
    其余一律 setattr：方法、类属性、property、`__init__` 全部换成新实现。
    返回补上的属性数。
    """
    n = 0
    for k, v in list(vars(new_cls).items()):
        if k in ("__dict__", "__weakref__", "__module__"):
            continue
        try:
            setattr(live_cls, k, v)
            n += 1
        except Exception:
            continue
    return n


def _patch_live_classes(mod, old_ns: dict) -> list:
    """对模块里"同名但对象不同"的类做就地热修补。返回 [类名, ...]。"""
    patched = []
    for name, new_obj in list(vars(mod).items()):
        old_obj = old_ns.get(name)
        if old_obj is new_obj or old_obj is None:
            continue
        if isinstance(new_obj, type) and isinstance(old_obj, type):
            if _patch_live_class(old_obj, new_obj):
                patched.append(name)
    return patched


# ---------------------------------------------------------------------------
# 旧引用重绑：`from x import y` 的旧对象 → 新对象
# ---------------------------------------------------------------------------
def _rebind_stale_refs(pairs: list) -> int:
    """pairs = [(old_obj, new_obj), ...]：把 sys.modules 里身份相同的旧引用换成新的。

    只扫属性表（每模块几十~几百个），跳过系统模块；通常几十毫秒内完成。
    """
    if not pairs:
        return 0
    n = 0
    for mname, mod in list(sys.modules.items()):
        if mname in _REBIND_SKIP or mod is None:
            continue
        ns = getattr(mod, "__dict__", None)
        if not ns:
            continue
        for attr, val in list(ns.items()):
            for old_obj, new_obj in pairs:
                if val is old_obj:
                    try:
                        setattr(mod, attr, new_obj)
                        n += 1
                    except Exception:
                        pass
                    break
    return n


# ---------------------------------------------------------------------------
# 进程状态 / 容器保留
# ---------------------------------------------------------------------------
def _preserve_state(mod, old_ns: dict) -> None:
    name = getattr(mod, "__name__", "")
    for key in _STATE_PRESERVE.get(name, ()):
        if key in old_ns:
            mod.__dict__[key] = old_ns[key]          # 同一对象放回
    for key in _INPLACE_CONTAINERS.get(name, ()):
        old_obj = old_ns.get(key)
        new_obj = mod.__dict__.get(key)
        if old_obj is None or new_obj is None or old_obj is new_obj:
            continue
        if isinstance(old_obj, list) and isinstance(new_obj, list):
            old_obj[:] = new_obj                     # 原地灌入，引用不换
            mod.__dict__[key] = old_obj
        elif isinstance(old_obj, dict) and isinstance(new_obj, dict):
            old_obj.clear()
            old_obj.update(new_obj)
            mod.__dict__[key] = old_obj


# ---------------------------------------------------------------------------
# 插件新建模块被回滚后：从 sys.modules 摘除死模块
# ---------------------------------------------------------------------------
def drop_dead_modules() -> list:
    """摘除"转译层和原版里都不存在了"的 zhuzhu_Copilot.* 模块（回滚 create_file 后残留）。

    不摘的话，死代码会一直活在 sys.modules 里：再次启用同名插件时会命中旧对象，
    看起来"改了没生效"。
    """
    dropped = []
    try:
        hook = _overlay_hook()
    except Exception:
        return dropped
    for name in list(sys.modules):
        if not name.startswith("zhuzhu_Copilot.") or name in _HOT_SKIP:
            continue
        rel = name.replace(".", "/") + ".py"
        try:
            has_overlay = (hook.overlay_dir() / rel).is_file()
            orig = hook._original_file(name.split("."))
        except Exception:
            continue
        if not has_overlay and orig is None:
            sys.modules.pop(name, None)
            dropped.append(name)
    return dropped


# ---------------------------------------------------------------------------
# UI 刷新：主题 / 派生 QSS / 活实例
# ---------------------------------------------------------------------------
def _refresh_ui(names: list) -> list:
    """UI 模块热重载后重建主题与派生 QSS，并刷新活实例。返回刷新动作清单。"""
    done = []
    ui_mods = [n for n in names if n.startswith("zhuzhu_Copilot.ui.")]
    if not ui_mods:
        return done
    try:
        from PyQt6.QtWidgets import QApplication
    except Exception:
        return done
    app = QApplication.instance()
    if app is None:
        return done

    try:
        from zhuzhu_Copilot.ui import styles
    except Exception:
        styles = None

    theme = None
    for w in app.allTopLevelWidgets():
        fn = getattr(w, "_current_theme", None)
        if callable(fn):
            try:
                theme = fn()
            except Exception:
                theme = None
            if theme:
                break

    if styles is not None:
        try:
            if "zhuzhu_Copilot.ui.styles" in ui_mods or \
                    "zhuzhu_Copilot.ui.agent_panel" in ui_mods:
                styles.set_palette(str(theme or "dark"))     # 重建 GLOBAL_QSS
                if theme:
                    styles.apply_palette(app)
                done.append("styles:QSS")
        except Exception:
            pass
        try:
            if "zhuzhu_Copilot.ui.agent_panel" in ui_mods:
                from zhuzhu_Copilot.ui import agent_panel as ap
                ap._apply_colors(dict(styles.PALETTE), force=True, bump_version=True)
                done.append("agent_panel:colors")
        except Exception:
            pass

    # 活实例刷新：只对定义了刷新方法的类实例逐个调用（MainWindow._apply_theme 等）
    n = 0
    for w in list(app.allTopLevelWidgets()):
        cls = type(w)
        if cls.__module__ not in ui_mods:
            continue
        for meth in ("_apply_theme", "refresh_theme"):
            fn = getattr(w, meth, None)
            if callable(fn):
                try:
                    fn()
                    n += 1
                except Exception:
                    pass
                break
    if n:
        done.append(f"{n} 个窗口已重刷")
    return done


# ---------------------------------------------------------------------------
# 单模块热加载（重载 + 热修补 + 重绑 + 状态保留）
# ---------------------------------------------------------------------------
def reload_module(name: str, force: bool = False) -> dict:
    """热加载单个模块。返回 {ok, patched, rebound, error}。"""
    out = {"ok": False, "patched": [], "rebound": 0, "error": ""}
    try:
        from zhuzhu_Copilot.core import plugin_source
        root, _mode = plugin_source.apply_root()
    except Exception:
        root = None
    if root is not None:
        _drop_pyc(root, name)
    mod = sys.modules.get(name)
    if mod is None:
        try:
            importlib.import_module(name)
            out["ok"] = True
            return out
        except Exception as e:
            out["error"] = f"{type(e).__name__}: {e}"
            return out

    old_ns = dict(mod.__dict__)                     # 快照（旧类对象靠实例续命）
    try:
        importlib.reload(mod)
    except Exception as e:
        # reload 失败时模块对象可能处于半新半旧状态：把快照里**存在**的名字补回去，
        # 尽量保住旧实现（调用方会回滚插件，转译层回落原版后下次再重载）
        for k, v in old_ns.items():
            mod.__dict__.setdefault(k, v)
        out["error"] = f"{type(e).__name__}: {e}"
        return out

    new_ns = mod.__dict__
    out["patched"] = _patch_live_classes(mod, old_ns)
    pairs = []
    for k, new_obj in list(new_ns.items()):
        old_obj = old_ns.get(k)
        if old_obj is None or old_obj is new_obj:
            continue
        pairs.append((old_obj, new_obj))
    out["rebound"] = _rebind_stale_refs(pairs)
    _preserve_state(mod, old_ns)
    out["ok"] = True
    return out


# ---------------------------------------------------------------------------
# 全量入口
# ---------------------------------------------------------------------------
def reload_overlay(force: bool = False) -> dict:
    """热加载转译层全部变更。返回
    {reloaded, imported, patched, rebound, dropped, refreshed, skipped, failed}。

    高性能：基于内容哈希增量——没变的模块一个字节都不碰；首次调用只建立基线
    （应用启动时转译层已生效，已导入模块与磁盘一致，无需重载）。
    """
    global _BASELINE_READY
    names = _module_names()
    result = {"reloaded": [], "imported": [], "patched": [], "rebound": 0,
              "dropped": [], "refreshed": [], "skipped": [], "failed": []}
    if not names:
        return result

    try:
        from zhuzhu_Copilot.core import plugin_source
        root, _mode = plugin_source.apply_root()
    except Exception:
        root = None

    # 1) 死模块清理（回滚 create_file 后的残留）
    try:
        result["dropped"] = drop_dead_modules()
    except Exception:
        pass

    # 2) 增量：哈希变了 / 未导入过 的模块才动
    todo = []
    for name in names:
        if name in _HOT_SKIP:
            result["skipped"].append(name)
            continue
        src = (root / (name.replace(".", "/") + ".py")) if root is not None else None
        h = _file_hash(src) if src is not None else ""
        if not force and _BASELINE_READY and _HASHES.get(name) == h \
                and name in sys.modules:
            continue                                # 内容没变：跳过
        todo.append((name, h))

    baseline = not _BASELINE_READY
    imported = []
    for name, h in todo:
        if root is not None:
            _drop_pyc(root, name)
        if name not in sys.modules:
            try:
                importlib.import_module(name)
                imported.append(name)
                _HASHES[name] = h
            except Exception as e:
                result["failed"].append(f"{name}（{type(e).__name__}: {e}）")
            continue
        if baseline:
            # 首次调用只建基线：启动时已导入的模块与磁盘一致，重载纯属浪费
            _HASHES[name] = h
            continue
        r = reload_module(name)
        if r["ok"]:
            result["reloaded"].append(name)
            result["patched"].extend(f"{name}.{c}" for c in r["patched"])
            result["rebound"] += r["rebound"]
            _HASHES[name] = h
        else:
            result["failed"].append(f"{name}（{r['error']}）")
    _BASELINE_READY = True

    # 3) UI 刷新（主题 / 派生 QSS / 活实例）
    try:
        touched = result["reloaded"] + imported
        if touched:
            result["refreshed"] = _refresh_ui(touched)
    except Exception:
        pass
    return result


def needs_restart(result: dict = None) -> bool:
    """恒为 False：本引擎的设计目标就是"任何插件变更都不需要重启"。

    保留这个入口是为了让调用方语义清晰——如果热加载失败，策略是**自动回滚**，
    而不是留一个"重启后才生效"的半套变更。
    """
    return False


def summary(result: dict) -> str:
    """把 reload_overlay 的结果整理成给用户看的提示（绝不再提"重启"）。"""
    result = result or {}
    reloaded = result.get("reloaded") or []
    imported = result.get("imported") or []
    patched = result.get("patched") or []
    rebound = int(result.get("rebound") or 0)
    dropped = result.get("dropped") or []
    refreshed = result.get("refreshed") or []
    failed = result.get("failed") or []
    parts = []
    if reloaded:
        parts.append(f"热加载 {len(reloaded)} 个模块")
    if imported:
        parts.append(f"载入 {len(imported)} 个新模块")
    if patched:
        names = sorted({c.rsplit(".", 1)[-1] for c in patched})
        parts.append("热修补 " + "、".join(names[:4]) + (" 等类" if len(names) > 4 else ""))
    if rebound:
        parts.append(f"重绑 {rebound} 处旧引用")
    if dropped:
        parts.append(f"清理 {len(dropped)} 个已回滚模块")
    if refreshed:
        parts.append("；".join(refreshed[:3]))
    if failed:
        parts.append("失败：" + "；".join(failed[:3]))
    return "；".join(parts) if parts else "无需热加载"
