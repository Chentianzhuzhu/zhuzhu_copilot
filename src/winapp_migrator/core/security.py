"""静默安全核心：纯 ctypes 直接调用 Win32 API，不依赖系统自带安全工具
（无 PowerShell / WMI / MpCmdRun / netstat / taskkill，全部底层 API 实现）

- 进程枚举与路径：CreateToolhelp32Snapshot + QueryFullProcessImageNameW
- 进程终止：OpenProcess + TerminateProcess，NtOpenProcess 兜底，SeDebugPrivilege 提权
- 启动项：winreg（RegOpenKeyEx / RegEnumValue / RegDeleteValue 底层注册表 API）
- 网络监听：GetExtendedTcpTable 枚举监听端口（iphlpapi）
- 防火墙状态：注册表 EnableFirewall

特征库采用保守白名单式规则，排除系统目录，避免误杀正常软件。
"""

import base64
import ctypes
import ctypes.wintypes as wintypes
import json
import os
import socket
import threading
import time
import winreg
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import List, Optional

from winapp_migrator.utils.helpers import setup_logging

logger = setup_logging()

# ------------------------------------------------------------
# Win32 常量
# ------------------------------------------------------------
TH32CS_SNAPPROCESS = 0x2
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_TERMINATE = 0x0001
PROCESS_ALL_ACCESS = 0x001FFFFF
TOKEN_ADJUST_PRIVILEGES = 0x0020
TOKEN_QUERY = 0x0008
SE_PRIVILEGE_ENABLED = 0x2
AF_INET = 2
TCP_TABLE_OWNER_PID_LISTENER = 5
MIB_TCP_STATE_LISTEN = 2

# ------------------------------------------------------------
# 恶意特征库（保守规则：仅收录知名恶意软件名，避免误杀）
# ------------------------------------------------------------
# 恶意进程名（小写，不含扩展名）。仅收录结论性恶意名称，剔除合法工具/歧义名
# （如 srvany 为微软 Resource Kit 合法工具、t-rex/trex 为合法基准测试名、winrar_setup/tcpviewer 为合法软件名）
_MALICIOUS_PROCESS_NAMES = frozenset({
    # 挖矿木马
    "xmrig", "minerd", "minerd64", "cryptominer", "cpuminer",
    "lolminer", "nbminer", "phoenixminer", "claymore", "wildrig",
    "teamredminer", "ethminer", "gminer", "kawpow",
    # 远控木马 / 蠕虫
    "njrat", "njw0rm", "darkcomet", "poisonivy", "gh0st", "ghostrat",
    "shellex", "asyncrat", "quasar", "quasarrat", "remcos", "comrat",
    # 键盘记录 / 盗号 / 窃密
    "keylogger", "qakbot", "emotet", "trickbot", "botnet",
    "infostealer", "azorult", "redline", "vidar", "formbook",
    # 勒索软件
    "wannacry", "locky", "cerber", "teslacrypt", "gandcrab",
    "ryuk", "conti", "blackcat", "revil", "phobos",
})

# 恶意进程名 + 必须出现在非系统目录（防止误杀同名正常组件）
_SYSTEM_DIRS = frozenset({
    "windows", "system32", "syswow64", "program files", "program files (x86)",
})

# 启动项命令中的恶意特征（仅保留结论性恶意软件名；剔除 temp 路径特征，
# 因大量合法软件更新器从 %TEMP% 运行，按路径判定会误删合法启动项）
_MALICIOUS_STARTUP_MARKERS = (
    "xmrig", "minerd", "njrat", "darkcomet", "poisonivy",
    "wannacry", "locky", "asyncrat", "quasar",
)

