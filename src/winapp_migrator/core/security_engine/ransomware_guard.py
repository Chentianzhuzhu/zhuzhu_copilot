"""蜜罐勒索防护：诱饵文件(canary) + 勒索加密后缀爆发检测 + VSS 卷影副本

机制：
- 在受保护目录（Documents/Desktop/Pictures/Downloads）投放哨兵诱饵文件，记录内容哈希
- 周期巡检：诱饵被篡改/删除/重命名，或目录内出现大量勒索加密后缀 → 判定勒索活动
- VSS：按配置周期创建卷影副本（后台线程，尽力而为），触发后可据此回滚
- 结果仅「检测 + 报告」，进程定位/终止由编排器复用 SecurityScanner 完成

用户态轮询方案，检测延迟约为巡检周期（秒级），足以在勒索加密扩散早期拦截。
"""
import hashlib
import subprocess
import threading
import time
from pathlib import Path
from typing import Dict, List

logger = __import__("winapp_migrator.utils.helpers", fromlist=["setup_logging"]).setup_logging()

from winapp_migrator.core.security_engine.config import config


class RansomwareGuard:
    """蜜罐勒索防护器"""

    def __init__(self):
        self._canary: Dict[str, str] = {}   # {绝对路径: 内容sha256}
        self._deployed = False
        self._last_shadow: float = 0.0
        self._shadow_lock = threading.Lock()

    def _ensure_deployed(self) -> None:
        """延迟部署：仅在防护首次巡检时投放诱饵，避免未开启防护也污染用户目录"""
        if self._deployed:
            return
        self._deployed = True
        self._deploy()

    # ---------- 诱饵投放 ----------
    def _deploy(self) -> None:
        if not config.enabled("ransomware"):
            return
        content = str(config.get("ransomware.canary_content", "canary"))
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        names = config.list_of("ransomware.canary_files", ["_passwords.txt"])
        for raw_dir in config.list_of("ransomware.canary_dirs", []):
            d = Path(config.expand(raw_dir))
            if not d.is_dir():
                continue
            for name in names:
                p = d / name
                if not p.exists():
                    try:
                        p.write_text(content, encoding="utf-8")
                    except OSError:
                        continue
                self._canary[str(p)] = digest

    # ---------- 巡检 ----------
    def check(self) -> dict:
        """检查诱饵完整性 + 加密后缀爆发，返回 {'detected','tampered','encrypted','shadow'}"""
        if not config.enabled("ransomware"):
            return {"detected": False}
        self._ensure_deployed()
        tampered = self._check_canary()
        encrypted = self._check_encryption_burst()
        self._maybe_create_shadow()
        return {
            "detected": bool(tampered or encrypted),
            "tampered": tampered,
            "encrypted": encrypted,
        }

    def _check_canary(self) -> List[str]:
        """返回被篡改/删除/改名的诱饵文件路径"""
        tampered = []
        for path, digest in self._canary.items():
            p = Path(path)
            if not p.exists():
                tampered.append(path)
                continue
            try:
                if hashlib.sha256(p.read_bytes()).hexdigest() != digest:
                    tampered.append(path)
            except OSError:
                tampered.append(path)
        return tampered

    def _check_encryption_burst(self) -> List[str]:
        """扫描受保护目录，返回命中的勒索加密后缀（计数≥阈值才报告，避免偶发误报）"""
        exts = {e.lower() for e in config.list_of("ransomware.extensions", [])}
        if not exts:
            return []
        hits: Dict[str, int] = {}
        for raw_dir in config.list_of("ransomware.canary_dirs", []):
            d = Path(config.expand(raw_dir))
            if not d.is_dir():
                continue
            try:
                for f in d.rglob("*"):
                    if f.is_file() and f.suffix.lower() in exts:
                        hits[f.suffix.lower()] = hits.get(f.suffix.lower(), 0) + 1
            except OSError:
                continue
        # 单目录内超过 3 个加密后缀文件才判定（避免用户正常保存 .crypt 类文件误报）
        return sorted(f"{ext}({n})" for ext, n in hits.items() if n >= 3)

    # ---------- VSS 卷影副本（尽力而为） ----------
    def _maybe_create_shadow(self) -> None:
        if not config.get("ransomware.vss_enabled", True):
            return
        interval = float(config.get("ransomware.vss_interval_h", 6)) * 3600
        if interval <= 0:
            return
        now = time.time()
        with self._shadow_lock:
            if now - self._last_shadow < interval:
                return
            self._last_shadow = now
        threading.Thread(target=self._create_shadow, daemon=True).start()

    def _create_shadow(self) -> None:
        drive = str(config.get("ransomware.vss_drive", "C:"))
        try:
            subprocess.run(
                ["vssadmin", "create", "shadow", f"/for={drive}", "/quiet"],
                capture_output=True, timeout=60, check=False)
        except (OSError, subprocess.TimeoutExpired):
            logger.warning("VSS 卷影副本创建失败（可能非管理员或服务未启用）")

    def list_shadows(self) -> List[str]:
        """列出可用卷影副本（供恢复用）；失败返回空列表"""
        try:
            r = subprocess.run(["vssadmin", "list", "shadows"], capture_output=True,
                               timeout=30, check=False, text=True)
            return [ln.strip() for ln in r.stdout.splitlines()
                    if "Shadow Copy ID" in ln or "卷影副本 ID" in ln]
        except (OSError, subprocess.TimeoutExpired):
            return []


ransomware_guard = RansomwareGuard()
