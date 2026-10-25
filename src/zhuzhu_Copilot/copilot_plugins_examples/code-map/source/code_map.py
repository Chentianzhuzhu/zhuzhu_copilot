"""工作区代码地图内核（Copilot 插件 code-map）。

一个只读的目录扫描器，回答「这个仓库有多大、代码主要在哪、哪些文件最重」：

  · `scan()`         遍历目录，统计文件数 / 代码行数 / 体积 / 扩展名分布 /
                     一级子目录分布 / 最大的文件；**全流程有硬上限**（文件数、单文件字节、
                     深度），遇到超大目录会自动截断并标注，绝不把应用拖住；
  · `format_report()` 把结果整理成可读文本（工具返回 / 弹窗 / 剪贴板共用）；
  · `tool_report()`   工具 `code_map` 的入口；
  · `load_root()/save_root()` 记住用户上次扫描的目录（存用户数据目录，不依赖插件配置）。

设计约束：不 import PyQt（界面侧在插件包内的页面模块）；不写任何业务文件；
扫描只读，跳过符号链接、跳过常见依赖/产物目录、跳过二进制后缀。
"""

import json
import os
import time
from pathlib import Path

#: 默认跳过的目录（依赖 / 构建产物 / 版本库内部）
DEFAULT_EXCLUDE = (
    ".git", ".svn", ".hg", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", ".idea", ".vscode", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "site-packages", ".next", ".nuxt", "target", "obj", "bin",
)

#: 计入「代码行数」的后缀（其余文件只统计个数与体积）
CODE_EXTS = {
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte",
    ".java", ".kt", ".kts", ".scala", ".go", ".rs", ".c", ".h", ".cc",
    ".cpp", ".hpp", ".cs", ".m", ".mm", ".swift", ".rb", ".php", ".pl",
    ".lua", ".sh", ".bash", ".zsh", ".ps1", ".bat", ".cmd", ".sql",
    ".html", ".htm", ".css", ".scss", ".sass", ".less", ".xml", ".json",
    ".yaml", ".yml", ".toml", ".ini", ".cfg", ".md", ".rst", ".tex", ".proto",
}

#: 明显是二进制/媒体的后缀：直接跳过（不读内容）
_SKIP_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".svg",
    ".mp3", ".wav", ".flac", ".mp4", ".avi", ".mkv", ".mov", ".webm",
    ".zip", ".rar", ".7z", ".gz", ".tar", ".bz2", ".xz", ".whl",
    ".exe", ".dll", ".pyd", ".so", ".dylib", ".bin", ".dat", ".db",
    ".pyc", ".pyo", ".class", ".jar", ".pt", ".onnx", ".npy", ".npz",
    ".ttf", ".otf", ".woff", ".woff2", ".eot", ".pdf", ".docx", ".xlsx",
    ".pptx", ".doc", ".xls", ".ppt",
}

MAX_FILES = 20000          # 单次扫描文件数上限（超出即截断并标注）
MAX_DEPTH = 6              # 目录深度上限
MAX_BYTES_PER_FILE = 2_000_000   # 单文件读取上限（超出只记体积，不数行）
MAX_SECONDS = 20.0         # 扫描时间上限（超时截断，保护交互）

_STATE_FILE = "code_map.json"


# ---------------------------------------------------------------------------
# 目录记忆
# ---------------------------------------------------------------------------
def _state_path():
    try:
        from zhuzhu_Copilot import app_identity
        return app_identity.data_root() / _STATE_FILE
    except Exception:
        return None


def load_root() -> str:
    """上次扫描的根目录（无记录返回空串）。"""
    p = _state_path()
    if p is None:
        return ""
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        root = str((data or {}).get("root") or "")
    except (OSError, ValueError):
        return ""
    return root if root and Path(root).is_dir() else ""


def save_root(root: str) -> None:
    """记住根目录（失败静默：记不住不影响扫描）。"""
    p = _state_path()
    if p is None:
        return
    try:
        p.write_text(json.dumps({"root": str(root or "")}, ensure_ascii=False),
                     encoding="utf-8")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# 扫描
# ---------------------------------------------------------------------------
def _count_lines(path: Path) -> int:
    """数行数（只读前 MAX_BYTES_PER_FILE 字节；解码失败按二进制处理）。"""
    try:
        with open(path, "rb") as f:
            blob = f.read(MAX_BYTES_PER_FILE)
    except OSError:
        return 0
    if not blob:
        return 0
    if b"\x00" in blob[:4096]:        # 含 NUL → 当作二进制，不数行
        return 0
    return blob.count(b"\n") + (0 if blob.endswith(b"\n") else 1)