# 高危端口（运行期以 security.json network 段为准，此处为默认兜底）：暴露且非本地服务时提示风险
_HIGH_RISK_PORTS = frozenset({445, 139, 135, 3389, 23, 21, 1433, 3306, 5900, 6379})
# Windows 本地服务端口（SMB 445 / NetBIOS 135·137-139 / mDNS 5353 / SSDP 1900 等）：
# 系统默认开放、防火墙按默认策略精细管控，直接当作「漏洞」上报会造成天天误报；
# 运行期可由 security.json network.local_allowed_ports 覆盖扩展
_LOCAL_SERVICE_PORTS = frozenset({135, 137, 138, 139, 445, 5353, 1900, 5040})

# 注册表启动项位置
_STARTUP_REG_KEYS = [
    (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Run"),
    (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\RunOnce"),
    (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run"),
    (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\RunOnce"),
]

# 防火墙策略注册表位置
_FW_POLICY_KEYS = {
    "标准": r"SYSTEM\CurrentControlSet\Services\SharedAccess\Parameters\FirewallPolicy\StandardProfile",
    "公用": r"SYSTEM\CurrentControlSet\Services\SharedAccess\Parameters\FirewallPolicy\PublicProfile",
    "域": r"SYSTEM\CurrentControlSet\Services\SharedAccess\Parameters\FirewallPolicy\DomainProfile",
}

# 隔离区（清理前备份，保证误判可恢复）
_QUARANTINE_ROOT = Path(os.environ.get("ProgramData", os.environ.get("TEMP", "."))) / "WinAppMigrator" / "Quarantine"
_QUARANTINE_REG = _QUARANTINE_ROOT / "startup"    # 注册表启动项备份（JSON）
_QUARANTINE_LNK = _QUARANTINE_ROOT / "lnk"        # 启动文件夹快捷方式备份
_QUARANTINE_LOG = _QUARANTINE_ROOT / "process"    # 被终止进程审计记录（JSON）


def quarantine_dir() -> Path:
    """隔离区目录（清理前的备份所在，可调用 SecurityScanner.restore_quarantine 恢复）"""
    return _QUARANTINE_ROOT

# ------------------------------------------------------------
# Win32 API 绑定
# ------------------------------------------------------------
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
ntdll = ctypes.WinDLL("ntdll", use_last_error=True)

kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
kernel32.Process32FirstW.restype = wintypes.BOOL
kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
kernel32.Process32NextW.restype = wintypes.BOOL
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
kernel32.TerminateProcess.restype = wintypes.BOOL
kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.ReadProcessMemory.argtypes = [
    wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t)]
kernel32.ReadProcessMemory.restype = wintypes.BOOL
ntdll.NtQueryInformationProcess.argtypes = [
    wintypes.HANDLE, wintypes.ULONG, wintypes.LPVOID, wintypes.ULONG, wintypes.PULONG]
ntdll.NtQueryInformationProcess.restype = ctypes.c_long


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_void_p),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


class PROCESS_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("ExitStatus", ctypes.c_ulong),
        ("PebBaseAddress", ctypes.c_void_p),
        ("AffinityMask", ctypes.c_void_p),
        ("BasePriority", ctypes.c_long),
        ("UniqueProcessId", ctypes.c_void_p),
        ("InheritedFromUniqueProcessId", ctypes.c_void_p),
    ]


class MIB_TCPROW_OWNER_PID(ctypes.Structure):
    _fields_ = [
        ("dwState", wintypes.DWORD),
        ("dwLocalAddr", wintypes.DWORD),
        ("dwLocalPort", wintypes.DWORD),
        ("dwRemoteAddr", wintypes.DWORD),
        ("dwRemotePort", wintypes.DWORD),
        ("dwOwningPid", wintypes.DWORD),
    ]


# ------------------------------------------------------------
# 底层工具函数
# ------------------------------------------------------------
def _enable_debug_privilege() -> None:
    """启用 SeDebugPrivilege，允许打开更多进程句柄（真实安全软件标准做法）"""
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    h_token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(),
                                     TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY,
                                     ctypes.byref(h_token)):
        return

    class LUID(ctypes.Structure):
        _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", ctypes.c_long)]

    class LUID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Luid", LUID), ("Attributes", wintypes.DWORD)]

    class TOKEN_PRIVILEGES(ctypes.Structure):
        _fields_ = [("PrivilegeCount", wintypes.DWORD),
                    ("Privileges", LUID_AND_ATTRIBUTES * 1)]

    luid = LUID()
    try:
        if not advapi32.LookupPrivilegeValueW(None, "SeDebugPrivilege", ctypes.byref(luid)):
            return
        tp = TOKEN_PRIVILEGES(1, (LUID_AND_ATTRIBUTES(luid, SE_PRIVILEGE_ENABLED),))
        advapi32.AdjustTokenPrivileges(h_token, False, ctypes.byref(tp), 0, None, None)
    finally:
        kernel32.CloseHandle(h_token)


