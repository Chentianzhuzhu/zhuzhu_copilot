"""执行防护：监控新启动的进程，检测申请管理员权限的安装/脚本，拦截格机与无文件攻击

纯 Win32 API（复用 security 核心工具）：
- 新进程发现：CreateToolhelp32Snapshot 快照对比基线
- 提权检测：LoadLibraryEx(LOAD_LIBRARY_AS_DATAFILE) 读取 PE manifest requestedExecutionLevel
- 命令行：ReadProcessMemory 读取目标进程 PEB CommandLine
- 拦截：OpenProcess + TerminateProcess

策略分级：
  warn  格机/破坏性命令（format/diskpart/sysprep/rm -rf 等）→ 程序名 + 参数严格匹配，仅提示避免误杀
  block 无文件攻击（PowerShell -EncodedCommand / IEX 远程加载）→ 仅脚本解释器命中才自动终止
  warn  申请管理员权限的程序 → 仅提示（避免误杀正常安装程序）
"""

import ctypes
import ctypes.wintypes as wintypes
import os
import re
from pathlib import Path
from typing import Dict, List, Optional

import winapp_migrator.core.security as _sec

kernel32 = _sec.kernel32
ntdll = _sec.ntdll

RT_MANIFEST = 24
LOAD_LIBRARY_AS_DATAFILE = 0x2

# ------------------------------------------------------------
# 恶意特征（保守，避免误杀正常操作）
# ------------------------------------------------------------
# 破坏性工具程序名（可执行文件本身即破坏性工具，如 format/diskpart/sysprep）
_DESTRUCT_EXES = {
    "format.exe", "format.com", "diskpart.exe", "sysprep.exe",
    "systemreset.exe", "reagentc.exe", "bootsect.exe",
}
# 命令解释器：仅在解释器进程中做命令行关键词检测，避免误杀普通程序
_SCRIPT_INTERPRETERS = {
    "cmd.exe", "powershell.exe", "pwsh.exe",
    "cscript.exe", "wscript.exe", "mshta.exe", "bash.exe", "sh.exe",
}
# 无文件攻击：编码执行 / 远程加载下载执行（仅脚本解释器命中）
_LIVELESS_MARKERS = (
    "encodedcommand", "downloadstring", "invoke-expression",
)


def _tokens(cmdline: str) -> List[str]:
    """命令行分词：带引号的参数视为一个 token"""
    return [t.strip('"') for t in re.findall(r'"(?:[^"]*)"|\S+', cmdline or "")]


def _exe_basename(tok: str) -> str:
    """取 token 的可执行文件名（去路径、小写）"""
    return os.path.basename(tok.strip('"')).lower()


def _is_drive(arg: str) -> bool:
    """是否为盘符参数（如 C: / C:\）"""
    return bool(re.match(r'^[a-zA-Z]:[\\/]?', arg))


def _destructive_reason(exe_name: str, toks: List[str]) -> Optional[str]:
    """严格匹配破坏性命令（程序名 + 参数），返回命中原因；未命中返回 None"""
    if not toks:
        return None
    # 1) 可执行文件本身是破坏性工具（如运行 format.exe）
    if exe_name in _DESTRUCT_EXES:
        return f"启动了磁盘格式化/系统重置工具 {exe_name}"
    # 2) 命令行中显式调用了破坏性工具（如 cmd /c format.com C:）
    if any(_exe_basename(t) in _DESTRUCT_EXES for t in toks):
        return "命令行中调用了磁盘格式化/系统重置工具"
    # 3) 解释器内建命令：按 token 与参数严格匹配（避免子串误杀普通程序）
    if exe_name not in _SCRIPT_INTERPRETERS:
        return None
    for i, t in enumerate(toks):
        b = _exe_basename(t)
        rest = toks[i + 1:]
        if b == "format" and any(_is_drive(a) for a in rest):
            return "执行磁盘格式化 (format)"
        if b == "diskpart":
            return "执行磁盘分区工具 (diskpart)"
        if b == "del" and any(_is_drive(a) for a in rest):
            return "执行强制删除磁盘文件 (del C:)"
        if b == "rm" and any(re.match(r'^-[a-zA-Z]*(r[a-zA-Z]*f|f[a-zA-Z]*r)', a) for a in rest):
            return "执行递归强制删除 (rm -rf)"
        if b == "cipher" and any(a.lower().startswith("/w") for a in rest):
            return "执行磁盘剩余空间擦除 (cipher /w)"
        if b == "bcdedit" and any(a.lower().startswith("/set") for a in rest):
            return "修改系统启动配置 (bcdedit /set)"
        if b == "dism" and any(a.lower().startswith("/remove") for a in rest):
            return "移除系统组件/驱动 (dism /remove)"
    return None


def _fileless_reason(exe_name: str, toks: List[str], low: str) -> Optional[str]:
    """无文件攻击检测：仅脚本解释器进程命中，避免误伤普通程序"""
    if exe_name not in _SCRIPT_INTERPRETERS:
        return None
    if any(t.lower().startswith("-enc") for t in toks) or "encodedcommand" in low:
        return "检测到编码执行 (PowerShell -EncodedCommand)"
    if any(t.lower() == "iex" for t in toks) or "downloadstring" in low or "invoke-expression" in low:
        return "检测到远程加载执行 (IEX/DownloadString)"
    return None


