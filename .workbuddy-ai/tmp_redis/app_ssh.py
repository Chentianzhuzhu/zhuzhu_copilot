import sys, paramiko

# App server (应用服务器)
HOST = "39.104.28.191"
USER = "root"
PWD  = "windows10ztlZTL"

def run(cmd, timeout=180):
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, port=22, username=USER, password=PWD, timeout=25,
              allow_agent=False, look_for_keys=False, banner_timeout=30, auth_timeout=30)
    try:
        stdin, stdout, stderr = c.exec_command(cmd, timeout=timeout)
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        rc = stdout.channel.recv_exit_status()
        return rc, out, err
    finally:
        c.close()

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read()
    rc, out, err = run(cmd)
    if out:
        sys.stdout.write(out)
    if err:
        sys.stdout.write("\n--- STDERR ---\n" + err)
    print("\n[exit %s]" % rc)