def _enum_processes() -> List[dict]:
    """CreateToolhelp32Snapshot 枚举全部进程，返回 [{'pid','name'}]"""
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == wintypes.HANDLE(-1).value:
        return []
    procs = []
    try:
        pe = PROCESSENTRY32W()
        pe.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = kernel32.Process32FirstW(snap, ctypes.byref(pe))
        while ok:
            procs.append({"pid": int(pe.th32ProcessID), "name": pe.szExeFile})
            ok = kernel32.Process32NextW(snap, ctypes.byref(pe))
    finally:
        kernel32.CloseHandle(snap)
    return procs


def _read_mem(h, addr: int, size: int) -> bytes:
    """ReadProcessMemory 读取目标进程内存"""
    buf = ctypes.create_string_buffer(size)
    read = ctypes.c_size_t(0)
    if kernel32.ReadProcessMemory(h, ctypes.c_void_p(addr), buf, size, ctypes.byref(read)):
        return buf.raw[:read.value]
    return b""


def _real_process_path(pid: int) -> str:
    """QueryFullProcessImageNameW 获取进程真实可执行路径"""
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
    finally:
        kernel32.CloseHandle(h)
    return ""


def _is_32bit_process(pid: int) -> bool:
    """判断目标进程是否为 32 位（WOW64）；当前进程为 32 位时全部按 32 位布局处理"""
    if ctypes.sizeof(ctypes.c_void_p) == 4:
        return True
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return False
    try:
        iswow2 = getattr(kernel32, "IsWow64Process2", None)  # Win10 1709+
        if iswow2:
            pm, nm = wintypes.USHORT(0), wintypes.USHORT(0)
            iswow2.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.USHORT),
                               ctypes.POINTER(wintypes.USHORT)]
            iswow2.restype = wintypes.BOOL
            if iswow2(h, ctypes.byref(pm), ctypes.byref(nm)):
                return pm.value == 0x014C  # IMAGE_FILE_MACHINE_I386
        else:
            iswow = getattr(kernel32, "IsWow64Process", None)
            if iswow:
                wow = wintypes.BOOL(False)
                iswow.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
                iswow.restype = wintypes.BOOL
                if iswow(h, ctypes.byref(wow)):
                    return bool(wow.value)
    finally:
        kernel32.CloseHandle(h)
    return False


def _peb_layout(pid: int) -> dict:
    """目标进程 PEB 布局参数：ProcessParameters/ImagePathName/CommandLine 偏移、指针与字符串大小。
    32 位 WOW64 与 64 位进程布局不同，读取错误偏移会拿到垃圾数据导致漏检/误检。"""
    if _is_32bit_process(pid):
        return {"pp": 0x10, "image": 0x38, "cmd": 0x40, "ptr": 4, "us": 8}
    return {"pp": 0x20, "image": 0x60, "cmd": 0x70, "ptr": 8, "us": 16}


def _process_still_matches(pid: int) -> bool:
    """终止前二次校验：确认 PID 当前指向的进程仍命中恶意特征库。
    防止扫描与处置之间进程退出、PID 被系统复用而误杀无辜进程。"""
    path = _process_path(pid)
    name = os.path.basename(path).lower().replace(".exe", "").strip() if path else ""
    if not name:
        for p in _enum_processes():
            if p["pid"] == pid:
                name = (p["name"] or "").lower().replace(".exe", "").strip()
                break
    return name in _MALICIOUS_PROCESS_NAMES


