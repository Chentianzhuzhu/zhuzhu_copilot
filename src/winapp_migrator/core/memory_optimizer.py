"""内存优化模块：终极方案
1. NtOpenProcess 兜底 + MmTrimAllSystemPagableMemory 系统级 trim
2. 暂停进程 → 硬限制工作集为 1 字节
3. 多轮压力循环：分配 80% RAM → trim → purge → 释放
4. 多次清空 standby list
"""

import ctypes
import ctypes.wintypes as w
import logging
import os
import gc
from ctypes import byref, sizeof, c_size_t, c_void_p, POINTER, Structure, c_byte

logger = logging.getLogger(__name__)

# ============================================================
# Win32 API 绑定
# ============================================================
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
psapi = ctypes.WinDLL("psapi", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)
ntdll = ctypes.WinDLL("ntdll", use_last_error=True)

# --- 常量 ---
TH32CS_SNAPPROCESS = 0x02
PROCESS_SET_QUOTA = 0x0100
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
PROCESS_VM_OPERATION = 0x0008
PROCESS_SUSPEND_RESUME = 0x0800
PROCESS_TERMINATE = 0x0001
TOKEN_ADJUST_PRIVILEGES = 0x0020
TOKEN_QUERY = 0x0008
SE_PRIVILEGE_ENABLED = 0x2
MEM_COMMIT = 0x1000
MEM_RESERVE = 0x2000
MEM_RELEASE = 0x8000
PAGE_READWRITE = 0x04

# --- 结构体 ---
class PROCESSENTRY32W(Structure):
    _fields_ = [
        ("dwSize", w.DWORD),
        ("cntUsage", w.DWORD),
        ("th32ProcessID", w.DWORD),
        ("th32DefaultHeapID", POINTER(w.ULONG)),
        ("th32ModuleID", w.DWORD),
        ("cntThreads", w.DWORD),
        ("th32ParentProcessID", w.DWORD),
        ("pcPriClassBase", w.LONG),
        ("dwFlags", w.DWORD),
        ("szExeFile", w.WCHAR * 260),
    ]

class PROCESS_MEMORY_COUNTERS_EX(Structure):
    _fields_ = [
        ("cb", w.DWORD),
        ("PageFaultCount", w.DWORD),
        ("PeakWorkingSetSize", c_size_t),
        ("WorkingSetSize", c_size_t),
        ("QuotaPeakPagedPoolUsage", c_size_t),
        ("QuotaPagedPoolUsage", c_size_t),
        ("QuotaPeakNonPagedPoolUsage", c_size_t),
        ("QuotaNonPagedPoolUsage", c_size_t),
        ("PagefileUsage", c_size_t),
        ("PeakPagefileUsage", c_size_t),
        ("PrivateUsage", c_size_t),
    ]

class LUID(Structure):
    _fields_ = [("LowPart", w.DWORD), ("HighPart", w.LONG)]

class LUID_AND_ATTRIBUTES(Structure):
    _fields_ = [("Luid", LUID), ("Attributes", w.DWORD)]

class TOKEN_PRIVILEGES(Structure):
    _fields_ = [("PrivilegeCount", w.DWORD), ("Privileges", LUID_AND_ATTRIBUTES * 1)]

class MEMORYSTATUSEX(Structure):
    _fields_ = [
        ("dwLength", w.DWORD),
        ("dwMemoryLoad", w.DWORD),
        ("ullTotalPhys", w.ULARGE_INTEGER),
        ("ullAvailPhys", w.ULARGE_INTEGER),
        ("ullTotalPageFile", w.ULARGE_INTEGER),
        ("ullAvailPageFile", w.ULARGE_INTEGER),
        ("ullTotalVirtual", w.ULARGE_INTEGER),
        ("ullAvailVirtual", w.ULARGE_INTEGER),
        ("ullAvailExtendedVirtual", w.ULARGE_INTEGER),
    ]

class CLIENT_ID(Structure):
    _fields_ = [("UniqueProcess", w.HANDLE), ("UniqueThread", w.HANDLE)]

class UNICODE_STRING(Structure):
    _fields_ = [("Length", w.USHORT), ("MaximumLength", w.USHORT), ("Buffer", c_void_p)]

