import os
import shutil
import time
from pathlib import Path
from typing import Callable, Optional

from winapp_migrator.utils.helpers import setup_logging, format_size, safe_remove
from winapp_migrator.core.permissions import take_ownership

logger = setup_logging()

class MigrationResult:
    def __init__(self, success: bool, message: str, details: list[str] = None, backup_path: Path = None):
        self.success = success
        self.message = message
        self.details = details or []
        self.backup_path = backup_path

def _remove_existing(target: Path) -> bool:
    """强力删除已存在目标目录：safe_remove 重试 → force_delete_directory 兜底（权限/360）"""
    if safe_remove(target):
        return True
    ok, _ = force_delete_directory(target)
    return ok


def migrate_folder(
    source: Path,
    target: Path,
    progress_callback: Optional[Callable[[int, str], None]] = None,
    mode: str = "junction",
    on_conflict: Optional[Callable[[Path, Path], bool]] = None,
) -> MigrationResult:
    """迁移目录。mode="junction" 时旧路径保留目录联接；mode="move" 完全移动，备份由调用方清理

    on_conflict(source, target)：目标已存在时询问用户，返回 True 表示删除目标并替换，
    False 表示跳过该目录（视为失败）。为 None 时目标已存在直接报错。
    """
    if not source.exists():
        return MigrationResult(False, f"源目录不存在: {source}")
    if target.exists():
        if on_conflict is None:
            return MigrationResult(False, f"目标目录已存在: {target}")
        try:
            replace = bool(on_conflict(source, target))
        except Exception:
            replace = False
        if not replace:
            return MigrationResult(False, f"用户选择跳过: 目标目录已存在 {target}")
        if not _remove_existing(target):
            return MigrationResult(False, f"目标目录已存在且无法替换: {target}")

    details = []

    def report(percent: int, msg: str):
        logger.info("[%d%%] %s", percent, msg)
        if progress_callback:
            progress_callback(percent, msg)

    report(5, "获取目标目录权限...")
    parent = source.parent
    if not os.access(parent, os.W_OK):
        take_ownership(parent)

    report(10, "复制文件到新位置...")
    try:
        shutil.copytree(source, target, symlinks=True)
    except Exception as e:
        logger.exception("复制目录失败")
        return MigrationResult(False, f"复制目录失败: {e}")

    report(60, "校验文件完整性...")
    if not _verify_copy(source, target):
        shutil.rmtree(target, ignore_errors=True)
        return MigrationResult(False, "文件复制校验失败，已回滚")

    report(70, "重命名原目录为备份...")
    backup = Path(str(source) + ".migrator_backup")
    renamed = False
    last_err = ""
    for attempt in range(3):
        try:
            if backup.exists():
                shutil.rmtree(backup, ignore_errors=True)
            source.rename(backup)
            renamed = True
            break
        except Exception as e:
            last_err = str(e)
            # 换备份名，避开可能被占用的残留备份
            backup = Path(f"{source}.migrator_backup{attempt + 1}")
            time.sleep(0.8)
    if not renamed:
        if mode == "junction":
            shutil.rmtree(target, ignore_errors=True)
            return MigrationResult(False, f"无法重命名原目录: {last_err}")
        # move 模式：文件已复制并校验，直接尝试删除原目录；删除失败则残留待清理，不中断迁移
        try:
            shutil.rmtree(source, ignore_errors=True)
        except Exception:
            pass
        if not source.exists():
            details.append("原目录已直接删除")
            return MigrationResult(True, f"成功迁移到 {target}", details)
        details.append(f"警告: 原目录未能删除（{last_err}），请稍后手动清理 {source}")
        return MigrationResult(True, f"成功迁移到 {target}", details, backup_path=source)

    report(80, "处理旧目录...")
    if mode == "junction":
        report(80, "创建目录联接保证兼容性...")
        try:
            _create_junction(source, target)
            details.append("已创建目录联接")
        except Exception as e:
            logger.exception("创建junction失败，尝试恢复")
            try:
                backup.rename(source)
                shutil.rmtree(target, ignore_errors=True)
            except Exception:
                pass
            return MigrationResult(False, f"创建目录联接失败: {e}")

        report(90, "清理备份...")
        try:
            shutil.rmtree(backup, ignore_errors=True)
            details.append("已清理备份")
        except Exception as e:
            logger.warning("清理备份失败: %s", e)
            details.append(f"备份保留在: {backup}")

        report(100, "迁移完成")
        return MigrationResult(True, f"成功迁移到 {target}", details)

    # 完全移动模式：旧目录已重命名为备份，等待调用方更新引用后清理
    report(80, "旧目录已重命名为备份，更新引用后将删除")
    details.append("旧目录已移除，待引用更新后清理备份")
    return MigrationResult(True, f"成功迁移到 {target}", details, backup_path=backup)

