# -*- coding: utf-8 -*-
"""应用身份的单一数据源：包名 / 显示名 / 注册表作用域 / 用户数据目录 / 临时目录。

所有「代码层标识」集中在此定义，避免散落硬编码；并承担旧版（WinAppMigrator）
遗留数据的**一次性自动迁移**——注册表设置与用户数据目录，保证老用户升级后主题、
执行模式、工作流、Agent、会话与密钥不丢失。

迁移由应用入口 `main.py` 在最早时机显式调用 `ensure_migrated()` 触发（不在
import 或取路径时隐式执行），避免测试/工具脚本产生改名或写注册表的副作用。
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time
from pathlib import Path

# ---------------- 当前身份标识 ----------------
APP_SLUG = "zhuzhu_Copilot"           # 代码层标识：包名 / 注册表 / 数据目录 / 构建产物
APP_DISPLAY_NAME = "zhuzhu Copilot"   # 用户可见名称：exe / 安装包 / 窗口标题
PROCESS_NAME = APP_DISPLAY_NAME       # 进程名（= exe 名），供自识别（如内存优化保护自身）

DATA_DIR_NAME = "." + APP_SLUG        # 用户数据目录名（家目录下）

# QSettings 的 (organization, application) 作用域
SETTINGS_SCOPE = (APP_SLUG, APP_SLUG)

# ---------------- 旧版标识（迁移来源，勿删） ----------------
LEGACY_SLUG = "WinAppMigrator"
LEGACY_DATA_DIR_NAME = ".winapp_migrator"
LEGACY_SETTINGS_SCOPE = (LEGACY_SLUG, LEGACY_SLUG)

# 迁移完成标记（写在新作用域内，避免重复迁移）
_MIGRATION_MARKER = "app_identity_migrated_from"

_migrated = False

# 数据目录解析缓存 {家目录字符串: (检查时刻, 数据目录)}；见 data_root() 的性能说明
_ROOT_CACHE: dict = {}
_ROOT_TTL_S = 5.0


def _home() -> Path:
    """用户家目录（测试可替换的接缝）。"""
    return Path.home()


def _settings(scope):
    """按作用域构造 QSettings（测试接缝：可注入 Ini 后端，隔离到临时目录）。"""
    from PyQt6.QtCore import QSettings
    return QSettings(*scope)


def qsettings():
    """当前作用域的 QSettings 实例。"""
    return _settings(SETTINGS_SCOPE)


def data_root() -> Path:
    """用户数据目录。

    正常迁移完成后即新目录；迁移未发生或失败而旧目录仍在时回退旧目录，
    宁可临时沿用旧名，也不让用户的工作流 / 会话 / 密钥失效。

    性能：本函数位于几乎全部数据路径的构造链上（工作流/会话/设置/日志），每轮引擎
    循环会走到数十次；每次 `is_dir()` 是一次 stat（Windows 杀软下 ≈0.5ms），实测占
    长任务每轮预算的一大块。故按家目录缓存解析结果（短 TTL + 迁移后显式失效）：
    数据目录在进程运行期内是稳定的，用户改名/迁移走 ensure_migrated → 显式刷新。
    """
    home = _home()
    key = str(home)
    now = time.monotonic()
    hit = _ROOT_CACHE.get(key)
    if hit is not None and now - hit[0] < _ROOT_TTL_S:
        return hit[1]
    new = home / DATA_DIR_NAME
    if new.is_dir():
        root = new
    else:
        legacy = home / LEGACY_DATA_DIR_NAME
        root = legacy if legacy.is_dir() else new
    _ROOT_CACHE[key] = (now, root)
    return root


def invalidate_data_root_cache() -> None:
    """失效数据目录解析缓存（目录改名/迁移、首启创建后调用；测试切换家目录亦可用）"""
    _ROOT_CACHE.clear()


def temp_dir() -> Path:
    """进程级临时目录（日志 / 崩溃转储）。"""
    return Path(tempfile.gettempdir()) / APP_SLUG


def legacy_package_name() -> str:
    """旧包名（= 旧数据目录名去掉前导点），由 LEGACY_* 常量派生，不另设字面量。"""
    return LEGACY_DATA_DIR_NAME.lstrip(".")


def rewrite_legacy_names(text: str) -> str:
    """把文本里的旧身份引用改写成当前身份（旧包名 / 旧显示名 → APP_SLUG）。

    供两处使用：①老用户首启时改写其工作流/插件/技能脚本；②构建期快照工作流种子。
    改的是**标识符**，不是业务逻辑，因此整串替换即可（含 ``.winapp_migrator`` 这类
    数据目录引用 —— 新目录名正是 "." + APP_SLUG）。
    """
    for old in (legacy_package_name(), LEGACY_SLUG):
        if old:
            text = text.replace(old, APP_SLUG)
    return text


# 会被改写的文本文件类型（用户工作流/插件/技能脚本 + 文档/配置）
_LEGACY_TEXT_EXT = frozenset({
    ".py", ".json", ".md", ".txt", ".toml", ".cfg", ".ini", ".yaml", ".yml",
})
_LEGACY_SKIP_DIRS = frozenset({
    "__pycache__", ".git", ".venv", "venv", "node_modules", ".mypy_cache",
})


def rewrite_legacy_names_in_tree(root: Path) -> int:
    """把 root 下文本文件里的旧身份引用改写成当前身份，返回实际改写的文件数。

    只写「确实含旧名」的文件（改名是幂等的），读不到/写不动就跳过 —— 绝不因为迁移
    失败而影响启动。

    **读写都带 `newline=""`**：不翻译行尾。用 `Path.write_text` 的默认行为会把 LF 文件
    整体写成 CRLF（Windows），一个词级改动就变成整文件 diff，也会粗暴改掉用户文件的
    行尾风格。
    """
    if not root.is_dir():
        return 0
    changed = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _LEGACY_SKIP_DIRS]
        for fn in filenames:
            if Path(fn).suffix.lower() not in _LEGACY_TEXT_EXT:
                continue
            fp = Path(dirpath) / fn
            try:
                with open(fp, "r", encoding="utf-8", newline="") as f:
                    src = f.read()
            except (OSError, UnicodeDecodeError):
                continue
            fixed = rewrite_legacy_names(src)
            if fixed == src:
                continue
            try:
                with open(fp, "w", encoding="utf-8", newline="") as f:
                    f.write(fixed)
                changed += 1
            except OSError:
                pass
    return changed


def _migrate_workflow_code() -> None:
    """改写用户工作流 / 插件 / 技能脚本里的旧包名引用。

    这些是**用户数据**（家目录下），改名只覆盖了随包分发的代码，没人动过它们。
    不改写的话，运行时加载器 exec 这些文件时 ModuleNotFoundError，只能回退内置默认
    实现 —— 用户自定义静默失效（日志里只有一条 warning）。同一批文件还会被快照成
    随包分发的工作流种子，所以新装机器同样会踩到。
    """
    try:
        root = data_root()
        for sub in ("workflows", "plugins", "skills"):
            rewrite_legacy_names_in_tree(root / sub)
    except Exception:
        pass          # 迁移失败不影响启动（文件保持原样，行为与迁移前一致）


def ensure_migrated() -> None:
    """迁移旧版遗留数据（进程内仅一次，任意环节失败都不影响启动）。"""
    global _migrated
    if _migrated:
        return
    _migrated = True
    _migrate_data_dir()          # 先改目录名，data_root() 才会指向新目录
    invalidate_data_root_cache()  # 目录改名后立即刷新解析缓存
    _migrate_settings()
    _migrate_workflow_code()     # 依赖 data_root()，必须在目录改名之后


def _migrate_data_dir() -> None:
    """旧数据目录改名到新目录：同盘 rename 瞬时完成，跨盘/被占用时回退为复制。"""
    new = _home() / DATA_DIR_NAME
    legacy = _home() / LEGACY_DATA_DIR_NAME
    if new.exists() or not legacy.is_dir():
        return
    try:
        os.rename(legacy, new)
        return
    except OSError:
        pass
    try:
        shutil.copytree(legacy, new)
    except Exception:
        pass          # 失败时 data_root() 自动回退旧目录，绝不丢数据


def _migrate_settings() -> None:
    """把旧注册表作用域下的全部设置搬到新作用域（仅当新作用域为空时执行）。"""
    try:
        new = _settings(SETTINGS_SCOPE)
        if new.contains(_MIGRATION_MARKER) or new.allKeys():
            return
        legacy = _settings(LEGACY_SETTINGS_SCOPE)
        keys = legacy.allKeys()
        if not keys:
            return
        for key in keys:
            new.setValue(key, legacy.value(key))
        new.setValue(_MIGRATION_MARKER, LEGACY_SLUG)
        new.sync()
    except Exception:
        pass
