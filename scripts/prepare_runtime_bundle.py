"""预打包沙盒运行时（Node.js / Python 便携版）到 build/runtime_bundle/runtime/。

打包流程：先运行本脚本把运行时下载到构建目录，再由 PyInstaller spec 的 datas
打进主程序目录；安装后 AI 首次执行 node/python 命令时从安装目录本地复制到
~/.zhuzhu_Copilot/runtime/，无需联网下载。已就绪的运行时会被跳过（缓存复用）。

用法：
    python scripts/prepare_runtime_bundle.py
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

# 复用 agent_runtime 的下载/解压/引导逻辑（避免重复实现）
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from zhuzhu_Copilot.core import agent_runtime  # noqa: E402

BUNDLE_ROOT = Path(__file__).resolve().parents[1] / "build" / "runtime_bundle" / "runtime"


def _report(msg: str):
    print(msg, flush=True)


def _prepare_node() -> Path:
    dest = BUNDLE_ROOT / "node"
    if (dest / "node.exe").is_file():
        _report(f"[复用] Node.js 已就绪: {dest}")
        return dest
    _report(f"下载沙盒 Node.js v{agent_runtime.NODE_VERSION}…")
    _download_runtime(agent_runtime.NODE_VERSION, "node", dest)
    return dest


def _prepare_python() -> Path:
    dest = BUNDLE_ROOT / "python"
    if (dest / "python.exe").is_file() and (dest / "Scripts" / "pip.exe").is_file():
        _report(f"[复用] Python 已就绪: {dest}")
        return dest
    _report(f"下载沙盒 Python {agent_runtime.PYTHON_VERSION}…")
    _download_runtime(agent_runtime.PYTHON_VERSION, "python", dest)
    _report("引导沙盒 pip…")
    getpip = dest.parent / "get-pip.py"
    agent_runtime._download("https://bootstrap.pypa.io/get-pip.py", getpip, None)
    subprocess.run([str(dest / "python.exe"), str(getpip), "--no-warn-script-location"],
                   check=True, capture_output=True)
    getpip.unlink(missing_ok=True)
    if not (dest / "Scripts" / "pip.exe").is_file():
        raise RuntimeError("pip 引导失败")
    return dest


def _download_runtime(version: str, name: str, dest: Path):
    """按 name 下载便携版 zip 并解压到 dest（Node 去顶层目录）"""
    arch = agent_runtime._arch()
    if name == "node":
        url = (f"https://nodejs.org/dist/v{version}/node-v{version}-win-{arch}.zip")
        zip_p = BUNDLE_ROOT / f"node-v{version}-win-{arch}.zip"
        agent_runtime._download(url, zip_p, None)
        agent_runtime._extract_zip(zip_p, dest, strip_top=True)
        zip_p.unlink(missing_ok=True)
        if not (dest / "node.exe").is_file():
            raise RuntimeError("Node.js 解压后未找到 node.exe")
    else:
        url = (f"https://www.python.org/ftp/python/{version}/"
               f"python-{version}-embed-{agent_runtime._py_arch()}.zip")
        zip_p = BUNDLE_ROOT / f"python-{version}-embed-{arch}.zip"
        agent_runtime._download(url, zip_p, None)
        agent_runtime._extract_zip(zip_p, dest, strip_top=False)
        zip_p.unlink(missing_ok=True)
        if not (dest / "python.exe").is_file():
            raise RuntimeError("Python 解压后未找到 python.exe")
        agent_runtime._enable_site(dest)


def _plugin_requirements() -> list:
    """收集内置 MCP / 插件声明的第三方依赖（requirements.txt）。

    范围：src/zhuzhu_Copilot/plugins/*/requirements.txt（随包插件，如 audio-analyzer 的
    soundfile/librosa）与 mcp_servers/requirements.txt（本地 MCP 服务器，若有）。
    这些依赖要随安装包一起进内置 Python，否则用户机器上插件工具首次调用就 ImportError。
    """
    root = Path(__file__).resolve().parents[1]
    out = []
    for p in sorted((root / "src" / "zhuzhu_Copilot" / "plugins").glob("*/requirements.txt")):
        out.append(p)
    local = root / "mcp_servers" / "requirements.txt"
    if local.is_file():
        out.append(local)
    return out


