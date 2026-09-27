"""沙盒运行时逻辑回归测试：探测/环境注入/依赖隔离（不触发真实下载）"""
import sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from winapp_migrator.core import agent_runtime as ar

TMP = Path(tempfile.mkdtemp())
ar.RUNTIME_ROOT = TMP


def _make(name):
    d = TMP / name
    d.mkdir(parents=True, exist_ok=True)
    if name == "node":
        (d / "node.exe").write_text("x")
        (d / "npm.cmd").write_text("@echo off")
    else:
        (d / "python.exe").write_text("x")
        (d / "Scripts").mkdir(exist_ok=True)
        (d / "Scripts" / "pip.exe").write_text("x")


def test_needed_runtimes():
    assert ar.needed_runtimes("node --version") == ["node"]
    assert ar.needed_runtimes("npm install") == ["node"]
    assert ar.needed_runtimes("npx tsc") == ["node"]
    assert ar.needed_runtimes("python --version") == ["python"]
    assert ar.needed_runtimes("py -3 script.py") == ["python"]
    assert ar.needed_runtimes("pip install requests") == ["python"]
    assert ar.needed_runtimes("dir C:\\") == []
    print("[OK] test_needed_runtimes")


def test_detect_and_env():
    _make("node")
    _make("python")
    assert ar.is_installed("node")
    assert ar.is_installed("python")
    env = ar.sandbox_env("node --version")
    # 沙盒 bin 前置在 PATH 最前
    assert str(TMP / "node") == env["PATH"].split(";")[0]
    # npm 缓存/前缀隔离到沙盒
    assert env["npm_config_cache"].startswith(str(TMP / "node"))
    assert env["npm_config_prefix"] == str(TMP / "node")
    # 系统 PATH 仍在
    assert "PATH" in ar.sandbox_env("python -m pip install x")
    env2 = ar.sandbox_env("python -m pip install x")
    assert env2["PIP_CACHE_DIR"].startswith(str(TMP / "python"))
    # Scripts 前置（pip 命中沙盒）
    assert (TMP / "python" / "Scripts") in [Path(p) for p in env2["PATH"].split(";")[:2]]
    print("[OK] test_detect_and_env")


def test_no_system_pollution():
    """保证绝不修改 os.environ / 系统 PATH"""
    before = dict(os.environ) if "os" in globals() else None
    import os
    before = dict(os.environ)
    _make("node")
    _make("python")
    ar.sandbox_env("node --version")
    ar.sandbox_env("python script.py")
    assert os.environ == before, "os.environ 被修改（违反隔离要求）"
    print("[OK] test_no_system_pollution")


if __name__ == "__main__":
    test_needed_runtimes()
    test_detect_and_env()
    test_no_system_pollution()
    print("\n全部通过")