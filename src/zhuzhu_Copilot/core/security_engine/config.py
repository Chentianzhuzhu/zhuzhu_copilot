"""安全引擎配置中心：加载 config/security.json（内置默认 + 用户覆盖），统一提供引擎级配置。

职责分工：
- config/security_rules.json  承载「检测规则」（RuleEngine 消费）
- config/security.json        承载「引擎配置」（本模块消费）：模块开关、阈值、路径、特征库

所有路径支持 %VAR% / ~ 展开；缺失字段回退到安全默认值，绝不硬编码到业务逻辑。
"""
from zhuzhu_Copilot import app_identity
import json
import os
import sys
from pathlib import Path
from typing import Any

logger = __import__("zhuzhu_Copilot.utils.helpers", fromlist=["setup_logging"]).setup_logging()

_USER_CONFIG = app_identity.data_root() / "config" / "security.json"

# 安全默认值（配置文件缺失/字段缺失时的兜底）
_DEFAULTS: dict = {
    "enabled_modules": {
        "yara": True,
        "ransomware": True,
        "popup": True,
        "startup": True,
        "dns": True,
        "download": True,
        "llm": True,
    },
    "yara": {
        "rules_dir": "config/yara",
        "scan_on_start": False,
    },
    "quarantine": {
        "root": "%ProgramData%/zhuzhu_Copilot/Quarantine",
    },
    "ransomware": {
        "canary_dirs": [
            "%USERPROFILE%/Documents",
            "%USERPROFILE%/Desktop",
            "%USERPROFILE%/Pictures",
            "%USERPROFILE%/Downloads",
        ],
        "canary_files": [
            "_passwords.txt",
            "_bank_account.docx",
            "_important_notes.pdf",
            "_customer_data.xlsx",
        ],
        "canary_content": "zhuzhu_Copilot canary file. Do not edit.",
        "extensions": [
            ".locked", ".encrypted", ".crypt", ".crypted", ".crypto",
            ".locky", ".zepto", ".wncry", ".wcry", ".cerber",
            ".de-crypt", ".pay", ".vault", ".ryuk", ".phobos",
        ],
        "vss_enabled": True,
        "vss_interval_h": 6,
        "vss_drive": "C:",
    },
    "popup": {
        "action": "close",          # close | notify
        "title_keywords": [
            "广告", "推荐", "热点资讯", "今日头条", "弹窗", "抢红包",
            "领红包", "抽奖", "优惠", "秒杀", "中奖", "免费领取",
        ],
        "ad_processes": [
            "popup", "ad", "news", "push", "banner", "minibrowser",
        ],
    },
    "startup": {
        "watch_reg_keys": [
            r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run",
            r"HKCU\Software\Microsoft\Windows\CurrentVersion\RunOnce",
            r"HKLM\Software\Microsoft\Windows\CurrentVersion\Run",
            r"HKLM\Software\Microsoft\Windows\CurrentVersion\RunOnce",
        ],
        "watch_folders": [
            "%APPDATA%/Microsoft/Windows/Start Menu/Programs/Startup",
            "%ProgramData%/Microsoft/Windows/Start Menu/Programs/Startup",
        ],
        "suspicious_markers": [
            "\\temp\\", "powershell", "wscript", "cscript",
            "mshta", "regsvr32", "certutil",
        ],
    },
    "dns": {
        "hosts_path": "C:/Windows/System32/drivers/etc/hosts",
        "suspicious_domains": [
            "google.com", "google.com.hk", "youtube.com", "twitter.com",
            "facebook.com", "github.com", "microsoft.com", "windowsupdate.com",
        ],
    },
    "network": {
        "syn_flood_threshold": 100,
        "syn_block_rounds": 2,
        "seg_rate_threshold": 5000,   # TCP 整体洪泛：每秒入段数
        "udp_rate_threshold": 4000,   # UDP 整体洪泛：每秒入数据报数
        "port_scan_ports": 60,        # 端口扫描：单源访问的不同本地端口数
        "port_scan_rounds": 2,
        "max_block_rules": 50,
        "high_risk_ports": [445, 139, 135, 3389, 23, 21, 1433, 3306, 5900, 6379],
        "local_allowed_ports": [135, 137, 138, 139, 445, 5353, 1900, 5040],
        "port_names": {"445": "SMB", "139": "NetBIOS", "135": "RPC", "3389": "RDP",
                       "23": "Telnet", "21": "FTP", "1433": "MSSQL", "3306": "MySQL",
                       "5900": "VNC", "6379": "Redis"},
    },
    "download_guard": {
        "dirs": [],                # 待扫描目录（%VAR% 展开）；空 → 自动探测系统下载目录
        "walk_subdirs": False,
        "scan_interval_s": 30,     # 供调用方控制巡检节奏
        "action": "notify",        # notify 上报 / quarantine 自动隔离
        "extensions": [
            ".exe", ".dll", ".scr", ".bat", ".cmd", ".ps1", ".vbs", ".vbe",
            ".js", ".jse", ".jar", ".msi", ".hta", ".lnk",
            ".docm", ".xlsm", ".pptm", ".doc", ".xls", ".ppt",
        ],
        "max_file_mb": 200,
        "script_text_exts": [
            ".bat", ".cmd", ".ps1", ".psm1", ".vbs", ".vbe",
            ".js", ".jse", ".hta", ".wsf", ".sh",
        ],
    },
    "llm": {
        "enabled": True,           # 低置信样本二次判定的总开关
        "base_url": "",            # 显式 LLM 配置；留空时回退应用主 LLM 连接
        "api_key": "",
        "model": "",
        "timeout_s": 15.0,
        "max_calls_per_scan": 10,  # 单轮批量扫描的 LLM 调用上限（防 API 费用失控）
    },
}