def _fmt_bytes(n: float) -> str:
    v = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if v < 1024 or unit == "GB":
            return ("%.0f %s" % (v, unit)) if unit == "B" else ("%.1f %s" % (v, unit))
        v /= 1024.0
    return "%.1f GB" % v


def scan(root, max_depth: int = MAX_DEPTH, exclude=None,
         max_files: int = MAX_FILES, max_seconds: float = MAX_SECONDS) -> dict:
    """扫描目录并返回统计结果。

    返回 dict：
      root / files / dirs / code_files / code_lines / bytes / elapsed / truncated /
      errors / by_ext[{ext,files,lines}] / top_files[{path,lines,bytes}] /
      by_dir[{dir,files,lines,bytes}]
    """
    base = Path(str(root or "")).expanduser()
    if not base.is_dir():
        raise ValueError("目录不存在：%s" % root)
    base = base.resolve()
    ex = {str(x).lower() for x in (exclude if exclude is not None else DEFAULT_EXCLUDE)}
    t0 = time.perf_counter()
    files = dirs = code_files = code_lines = total_bytes = errors = 0
    truncated = False
    by_ext = {}
    by_dir = {}
    tops = []

    stack = [(base, 0)]
    while stack:
        cur, depth = stack.pop()
        if time.perf_counter() - t0 > max_seconds:
            truncated = True
            break
        try:
            with os.scandir(cur) as it:
                entries = list(it)
        except OSError:
            errors += 1
            continue
        for e in entries:
            try:
                if e.is_symlink():
                    continue          # 不跟随符号链接（防环、防越界）
                if e.is_dir(follow_symlinks=False):
                    dirs += 1
                    if e.name.lower() in ex or e.name.startswith("."):
                        continue
                    if depth + 1 <= max_depth:
                        stack.append((Path(e.path), depth + 1))
                    continue
                if not e.is_file(follow_symlinks=False):
                    continue
                files += 1
                if files > max_files:
                    truncated = True
                    break
                p = Path(e.path)
                ext = p.suffix.lower()
                try:
                    size = e.stat(follow_symlinks=False).st_size
                except OSError:
                    size = 0
                total_bytes += size
                # 一级子目录归属（相对根的第一段；根下的文件归到「.」）
                try:
                    rel_parts = p.relative_to(base).parts
                except ValueError:
                    rel_parts = (p.name,)
                key_dir = rel_parts[0] if len(rel_parts) > 1 else "."
                agg = by_dir.setdefault(key_dir, {"files": 0, "lines": 0, "bytes": 0})
                agg["files"] += 1
                agg["bytes"] += size
                if ext in _SKIP_EXTS:
                    continue
                lines = _count_lines(p) if ext in CODE_EXTS else 0
                if ext in CODE_EXTS:
                    code_files += 1
                    code_lines += lines
                    agg["lines"] += lines
                    slot = by_ext.setdefault(ext or "(无后缀)", {"files": 0, "lines": 0})
                    slot["files"] += 1
                    slot["lines"] += lines
                    tops.append({"path": str(p.relative_to(base)).replace("\\", "/"),
                                 "lines": lines, "bytes": size})
            except OSError:
                errors += 1
                continue
        if files > max_files:
            break

    tops.sort(key=lambda x: (-x["lines"], -x["bytes"]))
    ext_rows = sorted(({"ext": k, "files": v["files"], "lines": v["lines"]}
                       for k, v in by_ext.items()),
                      key=lambda r: (-r["lines"], -r["files"]))
    dir_rows = sorted(({"dir": k, "files": v["files"], "lines": v["lines"],
                        "bytes": v["bytes"]} for k, v in by_dir.items()),
                      key=lambda r: (-r["lines"], -r["files"]))
    return {
        "root": str(base),
        "files": files, "dirs": dirs,
        "code_files": code_files, "code_lines": code_lines,
        "bytes": total_bytes, "errors": errors,
        "elapsed": round(time.perf_counter() - t0, 2),
        "truncated": truncated,
        "by_ext": ext_rows, "by_dir": dir_rows, "top_files": tops[:50],
    }


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------
def format_report(data: dict, top: int = 8) -> str:
    """把 scan() 的结果整理成可读文本。"""
    d = data or {}
    head = "【工作区代码地图】%s" % (d.get("root") or "(未扫描)")
    lines = [head,
             "文件 %s 个 / 目录 %s 个 · 代码文件 %s 个 · 代码 %s 行 · 总体积 %s"
             % (format(d.get("files", 0), ","), format(d.get("dirs", 0), ","),
                format(d.get("code_files", 0), ","),
                format(d.get("code_lines", 0), ","),
                _fmt_bytes(d.get("bytes", 0))),
             "扫描耗时 %.2fs%s" % (float(d.get("elapsed") or 0),
                                 "（已达上限截断，统计为部分结果）"
                                 if d.get("truncated") else "")]
    ext_rows = (d.get("by_ext") or [])[:top]
    if ext_rows:
        lines.append("")
        lines.append("按类型（行数前 %d）：" % len(ext_rows))
        for r in ext_rows:
            lines.append("  · %-8s %5d 个文件  %9s 行"
                         % (r["ext"], r["files"], format(r["lines"], ",")))
    dir_rows = (d.get("by_dir") or [])[:top]
    if dir_rows:
        lines.append("")
        lines.append("按目录（行数前 %d）：" % len(dir_rows))
        for r in dir_rows:
            lines.append("  · %-24s %5d 个文件  %9s 行  %s"
                         % (r["dir"][:24], r["files"], format(r["lines"], ","),
                            _fmt_bytes(r["bytes"])))
    tops = (d.get("top_files") or [])[:top]
    if tops:
        lines.append("")
        lines.append("最大的文件（按行数前 %d）：" % len(tops))
        for r in tops:
            lines.append("  · %-52s %7s 行  %s"
                         % (r["path"][:52], format(r["lines"], ","),
                            _fmt_bytes(r["bytes"])))
    if not ext_rows and not dir_rows:
        lines.append("")
        lines.append("目录为空，或全是二进制/被排除的内容。")
    return "\n".join(lines)


