import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from zhuzhu_Copilot.core import network_defense as nd
from zhuzhu_Copilot.core.security import SecurityScanner

# 纯逻辑单元：IP 转换 / 回环判定
assert nd._fmt_ip(0) == "0.0.0.0"
assert nd._ip_to_dw("127.0.0.1") == int.from_bytes(b"\x7f\x00\x00\x01", "little")
assert nd._is_loopback("127.0.0.1") and nd._is_loopback("127.5.5.5") and nd._is_loopback("::1")
assert not nd._is_loopback("192.168.1.10")

# 配置驱动阈值（security.json network 段）
d = nd.NetworkDefender()
assert d.scan_ports == 60, d.scan_ports
assert d.udp_threshold == 4000, d.udp_threshold
print("[OK] network_defense 纯逻辑")

# SMB 本地端口误报修复：构造监听端口集，校验判定策略
import zhuzhu_Copilot.core.security as sec
from unittest.mock import patch
scanner = SecurityScanner()
with patch.object(sec, "_listening_ports", lambda: {445, 139, 3389, 3306}):
    res = scanner.check_network()
ports = {x["port"] for x in res["high_risk_listening"]}
assert 445 not in ports, res   # SMB 不再误报
assert 139 not in ports, res   # NetBIOS 不再误报
assert 3389 in ports and 3306 in ports, res  # RDP/MySQL 仍会提示
print("[OK] SMB 误报修复:", res["high_risk_listening"])
print("全部通过")