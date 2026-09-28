"""自启动实时监控：对比注册表 Run/RunOnce 与启动文件夹基线，发现新增启动项并分析可疑性。

- 快照全量启动项（注册表 4 个位置 + 启动文件夹），与基线对比得「新增项」
- 新增项按可疑特征（temp 路径 / 脚本解释器 / LOLBin）判定，可疑者告警（可撤销）
- 特征库来自配置 startup.suspicious_markers，零硬编码
- 基线每次 check 后更新：只报告「本次新增」，不重复告警
"""
import winreg
from pathlib import Path
from typing import List, Set, Tuple

from zhuzhu_Copilot.core.security_engine.config import config

_HIVE_MAP = {
    "HKLM": winreg.HKEY_LOCAL_MACHINE,
    "HKCU": winreg.HKEY_CURRENT_USER,
}


def _parse_reg_key(raw: str):
    """解析 'HKCU\\Software\\...\\Run' 形式的注册表路径，返回 (hive, 子键) 或 None"""
    hive_str, sep, path = str(raw).partition("\\")
    hive = _HIVE_MAP.get(hive_str.upper())
    if not hive or not sep:
        return None
    return hive, path


class StartupGuard:
    """自启动项实时监控器"""

    def __init__(self):
        self._baseline: Set[Tuple[str, str, str]] = self._snapshot()

    def _snapshot(self) -> Set[Tuple[str, str, str]]:
        """返回启动项集合，元素为 (来源, 名称, 命令)"""
        items: Set[Tuple[str, str, str]] = set()
        for raw in config.list_of("startup.watch_reg_keys", []):
            parsed = _parse_reg_key(raw)
            if not parsed:
                continue
            hive, key_path = parsed
            try:
                with winreg.OpenKey(hive, key_path) as k:
                    i = 0
                    while True:
                        try:
                            name, value, _ = winreg.EnumValue(k, i)
                        except OSError:
                            break
                        items.add(("reg", name, str(value)))
                        i += 1
            except OSError:
                continue
        for raw in config.list_of("startup.watch_folders", []):
            d = Path(config.expand(raw))
            if not d.is_dir():
                continue
            for f in d.glob("*.lnk"):
                items.add(("lnk", f.stem, str(f)))
        return items

    def check(self) -> dict:
        """对比基线，返回 {'added': 全部新增, 'suspicious': 可疑新增}"""
        if not config.enabled("startup"):
            return {"added": [], "suspicious": []}
        current = self._snapshot()
        added = sorted(current - self._baseline)
        self._baseline = current
        suspicious = [x for x in added if self._is_suspicious(x)]
        return {"added": added, "suspicious": suspicious}

    @staticmethod
    def _is_suspicious(item: Tuple[str, str, str]) -> bool:
        _, _, command = item
        low = (command or "").lower()
        markers = [str(m).lower() for m in config.list_of("startup.suspicious_markers", [])]
        return any(m in low for m in markers)


startup_guard = StartupGuard()
