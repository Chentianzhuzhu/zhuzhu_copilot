# -*- coding: utf-8 -*-
"""app_identity（身份标识单一数据源 + 旧版遗留数据迁移）的回归测试。

覆盖改名（WinAppMigrator → zhuzhu_Copilot）后的兼容契约：
  1. 旧用户数据目录自动改名迁移，内容完整、旧目录不再残留；
  2. 新目录已存在时不合并、不覆盖（绝不吞用户数据）；
  3. 旧注册表作用域的设置迁移到新作用域，老用户主题/模式不丢；
  4. 新作用域已有设置时不覆盖（用户升级后的改动优先）；
  5. 进程内只迁移一次；
  6. 迁移未执行/失败时 data_root() 回退旧目录；
  7. main.py 的迁移调用早于新手指南取样（否则老用户升级会重复弹指南）。

所有用例把家目录与 QSettings 后端隔离到临时目录（Ini 后端 + 临时路径），
不触碰真实用户数据与注册表。
"""
import os
from pathlib import Path

import pytest

from PyQt6.QtCore import QSettings

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from zhuzhu_Copilot import app_identity            # noqa: E402


def _ini_settings(scope):
    """与生产同作用域、但落到 Ini 临时目录的 QSettings（测试隔离用）。"""
    return QSettings(QSettings.Format.IniFormat, QSettings.Scope.UserScope, *scope)


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """家目录 + 设置存储隔离到临时目录，并把迁移状态复位。"""
    monkeypatch.setattr(app_identity, "_home", lambda: tmp_path)
    monkeypatch.setattr(app_identity, "_settings", _ini_settings)
    monkeypatch.setattr(app_identity, "_migrated", False)

    ini_dir = tmp_path / "ini"
    ini_dir.mkdir()
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(ini_dir))
    yield tmp_path


def _make_legacy_data_dir(home: Path) -> Path:
    legacy = home / app_identity.LEGACY_DATA_DIR_NAME
    (legacy / "agent").mkdir(parents=True)
    (legacy / "agent" / "tts.json").write_text("{}", encoding="utf-8")
    return legacy


# ---------------- 用户数据目录迁移 ----------------

def test_migrates_legacy_data_dir(isolated):
    legacy = _make_legacy_data_dir(isolated)
    app_identity.ensure_migrated()

    new = isolated / app_identity.DATA_DIR_NAME
    assert (new / "agent" / "tts.json").is_file(), "迁移后内容必须完整"
    assert not legacy.exists(), "旧目录必须整体改名，不留残留"
    assert app_identity.data_root() == new


def test_keeps_new_data_dir_when_both_exist(isolated):
    legacy = _make_legacy_data_dir(isolated)
    new = isolated / app_identity.DATA_DIR_NAME
    new.mkdir()
    (new / "keep.txt").write_text("x", encoding="utf-8")

    app_identity.ensure_migrated()

    assert (new / "keep.txt").is_file(), "新目录已有数据时不得被旧目录覆盖"
    assert legacy.is_dir(), "无权合并旧目录：保持原样，交由用户处理"


def test_data_root_falls_back_to_legacy_when_not_migrated(isolated):
    legacy = _make_legacy_data_dir(isolated)
    # 未调用 ensure_migrated（如工具脚本直跑）→ 退回旧目录，宁可留旧名也不丢数据
    assert app_identity.data_root() == legacy


# ---------------- 设置迁移 ----------------

def test_migrates_legacy_registry_settings(isolated):
    old = _ini_settings(app_identity.LEGACY_SETTINGS_SCOPE)
    old.setValue("agent_theme", "dark")
    old.setValue("agent_mode", "auto")
    old.sync()

    app_identity.ensure_migrated()

    new = _ini_settings(app_identity.SETTINGS_SCOPE)
    assert new.value("agent_theme") == "dark"
    assert new.value("agent_mode") == "auto"


def test_does_not_overwrite_existing_settings(isolated):
    new = _ini_settings(app_identity.SETTINGS_SCOPE)
    new.setValue("agent_theme", "light")
    new.sync()
    old = _ini_settings(app_identity.LEGACY_SETTINGS_SCOPE)
    old.setValue("agent_theme", "dark")
    old.sync()

    app_identity.ensure_migrated()

    assert _ini_settings(app_identity.SETTINGS_SCOPE).value("agent_theme") == "light"


def test_ensure_migrated_only_runs_once(isolated):
    old = _ini_settings(app_identity.LEGACY_SETTINGS_SCOPE)
    old.setValue("agent_theme", "dark")
    old.sync()

    app_identity.ensure_migrated()
    old.setValue("agent_theme", "light")     # 迁移之后再改旧键不应被带入
    old.sync()
    app_identity.ensure_migrated()

    assert _ini_settings(app_identity.SETTINGS_SCOPE).value("agent_theme") == "dark"


def test_qsettings_uses_current_scope(isolated):
    """对外唯一的设置入口指向新作用域（不再引用旧名）。"""
    assert app_identity.qsettings().organizationName() == app_identity.APP_SLUG
    assert app_identity.qsettings().applicationName() == app_identity.APP_SLUG


# ---------------- 入口顺序契约 ----------------

def test_main_migrates_before_onboarding_sampling():
    src = (Path(__file__).resolve().parents[1] / "src" / "main.py").read_text(encoding="utf-8")
    migrate_at = src.index("app_identity.ensure_migrated()")
    sample_at = src.index("capture_startup_state()")
    assert migrate_at < sample_at, "迁移必须早于新手指南取样，否则老用户升级会重复弹指南"
