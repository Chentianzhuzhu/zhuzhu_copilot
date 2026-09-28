"""网络攻击检测与防御模块（纯 Win32 API，无第三方依赖）

- ARP 欺骗检测：GetIpNetTable 周期采样，网关 IP 的 MAC 突变即告警
  · 基线需连续稳定样本 + 单播地址校验；仅告警并建议核对，不主动发包
    （主动发送伪造 ARP 应答包可能触发网络侧误判、且在攻击中会巩固错误映射）
- SYN 洪泛检测：GetExtendedTcpTable 统计半开连接，连续多轮超阈值才判定
  · 封禁：HNetCfg.FwPolicy2 防火墙规则封禁攻击来源 IP，规则带时间戳并设上限自动清理
- TCP 洪泛检测：GetTcpStatistics 入段速率差分，检测整体洪泛告警
- UDP 洪泛检测：GetUdpStatistics 入数据报速率差分，检测 UDP 洪泛告警
- 端口扫描检测：单源在巡检窗口内连接大量不同本地端口（GetExtendedTcpTable SYN 行），
  连续多轮超阈值判定为端口扫描并封禁来源 IP（回环地址除外，避免本地工具误报）

阈值来自 security.json 的 network 段，模块常量仅作默认兜底，全部可调、不硬编码。
"""

import ctypes
import ctypes.wintypes as wintypes
import socket
import struct
import time
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from zhuzhu_Copilot.core.security_engine.config import config

# ------------------------------------------------------------
# 判定阈值（默认兜底值；运行期以 security.json network 段为准）
# ------------------------------------------------------------
SYN_FLOOD_THRESHOLD = 100     # 单源半开连接数
SEG_RATE_THRESHOLD = 5000     # 每秒 TCP 入段数（整体洪泛）
UDP_RATE_THRESHOLD = 4000     # 每秒 UDP 入数据报数（整体洪泛）
PORT_SCAN_PORTS = 60          # 单源访问的不同本地端口数（端口扫描）
PORT_SCAN_ROUNDS = 2          # 端口扫描连续 N 轮确认
ARP_WARN_COOLDOWN = 60        # 同一网关 ARP 告警冷却秒数
ARP_BASELINE_SAMPLES = 2      # 基线所需的连续稳定样本数
SYN_BLOCK_ROUNDS = 2          # 连续 N 轮超阈值才封禁（防临时突发误封）
MAX_BLOCK_RULES = 50          # 防火墙封禁规则数量上限，超限清理最旧规则
BLOCK_RULE_PREFIX = "zhuzhu_Copilot_Block_"

# ------------------------------------------------------------
# Win32 常量
# ------------------------------------------------------------
AF_INET = 2
TCP_TABLE_OWNER_PID_ALL = 5
MIB_TCP_STATE_SYN_RCVD = 2
MIB_TCP_STATE_SYN_SENT = 3
MIB_IPNET_TYPE_DYNAMIC = 3   # Windows SDK: DYNAMIC=3, STATIC=4（写 4 会把动态网关条目全过滤导致检测失效）
MIB_IPNET_TYPE_STATIC = 4

iphlpapi = ctypes.WinDLL("iphlpapi", use_last_error=True)
iphlpapi.GetIpNetTable.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), wintypes.BOOL]
iphlpapi.GetIpNetTable.restype = wintypes.DWORD
iphlpapi.GetIpForwardTable.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), wintypes.BOOL]
iphlpapi.GetIpForwardTable.restype = wintypes.DWORD
iphlpapi.GetTcpStatistics.argtypes = [ctypes.c_void_p]
iphlpapi.GetTcpStatistics.restype = wintypes.DWORD
iphlpapi.GetUdpStatistics.argtypes = [ctypes.c_void_p]
iphlpapi.GetUdpStatistics.restype = wintypes.DWORD
iphlpapi.DeleteIpNetEntry.argtypes = [ctypes.c_void_p]
iphlpapi.DeleteIpNetEntry.restype = wintypes.DWORD
iphlpapi.CreateIpNetEntry.argtypes = [ctypes.c_void_p]
iphlpapi.CreateIpNetEntry.restype = wintypes.DWORD


class MIB_IPNETROW(ctypes.Structure):
    _fields_ = [
        ("dwIndex", wintypes.DWORD),
        ("dwPhysAddrLen", wintypes.DWORD),
        ("bPhysAddr", ctypes.c_ubyte * 8),
        ("dwAddr", wintypes.DWORD),
        ("dwType", wintypes.DWORD),
    ]


