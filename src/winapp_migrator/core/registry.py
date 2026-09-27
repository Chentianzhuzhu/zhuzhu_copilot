import winreg
from pathlib import Path
from typing import List, Optional, Tuple

from winapp_migrator.utils.helpers import setup_logging

logger = setup_logging()

# 注册表路径引用高价值位置（app 启动/卸载依赖的常用位置），扫描快且覆盖主要场景
_FIXED_ROOTS = [
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"),
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce"),
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths"),
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Run"),
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\RunOnce"),
    (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
    (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"),
]
# 系统级巨树，全量遍历极慢且 app 路径引用极少（其关键引用已由白名单覆盖），跳过
_SKIP_TOP = {"microsoft", "windows", "classes", "policies"}

class RegistryPathUpdater:
    def __init__(self):
        self.changed: List[Tuple[str, str, str, str]] = []
        self.scanned: List[Tuple[str, str, str]] = []

    def update_paths(self, old_path: Path, new_path: Path, hints: Optional[List[str]] = None) -> Tuple[int, int]:
        self.changed = []
        self.scanned = []
        changed_count = 0
        error_count = 0

        old_str = str(old_path)
        new_str = str(new_path)
        old_lower = old_str.lower()

        self._scan_targets(old_str, old_lower, hints or [])

        for hkey, subpath, value_name, value_data in self.scanned:
            try:
                self._write_value(hkey, subpath, value_name, value_data.replace(old_str, new_str))
                self.changed.append((self._root_name(hkey), subpath, value_name, value_data))
                changed_count += 1
            except Exception as e:
                logger.warning("写入注册表失败 %s\\%s: %s", subpath, value_name, e)
                error_count += 1

        return changed_count, error_count

    def _scan_targets(self, old_str: str, old_lower: str, hints: List[str]):
        """扫描白名单固定位置 + 与 app 相关的顶级键子树"""
        # 1. 固定高价值位置
        for root, subpath in _FIXED_ROOTS:
            try:
                with winreg.OpenKey(root, subpath, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
                    self._scan_key(key, subpath, old_str, old_lower, root)
            except FileNotFoundError:
                pass
            except Exception as e:
                logger.warning("无法打开注册表键 %s: %s", subpath, e)

        # 2. app 相关顶级键（publisher/可执行名匹配），跳过系统巨树
        for root, base in [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE"),
        ]:
            try:
                with winreg.OpenKey(root, base, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
                    for i in range(winreg.QueryInfoKey(key)[0]):
                        try:
                            sub_name = winreg.EnumKey(key, i)
                        except OSError:
                            break
                        if sub_name.lower() in _SKIP_TOP or not self._hint_match(sub_name, hints):
                            continue
                        sub_path = f"{base}\\{sub_name}"
                        try:
                            with winreg.OpenKey(key, sub_name, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as sub:
                                self._scan_key(sub, sub_path, old_str, old_lower, root)
                        except OSError:
                            continue
            except Exception as e:
                logger.warning("枚举顶级键失败 %s: %s", base, e)

    @staticmethod
    def _hint_match(name: str, hints: List[str]) -> bool:
        n = name.lower()
        for h in hints:
            h = (h or "").strip().lower()
            if len(h) >= 2 and (h in n or n in h):
                return True
        return False

    def _scan_key(self, key, path: str, old_str: str, old_lower: str, root):
        try:
            for i in range(winreg.QueryInfoKey(key)[1]):
                try:
                    name, value, _ = winreg.EnumValue(key, i)
                    text = self._value_to_string(value)
                    # 预过滤：路径引用必然含盘符冒号，无冒号的值直接跳过
                    if not text or ":" not in text:
                        continue
                    if old_lower in text.lower():
                        self.scanned.append((root, path, name, text))
                except Exception:
                    continue
        except Exception:
            pass

        try:
            for i in range(winreg.QueryInfoKey(key)[0]):
                try:
                    sub_name = winreg.EnumKey(key, i)
                    sub_path = f"{path}\\{sub_name}"
                    with winreg.OpenKey(key, sub_name, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as sub:
                        self._scan_key(sub, sub_path, old_str, old_lower, root)
                except Exception:
                    continue
        except Exception:
            pass

    def _value_to_string(self, value) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, bytes):
            try:
                return value.decode("utf-16-le").rstrip("\x00")
            except Exception:
                return ""
        if isinstance(value, list):
            return ";".join(str(x) for x in value)
        return str(value)

    def _write_value(self, hkey, subpath: str, value_name: str, new_data: str):
        # 需同时具备读写权限：先 QueryValueEx 查询类型（KEY_QUERY_VALUE），再 SetValueEx（KEY_SET_VALUE）
        access = winreg.KEY_READ | winreg.KEY_WRITE | winreg.KEY_WOW64_64KEY
        with winreg.OpenKey(hkey, subpath, 0, access) as key:
            try:
                _, typ = winreg.QueryValueEx(key, value_name)
            except FileNotFoundError:
                typ = winreg.REG_SZ
            if typ == winreg.REG_SZ or typ == winreg.REG_EXPAND_SZ:
                winreg.SetValueEx(key, value_name, 0, typ, new_data)
            elif typ == winreg.REG_MULTI_SZ:
                winreg.SetValueEx(key, value_name, 0, typ, new_data.split(";"))

    def _root_name(self, hkey) -> str:
        if hkey == winreg.HKEY_LOCAL_MACHINE:
            return "HKLM"
        if hkey == winreg.HKEY_CURRENT_USER:
            return "HKCU"
        return "HK?"


# ---- 强力卸载：扫描并删除 app 关联注册表项 ----
# 策略：Uninstall/App Paths 匹配子键整键删除；Run/RunOnce 匹配值删除；
#       publisher 顶级键只删其中引用该 app 路径的键/值，不删整个顶级键（避免误伤同发布商其他应用）

def scan_app_entries(install_location, display_name, hints) -> List:
    """扫描与 app 关联的注册表位置，返回 [(kind, hkey, path, name)]；kind 为 KEY/VALUE"""
    entries = []
    install_lower = str(install_location).lower().rstrip("\\")

    def add_uninstall_roots(roots, base):
        for root in roots:
            try:
                with winreg.OpenKey(root, base, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
                    for i in range(winreg.QueryInfoKey(key)[0]):
                        sub_name = winreg.EnumKey(key, i)
                        if _uninstall_key_matches(key, sub_name, install_lower, display_name):
                            entries.append(("KEY", root, f"{base}\\{sub_name}", ""))
            except FileNotFoundError:
                pass
            except OSError:
                pass

    # 1. Uninstall 子键：InstallLocation/UninstallString/DisplayName 匹配 → 整键删除
    add_uninstall_roots(
        [winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER],
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall")
    add_uninstall_roots(
        [winreg.HKEY_LOCAL_MACHINE],
        r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall")

    # 2. App Paths 子键：默认值指向安装目录下的 exe → 整键删除
    for root, base in [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
    ]:
        try:
            with winreg.OpenKey(root, base, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
                for i in range(winreg.QueryInfoKey(key)[0]):
                    sub_name = winreg.EnumKey(key, i)
                    try:
                        with winreg.OpenKey(key, sub_name, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as sub:
                            default, _ = winreg.QueryValueEx(sub, "")
                    except (FileNotFoundError, OSError):
                        continue
                    if str(default).lower().startswith(install_lower):
                        entries.append(("KEY", root, f"{base}\\{sub_name}", ""))
        except FileNotFoundError:
            pass
        except OSError:
            pass

    # 3. Run/RunOnce 值：数据引用安装目录 → 删值
    for root, base in [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Run"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\RunOnce"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"),
    ]:
        try:
            with winreg.OpenKey(root, base, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
                for i in range(winreg.QueryInfoKey(key)[1]):
                    name, value, _ = winreg.EnumValue(key, i)
                    if install_lower in str(value).lower():
                        entries.append(("VALUE", root, base, name))
        except FileNotFoundError:
            pass
        except OSError:
            pass

    # 4. app 相关顶级键（publisher/可执行名匹配）：仅收集引用安装目录的值
    for root, base in [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE"),
    ]:
        try:
            with winreg.OpenKey(root, base, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
                for i in range(winreg.QueryInfoKey(key)[0]):
                    try:
                        sub_name = winreg.EnumKey(key, i)
                    except OSError:
                        break
                    if sub_name.lower() in _SKIP_TOP or not RegistryPathUpdater._hint_match(sub_name, hints):
                        continue
                    _collect_path_values(root, f"{base}\\{sub_name}", install_lower, entries)
        except OSError:
            pass

    return entries


def remove_app_entries(entries) -> int:
    """删除 scan_app_entries 收集的注册表位置，返回成功删除数量"""
    removed = 0
    for kind, hkey, path, name in entries:
        try:
            if kind == "KEY":
                if _delete_key_tree(hkey, path):
                    removed += 1
            else:
                access = winreg.KEY_READ | winreg.KEY_WRITE | winreg.KEY_WOW64_64KEY
                with winreg.OpenKey(hkey, path, 0, access) as key:
                    winreg.DeleteValue(key, name)
                removed += 1
        except Exception as e:
            logger.warning("删除注册表项失败 %s\\%s: %s", path, name, e)
    return removed


def _uninstall_key_matches(parent_key, sub_name: str, install_lower: str, display_name: str) -> bool:
    try:
        with winreg.OpenKey(parent_key, sub_name, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as sub:
            loc = _str_value(sub, "InstallLocation")
            uninst = _str_value(sub, "UninstallString")
            disp = _str_value(sub, "DisplayName")
    except OSError:
        return False
    if loc and loc.lower().rstrip("\\") == install_lower:
        return True
    if uninst and install_lower in uninst.lower():
        return True
    return bool(disp) and disp.lower() == (display_name or "").lower()


def _str_value(key, value_name: str) -> str:
    try:
        value, _ = winreg.QueryValueEx(key, value_name)
        return str(value) if value else ""
    except (FileNotFoundError, OSError):
        return ""


def _collect_path_values(root, path: str, install_lower: str, entries):
    """递归收集键内引用安装目录的值；只删值不删键，避免误伤同发布商其他应用"""
    subs = []
    try:
        with winreg.OpenKey(root, path, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            for i in range(winreg.QueryInfoKey(key)[1]):
                try:
                    name, value, _ = winreg.EnumValue(key, i)
                except OSError:
                    break
                if install_lower in str(value).lower():
                    entries.append(("VALUE", root, path, name))
            i = 0
            while True:
                try:
                    subs.append(winreg.EnumKey(key, i))
                    i += 1
                except OSError:
                    break
    except OSError:
        return
    for s in subs:
        _collect_path_values(root, f"{path}\\{s}", install_lower, entries)


def _delete_key_tree(hkey, path: str) -> bool:
    """递归删除注册表键树（含全部子键），返回是否成功"""
    try:
        with winreg.OpenKey(hkey, path, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            subs = []
            i = 0
            while True:
                try:
                    subs.append(winreg.EnumKey(key, i))
                    i += 1
                except OSError:
                    break
        for s in subs:
            _delete_key_tree(hkey, f"{path}\\{s}")
        parent_path, _, key_name = path.rpartition("\\")
        with winreg.OpenKey(hkey, parent_path, 0, winreg.KEY_READ | winreg.KEY_WRITE | winreg.KEY_WOW64_64KEY) as parent:
            winreg.DeleteKey(parent, key_name)
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False
