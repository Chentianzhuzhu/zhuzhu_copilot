Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public class Win {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr p);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  public delegate bool EnumWindowsProc(IntPtr h, IntPtr p);
}
"@
$targetPid = (Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match "main.py" }).ProcessId
Write-Output "target pid: $targetPid"
$found = $null
$cb = [Win+EnumWindowsProc]{
  param($h,$p)
  $wp = 0
  [Win]::GetWindowThreadProcessId($h, [ref]$wp) | Out-Null
  if ($wp -eq $targetPid -and [Win]::IsWindowVisible($h)) {
    $sb = New-Object System.Text.StringBuilder 256
    [Win]::GetWindowText($h, $sb, 256) | Out-Null
    Write-Output ("  hwnd=" + $h + " title=" + $sb.ToString())
    $script:found = $h
  }
  return $true
}
[Win]::EnumWindows($cb, [IntPtr]::Zero) | Out-Null
if ($found) {
  [Win]::ShowWindow($found, 9) | Out-Null
  [Win]::SetForegroundWindow($found) | Out-Null
  Write-Output "brought to front"
}
