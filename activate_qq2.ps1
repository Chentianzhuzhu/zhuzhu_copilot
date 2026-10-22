Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public class Win32Fg2 {
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll", CharSet=CharSet.Auto)] public static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int count);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr hWnd, IntPtr hWndInsertAfter, int X, int Y, int cx, int cy, uint uFlags);
}
"@

$p = Get-Process -Name QQ -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
if (-not $p) { Write-Output "QQ_PROCESS_NOT_FOUND"; exit 1 }

$hwnd = $p.MainWindowHandle
Write-Output ("QQ_HWND=" + $hwnd)

if ([Win32Fg2]::IsIconic($hwnd)) { [Win32Fg2]::ShowWindow($hwnd, 9) | Out-Null }

# 置顶再取消置顶，强制提到最上层
$HWND_TOPMOST = [IntPtr](-1)
$HWND_NOTOPMOST = [IntPtr](-2)
$SWP_NOMOVE = 0x0002
$SWP_NOSIZE = 0x0001
[Win32Fg2]::SetWindowPos($hwnd, $HWND_TOPMOST, 0, 0, 0, 0, $SWP_NOMOVE -bor $SWP_NOSIZE) | Out-Null
Start-Sleep -Milliseconds 200
[Win32Fg2]::SetWindowPos($hwnd, $HWND_NOTOPMOST, 0, 0, 0, 0, $SWP_NOMOVE -bor $SWP_NOSIZE) | Out-Null

$w = New-Object -ComObject WScript.Shell
$r = $w.AppActivate([int]$p.Id)
Start-Sleep -Milliseconds 700

$h = [Win32Fg2]::GetForegroundWindow()
$sb = New-Object System.Text.StringBuilder 256
[Win32Fg2]::GetWindowText($h, $sb, 256) | Out-Null
Write-Output ("AppActivate_Result=" + $r)
Write-Output ("Foreground_HWND=" + $h)
Write-Output ("Foreground_Title=" + $sb.ToString())