def _install_plugin_deps() -> None:
    """把插件/MCP 声明的依赖装进**内置运行时 Python**（随安装包分发）。

    幂等：pip 对已满足的依赖只做检查，构建期重复执行开销很小。
    只用公开 wheel（--only-binary 不强制，留源码包兜底）；失败直接抛错让构建失败，
    避免"打包成功但插件一用就缺依赖"。
    """
    reqs = _plugin_requirements()
    if not reqs:
        _report("[跳过] 没有插件/MCP 依赖清单")
        return
    py = BUNDLE_ROOT / "python" / "python.exe"
    if not py.is_file():
        raise RuntimeError("内置 Python 不存在，无法安装插件依赖")
    for req in reqs:
        text = req.read_text(encoding="utf-8", errors="replace").strip()
        if not text:
            continue
        _report(f"[依赖] 安装 {req.relative_to(req.parents[3])} → 内置运行时 Python…")
        p = subprocess.run(
            [str(py), "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
             "-r", str(req)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if p.returncode != 0:
            tail = (p.stdout or b"").decode("utf-8", "replace")[-1200:]
            raise RuntimeError(f"安装依赖失败：{req}\n{tail}")
    _report("[依赖] 插件/MCP 依赖已进入内置运行时")


def _prune_app_libs() -> None:
    """剔除不应随「工具运行时」分发的重型库（应用本体已自带一份）。

    现状：site-packages 里有 PyQt6（含 Qt6 二进制，实测约 220MB）——它由历史 pip
    操作装进沙盒运行时，但工具子进程（MCP / 插件 server）不需要 Qt，主程序安装包
    自己也带 PyQt6；重复携带只会把安装包撑大一倍。按发行包名前缀统一清理。
    """
    site = BUNDLE_ROOT / "python" / "Lib" / "site-packages"
    if not site.is_dir():
        return
    removed = 0
    for p in list(site.iterdir()):
        n = p.name.lower()
        if not (n.startswith("pyqt6") or n.startswith("pyqt5")):
            continue
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        else:
            try:
                p.unlink()
            except OSError:
                pass
        removed += 1
    if removed:
        _report(f"[清理] 内置运行时剔除 PyQt 副本 {removed} 个（应用本体自带）")


def _prune_webengine() -> None:
    """从沙盒 Python 站点包中剔除 QtWebEngine（应用已整体移除该模块）。

    沙盒运行时只需支撑 AI 执行的普通 Python 脚本；若历史 pip 安装把
    PyQt6-WebEngine 带入 bundle，会被整目录打进安装包（实测约 287MB）。
    按文件名匹配统一清理，不依赖固定路径清单。
    """
    site = BUNDLE_ROOT / "python" / "Lib" / "site-packages"
    if not site.is_dir():
        return
    removed = 0
    for p in sorted(site.rglob("*"), key=lambda x: len(x.parts), reverse=True):
        if not p.exists():
            continue
        name = p.name.lower()
        if "webengine" not in name:
            continue
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        else:
            try:
                p.unlink()
                removed += 1
            except OSError:
                pass
    if removed:
        _report(f"[清理] 沙盒运行时剔除 QtWebEngine 文件 {removed} 个")


def main():
    BUNDLE_ROOT.mkdir(parents=True, exist_ok=True)
    _prepare_node()
    _prepare_python()
    _install_plugin_deps()   # 内置 MCP/插件依赖随安装包分发给内置运行时
    _prune_app_libs()        # 应用本体已自带的重型库不重复打包
    _prune_webengine()
    size = sum(f.stat().st_size for f in BUNDLE_ROOT.rglob("*") if f.is_file())
    _report(f"完成：{BUNDLE_ROOT}（{size / 1024 / 1024:.1f} MB）")


if __name__ == "__main__":
    main()