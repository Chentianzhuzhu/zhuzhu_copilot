"""沙盒专属内置运行时（Node.js / Python）

- 存放于 ~/.winapp_migrator/runtime/，与用户层环境完全隔离
- 首次使用自动下载官方便携版并解压/引导，绝不写入系统 PATH（仅子进程级 PATH 前置）
- npm/pip 缓存与依赖均落在沙盒目录内，不污染用户环境
- run_command 透明拦截：把沙盒 bin 前置到子进程 PATH，AI 直接输入
  node/npm/npx、python/py/pip 即命中沙盒运行时
"""

import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import urllib.request
import zipfile
from pathlib import Path

RUNTIME_ROOT = Path.home() / ".winapp_migrator" / "runtime"

# 官方 LTS / 稳定版（可随版本升级调整）
NODE_VERSION = "20.18.0"
PYTHON_VERSION = "3.12.8"

_LOCK = threading.Lock()   # 多命令并发时避免重复下载
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


# ------------------------------------------------------------
# 路径与探测
# ------------------------------------------------------------
def _arch() -> str:
    m = platform.machine().lower()
    return "arm64" if m in ("arm64", "aarch64") else "x64"


def _py_arch() -> str:
    """Python 官方 embeddable 包命名：x64 → amd64（Node 用 x64，Python 用 amd64）。"""
    return "arm64" if _arch() == "arm64" else "amd64"


def runtime_dir(name: str) -> Path:
    return RUNTIME_ROOT / name


def node_bin() -> Path:
    return runtime_dir("node")


def python_bin() -> Path:
    return runtime_dir("python")


def node_exe() -> Path:
    return node_bin() / "node.exe"


def python_exe() -> Path:
    return python_bin() / "python.exe"


def is_installed(name: str) -> bool:
    if name == "node":
        return node_exe().is_file()
    if name == "python":
        return python_exe().is_file() and (python_bin() / "Scripts").is_dir()
    return False


def bundled_runtime_root() -> Path:
    """返回打包进安装目录的运行时根目录（exe 同级或 _MEIPASS 下的 runtime/）。
    开发/源码模式下回退到本仓库 build/runtime_bundle/runtime（由 prepare_runtime_bundle.py 生成）。
    未找到返回空 Path。"""
    candidates = []
    mp = getattr(sys, "_MEIPASS", None)
    if mp:
        candidates.append(Path(mp) / "runtime")
    try:
        candidates.append(Path(sys.executable).resolve().parent / "runtime")
    except Exception:
        pass
    # 开发模式：仓库构建期预下载的运行时（仅便于源码直跑调试）
    try:
        candidates.append(Path(__file__).resolve().parents[3]
                          / "build" / "runtime_bundle" / "runtime")
    except Exception:
        pass
    for c in candidates:
        if (c / "node" / "node.exe").is_file() and (c / "python" / "python.exe").is_file():
            return c
    return Path()


def python_interpreter() -> str:
    """返回适合运行本地脚本（如 MCP stdio server.py）的真实 Python 解释器路径。

    开发/源码模式返回 sys.executable（此时是真实 Python）；
    打包（frozen）模式下 sys.executable 是应用自身 exe，用它启动子进程脚本会
    递归拉起应用导致「无限打开程序自己」，必须替换为：
    1) 沙盒 Python（~/.winapp_migrator/runtime/python/python.exe，已部署优先）
    2) 安装目录内置运行时（_MEIPASS/runtime 或 exe 同级 runtime）
    3) PATH 中的 python（兜底，绝不回退到应用自身 exe）
    """
    if not getattr(sys, "frozen", False):
        return sys.executable
    exe = python_exe()
    if exe.is_file():
        return str(exe)
    root = bundled_runtime_root()
    if root:
        cand = root / "python" / "python.exe"
        if cand.is_file():
            return str(cand)
    return "python"


def bin_dirs(name: str) -> list:
    """沙盒可执行目录（用于前置到子进程 PATH）。未安装返回空。"""
    if not is_installed(name):
        return []
    if name == "node":
        return [str(node_bin())]
    d = python_bin()
    return [str(d), str(d / "Scripts")]


_INSTALL_HINT = (
    "pip install", "pip3 install",
    " -m pip install",
    "npm install", "npm i ", "npm add", "npm ci",
    "yarn add", "yarn install", "pnpm add", "pnpm install", "bun add", "bun install",
)


