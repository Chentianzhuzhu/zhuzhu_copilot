"""安装脚本静态回归：完成页应用名、安装范围选择、机器级步骤的条件。

不编译安装包（编译依赖 Inno Setup 与 dist 产物），只检查脚本的关键契约，
防止后续改文案/加步骤时把这两个易错点改回去。
"""
import re
from pathlib import Path

INSTALLER = Path(__file__).resolve().parent.parent / "installer"
ISS = INSTALLER / "zhuzhu_Copilot.iss"


def _iss() -> str:
    return ISS.read_text(encoding="utf-8")


def test_launch_checkbox_uses_inno_param_not_python_placeholder():
    """完成页勾选项必须用 Inno 的 %1 参数传应用名，不能留 %(app)s。

    %(app)s 是 Python 风格占位符，Inno 不会替换 —— 英文模式完成页会露出
    半截占位符（用户反馈的「启动（%）」），看不到正确应用名。
    """
    text = _iss()
    assert "Description: \"{cm:TLaunchApp,{#MyAppName}}\"" in text
    launch_lines = [ln for ln in text.splitlines() if "TLaunchApp=" in ln]
    assert launch_lines, "缺少 TLaunchApp 自定义消息"
    for ln in launch_lines:
        assert "%(app)s" not in ln, f"自定义消息仍含 Python 占位符: {ln}"
        assert "%1" in ln, f"自定义消息应使用 Inno 参数 %1: {ln}"


def test_install_scope_choice_between_current_and_all_users():
    """安装向导必须让用户选择「仅为我安装 / 为所有用户安装」。"""
    text = _iss()
    assert re.search(r"PrivilegesRequired=lowest", text)
    assert re.search(r"PrivilegesRequiredOverridesAllowed=dialog", text)
    # 两种语言都要有安装范围选择页的内置文案，否则会回退到默认（中文）语言
    for name in ("English.isl", "ChineseSimplified.isl"):
        isl = (INSTALLER / name).read_text(encoding="utf-8")
        assert "PrivilegesRequiredOverrideAllUsers=" in isl, name
        assert "PrivilegesRequiredOverrideCurrentUser=" in isl, name


def test_machine_level_steps_only_run_in_admin_mode():
    """证书 / VC++ / System32 dll 等需要管理员权限的步骤，仅为我安装时必须跳过。"""
    text = _iss()
    run_section = text.split("[Run]", 1)[1].split("[UninstallRun]", 1)[0]
    machine = [ln for ln in run_section.splitlines()
               if ("certutil.exe" in ln or "vc_redist" in ln
                   or "d3dcompiler_47.dll" in ln) and "Filename:" in ln]
    assert len(machine) == 5, f"机器级步骤数量异常: {len(machine)}"
    for ln in machine:
        assert "Check: IsAdminInstallMode" in ln, f"缺少管理员模式闸门: {ln}"
