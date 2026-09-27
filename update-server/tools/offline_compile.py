#!/usr/bin/env python3
"""离线编译校验：在没有 Maven 的开发机上，用本机 ~/.m2 缓存直接 javac 编译 update-server。

作用：改完 Java 代码立即拿到编译错误，不必等 mvn（CI 与服务器仍走 Maven 正式构建）。
只做类型/语法检查，不产出可运行制品。

用法：
  python tools/offline_compile.py             # 编译 src/main/java
  python tools/offline_compile.py --with-tests # 一并编译 src/test/java
"""
from __future__ import annotations

import argparse
import locale
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _cp import collect_jars, m2_repository, sources_of, to_arg_path, write_argfile  # noqa: E402


def run_javac(argfile: Path) -> int:
    # javac 在 Windows 上按控制台代码页输出，用系统首选编码读取，避免中文报错乱码
    enc = locale.getpreferredencoding(False) or "utf-8"
    proc = subprocess.run(
        ["javac", "@" + to_arg_path(argfile).strip('"')],
        capture_output=True, text=True, encoding=enc, errors="replace",
    )
    for stream in (proc.stdout, proc.stderr):
        if stream and stream.strip():
            print(stream.strip())
    return proc.returncode


def main() -> int:
    here = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(here / "src/main/java"))
    ap.add_argument("--test-src", default=str(here / "src/test/java"))
    ap.add_argument("--out", default=str(here / "target/offline-classes"))
    ap.add_argument("--with-tests", action="store_true")
    args = ap.parse_args()

    repo = m2_repository()
    if not repo.is_dir():
        print(f"[FAIL] 找不到本地 Maven 仓库：{repo}", file=sys.stderr)
        return 2

    sources = sources_of(Path(args.src))
    if not sources:
        print(f"[FAIL] 未找到 Java 源文件：{args.src}", file=sys.stderr)
        return 2

    jars = collect_jars(repo, [here / "target/offline-libs"])
    if len(jars) < 10:
        print(f"[FAIL] 依赖 jar 过少（{len(jars)}），请先运行 tools/fetch_offline_libs.py", file=sys.stderr)
        return 2

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    if args.with_tests:
        sources += sources_of(Path(args.test_src))

    cp = str(out) + ";" + ";".join(jars) if sys.platform.startswith("win") else str(out) + ":" + ":".join(jars)
    argfile = write_argfile(out / "javac.args", cp, out, sources)

    print(f"javac: {len(sources)} 个源文件，classpath {len(jars)} 个 jar")
    code = run_javac(argfile)
    print("[OK] 编译通过" if code == 0 else f"[FAIL] 编译失败，退出码 {code}", file=sys.stderr if code else sys.stdout)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
