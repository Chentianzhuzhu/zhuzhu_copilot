param(
  [int]$x = 900,
  [int]$y = 500
)

Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public class T {
  [DllImport("user32.dll", SetLastError=true)] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll", SetLastError=true)] public static extern bool GetCursorPos(out POINT p);
  [DllImport("user32.dll", SetLastError=true)] public static extern uint SendInput(uint nInputs, INPUT[] pInputs, int cbSize);
  [DllImport("user32.dll", SetLastError=true)] public static extern IntPtr OpenInputDesktop(uint dwFlags, bool fInherit, uint dwDesiredAccess);
  [DllImport("user32.dll", SetLastError=true)] public static extern IntPtr GetThreadDesktop(uint dwThreadId);
  [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
  [DllImport("user32.dll", SetLastError=true)] public static extern bool GetCursorInfo(ref CURSORINFO pci);
  [DllImport("user32.dll")] public static extern IntPtr GetProcessWindowStation();
  public struct POINT { public int X; public int Y; }
  public struct CURSORINFO { public int cbSize; public int flags; public IntPtr hCursor; public POINT ptScreenPos; }
  [StructLayout(LayoutKind.Sequential)]
  public struct MOUSEINPUT { public int dx; public int dy; public uint mouseData; public uint dwFlags; public uint time; public IntPtr dwExtraInfo; }
  [StructLayout(LayoutKind.Sequential)]
  public struct INPUT { public uint type; public MOUSEINPUT mi; }
}
"@

$p = New-Object T+POINT
[void][T]::GetCursorPos([ref]$p)
Write-Output ("BEFORE: " + $p.X + "," + $p.Y)

# 1) SetCursorPos + 错误码
$ok = [T]::SetCursorPos($x, $y)
$err = [System.Runtime.InteropServices.Marshal]::GetLastWin32Error()
Write-Output ("SETCURSOR: ok=" + $ok + " err=" + $err)
[void][T]::GetCursorPos([ref]$p)
Write-Output ("AFTER_SETCURSOR: " + $p.X + "," + $p.Y)

# 2) SendInput 绝对坐标移动
$nx = [int](($x / 1920.0) * 65535)
$ny = [int](($y / 1080.0) * 65535)
$inp = New-Object T+INPUT
$inp.type = 0
$inp.mi.dx = $nx
$inp.mi.dy = $ny
$inp.mi.dwFlags = 0x8001 -bor 0x0001   # ABSOLUTE | MOVE
$arr = @($inp)
$sent = [T]::SendInput(1, $arr, [System.Runtime.InteropServices.Marshal]::SizeOf($inp))
$err2 = [System.Runtime.InteropServices.Marshal]::GetLastWin32Error()
Write-Output ("SENDINPUT: sent=" + $sent + " err=" + $err2)
Start-Sleep -Milliseconds 200
[void][T]::GetCursorPos([ref]$p)
Write-Output ("AFTER_SENDINPUT: " + $p.X + "," + $p.Y)

# 3) 桌面/窗口站信息
$ci = New-Object T+CURSORINFO
$ci.cbSize = [System.Runtime.InteropServices.Marshal]::SizeOf($ci)
[void][T]::GetCursorInfo([ref]$ci)
Write-Output ("CURSORINFO: flags=" + $ci.flags + " pos=" + $ci.ptScreenPos.X + "," + $ci.ptScreenPos.Y)
$ws = [T]::GetProcessWindowStation()
$td = [T]::GetThreadDesktop([T]::GetCurrentThreadId())
$id = [T]::OpenInputDesktop(0, $false, 0x0100)
$err3 = [System.Runtime.InteropServices.Marshal]::GetLastWin32Error()
Write-Output ("WINSTA=" + $ws + " THREADDESKTOP=" + $td + " INPUTDESKTOP=" + $id + " err=" + $err3 + " session=" + [System.Diagnostics.Process]::GetCurrentProcess().SessionId)
Write-Output ("INTERACTIVE=" + [Environment]::UserInteractive)
