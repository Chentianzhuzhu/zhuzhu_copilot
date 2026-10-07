#!/usr/bin/env python3
"""本地离线跑单元测试（无需 Maven）。

流程：javac 编译 src/main + src/test，再用 junit-platform-console-standalone 执行。
依赖 target/offline-libs/junit-platform-console-standalone-*.jar（先跑 tools/fetch_offline_libs.py）。

用法：
  python tools/run_tests.py                       # 跑全部单元测试
  python tools/run_tests.py --preview             # 额外把页面渲染结果落盘到 target/preview/pages
  python tools/run_tests.py --preview --preview-content target/preview/live-site.json
                                                  # 预览使用线上真实内容
"""
from __future__ import annotations

import argparse
import glob
import locale
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _cp import collect_jars, m2_repository, sources_of, to_arg_path, write_argfile  # noqa: E402

MAIN_OUT = "target/offline-classes/main"
TEST_OUT = "target/offline-classes/test"
PREVIEW_DIR = "target/preview/pages"


def sep() -> str:
    return ";" if sys.platform.startswith("win") else ":"


def run_with_argfile(cmd: list[str], argfile: Path, encoding: str | None = None) -> int:
    """通过 @argfile 执行 java，规避 Windows 命令行长度上限（WinError 206）。

    仅当整条命令过长时才启用；否则保持直接调用，便于排查。
    """
    joined = " ".join(cmd)
    if len(joined) < 20000:
        return run(cmd, encoding=encoding)
    argfile.parent.mkdir(parents=True, exist_ok=True)
    # @argfile 按空白切分，含空格的参数必须整体加引号；反斜杠会被当转义符，统一改成正斜杠
    lines: list[str] = []
    for part in cmd[1:]:
        normalized = part.replace("\\", "/")
        if " " in normalized:
            normalized = '"' + normalized + '"'
        lines.append(normalized)
    argfile.write_text("\n".join(lines), encoding="utf-8")
    return run([cmd[0], "@" + str(argfile).replace("\\", "/")], encoding=encoding)


def run(cmd: list[str], encoding: str | None = None) -> int:
    """执行外部命令并回显输出；javac 用系统代码页，java 用 UTF-8。"""
    enc = encoding or locale.getpreferredencoding(False) or "utf-8"
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding=enc, errors="replace")
    for stream in (proc.stdout, proc.stderr):
        if stream and stream.strip():
            print(stream.strip())
    return proc.returncode


def javac(args_file: Path) -> int:
    return run(["javac", "@" + to_arg_path(args_file).strip('"')])


def main() -> int:
    here = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", action="store_true", help="同时生成页面预览产物")
    ap.add_argument("--preview-content", default=None,
                    help="线上 /api/site 抓取结果路径，使预览使用真实内容")
    args = ap.parse_args()

    repo = m2_repository()
    offline_libs = here / "target/offline-libs"

    console = glob.glob(str(offline_libs / "junit-platform-console-standalone-*.jar"))
    if not console:
        print("[FAIL] 缺少 junit-platform-console-standalone，请先运行 tools/fetch_offline_libs.py",
              file=sys.stderr)
        return 2

    jars = collect_jars(repo, [offline_libs])
    dep_cp = sep().join(jars)
    main_out = here / MAIN_OUT
    test_out = here / TEST_OUT
    main_out.mkdir(parents=True, exist_ok=True)
    test_out.mkdir(parents=True, exist_ok=True)

    main_src = sources_of(here / "src/main/java")
    test_src = sources_of(here / "src/test/java")
    if not test_src:
        print("[FAIL] 未找到测试源码 src/test/java", file=sys.stderr)
        return 2

    print(f"[1/2] 编译主代码（{len(main_src)} 个文件）")
    if javac(write_argfile(here / "target/main.args", dep_cp, main_out, main_src)) != 0:
        print("[FAIL] 主代码编译失败", file=sys.stderr)
        return 1

    print(f"[2/2] 编译测试代码（{len(test_src)} 个文件）")
    test_cp = str(main_out) + sep() + dep_cp
    if javac(write_argfile(here / "target/test.args", test_cp, test_out, test_src)) != 0:
        print("[FAIL] 测试代码编译失败", file=sys.stderr)
        return 1

    # 运行期必须把 src/main/resources 放进 classpath：
    # AssetVersionService 等通过 classpath:static/... 读取真实资源，否则只能拿到兜底值。
    resources = here / "src/main/resources"
    run_cp = (str(test_out) + sep() + str(main_out) + sep() + str(resources) + sep() + dep_cp)
    java_cmd = [
        "java",
        "-Dfile.encoding=UTF-8", "-Dstdout.encoding=UTF-8", "-Dstderr.encoding=UTF-8",
    ]
    if args.preview:
        preview = (here / PREVIEW_DIR).resolve()
        preview.mkdir(parents=True, exist_ok=True)
        java_cmd.append("-Dpreview.dir=" + str(preview).replace("\\", "/"))
        if args.preview_content:
            content_path = Path(args.preview_content)
            if not content_path.is_absolute():
                content_path = (here / args.preview_content).resolve()
            if not content_path.exists():
                print(f"[FAIL] 预览内容文件不存在：{content_path}", file=sys.stderr)
                return 2
            java_cmd.append("-Dpreview.content=" + str(content_path).replace("\\", "/"))

    print("[run] JUnit 5")
    java_cmd += [
        "-jar", console[0],
        "-cp", run_cp,
        "--select-package=com.zhuzhu.update",
        "--details=tree",
        "--disable-ansi-colors",
    ]
    code = run_with_argfile(java_cmd, here / "target/junit.args", encoding="utf-8")
    print("[OK] 全部单元测试通过" if code == 0 else f"[FAIL] 单元测试失败，退出码 {code}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
