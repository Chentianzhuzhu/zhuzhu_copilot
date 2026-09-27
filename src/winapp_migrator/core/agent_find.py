"""快速查找 App / 文件（优化：一次扫描建立索引缓存 + 并行遍历 + 提前终止）

- find_app：扫描开始菜单快捷方式 + 注册表 App Paths，名称模糊匹配，秒查（带缓存 TTL 1 小时）
- search_files：在用户目录并行遍历文件名模糊匹配，找到足够结果立即停止
"""

import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import winreg

_CACHE_TTL = 3600          # 应用索引缓存有效期（秒）
_CACHE_FILE = Path.home() / ".winapp_migrator" / "agent" / "apps_cache.json"
_cache = {"apps": [], "ts": 0.0}
_lock = threading.Lock()

# 未出现在快捷方式/注册表时的兜底常见应用（系统自带）
_COMMON_APPS = {
    "notepad": r"C:\Windows\System32\notepad.exe",
    "记事本": r"C:\Windows\System32\notepad.exe",
    "calc": r"C:\Windows\System32\calc.exe",
    "计算器": r"C:\Windows\System32\calc.exe",
    "cmd": r"C:\Windows\System32\cmd.exe",
    "命令提示符": r"C:\Windows\System32\cmd.exe",
    "powershell": r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
    "explorer": r"C:\Windows\explorer.exe",
    "资源管理器": r"C:\Windows\explorer.exe",
    "control": r"C:\Windows\System32\control.exe",
    "控制面板": r"C:\Windows\System32\control.exe",
}


def _norm(s: str) -> str:
    """归一化：小写 + 去掉空格/连字符/下划线/点"""
    return re.sub(r"[\s\-_\.]+", "", str(s or "").lower())


