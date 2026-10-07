"""SSH 部署辅助脚本：使用 paramiko 对目标服务器执行命令与文件传输。

用法：
  python ssh_helper.py run "<command>"
  python ssh_helper.py put <local_path> <remote_path>
  python ssh_helper.py get <remote_path> <local_path>

注意：密码**只**从 SSHPASS_PASSWORD 环境变量读取，不设默认值，
避免明文口令随脚本进入版本库（曾因硬编码 fallback 造成泄露风险）。
未设置该变量时直接报错退出。
"""
import os
import sys
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


def get_client():
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=HOST,
        port=PORT,
        username=USER,
        password=_password(),
        timeout=30,
        banner_timeout=30,
        auth_timeout=30,
        allow_agent=False,
        look_for_keys=False,
    )
    return client


def run_cmd(cmd):
    client = get_client()
    stdin, stdout, stderr = client.exec_command(cmd, timeout=600)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    code = stdout.channel.recv_exit_status()
    client.close()
    if out:
        print(out)
    if err:
        sys.stderr.write(err)
    return code


def _norm_local(path):
    """把 /c/Users/... 这类 Git-Bash 路径还原成 Windows 原生路径，供 Python 打开。"""
    import re
    m = re.match(r"^/([a-zA-Z])/(.*)$", path)
    if m:
        return f"{m.group(1).upper()}:\\{m.group(2).replace('/', os.sep)}"
    return path


def put_file(local, remote):
    client = get_client()
    sftp = client.open_sftp()
    sftp.put(_norm_local(local), remote)
    sftp.close()
    client.close()
    print(f"uploaded {local} -> {remote}")


def get_file(remote, local):
    client = get_client()
    sftp = client.open_sftp()
    sftp.get(remote, _norm_local(local))
    sftp.close()
    client.close()
    print(f"downloaded {remote} -> {local}")


if __name__ == "__main__":
    action = sys.argv[1]
    if action == "run":
        code = run_cmd(sys.argv[2])
        sys.exit(code)
    elif action == "put":
        put_file(sys.argv[2], sys.argv[3])
    elif action == "get":
        get_file(sys.argv[2], sys.argv[3])
    else:
        print("unknown action")
        sys.exit(1)