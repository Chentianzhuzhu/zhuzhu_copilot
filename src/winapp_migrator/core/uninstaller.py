"""强力卸载：优先调用应用自带卸载器，再删除根目录、注册表关联项、快捷方式、数据/存档/聊天记录目录"""
import os
import shlex
import subprocess
import winreg
from pathlib import Path
from typing import List, Optional, Callable

from winapp_migrator.utils.helpers import setup_logging, safe_remove
from winapp_migrator.core.app_scanner import AppInfo
from winapp_migrator.core.data_dirs import detect_data_dirs
from winapp_migrator.core.registry import scan_app_entries, remove_app_entries
from winapp_migrator.core.shortcut import scan_shortcuts, remove_shortcuts
from winapp_migrator.core.uwp import UWPManager
from winapp_migrator.core.migration import _terminate_processes, is_360_self_protection, force_delete_directory

logger = setup_logging()

# 注册表卸载项扫描位置（含 32 位程序的 WOW6432Node 项）
_UNINSTALL_LOCATIONS = [
    (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
]
# 安装目录下常见的自带卸载器文件名
_COMMON_UNINSTALLERS = (
    "unins000.exe", "unins001.exe", "unins1.exe",
    "uninstall.exe", "uninst.exe", "uninstaller.exe",
)
# 自带卸载器最长等待时间（可能弹窗等待用户交互）
_NATIVE_TIMEOUT_SEC = 180


class UninstallPlan:
    """卸载清单：确认对话框中展示的将删除内容"""

    def __init__(self, app: AppInfo):
        self.app = app
        self.is_uwp = app.app_type == "UWP"
        self.root = app.install_location
        self.package_name = app.package_name or app.name
        self.data_dirs: List[Path] = []
        self.registry_entries: List = []
        self.shortcuts: List[str] = []
        # 应用自带卸载器：(exe 路径, 参数列表) 或 None
        self.native_uninstaller: Optional[tuple[str, List[str]]] = None


def _parse_uninstall_command(cmd: str) -> Optional[tuple[str, List[str]]]:
    """解析 UninstallString → (exe 路径, 参数列表)；支持带引号路径"""
    if not cmd:
        return None
    try:
        parts = shlex.split(cmd, posix=False)
    except ValueError:
        parts = cmd.split()
    if not parts:
        return None
    exe = parts[0].strip('"')
    if not Path(exe).suffix.lower().endswith((".exe", ".com", ".bat", ".cmd", ".msi")):
        return None
    return exe, parts[1:]


def _query_native_uninstaller(app: AppInfo) -> Optional[tuple[str, List[str]]]:
    """扫描注册表卸载项：按 InstallLocation / UninstallString 路径 / DisplayName 匹配"""
    root_str = str(app.install_location).lower().rstrip("\\")
    name_str = (app.name or "").lower().strip()

    def _norm(p: str) -> str:
        return os.path.expandvars(p).lower().rstrip("\\")

    def _loc_matches(loc: str) -> bool:
        return bool(loc) and _norm(str(loc)) == root_str

    def _exe_matches(exe: str) -> bool:
        """UninstallString 指向的卸载器所在目录 == 安装根目录"""
        try:
            return str(Path(exe).resolve().parent).lower().rstrip("\\") == root_str
        except OSError:
            return _norm(str(Path(exe).parent)) == root_str

    def _name_matches(dn: str) -> bool:
        return bool(name_str) and bool(dn) and dn.lower().strip() == name_str

    for hive, key_path in _UNINSTALL_LOCATIONS:
        try:
            base = winreg.OpenKey(hive, key_path)
        except OSError:
            continue
        try:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(base, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(base, sub) as k:
                        def _qv(name):
                            try:
                                v, _ = winreg.QueryValueEx(k, name)
                                return v
                            except OSError:
                                return None
                        loc = _qv("InstallLocation")
                        us = _qv("UninstallString")
                        dn = _qv("DisplayName")
                    cmd = _parse_uninstall_command(us)
                    if not cmd:
                        continue
                    if _loc_matches(loc) or _exe_matches(cmd[0]) or _name_matches(dn):
                        return cmd
                except OSError:
                    continue
        finally:
            winreg.CloseKey(base)
    return None


def _find_uninstaller_in_dir(app: AppInfo) -> Optional[tuple[str, List[str]]]:
    """在安装根目录及一层子目录查找常见自带卸载器"""
    root = Path(app.install_location)
    if not root.is_dir():
        return None
    for name in _COMMON_UNINSTALLERS:
        p = root / name
        if p.is_file():
            return str(p), []
    try:
        for sub in root.iterdir():
            if sub.is_dir():
                for name in _COMMON_UNINSTALLERS:
                    p = sub / name
                    if p.is_file():
                        return str(p), []
    except OSError:
        pass
    return None


def _run_native_uninstaller(cmd: tuple[str, List[str]], cwd: str) -> tuple[bool, str]:
    """运行自带卸载器并等待其结束；超时则强制终止并继续清理"""
    exe, args = cmd
    try:
        proc = subprocess.Popen([exe, *args], cwd=cwd, close_fds=True)
    except OSError as e:
        return False, f"启动自带卸载器失败: {e}"
    try:
        proc.wait(timeout=_NATIVE_TIMEOUT_SEC)
        if proc.returncode != 0:
            return False, f"自带卸载器退出码 {proc.returncode}"
        return True, "自带卸载器执行完成"
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except OSError:
            pass
        return False, f"自带卸载器 {_NATIVE_TIMEOUT_SEC}s 未结束，已强制终止"


class Uninstaller:
    def build_plan(self, app: AppInfo) -> UninstallPlan:
        """构建卸载清单（UWP 只记包名；Win32 检测自带卸载器、数据目录、注册表、快捷方式）"""
        plan = UninstallPlan(app)
        if plan.is_uwp:
            return plan
        plan.native_uninstaller = _query_native_uninstaller(app) or _find_uninstaller_in_dir(app)
        plan.data_dirs = detect_data_dirs(app)
        hints = [app.publisher, Path(app.executable or "").stem, app.name]
        plan.registry_entries = scan_app_entries(app.install_location, app.name, hints)
        plan.shortcuts = scan_shortcuts(app.install_location)
        return plan

    def uninstall(
        self,
        plan: UninstallPlan,
        progress_callback: Optional[Callable[[int, str], None]] = None,
    ) -> dict:
        def notify(percent: int, msg: str):
            logger.info("[%d%%] %s", percent, msg)
            if progress_callback:
                progress_callback(percent, msg)

        if plan.is_uwp:
            notify(30, "正在卸载 UWP 应用...")
            ok, msg = UWPManager.uninstall_package(plan.package_name)
            notify(100, "完成")
            return {
                "success": ok,
                "message": msg,
                "removed_dirs": [],
                "registry_removed": 0,
                "shortcuts_removed": 0,
                "failed": [] if ok else [msg],
            }

        result = {
            "success": True,
            "message": "强力卸载完成",
            "removed_dirs": [],
            "registry_removed": 0,
            "shortcuts_removed": 0,
            "failed": [],
            "native_uninstaller": None,
            "native_ok": None,
        }

        notify(10, "强制结束占用进程...")
        blocked = _terminate_processes(plan.root)
        result["blocked"] = blocked
        result["blocked_360"] = is_360_self_protection(blocked)
        if blocked:
            result["failed"].append("以下进程无法自动结束（可能受保护）: " + ", ".join(blocked))

        notify(15, "调用应用自带卸载器...")
        if plan.native_uninstaller:
            result["native_uninstaller"] = " ".join([plan.native_uninstaller[0], *plan.native_uninstaller[1]]).strip()
            ok, msg = _run_native_uninstaller(plan.native_uninstaller, str(plan.root))
            result["native_ok"] = ok
            if not ok:
                result["failed"].append(msg)

        notify(30, "删除快捷方式...")
        result["shortcuts_removed"] = remove_shortcuts(plan.root)

        notify(45, "清理注册表关联项...")
        result["registry_removed"] = remove_app_entries(plan.registry_entries)

        notify(65, "删除数据/存档/聊天记录目录...")
        for d in plan.data_dirs:
            if safe_remove(d):
                result["removed_dirs"].append(str(d))
            else:
                result["failed"].append(f"数据目录删除失败: {d}")

        notify(85, "删除安装根目录...")
        if safe_remove(plan.root):
            result["removed_dirs"].append(str(plan.root))
        else:
            result["failed"].append(f"根目录删除失败: {plan.root}")

        notify(100, "完成")
        return result
