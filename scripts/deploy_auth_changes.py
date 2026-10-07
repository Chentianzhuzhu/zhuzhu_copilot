"""部署脚本：把本地 auth-server 改动同步到云服务器并重启服务。

用法：
  export SSHPASS_PASSWORD='服务器密码'
  python scripts/deploy_auth_changes.py            # 上传 + 重启 + 验证
  python scripts/deploy_auth_changes.py --dry-run  # 只列出将上传的文件

注意：SSH 密码**只**从 SSHPASS_PASSWORD 环境变量读取，不设默认值，
避免明文口令随脚本进入版本库。
"""
import os
import sys
import posixpath
import paramiko

HOST = "39.104.28.191"
PORT = 22
USER = "root"


def _password() -> str:
    """从环境变量读取 SSH 密码；缺失即报错（绝不内置明文口令）。"""
    pwd = os.environ.get("SSHPASS_PASSWORD", "").strip()
    if not pwd:
        raise SystemExit(
            "缺少环境变量 SSHPASS_PASSWORD。\n"
            "  Git Bash : export SSHPASS_PASSWORD='你的密码'\n"
            "  PowerShell: $env:SSHPASS_PASSWORD='你的密码'")
    return pwd


REMOTE_DIR = "/www/auth-server"
LOCAL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "auth-server")

# 需要同步的文件（相对 auth-server/ 的路径）
FILES = [
    "app/api/auth.py",
    "app/api/admin.py",
    "app/api/agreement.py",
    "app/api/checkin.py",
    "app/api/shop.py",
    "app/services/auth_service.py",
    "app/services/order_service.py",
    "app/services/checkin_service.py",
    "app/services/ws_manager.py",
    "app/config.py",
    "app/redis_client.py",
    "app/core/security.py",
    "app/core/login_state.py",
    "app/main.py",
    "app/schemas/__init__.py",
    "sql/migrate_20261007_checkin.sql",
    "static/login/index.html",
    "static/admin/index.html",
]


def get_client():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(hostname=HOST, port=PORT, username=USER, password=_password(),
              timeout=30, banner_timeout=30, auth_timeout=30,
              allow_agent=False, look_for_keys=False)
    return c


def main():
    dry = "--dry-run" in sys.argv
    print(f"部署目录: {LOCAL_DIR} -> {HOST}:{REMOTE_DIR}")
    client = get_client()
    sftp = client.open_sftp()
    try:
        for rel in FILES:
            local = os.path.join(LOCAL_DIR, rel.replace("/", os.sep))
            remote = posixpath.join(REMOTE_DIR, rel)
            if not os.path.exists(local):
                print(f"  [SKIP] 不存在: {local}")
                continue
            if dry:
                print(f"  [DRY] {local} -> {remote}")
                continue
            # 确保远端目录存在
            rdir = posixpath.dirname(remote)
            try:
                sftp.stat(rdir)
            except IOError:
                sftp.mkdir(rdir)
            sftp.put(local, remote)
            print(f"  [PUT] {rel}")
    finally:
        sftp.close()

    if dry:
        client.close()
        print("DRY-RUN 完成")
        return

    # 重启服务并验证
    cmd = (
        "cd /www/auth-server && "
        ".venv/bin/python -c \"import app.main; print('IMPORT OK')\" && "
        "systemctl restart auth-server && sleep 4 && "
        "systemctl is-active auth-server && "
        "curl -s -o /dev/null -w 'health=%{http_code}\\n' http://127.0.0.1:8000/ && "
        "journalctl -u auth-server -n 8 --no-pager | tail -8"
    )
    stdin, stdout, stderr = client.exec_command(cmd, timeout=180)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    print("---- 重启输出 ----")
    print(out)
    if err.strip():
        print("---- stderr ----")
        print(err)
    client.close()


if __name__ == "__main__":
    main()
