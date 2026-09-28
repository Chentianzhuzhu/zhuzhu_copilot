import os
import re
import json
import subprocess
import winreg
import xml.etree.ElementTree as ET
from pathlib import Path
from dataclasses import dataclass
from typing import List

from zhuzhu_Copilot.utils.helpers import setup_logging

logger = setup_logging()

# UWP AppxManifest 命名空间
_APPX_NS = {"x": "http://schemas.microsoft.com/appx/manifest/foundation/windows10"}

# 无控制台程序运行子进程时不弹黑窗口
NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0

# 系统保留目录（含常见系统目录名），根目录扫描与递归扫描统一跳过，避免误识别为 app
_SKIP_DIR_NAMES = {
    "$recycle.bin", "system volume information", "windows", "windows.old",
    "system32", "syswow64", "users", "programdata", "perflogs", "recovery",
    "sources", "drivers", "intel", "amd", "nvidia", "dell",
    "common files", "common", "windows defender", "windows mail",
    "windows media player", "windows nt", "windows photo viewer",
    "windows portable devices", "windows security", "internet explorer",
    "reference assemblies", "microsoft", "microsoft analysis services",
    "microsoft sql server", "microsoft silverlight", "uninstall information",
    "microsoft.net", "microsoft shared", "microsoft visual studio",
    "microsoft visual studio installer", "microsoft visual studio shared",
    # 容器型目录名（非系统盘递归时其子目录才可能是 app）
    "program files", "program files (x86)", "programs", "software",
    "apps", "app", "application", "工具", "软件", "应用",
    # 知名软件厂商容器目录（多为多个应用的总目录，不应作为单个 app）
    "tencent", "alibaba", "baidu", "netease", "sogou", "bytedance",
    "kingsoft", "oracle", "huawei", "xiaomi", "meizu",
}

# UWP 系统运行库包名特征（Get-AppxPackage 列出但不可作为应用迁移）
_UWP_RUNTIME_HINTS = (
    ".net.native", "vclibs", "microsoft.ui.xaml", "microsoft.services.",
    "microsoft.windowsappruntime", "windowsappruntime", "microsoft.desktopappinstaller",
)

# 注册表显示名特征：系统组件/运行库/补丁，不应出现在迁移列表
_SYSTEM_COMPONENT_NAMES = (
    "microsoft visual c++", "microsoft .net", "update for", "security update",
    "service pack", "microsoft edge", "webview2", "windows sdk",
    "windows software development kit", "microsoft xna", "directx",
    "microsoft silverlight", "msxml", "microsoft office click-to-run",
)

@dataclass(frozen=True)
class ScanFilter:
    """目录启发式扫描的排除规则（仅作用于「按目录名猜测」的目录扫描）。

    注册表与 UWP 两路来自系统安装记录，天然可信，不做任何过滤；只有目录扫描需要
    这些启发式，用于排除两类误识别：
      1. 超大目录——直接文件总大小超过 max_dir_bytes，实为素材 / 数据 / 游戏资源目录；
      2. 孤立可执行文件——目录内几乎只有 1 个 exe，实为安装包或绿色单文件小工具。
    阈值集中在此处，便于后期在设置页暴露为开关与可调数值（避免散落硬编码）。
    """

    max_dir_bytes: int = 2 * 1024 ** 3   # 单目录直接文件总大小上限（默认 2GB）
    lone_exe_max_files: int = 2          # 直接文件数 <= 此值且仅 1 个 exe → 视为绿色单文件程序


DEFAULT_SCAN_FILTER = ScanFilter()


@dataclass
class AppInfo:
    name: str
    publisher: str
    install_location: Path
    version: str
    app_type: str
    size_bytes: int
    package_name: str = ""
    executable: str = ""