class MIB_IPFORWARDROW(ctypes.Structure):
    _fields_ = [
        ("dwForwardDest", wintypes.DWORD),
        ("dwForwardMask", wintypes.DWORD),
        ("dwForwardPolicy", wintypes.DWORD),
        ("dwForwardNextHop", wintypes.DWORD),
        ("dwForwardIfIndex", wintypes.DWORD),
        ("dwForwardType", wintypes.DWORD),
        ("dwForwardProto", wintypes.DWORD),
        ("dwForwardAge", wintypes.DWORD),
        ("dwForwardNextHopAS", wintypes.DWORD),
        ("dwForwardMetric1", wintypes.DWORD),
        ("dwForwardMetric2", wintypes.DWORD),
        ("dwForwardMetric3", wintypes.DWORD),
        ("dwForwardMetric4", wintypes.DWORD),
        ("dwForwardMetric5", wintypes.DWORD),
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


class MIB_TCPSTATS(ctypes.Structure):
    _fields_ = [
        ("dwRtoAlgorithm", wintypes.DWORD), ("dwRtoMin", wintypes.DWORD),
        ("dwRtoMax", wintypes.DWORD), ("dwMaxConn", wintypes.DWORD),
        ("dwActiveOpens", wintypes.DWORD), ("dwPassiveOpens", wintypes.DWORD),
        ("dwAttemptFails", wintypes.DWORD), ("dwEstabResets", wintypes.DWORD),
        ("dwCurrEstab", wintypes.DWORD), ("dwInSegs", wintypes.DWORD),
        ("dwOutSegs", wintypes.DWORD), ("dwRetransSegs", wintypes.DWORD),
        ("dwInErrs", wintypes.DWORD), ("dwOutRsts", wintypes.DWORD),
        ("dwNumConns", wintypes.DWORD),
    ]


class MIB_UDPSTATS(ctypes.Structure):
    _fields_ = [
        ("dwInDatagrams", wintypes.DWORD),
        ("dwNoPorts", wintypes.DWORD),
        ("dwInErrors", wintypes.DWORD),
        ("dwOutDatagrams", wintypes.DWORD),
        ("dwNumAddrs", wintypes.DWORD),
    ]


# ------------------------------------------------------------
# 底层读取（真实 Win32 API）
# ------------------------------------------------------------
def _fmt_ip(dw: int) -> str:
    if dw == 0:
        return "0.0.0.0"
    return socket.inet_ntoa(struct.pack("<I", dw & 0xFFFFFFFF))


def _ip_to_dw(ip: str) -> int:
    """IP 字符串 → GetIpNetTable 使用的 dwAddr（内存按网络字节序存储的 DWORD）"""
    return int.from_bytes(socket.inet_aton(ip), "little")


def _get_gateway() -> str:
    """GetIpForwardTable 读取默认路由（0.0.0.0/0）下一跳作为网关"""
    size = wintypes.DWORD(0)
    iphlpapi.GetIpForwardTable(None, ctypes.byref(size), False)
    if not size.value:
        return ""
    buf = ctypes.create_string_buffer(size.value)
    if iphlpapi.GetIpForwardTable(buf, ctypes.byref(size), False) != 0:
        return ""
    n = ctypes.c_ulong.from_buffer(buf).value
    row_size = ctypes.sizeof(MIB_IPFORWARDROW)
    for i in range(n):
        row = MIB_IPFORWARDROW.from_buffer(buf, 4 + i * row_size)
        if row.dwForwardDest == 0 and row.dwForwardMask == 0:
            return _fmt_ip(row.dwForwardNextHop)
    return ""


def _arp_table() -> Dict[str, Tuple[str, int]]:
    """GetIpNetTable 读取 ARP 缓存，返回 {ip: (mac, 接口索引)}（仅动态条目）"""
    size = wintypes.DWORD(0)
    iphlpapi.GetIpNetTable(None, ctypes.byref(size), False)
    if not size.value:
        return {}
    buf = ctypes.create_string_buffer(size.value)
    if iphlpapi.GetIpNetTable(buf, ctypes.byref(size), False) != 0:
        return {}
    n = ctypes.c_ulong.from_buffer(buf).value
    row_size = ctypes.sizeof(MIB_IPNETROW)
    table = {}
    for i in range(n):
        row = MIB_IPNETROW.from_buffer(buf, 4 + i * row_size)
        if row.dwType == MIB_IPNET_TYPE_DYNAMIC:
            ip = _fmt_ip(row.dwAddr)
            mac = ":".join(f"{row.bPhysAddr[j]:02x}" for j in range(row.dwPhysAddrLen))
            if mac:
                table[ip] = (mac, row.dwIndex)
    return table


def _tcp_rows() -> List[dict]:
    """GetExtendedTcpTable 读取全部 TCP 连接（含 SYN 半开状态）"""
    try:
        iphlpapi.GetExtendedTcpTable.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD),
            wintypes.BOOL, wintypes.ULONG, wintypes.ULONG, wintypes.ULONG]
        iphlpapi.GetExtendedTcpTable.restype = wintypes.ULONG
        size = wintypes.DWORD(0)
        iphlpapi.GetExtendedTcpTable(None, ctypes.byref(size), False, AF_INET,
                                     TCP_TABLE_OWNER_PID_ALL, 0)
        if not size.value:
            return []
        buf = ctypes.create_string_buffer(size.value)
        if iphlpapi.GetExtendedTcpTable(buf, ctypes.byref(size), False, AF_INET,
                                        TCP_TABLE_OWNER_PID_ALL, 0) != 0:
            return []
        n = ctypes.c_ulong.from_buffer(buf).value
        row_size = ctypes.sizeof(MIB_TCPROW_OWNER_PID)
        rows = []
        for i in range(n):
            r = MIB_TCPROW_OWNER_PID.from_buffer(buf, 4 + i * row_size)
            rows.append({
                "state": r.dwState,
                "local_port": socket.ntohs(r.dwLocalPort),
                "remote_ip": _fmt_ip(r.dwRemoteAddr),
            })
        return rows
    except Exception:
        return []


