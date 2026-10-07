#!/usr/bin/env python3
"""远程核验小工具（密码只从环境变量读取，不落盘、不打印）。

用法：
  $env:SSH_PASS='***'; python outputs/_remote_check.py "命令1" "命令2" ...
"""
from __future__ import annotations

import os
import sys

import paramiko

HOST = os.environ.get("SSH_HOST", "39.104.28.191")
USER = os.environ.get("SSH_USER", "root")


def main() -> int:
    pwd = os.environ.get("SSH_PASS", "").strip()
    if not pwd:
        print("[FAIL] 未设置 SSH_PASS 环境变量", file=sys.stderr)
        return 2
    cmds = sys.argv[1:]
    if not cmds:
        print("[FAIL] 请至少给一条命令", file=sys.stderr)
        return 2

    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(HOST, username=USER, password=pwd, timeout=25,
                allow_agent=False, look_for_keys=False)
    try:
        for i, cmd in enumerate(cmds, 1):
            print(f"---- [{i}] $ {cmd}")
            _in, out, err = cli.exec_command(cmd, timeout=90)
            text = out.read().decode("utf-8", "replace")
            e = err.read().decode("utf-8", "replace")
            code = out.channel.recv_exit_status()
            print(text.rstrip())
            if e.strip():
                print("[stderr] " + e.strip())
            print(f"[exit={code}]")
    finally:
        cli.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