class AppScanner:
    def __init__(self, scan_filter: ScanFilter = None):
        self.apps: List[AppInfo] = []
        self.filter = scan_filter or DEFAULT_SCAN_FILTER

    def scan_all(self) -> List[AppInfo]:
        self.apps = []
        # 三路并行：UWP 枚举 / 注册表 / 目录扫描互不依赖
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [
                pool.submit(self._scan_uwp),
                pool.submit(self._scan_win32_registry),
                pool.submit(self._scan_common_folders),
            ]
            for f in futures:
                f.result()
        # 并行各环节的 seen 快照可能重叠，按安装目录去重后排序
        seen, uniq = set(), []
        for a in sorted(self.apps, key=lambda a: a.name.lower()):
            k = str(a.install_location).lower()
            if k not in seen:
                seen.add(k)
                uniq.append(a)
        return uniq

    def _scan_uwp(self):
        try:
            result = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "Get-AppxPackage | Select-Object Name, PackageFullName, Publisher, "
                    "InstallLocation, Version | ConvertTo-Json -Compress",
                ],
                capture_output=True,
                text=True,
                check=False,
                encoding="utf-8",
                errors="ignore",
                creationflags=NO_WINDOW,
            )
            if result.returncode != 0:
                logger.warning("扫描UWP失败: %s", result.stderr)
                return
            data = json.loads(result.stdout)
            if isinstance(data, dict):
                data = [data]
            for item in data:
                pkg_name = item.get("Name", "")
                if any(h in pkg_name.lower() for h in _UWP_RUNTIME_HINTS):
                    continue  # 系统运行库/框架，不可迁移
                loc = item.get("InstallLocation")
                if not loc or not Path(loc).exists():
                    continue
                loc_path = Path(loc)
                if self._is_system_path(loc_path):
                    continue
                self.apps.append(AppInfo(
                    name=self._uwp_display_name(loc_path, item.get("Name", "Unknown")),
                    publisher=item.get("Publisher", ""),
                    install_location=loc_path,
                    version=item.get("Version", ""),
                    app_type="UWP",
                    size_bytes=0,
                    package_name=item.get("PackageFullName", ""),
                ))
        except Exception as e:
            logger.exception("UWP扫描异常: %s", e)

    @staticmethod
    def _uwp_display_name(install_location: Path, fallback: str) -> str:
        """从 AppxManifest.xml 读取显示名（多为中文），ms-resource 引用时回退包名"""
        try:
            manifest = install_location / "AppxManifest.xml"
            if not manifest.is_file():
                return fallback
            root = ET.parse(str(manifest)).getroot()
            display = root.findtext("x:Properties/x:DisplayName", namespaces=_APPX_NS)
            if display and not display.lower().startswith("ms-resource"):
                return display.strip()
        except Exception:
            pass
        return fallback

    def _scan_win32_registry(self):
        keys = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        ]
        seen = {str(a.install_location).lower() for a in self.apps}
        for hkey, path in keys:
            try:
                with winreg.OpenKey(hkey, path) as key:
                    for i in range(winreg.QueryInfoKey(key)[0]):
                        sub_name = winreg.EnumKey(key, i)
                        try:
                            with winreg.OpenKey(key, sub_name) as sub:
                                name = self._reg_value(sub, "DisplayName")
                                if not name:
                                    continue
                                # 系统组件/运行库/补丁（VC++、.NET、Edge 等）不可迁移
                                low_name = name.lower()
                                if any(k in low_name for k in _SYSTEM_COMPONENT_NAMES):
                                    continue
                                loc = self._reg_value(sub, "InstallLocation")
                                icon = self._reg_value(sub, "DisplayIcon")
                                uninstall = self._reg_value(sub, "UninstallString")
                                loc_path = self._resolve_install_path(loc, icon, uninstall)
                                if not loc_path or not loc_path.exists() or not loc_path.is_dir():
                                    continue
                                # MSI 包缓存目录（Package Cache）不是真实安装目录
                                if "package cache" in str(loc_path).lower():
                                    continue
                                if self._is_system_path(loc_path):
                                    continue
                                key_path = str(loc_path).lower()
                                if key_path in seen:
                                    continue
                                seen.add(key_path)
                                self.apps.append(AppInfo(
                                    name=name,
                                    publisher=self._reg_value(sub, "Publisher") or "",
                                    install_location=loc_path,
                                    version=self._reg_value(sub, "DisplayVersion") or "",
                                    app_type="Win32",
                                    size_bytes=0,  # 大小由 UI 层 SizeWorker 异步统计
                                    executable=icon,
                                ))
                        except Exception:
                            continue
            except Exception as e:
                logger.warning("注册表扫描失败 %s: %s", path, e)

    def _resolve_install_path(self, loc: str, icon: str, uninstall: str) -> Path | None:
        """按优先级确定安装目录：InstallLocation > DisplayIcon 目录 > UninstallString 目录"""
        candidates = []
        if loc:
            candidates.append(loc)
        for source in (icon, uninstall):
            m = re.search(r'"([^"]+)"', source) or re.search(r"(\S+\.exe)", source, re.IGNORECASE)
            if m:
                exe_path = Path(m.group(1).strip())
                candidates.append(str(exe_path.parent))
        for c in candidates:
            try:
                # 去掉包裹路径的引号并展开环境变量，再解析为绝对路径
                p = Path(os.path.expandvars(str(c).strip('"'))).resolve()
                if p.is_dir():
                    return p
            except (OSError, ValueError):
                continue
        return None

    def _scan_common_folders(self):
        """扫描所有盘符上的常见安装目录与非系统盘根目录，作为 Win32 app 候选"""
        seen = {str(a.install_location).lower() for a in self.apps}
        system_root = str(Path(os.environ.get("SystemRoot", r"C:\Windows")).resolve()).lower()

        for drive in self._get_drives():
            root = Path(f"{drive}\\")
            # 1. 常见安装目录的一级子目录
            for sub in ("Program Files", "Program Files (x86)", "Programs", "Software",
                        "Apps", "App", "Application", "工具", "软件", "应用"):
                self._scan_folder(root / sub, seen)
            # 2. 非系统盘根目录递归扫描（C 盘根目录含大量系统目录，跳过）
            if str(root.resolve()).lower() == system_root:
                continue
            self._scan_folder(root, seen, max_depth=3)

    def _scan_folder(self, base: Path, seen: set, max_depth: int = 1):
        if not base.exists():
            return
        try:
            for entry in base.iterdir():
                if not entry.is_dir():
                    continue
                if entry.name.lower() in _SKIP_DIR_NAMES:
                    continue
                key = str(entry).lower()
                if key in seen:
                    continue
                # 一次遍历得到目录画像（文件数 / exe 数 / 总大小 / 子目录数），无递归开销
                files, exes, total, subdirs = self._dir_profile(entry)
                # 容器目录（无 exe 且未达深度上限）：继续向下寻找真实应用目录
                if max_depth > 1 and exes == 0:
                    self._scan_folder(entry, seen, max_depth=max_depth - 1)
                    continue
                # 排除超大目录与孤立 exe 目录（见 ScanFilter）
                if self._reject_candidate(entry, files, exes, total, subdirs):
                    continue
                seen.add(key)
                self.apps.append(AppInfo(
                    name=entry.name,
                    publisher="",
                    install_location=entry,
                    version="",
                    app_type="Win32",
                    size_bytes=0,
                ))
        except (PermissionError, OSError) as e:
            # 拒绝访问等容错：记录并跳过该目录，不影响其余扫描
            logger.warning("扫描目录失败 %s: %s", base, e)

    @staticmethod
    def _dir_profile(path: Path) -> tuple:
        """一次遍历得到目录画像：(直接文件数, exe 数, 文件总字节, 子目录数)。

        只扫一层、不递归——目录扫描的候选可能有上千个，递归统计体积会让扫描明显变慢；
        单层信息已足以判定「大文件目录」与「孤立 exe 目录」两类误识别。"""
        files = exes = total = subdirs = 0
        try:
            for entry in path.iterdir():
                try:
                    if entry.is_dir():
                        subdirs += 1
                        continue
                    files += 1
                    if entry.suffix.lower() == ".exe":
                        exes += 1
                    total += entry.stat().st_size
                except OSError:
                    continue
        except OSError:
            return 0, 0, 0, 0
        return files, exes, total, subdirs

    def _reject_candidate(self, path: Path, files: int, exes: int,
                          total: int, subdirs: int) -> bool:
        """目录扫描的排除判定（True = 不作为应用识别）。规则见 ScanFilter。"""
        f = self.filter
        if f.max_dir_bytes > 0 and total > f.max_dir_bytes:
            logger.debug("跳过超大目录（%.1fGB）: %s", total / 1024 ** 3, path)
            return True
        # 孤立可执行文件：只有 1 个 exe、几乎没有其他文件与子目录 → 安装包/绿色单文件工具
        if exes == 1 and subdirs == 0 and files <= f.lone_exe_max_files:
            logger.debug("跳过孤立 exe 目录: %s", path)
            return True
        return False

    @staticmethod
    def _get_drives():
        """枚举本机所有逻辑盘符（如 C: D:）"""
        import string
        from ctypes import windll
        bitmask = windll.kernel32.GetLogicalDrives()
        drives = []
        for letter in string.ascii_uppercase:
            if bitmask & 1:
                drives.append(f"{letter}:")
            bitmask >>= 1
        return drives

    @staticmethod
    def _is_system_path(path: Path) -> bool:
        """过滤不可迁移的系统目录"""
        text = str(path).lower()
        if "\\systemapps\\" in text or text.startswith(str(Path(os.environ.get("SystemRoot", r"C:\Windows"))).lower()):
            return True
        for name in ("common files",):
            if text.endswith(f"\\{name}"):
                return True
        return False

    def _reg_value(self, key, value_name: str) -> str:
        try:
            value, _ = winreg.QueryValueEx(key, value_name)
            return str(value) if value else ""
        except FileNotFoundError:
            return ""