def _verify_copy(src: Path, dst: Path) -> bool:
    try:
        for root, dirs, files in os.walk(src):
            rel_root = Path(root).relative_to(src)
            for d in dirs:
                s = Path(root) / d
                d_dst = dst / rel_root / d
                if not d_dst.exists() and not s.is_symlink():
                    return False
            for f in files:
                s = Path(root) / f
                d_dst = dst / rel_root / f
                if s.is_symlink():
                    if not d_dst.is_symlink():
                        return False
                    continue
                if not d_dst.exists():
                    return False
                if s.stat().st_size != d_dst.stat().st_size:
                    return False
        return True
    except Exception as e:
        logger.error("校验失败: %s", e)
        return False

def _terminate_processes(directory: Path) -> list:
    """强制结束运行目录内的进程，返回无法结束的进程列表（受保护/拒绝访问）

    强化算法（针对 360/UU远程 等带自我保护、SYSTEM 权限或守护进程的软件）：
    - 先提升 SeDebugPrivilege，允许结束 SYSTEM 等高权限进程
    - CIM 按可执行路径前缀匹配，路径取不到时按目录根部 exe 名称兜底匹配
    - 进程树自底向上结束（先杀子进程再杀父进程）
    - 每个进程依次尝试 Stop-Process / taskkill / WMI Terminate / 直接 TerminateProcess
    - 三轮清理，应对守护进程重启与延迟启动
    """
    import base64
    import subprocess
    script = r'''
$ErrorActionPreference = "SilentlyContinue"
$src = __SRC__
$exeNames = @()
try {
    $exeNames = @(Get-ChildItem -Path $src -Filter *.exe -File -ErrorAction Stop | ForEach-Object { $_.Name.ToLower() })
} catch {}

# 1) 启用 SeDebugPrivilege，允许结束 SYSTEM 等更高权限进程
Add-Type @"
using System;
using System.Runtime.InteropServices;
public struct WM_TP { public uint Count; public long Luid; public uint Attr; }
public static class WMPriv {
    [DllImport("advapi32.dll", SetLastError=true)]
    public static extern bool OpenProcessToken(IntPtr h, uint a, out IntPtr t);
    [DllImport("advapi32.dll", SetLastError=true)]
    public static extern bool LookupPrivilegeValue(string s, string n, out long l);
    [DllImport("advapi32.dll", SetLastError=true)]
    public static extern bool AdjustTokenPrivileges(IntPtr t, bool d, ref WM_TP p, uint l, IntPtr x, IntPtr y);
    public static void EnableDebug() {
        IntPtr tok; long luid;
        if (!OpenProcessToken((IntPtr)(-1), 0x28, out tok)) return;
        if (!LookupPrivilegeValue(null, "SeDebugPrivilege", out luid)) return;
        WM_TP tp = new WM_TP();
        tp.Count = 1; tp.Luid = luid; tp.Attr = 2;
        AdjustTokenPrivileges(tok, false, ref tp, 0, IntPtr.Zero, IntPtr.Zero);
    }
}
"@
[WMPriv]::EnableDebug()

# 2) 直接调用 TerminateProcess 作为最终手段
Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class WMKill {
    [DllImport("kernel32.dll", SetLastError=true)]
    public static extern IntPtr OpenProcess(uint a, bool i, uint p);
    [DllImport("kernel32.dll", SetLastError=true)]
    public static extern bool TerminateProcess(IntPtr h, uint c);
    public static bool NtKill(uint pid) {
        IntPtr h = OpenProcess(0x0400 | 0x0001, false, pid);
        if (h == IntPtr.Zero) h = OpenProcess(0x0001, false, pid);
        if (h == IntPtr.Zero) return false;
        bool ok = TerminateProcess(h, 1);
        return ok;
    }
}
"@

function Get-TargetProcs([string]$prefix) {
    # 路径前缀带目录边界（prefix + '\'），避免父目录/同名前缀误匹配
    $boundary = $prefix + [IO.Path]::DirectorySeparatorChar
    $procs = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $exe = $_.ExecutablePath
        $hit = $false
        if ($exe) { $hit = $exe.StartsWith($boundary, [System.StringComparison]::OrdinalIgnoreCase) }
        if (-not $hit -and $exeNames.Count -gt 0 -and $_.Name) {
            $hit = $exeNames -contains $_.Name.ToLower()
        }
        $hit
    })
    return $procs
}

function Get-Desc([hashtable]$byParent, [System.Collections.ArrayList]$out, [int]$parentId) {
    if ($byParent.ContainsKey($parentId)) {
        foreach ($c in $byParent[$parentId]) {
            [void]$out.Add($c)
            Get-Desc -byParent $byParent -out $out -parentId ([int]$c.ProcessId)
        }
    }
}

function Kill-One($p) {
    $id = [int]$p.ProcessId
    if ($id -le 0) { return $false }
    Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 60
    if (-not (Get-Process -Id $id -ErrorAction SilentlyContinue)) { return $false }
    & taskkill /F /T /PID $id 2>$null | Out-Null
    Start-Sleep -Milliseconds 60
    if (-not (Get-Process -Id $id -ErrorAction SilentlyContinue)) { return $false }
    Invoke-CimMethod -ClassName Win32_Process -Filter ("ProcessId = " + $id) -MethodName Terminate -ErrorAction SilentlyContinue | Out-Null
    Start-Sleep -Milliseconds 60
    if (-not (Get-Process -Id $id -ErrorAction SilentlyContinue)) { return $false }
    [void][WMKill]::NtKill($id)
    Start-Sleep -Milliseconds 60
    return [bool](Get-Process -Id $id -ErrorAction SilentlyContinue)
}

$blocked = New-Object System.Collections.ArrayList
for ($round = 0; $round -lt 3; $round++) {
    $procs = @(Get-TargetProcs -prefix $src)
    if ($procs.Count -eq 0) { break }
    $byParent = @{}
    foreach ($p in $procs) {
        $pp = [int]$p.ParentProcessId
        if (-not $byParent.ContainsKey($pp)) { $byParent[$pp] = @() }
        $byParent[$pp] += $p
    }
    # 子进程先于父进程结束，避免守护进程趁父进程存活时重启子进程
    $order = New-Object System.Collections.ArrayList
    $visited = @{}
    foreach ($p in $procs) {
        $desc = New-Object System.Collections.ArrayList
        Get-Desc -byParent $byParent -out $desc -parentId ([int]$p.ProcessId)
        foreach ($d in $desc) {
            if (-not $visited.ContainsKey([int]$d.ProcessId)) {
                $visited[[int]$d.ProcessId] = $true
                [void]$order.Add($d)
            }
        }
        if (-not $visited.ContainsKey([int]$p.ProcessId)) {
            $visited[[int]$p.ProcessId] = $true
            [void]$order.Add($p)
        }
    }
    foreach ($p in $order) {
        if (Kill-One $p) {
            $nm = $p.Name
            if (-not $nm) { $nm = ("PID " + $p.ProcessId) }
            [void]$blocked.Add(($nm + " (PID " + $p.ProcessId + ")"))
        }
    }
    Start-Sleep -Milliseconds (400 + 400 * $round)
}
$blocked | Select-Object -Unique
'''
    src_lit = "'" + str(directory.resolve()).replace("'", "''") + "'"
    encoded = base64.b64encode(script.replace("__SRC__", src_lit).encode("utf-16-le")).decode("ascii")
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-EncodedCommand", encoded],
            capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=90,
        )
        text = (result.stdout or b"").decode("utf-8", "replace")
        return [ln.strip() for ln in text.splitlines() if ln.strip()]
    except Exception:
        return []


