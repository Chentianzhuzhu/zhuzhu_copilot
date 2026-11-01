param(
  [string]$action = "move",
  [int]$x = 0,
  [int]$y = 0
)

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public class Nat {
  [DllImport("user32.dll", SetLastError=true)] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll", SetLastError=true)] public static extern bool GetCursorPos(out POINT p);
  [DllImport("user32.dll")] public static extern void mouse_event(uint dwFlags, int dx, int dy, uint dwData, IntPtr dwExtraInfo);
  [DllImport("user32.dll", SetLastError=true)] public static extern IntPtr FindWindow(string lpClassName, string lpWindowName);
  [DllImport("user32.dll", SetLastError=true)] public static extern bool SetForegroundWindow(IntPtr hWnd);
  [DllImport("user32.dll", CharSet=CharSet.Auto)] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll", CharSet=CharSet.Auto)] public static extern int GetWindowText(IntPtr hWnd, System.Text.StringBuilder s, int n);
  public struct POINT { public int X; public int Y; }
}
"@

function Show-Cursor {
  $p = New-Object Nat+POINT
  [void][Nat]::GetCursorPos([ref]$p)
  Write-Output ("CURSOR_NOW: " + $p.X + "," + $p.Y)
}

switch ($action) {
  "cursor" { Show-Cursor }
  "move" {
    $ok = [Nat]::SetCursorPos($x, $y)
    Start-Sleep -Milliseconds 120
    Write-Output ("SETCURSOR_OK: " + $ok + "  target=" + $x + "," + $y)
    Show-Cursor
  }
  "click" {
    [void][Nat]::SetCursorPos($x, $y)
    Start-Sleep -Milliseconds 150
    [Nat]::mouse_event(0x0002, 0, 0, 0, [IntPtr]::Zero)   # left down
    Start-Sleep -Milliseconds 60
    [Nat]::mouse_event(0x0004, 0, 0, 0, [IntPtr]::Zero)   # left up
    Start-Sleep -Milliseconds 120
    Show-Cursor
    Write-Output "CLICK_SENT"
  }
  "fs" {
    $fg = [Nat]::GetForegroundWindow()
    $sb = New-Object System.Text.StringBuilder 512
    [void][Nat]::GetWindowText($fg, $sb, 512)
    Write-Output ("FOREGROUND: " + $fg + " | " + $sb.ToString())
  }
  "procs" {
    Get-Process | Where-Object { $_.ProcessName -match "Weixin|WeChat|wechat" } |
      Select-Object Id, ProcessName, MainWindowTitle, Path | Format-List
  }
  "paths" {
    $roots = @("C:\Program Files\Tencent", "C:\Program Files (x86)\Tencent", "D:\Program Files\Tencent", "D:\Tencent", "C:\Tencent")
    foreach ($r in $roots) {
      if (Test-Path $r) {
        Get-ChildItem -Path $r -Recurse -Filter "*.exe" -ErrorAction SilentlyContinue |
          Where-Object { $_.Name -match "Weixin|WeChat" } |
          ForEach-Object { Write-Output ("FOUND: " + $_.FullName) }
      }
    }
    $lnkDirs = @([Environment]::GetFolderPath("Programs"), [Environment]::GetFolderPath("Desktop"))
    $sh = New-Object -ComObject WScript.Shell
    foreach ($d in $lnkDirs) {
      Get-ChildItem -Path $d -Recurse -Filter "*.lnk" -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match "微信|WeChat|Weixin" } |
        ForEach-Object {
          $sc = $sh.CreateShortcut($_.FullName)
          Write-Output ("LNK: " + $_.FullName + " -> " + $sc.TargetPath)
        }
    }
  }
}