def _has_valid_signature(path: str, timeout: float = 3.0) -> Optional[bool]:
    """WinVerifyTrust 校验文件 Authenticode 签名有效性。

    返回三态：True=有效签名；False=无有效签名；None=无法验证（超时/异常）。
    有效签名或无法验证的正常程序都不自动处置（仅提示），避免误杀；
    仅当确认无有效签名时才继续终止。验证放入守护线程并设超时：
    CryptoAPI 在网络/文件受限时可能阻塞，超时视为"无法验证"保守跳过。
    """
    if not path:
        return None
    result = {}

    def _verify():
        result["ok"] = _verify_signature_sync(path)

    t = threading.Thread(target=_verify, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return None  # 验证超时 → 无法确认 → 保守跳过，不自动处置
    return result.get("ok", False)


def _verify_signature_sync(path: str) -> bool:
    """同步调用 WinVerifyTrust（需在带超时的线程中运行）"""
    try:
        wintrust = ctypes.WinDLL("wintrust", use_last_error=True)

        class WINTRUST_FILE_INFO(ctypes.Structure):
            _fields_ = [
                ("cbStruct", wintypes.DWORD),
                ("pcwszFilePath", wintypes.LPCWSTR),
                ("hFile", wintypes.HANDLE),
                ("pgKnownSubject", ctypes.c_void_p),
            ]

        class GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8),
            ]

        class WINTRUST_DATA(ctypes.Structure):
            _fields_ = [
                ("cbStruct", wintypes.DWORD),
                ("pPolicyCallbackData", ctypes.c_void_p),
                ("pSIPClientData", ctypes.c_void_p),
                ("dwUIChoice", wintypes.DWORD),
                ("fdwRevocationChecks", wintypes.DWORD),
                ("dwUnionChoice", wintypes.DWORD),
                ("pFile", ctypes.c_void_p),
                ("dwStateAction", wintypes.DWORD),
                ("hWVTStateData", ctypes.c_void_p),
                ("pwszURLReference", wintypes.LPCWSTR),
                ("dwProvFlags", wintypes.DWORD),
                ("dwUIContext", wintypes.DWORD),
                ("pSignatureSettings", ctypes.c_void_p),
            ]

        WINTRUST_ACTION_GENERIC_VERIFY_V2 = GUID(
            0x00AAC56B, 0xCD44, 0x11D0, (0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE))
        wintrust.WinVerifyTrust.argtypes = [wintypes.HANDLE, ctypes.POINTER(GUID), ctypes.c_void_p]
        wintrust.WinVerifyTrust.restype = ctypes.c_long

        file_info = WINTRUST_FILE_INFO(ctypes.sizeof(WINTRUST_FILE_INFO), path, None, None)
        data = WINTRUST_DATA()
        data.cbStruct = ctypes.sizeof(WINTRUST_DATA)
        data.dwUnionChoice = 1          # WTD_CHOICE_FILE
        data.pFile = ctypes.cast(ctypes.pointer(file_info), ctypes.c_void_p)
        data.dwStateAction = 0          # WTD_STATEACTION_IGNORE
        # 离线校验：禁用吊销联网检查 + 仅用缓存 URL，避免卡在网络请求上
        data.dwProvFlags = 0x00000010 | 0x00000800  # WTD_REVOCATION_CHECK_NONE | WTD_CACHE_ONLY_URL_RETRIEVAL
        return wintrust.WinVerifyTrust(None, ctypes.byref(WINTRUST_ACTION_GENERIC_VERIFY_V2),
                                       ctypes.byref(data)) == 0
    except Exception:
        return False