def is_360_self_protection(blocked: list) -> bool:
    """判断被拦截的进程是否来自 360 安全卫士（自我保护会拦截终止请求）

    只匹配安全卫士专属进程名（360safe/zhudongfangyu 等），避免 360zip 等
    名称含 "360" 的第三方组件被误判，触发无关弹窗。
    """
    _GUARD_NAMES = (
        "360safe", "zhudongfangyu", "zhudong", "360tray", "360leakfixer",
        "360sd", "360safemon", "360bdoctor", "360antis", "360box", "360netmon",
    )
    joined = " ".join((n or "").lower() for n in blocked)
    return any(g in joined for g in _GUARD_NAMES)


# 360 文件保护相关内核驱动（自我保护在驱动层拦截删除/终止）
_360_DRIVERS = (
    "360FsFlt", "360Box64", "360AntiHijack", "360netmon", "360Sensor_DM",
    "360Sensor", "360AntiSteal", "360AntiSteal64", "360qpesv", "360Camera",
    "360Hvm", "360AntiHacker", "360elam64", "ComputerZ_x64",
)


def force_delete_directory(directory: Path) -> tuple:
    """强化删除目录（针对 360 自我保护目录等常规删除失败场景）

    流程：停止 360 文件保护驱动 -> 清只读/取所有权/授权 -> 递归删除。
    仍失败则禁用仍运行的 360 驱动（重启后不再加载即可删除），返回提示。
    """
    import base64
    import subprocess
    drivers = ", ".join('"' + d + '"' for d in _360_DRIVERS)
    script = rf'''
$target = __SRC__
$drivers = @({drivers})
$results = New-Object System.Collections.ArrayList

# 1) 尽力停止 360 文件保护驱动（System 启动的驱动可能拒绝停止）
foreach ($d in $drivers) {{
    $svc = Get-Service -Name $d -ErrorAction SilentlyContinue
    if ($svc -and $svc.Status -eq "Running") {{
        & sc.exe stop $d 2>$null | Out-Null
    }}
}}
Start-Sleep -Milliseconds 1000

# 2) 清只读并获取完全控制
attrib -r ($target + "\*") /s /d 2>$null | Out-Null
takeown /f $target /r /d y 2>$null | Out-Null
icacls $target /grant "*S-1-5-32-544:(OI)(CI)F" /T /C /Q 2>$null | Out-Null

# 3) 递归删除
try {{
    Remove-Item $target -Recurse -Force -ErrorAction Stop
    [void]$results.Add("REMOVED")
}} catch {{
    $blocked = @()
    Get-ChildItem $target -Recurse -Force -ErrorAction SilentlyContinue | ForEach-Object {{
        try {{ Remove-Item $_.FullName -Force -ErrorAction Stop }} catch {{ $blocked += $_.FullName }}
    }}
    if ($blocked.Count -eq 0) {{
        Remove-Item $target -Force -ErrorAction SilentlyContinue | Out-Null
        if (-not (Test-Path $target)) {{ [void]$results.Add("REMOVED") }}
    }}
    if ($blocked.Count -gt 0) {{
        [void]$results.Add("BLOCKED=" + (($blocked | Select-Object -First 10) -join "|"))
    }}
}}

# 4) 目录仍在且 360 驱动仍运行：禁用驱动，重启后即可删除
if (Test-Path $target) {{
    $still = @()
    foreach ($d in $drivers) {{
        $svc = Get-Service -Name $d -ErrorAction SilentlyContinue
        if ($svc -and $svc.Status -eq "Running") {{ $still += $d }}
    }}
    if ($still.Count -gt 0) {{
        [void]$results.Add("DRIVERS_RUNNING=" + ($still -join ","))
        foreach ($d in $still) {{ & sc.exe config $d start= disabled 2>$null | Out-Null }}
        [void]$results.Add("DISABLED_FOR_REBOOT")
    }}
}}

$results | ForEach-Object {{ Write-Output $_ }}
'''
    src_lit = "'" + str(directory.resolve()).replace("'", "''") + "'"
    encoded = base64.b64encode(script.replace("__SRC__", src_lit).encode("utf-16-le")).decode("ascii")
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-EncodedCommand", encoded],
            capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=120,
        )
        lines = [(result.stdout or b"").decode("utf-8", "replace").strip() for ln in
                 (result.stdout or b"").decode("utf-8", "replace").splitlines() if ln.strip()]
        if "REMOVED" in lines:
            return True, "已强制删除"
        drivers_running = next((ln.split("=", 1)[1] for ln in lines if ln.startswith("DRIVERS_RUNNING=")), "")
        blocked = next((ln.split("=", 1)[1] for ln in lines if ln.startswith("BLOCKED=")), "")
        disabled = any("DISABLED_FOR_REBOOT" in ln for ln in lines)
        msg = f"360 文件保护驱动（{drivers_running}）仍在内核中拦截删除"
        if disabled:
            msg += "，已禁用这些驱动，请重启电脑后重新删除即可成功"
        if blocked:
            msg += f"\n仍被占用的文件: {blocked}"
        return False, msg
    except Exception as e:
        return False, f"强制删除失败: {e}"


def _create_junction(link: Path, target: Path):
    """在 link 处创建指向 target 的目录联接（junction），链接目录由标准库内部创建"""
    import _winapi
    try:
        # 参数顺序: (目标路径, 链接路径)；link 必须不存在
        _winapi.CreateJunction(str(target.resolve()), str(link))
    except Exception:
        # 清理标准库失败时可能残留的空链接目录，避免影响后续回滚
        try:
            os.rmdir(link)
        except OSError:
            pass
        raise