def tool_report(args=None) -> dict:
    """工具 `code_map` 的实现：扫描并返回文本报告（异常转成可读提示）。"""
    a = args or {}
    root = str(a.get("path") or a.get("root") or "").strip()
    if not root:
        root = load_root() or str(Path.cwd())
    try:
        depth = int(a.get("depth") or MAX_DEPTH)
    except (TypeError, ValueError):
        depth = MAX_DEPTH
    depth = max(1, min(MAX_DEPTH, depth))
    try:
        top = int(a.get("top") or 8)
    except (TypeError, ValueError):
        top = 8
    top = max(3, min(30, top))
    try:
        data = scan(root, max_depth=depth)
    except (ValueError, OSError) as e:
        return {"text": "代码地图扫描失败：%s" % e, "images": []}
    save_root(data["root"])
    return {"text": format_report(data, top=top), "images": []}


# ---------------------------------------------------------------------------
# 结果缓存（服务启动时预热，页面打开即见；工具与页面共用同一份）
# ---------------------------------------------------------------------------
import threading as _threading

_CACHE = {"data": None, "at": 0.0, "lock": _threading.Lock()}


def cached(max_age: float = 0.0) -> dict:
    """取缓存结果；max_age > 0 时超过该秒数视为过期返回 {}。"""
    with _CACHE["lock"]:
        data = _CACHE["data"]
        at = float(_CACHE["at"] or 0.0)
    if not data:
        return {}
    if max_age and (time.time() - at) > max_age:
        return {}
    return data


def cache_scan(root=None, max_depth: int = MAX_DEPTH) -> dict:
    """扫描并写入缓存（供后台服务预热 / 页面刷新共用）。失败返回 {}。"""
    target = str(root or "").strip() or load_root()
    if not target:
        return {}
    try:
        data = scan(target, max_depth=max_depth)
    except (ValueError, OSError):
        return {}
    with _CACHE["lock"]:
        _CACHE["data"] = data
        _CACHE["at"] = time.time()
    return data


def clear_cache() -> None:
    with _CACHE["lock"]:
        _CACHE["data"] = None
        _CACHE["at"] = 0.0


def store_cache(data: dict) -> None:
    """把页面自己扫到的结果写回缓存（工具与页面共用同一份，避免重复扫描）。"""
    if not isinstance(data, dict) or not data.get("root"):
        return
    with _CACHE["lock"]:
        _CACHE["data"] = data
        _CACHE["at"] = time.time()