class OBJECT_ATTRIBUTES(Structure):
    _fields_ = [
        ("Length", w.ULONG),
        ("RootDirectory", w.HANDLE),
        ("ObjectName", POINTER(UNICODE_STRING)),
        ("Attributes", w.ULONG),
        ("SecurityDescriptor", c_void_p),
        ("SecurityQualityOfService", c_void_p),
    ]

# --- kernel32 ---
CreateToolhelp32Snapshot = kernel32.CreateToolhelp32Snapshot
CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
CreateToolhelp32Snapshot.restype = w.HANDLE

Process32FirstW = kernel32.Process32FirstW
Process32FirstW.argtypes = [w.HANDLE, POINTER(PROCESSENTRY32W)]
Process32FirstW.restype = w.BOOL

Process32NextW = kernel32.Process32NextW
Process32NextW.argtypes = [w.HANDLE, POINTER(PROCESSENTRY32W)]
Process32NextW.restype = w.BOOL

OpenProcess = kernel32.OpenProcess
OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
OpenProcess.restype = w.HANDLE

CloseHandle = kernel32.CloseHandle
CloseHandle.argtypes = [w.HANDLE]
CloseHandle.restype = w.BOOL

SetProcessWorkingSetSize = kernel32.SetProcessWorkingSetSize
SetProcessWorkingSetSize.argtypes = [w.HANDLE, c_size_t, c_size_t]
SetProcessWorkingSetSize.restype = w.BOOL

GetCurrentProcess = kernel32.GetCurrentProcess
GetCurrentProcess.restype = w.HANDLE

SetSystemFileCacheSize = kernel32.SetSystemFileCacheSize
SetSystemFileCacheSize.argtypes = [c_size_t, c_size_t, w.DWORD]
SetSystemFileCacheSize.restype = w.BOOL

GlobalMemoryStatusEx = kernel32.GlobalMemoryStatusEx
GlobalMemoryStatusEx.argtypes = [POINTER(MEMORYSTATUSEX)]
GlobalMemoryStatusEx.restype = w.BOOL

VirtualAlloc = kernel32.VirtualAlloc
VirtualAlloc.argtypes = [c_void_p, c_size_t, w.DWORD, w.DWORD]
VirtualAlloc.restype = c_void_p

VirtualFree = kernel32.VirtualFree
VirtualFree.argtypes = [c_void_p, c_size_t, w.DWORD]
VirtualFree.restype = w.BOOL

# --- psapi ---
EmptyWorkingSet = psapi.EmptyWorkingSet
EmptyWorkingSet.argtypes = [w.HANDLE]
EmptyWorkingSet.restype = w.BOOL

GetProcessMemoryInfo = psapi.GetProcessMemoryInfo
GetProcessMemoryInfo.argtypes = [w.HANDLE, POINTER(PROCESS_MEMORY_COUNTERS_EX), w.DWORD]
GetProcessMemoryInfo.restype = w.BOOL

# --- advapi32 ---
OpenProcessToken = advapi32.OpenProcessToken
OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, POINTER(w.HANDLE)]
OpenProcessToken.restype = w.BOOL

LookupPrivilegeValueW = advapi32.LookupPrivilegeValueW
LookupPrivilegeValueW.argtypes = [w.LPCWSTR, w.LPCWSTR, POINTER(LUID)]
LookupPrivilegeValueW.restype = w.BOOL

AdjustTokenPrivileges = advapi32.AdjustTokenPrivileges
AdjustTokenPrivileges.argtypes = [w.HANDLE, w.BOOL, POINTER(TOKEN_PRIVILEGES), w.DWORD, c_void_p, c_void_p]
AdjustTokenPrivileges.restype = w.BOOL

# --- user32 ---
GetForegroundWindow = user32.GetForegroundWindow
GetForegroundWindow.restype = w.HANDLE

GetWindowThreadProcessId = user32.GetWindowThreadProcessId
GetWindowThreadProcessId.argtypes = [w.HANDLE, POINTER(w.DWORD)]
GetWindowThreadProcessId.restype = w.DWORD

# --- ntdll ---
NtSuspendProcess = ntdll.NtSuspendProcess
NtSuspendProcess.argtypes = [w.HANDLE]
NtSuspendProcess.restype = w.LONG

NtResumeProcess = ntdll.NtResumeProcess
NtResumeProcess.argtypes = [w.HANDLE]
NtResumeProcess.restype = w.LONG

