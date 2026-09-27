#!/usr/bin/env python3
"""补齐离线编译所需的编译期依赖到 target/offline-libs/。

本机 ~/.m2 若缺少 spring-data-jpa / jakarta.* 等 jar，offline_compile.py 会报找不到符号。
本脚本按固定坐标下载这些 jar（仅用于本地类型检查，正式构建仍由 Maven 完成）。

用法：
  python tools/fetch_offline_libs.py [--mirror URL]
"""
from __future__ import annotations

import argparse
import sys
import urllib.request
from pathlib import Path

MIRROR = "https://maven.aliyun.com/repository/public"

# 坐标与版本对齐 spring-boot-starter-parent 3.2.5 / Spring Framework 6.1.x
ARTIFACTS = [
    "org/springframework/data/spring-data-jpa/3.2.5/spring-data-jpa-3.2.5.jar",
    "org/springframework/spring-orm/6.1.6/spring-orm-6.1.6.jar",
    "jakarta/persistence/jakarta.persistence-api/3.1.0/jakarta.persistence-api-3.1.0.jar",
    "jakarta/transaction/jakarta.transaction-api/2.0.1/jakarta.transaction-api-2.0.1.jar",
    "jakarta/servlet/jakarta.servlet-api/6.0.0/jakarta.servlet-api-6.0.0.jar",
    # 模板渲染测试所需（版本对齐 spring-boot-starter-thymeleaf 3.2.5）
    "org/thymeleaf/thymeleaf/3.1.2.RELEASE/thymeleaf-3.1.2.RELEASE.jar",
    "org/thymeleaf/thymeleaf-spring6/3.1.2.RELEASE/thymeleaf-spring6-3.1.2.RELEASE.jar",
    "org/attoparser/attoparser/2.0.7.RELEASE/attoparser-2.0.7.RELEASE.jar",
    "org/unbescape/unbescape/1.1.6.RELEASE/unbescape-1.1.6.RELEASE.jar",
    # 本地跑单测用的 JUnit 控制台（自带 jupiter 引擎与 platform launcher）
    "org/junit/platform/junit-platform-console-standalone/1.10.5/junit-platform-console-standalone-1.10.5.jar",
]


def main() -> int:
    here = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--mirror", default=MIRROR)
    ap.add_argument("--out", default=str(here / "target/offline-libs"))
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    failed = 0
    for path in ARTIFACTS:
        name = path.rsplit("/", 1)[-1]
        target = out / name
        if target.exists() and target.stat().st_size > 1024:
            print(f"[skip] {name}")
            continue
        url = f"{args.mirror.rstrip('/')}/{path}"
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:
                data = resp.read()
            target.write_bytes(data)
            print(f"[ok]   {name} ({len(data)} bytes)")
        except Exception as e:  # noqa: BLE001 - 网络异常统一提示
            print(f"[fail] {name}: {e}", file=sys.stderr)
            failed += 1

    if failed:
        print(f"\n{failed} 个依赖下载失败，可改用 --mirror 指定其他仓库", file=sys.stderr)
        return 1
    print("\n依赖已就绪，可运行：python tools/offline_compile.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