def _bundled_config() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", ".")) / "config" / "security.json"
    return Path(__file__).resolve().parents[4] / "config" / "security.json"


def _deep_merge(base: dict, override: dict) -> dict:
    """递归合并：override 覆盖 base，返回新 dict（不修改入参）"""
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class SecurityConfig:
    """引擎配置单例：get("a.b.c", default) 点号路径取值"""

    def __init__(self):
        self.data: dict = {}
        self.reload()

    def reload(self) -> None:
        merged = json.loads(json.dumps(_DEFAULTS))
        for src in (_bundled_config(), _USER_CONFIG):
            if src.exists():
                try:
                    loaded = json.loads(src.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        merged = _deep_merge(merged, loaded)
                except (OSError, ValueError) as e:
                    logger.warning("安全配置加载失败 %s: %s", src, e)
        self.data = merged

    def get(self, path: str, default: Any = None) -> Any:
        node: Any = self.data
        for key in path.split("."):
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def enabled(self, module: str) -> bool:
        return bool(self.get(f"enabled_modules.{module}", True))

    def expand(self, raw: str) -> str:
        return os.path.expandvars(os.path.expanduser(raw or ""))

    def path(self, key: str, default: str) -> Path:
        return Path(self.expand(str(self.get(key, default))))

    def list_of(self, key: str, default: list) -> list:
        val = self.get(key, default)
        return list(val) if isinstance(val, list) else list(default)

    def set_module_enabled(self, module: str, enabled: bool) -> None:
        """运行时切换模块开关（check 型 guard 立即生效；popup 需重启防护）"""
        self.data.setdefault("enabled_modules", {})[module] = bool(enabled)

    def save(self) -> bool:
        """把当前配置持久化到用户级配置文件（重启后仍生效）"""
        try:
            _USER_CONFIG.parent.mkdir(parents=True, exist_ok=True)
            _USER_CONFIG.write_text(
                json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
            return True
        except OSError:
            return False


config = SecurityConfig()