NtSetSystemInformation = ntdll.NtSetSystemInformation
NtSetSystemInformation.argtypes = [w.DWORD, c_void_p, w.ULONG]
NtSetSystemInformation.restype = w.LONG

NtOpenProcess = ntdll.NtOpenProcess
NtOpenProcess.argtypes = [POINTER(w.HANDLE), w.DWORD, POINTER(OBJECT_ATTRIBUTES), POINTER(CLIENT_ID)]
NtOpenProcess.restype = w.LONG

# MmTrimAllSystemPagableMemory — 系统级 trim，比 EmptyWorkingSet 激进得多
# 告诉内核立即回收所有可分页的系统内存（内核池、驱动、缓存等）
try:
    MmTrimAllSystemPagableMemory = ntdll.MmTrimAllSystemPagableMemory
    MmTrimAllSystemPagableMemory.restype = w.LONG
    _HAS_MM_TRIM = True
except AttributeError:
    _HAS_MM_TRIM = False

# ============================================================
# 保护列表
# ============================================================
# 绝不暂停的核心进程
_SUSPEND_PROTECTED = {
    "system", "idle", "csrss", "wininit",
    "services", "lsass", "winlogon", "smss",
    "dwm", "explorer", "audiodg",
    "winappmigrator",
}

# 可以 trim 但不暂停的进程（svchost 也从暂停保护中移出，改为 trim-only）
_TRIM_ONLY_PROTECTED = {
    "svchost", "services", "lsass", "csrss", "winlogon",
}

# 自身保护
_WS_PROTECTED = {"winappmigrator"}

# ACCESS 掩码
_ACCESS_TRIM = PROCESS_SET_QUOTA | PROCESS_QUERY_INFORMATION
_ACCESS_ALL = PROCESS_SET_QUOTA | PROCESS_QUERY_INFORMATION | PROCESS_SUSPEND_RESUME | PROCESS_VM_READ | PROCESS_VM_OPERATION | PROCESS_TERMINATE


def _make_object_attributes():
    oa = OBJECT_ATTRIBUTES()
    oa.Length = sizeof(oa)
    oa.RootDirectory = None
    oa.ObjectName = None
    oa.Attributes = 0
    oa.SecurityDescriptor = None
    oa.SecurityQualityOfService = None
    return oa


def _open_process_fallback(pid: int, access: int) -> w.HANDLE | None:
    """OpenProcess → NtOpenProcess 兜底，覆盖受保护进程"""
    h = OpenProcess(access, False, pid)
    if h:
        return h

    cid = CLIENT_ID()
    cid.UniqueProcess = w.HANDLE(pid)
    cid.UniqueThread = None
    oa = _make_object_attributes()
    h_nt = w.HANDLE()
    status = NtOpenProcess(byref(h_nt), access, byref(oa), byref(cid))
    if status == 0 and h_nt:
        return h_nt

    return None


def _close_handle_safe(h):
    if h:
        try:
            CloseHandle(h)
        except Exception:
            pass


def _enable_privileges() -> bool:
    """启用 SeIncreaseQuotaPrivilege + SeDebugPrivilege"""
    h_token = w.HANDLE()
    if not OpenProcessToken(GetCurrentProcess(), TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY, byref(h_token)):
        return False

    for priv_name in ("SeIncreaseQuotaPrivilege", "SeDebugPrivilege"):
        luid = LUID()
        if not LookupPrivilegeValueW(None, priv_name, byref(luid)):
            continue
        tp = TOKEN_PRIVILEGES()
        tp.PrivilegeCount = 1
        tp.Privileges[0].Luid = luid
        tp.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED
        AdjustTokenPrivileges(h_token, False, byref(tp), sizeof(tp), None, None)

    CloseHandle(h_token)
    return True


def _purge_standby_list() -> bool:
    """清空 standby list"""
    cmd = c_size_t(0)
    status = NtSetSystemInformation(0x50, byref(cmd), sizeof(cmd))
    return status == 0


def _trim_system_memory() -> bool:
    """系统级 trim — 回收所有可分页系统内存"""
    if not _HAS_MM_TRIM:
        return False
    try:
        MmTrimAllSystemPagableMemory()
        return True
    except Exception:
        return False


def _get_foreground_pids() -> set:
    pids = set()
    hwnd = GetForegroundWindow()
    if hwnd:
        fg_pid = w.DWORD()
        GetWindowThreadProcessId(hwnd, byref(fg_pid))
        if fg_pid.value:
            pids.add(fg_pid.value)
    return pids


