Write-Output "===== PROCESSES ====="
Get-Process | Where-Object { $_.ProcessName -match "Weixin|WeChat|wechat" } |
  Select-Object Id, ProcessName, MainWindowTitle, Path | Format-List

Write-Output "===== INTEGRITY / ELEVATION ====="
try {
  Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public class Tok {
  [DllImport("advapi32.dll", SetLastError=true)] public static extern bool OpenProcessToken(IntPtr h, uint acc, out IntPtr tok);
  [DllImport("advapi32.dll", SetLastError=true)] public static extern bool GetTokenInformation(IntPtr tok, int cls, out uint info, uint len, out uint ret);
  [DllImport("kernel32.dll")] public static extern IntPtr OpenProcess(uint acc, bool inh, int pid);
}
"@
  function Get-Integrity([int]$pid) {
    $h = [Tok]::OpenProcess(0x0400, $false, $pid)
    if ($h -eq [IntPtr]::Zero) { return "openprocess_fail" }
    $tok = [IntPtr]::Zero
    if (-not [Tok]::OpenProcessToken($h, 0x0008, [ref]$tok)) { return "opentoken_fail" }
    $lvl = [uint32]0
    $ret = [uint32]0
    $ok = [Tok]::GetTokenInformation($tok, 25, [ref]$lvl, 4, [ref]$ret)
    if (-not $ok) { return "getinfo_fail" }
    switch ($lvl) {
      0x1000 { return "Low" }
      0x2000 { return "Medium" }
      0x2100 { return "MediumPlus" }
      0x3000 { return "High(管理员)" }
      0x4000 { return "System" }
      default { return ("0x" + $lvl.ToString("X")) }
    }
  }
  Write-Output ("AGENT_SELF(pid=" + $PID + "): " + (Get-Integrity $PID))
  foreach ($p in (Get-Process | Where-Object { $_.ProcessName -match "Weixin|WeChat|wechat" })) {
    Write-Output ("  " + $p.ProcessName + " pid=" + $p.Id + " -> " + (Get-Integrity $p.Id))
  }
} catch { Write-Output ("ERR: " + $_.Exception.Message) }

Write-Output "===== WECHAT PATHS ====="
$roots = @("C:\Program Files\Tencent", "C:\Program Files (x86)\Tencent", "D:\Program Files\Tencent", "D:\Tencent", "C:\Tencent", "D:\Program Files (x86)\Tencent")
foreach ($r in $roots) {
  if (Test-Path $r) {
    Get-ChildItem -Path $r -Recurse -Filter "*.exe" -ErrorAction SilentlyContinue |
      Where-Object { $_.Name -match "Weixin|WeChat|wechat" } |
      ForEach-Object { Write-Output ("FOUND: " + $_.FullName) }
  }
}
Write-Output "----- 注册表 -----"
Get-ItemProperty "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*", "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*", "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*" -ErrorAction SilentlyContinue |
  Where-Object { $_.DisplayName -match "微信|Weixin|WeChat" } |
  Select-Object DisplayName, DisplayVersion, InstallLocation | Format-List
Write-Output "----- 快捷方式 -----"
$sh = New-Object -ComObject WScript.Shell
foreach ($d in @([Environment]::GetFolderPath("Programs"), [Environment]::GetFolderPath("Desktop"), "C:\ProgramData\Microsoft\Windows\Start Menu\Programs")) {
  Get-ChildItem -Path $d -Recurse -Filter "*.lnk" -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match "微信|WeChat|Weixin" } |
    ForEach-Object {
      $sc = $sh.CreateShortcut($_.FullName)
      Write-Output ("LNK: " + $_.FullName + " -> " + $sc.TargetPath)
    }
}
