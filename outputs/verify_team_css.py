"""一次性验证：确认线上官网 CSS 已是本次修复后的版本（团队成员照片 4:5 取景框）。

用法：$env:REMOTE_PASS='...'; python outputs/verify_team_css.py
"""
import hashlib
import os
import sys

import paramiko

HOST = "39.104.28.191"
USER = "root"
LOCAL_CSS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "update-server", "src", "main", "resources", "static", "css", "site.css")


def main() -> int:
    pwd = os.environ.get("REMOTE_PASS", "").strip()
    if not pwd:
        print("缺少 REMOTE_PASS 环境变量", file=sys.stderr)
        return 2

    local_md5 = hashlib.md5(open(LOCAL_CSS, "rb").read()).hexdigest()
    print(f"本地 site.css md5: {local_md5}")

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=pwd, timeout=30,
              allow_agent=False, look_for_keys=False)

    def sh(cmd: str, timeout: int = 60) -> str:
        _in, out, err = c.exec_command(cmd, timeout=timeout)
        text = out.read().decode("utf-8", "replace")
        e = err.read().decode("utf-8", "replace")
        return (text + ("\n[stderr] " + e if e.strip() else "")).strip()

    try:
        print("\n---- 1. 线上 HTTP 返回的 CSS 指纹 ----")
        print(sh("curl -s --max-time 10 http://127.0.0.1:8080/css/site.css | md5sum"))

        print("\n---- 2. 部署目录里的 CSS 指纹 ----")
        print(sh("md5sum /www/update-server/target/classes/static/css/site.css"))

        print("\n---- 3. 关键修复规则是否在线上 CSS 中 ----")
        print(sh("curl -s --max-time 10 http://127.0.0.1:8080/css/site.css "
                 "| grep -nE 'aspect-ratio: 4 / 5|object-position: center 15%|about-team-grid'"))

        print("\n---- 4. /about 页面状态与团队区块 ----")
        print(sh("curl -s -o /dev/null -w '/about code=%{http_code}\\n' --max-time 10 "
                 "http://127.0.0.1:8080/about"))
        print(sh("curl -s --max-time 10 http://127.0.0.1:8080/about "
                 "| grep -oE 'class=\"member-avatar\"|member-avatar-fallback|about-team-grid' "
                 "| sort | uniq -c"))

        print("\n---- 5. 服务状态 ----")
        print(sh("systemctl is-active update-server; "
                 "systemctl show -p ActiveEnterTimestamp --value update-server"))
    finally:
        c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
