"""扩展面板注册表：允许插件 / 工作流 / 代码注册自定义 UI 功能面板，主界面装配时动态挂载。

面板来源（自动发现 + 代码注册，同名面板按注册顺序后者覆盖前者）：
  1. code       register_panel(name, ...) 程序化注册（任意模块可调用，最灵活）
  2. workflow   <workflows>/<name>/panel.py  自动发现（工作流可选核心文件）
  3. plugin     <plugins>/<name>/panel.py    自动发现（插件可选携带，零侵入）

panel.py 约定（与工作流核心文件契约一致，缺失项用默认值）：
  TITLE   面板标题（缺省用插件/工作流名）
  WIDTH   面板固定宽度（缺省 300）
  HEIGHT  面板初始高度（缺省 240）
  def build_panel(owner) -> QWidget   必需；owner 为 AgentPanel 实例，返回可挂载控件

UI 装配流程：
  scan_panels()               # 扫描插件/工作流目录并注册（幂等，已注册跳过）
  list_panels() -> [dict]     # 枚举已注册面板
  build_panel_widgets(owner)  # 调用各工厂，返回 [(name, title, width, height, widget)]
"""

import importlib.util
import logging
import sys
from pathlib import Path

_log = logging.getLogger("winapp_migrator.agent_panels")

# 面板注册表：name -> {name, title, source, order, width, height, factory}
_REGISTRY: dict = {}


def register_panel(name: str, title: str, factory, source: str = "code",
                   order: int = 0, width: int = 300, height: int = 240,
                   path=None, mtime=None) -> bool:
    """注册自定义功能面板（稳定 API，供插件/工作流/代码调用）：
    - name：唯一标识（字母数字下划线，拒绝路径穿越字符）
    - title：面板标题（显示在浮窗顶部）
    - factory：build_panel(owner) -> QWidget 的工厂函数
    - source：来源标记（code/plugin/workflow）
    - order：排序权重（越小越靠左上方堆叠），同序按 name 字典序
    - width/height：浮窗初始尺寸（像素）
    - path/mtime：源文件路径与最后修改时间（工作流/插件面板热更新追踪用）
    同名重复注册即覆盖更新。返回是否注册成功。"""
    if not name or not callable(factory):
        return False
    if any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
           for c in name):
        _log.warning("扩展面板名非法（仅字母/数字/下划线/中划线）: %s", name)
        return False
    _REGISTRY[name] = {
        "name": name,
        "title": (title or "").strip() or name,
        "source": source,
        "order": int(order or 0),
        "width": max(120, int(width or 300)),
        "height": max(80, int(height or 240)),
        "factory": factory,
        # 源文件热更新追踪：workflow/plugin 面板记录源文件路径与 mtime，
        # 文件变更后 reload_panels 据此判断需要强制重载的面板。
        "path": path,
        "mtime": mtime,
    }
    return True


def unregister_panel(name: str) -> None:
    """注销面板（浮窗已创建时由 UI 侧在下次装配移除）"""
    _REGISTRY.pop(name, None)


def list_panels() -> list:
    """枚举已注册面板（按 order 升序、name 字典序）"""
    return [_panel_info(n) for n in _sorted_names()]


def _panel_info(name: str) -> dict:
    d = _REGISTRY[name]
    return {"name": name, "title": d["title"], "source": d["source"],
            "order": d["order"], "width": d["width"], "height": d["height"]}


def _sorted_names() -> list:
    return sorted(_REGISTRY,
                  key=lambda n: (_REGISTRY[n]["order"], n))


def build_panel_widgets(owner) -> list:
    """调用各面板工厂构建控件。返回 [(name, title, width, height, widget)]；
    工厂返回 None 或抛异常的面板跳过（不影响其他面板）。"""
    out = []
    for name in _sorted_names():
        d = _REGISTRY[name]
        try:
            widget = d["factory"](owner)
        except Exception as e:
            _log.warning("构建扩展面板失败: %s -> %s", name, e)
            widget = None
        out.append((name, d["title"], d["width"], d["height"], widget))
    return out


def build_panel(name: str, owner):
    """构建单一面板。返回 (title, width, height, widget)；面板不存在/构建失败返回
    (名称, 默认尺寸, None)。供热更新原位重建单个面板用。"""
    d = _REGISTRY.get(name)
    if not d:
        return name, None, None, None
    try:
        widget = d["factory"](owner)
    except Exception as e:
        _log.warning("重建扩展面板失败: %s -> %s", name, e)
        widget = None
    return d["title"], d["width"], d["height"], widget