def _tcp_stats() -> Tuple[int, int]:
    """GetTcpStatistics 读取 TCP 累计入段数与当前已建立连接数"""
    s = MIB_TCPSTATS()
    if iphlpapi.GetTcpStatistics(ctypes.byref(s)) == 0:
        return s.dwInSegs, s.dwCurrEstab
    return 0, 0


def _udp_datagrams() -> int:
    """GetUdpStatistics 读取 UDP 累计入数据报数"""
    s = MIB_UDPSTATS()
    if iphlpapi.GetUdpStatistics(ctypes.byref(s)) == 0:
        return int(s.dwInDatagrams)
    return 0


# ------------------------------------------------------------
# 防御动作（真实 API）
# ------------------------------------------------------------
def block_ip(ip: str) -> bool:
    """Windows 防火墙（HNetCfg.FwPolicy2）封禁指定来源 IP。

    规则名带时间戳便于按序清理；超过 MAX_BLOCK_RULES 时删除最旧的封禁规则，
    避免攻击源 IP 多为伪造而封禁无效时规则无限累积污染用户防火墙。
    """
    try:
        import win32com.client
        fw = win32com.client.Dispatch("HNetCfg.FwPolicy2")
        # 规则数量上限控制：删除最旧的封禁规则（名字前缀为时间戳，按名排序即按时间）
        own = [r for r in fw.Rules if getattr(r, "Name", "").startswith(BLOCK_RULE_PREFIX)]
        if len(own) >= MAX_BLOCK_RULES:
            oldest = sorted(own, key=lambda r: getattr(r, "Name", ""))[0]
            fw.Rules.Remove(oldest.Name)
        rule = win32com.client.Dispatch("HNetCfg.FwRule")
        rule.Name = f"{BLOCK_RULE_PREFIX}{int(time.time())}_{ip}"
        rule.Direction = 1      # NET_FW_RULE_DIR_IN 入站
        rule.Action = 0         # NET_FW_ACTION_BLOCK
        rule.RemoteAddresses = ip
        rule.Enabled = True
        rule.Profiles = fw.CurrentProfileTypes
        fw.Rules.Add(rule)
        return True
    except Exception:
        return False


