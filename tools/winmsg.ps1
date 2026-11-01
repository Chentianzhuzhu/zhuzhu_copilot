param(
  [string]$action = "list",
  [string]$title = "",
  [int]$hwnd = 0,
  [int]$x = 0,
  [int]$y = 0,
  [string]$text = "",
  [string]$key = "",
  [string]$cls = ""
)

Add-Type -TypeDefinition @"
using System;
using System.Text;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public class Win {
  public delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr l);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll", CharSet=CharSet.Auto)] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll", CharSet=CharSet.Auto)] public static extern int GetClassName(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h, uint msg, IntPtr wp, IntPtr lp);
  [DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr h, uint msg, IntPtr wp, IntPtr lp);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr hdc, uint flags);
  [DllImport("user32.dll")] public static extern IntPtr GetWindow(IntPtr h, uint cmd);
  [DllImport("user32.dll", SetLastError=true)] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }

  public static List<string> List() {
    List<string> res = new List<string>();
    EnumWindows(delegate(IntPtr h, IntPtr l) {
      if (!IsWindowVisible(h)) return true;
      StringBuilder t = new StringBuilder(512);
      GetWindowText(h, t, 512);
      StringBuilder c = new StringBuilder(256);
      GetClassName(h, c, 256);
      RECT r; GetWindowRect(h, out r);
      RECT cr; GetClientRect(h, out cr);
      uint pid; GetWindowThreadProcessId(h, out pid);
      string tt = t.ToString();
      if (tt.Length > 0 || c.ToString().Contains("Qt")) {
        res.Add(h.ToString() + "\t" + tt + "\t" + c.ToString() + "\t" + r.Left + "," + r.Top + "," + (r.Right-r.Left) + "x" + (r.Bottom-r.Top) + "\tclient=" + cr.Right + "x" + cr.Bottom + "\tpid=" + pid);
      }
      return true;
    }, IntPtr.Zero);
    return res;
  }
}
"@

function Get-Target {
  if ($hwnd -ne 0) { return [IntPtr]$hwnd }
  $found = [IntPtr]::Zero
  $all = [Win]::List()
  foreach ($line in $all) {
    $parts = $line -split "`t"
    if ($title -ne "" -and $parts[1] -like "*$title*") { $found = [IntPtr][int64]$parts[0] }
  }
  if ($found -eq [IntPtr]::Zero) {
    foreach ($line in $all) {
      $parts = $line -split "`t"
      if ($cls -ne "" -and $parts[2] -like "*$cls*") { $found = [IntPtr][int64]$parts[0] }
    }
  }
  return $found
}

switch ($action) {
  "list" {
    foreach ($l in [Win]::List()) { Write-Output $l }
  }
  "info" {
    $h = Get-Target
    if ($h -eq [IntPtr]::Zero) { Write-Output "NOT_FOUND"; break }
    $r = New-Object Win+RECT
    [void][Win]::GetWindowRect($h, [ref]$r)
    $cr = New-Object Win+RECT
    [void][Win]::GetClientRect($h, [ref]$cr)
    $sb = New-Object System.Text.StringBuilder 512
    [void][Win]::GetWindowText($h, $sb, 512)
    $cb = New-Object System.Text.StringBuilder 256
    [void][Win]::GetClassName($h, $cb, 256)
    Write-Output ("HWND=" + $h)
    Write-Output ("TITLE=" + $sb.ToString())
    Write-Output ("CLASS=" + $cb.ToString())
    Write-Output ("WINDOWRECT=" + $r.Left + "," + $r.Top + "," + ($r.Right-$r.Left) + "x" + ($r.Bottom-$r.Top))
    Write-Output ("CLIENTRECT=" + $cr.Right + "x" + $cr.Bottom)
  }
  "click" {
    $h = Get-Target
    if ($h -eq [IntPtr]::Zero) { Write-Output "NOT_FOUND"; break }
    $lp = [IntPtr](($y -shl 16) -bor ($x -band 0xFFFF))
    $r1 = [Win]::PostMessage($h, 0x0200, [IntPtr]0, $lp)   # WM_MOUSEMOVE
    Start-Sleep -Milliseconds 40
    $r2 = [Win]::PostMessage($h, 0x0201, [IntPtr]1, $lp)   # WM_LBUTTONDOWN
    Start-Sleep -Milliseconds 80
    $r3 = [Win]::PostMessage($h, 0x0202, [IntPtr]0, $lp)   # WM_LBUTTONUP
    Write-Output ("POST_CLICK hwnd=" + $h + " client=" + $x + "," + $y + " results=" + $r1 + "," + $r2 + "," + $r3 + " err=" + [System.Runtime.InteropServices.Marshal]::GetLastWin32Error())
  }
  "clickfg" {
    $h = Get-Target
    if ($h -eq [IntPtr]::Zero) { Write-Output "NOT_FOUND"; break }
    [void][Win]::SetForegroundWindow($h)
    Start-Sleep -Milliseconds 300
    $r = New-Object Win+RECT
    [void][Win]::GetWindowRect($h, [ref]$r)
    $lp = [IntPtr](($y -shl 16) -bor ($x -band 0xFFFF))
    [void][Win]::PostMessage($h, 0x0200, [IntPtr]0, $lp)
    Start-Sleep -Milliseconds 40
    [void][Win]::PostMessage($h, 0x0201, [IntPtr]1, $lp)
    Start-Sleep -Milliseconds 80
    [void][Win]::PostMessage($h, 0x0202, [IntPtr]0, $lp)
    Write-Output ("POST_CLICK_FG hwnd=" + $h + " client=" + $x + "," + $y + " fg=" + $fg)
  }
  "type" {
    $h = Get-Target
    if ($h -eq [IntPtr]::Zero) { Write-Output "NOT_FOUND"; break }
    $ok = 0
    foreach ($ch in $text.ToCharArray()) {
      $code = [int][char]$ch
      [void][Win]::PostMessage($h, 0x0102, [IntPtr]$code, [IntPtr]1)   # WM_CHAR
      Start-Sleep -Milliseconds 25
      $ok++
    }
    Write-Output ("POST_TYPE hwnd=" + $h + " chars=" + $ok)
  }
  "key" {
    $h = Get-Target
    if ($h -eq [IntPtr]::Zero) { Write-Output "NOT_FOUND"; break }
    $map = @{ "enter" = 0x0D; "tab" = 0x09; "esc" = 0x1B; "space" = 0x20; "backspace" = 0x08 }
    if (-not $map.ContainsKey($key.ToLower())) { Write-Output ("UNSUPPORTED_KEY " + $key); break }
    $vk = $map[$key.ToLower()]
    [void][Win]::PostMessage($h, 0x0100, [IntPtr]$vk, [IntPtr]1)   # WM_KEYDOWN
    Start-Sleep -Milliseconds 40
    [void][Win]::PostMessage($h, 0x0102, [IntPtr]$vk, [IntPtr]1)   # WM_CHAR
    Start-Sleep -Milliseconds 40
    [void][Win]::PostMessage($h, 0x0101, [IntPtr]$vk, [IntPtr]1)   # WM_KEYUP
    Write-Output ("POST_KEY hwnd=" + $h + " key=" + $key)
  }
  "close" {
    $h = Get-Target
    if ($h -eq [IntPtr]::Zero) { Write-Output "NOT_FOUND"; break }
    [void][Win]::PostMessage($h, 0x0010, [IntPtr]0, [IntPtr]0)   # WM_CLOSE
    Write-Output ("POST_CLOSE hwnd=" + $h)
  }
}