def is_dep_install(command: str) -> bool:
    """判断是否为依赖安装类命令（pip/npm/yarn/pnpm 等 install/add）。

    命中时禁止依赖落入内置沙箱虚拟环境，改走用户本机全局环境：
    不命中沙盒运行时、不注入沙盒 npm 前缀/pip 缓存。"""
    low = (command or "").strip().lower().replace("\\", "/")
    if not low:
        return False
    return any(kw in low for kw in _INSTALL_HINT)


def needed_runtimes(command: str) -> list:
    """按命令首个 token 判断命中的沙盒运行时（node/npm/npx → node；python/py/pip → python）。
    依赖安装类命令（pip/npm install、add 等）不落沙箱虚拟环境，一律走用户本机全局环境。"""
    if is_dep_install(command):
        return []
    low = (command or "").strip().lower().replace("\\", "/")
    if not low:
        return []
    tok = os.path.basename(low.split()[0].strip("\""))
    tok = tok[:-4] if tok.lower().endswith(".exe") else tok
    if tok in ("node", "npm", "npx"):
        return ["node"]
    if tok in ("python", "py", "python3", "pip", "pip3"):
        return ["python"]
    return []


# ------------------------------------------------------------
# 进程级环境注入（绝不写系统 PATH）
# ------------------------------------------------------------
def sandbox_env(command: str = "") -> dict:
    """返回进程级 env：把已安装沙盒 bin 前置到 PATH + 隔离 npm/pip 缓存。
    仅作用于本次子进程，不修改 os.environ / 系统 PATH。
    依赖安装命令（pip/npm install 等）不注入沙盒 npm 前缀/pip 缓存，改走用户本机全局环境。"""
    env = dict(os.environ)
    if is_dep_install(command):
        return env
    extra = []
    for name in needed_runtimes(command):
        extra += bin_dirs(name)
    if extra:
        env["PATH"] = os.pathsep.join(extra + [env.get("PATH", "")])
    if is_installed("node"):
        env["npm_config_cache"] = str(node_bin() / ".npm-cache")   # npm 缓存进沙盒
        env["npm_config_prefix"] = str(node_bin())                 # npm 全局装进沙盒
    if is_installed("python"):
        env["PIP_CACHE_DIR"] = str(python_bin() / ".pip-cache")    # pip 缓存进沙盒
    return env


# ------------------------------------------------------------
# 下载与安装
# ------------------------------------------------------------
def _report(cb, msg: str):
    if callable(cb):
        try:
            cb(msg)
        except Exception:
            pass


def _download(url: str, dest: Path, cb=None):
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        with open(dest, "wb") as f:
            while True:
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if cb and total:
                    _report(cb, f"沙盒运行时下载中 {max(1, int(done * 100 / total))}%")


def _extract_zip(zip_path: Path, dest: Path, strip_top: bool):
    """安全解压 zip 到 dest；strip_top=True 时去掉顶层目录（如 node-v20-x64/）。"""
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            parts = name.split("/")
            if strip_top:
                parts = parts[1:]
            rel = parts[-1] if not parts[:-1] else "/".join(parts)
            if not rel:
                continue
            target = dest / rel
            # 防路径穿越
            if not target.resolve().is_relative_to(dest.resolve()):
                continue
            if name.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(name) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)


def _ensure_node(cb) -> tuple:
    url = (f"https://nodejs.org/dist/v{NODE_VERSION}/"
           f"node-v{NODE_VERSION}-win-{_arch()}.zip")
    zip_p = RUNTIME_ROOT / f"node-v{NODE_VERSION}-win-{_arch()}.zip"
    _report(cb, f"正在下载沙盒 Node.js v{NODE_VERSION}…")
    _download(url, zip_p, cb)
    _report(cb, "正在解压 Node.js…")
    _extract_zip(zip_p, node_bin(), strip_top=True)
    zip_p.unlink(missing_ok=True)
    if not node_exe().is_file():
        raise RuntimeError("Node.js 解压后未找到 node.exe")
    _report(cb, "沙盒 Node.js 就绪")
    return True, f"沙盒 Node.js v{NODE_VERSION} 已就绪: {node_bin()}"


