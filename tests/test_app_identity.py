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


# ---------------- 用户工作流脚本里的旧包名改写 ----------------
# 改名只覆盖了随包分发的代码，工作流/插件/技能脚本是**用户数据**（家目录下），
# 没人改写它们。不改写就会在加载时 ModuleNotFoundError → 静默回退内置实现，
# 用户自定义全部失效；同一批文件还会被快照成随包分发的种子，新装机器同样踩到。

def _make_legacy_workflow(home: Path) -> Path:
    wf = home / app_identity.DATA_DIR_NAME / "workflows" / "my_flow"
    wf.mkdir(parents=True)
    (wf / "agent.py").write_text(
        "from winapp_migrator.core import agent_subagent\n"
        "UA = 'WinAppMigrator/CordisWorkflow'\n"
        "DIR = '~/.winapp_migrator/plugins/'\n", encoding="utf-8")
    (wf / "workflow.json").write_text('{"name": "my_flow"}', encoding="utf-8")
    return wf


def test_rewrite_legacy_names_replaces_both_spellings():
    old = app_identity.legacy_package_name()
    src = f"{old}.core + {app_identity.LEGACY_SLUG} + .{old}"
    out = app_identity.rewrite_legacy_names(src)
    assert old not in out and app_identity.LEGACY_SLUG not in out
    assert out == (f"{app_identity.APP_SLUG}.core + {app_identity.APP_SLUG}"
                   f" + .{app_identity.APP_SLUG}")
    # 幂等：再改一次不变
    assert app_identity.rewrite_legacy_names(out) == out


def test_migrates_legacy_package_names_in_user_workflows(isolated):
    wf = _make_legacy_workflow(isolated)
    app_identity.ensure_migrated()

    agent = (wf / "agent.py").read_text(encoding="utf-8")
    assert app_identity.legacy_package_name() not in agent
    assert app_identity.LEGACY_SLUG not in agent
    assert f"from {app_identity.APP_SLUG}.core import agent_subagent" in agent
    assert f"~/.{app_identity.APP_SLUG}/plugins/" in agent, "数据目录引用也要跟着换名"
    # 不含旧名的文件不得被无谓改写
    assert (wf / "workflow.json").read_text(encoding="utf-8") == '{"name": "my_flow"}'


def test_rewrite_preserves_line_endings(isolated):
    """改写旧包名不得顺带改掉文件行尾风格。

    曾经的坑：用 Path.write_text 默认行为写回，会把 LF 文件整体写成 CRLF（Windows），
    一个词级改动就变成整文件 diff，也会粗暴改掉用户文件的行尾。
    """
    root = isolated / app_identity.DATA_DIR_NAME / "workflows" / "lf_flow"
    root.mkdir(parents=True)
    fp = root / "agent.py"
    fp.write_bytes(b"from winapp_migrator.core import x\nfrom winapp_migrator import y\n")

    n = app_identity.rewrite_legacy_names_in_tree(isolated / app_identity.DATA_DIR_NAME
                                                / "workflows")

    raw = fp.read_bytes()
    assert n == 1
    assert b"\r\n" not in raw, "LF 文件被写成了 CRLF"
    assert raw.count(b"\n") == 2
    assert app_identity.legacy_package_name().encode() not in raw


def test_workflow_code_migration_survives_missing_dirs(isolated):
    """数据目录里没有 workflows/plugins/skills 时不得抛错（全新安装即如此）。"""
    app_identity.ensure_migrated()          # 不应抛异常


def test_suite_never_points_data_root_at_real_user_dir():
    """全局隔离契约：测试进程里 data_root() 绝不允许落在**用户真实家目录**下。

    背景（真实缺陷，用户可见）：`test_ai_turn_wrapper.py` 构造真实 AgentPanel，面板会
    恢复「上次会话」= 用户真实会话，然后用例灌入 `_on_reasoning(CHUNK)`×24 并置
    `_task_active=True` → 面板自动落盘把这段**测试夹具文本写进用户真实会话文件**，
    于是用户每次重启应用都看到一段假「思考过程」。隔离由 conftest._isolated_data_root
    统一提供，这条用例守住那个接缝：一旦有人去掉隔离（或新写用例绕过它），此处立刻变红。
    """
    # 不能只判「家目录是否为祖先」：Windows 的临时目录本身就在家目录下
    # （%LOCALAPPDATA%\Temp），那样判会把正确的隔离误报为污染。直接比对真实数据目录。
    real_home = Path.home()
    root = app_identity.data_root()
    assert root not in (real_home / app_identity.DATA_DIR_NAME,
                        real_home / app_identity.LEGACY_DATA_DIR_NAME), \
        f"测试把数据目录指向了用户真实目录：{root}（测试绝不能碰用户数据）"


# ---------------- 入口顺序契约 ----------------

def test_main_migrates_before_onboarding_sampling():
    src = (Path(__file__).resolve().parents[1] / "src" / "main.py").read_text(encoding="utf-8")
    migrate_at = src.index("app_identity.ensure_migrated()")
    sample_at = src.index("capture_startup_state()")
    assert migrate_at < sample_at, "迁移必须早于新手指南取样，否则老用户升级会重复弹指南"
