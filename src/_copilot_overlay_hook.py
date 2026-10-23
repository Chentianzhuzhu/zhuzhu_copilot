"""Copilot 源码覆盖层导入钩子（独立模块，禁止依赖 zhuzhu_Copilot 包）。

背景：应用以 PyInstaller 打包分发（安装目录里没有源码），「源码级 Copilot 插件」
不能把代码插进安装目录。改为写入用户数据目录下的**源码覆盖层**：

    ~/.zhuzhu_Copilot/source_overlay/zhuzhu_Copilot/<原相对路径>

并通过本钩子让**任何 zhuzhu_Copilot.* 的导入优先命中覆盖层**：

  · 覆盖层有同名 .py        → 导入覆盖层版本（改主题 / 自定义 Agent / 自定义工具 /
                              自定义语言 / 增删替换任何模块，全部走这条路）
  · 覆盖层没有              → 原样回落打包内（或源码目录）的版本，行为与未装插件一致
  · 覆盖层里的新文件        → 成为可被正常 import 的新模块（create_file 的落地形态）

因此**任何人都能把 Copilot 自定义成想要的样子**，且在打包版上同样生效
（应用/回滚插件后重启应用即生效）。

用法：src/main.py 在 `from zhuzhu_Copilot import ...` **之前**调用 install()。
本模块因此不能 import zhuzhu_Copilot 任何子模块（否则循环），
家目录解析在此处按 app_identity 的同名规则内联实现（旧版数据目录回退）。
"""

import importlib.abc
import importlib.util
import os
import sys
from pathlib import Path

#: 覆盖层根目录名（用户数据目录下）
OVERLAY_DIRNAME = "source_overlay"
#: 数据目录名（与 zhuzhu_Copilot/app_identity.py 的 DATA_DIR_NAME 保持一致）
_DATA_DIR_NAME = ".zhuzhu_Copilot"
_LEGACY_DATA_DIR_NAME = ".winapp_migrator"
#: 只接管本包的导入
_PACKAGE = "zhuzhu_Copilot"
#: 打包态随应用分发的源码快照目录名（见 build/zhuzhu_Copilot.spec 的 datas）
_SNAPSHOT_DIR = "_copilot_src"

_installed = False


def overlay_dir() -> Path:
    """覆盖层根目录（不存在不创建，由插件应用时创建）。"""
    home = Path(os.path.expanduser("~"))
    new = home / _DATA_DIR_NAME
    if new.is_dir():
        root = new
    else:
        legacy = home / _LEGACY_DATA_DIR_NAME
        root = legacy if legacy.is_dir() else new
    return root / OVERLAY_DIRNAME


def _original_root() -> Path | None:
    """应用源码/打包根目录（含 zhuzhu_Copilot/ 的那一层）；找不到返回 None。

    源码运行：本文件位于 src/ 下，src/ 即根（zhuzhu_Copilot 为命名空间包，
    只判目录存在，不判 __init__.py）。
    打包运行：随包分发的源码快照（spec datas 注入），onedir 平铺布局下位于
    <exe>/_copilot_src，带 _internal 的布局位于 <exe>/_internal/_copilot_src，
    onefile 位于 sys._MEIPASS/_copilot_src。
    """
    here = Path(__file__).resolve().parent
    if (here / _PACKAGE).is_dir():
        return here
    if getattr(sys, "frozen", False):
        bases = [getattr(sys, "_MEIPASS", None),
                 str(Path(sys.executable).parent / "_internal"),
                 str(Path(sys.executable).parent)]
        for base in bases:
            if not base:
                continue
            cand = Path(base) / _SNAPSHOT_DIR
            if (cand / _PACKAGE).is_dir():
                return cand
    return None


def _original_file(rel_parts) -> Path | None:
    """打包/源码树中与覆盖层模块对应的原文件路径（不存在返回 None）。

    rel_parts 为模块名分段（如 ['zhuzhu_Copilot','app_identity']）或以
    '__init__.py' 结尾的包分段；模块分段自动补 .py 后缀。
    """
    root = _original_root()
    if root is None:
        return None
    parts = [str(x) for x in rel_parts]
    if parts and not parts[-1].endswith(".py"):
        parts[-1] += ".py"
    p = root.joinpath(*parts)
    return p if p.is_file() else None


class _OverlayFinder(importlib.abc.MetaPathFinder):
    """meta_path 最前置查找器：zhuzhu_Copilot.* 命中覆盖层时按文件加载。

    `__file__` 指回**原版路径**（若存在）：大量模块用
    `Path(__file__).parent` 定位包内资源（技能目录 / 语言包 / 配置），
    指回覆盖层会让资源解析集体落空。`__path__`（包）仍以覆盖层目录优先，
    保证覆盖层内新增的子模块可被 import。
    """

    def find_spec(self, fullname, path=None, target=None):
        if not fullname.startswith(_PACKAGE + ".") and fullname != _PACKAGE:
            return None
        rel = fullname.split(".")
        base = overlay_dir().joinpath(*rel)
        is_pkg = False
        cand = None
        init = base / "__init__.py"
        mod = base.parent / (base.name + ".py")
        if init.is_file():
            cand, is_pkg = init, True
        elif mod.is_file():
            cand = mod
        if cand is None:
            return None          # 未覆盖 → 交回正常导入链（打包内/源码目录版本）
        try:
            spec = importlib.util.spec_from_file_location(
                fullname, str(cand),
                submodule_search_locations=[str(base)] if is_pkg else None)
        except Exception:
            return None
        if spec is None or spec.loader is None:
            return None
        orig = _original_file(rel + (["__init__.py"] if is_pkg else []))
        if orig is not None:
            # 保持资源解析锚点指向原版文件（详见类 docstring）
            _orig_cache[fullname] = str(orig)
            spec.loader = _LoaderProxy(spec.loader, fullname)
        return spec


#: fullname → 原版文件路径（loader 执行完后把 __file__ 改回去）
_orig_cache: dict = {}


class _LoaderProxy:
    """loader 代理：模块执行成功后把 `__file__` 指回原版路径。

    不继承具体 loader 类型（SourceFileLoader / ExtensionFileLoader 构造签名
    各异，子类化脆弱），改用组合代理，只实现 import 协议必需的三个方法。
    """

    def __init__(self, inner, fullname: str):
        self._inner = inner
        self._fullname = fullname

    def create_module(self, spec):
        return self._inner.create_module(spec)

    def exec_module(self, module):
        self._inner.exec_module(module)
        try:
            module.__file__ = _orig_cache.get(self._fullname, module.__file__)
        except Exception:
            pass

    def is_package(self, fullname):
        f = getattr(self._inner, "is_package", None)
        return bool(f(fullname)) if f else False


def install() -> bool:
    """安装覆盖层导入钩子（幂等；必须在导入 zhuzhu_Copilot 包之前调用）。"""
    global _installed
    if _installed:
        return True
    if not overlay_dir().is_dir():
        return False          # 没有覆盖层 → 零侵入，行为与原来完全一致
    try:
        importlib.invalidate_caches()
    except Exception:
        pass
    sys.meta_path.insert(0, _OverlayFinder())
    _installed = True
    return True


def overlay_modules() -> list:
    """覆盖层内的模块清单（相对路径，如 zhuzhu_Copilot/ui/styles.py；诊断用）。"""
    out = []
    d = overlay_dir()
    if not d.is_dir():
        return out
    for p in sorted(d.rglob("*.py")):
        try:
            out.append(str(p.relative_to(d)).replace("\\", "/"))
        except OSError:
            continue
    return out
