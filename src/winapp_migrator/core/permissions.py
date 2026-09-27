import ctypes
from ctypes import wintypes
from pathlib import Path
import subprocess

from winapp_migrator.utils.helpers import setup_logging

logger = setup_logging()

NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0

ADVAPI32 = ctypes.windll.advapi32
KERNEL32 = ctypes.windll.kernel32

def enable_privilege(privilege_name: str) -> bool:
    hToken = wintypes.HANDLE()
    if not ADVAPI32.OpenProcessToken(
        KERNEL32.GetCurrentProcess(),
        0x0020 | 0x0008,
        ctypes.byref(hToken),
    ):
        logger.error("OpenProcessToken 失败: %s", KERNEL32.GetLastError())
        return False

    luid = wintypes.LUID()
    if not ADVAPI32.LookupPrivilegeValueW(None, privilege_name, ctypes.byref(luid)):
        KERNEL32.CloseHandle(hToken)
        return False

    tp = wintypes.TOKEN_PRIVILEGES()
    tp.PrivilegeCount = 1
    tp.Privileges[0].Luid = luid
    tp.Privileges[0].Attributes = 0x00000002

    if not ADVAPI32.AdjustTokenPrivileges(hToken, False, ctypes.byref(tp), 0, None, None):
        KERNEL32.CloseHandle(hToken)
        return False

    KERNEL32.CloseHandle(hToken)
    return True

def take_ownership(path: Path) -> bool:
    try:
        enable_privilege("SeTakeOwnershipPrivilege")
        enable_privilege("SeRestorePrivilege")
        enable_privilege("SeSecurityPrivilege")
    except Exception as e:
        logger.warning("启用特权失败: %s", e)

    try:
        result = subprocess.run(
            ["takeown", "/F", str(path), "/R", "/D", "Y"],
            capture_output=True,
            text=True,
            check=False,
            creationflags=NO_WINDOW,
        )
        logger.debug("takeown 输出: %s", result.stdout)
        if result.returncode != 0:
            logger.warning("takeown 部分失败: %s", result.stderr)
    except Exception as e:
        logger.error("takeown 执行失败: %s", e)
        return False

    user = get_current_username()
    if user:
        try:
            result = subprocess.run(
                ["icacls", str(path), "/grant", f"{user}:F", "/T", "/C"],
                capture_output=True,
                text=True,
                check=False,
            )
            logger.debug("icacls grant 输出: %s", result.stdout)
        except Exception as e:
            logger.error("icacls 执行失败: %s", e)
            return False

    try:
        result = subprocess.run(
            ["icacls", str(path), "/grant", "Administrators:F", "/T", "/C"],
            capture_output=True,
            text=True,
            check=False,
            creationflags=NO_WINDOW,
        )
        logger.debug("icacls admin 输出: %s", result.stdout)
    except Exception as e:
        logger.error("icacls admin 执行失败: %s", e)
        return False

    return True

def get_current_username() -> str:
    import ctypes
    buf_size = 256
    buf = ctypes.create_unicode_buffer(buf_size)
    size = wintypes.DWORD(buf_size)
    if ctypes.windll.advapi32.GetUserNameW(buf, ctypes.byref(size)):
        return buf.value
    return ""