def _manifest_level(exe_path: str) -> str:
    """读取 PE 可执行文件的 manifest requestedExecutionLevel（空串表示未提权）"""
    kernel32.LoadLibraryExW.argtypes = [wintypes.LPCWSTR, wintypes.HANDLE, wintypes.DWORD]
    kernel32.LoadLibraryExW.restype = wintypes.HMODULE
    kernel32.FindResourceW.argtypes = [wintypes.HMODULE, ctypes.c_void_p, ctypes.c_void_p]
    kernel32.FindResourceW.restype = wintypes.HRSRC
    kernel32.SizeofResource.argtypes = [wintypes.HMODULE, wintypes.HRSRC]
    kernel32.SizeofResource.restype = wintypes.DWORD
    kernel32.LoadResource.argtypes = [wintypes.HMODULE, wintypes.HRSRC]
    kernel32.LoadResource.restype = wintypes.HGLOBAL
    kernel32.LockResource.argtypes = [wintypes.HGLOBAL]
    kernel32.LockResource.restype = ctypes.c_void_p
    kernel32.FreeLibrary.argtypes = [wintypes.HMODULE]

    try:
        hmod = kernel32.LoadLibraryExW(exe_path, None, LOAD_LIBRARY_AS_DATAFILE)
        if not hmod:
            return ""
        try:
            hres = kernel32.FindResourceW(hmod, ctypes.c_void_p(1), ctypes.c_void_p(RT_MANIFEST))
            if not hres:
                return ""
            size = kernel32.SizeofResource(hmod, hres)
            hdata = kernel32.LoadResource(hmod, hres)
            ptr = kernel32.LockResource(hdata)
            if not ptr or size <= 0:
                return ""
            xml = ctypes.string_at(ptr, size).decode("utf-8", "ignore")
            m = re.search(r'requestedExecutionLevel[^>]*level="(\w+)"', xml)
            return m.group(1) if m else ""
        finally:
            kernel32.FreeLibrary(hmod)
    except Exception:
        return ""


def _process_cmdline(pid: int) -> str:
    """ReadProcessMemory 读取目标进程 PEB.CommandLine（按进程位数自适应偏移）"""
    PROCESS_QUERY_INFORMATION = 0x0400
    PROCESS_VM_READ = 0x0010
    h = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not h:
        return ""
    try:
        pbi = _sec.PROCESS_BASIC_INFORMATION()
        if ntdll.NtQueryInformationProcess(h, 0, ctypes.byref(pbi),
                                           ctypes.sizeof(pbi), None) == 0 and pbi.PebBaseAddress:
            layout = _sec._peb_layout(pid)
            pp_raw = _sec._read_mem(h, pbi.PebBaseAddress + layout["pp"], layout["ptr"])
            if len(pp_raw) == layout["ptr"]:
                pp = int.from_bytes(pp_raw, "little")
                if pp:
                    us_raw = _sec._read_mem(h, pp + layout["cmd"], layout["us"])
                    if len(us_raw) == layout["us"]:
                        length = int.from_bytes(us_raw[:2], "little")
                        buf_addr = int.from_bytes(us_raw[layout["ptr"]:layout["ptr"] * 2], "little")
                        if buf_addr and 0 < length <= 4096:
                            data = _sec._read_mem(h, buf_addr, length)
                            if data:
                                return data.decode("utf-16-le", "replace")
    finally:
        kernel32.CloseHandle(h)
    return ""


class ExecutionGuard:
    """执行防护：监控新进程并检测提权/格机/无文件攻击"""

    def __init__(self):
        self._baseline: set = set()

    def start(self):
        """记录当前进程基线（防护开启时已运行的进程视为可信）"""
        self._baseline = {p["pid"] for p in _sec._enum_processes()}

    def check(self) -> dict:
        """对比快照发现新进程并分析，返回 {'blocked': [...], 'warned': [...]}"""
        result = {"blocked": [], "warned": []}
        now_pids = {p["pid"] for p in _sec._enum_processes()}
        for pid in sorted(now_pids - self._baseline):
            entry = self._analyze(pid)
            if not entry:
                continue
            if entry["level"] == "block":
                # 终止前二次校验（防 PID 复用误杀）：当前 PID 仍命中相同攻击特征才处置
                recheck = self._analyze(pid)
                if recheck is not None and recheck["level"] == "block":
                    entry["killed"] = _sec._terminate_process(pid)
                else:
                    entry["killed"] = False
                    entry["reason"] += "（进程已消失或 PID 复用，跳过终止）"
                result["blocked"].append(entry)
            else:
                result["warned"].append(entry)
        self._baseline = now_pids
        return result

    def _analyze(self, pid: int) -> Optional[dict]:
        path = _sec._process_path(pid)
        name = Path(path).name if path else ""
        exe = name.lower()
        cmdline = _process_cmdline(pid)
        toks = _tokens(cmdline)
        low = cmdline.lower()

        # 1) 格机/破坏性命令 → 提示（程序名 + 参数严格匹配，避免误杀）
        reason = _destructive_reason(exe, toks)
        if reason:
            return {"pid": pid, "name": name or f"PID {pid}", "cmdline": cmdline,
                    "level": "warn", "reason": reason}
        # 2) 无文件攻击（仅脚本解释器）→ 拦截
        reason = _fileless_reason(exe, toks, low)
        if reason:
            return {"pid": pid, "name": name or f"PID {pid}", "cmdline": cmdline,
                    "level": "block", "reason": reason}
        # 3) 申请管理员权限 → 提示（不拦截，避免误杀正常安装程序）
        if path and _manifest_level(path) == "requireAdministrator" \
                and _sec.SecurityScanner._not_system(path):
            return {"pid": pid, "name": name or f"PID {pid}", "cmdline": cmdline,
                    "level": "warn", "reason": "该程序申请管理员权限"}
        return None