def _process_path(pid: int) -> str:
    """获取进程显示路径：优先读目标进程 PEB.ImagePathName（含伪装值，
    恶意软件常篡改 PEB 使显示路径/命令行暴露特征名），失败回退真实路径。
    按目标进程位数自适应 PEB 偏移（32 位 WOW64 与 64 位布局不同）"""
    PROCESS_QUERY_INFORMATION = 0x0400
    PROCESS_VM_READ = 0x0010
    h = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if h:
        try:
            pbi = PROCESS_BASIC_INFORMATION()
            if ntdll.NtQueryInformationProcess(h, 0, ctypes.byref(pbi),
                                               ctypes.sizeof(pbi), None) == 0 and pbi.PebBaseAddress:
                layout = _peb_layout(pid)
                pp_raw = _read_mem(h, pbi.PebBaseAddress + layout["pp"], layout["ptr"])
                if len(pp_raw) == layout["ptr"]:
                    pp = int.from_bytes(pp_raw, "little")
                    if pp:
                        us_raw = _read_mem(h, pp + layout["image"], layout["us"])
                        if len(us_raw) == layout["us"]:
                            length = int.from_bytes(us_raw[:2], "little")
                            buf_addr = int.from_bytes(us_raw[layout["ptr"]:layout["ptr"] * 2], "little")
                            if buf_addr and 0 < length <= 2048:
                                data = _read_mem(h, buf_addr, length)
                                if data:
                                    return data.decode("utf-16-le", "replace")
        finally:
            kernel32.CloseHandle(h)
    return _real_process_path(pid)


def _terminate_process(pid: int) -> bool:
    """OpenProcess + TerminateProcess，失败时 NtOpenProcess 兜底"""
    h = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
    if h:
        try:
            if kernel32.TerminateProcess(h, 1):
                return True
        finally:
            kernel32.CloseHandle(h)

    # NtOpenProcess + NtTerminateProcess 兜底
    class CLIENT_ID(ctypes.Structure):
        _fields_ = [("UniqueProcess", wintypes.HANDLE),
                    ("UniqueThread", wintypes.HANDLE)]

    class OBJECT_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Length", wintypes.ULONG),
                    ("RootDirectory", wintypes.HANDLE),
                    ("ObjectName", ctypes.c_void_p),
                    ("Attributes", wintypes.ULONG),
                    ("SecurityDescriptor", ctypes.c_void_p),
                    ("SecurityQualityOfService", ctypes.c_void_p)]

    ntdll.NtOpenProcess.argtypes = [
        ctypes.POINTER(wintypes.HANDLE), wintypes.ULONG,
        ctypes.POINTER(OBJECT_ATTRIBUTES), ctypes.POINTER(CLIENT_ID)]
    ntdll.NtOpenProcess.restype = ctypes.c_long
    ntdll.NtTerminateProcess.argtypes = [wintypes.HANDLE, ctypes.c_long]
    ntdll.NtTerminateProcess.restype = ctypes.c_long
    ntdll.NtClose.argtypes = [wintypes.HANDLE]

    cid = CLIENT_ID(pid, None)
    oa = OBJECT_ATTRIBUTES(ctypes.sizeof(OBJECT_ATTRIBUTES), None, None, 0, None, None)
    h = wintypes.HANDLE()
    if ntdll.NtOpenProcess(ctypes.byref(h), PROCESS_ALL_ACCESS,
                           ctypes.byref(oa), ctypes.byref(cid)) == 0:
        try:
            return ntdll.NtTerminateProcess(h, 1) == 0
        finally:
            ntdll.NtClose(h)
    return False


