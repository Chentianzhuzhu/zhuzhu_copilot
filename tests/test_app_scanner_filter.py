"""应用识别过滤规则测试（app_scanner.ScanFilter）。

覆盖用户报告的两类误识别：
  1. 大文件目录（素材 / 数据 / 游戏资源）被当成应用；
  2. 单个 exe 的绿色工具 / 安装包目录被当成应用。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zhuzhu_Copilot.core.app_scanner import AppScanner, ScanFilter  # noqa: E402

MB = 1024 * 1024


def _scanner(tmp_path) -> AppScanner:
    """用 10MB 阈值的小过滤规则，便于在临时目录里构造"超大"场景"""
    return AppScanner(ScanFilter(max_dir_bytes=10 * MB, lone_exe_max_files=2))


def _scan(scanner: AppScanner, root: Path):
    seen = set()
    scanner.apps = []
    scanner._scan_folder(root, seen, max_depth=1)
    return {a.name for a in scanner.apps}


def _mk(path: Path, name: str, size: int = 0):
    f = path / name
    f.write_bytes(b"\0" * size)
    return f


def test_real_app_dir_is_kept(tmp_path):
    """真实应用目录：主程序 + 资源文件 + 子目录 → 正常识别"""
    app = tmp_path / "RealApp"
    app.mkdir()
    _mk(app, "RealApp.exe", 1024)
    for i in range(5):
        _mk(app, f"res{i}.dll", 1024)
    (app / "locales").mkdir()
    assert "RealApp" in _scan(_scanner(tmp_path), tmp_path)


def test_lone_exe_dir_is_rejected(tmp_path):
    """仅一个 exe → 安装包 / 绿色单文件工具，不识别"""
    d = tmp_path / "PortableTool"
    d.mkdir()
    _mk(d, "tool.exe", 1024)
    assert _scan(_scanner(tmp_path), tmp_path) == set()


def test_lone_exe_with_readme_is_rejected(tmp_path):
    """1 个 exe + 1 个说明文件，无子目录 → 仍视为绿色单文件程序"""
    d = tmp_path / "TinyTool"
    d.mkdir()
    _mk(d, "tiny.exe", 1024)
    _mk(d, "readme.txt", 16)
    assert _scan(_scanner(tmp_path), tmp_path) == set()


def test_lone_exe_with_subdir_is_kept(tmp_path):
    """有子目录说明是完整应用目录结构 → 保留"""
    d = tmp_path / "HasSub"
    d.mkdir()
    _mk(d, "main.exe", 1024)
    (d / "data").mkdir()
    assert "HasSub" in _scan(_scanner(tmp_path), tmp_path)


def test_oversized_dir_is_rejected(tmp_path):
    """直接文件总大小超过阈值 → 数据 / 素材目录，不识别（含子目录，排除孤立 exe 规则干扰）"""
    d = tmp_path / "BigData"
    d.mkdir()
    _mk(d, "app.exe", 1024)
    _mk(d, "movie.mp4", 20 * MB)
    (d / "assets").mkdir()
    assert _scan(_scanner(tmp_path), tmp_path) == set()


def test_size_summed_across_files(tmp_path):
    """多个中等文件累计超阈值同样被排除（避免只拦单文件）"""
    d = tmp_path / "ManyParts"
    d.mkdir()
    _mk(d, "a.exe", 1024)
    for i in range(4):
        _mk(d, f"part{i}.bin", 3 * MB)
    assert _scan(_scanner(tmp_path), tmp_path) == set()


def test_disabling_oversize_rule(tmp_path):
    """阈值置 0 表示关闭体积过滤（可扩展性：规则可由上层按需关闭）。
    目录含子目录，故只受体积规则约束，不触发孤立 exe 规则。"""
    d = tmp_path / "BigData"
    d.mkdir()
    _mk(d, "app.exe", 1024)
    _mk(d, "movie.mp4", 20 * MB)
    (d / "assets").mkdir()
    sc = AppScanner(ScanFilter(max_dir_bytes=0, lone_exe_max_files=2))
    assert "BigData" in _scan(sc, tmp_path)


def test_default_filter_threshold_is_2gb():
    """默认阈值 = 2GB（避免硬编码散落，集中一处便于后期暴露到设置页）"""
    assert ScanFilter().max_dir_bytes == 2 * 1024 ** 3


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
