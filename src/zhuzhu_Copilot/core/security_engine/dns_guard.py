"""DNS 劫持检测与 hosts 保护：检测 hosts 文件中的域名劫持/屏蔽条目，监控 hosts 篡改。

- 解析 hosts 非注释行，若「可疑域名列表」中的域名被写入 hosts，视为劫持（正常用户不会
  主动把这些常用域名（google/github/microsoft 等）写进 hosts，写入即强劫持信号）
- 记录 hosts 内容哈希基线，检测两次巡检之间是否被篡改
- 域名列表与 hosts 路径均来自配置，零硬编码
"""
import hashlib
from pathlib import Path
from typing import List

from zhuzhu_Copilot.core.security_engine.config import config


class DnsGuard:
    """hosts 劫持与篡改监控器"""

    def __init__(self):
        self._hosts = Path(config.expand(
            str(config.get("dns.hosts_path", "C:/Windows/System32/drivers/etc/hosts"))))
        self._baseline_hash = self._hash()

    def _hash(self) -> str:
        try:
            return hashlib.sha256(self._hosts.read_bytes()).hexdigest()
        except OSError:
            return ""

    def check(self) -> dict:
        """返回 {'hijacked': [{domain,ip}], 'changed': bool}"""
        if not config.enabled("dns"):
            return {"hijacked": [], "changed": False}
        hijacked = self._detect_hijack()
        cur = self._hash()
        changed = bool(self._baseline_hash) and cur != self._baseline_hash
        self._baseline_hash = cur
        return {"hijacked": hijacked, "changed": changed}

    def _detect_hijack(self) -> List[dict]:
        domains = {d.lower().rstrip(".") for d in config.list_of("dns.suspicious_domains", [])}
        if not domains:
            return []
        found: List[dict] = []
        try:
            lines = self._hosts.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return []
        for line in lines:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            parts = s.split()
            if len(parts) < 2:
                continue
            ip = parts[0]
            for domain in parts[1:]:
                if domain.lower().rstrip(".") in domains:
                    found.append({"domain": domain, "ip": ip})
        return found


dns_guard = DnsGuard()
