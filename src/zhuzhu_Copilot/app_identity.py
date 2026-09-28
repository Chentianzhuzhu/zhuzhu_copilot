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
    """
    new = _home() / DATA_DIR_NAME
    if new.is_dir():
        return new
    legacy = _home() / LEGACY_DATA_DIR_NAME
    return legacy if legacy.is_dir() else new


def temp_dir() -> Path:
    """进程级临时目录（日志 / 崩溃转储）。"""
    return Path(tempfile.gettempdir()) / APP_SLUG


def ensure_migrated() -> None:
    """迁移旧版遗留数据（进程内仅一次，任意环节失败都不影响启动）。"""
    global _migrated
    if _migrated:
        return
    _migrated = True
    _migrate_data_dir()
    _migrate_settings()


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