def _start_menu_dirs() -> list:
    dirs = []
    pd = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
    dirs.append(Path(pd) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    ad = os.environ.get("APPDATA", "")
    if ad:
        dirs.append(Path(ad) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    return [d for d in dirs if d.is_dir()]


def _desktop_dirs() -> list:
    dirs = []
    ud = os.environ.get("USERPROFILE", "")
    if ud:
        dirs += [Path(ud) / "Desktop", Path(ud) / "桌面"]
    pub = os.environ.get("PUBLIC", r"C:\Users\Public")
    dirs.append(Path(pub) / "Desktop")
    return [d for d in dirs if d.is_dir()]


def _lnk_entries() -> list:
    """扫描开始菜单与桌面快捷方式，返回 [{"name","path","kind":"lnk"}]"""
    out, seen = [], set()
    for base in _start_menu_dirs() + _desktop_dirs():
        try:
            for root, _dirs, files in os.walk(base):
                for f in files:
                    if not f.lower().endswith(".lnk"):
                        continue
                    p = os.path.join(root, f)
                    if p in seen:
                        continue
                    seen.add(p)
                    out.append({"name": Path(f).stem, "path": p, "kind": "lnk"})
        except OSError:
            continue
    return out


def _app_paths_entries() -> list:
    """注册表 App Paths：返回 [{"name","path","kind":"exe"}]"""
    out = []
    roots = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
    ]
    for hive, sub in roots:
        try:
            with winreg.OpenKey(hive, sub) as k:
                i = 0
                while True:
                    try:
                        key_name = winreg.EnumKey(k, i)
                        i += 1
                    except OSError:
                        break
                    try:
                        with winreg.OpenKey(k, key_name) as kk:
                            exe, _ = winreg.QueryValueEx(kk, None)
                    except OSError:
                        exe = None
                    if not exe:
                        continue
                    out.append({"name": Path(key_name).stem, "path": exe, "kind": "exe"})
        except OSError:
            continue
    return out


def _load_app_index() -> list:
    """应用索引：内存缓存 → 文件缓存 → 重建（TTL 1 小时，秒查）"""
    now = time.time()
    with _lock:
        if _cache["apps"] and now - _cache["ts"] < _CACHE_TTL:
            return _cache["apps"]
    if _CACHE_FILE.exists():
        try:
            data = json.loads(_CACHE_FILE.read_text("utf-8"))
            if data.get("apps") and data.get("ts", 0) + _CACHE_TTL > now:
                with _lock:
                    _cache["apps"], _cache["ts"] = data["apps"], data["ts"]
                return _cache["apps"]
        except Exception:
            pass
    apps = _lnk_entries() + _app_paths_entries()
    with _lock:
        _cache["apps"], _cache["ts"] = apps, now
    try:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(json.dumps({"apps": apps, "ts": now},
                                          ensure_ascii=False, indent=1), "utf-8")
    except OSError:
        pass
    return apps


def find_app(query: str, limit: int = 10) -> str:
    """模糊查找应用，返回可启动路径候选（名称 → 路径）"""
    q = _norm(query)
    if not q:
        return "（缺少应用名）"

    def score(a):
        n = _norm(a["name"])
        if n == q:
            return 0
        if q in n:
            return 1
        if n in q:
            return 2
        return 9

    hits = sorted((a for a in _load_app_index() if score(a) < 9),
                  key=score)[:max(1, int(limit))]
    if hits:
        lines = [f"{i + 1}. {a['name']} → {a['path']}（{a['kind']}）"
                 for i, a in enumerate(hits)]
        return "找到应用：\n" + "\n".join(lines)
    # 兜底：系统常见应用
    fallback = [{"name": k, "path": v} for k, v in _COMMON_APPS.items() if q in _norm(k)]
    if fallback:
        return "找到应用：\n" + "\n".join(f"{i + 1}. {a['name']} → {a['path']}"
                                           for i, a in enumerate(fallback))
    return f"未找到与「{query}」匹配的应用，可尝试用 start_menu 目录名称重试"


def search_files(query: str, folder: str = "", limit: int = 30,
                 ext: str = "", case_sensitive: bool = False,
                 exclude: str = "") -> str:
    """并行遍历用户目录模糊查找文件（找到足够结果立即停止）。

    ext：逗号分隔的扩展名过滤（如 "py,md" 或 "*.py"），留空不限。
    case_sensitive：文件名匹配是否区分大小写，默认不区分。
    exclude：逗号分隔的路径/名称排除关键字（子串命中即跳过），如 "node_modules,build"。
    """
    if not query or not str(query).strip():
        return "（缺少文件名关键字）"
    q = str(query).strip()
    qn = q if case_sensitive else _norm(q)
    ext_pats = [e.strip().lstrip(".") for e in (ext or "").split(",") if e.strip()]
    excl = [e.strip() for e in (exclude or "").split(",") if e.strip()]
    roots = []
    if folder:
        p = Path(os.path.expandvars(os.path.expanduser(folder)))
        if p.is_dir():
            roots = [p]
    if not roots:
        home = Path.home()
        roots = [home / "Desktop", home / "桌面", home / "Downloads", home / "下载",
                 home / "Documents", home / "文档"]
        roots = [d for d in roots if d.is_dir()]
    if not roots:
        roots = [Path.home()]

    results, done = [], threading.Event()
    n_limit = max(1, min(int(limit or 30), 500))   # 钳制结果条数上限，防超大 limit 拖垮
    _budget = [0]                                   # 全局遍历预算：防超大目录（无匹配）无限 walk
    _BUDGET_MAX = 200000

    def walk_one(root):
        try:
            for dirpath, dirnames, filenames in os.walk(root):
                if done.is_set():
                    return
                dirnames[:] = [d for d in dirnames
                               if not d.startswith(("$", "System Volume Information"))
                               and not _excluded(os.path.join(dirpath, d))]
                for fn in filenames:
                    _budget[0] += 1
                    if _budget[0] > _BUDGET_MAX or done.is_set():
                        done.set()
                        return
                    fp = os.path.join(dirpath, fn)
                    if _excluded(fp):
                        continue
                    if ext_pats and Path(fn).suffix.lstrip(".") not in ext_pats:
                        continue
                    name = fn if case_sensitive else _norm(fn)
                    if qn in name:
                        results.append(fp)
                        if len(results) >= n_limit:
                            done.set()
                            return
        except OSError:
            return

    def _excluded(fp: str) -> bool:
        low = fp.casefold()
        return any(x.casefold() in low for x in excl)

    with ThreadPoolExecutor(max_workers=min(len(roots), 8)) as ex:
        futs = [ex.submit(walk_one, r) for r in roots]
        for _ in as_completed(futs):
            if done.is_set():
                break
    lines = results[:n_limit]
    if not lines:
        return f"未找到包含「{query}」的文件"
    return f"找到 {len(lines)} 个文件：\n" + "\n".join(lines)


# ---- grep：按内容检索（正则/关键字） ----
_GREP_SKIP_DIRS = frozenset({".git", ".svn", "__pycache__", "node_modules",
                             "venv", ".venv", ".idea", "dist", "build",
                             ".cache", "$RECYCLE.BIN"})
_GREP_MAX_FILE = 2 * 1024 * 1024   # 跳过 >2MB 文件（多为二进制/构建产物）
_GREP_MAX_LINE = 240               # 命中行截断长度
_GREP_BUDGET = 300000              # 遍历文件数上限，防极大目录无匹配无限扫


def grep_contents(pattern: str, folder: str = "", glob: str = "",
                  max_results: int = 50, case_sensitive: bool = False,
                  context: int = 0, line_numbers: bool = True) -> str:
    """在目录下按内容检索文件，返回「路径:行号: 内容」命中清单。

    pattern 优先按正则匹配；表达式非法时自动退化为普通关键字子串匹配。
    glob 支持逗号分隔的文件过滤（如 '*.py,*.md'），缺省检索全部文本文件。
    context：命中行前后各附加 N 行上下文（默认 0）；line_numbers：是否输出行号（默认是）。
    同一文件可返回多个命中行（按 max_results 总量封顶），供逐行定位代码。
    """
    pat = (pattern or "").strip()
    if not pat:
        return "（缺少检索关键字/正则）"
    pats = [p.strip() for p in (glob or "").split(",") if p.strip()]
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        rx = re.compile(pat, flags)
        use_re = True
    except re.error:
        rx = None
        use_re = False
    needle = pat.casefold()
    root = Path(os.path.expandvars(os.path.expanduser(folder or "")))
    if not root.is_dir():
        return f"目录不存在: {root}"
    n_limit = max(1, min(int(max_results or 50), 500))
    ctx = max(0, min(int(context or 0), 50))
    results, budget = [], [0]

    def _match(text: str) -> bool:
        return rx.search(text) if use_re else (needle in text.casefold())

    def _filter(name: str) -> bool:
        low = name.casefold()
        return any(Path(name).match(p) or p.casefold() in low for p in pats)

    def walk(base):
        try:
            for dirpath, dirnames, filenames in os.walk(base):
                dirnames[:] = [d for d in dirnames
                               if d not in _GREP_SKIP_DIRS
                               and not d.startswith(("$", "System Volume Information"))]
                for fn in filenames:
                    budget[0] += 1
                    if budget[0] > _GREP_BUDGET or len(results) >= n_limit:
                        return
                    if pats and not _filter(fn):
                        continue
                    fp = os.path.join(dirpath, fn)
                    rel = os.path.relpath(fp, root)
                    try:
                        if os.path.getsize(fp) > _GREP_MAX_FILE:
                            continue
                        with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                            lines = f.readlines()
                        for ln, line in enumerate(lines, 1):
                            if len(results) >= n_limit:
                                return
                            if not _match(line):
                                continue
                            # 命中行 + 可选上下文（前后 N 行，跨行都带行号便于精确引用）
                            lo = max(0, ln - 1 - ctx)
                            hi = min(len(lines), ln + ctx)
                            for k in range(lo, hi):
                                snippet = lines[k].strip().replace("\t", " ")[:_GREP_MAX_LINE]
                                if line_numbers:
                                    results.append(f"{rel}:{k + 1}: {snippet}")
                                else:
                                    results.append(f"{rel}: {snippet}")
                            if ctx:
                                results.append(f"{rel} · 分节")
                    except (OSError, UnicodeError, MemoryError):
                        continue
        except OSError:
            return

    walk(root)
    if not results:
        return f"在 {root} 中未找到与「{pattern}」匹配的内容"
    head = f"找到 {len(results)} 处"
    if ctx:
        head += f"（含前后 {ctx} 行上下文）"
    return f"{head}。路径:行号: 内容\n" + "\n".join(results)


# 代码检索默认文件扩展名集合（search_code 未显式指定 glob 时按此过滤）
CODE_EXTS = frozenset({
    "py", "kt", "kts", "java", "js", "mjs", "cjs", "ts", "tsx", "jsx",
    "go", "rs", "c", "h", "cc", "cpp", "hpp", "cs", "swift", "php",
    "html", "htm", "css", "scss", "less", "vue", "svelte", "json",
    "md", "markdown", "yaml", "yml", "toml", "xml", "sql", "sh", "bat",
    "cmd", "ini", "cfg", "conf", "gradle", "env",
})


def search_code(pattern: str, folder: str = "", glob: str = "",
                max_results: int = 50, case_sensitive: bool = False,
                context: int = 2, name: str = "") -> str:
    """代码检索：按内容定位代码行（相对路径 + 行号 + 上下文），按文件分组输出。

    与 grep 的差异：缺省 glob 只检索代码/配置类文件（CODE_EXTS），且输出按文件
    分节（每文件一段命中区），便于一次看清一个文件的全部相关行。name 可选：额外
    要求**文件名**包含该关键字（可缩小到某个具体文件/模块）。
    """
    pat = (pattern or "").strip()
    if not pat:
        return "（缺少检索关键字/正则）"
    pats = [p.strip() for p in (glob or "").split(",") if p.strip()]
    if name:
        namec = str(name).casefold()
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        rx = re.compile(pat, flags)
        use_re = True
    except re.error:
        rx = None
        use_re = False
    needle = pat.casefold()
    root = Path(os.path.expandvars(os.path.expanduser(folder or "")))
    if not root.is_dir():
        return f"目录不存在: {root}"
    n_limit = max(1, min(int(max_results or 50), 500))
    ctx = max(0, min(int(context or 0), 50))
    grouped, results, budget = {}, [], [0]   # path -> [(行号, 文本), ...]（含上下文行）

    def _match(text: str) -> bool:
        return rx.search(text) if use_re else (needle in text.casefold())

    def _filter(name_: str) -> bool:
        low = name_.casefold()
        if name and namec not in Path(name_).stem.casefold():
            return False
        if not pats:
            return Path(name_).suffix.lstrip(".").casefold() in CODE_EXTS
        return any(Path(name_).match(p) or p.casefold() in low for p in pats)

    def walk(base):
        try:
            for dirpath, dirnames, filenames in os.walk(base):
                dirnames[:] = [d for d in dirnames
                               if d not in _GREP_SKIP_DIRS
                               and not d.startswith(("$", "System Volume Information"))]
                for fn in filenames:
                    budget[0] += 1
                    if budget[0] > _GREP_BUDGET or len(results) >= n_limit:
                        return
                    if not _filter(fn):
                        continue
                    fp = os.path.join(dirpath, fn)
                    try:
                        if os.path.getsize(fp) > _GREP_MAX_FILE:
                            continue
                        with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                            lines = f.readlines()
                        hits = [ln for ln, line in enumerate(lines, 1) if _match(line)]
                        if not hits:
                            continue
                        rel = os.path.relpath(fp, root)
                        seen = set()
                        for ln in hits:
                            if len(results) >= n_limit:
                                return
                            for k in range(max(0, ln - 1 - ctx), min(len(lines), ln + ctx)):
                                if k in seen:
                                    continue
                                seen.add(k)
                                grouped.setdefault(rel, []).append(
                                    (k + 1, lines[k].strip().replace("\t", " ")[:_GREP_MAX_LINE]))
                                results.append((rel, k + 1))
                    except (OSError, UnicodeError, MemoryError):
                        continue
        except OSError:
            return

    walk(root)
    if not results:
        return f"在 {root} 中未找到与「{pattern}」匹配的代码" + \
               (f"（文件含「{name}」）" if name else "")
    parts = [f"代码检索「{pattern}」命中 {len(grouped)} 个文件（共 {len(results)} 行）："]
    for rel in sorted(grouped):
        rows = grouped[rel]
        parts.append(f"==== {rel} ====")
        parts.extend(f"{n}: {t}" for n, t in rows)
    return "\n".join(parts)
