"""第一课验收脚本：运行 python stage1_demo.py 观察输出。"""
import time
from pathlib import Path

from PyQt6.QtCore import QSettings

import app_identity


def demo_paths() -> None:
    print("APP_SLUG       =", app_identity.APP_SLUG)
    print("APP_DISPLAY_NAME =", app_identity.APP_DISPLAY_NAME)
    print("data_root()    =", app_identity.data_root())
    print("temp_dir()     =", app_identity.temp_dir())
    print("DATA_DIR_NAME  =", app_identity.DATA_DIR_NAME)


def demo_cache() -> None:
    """连续调用 1000 次，观察缓存命中后的耗时。"""
    start = time.perf_counter()
    for _ in range(1000):
        app_identity.data_root()
    print(f"1000 次 data_root(): {(time.perf_counter() - start) * 1000:.2f} ms")

    app_identity.invalidate_data_root_cache()
    first = time.perf_counter()
    app_identity.data_root()
    print(f"清缓存后首次调用:   {(time.perf_counter() - first) * 1000:.2f} ms")


def demo_settings() -> None:
    """用 INI 文件演示 QSettings，避免往注册表写测试数据。"""
    ini = app_identity.temp_dir() / "demo_settings.ini"
    ini.parent.mkdir(parents=True, exist_ok=True)

    s = QSettings(str(ini), QSettings.Format.IniFormat)
    s.setValue("agent_theme", "light")
    s.sync()
    print("QSettings 读回 agent_theme =", s.value("agent_theme"))


if __name__ == "__main__":
    demo_paths()
    demo_cache()
    demo_settings()
