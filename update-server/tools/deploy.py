#!/usr/bin/env python3
"""把 update-server 部署到线上服务器（SSH/SFTP + Maven 远端构建 + systemd 重启 + 健康检查）。

设计要点：
- 只上传源码与配置，不触碰服务器上的 uploads/ 与备份目录；
- 上传用临时名 + 原子替换，避免半截文件；
- 构建在服务器上执行（与 CI 一致的 Maven 流程），失败即中止且不重启服务；
- 凭据只从环境变量读取，不写入仓库、不打印。

用法：
  export REMOTE_HOST=1.2.3.4 REMOTE_USER=root REMOTE_PASS=***
  python tools/deploy.py                     # 上传 + 构建 + 重启 + 健康检查
  python tools/deploy.py --skip-build        # 只上传（用于排障）
  python tools/deploy.py --dry-run           # 只列出将上传的文件
"""
from __future__ import annotations

import argparse
import os
import posixpath
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

try:
    import paramiko
except ImportError:  # pragma: no cover - 环境缺依赖时给出人话提示
    print("[FAIL] 缺少 paramiko，请先执行：pip install paramiko", file=sys.stderr)
    raise SystemExit(2)

LOCAL_ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {"target", "uploads", ".git", "__pycache__", ".idea", ".vscode", "node_modules"}
SKIP_SUFFIXES = {".pyc", ".pyo", ".log", ".bak"}

# 本地已删除、但服务器上仍残留的历史文件（改由控制器动态生成或已迁移到模板）
REMOTE_STALE = [
    "src/main/resources/static/index.html",
    "src/main/resources/static/site.webmanifest",
]


def collect_files() -> list[Path]:
    out: list[Path] = []
    for path in sorted(LOCAL_ROOT.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(LOCAL_ROOT)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if path.suffix.lower() in SKIP_SUFFIXES:
            continue
        if path.name.startswith("update-server-1.0.0.jar"):
            continue
        out.append(path)
    return out


def connect(host: str, user: str, password: str) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, username=user, password=password, timeout=20,
                   allow_agent=False, look_for_keys=False)
    return client


def sh(client: paramiko.SSHClient, cmd: str, timeout: int = 120) -> tuple[int, str]:
    _, out, err = client.exec_command(cmd, timeout=timeout)
    text = out.read().decode("utf-8", "replace") + err.read().decode("utf-8", "replace")
    return out.channel.recv_exit_status(), text.strip()


def check_page_assets(client: paramiko.SSHClient, page_path: str) -> list[str]:
    """把页面里引用的站内资源逐个真实请求一遍（按浏览器规则解析相对路径）。

    「HTML 引用的资源取不到」是最常见的线上事故：/admin 是内部转发，相对路径会被解析成
    /admin.css；带版本号的 CSS 也曾因资源处理器配置错误整批 404。这类问题只看代码看不出来，
    因此这里按最终 URL 逐个请求验证。
    """
    _, html = sh(client, f"curl -s --max-time 10 http://127.0.0.1:8080{page_path}")
    refs = set()
    for match in re.finditer(r'(?:href|src)="([^"]+)"', html):
        ref = match.group(1).strip()
        if not ref or ref.startswith(("#", "//", "http://", "https://", "mailto:", "data:", "javascript:")):
            continue
        refs.add(urljoin(page_path, ref))

    failures = []
    for ref in sorted(refs):
        _, code = sh(client, "curl -s -o /dev/null -w '%{http_code}' --max-time 8 "
                             f"'http://127.0.0.1:8080{ref}'")
        code = code.strip()
        # 2xx 正常；3xx 也正常（下载接口本身就是跳转）；4xx/5xx 才算取不到
        if code.startswith(("2", "3")):
            continue
        failures.append(f"{page_path} 引用的 {ref} → {code}")
    return failures


def wait_ready(client: paramiko.SSHClient, timeout: int = 120) -> bool:
    """等待应用就绪：Spring Boot 启动（JPA + Hibernate 初始化）通常需要 15~25 秒。"""
    print("    等待服务就绪（最多 %d 秒）" % timeout)
    deadline = time.time() + timeout
    while time.time() < deadline:
        _, code = sh(client, "curl -s -o /dev/null -w '%{http_code}' --max-time 5 "
                             "http://127.0.0.1:8080/api/site")
        if code.strip() == "200":
            print("    服务已就绪")
            return True
        time.sleep(3)
    return False


