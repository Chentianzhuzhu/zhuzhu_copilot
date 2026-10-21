"""Windows 系统代理（WinINET）启用与还原。

开启全局代理时把系统代理指向本机转发器（SOCKS5 + HTTP 同端口），
退出/关闭代理时按快照还原用户原有设置，避免残留。

快照写入用户数据目录（崩溃后下次启动也能还原）：
  <data_root>/sysproxy_snapshot.json
"""

import json
import sys

_KEY = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
_SNAPSHOT_NAME = "sysproxy_snapshot.json"
_INTERNET_OPTION_REFRESH = 37
_INTERNET_OPTION_SETTINGS_CHANGED = 39


def _snapshot_path():
    from zhuzhu_Copilot import app_identity
    return app_identity.data_root() / _SNAPSHOT_NAME


def _open_key(write=False):
    import winreg
    access = winreg.KEY_READ | (winreg.KEY_WRITE if write else 0)
    return winreg.OpenKey(winreg.HKEY_CURRENT_USER, _KEY, 0, access)


def _read_values() -> dict:
    """读取三个代理值，返回 {name: [value, type]}；缺失则为 None。"""
    out = {"ProxyEnable": None, "ProxyServer": None, "ProxyOverride": None}
    if sys.platform != "win32":
        return out
    import winreg
    try:
        with _open_key() as k:
            for name in out:
                try:
                    val, typ = winreg.QueryValueEx(k, name)
                    out[name] = [val, typ]
                except FileNotFoundError:
                    pass
    except OSError:
        pass
    return out


def _write_values(values: dict) -> None:
    """按 [value, type] 写回；value 为 None 表示删除该值。"""
    if sys.platform != "win32":
        return
    import winreg
    with _open_key(write=True) as k:
        for name, item in values.items():
            if item is None:
                try:
                    winreg.DeleteValue(k, name)
                except FileNotFoundError:
                    pass
            else:
                winreg.SetValueEx(k, name, 0, int(item[1]), item[0])


def _notify() -> None:
    """通知系统代理设置已变更（浏览器等正在运行的程序立即生效）。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        for opt in (_INTERNET_OPTION_SETTINGS_CHANGED, _INTERNET_OPTION_REFRESH):
            ctypes.windll.wininet.InternetSetOptionW(None, opt, None, 0)
    except Exception:
        pass


def snapshot_exists() -> bool:
    return _snapshot_path().is_file()


def _load_snapshot() -> dict:
    try:
        return json.loads(_snapshot_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_snapshot_doc(doc: dict) -> None:
    try:
        path = _snapshot_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def save_snapshot() -> None:
    """首次启用时保存用户原始代理设置（已有快照不覆盖）。"""
    if _snapshot_path().is_file():
        return
    _save_snapshot_doc({"values": _read_values()})


def mark_applied(port: int) -> None:
    """记录本次写入的本地端口，供崩溃恢复时判断快照是否仍归本应用所有。"""
    doc = _load_snapshot()
    doc["port"] = int(port)
    _save_snapshot_doc(doc)


def enable(port: int, bypass=()) -> bool:
    """把系统代理指向 127.0.0.1:port（http/https/socks 三协议同端口）。

    bypass 中的域名会追加到 ProxyOverride，让 WinINET 应用直接绕过本机代理；
    本地转发器自身也会按同一列表重新判断（双保险）。
    """
    if sys.platform != "win32":
        return False
    save_snapshot()
    cur = _read_values()
    override = (cur.get("ProxyOverride") or [None, None])[0] or ""
    parts = [x for x in override.split(";") if x]
    for p in bypass or ():
        p = str(p).strip().lower()
        if not p or p[0].isdigit() or "*" in p or "/" in p:
            continue                     # IP 前缀/CIDR/通配由本机转发器判断
        for pat in (p, "*." + p):
            if pat not in parts:
                parts.append(pat)
    if "<local>" not in parts:
        parts.append("<local>")
    _write_values({
        "ProxyEnable": [1, 4],                       # REG_DWORD
        "ProxyServer": [
            f"http=127.0.0.1:{port};https=127.0.0.1:{port};socks=127.0.0.1:{port}",
            1,                                        # REG_SZ
        ],
        "ProxyOverride": [";".join(parts), 1],
    })
    mark_applied(port)
    _notify()
    return True


def restore() -> bool:
    """按快照还原系统代理。

    若记录过本地端口且当前系统代理已不指向它（用户手动改过），视为过期快照
    直接丢弃，不覆盖用户当前设置。无快照返回 False。
    """
    path = _snapshot_path()
    if not path.is_file():
        return False
    doc = _load_snapshot()
    port = doc.get("port")
    if port and not is_pointing_to(int(port)):
        path.unlink(missing_ok=True)
        return False
    _write_values(doc.get("values") or {})
    _notify()
    try:
        path.unlink()
    except OSError:
        pass
    return True


def current_server() -> str:
    item = _read_values().get("ProxyServer")
    return str(item[0]) if item else ""


def is_pointing_to(port: int) -> bool:
    return f"127.0.0.1:{port}" in current_server()
