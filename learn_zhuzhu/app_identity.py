"""应用身份层：包名、显示名、用户数据目录、临时目录的唯一来源。

原项目文件：src/zhuzhu_Copilot/app_identity.py（约 220 行）
本课只保留主干，迁移逻辑放到下一课。
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

# ---------- 常量：所有“名字”只在这里写一次 ----------
APP_SLUG = "zhuzhu_Copilot"          # 代码层标识：包名 / 注册表 / 目录名
APP_DISPLAY_NAME = "zhuzhu Copilot"  # 用户可见名称：窗口标题 / exe 名

DATA_DIR_NAME = "." + APP_SLUG       # 用户数据目录：家目录下 .zhuzhu_Copilot
SETTINGS_SCOPE = (APP_SLUG, APP_SLUG)  # QSettings 的 (organization, application)

# 旧版目录名：老用户升级后要优先沿用，避免工作流/会话丢失
LEGACY_DATA_DIR_NAME = ".winapp_migrator"

# ---------- 模块级缓存：整个进程共享这一份 ----------
# 键是“家目录字符串”，值是 (上次检查时刻, 解析出的目录)
_ROOT_CACHE: dict[str, tuple[float, Path]] = {}
_ROOT_TTL_S = 5.0


def _home() -> Path:
    """用户家目录。单独抽成函数，测试时可以替换。"""
    return Path.home()


def data_root() -> Path:
    """返回用户数据目录，带 5 秒缓存。

    为什么缓存：这个函数位于几乎所有数据路径的构造链上，
    每次 is_dir() 都是一次磁盘 stat，长任务里会被调用几十次。
    """
    home = _home()
    key = str(home)
    now = time.monotonic()

    hit = _ROOT_CACHE.get(key)
    if hit is not None and now - hit[0] < _ROOT_TTL_S:
        return hit[1]

    new = home / DATA_DIR_NAME
    legacy = home / LEGACY_DATA_DIR_NAME

    if new.is_dir():
        root = new
    elif legacy.is_dir():
        root = legacy      # 迁移没完成/失败：宁可沿用旧目录，也不让数据“消失”
    else:
        root = new         # 首次运行：返回新目录（调用方负责创建）

    _ROOT_CACHE[key] = (now, root)
    return root


def invalidate_data_root_cache() -> None:
    """清空缓存。目录改名、迁移完成、测试切换家目录时调用。"""
    _ROOT_CACHE.clear()


def temp_dir() -> Path:
    """进程级临时目录：日志 / 崩溃转储。"""
    return Path(tempfile.gettempdir()) / APP_SLUG


def qsettings():
    """当前作用域的 QSettings 实例。

    延迟导入：只有真正需要读写设置时才 import PyQt6，
    让命令行脚本和单元测试不必启动 Qt。
    """
    from PyQt6.QtCore import QSettings
    return QSettings(*SETTINGS_SCOPE)
