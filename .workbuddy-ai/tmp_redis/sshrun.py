import sys, os, paramiko, io

HOST = "122.228.216.138"
USER = "root"
PWD  = "SyPbFW4SToAj7BYU"

def connect():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, port=22, username=USER, password=PWD, timeout=25,
              allow_agent=False, look_for_keys=False, banner_timeout=30, auth_timeout=30)
    return c

def run(cmd, timeout=180):
    c = connect()
    try:
        stdin, stdout, stderr = c.exec_command(cmd, timeout=timeout, get_pty=False)
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        rc = stdout.channel.recv_exit_status()
        return rc, out, err
    finally:
        c.close()

if __name__ == "__main__":
    cmd = sys.stdin.read() if len(sys.argv) < 2 else sys.argv[1]
    rc, out, err = run(cmd)
    if out:
        sys.stdout.write(out)
    if err:
        sys.stdout.write("\n--- STDERR ---\n" + err)
    print("\n[exit %s]" % rc)
