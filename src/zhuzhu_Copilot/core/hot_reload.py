"""转译层热加载：插件启用/停用后让变更尽量立即生效，不必重启应用。

转译层（`~/.zhuzhu_Copilot/source_overlay`）写入后，Python 里已导入的模块仍是
旧代码。这里按「浅 → 深」顺序重新导入/重载转译层模块：

  · 新增模块：直接 import，立即可用；
  · 替换模块：`importlib.reload` 原地重执行，其他模块通过 `模块.属性` 访问的
    会看到新实现（`from x import y` 的旧引用不会更新，这是 Python 语义）；
  · 新建的引擎实例 / 设置对话框会使用新代码，已存在的实例保持旧实现。

少数模块不做热重载（见 `_HOT_SKIP`）：它们持有进程级外观/窗口状态，重载会让
已存在的界面与常量脱节。这类插件的变更在下次启动时生效，UI 会明确提示。
"""

import importlib
import sys
from pathlib import Path

#: 不热重载的模块（包根 / 外观全局状态 / 巨型 UI / 插件引擎自身）
_HOT_SKIP = frozenset({
    "zhuzhu_Copilot",                    # 重载包根会让所有子模块引用脱节
    "zhuzhu_Copilot.ui.agent_panel",
    "zhuzhu_Copilot.ui.main_window",
    "zhuzhu_Copilot.ui.styles",
    "zhuzhu_Copilot.core.plugin_source",
    "zhuzhu_Copilot.core.copilot_plugins",
    "zhuzhu_Copilot.core.plugin_guard",
    "zhuzhu_Copilot.core.hot_reload",
})


def _module_names() -> list:
    """转译层里的模块名（按层级由浅到深排序：先重载被依赖的）。"""
    try:
        import _copilot_overlay_hook
        _copilot_overlay_hook.install()      # 首次启用时覆盖层目录刚创建
        rels = _copilot_overlay_hook.overlay_modules()
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


def _drop_pyc(root, name: str) -> None:
    """删掉该模块的字节码缓存：源文件 mtime 只精确到秒，同秒内改写会误用旧 pyc。"""
    try:
        src = root / (name.replace(".", "/") + ".py")
        pyc = importlib.util.cache_from_source(str(src))
        Path(pyc).unlink(missing_ok=True)
    except Exception:
        pass


def reload_overlay() -> dict:
    """重载转译层模块。返回 {reloaded, skipped, failed}（均为模块名列表）。"""
    names = _module_names()
    if not names:
        return {"reloaded": [], "skipped": [], "failed": []}
    try:
        importlib.invalidate_caches()
    except Exception:
        pass
    try:
        from zhuzhu_Copilot.core import plugin_source
        root, _mode = plugin_source.apply_root()
    except Exception:
        root = None
    reloaded, skipped, failed = [], [], []
    for name in names:
        if name in _HOT_SKIP:
            skipped.append(name)
            continue
        if root is not None:
            _drop_pyc(root, name)
        try:
            mod = sys.modules.get(name)
            if mod is not None:
                importlib.reload(mod)
            else:
                importlib.import_module(name)
            reloaded.append(name)
        except Exception as e:
            failed.append(f"{name}（{type(e).__name__}: {e}）")
    return {"reloaded": reloaded, "skipped": skipped, "failed": failed}


def summary(result: dict) -> str:
    """把 reload_overlay 的结果整理成一句给用户看的提示。"""
    result = result or {}
    reloaded = result.get("reloaded") or []
    skipped = result.get("skipped") or []
    failed = result.get("failed") or []
    parts = []
    if reloaded:
        parts.append(f"已热加载 {len(reloaded)} 个模块")
    if skipped:
        parts.append(f"{len(skipped)} 个界面模块需重启后生效")
    if failed:
        parts.append("加载失败：" + "；".join(failed[:3]))
    return "；".join(parts) if parts else "无需热加载"
