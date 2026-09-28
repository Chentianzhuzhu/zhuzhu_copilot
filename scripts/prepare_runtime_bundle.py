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


def main():
    BUNDLE_ROOT.mkdir(parents=True, exist_ok=True)
    _prepare_node()
    _prepare_python()
    size = sum(f.stat().st_size for f in BUNDLE_ROOT.rglob("*") if f.is_file())
    _report(f"完成：{BUNDLE_ROOT}（{size / 1024 / 1024:.1f} MB）")


if __name__ == "__main__":
    main()