def _enable_site(py_dir: Path):
    """启用 python embeddable 的 site-packages：编辑 ._pth 反注释 import site 并加入 Lib\\site-packages"""
    for p in py_dir.glob("*_pth"):
        lines = p.read_text(encoding="utf-8").splitlines()
        out, has_site, has_sp = [], False, False
        for ln in lines:
            s = ln.strip()
            if s.startswith("#import site") or s.startswith("# import site"):
                out.append("import site")
                has_site = True
            elif s == "import site":
                has_site = True
                out.append(ln)
            elif s.replace("\\", "/") == "lib/site-packages":
                has_sp = True
                out.append(ln)
            else:
                out.append(ln)
        if not has_site:
            out.insert(0, "import site")
        if not has_sp:
            out.append("Lib\\site-packages")
        p.write_text("\n".join(out) + "\n", encoding="utf-8")


def _ensure_python(cb) -> tuple:
    py_dir = python_bin()
    url = (f"https://www.python.org/ftp/python/{PYTHON_VERSION}/"
           f"python-{PYTHON_VERSION}-embed-{_py_arch()}.zip")
    zip_p = RUNTIME_ROOT / f"python-{PYTHON_VERSION}-embed-{_arch()}.zip"
    _report(cb, f"正在下载沙盒 Python {PYTHON_VERSION}…")
    _download(url, zip_p, cb)
    _report(cb, "正在解压 Python…")
    _extract_zip(zip_p, py_dir, strip_top=False)
    zip_p.unlink(missing_ok=True)
    if not (py_dir / "python.exe").is_file():
        raise RuntimeError("Python 解压后未找到 python.exe")
    _enable_site(py_dir)
    # 引导 pip 到沙盒 site-packages
    _report(cb, "正在引导沙盒 pip…")
    getpip = RUNTIME_ROOT / "get-pip.py"
    _download("https://bootstrap.pypa.io/get-pip.py", getpip, None)
    subprocess.run([str(py_dir / "python.exe"), str(getpip), "--no-warn-script-location"],
                   check=True, capture_output=True)
    getpip.unlink(missing_ok=True)
    if not (py_dir / "Scripts").is_dir():
        raise RuntimeError("pip 引导失败，未生成 Scripts 目录")
    _report(cb, "沙盒 Python 就绪")
    return True, f"沙盒 Python {PYTHON_VERSION} 已就绪: {py_dir}"


def _deploy_from_bundle(name: str, cb) -> tuple:
    """从安装目录内打包的 runtime 本地复制到沙盒目录（无需联网下载）。
    返回 (ok, message)；未找到打包运行时返回 None。"""
    src = bundled_runtime_root() / name
    exe_name = "node.exe" if name == "node" else "python.exe"
    if not (src / exe_name).is_file():
        return None
    try:
        _report(cb, f"正在从安装目录部署沙盒 {name}…")
        RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
        # 先建临时目录再改名，避免半途复制失败留下残缺运行时
        tmp = RUNTIME_ROOT / f".{name}.tmp"
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
        shutil.copytree(src, tmp)
        final = runtime_dir(name)
        if final.exists():
            shutil.rmtree(final, ignore_errors=True)
        tmp.replace(final)
        if name == "python":
            _enable_site(final)   # 幂等：打包时已启用，这里兜底一次
        _report(cb, f"沙盒 {name} 已部署")
        return True, f"沙盒 {name} 已就绪（从安装目录）"
    except Exception as e:
        return False, f"沙盒 {name} 部署失败: {e}"


def ensure_runtime(name: str, cb=None) -> tuple:
    """确保沙盒运行时就绪：缺失时优先从安装目录打包的运行时本地复制。
    禁止在对话内联网下载（运行时已随安装程序预置）；未附带时报错提示。返回 (ok, message)。"""
    if name not in ("node", "python"):
        return False, f"未知运行时: {name}"
    if is_installed(name):
        return True, f"沙盒 {name} 已就绪"
    with _LOCK:
        if is_installed(name):   # 二次检查：并发下已被其他线程装好
            return True, f"沙盒 {name} 已就绪"
        # 打包进安装目录的运行时优先：本地复制替代联网下载
        bundled = _deploy_from_bundle(name, cb)
        if bundled is not None:
            return bundled
        # 未随安装包附带：不再联网下载，给出明确指引
        return False, (f"沙盒 {name} 未随安装包附带。请通过安装程序安装本应用"
                       f"（已预置 {name} 运行时），或先部署到桌面环境后再运行沙盒命令。")