def _listening_ports() -> set:
    """GetExtendedTcpTable 枚举监听端口（iphlpapi）"""
    try:
        iphlpapi = ctypes.WinDLL("iphlpapi", use_last_error=True)
        iphlpapi.GetExtendedTcpTable.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD),
            wintypes.BOOL, wintypes.ULONG, wintypes.ULONG, wintypes.ULONG]
        iphlpapi.GetExtendedTcpTable.restype = wintypes.ULONG

        size = wintypes.DWORD(0)
        iphlpapi.GetExtendedTcpTable(None, ctypes.byref(size), False, AF_INET,
                                     TCP_TABLE_OWNER_PID_LISTENER, 0)
        if not size.value:
            return set()
        buf = ctypes.create_string_buffer(size.value)
        if iphlpapi.GetExtendedTcpTable(buf, ctypes.byref(size), False, AF_INET,
                                        TCP_TABLE_OWNER_PID_LISTENER, 0) != 0:
            return set()
        n = ctypes.c_ulong.from_buffer(buf).value
        ports = set()
        offset = 4  # 表头 dwNumEntries 之后是行数组
        row_size = ctypes.sizeof(MIB_TCPROW_OWNER_PID)
        for i in range(n):
            row = MIB_TCPROW_OWNER_PID.from_buffer(buf, offset + i * row_size)
            if row.dwState == MIB_TCP_STATE_LISTEN:
                ports.add(socket.ntohs(row.dwLocalPort))
        return ports
    except Exception:
        return set()


def _firewall_off_profiles() -> List[str]:
    """读取防火墙策略注册表，返回已关闭防火墙的配置文件名"""
    off = []
    for label, key_path in _FW_POLICY_KEYS.items():
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as k:
                v, _ = winreg.QueryValueEx(k, "EnableFirewall")
                if v == 0:
                    off.append(label)
        except OSError:
            continue
    return off


