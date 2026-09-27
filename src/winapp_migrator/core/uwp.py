import os
import json
import subprocess
from pathlib import Path
from typing import List, Tuple

from winapp_migrator.utils.helpers import setup_logging
from winapp_migrator.core.permissions import take_ownership
from winapp_migrator.core.migration import migrate_folder

logger = setup_logging()

UWPPackage = dict

# 无控制台程序运行子进程时不弹黑窗口
NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0

class UWPManager:
    @staticmethod
    def list_packages() -> List[UWPPackage]:
        try:
            result = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "Get-AppxPackage | Select-Object Name, PackageFullName, Publisher, "
                    "InstallLocation, PackageFamilyName, Version | ConvertTo-Json -Compress",
                ],
                capture_output=True,
                text=True,
                check=False,
                encoding="utf-8",
                errors="ignore",
                creationflags=NO_WINDOW,
            )
            if result.returncode != 0:
                logger.warning("获取UWP列表失败: %s", result.stderr)
                return []
            data = json.loads(result.stdout)
            if isinstance(data, dict):
                return [data]
            return data
        except Exception as e:
            logger.exception("列出UWP包异常: %s", e)
            return []

    @staticmethod
    def uninstall_package(package_full_name: str) -> Tuple[bool, str]:
        """调用 Remove-AppxPackage 卸载 UWP 应用（含其数据），返回 (成功, 消息)"""
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-Command", f"Remove-AppxPackage -Package '{package_full_name}'"],
                capture_output=True,
                text=True,
                check=False,
                encoding="utf-8",
                errors="ignore",
                creationflags=NO_WINDOW,
                timeout=120,
            )
            if result.returncode != 0:
                logger.warning("UWP卸载失败: %s", result.stderr)
                return False, result.stderr.strip() or "Remove-AppxPackage 失败"
            return True, "UWP 应用已卸载"
        except Exception as e:
            logger.exception("UWP卸载异常: %s", e)
            return False, str(e)

    @staticmethod
    def migrate_package(
        package_full_name: str,
        target_drive: Path,
        progress_callback=None,
    ) -> Tuple[bool, str]:
        packages = UWPManager.list_packages()
        pkg = next((p for p in packages if p.get("PackageFullName") == package_full_name), None)
        if not pkg:
            return False, f"未找到UWP包: {package_full_name}"

        install_location = Path(pkg["InstallLocation"])
        if not install_location.exists():
            return False, f"UWP安装目录不存在: {install_location}"

        logger.info("迁移UWP包 %s 从 %s 到 %s", package_full_name, install_location, target_drive)

        target = target_drive / "WinAppMigrator" / "UWP" / package_full_name
        target.parent.mkdir(parents=True, exist_ok=True)

        result = migrate_folder(install_location, target, progress_callback, use_junction=True)
        if not result.success:
            return False, result.message

        registry_message = UWPManager._try_reregister_package(package_full_name, target)
        if registry_message:
            result.details.append(registry_message)

        return True, "\n".join([result.message] + result.details)

    @staticmethod
    def _try_reregister_package(package_full_name: str, new_location: Path) -> str:
        manifest = new_location / "AppxManifest.xml"
        if not manifest.exists():
            return "未找到 AppxManifest.xml，跳过重新注册"
        try:
            result = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    f"Add-AppxPackage -Register '{manifest}' -DisableDevelopmentMode",
                ],
                capture_output=True,
                text=True,
                check=False,
                encoding="utf-8",
                errors="ignore",
                creationflags=NO_WINDOW,
            )
            logger.info("重新注册UWP: %s", result.stdout)
            if result.returncode != 0:
                logger.warning("重新注册UWP失败（不影响运行）: %s", result.stderr)
                return f"重新注册提示: {result.stderr.strip()}"
            return "UWP包已重新注册"
        except Exception as e:
            logger.exception("重新注册UWP异常: %s", e)
            return f"重新注册异常: {e}"
