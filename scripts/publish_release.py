#!/usr/bin/env python3
"""把构建产物（安装包 exe）推送到 GitHub Release，供用户直链下载。

设计要点
--------
* 令牌只从环境变量读取（`GITHUB_TOKEN` 或 `GH_TOKEN`），不写入仓库、不回显、不落日志；
* **幂等**：同名 tag 的 release 已存在则复用；同名资产已存在则先删后传，重跑不会产生重复；
* **流式上传**：显式 Content-Length + 分块发送，400MB+ 的安装包不进内存；
* 只依赖标准库，发版不需要额外装依赖。

用法
----
    export GITHUB_TOKEN=<personal-access-token>      # 需要 repo 权限
    python scripts/publish_release.py --dry-run      # 只打印将要做什么
    python scripts/publish_release.py                # 建/复用 release 并上传安装包

可选参数：`--tag` / `--asset` / `--name` / `--notes` / `--notes-file` / `--draft`
/ `--prerelease` / `--repo` / `--api-base` / `--upload-base`。
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_SOURCE = ROOT / "src" / "zhuzhu_Copilot" / "update_check.py"
DEFAULT_ASSET = ROOT / "dist" / "zhuzhu Copilot Setup.exe"
DEFAULT_REPO = "Chentianzhuzhu/zhuzhu_copilot"
DEFAULT_API = "https://api.github.com"
DEFAULT_UPLOAD_API = "https://uploads.github.com"
USER_AGENT = "zhuzhu-copilot-release"
CHUNK = 1024 * 1024


class ReleaseError(RuntimeError):
    """发版过程中的可读错误（不吞掉 HTTP 状态码与响应体）"""


def token_from_env() -> str:
    for key in ("GITHUB_TOKEN", "GH_TOKEN"):
        value = os.environ.get(key, "").strip()
        if value:
            return value
    raise ReleaseError("缺少令牌：请先设置环境变量 GITHUB_TOKEN（或 GH_TOKEN）")


def version_from_source() -> str:
    """版本号单一来源：客户端 update_check.py 的 APP_VERSION，避免与 tag 漂移"""
    text = VERSION_SOURCE.read_text(encoding="utf-8")
    m = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', text, re.M)
    if not m:
        raise ReleaseError(f"未能从 {VERSION_SOURCE} 解析 APP_VERSION")
    return m.group(1)


def request_json(method: str, url: str, token: str, payload: dict | None = None) -> object:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"token {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", USER_AGENT)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:400]
        raise ReleaseError(f"{method} {url} 失败：HTTP {e.code} {detail}") from e
    except urllib.error.URLError as e:
        raise ReleaseError(f"{method} {url} 无法连接：{e.reason}") from e
    return json.loads(body) if body.strip() else {}


def get_release(repo: str, tag: str, token: str, api: str) -> dict | None:
    url = f"{api}/repos/{repo}/releases/tags/{urllib.parse.quote(tag)}"
    try:
        return request_json("GET", url, token)  # type: ignore[return-value]
    except ReleaseError as e:
        if "HTTP 404" in str(e):
            return None
        raise


def create_release(repo: str, tag: str, token: str, api: str, name: str,
                   notes: str, draft: bool, prerelease: bool) -> dict:
    payload = {
        "tag_name": tag,
        "name": name,
        "body": notes,
        "draft": draft,
        "prerelease": prerelease,
    }
    return request_json("POST", f"{api}/repos/{repo}/releases", token, payload)  # type: ignore[return-value]


def delete_asset(repo: str, asset_id: int, token: str, api: str) -> None:
    request_json("DELETE", f"{api}/repos/{repo}/releases/assets/{asset_id}", token)


def upload_asset(upload_base: str, repo: str, release_id: int, asset: Path,
                 name: str, token: str) -> dict:
    """流式上传单个资产；显式 Content-Length，避免把大文件读进内存"""
    parsed = urllib.parse.urlsplit(upload_base)
    conn = http.client.HTTPSConnection(parsed.hostname or "", parsed.port or 443, timeout=3600)
    path = f"{parsed.path}/repos/{repo}/releases/{release_id}/assets?{urllib.parse.urlencode({'name': name})}"
    size = asset.stat().st_size
    try:
        conn.putrequest("POST", path)
        conn.putheader("Authorization", f"token {token}")
        conn.putheader("Accept", "application/vnd.github+json")
        conn.putheader("User-Agent", USER_AGENT)
        conn.putheader("Content-Type", "application/octet-stream")
        conn.putheader("Content-Length", str(size))
        conn.endheaders()
        sent = 0
        with asset.open("rb") as fh:
            while True:
                chunk = fh.read(CHUNK)
                if not chunk:
                    break
                conn.send(chunk)
                sent += len(chunk)
                print(f"\r  上传中 {sent / size * 100:5.1f}%  ({sent // CHUNK} MB)", end="", flush=True)
        print()
        resp = conn.getresponse()
        body = resp.read().decode("utf-8", errors="replace")
        if resp.status not in (200, 201):
            raise ReleaseError(f"资产上传失败：HTTP {resp.status} {body[:400]}")
        return json.loads(body)
    finally:
        conn.close()


def human_size(num: int) -> str:
    mb = num / 1024 / 1024
    return f"{mb / 1024:.2f} GB" if mb >= 1024 else f"{mb:.1f} MB"


def build_notes(version: str, asset: Path) -> str:
    return (
        f"## zhuzhu Copilot {version}\n\n"
        f"Windows 桌面 AI Agent 应用。安装包：`{asset.name}`"
        f"（{human_size(asset.stat().st_size)}）。\n\n"
        "安装后首次启动会自动检查更新；完整功能与部署说明见仓库 README。\n"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="推送构建产物到 GitHub Release")
    ap.add_argument("--repo", default=DEFAULT_REPO, help="owner/name")
    ap.add_argument("--tag", default=None, help="tag 名，默认 v<APP_VERSION>")
    ap.add_argument("--asset", default=str(DEFAULT_ASSET), help="要上传的文件")
    ap.add_argument("--name", default=None, help="Release 标题，默认与 tag 相同")
    ap.add_argument("--notes", default=None, help="Release 说明正文")
    ap.add_argument("--notes-file", default=None, help="从文件读取说明正文")
    ap.add_argument("--asset-name", default=None, help="上传后的资产名，默认用文件名")
    ap.add_argument("--draft", action="store_true", help="建为草稿")
    ap.add_argument("--prerelease", action="store_true", help="标记为预发布")
    ap.add_argument("--api-base", default=DEFAULT_API, help="GitHub API 基址")
    ap.add_argument("--upload-base", default=DEFAULT_UPLOAD_API, help="资产上传基址")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不发起请求")
    args = ap.parse_args()

    asset = Path(args.asset)
    if not asset.is_file():
        raise ReleaseError(f"构建产物不存在：{asset}")

    version = version_from_source()
    tag = args.tag or f"v{version}"
    name = args.name or f"zhuzhu Copilot {tag}"
    asset_name = args.asset_name or asset.name
    if args.notes_file:
        notes = Path(args.notes_file).read_text(encoding="utf-8")
    else:
        notes = args.notes if args.notes is not None else build_notes(tag, asset)

    print(f"仓库     : {args.repo}")
    print(f"tag      : {tag}（APP_VERSION={version}）")
    print(f"资产     : {asset}  ({human_size(asset.stat().st_size)})")
    print(f"资产名   : {asset_name}")
    print(f"草稿/预发: {args.draft} / {args.prerelease}")

    if args.dry_run:
        print("[dry-run] 未发起任何请求")
        return 0

    token = token_from_env()
    release = get_release(args.repo, tag, token, args.api_base)
    if release is None:
        print(f"未找到 tag {tag} 的 release，正在创建…")
        release = create_release(args.repo, tag, token, args.api_base, name, notes,
                                 args.draft, args.prerelease)
    else:
        print(f"复用已有 release（id={release['id']}，{len(release.get('assets', []))} 个资产）")

    release_id = int(release["id"])
    for existing in release.get("assets", []):
        if existing.get("name") == asset_name:
            print(f"同名资产已存在（id={existing['id']}），先删除以便覆盖上传")
            delete_asset(args.repo, int(existing["id"]), token, args.api_base)

    result = upload_asset(args.upload_base, args.repo, release_id, asset, asset_name, token)
    print("完成：")
    print(f"  release : {release.get('html_url', '')}")
    print(f"  直链    : {result.get('browser_download_url', '')}")
    print(f"  大小    : {human_size(int(result.get('size', 0)))}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ReleaseError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        sys.exit(1)
