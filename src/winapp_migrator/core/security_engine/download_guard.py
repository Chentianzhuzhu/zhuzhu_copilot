"""下载目录文件安全分析：监控下载目录新增文件，规则+YARA+LLM 流水线判定。

- 扫描目录来自配置 download_guard.dirs（%VAR% 展开）；未配置时探测系统「下载」目录
  （SHGetKnownFolderPath FOLDERID_Downloads，真实 Win32 API），兜底 %USERPROFILE%/Downloads
- 增量扫描：内存快照记录 (路径, 大小, mtime)，仅分析新增/变更文件，避免每轮重复分析同一文件
- 分析链路复用 deep_analyze：静态特征 → 规则/YARA → 低置信样本 LLM 二次判定
- 处置动作由配置 download_guard.action 控制（notify 上报 / quarantine 自动隔离）
- 结果记录含 verdict/confidence/信号列表，供编排器聚合上报
"""
import ctypes
import os
import time
from ctypes import wintypes
from pathlib import Path

from winapp_migrator.core.security_engine.config import config
from winapp_migrator.core.security_engine.deep_analyze import deep_analyzer
from winapp_migrator.core.security_engine.quarantine import quarantine

_FOLDERID_Downloads = "{374DE290-123F-4565-9164-39C4925E467B}"


def _known_folder(folder_id: str) -> str:
    """SHGetKnownFolderPath 查询系统已知目录（Downloads 等），失败返回空"""
    try:
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        shell32.SHGetKnownFolderPath.argtypes = [
            ctypes.c_void_p, wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(ctypes.c_wchar_p)]
        shell32.SHGetKnownFolderPath.restype = ctypes.c_long
        buf = ctypes.c_wchar_p()
        guid = ctypes.create_unicode_buffer(folder_id)
        if shell32.SHGetKnownFolderPath(ctypes.cast(guid, ctypes.c_void_p), 0, None, ctypes.byref(buf)) == 0:
            return buf.value or ""
    except Exception:
        pass
    return ""


def scan_dirs() -> list[str]:
    """待扫描目录：配置 download_guard.dirs（展开中）→ 系统下载目录 → 用户下载目录兜底"""
    dirs = [str(config.expand(x)) for x in config.list_of("download_guard.dirs", [])]
    dirs = [d for d in dirs if d]
    if not dirs:
        sys_dl = _known_folder(_FOLDERID_Downloads)
        if sys_dl:
            dirs.append(sys_dl)
        fallback = str(config.expand("%USERPROFILE%/Downloads"))
        if fallback and fallback not in dirs:
            dirs.append(fallback)
    return [d for d in dict.fromkeys(dirs) if os.path.isdir(d)]


class DownloadGuard:
    """下载目录增量安全扫描器"""

    def __init__(self):
        self._seen: dict[str, tuple] = {}   # 路径 -> (size, mtime)，已分析文件快照
        self._last_check = 0.0              # 上次扫描时间（按 scan_interval_s 节流）

    # ---------- 外部接口 ----------
    def check(self) -> list[dict]:
        """扫描一遍下载目录：返回本轮新增文件的判定结果（空列表表示无新增或未命中）。
        按配置 download_guard.scan_interval_s 节流，避免每轮巡检都全量扫一轮。"""
        if not config.enabled("download_guard"):
            return []
        interval = float(config.get("download_guard.scan_interval_s", 30))
        now = time.time()
        # interval<=0 表示不节流（测试/手动触发）；默认按配置节流避免每轮全量扫
        if interval > 0 and now - self._last_check < interval:
            return []
        self._last_check = now
        records = []
        exts = [str(x).lower() for x in config.list_of("download_guard.extensions", [])]
        walk = bool(config.get("download_guard.walk_subdirs", False))
        max_mb = int(config.get("download_guard.max_file_mb") or 200)
        for d in scan_dirs():
            for p in self._iter_files(Path(d), walk, exts or None):
                records.append(self._check_file(str(p), max_mb))
        # 本轮结束，为下轮重置 LLM 调用预算
        deep_analyzer.end_scan()
        return [r for r in records if r]

    def analyze_file(self, path: str) -> dict | None:
        """单文件即时分析（供新增文件监听 / 手动扫描调用），按文件快照去重"""
        if not path or not os.path.isfile(path):
            return None
        max_mb = int(config.get("download_guard.max_file_mb") or 200)
        return self._check_file(path, max_mb)

    # ---------- 内部 ----------
    def _iter_files(self, d: Path, walk: bool, exts: list[str] | None):
        names = d.glob("*") if not walk else d.rglob("*")
        for f in names:
            try:
                if f.is_file() and f.stat().st_size > 0:
                    if exts is None or f.suffix.lower() in exts:
                        yield f
            except OSError:
                continue

    def _check_file(self, path: str, max_mb: int) -> dict | None:
        try:
            st = os.stat(path)
        except OSError:
            return None
        if st.st_size <= 0 or (max_mb > 0 and st.st_size > max_mb * 1024 * 1024):
            return None
        sig = (st.st_size, int(st.st_mtime))
        if self._seen.get(path) == sig:
            return None  # 已分析且未变更
        self._seen[path] = sig
        if len(self._seen) > 4096:   # 防快照无限膨胀：清空重建（下轮重新分析全部，保守方向）
            self._seen = {path: sig}
        return self._classify(path)

    def _classify(self, path: str) -> dict | None:
        res = deep_analyzer.analyze_file(path)
        # 文本型脚本（.bat/.ps1/.vbs 等）额外按脚本类别分析：脚本规则（编码命令/存活加载等）
        # 对文本内容比文件级静态特征更敏感，两者取更严重判定
        script_exts = {str(x).lower() for x in config.list_of("download_guard.script_text_exts", [])}
        if Path(path).suffix.lower() in script_exts:
            try:
                text = Path(path).read_text(encoding="utf-8", errors="replace")[:65536]
            except OSError:
                text = ""
            sres = deep_analyzer.analyze_script(text, path, os.path.basename(path))
            if _severity(sres.get("verdict")) > _severity(res.get("verdict")):
                res = sres
        if res["verdict"] == "clean":
            return None
        action = str(config.get("download_guard.action", "notify"))
        isolated = False
        if res["verdict"] == "malicious" and action == "quarantine":
            isolated = quarantine.isolate(path, res.get("reason") or "下载目录恶意文件")
        return {
            "kind": "download",
            "path": path,
            "verdict": res["verdict"],
            "confidence": res.get("confidence", 0),
            "signals": res.get("signals") or [],
            "reason": res.get("reason") or "",
            "isolated": isolated,
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        }


def _severity(verdict: str) -> int:
    return {"malicious": 2, "suspicious": 1, "clean": 0}.get(str(verdict).lower(), 0)


download_guard = DownloadGuard()