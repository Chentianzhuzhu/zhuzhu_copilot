#!/usr/bin/env python3
"""本地离线构建辅助：从 ~/.m2 缓存 + target/offline-libs 组装 javac classpath。

供 offline_compile.py 与 run_tests.py 共用，避免两处重复维护。
"""
from __future__ import annotations

import glob
import os
from pathlib import Path

# sources / javadoc / tests 包对编译无用，跳过
SKIP_SUFFIXES = ("-sources.jar", "-javadoc.jar", "-tests.jar")


def m2_repository() -> Path:
    override = os.environ.get("M2_REPO")
    return Path(override) if override else Path.home() / ".m2" / "repository"


def collect_jars(repo: Path, extra_dirs: list[Path]) -> list[str]:
    """收集依赖 jar；Spring 框架同名多版本时取最高版本，避免旧类优先命中。"""
    jars = glob.glob(str(repo / "**" / "*.jar"), recursive=True)
    for d in extra_dirs:
        jars += glob.glob(str(d / "*.jar"))

    best_spring: dict[str, tuple[tuple[int, ...], str]] = {}
    others: list[str] = []

    for jar in jars:
        norm = jar.replace("\\", "/")
        if norm.endswith(SKIP_SUFFIXES):
            continue
        if "/org/springframework/spring-" in norm:
            parts = norm.split("/")
            version = next((p for p in reversed(parts[:-1]) if p and p[0].isdigit()), "")
            if not version:
                continue
            key = "/".join(parts[parts.index("springframework") + 1:-1])
            version_key = tuple(int(x) if x.isdigit() else 0 for x in version.split("."))
            prev = best_spring.get(key)
            if prev is None or version_key > prev[0]:
                best_spring[key] = (version_key, jar)
        else:
            others.append(jar)

    return sorted(others) + [v[1] for v in best_spring.values()]


def sources_of(src_dir: Path) -> list[str]:
    return sorted(glob.glob(str(src_dir / "**" / "*.java"), recursive=True))


def to_arg_path(path: object) -> str:
    """转成 javac @argfile 里可用的带引号路径（路径可能含空格）。"""
    return '"' + str(path).replace("\\", "/") + '"'


def write_argfile(path: Path, classpath: str, out_dir: Path, sources: list[str]) -> Path:
    lines = [
        "-encoding", "UTF-8",
        "-d", to_arg_path(out_dir),
        "-cp", to_arg_path(classpath),
        "-nowarn",
        "-proc:none",
        *[to_arg_path(s) for s in sources],
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