def _enum_processes() -> list[tuple[int, str, int]]:
    """枚举所有进程，返回 [(pid, name, working_set_bytes), ...] 按内存降序"""
    snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == w.HANDLE(-1):
        return []

    processes = []
    pe = PROCESSENTRY32W()
    pe.dwSize = sizeof(pe)

    if Process32FirstW(snap, byref(pe)):
        while True:
            pid = pe.th32ProcessID
            name = pe.szExeFile.lower() if pe.szExeFile else ""

            ws_bytes = 0
            if pid > 0:
                h = _open_process_fallback(pid, PROCESS_QUERY_INFORMATION | PROCESS_VM_READ)
                if h:
                    pmc = PROCESS_MEMORY_COUNTERS_EX()
                    pmc.cb = sizeof(pmc)
                    if GetProcessMemoryInfo(h, byref(pmc), sizeof(pmc)):
                        ws_bytes = pmc.WorkingSetSize
                    _close_handle_safe(h)

            processes.append((pid, name, ws_bytes))

            if not Process32NextW(snap, byref(pe)):
                break

    CloseHandle(snap)
    processes.sort(key=lambda x: x[2], reverse=True)
    return processes


def _get_memory_status() -> dict:
    ms = MEMORYSTATUSEX()
    ms.dwLength = sizeof(ms)
    GlobalMemoryStatusEx(byref(ms))
    return {
        "total_mb": ms.ullTotalPhys / (1024 * 1024),
        "avail_mb": ms.ullAvailPhys / (1024 * 1024),
        "used_mb": (ms.ullTotalPhys - ms.ullAvailPhys) / (1024 * 1024),
        "load_pct": ms.dwMemoryLoad,
    }


def _allocate_pressure(total_mb: float) -> list:
    """
    分配大块内存制造极端内存压力。
    目标：80% 总 RAM，确保内核感受到强烈压力。
    Touch 每一页确保物理 RAM 被实际占用。
    """
    target_mb = total_mb * 0.8
    if target_mb < 100:
        return []

    chunks = []
    remaining = int(target_mb * 1024 * 1024)
    chunk_size = 256 * 1024 * 1024

    while remaining > 32 * 1024 * 1024:
        alloc_size = min(chunk_size, remaining)
        buf = VirtualAlloc(None, c_size_t(alloc_size), MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE)
        if not buf:
            chunk_size //= 2
            if chunk_size < 16 * 1024 * 1024:
                break
            continue

        # memset 零填充，确保每个页面都被 commit
        try:
            ctypes.memset(buf, 0, alloc_size)
        except Exception:
            pass

        chunks.append((buf, alloc_size))
        remaining -= alloc_size

    return chunks


def _free_pressure(chunks: list):
    """释放所有压力内存块"""
    for buf, size in chunks:
        VirtualFree(buf, c_size_t(0), MEM_RELEASE)
    chunks.clear()
    gc.collect()


def _should_suspend(name: str) -> bool:
    return name not in _SUSPEND_PROTECTED and name not in _TRIM_ONLY_PROTECTED


def _trim_process(h, hard_limit: bool = False):
    """对单个进程执行 trim"""
    EmptyWorkingSet(h)
    if hard_limit:
        SetProcessWorkingSetSize(h, c_size_t(1), c_size_t(1))
    else:
        SetProcessWorkingSetSize(h, c_size_t(-1), c_size_t(-1))


