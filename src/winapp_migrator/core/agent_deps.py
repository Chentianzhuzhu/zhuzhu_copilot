"""自定义代码依赖管理：为工作流 / 功能面板等 AI 自定义代码声明并自动补齐 pip 依赖。

自定义代码（tools.py / agent.py / llm.py / panel.py）允许顶部带第三方 import；
对应目录下放 requirements.txt 声明依赖，加载前调用 ensure_dir_deps 自动安装缺失项，
并以内容哈希做幂等跳过，避免每次启动重复安装。

依赖安装走用户本机 Python（不走内置沙箱虚拟环境），失败仅告警不抛，不影响后续加载。
"""

import hashlib
import logging
import subprocess
import sys
from pathlib import Path

_log = logging.getLogger("winapp_migrator.agent_deps")


def requirements_of(directory) -> Path:
    """返回 <directory>/requirements.txt 路径（不存在时路径仍可用，调用方自行判 is_file）"""
    return Path(directory) / "requirements.txt"


def _stamp_file(directory) -> Path:
    return Path(directory) / ".deps.stamp"


def _desired_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _pip_python() -> str:
    """返回用于安装依赖的 Python 可执行：开发模式用当前解释器（用户全局环境）；
    打包(frozen)下 sys.executable 是应用自身 exe，回退沙盒 Python（best effort）。"""
    if not getattr(sys, "frozen", False):
        return sys.executable
    try:
        from winapp_migrator.core import agent_runtime
        exe = agent_runtime.python_exe()
        if exe.is_file():
            return str(exe)
    except Exception:
        pass
    return "python"


def ensure_dir_deps(directory, timeout: int = 300) -> None:
    """安装 <directory>/requirements.txt 声明的缺失依赖（幂等：内容未变则跳过）。
    失败仅记录告警，不抛出——依赖缺失自有代码 import 报错会兜底提示。"""
    try:
        req = requirements_of(directory)
        if not req.is_file():
            return
        if not req.read_text(encoding="utf-8", errors="replace").strip():
            return
        stamp = _stamp_file(directory)
        tag = _desired_hash(req)
        try:
            if stamp.is_file() and stamp.read_text(encoding="utf-8").strip() == tag:
                return
        except OSError:
            pass
        proc = subprocess.run(
            [_pip_python(), "-m", "pip", "install", "--disable-pip-version-check",
             "-r", str(req)],
            capture_output=True, text=True, timeout=int(timeout or 300))
        if proc.returncode == 0:
            try:
                stamp.write_text(tag, encoding="utf-8")
            except OSError:
                pass
            _log.info("自定义代码依赖已就绪: %s", req)
        else:
            _log.warning("自定义代码依赖安装失败(%s): %s",
                         req, (proc.stderr or proc.stdout)[-500:])
    except Exception as e:
        _log.warning("依赖保障跳过: %s", e)