def reload_panels() -> list:
    """热更新：检测工作流/插件面板源文件（panel.py）是否被修改，被修改的强制重载
    并返回变更面板名列表（未变更返回空列表）。由 UI 在主线程周期性调用——
    只重载源码被改动的面板，未改动/代码注册的面板保持不变，实现"改动即热更"。
    返回的面板将由 UI 原位重建窗口（保留几何与拖拽位置）。"""
    changed = []
    for name in list(_REGISTRY):
        d = _REGISTRY[name]
        path = d.get("path")
        if d.get("source") == "code" or not path:
            continue   # 代码注册/无源文件：无可热更新
        try:
            cur = Path(path).stat().st_mtime_ns
        except Exception:
            continue   # 源文件已不存在：无需重载
        if d.get("mtime") == cur:
            continue   # 未变更
        # 源文件已变更：重新加载模块替换 factory，保持原地更新
        if _load_panel_file(name, Path(path), d.get("source", "workflow")):
            changed.append(name)
            _log.info("扩展面板热更新: %s", name)
    return changed


# ------------------------------------------------------------
# 自动发现：扫描插件/工作流目录下的 panel.py 并注册
# ------------------------------------------------------------
def scan_panels() -> int:
    """扫描插件（启用中）与工作流（启用中）目录下的 panel.py，动态加载并注册。
    幂等：已注册的同名面板跳过（保留先注册者）。返回新增注册数。"""
    registered = set(_REGISTRY)
    new_count = 0
    # 1) 插件面板（enabled 才加载，禁用插件不挂载 UI）
    try:
        from winapp_migrator.core import agent_plugins
        for p in agent_plugins.list_plugins() or []:
            if not p.get("enabled", True):
                continue
            name = str(p.get("name") or "")
            if not name or name in registered:
                continue
            try:
                panel = Path(agent_plugins.plugin_dir(name)) / "panel.py"
            except Exception:
                continue
            if panel.is_file() and _load_panel_file(name, panel, "plugin"):
                registered.add(name)
                new_count += 1
    except Exception:
        pass
    # 2) 工作流面板（enabled 才加载）
    try:
        from winapp_migrator.core import agent_workflow
        for w in agent_workflow.list_workflows() or []:
            if not w.get("enabled", True):
                continue
            name = w["name"]
            if name in registered:
                continue
            panel = agent_workflow.workflow_dir(name) / "panel.py"
            if panel.is_file() and _load_panel_file(name, panel, "workflow"):
                registered.add(name)
                new_count += 1
    except Exception:
        pass
    return new_count


def _load_panel_file(name: str, path: Path, source: str) -> bool:
    """动态加载单个 panel.py 并注册（模块约定见模块 docstring）。"""
    try:
        mod_name = f"winapp_migrator_panel_{source}_{name.replace('-', '_')}"
        sys.modules.pop(mod_name, None)   # 清理旧模块，避免反复扫描时对象累积
        # 面板代码可带第三方 import：加载前先确保同目录 requirements.txt 依赖已安装
        try:
            from winapp_migrator.core import agent_deps
            agent_deps.ensure_dir_deps(path.parent)
        except Exception:
            pass
        spec = importlib.util.spec_from_file_location(mod_name, path)
        if spec is None or spec.loader is None:
            return False
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)
    except Exception as e:
        _log.warning("加载扩展面板文件失败: %s -> %s", path, e)
        return False
    factory = getattr(mod, "build_panel", None)
    if not callable(factory):
        _log.warning("扩展面板 %s/%s 未暴露 build_panel(owner)，已跳过", source, name)
        return False
    title = str(getattr(mod, "TITLE", "") or "").strip() or name
    try:
        width = int(getattr(mod, "WIDTH", 0) or 300)
    except Exception:
        width = 300
    try:
        height = int(getattr(mod, "HEIGHT", 0) or 240)
    except Exception:
        height = 240
    try:
        mtime = path.stat().st_mtime_ns
    except Exception:
        mtime = None
    return register_panel(name, title, factory, source=source,
                          width=width, height=height,
                          path=str(path), mtime=mtime)