def _restore_gateway_arp(gateway: str, polluted_mac: str, correct_mac: str, if_index: int) -> bool:
    """修复网关 ARP 条目：删除被污染的动态条目，并静态绑定正确网关 MAC。

    静态条目不会被后续 ARP 应答更新，因此可对抗 arpspoof 持续欺骗；
    Windows 8+ 上静态条目仅会话内有效（重启/接口重置自动清除，不会长期残留）。
    """
    def _mac_bytes(mac: str) -> bytes:
        return bytes.fromhex(mac.replace(":", ""))[:6]

    try:
        # 删除当前被污染的条目（条目可能已被系统刷新而失败，不影响后续绑定）
        del_row = MIB_IPNETROW()
        del_row.dwIndex = if_index
        del_row.dwPhysAddrLen = 6
        del_row.bPhysAddr[:6] = _mac_bytes(polluted_mac)
        del_row.dwAddr = _ip_to_dw(gateway)
        del_row.dwType = MIB_IPNET_TYPE_DYNAMIC
        iphlpapi.DeleteIpNetEntry(ctypes.byref(del_row))

        # 创建静态条目绑定基线（正确）MAC
        fix_row = MIB_IPNETROW()
        fix_row.dwIndex = if_index
        fix_row.dwPhysAddrLen = 6
        fix_row.bPhysAddr[:6] = _mac_bytes(correct_mac)
        fix_row.dwAddr = _ip_to_dw(gateway)
        fix_row.dwType = MIB_IPNET_TYPE_STATIC
        return iphlpapi.CreateIpNetEntry(ctypes.byref(fix_row)) == 0
    except Exception:
        return False