# ------------------------------------------------------------
# 安全扫描器
# ------------------------------------------------------------
class SecurityScanner:
    """自研安全检测与清理（纯 Win32 API）"""

    # ---------- 恶意进程 ----------
    def scan_processes(self) -> List[dict]:
        """枚举进程并按特征库匹配，返回 [{'pid','name','path'}]。

        进程名（Name）与可执行路径文件名（ExecutablePath）任一命中特征库即告警：
        恶意软件常伪装进程名（PEB 篡改），但可执行路径往往暴露矿机/木马文件名。
        名称命中的条目必须补查真实路径：路径为空或位于系统目录时不处置，避免误杀系统组件。
        """
        all_procs = _enum_processes()
        if not all_procs:
            return []

        out = []
        pending = []
        for p in all_procs:
            name = (p["name"] or "").lower()
            if self._is_malicious_name(name):
                real = _real_process_path(p["pid"])
                if real and self._not_system(real):  # 路径为空或系统目录内 → 跳过
                    out.append({"pid": p["pid"], "name": p["name"], "path": real})
            else:
                pending.append(p)

        # 其余进程取路径匹配（并行加速，进程伪装后路径暴露特征）
        if pending:
            with ThreadPoolExecutor(max_workers=8) as pool:
                for p, path in zip(pending, pool.map(_process_path, [q["pid"] for q in pending])):
                    if self._malicious_path(path) and self._not_system(path):
                        out.append({"pid": p["pid"], "name": p["name"], "path": path})

        return out

    @staticmethod
    def _is_malicious_name(name: str) -> bool:
        return name.replace(".exe", "").strip() in _MALICIOUS_PROCESS_NAMES

    @staticmethod
    def _malicious_path(path: str) -> bool:
        """匹配可执行路径文件名（进程名伪装后路径暴露特征）"""
        if not path:
            return False
        base = os.path.basename(path).lower().replace(".exe", "").strip()
        return base in _MALICIOUS_PROCESS_NAMES

    @staticmethod
    def _not_system(path: str) -> bool:
        if not path:
            return True
        try:
            parts = Path(path).parts
        except Exception:
            return True
        return not any(p in _SYSTEM_DIRS for p in (x.lower() for x in parts))

    # ---------- 恶意启动项 ----------
    def scan_startup(self) -> List[dict]:
        """扫描注册表 Run/RunOnce 与启动文件夹，返回 [{'where','name','command','hive','vtype'}]"""
        found: List[dict] = []

        def _check_command(where: str, name: str, command: str,
                           hive: str = None, vtype: int = None):
            if not command:
                return
            cmd_lower = command.lower()
            if any(m in cmd_lower for m in _MALICIOUS_STARTUP_MARKERS):
                found.append({"where": where, "name": name, "command": command,
                              "hive": hive, "vtype": vtype})

        for hive, key_path in _STARTUP_REG_KEYS:
            hive_label = "HKLM" if hive == winreg.HKEY_LOCAL_MACHINE else "HKCU"
            try:
                with winreg.OpenKey(hive, key_path) as k:
                    i = 0
                    while True:
                        try:
                            name, value, vtype = winreg.EnumValue(k, i)
                        except OSError:
                            break
                        i += 1
                        _check_command(key_path, name, value, hive_label, vtype)
            except OSError:
                continue

        for base in (Path.home(), Path(r"C:\Users\Public")):
            startup = base / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
            if startup.is_dir():
                for lnk in startup.glob("*.lnk"):
                    _check_command(str(startup), lnk.stem, lnk.stem, "folder")
        return found

    def _quarantine_startup(self, entry: dict) -> bool:
        """删除前备份：快捷方式移动到隔离目录；注册表值写入隔离 JSON。备份失败返回 False"""
        try:
            if entry.get("hive") == "folder":
                src = Path(entry["where"]) / f"{entry['name']}.lnk"
                if not src.exists():
                    return False
                _QUARANTINE_LNK.mkdir(parents=True, exist_ok=True)
                src.rename(_QUARANTINE_LNK / f"{entry['name']}_{int(time.time() * 1000)}.lnk")
                return True

            if entry.get("hive") not in ("HKLM", "HKCU"):
                return False
            hive = winreg.HKEY_LOCAL_MACHINE if entry["hive"] == "HKLM" else winreg.HKEY_CURRENT_USER
            with winreg.OpenKey(hive, entry["where"], 0, winreg.KEY_QUERY_VALUE) as k:
                data, vtype = winreg.QueryValueEx(k, entry["name"])
            if isinstance(data, bytes):
                data = {"kind": "bytes", "value": base64.b64encode(data).decode()}
            else:
                data = {"kind": "str", "value": str(data)}
            _QUARANTINE_REG.mkdir(parents=True, exist_ok=True)
            record = {
                "hive": entry["hive"], "key": entry["where"], "name": entry["name"],
                "type": vtype, "data": data,
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            name_safe = entry["name"].replace("\\", "_").replace("/", "_")
            (_QUARANTINE_REG / f"startup_{int(time.time() * 1000)}_{name_safe}.json").write_text(
                json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            return True
        except OSError:
            return False

    def _remove_startup(self, entry: dict) -> bool:
        """删除恶意启动项：先备份到隔离区（备份失败则不删，保证可恢复），再按扫描到的精确位置删除"""
        if not self._quarantine_startup(entry):
            return False
        if entry.get("hive") == "folder":
            return True  # 快捷方式已移动到隔离目录，原位置即已清除
        if entry.get("hive") not in ("HKLM", "HKCU"):
            return False
        hive = winreg.HKEY_LOCAL_MACHINE if entry["hive"] == "HKLM" else winreg.HKEY_CURRENT_USER
        try:
            with winreg.OpenKey(hive, entry["where"], 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, entry["name"])
                return True
        except OSError:
            return False

    def _record_process(self, proc: dict) -> None:
        """终止进程前写入审计记录（隔离日志，误杀可追溯）"""
        try:
            _QUARANTINE_LOG.mkdir(parents=True, exist_ok=True)
            record = {
                "pid": proc["pid"], "name": proc.get("name"), "path": proc.get("path"),
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            (_QUARANTINE_LOG / f"process_{proc['pid']}_{int(time.time() * 1000)}.json").write_text(
                json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    def restore_quarantine(self) -> int:
        """从隔离区恢复被清理的启动项（进程已终止仅记录，无法恢复），返回恢复数量"""
        restored = 0
        for f in sorted(_QUARANTINE_REG.glob("*.json")):
            try:
                rec = json.loads(f.read_text(encoding="utf-8"))
                hive = winreg.HKEY_LOCAL_MACHINE if rec.get("hive") == "HKLM" else winreg.HKEY_CURRENT_USER
                with winreg.OpenKey(hive, rec["key"], 0, winreg.KEY_SET_VALUE) as k:
                    data = rec["data"]
                    value = base64.b64decode(data["value"]) if data.get("kind") == "bytes" else data["value"]
                    winreg.SetValueEx(k, rec["name"], 0, rec.get("type", winreg.REG_SZ), value)
                f.unlink()
                restored += 1
            except (OSError, KeyError, ValueError):
                continue
        for f in _QUARANTINE_LNK.glob("*.lnk"):
            try:
                name = f.stem.rsplit("_", 1)[0] + ".lnk"
                for base in (Path.home(), Path(r"C:\Users\Public")):
                    dst = base / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / name
                    if not dst.exists():
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        f.rename(dst)
                        restored += 1
                        break
            except OSError:
                continue
        return restored

    # ---------- 网络防护检查 ----------
    def check_network(self) -> dict:
        """检查监听端口暴露与防火墙启用状态。

        本地服务端口（SMB/NetBIOS/mDNS 等系统默认开放、防火墙精细管控的端口）不参与
        「高危暴露」判定，避免误报；高危、且非本地服务的监听端口才算暴露风险。
        端口策略（高危清单/本地服务白名单/端口说明）全部来自 security.json network 段，
        运行期可覆盖，零硬编码。
        """
        from winapp_migrator.core.security_engine.config import config
        high_risk = {int(x) for x in config.list_of("network.high_risk_ports", sorted(_HIGH_RISK_PORTS))}
        local_ok = {int(x) for x in config.list_of("network.local_allowed_ports", sorted(_LOCAL_SERVICE_PORTS))}
        names = {int(k): str(v) for k, v in dict(config.get("network.port_names") or {}).items()}
        exposed = sorted(
            ({"port": p, "name": names.get(p) or ""} for p in _listening_ports()
             if p in high_risk and p not in local_ok),
            key=lambda x: x["port"],
        )
        fw_off = _firewall_off_profiles()
        return {
            "firewall_off": fw_off,
            "high_risk_listening": exposed,
            "risk": bool(fw_off) or bool(exposed),
        }

    # ---------- 组合扫描 ----------
    def sweep(self, include_network: bool = False) -> dict:
        """一次完整巡检：检测恶意进程/启动项并自动清理；可选网络检查"""
        summary = {
            "killed": [],      # 已结束的恶意进程
            "removed": [],     # 已删除的恶意启动项
            "failed": [],      # 检测到但清理失败（仍需通知用户）
            "signed": [],      # 签名有效的同名进程（仅提示不处置）
            "network": None,
            "risk": False,
        }
        _enable_debug_privilege()

        for p in self.scan_processes():
            if not _process_still_matches(p["pid"]):
                continue  # 进程已退出或 PID 被复用，放弃处置避免误杀
            # 签名校验：有效签名或无法验证（超时/受限）均不自动处置，仅提示
            real = _real_process_path(p["pid"])
            sig = _has_valid_signature(real) if real else None
            if sig is not False:
                tag = "签名有效" if sig is True else "签名验证超时"
                summary["signed"].append(f"{p['name']} (PID {p['pid']})（{tag}，跳过）")
                continue
            self._record_process(p)
            if _terminate_process(p["pid"]):
                summary["killed"].append(f"{p['name']} (PID {p['pid']})")
            else:
                summary["failed"].append(f"进程 {p['name']} (PID {p['pid']}) 清理失败")

        for s in self.scan_startup():
            if self._remove_startup(s):
                summary["removed"].append(f"{s['where']} → {s['name']}")
            else:
                summary["failed"].append(f"启动项 {s['name']} 清理失败")

        if include_network:
            net = self.check_network()
            summary["network"] = net
            summary["risk"] = summary["risk"] or net["risk"]

        return summary
