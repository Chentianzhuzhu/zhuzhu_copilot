"""本地 MCP server（stdio 传输）：供 WinAppMigrator 的 agent_mcp 客户端连接调用测试

协议：newline-delimited JSON-RPC 2.0，stdin 读请求 / stdout 写响应（UTF-8 二进制流）。
工具均为真实系统操作（标准库实现，零第三方依赖）：
  system_info / get_time / env_var / read_file / list_dir
"""

import ctypes
import json
import os
import platform
import socket
import sys
import time
from pathlib import Path

VERSION = "2024-11-05"
MAX_READ = 256 * 1024   # read_file 最大读取字节数
MAX_LIST = 50           # list_dir 最多列出项数


# ---------- 工具实现（真实 API 调用） ----------

def _total_memory_mb() -> int:
    """读取本机物理内存总量（MB）"""
    try:
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]
        ms = MEMORYSTATUSEX()
        ms.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms)):
            return ms.ullTotalPhys // (1024 * 1024)
    except Exception:
        pass
    return 0


def _os_name() -> str:
    """从注册表读取真实系统产品名（Win10/Win11 内核同为 10.0，platform 无法区分）

    注意：Win11 的注册表 ProductName 仍返回 "Windows 10 …"（微软兼容性设计），
    需按 CurrentBuildNumber（>=22000 为 Win11）修正名称。
    """
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as k:
            name = winreg.QueryValueEx(k, "ProductName")[0]
            display = winreg.QueryValueEx(k, "DisplayVersion")[0]
            build = int(winreg.QueryValueEx(k, "CurrentBuildNumber")[0])
        if build >= 22000 and name.startswith("Windows 10"):
            name = name.replace("Windows 10", "Windows 11")
        return f"{name}（{display}，内部版本 {build}）"
    except (OSError, ValueError):
        return f"{platform.system()} {platform.release()}"


def tool_system_info(args: dict) -> str:
    return (f"主机名: {socket.gethostname()}\n"
            f"系统: {_os_name()}\n"
            f"架构: {platform.machine()}\n"
            f"CPU 核心数: {os.cpu_count()}\n"
            f"物理内存: {_total_memory_mb()} MB\n"
            f"Python: {sys.version.split()[0]}")


def tool_get_time(args: dict) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def tool_env_var(args: dict) -> str:
    name = str(args.get("name", "")).strip()
    if not name:
        raise ValueError("缺少环境变量名（name）")
    value = os.environ.get(name)
    return f"{name} = {value}" if value is not None else f"环境变量 {name} 不存在"


def tool_read_file(args: dict) -> str:
    p = Path(str(args.get("path", "")).strip())
    if not p.is_file():
        raise FileNotFoundError(f"文件不存在: {p}")
    data = p.read_bytes()
    if len(data) > MAX_READ:
        raise ValueError(f"文件过大（{len(data)} 字节 > {MAX_READ}），拒绝读取")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return f"（非 UTF-8 二进制文件，共 {len(data)} 字节，已跳过）"


def tool_list_dir(args: dict) -> str:
    p = Path(str(args.get("path", "")).strip())
    if not p.is_dir():
        raise NotADirectoryError(f"目录不存在: {p}")
    entries = sorted(p.iterdir())[:MAX_LIST]
    lines = [f"{d.name}/" if d.is_dir() else d.name for d in entries]
    return "\n".join(lines) if lines else "（空目录）"


# ---------- 工具注册表 ----------

TOOLS = [
    {"name": "system_info", "description": "获取本机系统信息（主机名、系统版本、CPU、内存、Python 版本）",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "get_time", "description": "获取当前系统时间（YYYY-MM-DD HH:MM:SS）",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "env_var", "description": "读取指定环境变量的值",
     "inputSchema": {"type": "object",
                     "properties": {"name": {"type": "string", "description": "环境变量名"}},
                     "required": ["name"]}},
    {"name": "read_file", "description": "读取 UTF-8 文本文件内容（最大 256KB）",
     "inputSchema": {"type": "object",
                     "properties": {"path": {"type": "string", "description": "文件绝对路径"}},
                     "required": ["path"]}},
    {"name": "list_dir", "description": "列出目录下内容（最多 50 项）",
     "inputSchema": {"type": "object",
                     "properties": {"path": {"type": "string", "description": "目录绝对路径"}},
                     "required": ["path"]}},
]

_HANDLERS = {
    "system_info": tool_system_info,
    "get_time": tool_get_time,
    "env_var": tool_env_var,
    "read_file": tool_read_file,
    "list_dir": tool_list_dir,
}


# ---------- JSON-RPC 分发 ----------

def _handle(msg: dict):
    """处理单个请求/通知；通知（无 id）返回 None 不回复"""
    method = msg.get("method", "")
    params = msg.get("params", {}) or {}
    rid = msg.get("id")

    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "local-mcp-server", "version": "1.0"},
        }}
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        name = params.get("name", "")
        fn = _HANDLERS.get(name)
        if fn is None:
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32601, "message": f"未知工具: {name}"}}
        try:
            text = fn(params.get("arguments", {}) or {})
            result = {"content": [{"type": "text", "text": text}], "isError": False}
        except Exception as e:
            result = {"content": [{"type": "text", "text": str(e)}], "isError": True}
        return {"jsonrpc": "2.0", "id": rid, "result": result}
    # 其他未知方法：返回 method not found
    return {"jsonrpc": "2.0", "id": rid,
            "error": {"code": -32601, "message": f"未知方法: {method}"}}


def main():
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    for line in stdin:
        if not line.strip():
            continue
        try:
            msg = json.loads(line.decode("utf-8", "replace"))
        except json.JSONDecodeError:
            continue
        resp = _handle(msg)
        if resp is not None:
            stdout.write(json.dumps(resp, ensure_ascii=False).encode("utf-8") + b"\n")
            stdout.flush()


if __name__ == "__main__":
    main()