def upload(client: paramiko.SSHClient, sftp: paramiko.SFTPClient,
           files: list[Path], remote_dir: str) -> int:
    """逐个文件上传：先建目录，再写临时名并原子改名。"""
    made: set[str] = set()
    for path in files:
        rel = path.relative_to(LOCAL_ROOT).as_posix()
        target = posixpath.join(remote_dir, rel)
        parent = posixpath.dirname(target)
        if parent not in made:
            sh(client, f"mkdir -p {parent}")
            made.add(parent)
        tmp = target + ".upload"
        sftp.put(str(path), tmp)
        # OpenSSH 的 SFTP 服务不允许 rename 覆盖已存在文件，先删再改名
        try:
            sftp.rename(tmp, target)
        except OSError:
            try:
                sftp.remove(target)
            except OSError:
                pass
            sftp.rename(tmp, target)
    return len(files)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=os.environ.get("REMOTE_HOST", ""))
    ap.add_argument("--user", default=os.environ.get("REMOTE_USER", "root"))
    ap.add_argument("--remote-dir", default="/www/update-server")
    ap.add_argument("--service", default="update-server")
    ap.add_argument("--env-file", default="/etc/update-server.env")
    ap.add_argument("--base-url", default=None,
                    help="官网绝对地址，写入 SITE_BASE_URL（用于 canonical / sitemap）")
    ap.add_argument("--skip-build", action="store_true")
    ap.add_argument("--skip-restart", action="store_true")
    ap.add_argument("--verify-only", action="store_true", help="只做健康检查，不上传不构建不重启")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    password = os.environ.get("REMOTE_PASS", "")
    if not args.dry_run and (not args.host or not password):
        print("[FAIL] 需要设置 REMOTE_HOST 与 REMOTE_PASS 环境变量", file=sys.stderr)
        return 2

    files = collect_files()
    print(f"[1/5] 待上传 {len(files)} 个文件 → {args.remote_dir}")
    if args.dry_run:
        for path in files:
            print("   ", path.relative_to(LOCAL_ROOT).as_posix())
        return 0

    client = connect(args.host, args.user, password)
    try:
        if args.verify_only:
            print("[verify] 跳过上传与构建，直接检查线上状态")

        # ---- 备份现有产物 ----
        ts = time.strftime("%Y%m%d%H%M%S")
        if not args.verify_only:
            sh(client, f"cd {args.remote_dir} && if [ -f target/update-server-1.0.0.jar ]; then "
                       f"cp target/update-server-1.0.0.jar target/update-server-1.0.0.jar.bak.{ts}; fi")
            print(f"  已备份原 jar（.bak.{ts}）")

        # ---- 上传 ----
        if not args.verify_only:
            sftp = client.open_sftp()
            try:
                count = upload(client, sftp, files, args.remote_dir)
            finally:
                sftp.close()
            print(f"[2/5] 上传完成：{count} 个文件")

            removed, out = sh(client, "cd %s && for f in %s; do "
                                      "if [ -f \"$f\" ]; then rm -f \"$f\" && echo $f; fi; done"
                               % (args.remote_dir, " ".join(REMOTE_STALE)))
            if out.strip():
                print("  已清理历史残留文件：", out.replace("\n", " "))

        if args.verify_only or args.skip_build:
            print("[3/5] 跳过构建")
        else:
            # ---- 远端构建（后台执行 + 轮询，避免长连接被掐断） ----
            sh(client, "rm -f /tmp/upd-build.log /tmp/upd-build.rc")
            sh(client, f"cd {args.remote_dir} && nohup sh -c "
                       f"'/opt/maven/bin/mvn -B -q clean package > /tmp/upd-build.log 2>&1; "
                       f"echo $? > /tmp/upd-build.rc' >/dev/null 2>&1 &")
            print("[3/5] 远端构建中：mvn clean package")
            code = None
            deadline = time.time() + 540
            while time.time() < deadline:
                time.sleep(8)
                rc, out = sh(client, "cat /tmp/upd-build.rc 2>/dev/null || true")
                if out.strip().isdigit():
                    code = int(out.strip())
                    break
                _, tail = sh(client, "tail -n 1 /tmp/upd-build.log 2>/dev/null || true")
                if tail:
                    print("    …", tail[:160])
            if code is None:
                print("[FAIL] 构建超时（9 分钟），请查看服务器 /tmp/upd-build.log", file=sys.stderr)
                return 1
            _, log = sh(client, "tail -n 40 /tmp/upd-build.log")
            if code != 0:
                print(log, file=sys.stderr)
                print(f"[FAIL] 构建失败（exit {code}），已中止部署，服务仍在运行旧版本", file=sys.stderr)
                return 1
            print("[3/5] 构建成功")

        # ---- 站点绝对地址（幂等写入，便于 canonical / sitemap 稳定） ----
        if args.base_url:
            _, exists = sh(client, f"grep -q '^SITE_BASE_URL=' {args.env_file} && echo yes || echo no")
            if exists.strip() == "yes":
                sh(client, f"sed -i 's|^SITE_BASE_URL=.*|SITE_BASE_URL={args.base_url}|' {args.env_file}")
                print(f"  已更新 SITE_BASE_URL={args.base_url}")
            else:
                sh(client, f"cp {args.env_file} {args.env_file}.bak.$(date +%s) 2>/dev/null; "
                           f"printf '\\nSITE_BASE_URL={args.base_url}\\n' >> {args.env_file}")
                print(f"  已写入 SITE_BASE_URL={args.base_url}")

        # ---- 重启服务 ----
        if args.skip_restart or args.verify_only:
            print("[4/5] 跳过重启")
        else:
            sh(client, f"systemctl restart {args.service}")
            time.sleep(3)
            if not wait_ready(client):
                _, logs = sh(client, f"journalctl -u {args.service} -n 30 --no-pager")
                print(logs, file=sys.stderr)
                print("[FAIL] 服务未在超时时间内就绪", file=sys.stderr)
                return 1
            _, status = sh(client, f"systemctl is-active {args.service}")
            print(f"[4/5] 服务状态：{status}")
            if status.strip() != "active":
                _, logs = sh(client, f"journalctl -u {args.service} -n 30 --no-pager")
                print(logs, file=sys.stderr)
                return 1

        # ---- 健康检查 ----
        print("[5/5] 健康检查")
        checks = [
            ("/", 200), ("/gallery", 200), ("/download", 200), ("/faq", 200), ("/about", 200),
            ("/sitemap", 200), ("/robots.txt", 200), ("/sitemap.xml", 200), ("/site.webmanifest", 200),
            ("/favicon.svg", 200), ("/css/site.css", 200), ("/js/site.js", 200),
            ("/api/site", 200), ("/api/version/latest", 200), ("/uploads/", 404),
            # 后台：/admin 是内部转发，/admin/ 也要能进，资源必须可取
            ("/admin", 200), ("/admin/", 200), ("/admin/index.html", 200),
            ("/admin/admin.css", 200), ("/admin/admin.js", 200),
        ]
        failed = []
        for path, expect in checks:
            _, out = sh(client, "curl -s -o /dev/null -w '%{http_code}' --max-time 8 "
                                f"http://127.0.0.1:8080{path}")
            got = out.strip()
            mark = "OK " if got == str(expect) else "BAD"
            if got != str(expect):
                failed.append(f"{path} → {got}（期望 {expect}）")
            print(f"   {mark} {path:26} {got}")

        # 关键一步：把页面里引用的站内资源逐个真实请求一遍（含相对路径解析）。
        # 「HTML 引用的资源取不到」是最常见的线上事故（版本化 CSS 404、后台相对路径 404 都属于此类）。
        for page in ("/", "/admin"):
            bad = check_page_assets(client, page)
            if bad:
                failed.extend(bad)
                print(f"   BAD {page} 引用的资源有取不到的：{len(bad)} 项")
                for item in bad:
                    print(f"       - {item}")
            else:
                print(f"   OK  {page} 引用的站内资源全部可取")

        _, title = sh(client, "curl -s --max-time 8 http://127.0.0.1:8080/ | "
                              "grep -o '<title>[^<]*</title>' | head -1")
        _, sections = sh(client, "curl -s --max-time 8 http://127.0.0.1:8080/ | "
                                 "grep -o 'id=\"[a-z]*\"' | sort -u | tr '\\n' ' '")
        print(f"   首页标题：{title}")
        print(f"   首页区块：{sections.strip()}")
        if failed:
            print(f"\n[FAIL] 以下检查未通过：{failed}", file=sys.stderr)
            return 1
        print("\n[OK] 部署完成，全部检查通过")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
