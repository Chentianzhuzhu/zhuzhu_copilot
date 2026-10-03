# -*- coding: utf-8 -*-
"""PowerShell 脚本必须带 UTF-8 BOM（否则 Windows PowerShell 5.1 下中文变乱码、脚本直接报语法错）。

真实缺陷（用户报「打包脚本一跑就一片语法错」）：
    build_release.ps1 是 UTF-8 **无 BOM**，而 Windows PowerShell 5.1 对无 BOM 的 .ps1
    按**系统 ANSI（简体中文机器上是 GBK）**解码 —— 中文注释与提示文字被解成乱码字节，
    其中 `？`、全角括号等落在引号/括号位置，把配对关系打乱。于是报出的却是
    「`"[" 后面缺少类型名称`」「表达式中缺少右")"」这类**看起来与中文无关**的语法错，
    并连带报出 `Write-Ok "瀛樺湪 $p"` 这种乱码字符串 —— 极难从表象定位到编码问题。

对照：同目录带 BOM 的脚本从未出过该问题（本用例即锁住这个差异）。

为什么只查 .ps1：cmd.exe 读 .bat 用的是 OEM 代码页，给 .bat 加 UTF-8 BOM 反而可能被
当成命令的一部分；.bat 的中文乱码只是控制台显示问题，不影响语法，故不在本契约内。
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOM = b"\xef\xbb\xbf"

# 不扫第三方/产物目录：里面的脚本不受本仓库编码约定约束
_SKIP_DIRS = {".git", ".review_tmp", "node_modules", "dist", "__pycache__",
              "runtime_bundle", ".venv", "build"}


def _scripts() -> list:
    out = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fn in filenames:
            if fn.lower().endswith(".ps1"):
                out.append(Path(dirpath) / fn)
    return sorted(out)


def test_ps1_scripts_exist():
    """前置：确实扫到了脚本（否则下面的断言会因空集合而假通过）。"""
    assert _scripts(), "未找到任何 .ps1，用例基准失准"


def test_found_the_known_build_scripts():
    """关键脚本必须被扫到（它们是本契约的主要保护对象）。"""
    names = {p.name for p in _scripts()}
    assert "build_release.ps1" in names, names


def test_non_ascii_ps1_has_utf8_bom():
    """含非 ASCII（本项目即中文）的 .ps1 必须带 UTF-8 BOM。

    无 BOM 时 PowerShell 5.1 走 ANSI 解码 → 中文乱码 + 引号配对错位 + 满屏语法报错。
    """
    bad = []
    for p in _scripts():
        raw = p.read_bytes()
        if raw.startswith(BOM):
            continue
        if any(b > 127 for b in raw):
            bad.append(str(p.relative_to(ROOT)))
    assert not bad, (
        "以下 PowerShell 脚本含非 ASCII 却缺 UTF-8 BOM，Windows PowerShell 5.1 会乱码并报语法错："
        f"{bad}")


def test_ps1_is_valid_utf8():
    """脚本必须是合法 UTF-8（允许有/无 BOM），避免出现 GBK 混编的半乱码文件。"""
    bad = []
    for p in _scripts():
        try:
            p.read_bytes().decode("utf-8")
        except UnicodeDecodeError as e:
            bad.append(f"{p.relative_to(ROOT)}: {e}")
    assert not bad, f"非 UTF-8 编码的 PowerShell 脚本：{bad}"


def test_bom_does_not_leak_into_paths_or_shebang():
    """BOM 只能出现在文件首字节，不得重复或出现在行中间。"""
    bad = []
    for p in _scripts():
        raw = p.read_bytes()
        if raw.count(BOM) > 1 or (BOM in raw and not raw.startswith(BOM)):
            bad.append(str(p.relative_to(ROOT)))
    assert not bad, f"BOM 位置异常：{bad}"