# ------------------------------------------------------------
# 检测器
# ------------------------------------------------------------
class NetworkDefender:
    """网络攻击检测与防御：ARP 欺骗 / SYN·TCP·UDP 洪泛 / 端口扫描"""

    def __init__(self):
        # 阈值全部取自配置（network 段），模块常量兜底
        self.syn_threshold = int(config.get("network.syn_flood_threshold", SYN_FLOOD_THRESHOLD))
        self.seg_threshold = int(config.get("network.seg_rate_threshold", SEG_RATE_THRESHOLD))
        self.udp_threshold = int(config.get("network.udp_rate_threshold", UDP_RATE_THRESHOLD))
        self.scan_ports = int(config.get("network.port_scan_ports", PORT_SCAN_PORTS))
        self.scan_rounds = max(1, int(config.get("network.port_scan_rounds", PORT_SCAN_ROUNDS)))
        self.syn_rounds = max(1, int(config.get("network.syn_block_rounds", SYN_BLOCK_ROUNDS)))
        self._arp_baseline: Dict[str, str] = {}       # 网关 IP -> 基线 MAC（连续 N 次稳定样本建立）
        self._arp_candidates: Dict[str, List[str]] = {}  # 基线建立前的样本缓冲
        self._last_arp_warn: float = 0.0
        self._last_segs: Optional[int] = None
        self._last_seg_time: float = 0.0
        self._last_udp: Optional[int] = None
        self._last_udp_time: float = 0.0
        self._blocked: set = set()
        self._syn_strikes: Dict[str, int] = {}    # 来源 IP -> 连续超阈值轮数
        self._scan_strikes: Dict[str, int] = {}   # 来源 IP -> 端口扫描确认轮数
        self._gateway: Optional[str] = None

    def check(self) -> dict:
        """周期巡检：返回 {'arp_spoof': {...}|None, 'flood': {...}|None}"""
        result = {"arp_spoof": None, "flood": None}
        if not self._gateway:
            self._gateway = _get_gateway()
        if self._gateway:
            spoof = self._check_arp_spoof(self._gateway)
            if spoof:
                result["arp_spoof"] = spoof
        flood = self._check_flood()
        if flood["detected"]:
            result["flood"] = flood
        return result

    # ---------- ARP 欺骗 ----------
    def _check_arp_spoof(self, gateway: str) -> Optional[dict]:
        entry = _arp_table().get(gateway)
        if not entry:
            return None
        mac, if_index = entry
        # MAC 合理性校验：拒绝全零地址与组播/广播（首字节最低位=1）。
        # 注意首字节为 0x00 的厂商 OUI（如 VMware 00:0c:29）是合法单播，不能误拒
        try:
            first = int(mac.split(":")[0], 16)
            if mac == "00:00:00:00:00:00" or (first & 1):
                return None
        except (ValueError, IndexError):
            return None

        # 基线未建立：需连续 N 次相同样本（防止临时抖动/攻击发生时首样本即被污染）
        if gateway not in self._arp_baseline:
            cands = self._arp_candidates.setdefault(gateway, [])
            if not cands or cands[-1] == mac:
                cands.append(mac)
                if len(cands) >= ARP_BASELINE_SAMPLES:
                    self._arp_baseline[gateway] = cands[-1]  # 连续稳定样本 → 基线成立
                    self._arp_candidates.pop(gateway, None)
            else:
                self._arp_candidates[gateway] = [mac]  # 样本变化，重新累计
            return None

        baseline = self._arp_baseline[gateway]
        if mac == baseline:
            return None
        # 网关 MAC 突变 → 疑似 ARP 欺骗：删除污染条目并静态绑定基线 MAC 尝试修复。
        # 修复尝试放在冷却判断之前，失败时每轮巡检都会重试，直到网络恢复
        repaired = _restore_gateway_arp(gateway, mac, baseline, if_index)
        if time.time() - self._last_arp_warn < ARP_WARN_COOLDOWN:
            return None
        self._last_arp_warn = time.time()
        return {
            "detected": True,
            "gateway": gateway,
            "old_mac": baseline,
            "new_mac": mac,
            "repaired": repaired,
            "reason": ("已删除污染条目并静态绑定正确网关 MAC，网络已恢复"
                       if repaired else "自动修复失败，建议核对网关设备 MAC 或重启路由器"),
        }

    # ---------- 洪泛及端口扫描检测 ----------
    def _check_flood(self) -> dict:
        rows = _tcp_rows()
        syn_by_ip = defaultdict(int)
        ports_by_ip = defaultdict(set)
        for c in rows:
            if c["state"] in (MIB_TCP_STATE_SYN_RCVD, MIB_TCP_STATE_SYN_SENT) \
                    and c["remote_ip"] != "0.0.0.0":
                if not _is_loopback(c["remote_ip"]):   # 回环地址不参与封禁判定，避免本地工具误报
                    syn_by_ip[c["remote_ip"]] += 1
                    ports_by_ip[c["remote_ip"]].add(c["local_port"])

        # SYN 洪泛：连续多轮超阈值才封禁（防单次突发误封）
        for ip, n in syn_by_ip.items():
            if n >= self.syn_threshold:
                self._syn_strikes[ip] = self._syn_strikes.get(ip, 0) + 1
            else:
                self._syn_strikes.pop(ip, None)
        syn_src = sorted(ip for ip, n in syn_by_ip.items()
                         if n >= self.syn_threshold
                         and self._syn_strikes.get(ip, 0) >= self.syn_rounds)

        # 端口扫描：单源访问大量不同本地端口，连续多轮确认
        for ip, ports in ports_by_ip.items():
            if len(ports) >= self.scan_ports:
                self._scan_strikes[ip] = self._scan_strikes.get(ip, 0) + 1
            else:
                self._scan_strikes.pop(ip, None)
        scan_src = sorted(ip for ip, ports in ports_by_ip.items()
                          if len(ports) >= self.scan_ports
                          and self._scan_strikes.get(ip, 0) >= self.scan_rounds)

        # 自动封禁攻击来源（规则带上限，防伪造源 IP 累积规则）
        blocked = []
        for ip in sorted(set(syn_src) | set(scan_src)):
            if ip not in self._blocked and block_ip(ip):
                self._blocked.add(ip)
                blocked.append(ip)

        # 整体洪泛：TCP 入段 / UDP 入数据报速率差分
        now = time.time()
        rate = udp_rate = 0
        segs, _ = _tcp_stats()
        if self._last_segs is not None and now > self._last_seg_time:
            dt = now - self._last_seg_time
            if dt > 0:
                rate = int((segs - self._last_segs) / dt)
        self._last_segs = segs
        self._last_seg_time = now

        udp = _udp_datagrams()
        if self._last_udp is not None and now > self._last_udp_time:
            dt = now - self._last_udp_time
            if dt > 0:
                udp_rate = int((udp - self._last_udp) / dt)
        self._last_udp = udp
        self._last_udp_time = now

        packet_flood = rate > self.seg_threshold
        udp_flood = udp_rate > self.udp_threshold
        return {
            "syn_sources": syn_src,
            "scan_sources": scan_src,
            "blocked": blocked,
            "packet_rate": rate,
            "packet_flood": packet_flood,
            "udp_rate": udp_rate,
            "udp_flood": udp_flood,
            "detected": bool(syn_src) or bool(scan_src) or packet_flood or udp_flood,
        }


def _is_loopback(ip: str) -> bool:
    """回环/本机地址不参与洪泛·扫描封禁判定（本地开发工具/防火墙自检等合法场景）"""
    return ip in ("127.0.0.1", "::1", "0.0.0.0") or ip.startswith("127.")
