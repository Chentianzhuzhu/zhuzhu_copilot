"""更新桌面/开始菜单快捷方式指向新路径（完全移动后旧路径失效）"""
import os
import base64
import subprocess
from pathlib import Path

NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0

_SCRIPT = r"""
$w = New-Object -ComObject WScript.Shell
$oldDir = __OLD__
$newDir = __NEW__
$count = 0
$folders = @(
    [Environment]::GetFolderPath('Desktop'),
    [Environment]::GetFolderPath('CommonDesktopDirectory'),
    [Environment]::GetFolderPath('StartMenu') + '\Programs',
    [Environment]::GetFolderPath('CommonStartMenu') + '\Programs'
)
foreach ($folder in $folders) {
    if (-not (Test-Path -LiteralPath $folder)) { continue }
    Get-ChildItem -LiteralPath $folder -Filter *.lnk -Recurse -ErrorAction SilentlyContinue | ForEach-Object {
        try {
            $lnk = $w.CreateShortcut($_.FullName)
            $changed = $false
            if ($lnk.TargetPath -and $lnk.TargetPath.StartsWith($oldDir, [System.StringComparison]::OrdinalIgnoreCase)) {
                $lnk.TargetPath = $newDir + $lnk.TargetPath.Substring($oldDir.Length)
                $changed = $true
            }
            if ($lnk.WorkingDirectory -and $lnk.WorkingDirectory.StartsWith($oldDir, [System.StringComparison]::OrdinalIgnoreCase)) {
                $lnk.WorkingDirectory = $newDir + $lnk.WorkingDirectory.Substring($oldDir.Length)
                $changed = $true
            }
            if ($changed) { $lnk.Save(); $count++ }
        } catch {}
    }
}
$host.UI.Write($count)
"""

def _ps_quote(s) -> str:
    """转成 PowerShell 单引号字面量（反斜杠/中文/空格均安全）"""
    return "'" + str(s).replace("'", "''") + "'"

def update_shortcuts(old_dir: Path, new_dir: Path) -> int:
    """把桌面/开始菜单中指向旧目录的快捷方式更新到新目录，返回更新数量"""
    try:
        script = _SCRIPT.replace("__OLD__", _ps_quote(old_dir)).replace("__NEW__", _ps_quote(new_dir))
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-EncodedCommand", encoded],
            capture_output=True, creationflags=NO_WINDOW, timeout=120,
        )
        text = (result.stdout or b"").decode("utf-8", "replace").strip()
        return int(text) if text.isdigit() else 0
    except Exception:
        return 0


# ---- 强力卸载：扫描/删除指向目录的快捷方式（Python 原生解析 .lnk，避免 PowerShell 冷启动开销）----

def _shortcut_folders() -> list:
    """[(目录, 是否递归)]：桌面仅根目录（避免递归进巨大目录树），开始菜单递归分组文件夹"""
    folders = []
    profile = os.environ.get("USERPROFILE", "")
    if profile:
        folders.append((Path(profile) / "Desktop", False))
        folders.append((Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs", True))
    folders.append((Path(os.environ.get("PUBLIC", r"C:\Users\Public")) / "Desktop", False))
    folders.append((Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Microsoft" / "Windows" / "Start Menu" / "Programs", True))
    return folders


def _lnk_target(path: Path) -> str | None:
    """读取 .lnk 快捷方式的 TargetPath（WScript.Shell COM，准确且快）"""
    global _shell
    if _shell is None:
        try:
            import win32com.client
            _shell = win32com.client.Dispatch("WScript.Shell")
        except Exception:
            return None
    try:
        return _shell.CreateShortcut(str(path)).TargetPath or None
    except Exception:
        return None


_shell = None


def _iter_shortcuts(directory: Path):
    """遍历四个快捷方式目录，产出 TargetPath 指向该目录的 .lnk"""
    prefix = str(directory).lower()
    for folder, recurse in _shortcut_folders():
        if not folder.is_dir():
            continue
        try:
            pattern = folder.rglob("*.lnk") if recurse else folder.glob("*.lnk")
            for lnk in pattern:
                target = _lnk_target(lnk)
                if target and target.lower().startswith(prefix):
                    yield lnk
        except OSError:
            continue


def scan_shortcuts(directory: Path) -> list:
    """返回桌面/开始菜单中 TargetPath 指向该目录的 .lnk 路径列表"""
    try:
        return [str(lnk) for lnk in _iter_shortcuts(directory)]
    except Exception:
        return []


def remove_shortcuts(directory: Path) -> int:
    """删除桌面/开始菜单中指向该目录的快捷方式，返回删除数量"""
    count = 0
    for lnk in _iter_shortcuts(directory):
        try:
            lnk.unlink()
            count += 1
        except OSError:
            pass
    return count