def optimize_memory(progress_callback=None) -> dict:
    """
    终极方案：
    1. NtOpenProcess 兜底 + MmTrimAllSystemPagableMemory 系统级 trim
    2. 暂停进程 → 硬限制工作集为 1 字节
    3. 多轮压力循环：分配 80% RAM → trim → purge → 释放
    4. 多次清空 standby list
    """

    def notify(pct: int, msg: str):
        logger.info("[%d%%] %s", pct, msg)
        if progress_callback:
            try:
                progress_callback(pct, msg)
            except Exception:
                pass

    NEG_ONE = c_size_t(-1)
    ONE = c_size_t(1)
    NEG_ONE_SZ = c_size_t(-1)

    # 1. 启用特权
    notify(1, "启用 SeIncreaseQuotaPrivilege + SeDebugPrivilege...")
    _enable_privileges()

    # 2. 基线
    notify(3, "获取基线...")
    mem_before = _get_memory_status()
    total_mb = mem_before["total_mb"]

    notify(5, "枚举进程 (NtOpenProcess 兜底)...")
    all_procs = _enum_processes()
    proc_count = len(all_procs)
    ws_before = sum(p[2] for p in all_procs)
    ws_before_mb = ws_before / (1024 * 1024)

    protected_pids = {os.getpid()} | _get_foreground_pids()

    ok_openprocess = 0
    ok_ntopen = 0
    fail_open = 0
    suspended = 0
    trim_only = 0

    # ============================================================
    # Phase 1: 暂停所有非核心进程 + trim-only 进程
    # ============================================================
    notify(7, "Phase 1: 暂停非核心进程 + trim-only 进程...")

    suspended_handles = []  # (pid, h, was_suspended)
    trim_only_handles = []  # (pid, h)

    for i, (pid, name, _ws) in enumerate(all_procs):
        if pid in protected_pids or pid <= 4:
            continue
        if name in _SUSPEND_PROTECTED and name not in _TRIM_ONLY_PROTECTED:
            continue

        can_suspend = _should_suspend(name)
        is_trim_only = name in _TRIM_ONLY_PROTECTED
        access = _ACCESS_ALL if (can_suspend and not is_trim_only) else _ACCESS_TRIM

        h = _open_process_fallback(pid, access)
        if not h:
            fail_open += 1
            continue

        if OpenProcess(access, False, pid) is None:
            ok_ntopen += 1
        else:
            ok_openprocess += 1

        if can_suspend and not is_trim_only:
            NtSuspendProcess(h)
            suspended += 1
            suspended_handles.append((pid, h, True))
        elif is_trim_only:
            trim_only_handles.append((pid, h))
            trim_only += 1
        else:
            suspended_handles.append((pid, h, False))

        if i % 50 == 0:
            notify(7 + int(i / max(proc_count, 1) * 6),
                   f"暂停 {suspended}, trim-only {trim_only}, OpenProcess={ok_openprocess}, NtOpen={ok_ntopen}, 失败={fail_open}")

    notify(13, f"Phase 1 完成: 暂停 {suspended}, trim-only {trim_only}, OpenProcess={ok_openprocess}, NtOpen={ok_ntopen}, 失败={fail_open}")

    # ============================================================
    # Phase 2: 硬限制工作集为 1 字节（进程已暂停，不抖页）
    # ============================================================
    notify(15, "Phase 2: 硬限制工作集为 1 字节...")

    all_handles = suspended_handles + trim_only_handles
    for i, (pid, h) in enumerate([(p, h) for p, h, *_ in all_handles]):
        _trim_process(h, hard_limit=True)
        if i % 50 == 0:
            notify(15 + int(i / max(len(all_handles), 1) * 5),
                   f"硬限制 {i+1}/{len(all_handles)}...")

    # ============================================================
    # Phase 3: 系统级 trim + purge
    # ============================================================
    notify(21, "Phase 3: MmTrimAllSystemPagableMemory + standby purge...")
    _trim_system_memory()
    _purge_standby_list()

    # ============================================================
    # Phase 4-6: 三轮压力循环
    # ============================================================
    total_allocated_mb = 0
    for cycle in range(3):
        base_pct = 23 + cycle * 15
        notify(base_pct, f"Phase {4+cycle}: 压力循环 {cycle+1}/3 — 分配内存...")

        pressure_chunks = _allocate_pressure(total_mb)
        allocated_mb = sum(sz for _, sz in pressure_chunks) / (1024 * 1024)
        total_allocated_mb = max(total_allocated_mb, allocated_mb)

        notify(base_pct + 3, f"已分配 {allocated_mb:.0f} MB, 系统级 trim...")
        _trim_system_memory()

        notify(base_pct + 6, "压力下 trim 所有进程...")
        for i, (pid, h) in enumerate([(p, h) for p, h, *_ in all_handles]):
            _trim_process(h, hard_limit=True)
            if i % 50 == 0:
                notify(base_pct + 6 + int(i / max(len(all_handles), 1) * 3),
                       f"压力 trim {i+1}/{len(all_handles)}...")

        notify(base_pct + 10, "purge standby...")
        _purge_standby_list()

        notify(base_pct + 12, "释放压力内存...")
        _free_pressure(pressure_chunks)

    # ============================================================
    # Phase 7: 恢复工作集限制 + 恢复进程
    # ============================================================
    notify(68, "Phase 7: 恢复工作集限制 + 恢复进程...")

    for i, (pid, h, was_suspended) in enumerate(suspended_handles):
        SetProcessWorkingSetSize(h, NEG_ONE, NEG_ONE)
        if was_suspended:
            NtResumeProcess(h)
        _close_handle_safe(h)
        if i % 50 == 0:
            notify(68 + int(i / max(len(suspended_handles), 1) * 5),
                   f"恢复 {i+1}/{len(suspended_handles)}...")

    # 关闭 trim-only 句柄
    for pid, h in trim_only_handles:
        SetProcessWorkingSetSize(h, NEG_ONE, NEG_ONE)
        _close_handle_safe(h)

    # ============================================================
    # Phase 8: 清空系统文件缓存
    # ============================================================
    notify(74, "Phase 8: 清空系统文件缓存...")
    try:
        SetSystemFileCacheSize(NEG_ONE_SZ, NEG_ONE_SZ, 0x2)
        SetSystemFileCacheSize(NEG_ONE_SZ, NEG_ONE_SZ, 0)
    except Exception:
        pass

    # ============================================================
    # Phase 9: 压缩 System 进程 (PID 4)
    # ============================================================
    notify(77, "Phase 9: 压缩 System 进程...")
    h_sys = _open_process_fallback(4, _ACCESS_TRIM)
    if h_sys:
        _trim_process(h_sys)
        _close_handle_safe(h_sys)

    # ============================================================
    # Phase 10: 最终系统级 trim + 最终收尾
    # ============================================================
    notify(80, "Phase 10: 最终系统级 trim...")
    _trim_system_memory()

    notify(83, "最终收尾压缩...")
    for i, (pid, name, _ws) in enumerate(all_procs):
        if pid in protected_pids or pid <= 4 or name in _WS_PROTECTED:
            continue
        h = _open_process_fallback(pid, _ACCESS_TRIM)
        if h:
            _trim_process(h)
            _close_handle_safe(h)
        if i % 50 == 0:
            notify(83 + int(i / max(proc_count, 1) * 5),
                   f"收尾 {i+1}/{proc_count}...")

    # ============================================================
    # 最终多重 purge
    # ============================================================
    notify(90, "最终 purge standby (x5)...")
    for _ in range(5):
        _purge_standby_list()

    # ============================================================
    # 结果统计
    # ============================================================
    notify(96, "计算结果...")
    mem_after = _get_memory_status()

    all_procs2 = _enum_processes()
    ws_after = sum(p[2] for p in all_procs2)
    ws_after_mb = ws_after / (1024 * 1024)

    freed_mb = ws_before_mb - ws_after_mb
    avail_gained = mem_after["avail_mb"] - mem_before["avail_mb"]
    pct = freed_mb / max(ws_before_mb, 1) * 100 if ws_before_mb > 0 else 0

    total_ok = ok_openprocess + ok_ntopen

    notify(100, "优化完成")

    details = [
        f"总内存: {mem_after['total_mb']:.0f} MB",
        f"优化前工作集: {ws_before_mb:.0f} MB",
        f"优化后工作集: {ws_after_mb:.0f} MB",
        f"释放: {freed_mb:.0f} MB ({pct:.0f}%)",
        f"可用内存增加: {avail_gained:.0f} MB",
        f"打开进程: {total_ok} (OpenProcess: {ok_openprocess}, NtOpen: {ok_ntopen})",
        f"暂停进程: {suspended}, trim-only: {trim_only}, 失败: {fail_open}",
        f"最大压力内存: {total_allocated_mb:.0f} MB",
        f"MmTrimAllSystemPagableMemory: {'可用' if _HAS_MM_TRIM else '不可用'}",
    ]

    msg = f"释放 {freed_mb:.0f} MB ({pct:.0f}%)，可用内存 +{avail_gained:.0f} MB"

    return {
        "success": True,
        "message": msg,
        "freed_mb": round(freed_mb, 1),
        "inuse_before_mb": round(ws_before_mb, 0),
        "inuse_after_mb": round(ws_after_mb, 0),
        "processes_trimmed": total_ok,
        "processes_killed": 0,
        "services_stopped": 0,
        "services_disabled": 0,
        "details": details,
    }